# MOE-QGEN

Automation utilities for reproducing MOE-enhanced DUQGEN experiments on remote
servers. The toolkit mirrors the DUQGEN directory layout, submits jobs through
the cluster `submit` wrapper, and gathers everything required for the final
report—including baseline results, ablations A1–A5, and visual analytics.

## Key capabilities

- 🔁 **One command for all experiments** – run the full DUQGEN suite (baseline +
  A1–A5) across FiQA, BEIR, and NQ datasets by entering the server password
  once.
- 🧠 **Head-node aware orchestration** – automatically wraps every generation
  and evaluation job with the university `submit` tool, polls `squeue/sacct`,
  and enforces the DUQGEN repository layout on the compute nodes.
- 📦 **Asset bootstrapping** – optional bootstrap commands pre-download
  LLaMA-2, Contriever, ColBERTv2, and BEIR collections so the generation step is
  fully offline.
- 📊 **Complete reporting** – collects `/usr/bin/time` statistics, uniqueness,
  coverage, nDCG@10/MAP/MRR, expert-weight and cluster heatmaps, and writes a
  Markdown/CSV experiment table ready for the paper.
- 🛠️ **Fail-fast debugging** – job stdout/stderr, weight histories, and cluster
  metadata are mirrored locally for each dataset/experiment pair.

## Installation

Create a Python ≥3.9 environment on your workstation (or Codespace) and install
local dependencies:

```bash
pip install -r requirements.txt
```

These packages are only needed for the automation client (SSH + plotting). The
remote DUQGEN environment keeps using its own Conda setup.

## Server configuration

All remote settings live in `configs/duqgen_moe.yaml`.

### `env.workdir`
Absolute path to the DUQGEN repository on the server. The automation always
changes into this directory before executing commands so that the DUQGEN folder
structure remains untouched.

### `env.setup`
Shell commands executed at the start of every job (e.g. sourcing your
`~/.bashrc` and activating the `duqgen` Conda environment).

### `env.bootstrap`
Commands that should run **once** before the experiment sweep. They execute via
`submit` to respect the head-node restriction and typically:

- install DUQGEN requirements,
- download LLaMA-2/Contriever/ColBERT weights with `huggingface-cli`,
- pull BEIR datasets (example Python one-liner provided),
- prepare retrieval runs used by DUQGEN evaluation.

Placeholders like `{workdir}` are expanded automatically.

### `env.submit`
Parameters for the HPC job wrapper. The defaults match the H100 Head Unit
manual:

- `binary`: command name (`submit`).
- `env`: Conda environment passed to `-env`.
- `job_dir`: directory under the DUQGEN tree that stores job scripts/logs.
- `poll_interval`: seconds between `squeue` checks.
- `extra_args`: additional flags such as `-p gpu-test` if you need a specific
  partition.

### `datasets`
Absolute paths to the DUQGEN collections/embeddings/runs. The sample file lists
FiQA, NQ, and the BEIR datasets used in the paper—replace `<user>` with your
account name.

### `experiments`
Each block represents one run (baseline or ablation). Command templates can use
any field from the dataset, experiment params, or `output_dir`/`output_dir_rel`
(which points to `runs/<dataset>/<experiment>` inside `env.workdir`). The
provided definitions cover:

- A1 – automatic K (full vs. subsampling).
- A2 – expert removal (neighbour, TF-IDF, novelty, cluster).
- A3 – reward scheduling (r1, r1+r2, r1+r2+r3).
- A4 – anti-collapse safeguards.
- A5 – multi-candidate sampling vs. deduplication.

## Running everything in one step

From your local machine or Codespace:

```bash
python scripts/run_remote_experiments.py \
  --config configs/duqgen_moe.yaml \
  --host <login_node_ip> \
  --user <username> \
  --password <password>
```

What happens next:

1. The script connects via SSH and, if configured, kicks off the bootstrap job
   to download models/datasets.
2. For each dataset/experiment it creates a job script under
   `<workdir>/runtime/moe_jobs`, submits it with `submit`, and monitors
   completion with `squeue`/`sacct`.
3. `/usr/bin/time -v` metrics land in `<output_dir>/<job>.time`; stdout/stderr
   are saved alongside the job logs for post-mortem analysis.
4. Generated queries, weight trajectories, cluster stats, evaluation metrics,
   and job logs are downloaded to `./artifacts/<dataset>/<experiment>/`.
