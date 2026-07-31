#!/usr/bin/env python3
"""Run the executable 10x wrapper conformance contract.

The checked API and unit-test manifests admit the wrapper surface and fixtures.
Clang-derived instrumentation supplies fault/resource capabilities. Each
library test suite is replayed with call logging enabled, and p101-trace (which
uses lib_tool_event) normalizes the protocol before this checker evaluates
per-wrapper runtime obligations.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
CONTRACT_PATH = SCRIPT_DIR / "wrapper-conformance-contract.json"
INSTRUMENTATION_PATH = SCRIPT_DIR / "instrumentation-contract.json"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def active_libraries() -> dict[str, Path]:
    roles = json.loads(INSTRUMENTATION_PATH.read_text(encoding="utf-8"))[
        "library_roles"
    ]
    return {
        name: WORKSPACE / "libraries" / name
        for name, role in sorted(roles.items())
        if role != "infrastructure"
    }


def find_program(repo: Path, name: str) -> Path:
    marker = repo / ".last-build-dir"
    candidates: list[Path] = []
    if marker.is_file():
        candidates.append(repo / marker.read_text(encoding="utf-8").strip() / name)
    candidates.extend(sorted(repo.glob(f"build-*/{name}"), reverse=True))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(f"cannot find a built {name}; build {repo.name} first")


def normalize_calls(trace: Path, source: Path, output: Path) -> None:
    result = subprocess.run(
        [str(trace), "-f", str(source)],
        stdout=output.open("w", encoding="utf-8"),
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    # Test processes do not use the capture conductor, so producer-completion
    # and whole-stack checks may return 2. Parsing itself must still be clean.
    diagnostic = result.stderr
    if result.returncode != 0 and (
        "0 malformed records, 0 unsupported-version records" not in diagnostic
    ):
        raise RuntimeError(
            f"p101-trace rejected {source}: {diagnostic.strip()}"
        )


def call_evidence(path: Path) -> tuple[Counter[str], Counter[str], set[str], set[str]]:
    enters: Counter[str] = Counter()
    exits: Counter[str] = Counter()
    arguments: set[str] = set()
    results: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 11:
                raise RuntimeError(f"malformed normalized call record: {line!r}")
            event = fields[4]
            name = fields[5]
            if event == "ENTER":
                enters[name] += 1
                if fields[7] != "-":
                    arguments.add(name)
            elif event == "EXIT":
                exits[name] += 1
                if fields[8] != "-":
                    results.add(name)
    return enters, exits, arguments, results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run per-wrapper runtime conformance checks."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "p101-wrapper-conformance",
    )
    parser.add_argument("--library", action="append", default=[])
    args = parser.parse_args()

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("schema") != "p101-wrapper-conformance-contract-v1":
        print("FAIL: unsupported wrapper conformance contract")
        return 2
    selected = active_libraries()
    if args.library:
        requested = set(args.library)
        unknown = requested - selected.keys()
        if unknown:
            print(f"FAIL: unknown libraries: {', '.join(sorted(unknown))}")
            return 2
        selected = {name: selected[name] for name in sorted(requested)}

    args.output.mkdir(parents=True, exist_ok=True)
    trace = find_program(WORKSPACE / "programs" / "p101-trace", "p101-trace")
    instrumentation_receipt = args.output / "instrumentation.json"
    instrumentation = subprocess.run(
        [
            str(SCRIPT_DIR / "check-p101-instrumentation.py"),
            "--receipt",
            str(instrumentation_receipt),
        ],
        cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    (args.output / "instrumentation.log").write_text(
        instrumentation.stdout, encoding="utf-8"
    )
    if instrumentation.returncode != 0:
        print("FAIL: static instrumentation contract failed")
        return 1
    capability_rows = json.loads(
        instrumentation_receipt.read_text(encoding="utf-8")
    )["function_capabilities"]
    capabilities = {
        (row["library"], row["function"]): row for row in capability_rows
    }
    failures: list[str] = []
    receipts: list[dict[str, object]] = []
    required_arguments = set(contract["logging"]["arguments_required"])
    required_results = set(contract["logging"]["results_required"])

    for library, repo in selected.items():
        api = {row["function"] for row in rows(repo / "api-manifest.tsv")}
        tests = {
            row["function"]: row["test_kind"]
            for row in rows(repo / "test" / "unit-test-manifest.tsv")
        }
        call_log = args.output / f"{library}.calls.log"
        resource_log = args.output / f"{library}.resources.log"
        normalized = args.output / f"{library}.calls.tsv"
        call_log.unlink(missing_ok=True)
        resource_log.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "P101_CALL_LOG": str(call_log),
                "P101_CALL_LOG_ARGS": "1",
                "P101_CALL_LOG_RESULT": "1",
                "P101_RESOURCE_LOG": str(resource_log),
            }
        )
        result = subprocess.run(
            ["./test.sh"],
            cwd=repo,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        (args.output / f"{library}.test.log").write_text(
            result.stdout, encoding="utf-8"
        )
        if result.returncode != 0:
            failures.append(f"{library}: test.sh failed")
            continue
        if not call_log.is_file():
            failures.append(f"{library}: tests emitted no call log")
            continue
        try:
            normalize_calls(trace, call_log, normalized)
            enters, exits, arguments, results = call_evidence(normalized)
        except RuntimeError as exc:
            failures.append(f"{library}: {exc}")
            continue

        trace_api = {
            name
            for name in api
            if capabilities[(library, name)]["has_env"]
        }
        missing_calls = sorted(name for name in trace_api if enters[name] == 0)
        unbalanced = sorted(
            name for name in trace_api if enters[name] != exits[name]
        )
        missing_failure = sorted(
            name for name in api if tests.get(name) == "fault" and enters[name] == 0
        )
        missing_arguments = sorted((api & required_arguments) - arguments)
        missing_results = sorted((api & required_results) - results)
        for name in missing_calls:
            failures.append(f"{library}:{name}: no runtime success/failure invocation")
        for name in unbalanced:
            failures.append(
                f"{library}:{name}: ENTER={enters[name]} EXIT={exits[name]}"
            )
        for name in missing_failure:
            failures.append(f"{library}:{name}: fault test emitted no call")
        for name in missing_arguments:
            failures.append(f"{library}:{name}: required arguments were not logged")
        for name in missing_results:
            failures.append(f"{library}:{name}: required result was not logged")
        receipts.append(
            {
                "library": library,
                "apis": len(api),
                "trace_applicable": len(trace_api),
                "invoked": len(trace_api - set(missing_calls)),
                "balanced": len(trace_api - set(unbalanced)),
                "fault_tests": sum(kind == "fault" for kind in tests.values()),
                "arguments_logged": len(api & arguments),
                "results_logged": len(api & results),
                "passed": not (
                    missing_calls
                    or unbalanced
                    or missing_failure
                    or missing_arguments
                    or missing_results
                ),
            }
        )

    receipt = {
        "schema": "p101-wrapper-conformance-receipt-v1",
        "contract": str(CONTRACT_PATH),
        "libraries": receipts,
        "public_apis": sum(int(item["apis"]) for item in receipts),
        "failures": failures,
        "passed": not failures,
    }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrapper conformance: {receipt['public_apis']} APIs, "
        f"{len(receipts)} libraries"
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(f"wrapper conformance passed: {args.output / 'receipt.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
