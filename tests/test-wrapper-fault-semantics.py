#!/usr/bin/env python3
"""Negative controls for the wrapper fault-semantics contract."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = SCRIPTS_ROOT / "checks" / "check-wrapper-fault-semantics.py"


def module():
    specification = importlib.util.spec_from_file_location(
        "wrapper_fault_semantics", CHECK_PATH
    )
    assert specification is not None and specification.loader is not None
    loaded = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(loaded)
    return loaded


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    checker = module()
    contract = json.loads(checker.CONTRACT_PATH.read_text(encoding="utf-8"))
    failure = json.loads(checker.FAILURE_PATH.read_text(encoding="utf-8"))
    lifecycle = json.loads(checker.LIFECYCLE_PATH.read_text(encoding="utf-8"))
    checked_failures = checker.validate(contract, failure, lifecycle)
    checked_diagnostics = "; ".join(checked_failures)
    check(
        checked_failures == [],
        f"checked contract should pass: {checked_diagnostics}",
    )

    bad_phase = copy.deepcopy(contract)
    bad_phase["modes"]["uncertain"]["phase"] = "before-call"
    check(
        bool(checker.validate(bad_phase, failure, lifecycle)),
        "phase drift should fail",
    )

    bad_retry = copy.deepcopy(contract)
    bad_retry["modes"]["uncertain"]["retry_rule"] = "retry-always"
    check(
        bool(checker.validate(bad_retry, failure, lifecycle)),
        "unsafe retry policy should fail",
    )

    missing_wrapper = copy.deepcopy(contract)
    missing_wrapper["modes"]["short"]["supported_wrapper_usrs"].pop()
    check(
        bool(checker.validate(missing_wrapper, failure, lifecycle)),
        "wrapper inventory drift should fail",
    )

    print("wrapper fault semantic negative controls: 4 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
