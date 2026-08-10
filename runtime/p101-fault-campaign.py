#!/usr/bin/env python3
"""Run contract-derived p101 fault campaigns for selected wrappers."""

from __future__ import annotations

import argparse
import csv
import errno
import json
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wrapper_fault_contract import (
    fault_domain,
    fault_symbol_header,
    injected_fault_cases,
)


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
FAILURE_CONTRACT = SCRIPT_ROOT / "contracts" / "wrapper-failure-contract.json"
PLATFORM_CONTRACT = SCRIPT_ROOT / "contracts" / "wrapper-platform-faults.json"
SEMANTICS_CONTRACT = SCRIPT_ROOT / "contracts" / "wrapper-fault-semantics.json"
WORKSPACE = SCRIPT_ROOT.parent


def built_tool(repository: Path, name: str, environment_name: str) -> Path:
    candidates: list[Path] = []
    configured = os.environ.get(environment_name)
    if configured:
        candidates.append(Path(configured))
    for marker_name in (".last-runtime-build-dir", ".last-build-dir"):
        marker = repository / marker_name
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
    raise ValueError(f"{name} is not built in {repository}")


@dataclass(frozen=True)
class Scenario:
    wrapper: str
    mode: str
    code_name: str
    code_value: int | None
    domain: str
    symbol_header: str


def _host_platform() -> str:
    name = platform.system().lower()
    if name == "darwin":
        return "macos"
    if name == "freebsd":
        return "freebsd"
    if name == "linux":
        return "linux"
    raise ValueError(f"unsupported host platform: {platform.system()}")


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def build_scenarios(
    wrappers: list[str], platform_name: str, library: str | None
) -> list[Scenario]:
    failures_by_name = _load(FAILURE_CONTRACT).get("wrappers", {})
    failures = {
        record.get("function_usr"): record
        for record in failures_by_name.values()
        if isinstance(record, dict) and record.get("function_usr")
    }
    platform_contract = _load(PLATFORM_CONTRACT)
    wrapper_mappings = {
        record.get("function_usr"): record
        for record in platform_contract.get("wrappers", {}).values()
        if isinstance(record, dict) and record.get("function_usr")
    }
    semantics = _load(SEMANTICS_CONTRACT).get("modes", {})
    wrapper_usrs: dict[str, str] = {}
    for manifest in sorted(
        (WORKSPACE / "libraries").glob("lib_*/api-manifest.tsv")
    ):
        with manifest.open(encoding="utf-8") as stream:
            for row in csv.DictReader(stream, delimiter="\t"):
                previous = wrapper_usrs.setdefault(
                    row["function"],
                    row["function_usr"],
                )
                if previous != row["function_usr"]:
                    raise ValueError(
                        f"ambiguous public API spelling: {row['function']}"
                    )
    if not wrappers:
        wrappers = sorted(
            name
            for name, function_usr in wrapper_usrs.items()
            for record in (failures.get(function_usr),)
            if isinstance(record, dict)
            if library is None or record.get("library") == library
        )
    scenarios: list[Scenario] = []
    for wrapper in wrappers:
        canonical = wrapper
        wrapper_usr = wrapper_usrs.get(canonical)
        failure = failures.get(wrapper_usr)
        if not isinstance(failure, dict):
            raise ValueError(f"wrapper is absent from the failure contract: {wrapper}")
        if library is not None and failure.get("library") != library:
            raise ValueError(f"{canonical} is not owned by {library}")
        wrapper_mapping = wrapper_mappings.get(wrapper_usr)
        if not isinstance(wrapper_mapping, dict):
            raise ValueError(
                f"{canonical} has no wrapper-to-native platform mapping"
            )
        native_name = wrapper_mapping.get("function")
        if not isinstance(native_name, str) or not native_name:
            raise ValueError(f"{canonical} has an invalid native function mapping")
        code_names = injected_fault_cases(
            platform_contract,
            native_name,
            platform_name,
        )
        domain = fault_domain(platform_contract, native_name)
        symbol_header = fault_symbol_header(platform_contract, native_name)
        for code_name in code_names:
            code_value = getattr(errno, code_name, None)
            scenarios.append(
                Scenario(
                    canonical,
                    "error",
                    code_name,
                    code_value if isinstance(code_value, int) else None,
                    domain,
                    symbol_header,
                )
            )
        if "EINTR" in code_names:
            scenarios.append(
                Scenario(
                    canonical,
                    "eintr",
                    "EINTR",
                    errno.EINTR,
                    domain,
                    symbol_header,
                )
            )
        if "ETIMEDOUT" in code_names:
            scenarios.append(
                Scenario(
                    canonical,
                    "timeout",
                    "ETIMEDOUT",
                    errno.ETIMEDOUT,
                    domain,
                    symbol_header,
                )
            )
        for mode in ("short", "uncertain"):
            supported = semantics.get(mode, {}).get(
                "supported_wrapper_usrs", []
            )
            if wrapper_usr in supported:
                scenarios.append(
                    Scenario(
                        canonical,
                        mode,
                        "-",
                        0,
                        domain,
                        symbol_header,
                    )
                )
    return scenarios