5. Expert-weight and cluster heatmaps are rendered locally and stored with the
   artifacts.
6. Once all jobs finish, the per-run metrics are merged into
   `artifacts/summary.csv` and `artifacts/summary.md`.

If any job fails (non-zero exit or `sacct` reports a non-`COMPLETED` state) the
pipeline stops immediately and surfaces the offending stdout/stderr.

## Output layout

```
artifacts/
  ├── fiqa/
  │   └── moe_full/
  │       ├── generated_queries.jsonl
  │       ├── weights_history.jsonl
  │       ├── weights_heatmap.png
  │       ├── cluster_stats.json
  │       ├── cluster_heatmap.png
  │       ├── <job>.out / <job>.err / <job>.time
  │       └── metrics.json
  ├── ... other datasets / experiments ...
  ├── summary.csv
  └── summary.md
```

The CSV/Markdown table includes K-selection time & memory, average r1,
uniqueness, coverage, nDCG@10, MAP, and MRR—matching the DUQGEN reporting
requirements.

## Tips

- Ensure `/usr/bin/time` is available on compute nodes. Adjust the command in
  the configuration if it lives elsewhere.
- The bootstrap commands may require additional Python packages on the remote
  side (`huggingface_hub`, `beir`, etc.). Install them inside the DUQGEN Conda
  environment referenced by `env.setup`.
- Use `submit` utilities (`squeue`, `sacct`, `scancel`) for manual inspection or
  cancellation; the automation prints job IDs in the log.
- To add more datasets or ablations, append new entries to `datasets` or
  `experiments`; outputs will continue to mirror the DUQGEN directory tree.

With the configuration in place, typing your password once is enough to launch
MOE question generation, DUQGEN evaluation, and result aggregation end-to-end.

## 快速上手（中文）

如果你希望直接在 Codespaces（或本地机器）里一次性完成所有 DUQGEN + MOE 的实验，按照下面的步骤操作即可：

1. **安装本地依赖**：

   ```bash
   pip install -r requirements.txt
   ```

2. **检查配置文件**：打开 `configs/duqgen_moe.yaml`，根据你的服务器环境修改：
   - `env.workdir`：服务器上 DUQGEN 仓库的绝对路径；脚本会自动进入该目录，保证目录结构不被破坏。
   - `env.setup`：例如 `source ~/.bashrc && conda activate duqgen`，保证后续命令在正确的 Conda 环境中执行。
   - `env.bootstrap`：首次运行时需要下载模型/数据集（LLaMA、Contriever、ColBERT、BEIR/NQ/FiQA 等）可在这里写好命令，脚本会自动通过 `submit` 提交一次性执行。
   - `datasets`：把 FiQA、NQ、BEIR 数据集在服务器上的实际路径填进去。
   - `experiments`：默认已经列出基线和 A1–A5 消融，不需要改动即可覆盖论文里的所有实验。

3. **一键启动**：在 Codespaces 里运行下列命令，按提示输入服务器密码即可：

   ```bash
   python scripts/run_remote_experiments.py \
     --config configs/duqgen_moe.yaml \
     --host <登录节点 IP 或主机名> \
     --user <服务器用户名> \
     --password <服务器密码>
   ```

4. **等待自动化完成**：脚本会顺序完成以下操作，无需手动干预：
   - （可选）执行 `env.bootstrap` 中的下载脚本，只运行一次。
   - 针对每个数据集、每种实验（基线 + A1–A5）生成 job 脚本，调用 `submit` 提交，并轮询 `squeue/sacct` 等待完成。
   - 自动记录 `/usr/bin/time -v`，保存 stdout/stderr、权重轨迹、聚类统计、生成的 query、评估指标。
   - 将所有产物下载到本地 `artifacts/<dataset>/<experiment>/` 目录，并自动绘制权重/聚类热力图。
   - 汇总为 `artifacts/summary.csv` 与 `artifacts/summary.md`，里面包含选 K 时间/显存、平均 r1、唯一率、覆盖率、nDCG@10/MAP/MRR。

5. **查看结果**：所有日志、图表和指标都已经整理好，只需要打开 `artifacts/` 下的文件即可撰写报告。

> 小贴士：如果服务器需要跳板机或多重 SSH，可以把 `--host` 设置为最终可达的登录节点；如需自定义端口、密钥登录等参数，可在 `python scripts/run_remote_experiments.py --help` 中查看更多选项。
