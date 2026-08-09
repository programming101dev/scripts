#!/usr/bin/env python3
"""Render focused views from a verified p101 analysis directory."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


EXIT_FINDINGS = 1
EXIT_TROUBLE = 2


def model_module() -> ModuleType:
    path = Path(__file__).resolve().with_name("p101-model.py")
    spec = importlib.util.spec_from_file_location("p101_model_view", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load p101 model verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="p101-view.py",
        description="Render a focused view from a verified p101 analysis.",
    )
    parser.add_argument(
        "view", choices=("report", "resource", "sync", "trace")
    )
    parser.add_argument("analysis_dir")
    parser.add_argument("-j", "--json", action="store_true")
    parser.add_argument("-m", "--mermaid", action="store_true")
    parser.add_argument("-s", "--summary", action="store_true")
    parser.add_argument("-o", "--output")
    return parser.parse_args(argv)


def select_artifact(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.view == "report":
        if args.summary:
            return "summary.md", None
        if args.mermaid:
            return "resource-lifetimes.md", None
        return (
            ("correlated-report.json", "correlated-report.json")
            if args.json
            else ("correlated-report.txt", "correlated-report.json")
        )
    if args.mermaid:
        raise ValueError("--mermaid is only supported by the report view")
    if args.view == "resource":
        if args.summary:
            raise ValueError("--summary is only supported by report and trace")
        return (
            ("resource-report.json", "resource-report.json")
            if args.json
            else ("resource-report.txt", "resource-report.json")
        )
    if args.view == "sync":
        if args.summary:
            raise ValueError("--summary is only supported by report and trace")
        return (
            ("concurrency-report.json", "concurrency-report.json")
            if args.json
            else ("concurrency-report.txt", "concurrency-report.json")
        )
    if args.json or args.mermaid:
        raise ValueError("the trace view supports tree output or --summary")
    return (
        ("trace-summary.txt", "correlated-report.json")
        if args.summary
        else ("trace-tree.txt", "correlated-report.json")
    )


def finding_status(
    directory: Path, document_name: str | None, policy: str
) -> int:
    if document_name is None:
        return 0
    try:
        document = json.loads(
            (directory / document_name).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return EXIT_TROUBLE
    findings = document.get("findings")
    if not isinstance(findings, list):
        return EXIT_TROUBLE
    if policy == "report":
        return EXIT_FINDINGS if findings else 0
    if policy in {"resource", "synchronization", "trace"}:
        matches = [
            finding
            for finding in findings
            if isinstance(finding, dict)
            and finding.get("policy") == policy
        ]
        return EXIT_FINDINGS if matches else 0
    return 0


def lesson_appendix(
    directory: Path, document_name: str | None, policy: str
) -> str:
    if document_name is None:
        return ""
    try:
        document = json.loads(
            (directory / document_name).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    findings = document.get("findings")
    if not isinstance(findings, list):
        return ""
    rows: list[str] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if policy != "report" and finding.get("policy") != policy:
            continue
        lesson = finding.get("lesson")
        primary = lesson.get("primary") if isinstance(lesson, dict) else None
        if not isinstance(primary, dict):
            continue
        finding_id = str(finding.get("id", "?"))
        url = str(primary.get("url", ""))
        key = (finding_id, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            f"- {finding_id}: {primary.get('title', 'lesson')} — {url}"
        )
    if not rows:
        return ""
    return "\nLessons:\n" + "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    directory = Path(args.analysis_dir).expanduser().resolve()
    if not directory.is_dir():
        print(f"p101 {args.view}: analysis directory not found: {directory}", file=sys.stderr)
        return EXIT_TROUBLE
    verifier = model_module()
    try:
        receipt_result = verifier.receipt_result(directory)
        artifact, finding_document = select_artifact(args)
    except (verifier.ModelError, ValueError) as error:
        print(f"p101 {args.view}: {error}", file=sys.stderr)
        return EXIT_TROUBLE
    if receipt_result == "trouble":
        print(f"p101 {args.view}: analysis receipt records tool trouble", file=sys.stderr)
        return EXIT_TROUBLE
    try:
        contents = (directory / artifact).read_text(encoding="utf-8")
        policy = "synchronization" if args.view == "sync" else args.view
        if not args.json and not args.mermaid:
            contents += lesson_appendix(directory, finding_document, policy)
        if args.output is None:
            sys.stdout.write(contents)
        else:
            output = Path(args.output).expanduser()
            output.write_text(contents, encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"p101 {args.view}: cannot render {artifact}: {error}", file=sys.stderr)
        return EXIT_TROUBLE
    return finding_status(directory, finding_document, policy)


if __name__ == "__main__":
    raise SystemExit(main())