def resolve_symbolic_codes(scenarios: list[Scenario]) -> dict[str, int]:
    names = sorted(
        {
            scenario.code_name
            for scenario in scenarios
            if scenario.code_value is None
        }
    )
    if not names:
        return {}
    headers = {"stdio.h"}
    for scenario in scenarios:
        if scenario.code_name in names:
            headers.add(scenario.symbol_header)

    source_lines = [
        *(f"#include <{header}>" for header in sorted(headers)),
        "int main(void)",
        "{",
        *(
            fr'    printf("{name}\t%d\n", (int){name});'
            for name in names
        ),
        "    return 0;",
        "}",
    ]
    compiler = os.environ.get("CC", "cc")
    with tempfile.TemporaryDirectory(prefix="p101-fault-codes.") as temporary:
        root = Path(temporary)
        source = root / "codes.c"
        executable = root / "codes"
        source.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
        compiled = subprocess.run(
            [
                compiler,
                "-std=c17",
                "-D_POSIX_C_SOURCE=200809L",
                "-D_XOPEN_SOURCE=700",
                "-D_DEFAULT_SOURCE",
                "-D_DARWIN_C_SOURCE",
                "-D_GNU_SOURCE",
                str(source),
                "-o",
                str(executable),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if compiled.returncode != 0:
            raise ValueError(
                "cannot resolve platform fault codes with "
                f"{compiler}: {compiled.stderr.strip()}"
            )
        resolved = subprocess.run(
            [str(executable)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if resolved.returncode != 0:
            raise ValueError(
                "platform fault-code resolver failed: "
                f"{resolved.stderr.strip()}"
            )

    values: dict[str, int] = {}
    for line in resolved.stdout.splitlines():
        name, separator, value = line.partition("\t")
        if not separator or name not in names:
            raise ValueError("platform fault-code resolver emitted invalid output")
        values[name] = int(value)
    missing = sorted(set(names) - set(values))
    if missing:
        raise ValueError(
            "platform fault-code resolver omitted: " + ", ".join(missing)
        )
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run every documented platform errno and special fault outcome for "
            "selected p101 wrappers."
        )
    )
    parser.add_argument("--wrapper", action="append", default=[])
    parser.add_argument("--library")
    parser.add_argument(
        "--platform", choices=("linux", "macos", "freebsd"), default=_host_platform()
    )
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--max-fault-index", type=int, default=1024)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--amount", type=int, default=1)
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=60.0,
        help="maximum seconds for each executed fault case (default: 60)",
    )
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.dry_run and not args.command:
        parser.error("a command is required after --")
    if not args.wrapper and args.library is None:
        parser.error("select at least one --wrapper or one --library")
    if args.max_cases < 0 or args.max_fault_index < 0:
        parser.error("case and fault-index limits must be non-negative")
    if args.repeat <= 0 or args.amount < 0 or args.case_timeout <= 0:
        parser.error("repeat must be positive and amount must be non-negative")
    return args


