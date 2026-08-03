#!/usr/bin/env python3
"""Run contract-derived p101 fault campaigns for selected wrappers."""

from __future__ import annotations

import argparse
import errno
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wrapper_fault_contract import fault_domain, injected_fault_cases


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
FAILURE_CONTRACT = SCRIPT_ROOT / "contracts" / "wrapper-failure-contract.json"
PLATFORM_CONTRACT = SCRIPT_ROOT / "contracts" / "wrapper-platform-faults.json"
SEMANTICS_CONTRACT = SCRIPT_ROOT / "contracts" / "wrapper-fault-semantics.json"


@dataclass(frozen=True)
class Scenario:
    wrapper: str
    mode: str
    code_name: str
    code_value: int | None
    domain: str


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
    failures = _load(FAILURE_CONTRACT).get("wrappers", {})
    platform_contract = _load(PLATFORM_CONTRACT)
    wrapper_mappings = platform_contract.get("wrappers", {})
    semantics = _load(SEMANTICS_CONTRACT).get("modes", {})
    if not wrappers:
        wrappers = sorted(
            name
            for name, record in failures.items()
            if library is None or record.get("library") == library
        )
    scenarios: list[Scenario] = []
    for wrapper in wrappers:
        canonical = wrapper if wrapper.startswith("p101_") else f"p101_{wrapper}"
        failure = failures.get(canonical)
        if not isinstance(failure, dict):
            raise ValueError(f"wrapper is absent from the failure contract: {wrapper}")
        if library is not None and failure.get("library") != library:
            raise ValueError(f"{canonical} is not owned by {library}")
        wrapper_mapping = wrapper_mappings.get(canonical)
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
        for code_name in code_names:
            code_value = getattr(errno, code_name, None)
            scenarios.append(
                Scenario(
                    canonical,
                    "error",
                    code_name,
                    code_value if isinstance(code_value, int) else None,
                    domain,
                )
            )
        if "EINTR" in code_names:
            scenarios.append(
                Scenario(canonical, "eintr", "EINTR", errno.EINTR, domain)
            )
        if "ETIMEDOUT" in code_names:
            scenarios.append(
                Scenario(
                    canonical,
                    "timeout",
                    "ETIMEDOUT",
                    errno.ETIMEDOUT,
                    domain,
                )
            )
        for mode in ("short", "uncertain"):
            supported = semantics.get(mode, {}).get("supported_wrappers", [])
            if canonical in supported:
                scenarios.append(Scenario(canonical, mode, "-", 0, domain))
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
    for name in names:
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None:
            raise ValueError(f"invalid symbolic fault code in contract: {name}")

    headers = {"errno.h", "stdio.h"}
    for name in names:
        if name.startswith("EAI_"):
            headers.add("netdb.h")
        elif name.startswith("REG_"):
            headers.add("regex.h")
        elif name.startswith("GLOB_"):
            headers.add("glob.h")
        elif name.startswith("WRDE_"):
            headers.add("wordexp.h")
        elif name.startswith("MM_"):
            headers.add("fmtmsg.h")

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
    if args.repeat <= 0 or args.amount < 0:
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

    args.output.mkdir(parents=True, exist_ok=True)
    p101 = SCRIPT_ROOT / "p101"
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
            str(p101),
            "walk",
            "-n",
            str(args.max_fault_index),
            "-F",
            scenario.wrapper.removeprefix("p101_"),
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
            status = 0
            print(" ".join(command))
        else:
            status = subprocess.run(command, check=False).returncode
        results.append(
            {
                "wrapper": scenario.wrapper,
                "mode": scenario.mode,
                "code": scenario.code_name,
                "code_value": code_value,
                "domain": scenario.domain,
                "status": status,
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
