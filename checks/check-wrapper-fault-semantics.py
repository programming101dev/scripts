#!/usr/bin/env python3
"""Check the small, executable contract for wrapper fault outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-fault-semantics.json"
FAILURE_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-failure-contract.json"
LIFECYCLE_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-lifecycle-contract.json"
ENV_HEADER = WORKSPACE / "libraries" / "lib_env" / "include" / "p101_env" / "env.h"
ENV_SOURCE = WORKSPACE / "libraries" / "lib_env" / "src" / "env.c"
IO_SOURCE = WORKSPACE / "libraries" / "lib_io" / "src" / "posix" / "unistd.c"
IO_BEHAVIOR = WORKSPACE / "libraries" / "lib_io" / "test" / "test_behavior.c"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(
    contract: dict[str, Any],
    failure: dict[str, Any],
    lifecycle: dict[str, Any],
    env_header: str,
    env_source: str,
    io_source: str,
    io_behavior: str,
) -> list[str]:
    failures: list[str] = []
    expected_modes = {"error", "eintr", "timeout", "short", "uncertain"}
    modes = contract.get("modes", {})
    if contract.get("schema") != "p101-wrapper-fault-semantics-v1":
        failures.append("unsupported fault-semantics schema")
    if set(modes) != expected_modes:
        failures.append("fault mode inventory drifted")

    expected = {
        "error": ("P101_ENV_FAULT_ERROR", "before-call", "retry-safe"),
        "eintr": ("P101_ENV_FAULT_ERROR", "before-call", "retry-safe"),
        "timeout": ("P101_ENV_FAULT_ERROR", "before-call", "retry-safe"),
        "short": (
            "P101_ENV_FAULT_SHORT",
            "after-partial-progress",
            "progress-known",
        ),
        "uncertain": (
            "P101_ENV_FAULT_UNCERTAIN",
            "after-dispatch",
            "outcome-uncertain",
        ),
    }
    required_fields = {
        "kind",
        "phase",
        "disposition",
        "operation_effect",
        "retry_rule",
        "supported_wrappers",
    }
    for name, values in expected.items():
        record = modes.get(name, {})
        if set(record) != required_fields:
            failures.append(f"{name}: semantic fields drifted")
            continue
        if (record["kind"], record["phase"], record["disposition"]) != values:
            failures.append(f"{name}: phase or disposition drifted")
        if record["kind"] not in env_header:
            failures.append(f"{name}: fault kind is absent from lib_env")
        if f'"{name}"' not in env_source:
            failures.append(f"{name}: environment mode is not implemented")

    io_wrappers = ["p101_pread", "p101_pwrite", "p101_read", "p101_write"]
    for name in ("short", "uncertain"):
        if modes.get(name, {}).get("supported_wrappers") != io_wrappers:
            failures.append(f"{name}: supported wrapper inventory drifted")
    short_wrappers = sorted(
        name
        for name, record in failure.get("wrappers", {}).items()
        if "short" in record.get("fault_modes", [])
    )
    if short_wrappers != io_wrappers:
        failures.append("generated short-I/O wrapper inventory drifted")
    lifecycle_wrappers = sorted(
        record["fault_name"]
        for record in lifecycle.get("scenarios", {}).values()
        if "short" in record.get("fault_modes", [])
    )
    if lifecycle_wrappers != io_wrappers:
        failures.append("short-I/O lifecycle evidence drifted")

    for symbol in (
        "P101_ENV_FAULT_SHORT",
        "P101_ENV_FAULT_UNCERTAIN",
        "hide_success",
        "p101_env_record_fault_action",
    ):
        if symbol not in io_source:
            failures.append(f"lib_io is missing {symbol}")
    for evidence in (
        "test_uncertain_write_hides_completed_operation",
        "p101_error_is_errno(err, ETIMEDOUT)",
        "memcmp(received, payload",
    ):
        if evidence not in io_behavior:
            failures.append(f"uncertain-outcome native evidence is missing {evidence}")

    if modes.get("uncertain", {}).get("retry_rule") != "automatic-retry-forbidden":
        failures.append("uncertain outcomes must not authorize automatic retry")
    if failure.get("semantics", {}).get("fault_boundary") != "before-observable-work":
        failures.append("generated early-failure boundary drifted")
    return failures


def main() -> int:
    failures = validate(
        load(CONTRACT_PATH),
        load(FAILURE_PATH),
        load(LIFECYCLE_PATH),
        ENV_HEADER.read_text(encoding="utf-8"),
        ENV_SOURCE.read_text(encoding="utf-8"),
        IO_SOURCE.read_text(encoding="utf-8"),
        IO_BEHAVIOR.read_text(encoding="utf-8"),
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("wrapper fault semantics: 5 modes, 4 after-dispatch/partial-progress wrappers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
