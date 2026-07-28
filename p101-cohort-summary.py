#!/usr/bin/env python3
"""Summarize p101 JSON findings across many submissions."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate p101 report JSON files for an instructor/cohort view.")
    parser.add_argument("paths", nargs="+", type=Path, help="JSON files or directories containing JSON reports")
    parser.add_argument("-j", "--json", action="store_true", help="emit machine-readable JSON instead of Markdown")
    return parser.parse_args(argv)


def discover_json(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    missing: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            files.append(path)
        else:
            missing.append(path)
    return files, missing


def load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def finding_id(finding: dict[str, Any]) -> str:
    value = finding.get("id")
    if isinstance(value, str) and value:
        return value
    kind = finding.get("kind")
    if isinstance(kind, str) and kind:
        return kind
    return "unknown"


def markdown_cell(text: object) -> str:
    value = str(text)
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`").replace("\r", " ").replace("\n", " ")


def collect(files: list[Path]) -> dict[str, Any]:
    diagnostics: Counter[str] = Counter()
    by_file: dict[str, Counter[str]] = defaultdict(Counter)
    submissions_with_findings = 0
    parsed_files = 0

    for path in files:
        data = load_json(path)
        if not isinstance(data, dict):
            continue

        findings = data.get("findings")
        if not isinstance(findings, list):
            continue

        parsed_files += 1
        if findings:
            submissions_with_findings += 1

        for finding in findings:
            if not isinstance(finding, dict):
                continue
            diag = finding_id(finding)
            diagnostics[diag] += 1
            site = finding.get("site")
            if isinstance(site, dict):
                source = site.get("file")
                if isinstance(source, str) and source:
                    by_file[source][diag] += 1

    return {
        "reports_read": parsed_files,
        "reports_with_findings": submissions_with_findings,
        "diagnostics": dict(sorted(diagnostics.items())),
        "files": {source: dict(sorted(counter.items())) for source, counter in sorted(by_file.items())},
    }


def print_markdown(summary: dict[str, Any]) -> None:
    print("# p101 cohort summary")
    print()
    print(f"- Reports read: {summary['reports_read']}")
    print(f"- Reports with findings: {summary['reports_with_findings']}")
    print()
    print("## Diagnostics")
    print()
    print("| ID | Count |")
    print("| --- | ---: |")
    for diag, count in sorted(summary["diagnostics"].items(), key=lambda item: (-item[1], item[0])):
        print(f"| `{markdown_cell(diag)}` | {count} |")
    print()
    print("## Source hot spots")
    print()
    print("| Source | Findings |")
    print("| --- | --- |")
    for source, diagnostics in summary["files"].items():
        text = ", ".join(f"{markdown_cell(diag)}: {count}" for diag, count in diagnostics.items())
        print(f"| `{markdown_cell(source)}` | {text} |")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    files, missing = discover_json(args.paths)
    if missing:
        for path in missing:
            print(f"p101-cohort-summary: input path not found: {path}", file=sys.stderr)
        return 2
    try:
        summary = collect(files)
    except ValueError as exc:
        print(f"p101-cohort-summary: {exc}", file=sys.stderr)
        return 2
    if summary["reports_read"] == 0:
        print("p101-cohort-summary: no valid p101 finding reports were found", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_markdown(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
