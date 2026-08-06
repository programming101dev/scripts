#!/usr/bin/env python3
"""Focused tests for the contract-derived fault campaign."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
PATH = ROOT / "runtime" / "p101-fault-campaign.py"
SPEC = importlib.util.spec_from_file_location("p101_fault_campaign", PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load {PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    scenarios = MODULE.build_scenarios(["p101_read"], "linux", None)
    keys = {(item.mode, item.code_name) for item in scenarios}
    check(("error", "EBADF") in keys, "read must cover EBADF")
    check(("eintr", "EINTR") in keys, "read must cover EINTR")
    check(("short", "-") in keys, "read must cover short results")
    check(("uncertain", "-") in keys, "read must cover uncertain results")
    check(len(keys) == len(scenarios), "fault scenarios must be unique")

    selected = MODULE.build_scenarios(["p101_open"], "macos", "lib_io")
    check(bool(selected), "open scenarios must exist")
    check(all(item.wrapper == "p101_open" for item in selected), "library filter drift")
    check(all(item.mode in {"error", "eintr"} for item in selected), "open modes drift")

    renamed = MODULE.build_scenarios(
        ["p101_semctl_arg"], "freebsd", "lib_ipc"
    )
    check(bool(renamed), "semctl scenarios must exist")
    check({item.code_name for item in renamed} >= {
        "EACCES",
        "EINVAL",
        "EPERM",
        "ERANGE",
    }, "semctl error coverage drift")

    no_native_failures = MODULE.build_scenarios(
        ["p101_abs"], "linux", "lib_c"
    )
    check(("error", "EIO") in {
        (item.mode, item.code_name) for item in no_native_failures
    }, "infallible native operation must retain injected failure coverage")

    system_failures = MODULE.build_scenarios(
        ["p101_regcomp"], "macos", "lib_text"
    )
    check(any(
        item.domain == "system" and item.code_name == "REG_BADPAT"
        for item in system_failures
    ), "system-domain failures must be retained")

    try:
        MODULE.build_scenarios(["not_a_wrapper"], "linux", None)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown wrappers must be rejected")
    print("p101 fault campaign tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
