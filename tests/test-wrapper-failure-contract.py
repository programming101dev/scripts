#!/usr/bin/env python3
"""Regression tests for generated wrapper failure-semantics mechanics."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-failure-contract.json"


def load_generator():
    path = SCRIPTS_ROOT / "generators" / "generate-wrapper-unit-tests.py"
    spec = importlib.util.spec_from_file_location(
        "generate_wrapper_unit_tests",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_macro_reader(generator) -> None:
    text = (
        'P101_WRAPPER_FAULT_RETURN(env, err, '
        '((datum){.dptr = NULL, .dsize = 0}));'
    )
    invocation = generator.macro_invocation(text, 0)
    check(invocation is not None, "nested macro invocation was not read")
    name, arguments = invocation
    check(name == "P101_WRAPPER_FAULT_RETURN", "macro name drifted")
    check(len(arguments) == 3, "nested commas split macro arguments")
    check(
        arguments[-1] == "((datum){.dptr = NULL, .dsize = 0})",
        "failure expression drifted",
    )


def test_function_pointer_result(generator) -> None:
    declaration = {
        "type": {
            "qualType": (
                "void (*(const struct p101_env *, struct p101_error *, "
                "int, void (*)(int)))(int)"
            )
        }
    }
    check(
        generator.result_declaration(declaration, "result")
        == "void (*result)(int)",
        "function-pointer return declaration drifted",
    )


def test_checked_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    check(
        contract.get("schema") == "p101-wrapper-failure-contract-v1",
        "failure contract schema drifted",
    )
    wrappers = contract.get("wrappers", {})
    check(bool(wrappers), "failure contract is empty")
    required = {
        "library",
        "error_domain",
        "return_kind",
        "return_expression",
        "errno",
        "fault_boundary",
        "fault_modes",
        "runtime_canary_arguments",
        "writable_arguments",
        "resource_events",
    }
    for name, record in wrappers.items():
        check(
            set(record) == required,
            f"{name}: failure contract fields drifted",
        )
        check(
            record["return_kind"] in {"value", "error-code", "void"},
            f"{name}: invalid return kind",
        )
        check(
            record["error_domain"] in {"errno", "system"},
            f"{name}: invalid error domain",
        )
        check(record["errno"] == "preserved", f"{name}: errno policy drifted")
        check(
            record["fault_boundary"] == "before-observable-work",
            f"{name}: fault boundary drifted",
        )
        check(
            record["fault_modes"] in (["error"], ["error", "short"]),
            f"{name}: fault modes drifted",
        )
        check(
            record["resource_events"] == "none",
            f"{name}: resource policy drifted",
        )


def main() -> int:
    generator = load_generator()
    tests = (
        lambda: test_macro_reader(generator),
        lambda: test_function_pointer_result(generator),
        test_checked_contract,
    )
    for test in tests:
        test()
    print(f"wrapper failure contract tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
