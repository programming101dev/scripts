#!/usr/bin/env python3
"""Presentation-only helpers for the governed p101 check graph."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any


def markdown_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def log_result(log_path: Path, fallback: str) -> tuple[str, int, int]:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        size = log_path.stat().st_size
    except OSError:
        return fallback, 0, 0
    lines = content.splitlines()
    result = fallback
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("$ ") and not stripped.startswith("# retry "):
            result = stripped
            break
    return result, size, len(lines)


def write_summary(
    output: Path, records: list[dict[str, Any]], document: dict[str, Any]
) -> None:
    lines = [
        "# p101 governed check graph",
        "",
        f"- Host: {platform.system()} {platform.release()} {platform.machine()}",
        f"- Graph: `{document['schema']}`",
        "",
        "| Status | Check | Time | Result | Artifact |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for record in sorted(records, key=lambda item: item.get("order", 0)):
        lines.append(
            f"| {record['outcome'].upper()} | {record['title']} | "
            f"{record['duration_ns'] / 1_000_000_000:.3f}s | "
            f"{markdown_cell(record['result'])} | "
            f"[log](./logs/{record['id']}.log) |"
        )
    lines.extend(["", "## Limits", "", document["does_not_prove"], ""])
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def write_profile(
    output: Path,
    records: list[dict[str, Any]],
    *,
    mode: str = "functional",
    elapsed_ns: int | None = None,
) -> None:
    total_ns = sum(record["duration_ns"] for record in records)
    elapsed_ns = total_ns if elapsed_ns is None else elapsed_ns
    lines = [
        "# p101 post-update profile",
        "",
        f"- Mode: {mode}",
        f"- Checks invoked: {len(records)}",
        f"- Sum of check wall times: {total_ns / 1_000_000_000:.3f}s",
        f"- End-to-end graph time: {elapsed_ns / 1_000_000_000:.3f}s",
        "- Timing scope: each governed child command, measured with `time.time_ns()`.",
        (
            "- Interpretation: isolated sequential measurements; cache reuse is disabled."
            if mode == "measurement"
            else "- Interpretation: functional throughput; concurrency and cache reuse may affect individual timings."
        ),
        "",
        "## Invocation order",
        "",
        "| # | Check | Outcome | Exit | Time | Log | Result |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for index, record in enumerate(
        sorted(records, key=lambda item: item.get("order", 0)), start=1
    ):
        lines.append(
            f"| {index} | `{record['id']}` | {record['outcome']} | "
            f"{record['return_code']} | "
            f"{record['duration_ns'] / 1_000_000_000:.3f}s | "
            f"{record['log_bytes']} B / {record['log_lines']} lines | "
            f"{markdown_cell(record['result'])} |"
        )
    lines.extend(
        [
            "",
            "## Slowest checks",
            "",
            "| Rank | Check | Time | Share of measured check time |",
            "| ---: | --- | ---: | ---: |",
        ]
    )
    for rank, record in enumerate(
        sorted(records, key=lambda item: item["duration_ns"], reverse=True),
        start=1,
    ):
        share = 0.0 if total_ns == 0 else 100.0 * record["duration_ns"] / total_ns
        lines.append(
            f"| {rank} | `{record['id']}` | "
            f"{record['duration_ns'] / 1_000_000_000:.3f}s | {share:.1f}% |"
        )
    lines.append("")
    (output / "profile.md").write_text("\n".join(lines), encoding="utf-8")
