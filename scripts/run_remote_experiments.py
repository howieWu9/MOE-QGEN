#!/usr/bin/env python
"""Run MOE-DUQGEN experiments remotely and aggregate the metrics."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from moe_qgen import (
    CommandResult,
    RemoteRunner,
    aggregate_metrics,
    ensure_local_dir,
    load_metrics_files,
    parse_time_output,
    plot_cluster_heatmap,
    plot_weight_heatmap,
)
from moe_qgen.metrics import MetricsSummary

logger = logging.getLogger("moe_qgen.runner")


@dataclass
class SubmitConfig:
    """Configuration for the cluster ``submit`` wrapper."""

    binary: str = "submit"
    poll_interval: int = 60
    job_dir: str = ".moe_jobs"
    extra_args: List[str] = field(default_factory=list)
    env: Optional[str] = None
    max_name_length: int = 80


_JOB_ID_RE = re.compile(r"(\d+)")
_JOB_NAME_SANITIZE = re.compile(r"[^A-Za-z0-9_-]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="YAML configuration file")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", help="Server password. If omitted, a prompt will be shown.")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts"),
        help="Local directory to store downloaded artifacts.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def load_config(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_env_commands(setup: Iterable[str], workdir: str) -> List[str]:
    commands = list(setup or [])
    commands.append(f"cd {workdir}")
    return commands


def parse_submit_config(config: Optional[Dict]) -> Optional[SubmitConfig]:
    if not config:
        return None
    return SubmitConfig(
        binary=config.get("binary", "submit"),
        poll_interval=int(config.get("poll_interval", 60)),
        job_dir=config.get("job_dir", ".moe_jobs"),
        extra_args=list(config.get("extra_args", [])),
        env=config.get("env"),
        max_name_length=int(config.get("max_name_length", 80)),
    )


def resolve_remote_path(path: str, workdir: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute():
        return str(pure)
    return str(PurePosixPath(workdir) / pure)


def relative_to_workdir(path: str, workdir: str) -> str:
    try:
        return str(PurePosixPath(path).relative_to(PurePosixPath(workdir)))
    except ValueError:
        return path


def sanitise_job_name(name: str, max_length: int) -> str:
    cleaned = _JOB_NAME_SANITIZE.sub("-", name).strip("-")
    if not cleaned:
        cleaned = "moe-job"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def extract_job_id(text: str) -> Optional[str]:
    matches = _JOB_ID_RE.findall(text)
    return matches[-1] if matches else None


def parse_exit_code(exit_code: str, state: str) -> int:
    primary = exit_code.split(":", 1)[0] if exit_code else "0"
    rc = int(primary) if primary.isdigit() else 0
    if state and state.upper() not in {"COMPLETED", "COMPLET"} and rc == 0:
        rc = 1
    return rc


def wait_for_job_completion(runner: RemoteRunner, job_id: str, poll_interval: int) -> None:
    logger.info("Waiting for job %s", job_id)
    while True:
        status = runner.run(f"squeue -h -j {job_id}")
        if status.return_code != 0 or not status.stdout.strip():
            break
        time.sleep(poll_interval)


def query_job_state(runner: RemoteRunner, job_id: str) -> Tuple[str, str]:
    status = runner.run(f"sacct -P -b -j {job_id} -o State,ExitCode")
    if status.return_code != 0:
        return "UNKNOWN", "0:0"
    lines = [line for line in status.stdout.splitlines() if line and not line.startswith("State")]
    if not lines:
        return "UNKNOWN", "0:0"
    parts = lines[-1].split("|")
    state = parts[0].strip()
    exit_code = parts[1].strip() if len(parts) > 1 else "0:0"
    return state, exit_code


def execute_via_submit(
    runner: RemoteRunner,
    *,
    command: str,
    env_commands: Sequence[str],
    submit_cfg: SubmitConfig,
    workdir: str,
    job_prefix: str,
    remote_output_dir: str,
    capture_time: bool,
) -> Tuple[CommandResult, Optional[str], Dict[str, str]]:
    job_root = resolve_remote_path(submit_cfg.job_dir, workdir)
    runner.ensure_remote_dirs([job_root, remote_output_dir])
    job_name = sanitise_job_name(job_prefix, submit_cfg.max_name_length)
    script_path = str(PurePosixPath(job_root) / f"{job_name}.sh")
    job_stdout = str(PurePosixPath(job_root) / f"{job_name}.out")
    job_stderr = str(PurePosixPath(job_root) / f"{job_name}.err")
    time_log = (
        str(PurePosixPath(remote_output_dir) / f"{job_name}.time") if capture_time else None
    )

    script_lines = ["#!/bin/bash", "set -euo pipefail"]
    script_lines.extend(env_commands)
    script_lines.append(f"mkdir -p {shlex.quote(remote_output_dir)}")
    if capture_time and time_log:
        script_lines.append(f"/usr/bin/time -v -o {shlex.quote(time_log)} {command}")
    else:
        script_lines.append(command)
    script_content = "\n".join(script_lines) + "\n"
    runner.write_text(script_path, script_content, mode=0o755)

    submit_parts: List[str] = [submit_cfg.binary, "-j", job_name, "-o", job_stdout, "-e", job_stderr]
    if submit_cfg.env:
        submit_parts.extend(["-env", submit_cfg.env])
    submit_parts.extend(submit_cfg.extra_args)
    submit_parts.extend(["--", "bash", script_path])
    submit_command = " ".join(shlex.quote(part) for part in submit_parts)

    submit_result = runner.run(submit_command)
    if submit_result.return_code != 0:
        raise RuntimeError(
            f"Failed to submit job '{job_name}': {submit_result.stderr or submit_result.stdout}"
        )
    job_id = extract_job_id(submit_result.stdout + submit_result.stderr)
    if not job_id:
        raise RuntimeError(f"Unable to determine job id for submission '{job_name}'.")

    start = time.time()
    wait_for_job_completion(runner, job_id, submit_cfg.poll_interval)
    end = time.time()
    state, exit_code = query_job_state(runner, job_id)
    rc = parse_exit_code(exit_code, state)

    stdout_text = runner.read_text(job_stdout) if runner.exists(job_stdout) else ""
    stderr_text = runner.read_text(job_stderr) if runner.exists(job_stderr) else ""
    time_text = runner.read_text(time_log) if capture_time and time_log and runner.exists(time_log) else None

    result = CommandResult(
        command=command,
        return_code=rc,
        stdout=stdout_text,
        stderr=stderr_text,
        start_time=start,
        end_time=end,
    )

    meta = {
        "job_id": job_id,
        "job_name": job_name,
        "job_stdout": job_stdout,
        "job_stderr": job_stderr,
        "time_log": time_log,
        "state": state,
        "exit_code": exit_code,
        "script_path": script_path,
    }
    logger.info("Job %s finished with state=%s exit=%s", job_id, state, exit_code)
    return result, time_text, meta


def execute_command(
    runner: RemoteRunner,
    *,
    command: str,
    env_commands: Sequence[str],
    submit_cfg: Optional[SubmitConfig],
    workdir: str,
    job_prefix: str,
    remote_output_dir: str,
    capture_time: bool,
) -> Tuple[CommandResult, Optional[str], Dict[str, str]]:
    if submit_cfg:
        return execute_via_submit(
            runner,
            command=command,
            env_commands=env_commands,
            submit_cfg=submit_cfg,
            workdir=workdir,
            job_prefix=job_prefix,
            remote_output_dir=remote_output_dir,
            capture_time=capture_time,
        )
    formatted_command = f"/usr/bin/time -v {command}" if capture_time else command
    result = runner.run(formatted_command, env=env_commands)
    time_text = result.stderr if capture_time else None
    meta = {
        "job_id": None,
        "job_name": job_prefix,
        "job_stdout": None,
        "job_stderr": None,
        "time_log": None,
        "state": "DIRECT",
        "exit_code": str(result.return_code),
        "script_path": None,
    }
    return result, time_text, meta


def ensure_remote_paths(runner: RemoteRunner, paths: Iterable[str]) -> None:
    for path in paths:
        if path:
            runner.ensure_remote_dirs([path])


def run_bootstrap(
    runner: RemoteRunner,
    commands: Iterable[str],
    env_commands: Sequence[str],
    submit_cfg: Optional[SubmitConfig],
    workdir: str,
) -> None:
    for idx, raw_cmd in enumerate(commands, start=1):
        command = raw_cmd.format(workdir=workdir)
        logger.info("[bootstrap %d] %s", idx, command)
        result, _, meta = execute_command(
            runner,
            command=command,
            env_commands=env_commands,
            submit_cfg=submit_cfg,
            workdir=workdir,
            job_prefix=f"bootstrap-{idx}",
            remote_output_dir=resolve_remote_path(submit_cfg.job_dir if submit_cfg else workdir, workdir),
            capture_time=False,
        )
        if result.return_code != 0:
            raise RuntimeError(
                f"Bootstrap command failed (job {meta.get('job_id') or 'direct'}): {result.stderr}"
            )


def run_experiment(
    runner: RemoteRunner,
    dataset: Dict,
    experiment: Dict,
    env_commands: Sequence[str],
    output_base: Path,
    workdir: str,
    submit_cfg: Optional[SubmitConfig],
) -> MetricsSummary:
    dataset_name = dataset["name"]
    experiment_id = experiment["id"]
    logger.info("=== Dataset: %s | Experiment: %s ===", dataset_name, experiment_id)

    remote_output_rel = experiment.get("remote_output", f"runs/{dataset_name}/{experiment_id}")
    remote_output_dir = resolve_remote_path(remote_output_rel, workdir)
    ensure_remote_paths(runner, [remote_output_dir])

    context = {
        **dataset,
        **experiment.get("params", {}),
        "output_dir": remote_output_dir,
        "output_dir_rel": relative_to_workdir(remote_output_dir, workdir),
        "workdir": workdir,
    }

    generation_cfg = experiment["generation"]
    measure_time = generation_cfg.get("measure_time", True)
    generation_cmd = generation_cfg["command"].format(**context)
    gen_result, time_text, gen_meta = execute_command(
        runner,
        command=generation_cmd,
        env_commands=env_commands,
        submit_cfg=submit_cfg,
        workdir=workdir,
        job_prefix=f"{dataset_name}-{experiment_id}-gen",
        remote_output_dir=remote_output_dir,
        capture_time=measure_time,
    )
    if gen_result.return_code != 0:
        raise RuntimeError(
            f"Generation command failed for {dataset_name}/{experiment_id}: {gen_result.stderr}"
        )
    time_info = parse_time_output(time_text) if time_text else None

    local_exp_dir = ensure_local_dir(output_base / dataset_name / experiment_id)

    artifact_templates = list(dict.fromkeys(generation_cfg.get("artifacts", [])))
    extra_artifacts: List[str] = []
    for key in ("job_stdout", "job_stderr", "time_log"):
        value = gen_meta.get(key)
        if value:
            extra_artifacts.append(value)

    for artifact in artifact_templates:
        remote_path = artifact.format(**context)
        local_path = local_exp_dir / Path(remote_path).name
        if runner.exists(remote_path):
            runner.download(remote_path, str(local_path))
        else:
            logger.warning("Remote artifact not found: %s", remote_path)

    for remote_path in extra_artifacts:
        local_path = local_exp_dir / Path(remote_path).name
        if runner.exists(remote_path):
            runner.download(remote_path, str(local_path))

    metric_paths: List[str] = []
    for idx, eval_cfg in enumerate(experiment.get("evaluation", []), start=1):
        eval_cmd = eval_cfg["command"].format(**context)
        eval_result, _, eval_meta = execute_command(
            runner,
            command=eval_cmd,
            env_commands=env_commands,
            submit_cfg=submit_cfg,
            workdir=workdir,
            job_prefix=f"{dataset_name}-{experiment_id}-eval{idx}",
            remote_output_dir=remote_output_dir,
            capture_time=False,
        )
        if eval_result.return_code != 0:
            raise RuntimeError(
                f"Evaluation command failed for {dataset_name}/{experiment_id}: {eval_result.stderr}"
            )
        metric_path = eval_cfg.get("metrics_file")
        if metric_path:
            metric_path = metric_path.format(**context)
            metric_paths.append(metric_path)
            remote_metric = metric_path
            local_metric = local_exp_dir / Path(remote_metric).name
            if runner.exists(remote_metric):
                runner.download(remote_metric, str(local_metric))
        # Download evaluation logs for reference if jobs were submitted
        if submit_cfg:
            for key in ("job_stdout", "job_stderr"):
                path = eval_meta.get(key)
                if path and runner.exists(path):
                    runner.download(path, str(local_exp_dir / Path(path).name))

    local_metric_files = [str(local_exp_dir / Path(p).name) for p in metric_paths]
    metrics = load_metrics_files(local_metric_files) if local_metric_files else {}

    summary = MetricsSummary(
        dataset=dataset_name,
        experiment=experiment_id,
        select_k_time=time_info.seconds if time_info else None,
        select_k_memory=time_info.max_rss_mb if time_info else None,
        avg_r1=metrics.get("avg_r1"),
        uniqueness=metrics.get("uniqueness"),
        coverage=metrics.get("coverage"),
        ndcg10=metrics.get("ndcg@10"),
        map=metrics.get("map"),
        mrr=metrics.get("mrr"),
    )

    if experiment.get("plots", True):
        weight_history = local_exp_dir / "weights_history.jsonl"
        cluster_stats_path = local_exp_dir / "cluster_stats.json"
        if weight_history.exists():
            plot_weight_heatmap(weight_history, local_exp_dir / "weights_heatmap.png")
            if cluster_stats_path.exists():
                with cluster_stats_path.open("r", encoding="utf-8") as f:
                    stats = json.load(f)
                cluster_count = int(stats.get("K", len(stats.get("cluster_sizes", []))))
                plot_cluster_heatmap(
                    weight_history,
                    cluster_count,
                    local_exp_dir / "cluster_heatmap.png",
                )

    return summary


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    config = load_config(args.config)
    output_dir = ensure_local_dir(args.output_dir)

    env_cfg = config.get("env", {})
    if "workdir" not in env_cfg:
        raise ValueError("Configuration must define env.workdir")
    workdir = env_cfg["workdir"]
    env_commands = build_env_commands(env_cfg.get("setup", []), workdir)
    submit_cfg = parse_submit_config(env_cfg.get("submit"))

    datasets = config.get("datasets", [])
    experiments = config.get("experiments", [])
    if not datasets:
        raise ValueError("No datasets specified in the configuration")
    if not experiments:
        raise ValueError("No experiments specified in the configuration")

    summaries: List[MetricsSummary] = []
    with RemoteRunner(
        host=args.host,
        user=args.user,
        password=args.password,
        port=args.port,
    ) as runner:
        bootstrap_cmds = env_cfg.get("bootstrap", [])
        if bootstrap_cmds:
            run_bootstrap(runner, bootstrap_cmds, env_commands, submit_cfg, workdir)
        for dataset in datasets:
            for experiment in experiments:
                summaries.append(
                    run_experiment(
                        runner,
                        dataset,
                        experiment,
                        env_commands,
                        output_dir,
                        workdir,
                        submit_cfg,
                    )
                )

    df = aggregate_metrics(summaries)
    table_path = output_dir / "summary.csv"
    df.to_csv(table_path, index=False)
    markdown_path = output_dir / "summary.md"
    df.to_markdown(markdown_path, index=False)
    logger.info("Summary saved to %s and %s", table_path, markdown_path)


if __name__ == "__main__":
    main()