def main() -> int:
    args = parse_args()
    host_platform = _host_platform()
    if args.platform != host_platform:
        print(
            "p101 fault campaign: selected platform "
            f"{args.platform} does not match host platform {host_platform}",
            file=sys.stderr,
        )
        return 2
    try:
        scenarios = build_scenarios(args.wrapper, args.platform, args.library)
    except ValueError as error:
        print(f"p101 fault campaign: {error}", file=sys.stderr)
        return 2
    if args.max_cases:
        scenarios = scenarios[: args.max_cases]
    if not scenarios:
        print("p101 fault campaign: selection produced no supported cases", file=sys.stderr)
        return 2
    try:
        symbolic_values = resolve_symbolic_codes(scenarios)
    except ValueError as error:
        print(f"p101 fault campaign: {error}", file=sys.stderr)
        return 2

    try:
        fault_runner = built_tool(
            WORKSPACE / "programs" / "p101-test", "test-faults", "P101_TEST_FAULTS"
        )
        capture_tool = built_tool(
            WORKSPACE / "programs" / "p101-inspect",
            "inspect-capture",
            "P101_INSPECT_CAPTURE",
        )
        model_tool = built_tool(
            WORKSPACE / "libraries" / "lib_tool_event",
            "p101-event-model",
            "P101_EVENT_MODEL",
        )
    except ValueError as error:
        print(f"p101 fault campaign: {error}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    overall = 0
    for index, scenario in enumerate(scenarios, start=1):
        case_dir = args.output / (
            f"{index:04d}-{scenario.wrapper}-{scenario.mode}-{scenario.code_name}"
        )
        code_value = scenario.code_value
        if code_value is None:
            code_value = symbolic_values[scenario.code_name]
        command = [
            str(fault_runner),
            "-U",
            str(SCRIPT_ROOT / "runtime" / "p101-run.py"),
            "-O",
            str(capture_tool),
            "-Y",
            str(SCRIPT_ROOT / "runtime" / "p101-analyze.py"),
            "-B",
            str(model_tool),
            "-n",
            str(args.max_fault_index),
            "-F",
            scenario.wrapper,
            "-M",
            scenario.mode,
            "-A",
            str(args.amount),
            "-R",
            str(args.repeat),
            "-l",
            str(case_dir),
        ]
        if code_value:
            command.extend(("-E", str(code_value)))
        command.extend(("--", *args.command))
        print(
            f"==> {index}/{len(scenarios)} {scenario.wrapper} "
            f"{scenario.mode} {scenario.code_name}"
        )
        if args.dry_run:
            status: int | None = None
            raw_status: int | None = None
            signal_number: int | None = None
            timed_out = False
            print(" ".join(command))
        else:
            try:
                raw_status = subprocess.run(
                    command,
                    check=False,
                    timeout=args.case_timeout,
                ).returncode
                signal_number = -raw_status if raw_status < 0 else None
                status = raw_status if raw_status in {0, 1} else 2
                timed_out = False
            except subprocess.TimeoutExpired:
                raw_status = None
                signal_number = None
                status = 2
                timed_out = True
        results.append(
            {
                "wrapper": scenario.wrapper,
                "mode": scenario.mode,
                "code": scenario.code_name,
                "code_value": code_value,
                "domain": scenario.domain,
                "status": status,
                "raw_status": raw_status,
                "signal": signal_number,
                "timed_out": timed_out,
                "output": str(case_dir),
            }
        )
        if status == 2:
            overall = 2
        elif status == 1 and overall == 0:
            overall = 1

    receipt = {
        "schema": "p101-fault-campaign-v1",
        "platform": args.platform,
        "dry_run": args.dry_run,
        "cases": results,
        "summary": {
            "total": len(results),
            "clean": sum(item["status"] == 0 for item in results),
            "findings": sum(item["status"] == 1 for item in results),
            "trouble": sum(item["status"] == 2 for item in results),
        },
    }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "p101 fault campaign: "
        f"{receipt['summary']['total']} cases, "
        f"{receipt['summary']['findings']} with findings, "
        f"{receipt['summary']['trouble']} trouble"
    )
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
