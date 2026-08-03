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
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def main() -> int:
    scenarios = MODULE.build_scenarios(["p101_read"], "linux", None)
    keys = {(item.mode, item.code_name) for item in scenarios}
    assert ("error", "EBADF") in keys
    assert ("eintr", "EINTR") in keys
    assert ("short", "-") in keys
    assert ("uncertain", "-") in keys
    assert len(keys) == len(scenarios)

    selected = MODULE.build_scenarios(["open"], "macos", "lib_io")
    assert selected
    assert all(item.wrapper == "p101_open" for item in selected)
    assert all(item.mode in {"error", "eintr"} for item in selected)

    renamed = MODULE.build_scenarios(
        ["p101_semctl_arg"], "freebsd", "lib_ipc"
    )
    assert renamed
    assert {item.code_name for item in renamed} >= {
        "EACCES",
        "EINVAL",
        "EPERM",
        "ERANGE",
    }

    no_native_failures = MODULE.build_scenarios(
        ["p101_abs"], "linux", "lib_c"
    )
    assert ("error", "EIO") in {
        (item.mode, item.code_name) for item in no_native_failures
    }

    system_failures = MODULE.build_scenarios(
        ["p101_regcomp"], "macos", "lib_text"
    )
    assert any(
        item.domain == "system" and item.code_name == "REG_BADPAT"
        for item in system_failures
    )

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
