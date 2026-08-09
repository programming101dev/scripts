#!/usr/bin/env python3
"""Capture one command and analyze the resulting immutable event streams."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_TROUBLE = 2


def built_tool(repository: Path, name: str) -> Path:
    markers = (repository / ".last-runtime-build-dir", repository / ".last-build-dir")
    candidates: list[Path] = []
    for marker in markers:
        if marker.is_file():
            build_dir = marker.read_text(encoding="utf-8").strip()
            if build_dir:
                candidates.append(repository / build_dir / name)
    candidates.extend(
        repository / build_dir / name
        for build_dir in ("build-clang-22", "build-clang", "build-gcc")
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(f"{name} is not built in {repository}")


def normalized_status(returncode: int) -> int:
    return (
        returncode
        if returncode in {EXIT_CLEAN, EXIT_FINDINGS}
        else EXIT_TROUBLE
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="p101-run.py",
        description="capture one command, then build and analyze one shared run model",
    )
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("-A", "--log-arguments", action="store_true")
    parser.add_argument("-R", "--log-results", action="store_true")
    parser.add_argument("--observe-tool", type=Path)
    parser.add_argument("--analyze-tool", type=Path)
    parser.add_argument("--model-tool", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    invocation_dir = Path(os.environ.get("P101_INVOCATION_CWD", Path.cwd())).resolve()
    output = args.output
    if output is None:
        output = Path(
            tempfile.mkdtemp(prefix="p101-run.", dir=str(invocation_dir))
        )
    else:
        if not output.is_absolute():
            output = invocation_dir / output
        if output.exists() or output.is_symlink():
            print(f"p101 run: output path already exists: {output}", file=sys.stderr)
            return EXIT_TROUBLE
        output.mkdir(parents=True)
    output = output.resolve()
    capture = output / "capture"
    analysis = output / "analysis"

    if args.observe_tool is None:
        inspect_repository = script_dir.parent.parent / "programs" / "p101-inspect"
        try:
            observe_command = [str(built_tool(inspect_repository, "inspect-capture"))]
        except FileNotFoundError as error:
            print(f"p101 run: {error}", file=sys.stderr)
            return EXIT_TROUBLE
    else:
        observe_command = [str(args.observe_tool)]
    if args.log_arguments:
        observe_command.append("-A")
    if args.log_results:
        observe_command.append("-R")
    observe_command.extend(["-o", str(capture), "--", *args.command])
    observed = subprocess.run(observe_command, cwd=invocation_dir, check=False)
    observed_status = normalized_status(observed.returncode)
    if observed_status == EXIT_TROUBLE:
        print(
            f"p101 run: capture failed with status {observed.returncode}",
            file=sys.stderr,
        )
        return EXIT_TROUBLE

    analyze_command = [
        str(args.analyze_tool or (script_dir / "p101-analyze.py")),
        "-o",
        str(analysis),
    ]
    if args.model_tool is not None:
        analyze_command.extend(["--model-tool", str(args.model_tool)])
    analyze_command.append(str(capture))
    analyzed = subprocess.run(analyze_command, cwd=invocation_dir, check=False)
    analyzed_status = normalized_status(analyzed.returncode)
    if analyzed_status == EXIT_TROUBLE:
        return EXIT_TROUBLE

    status = max(observed_status, analyzed_status)
    print(f"p101 run output: {output}")
    print(f"Capture: {capture}")
    print(f"Analysis: {analysis}")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
