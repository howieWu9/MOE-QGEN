"""Utility helpers for MOE-DUQGEN automation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple

_TIME_PATTERN = re.compile(r"Elapsed \(wall clock\) time \((?:h:mm:ss|seconds)\) : (?P<value>.+)")
_MEM_PATTERN = re.compile(r"Maximum resident set size \(kbytes\): (?P<value>\d+)")


def ensure_local_dir(path: str | os.PathLike) -> Path:
    """Ensure that ``path`` exists locally and return a :class:`Path` instance."""

    path_obj = Path(path)
    path_obj.mkdir(parents=True, exist_ok=True)
    return path_obj


@dataclass
class TimeUsage:
    """Container describing execution time and memory usage."""

    seconds: float | None
    max_rss_mb: float | None

    def as_dict(self) -> Dict[str, float | None]:
        return {"select_k_time": self.seconds, "select_k_memory": self.max_rss_mb}


def _parse_wall_clock(value: str) -> float | None:
    """Parse the wall clock value emitted by ``/usr/bin/time -v``."""

    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    if value.count(":") == 2:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if value.count(":") == 1:
        minutes, seconds = value.split(":")
        return int(minutes) * 60 + float(seconds)
    try:
        return float(value)
    except ValueError:
        return None


def parse_time_output(text: str) -> TimeUsage:
    """Parse ``/usr/bin/time -v`` stderr text.

    Parameters
    ----------
    text:
        Raw stderr captured from the command.
    """

    seconds = None
    max_rss_mb = None
    for line in text.splitlines():
        m_time = _TIME_PATTERN.search(line)
        if m_time:
            seconds = _parse_wall_clock(m_time.group("value"))
            continue
        m_mem = _MEM_PATTERN.search(line)
        if m_mem:
            max_rss_mb = int(m_mem.group("value")) / 1024.0
    return TimeUsage(seconds=seconds, max_rss_mb=max_rss_mb)


def apply_template(template: str, context: Dict[str, str]) -> str:
    """Apply a basic ``str.format`` template with safe substitution."""

    try:
        return template.format(**context)
    except KeyError as exc:  # pragma: no cover - user error surfaced clearly
        missing = exc.args[0]
        raise KeyError(f"Missing placeholder '{missing}' in context for template: {template}") from exc


def apply_templates(templates: Iterable[str], context: Dict[str, str]) -> Tuple[str, ...]:
    return tuple(apply_template(t, context) for t in templates)
