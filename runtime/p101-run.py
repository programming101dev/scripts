#!/usr/bin/env python3
"""Capture one command and analyze the resulting immutable event streams."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_TROUBLE = 2


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="p101 run",
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
    output = args.output
    if output is None:
        output = Path(
            tempfile.mkdtemp(prefix="p101-run.", dir=str(Path.cwd()))
        )
    else:
        if output.exists() or output.is_symlink():
            print(f"p101 run: output path already exists: {output}", file=sys.stderr)
            return EXIT_TROUBLE
        output.mkdir(parents=True)
    output = output.resolve()
    capture = output / "capture"
    analysis = output / "analysis"

    if args.observe_tool is None:
        observe_command = [str(script_dir / "p101"), "observe"]
    else:
        observe_command = [str(args.observe_tool), "-C"]
    if args.log_arguments:
        observe_command.append("-A")
    if args.log_results:
        observe_command.append("-R")
    observe_command.extend(["-o", str(capture), "--", *args.command])
    observed = subprocess.run(observe_command, check=False)
    if observed.returncode > EXIT_FINDINGS:
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
    analyzed = subprocess.run(analyze_command, check=False)
    if analyzed.returncode > EXIT_FINDINGS:
        return EXIT_TROUBLE

    status = max(observed.returncode, analyzed.returncode)
    print(f"p101 run output: {output}")
    print(f"Capture: {capture}")
    print(f"Analysis: {analysis}")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
