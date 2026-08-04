#!/usr/bin/env python3
"""Regression tests for generated wrapper failure-semantics mechanics."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-failure-contract.json"
OUTCOME_CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-outcome-contract.json"


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


def test_versioned_libclang_include_discovery(generator) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        versioned = root / "lib" / "llvm-18" / "include"
        local = root / "llvm19" / "include"
        versioned.mkdir(parents=True)
        local.mkdir(parents=True)
        found = set(generator.versioned_libclang_include_dirs(root))
        check(versioned in found, "versioned Linux libclang include was missed")
        check(local in found, "prefix-local libclang include was missed")


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


def test_portable_zero_typedefs(generator) -> None:
    for qualified, desugared in (
        ("iconv_t", "void *"),
        ("nl_catd", "int"),
        ("pthread_t", "unsigned long"),
        ("pthread_t", "struct _opaque_pthread_t *"),
    ):
        parameter = {
            "type": {
                "qualType": qualified,
                "desugaredQualType": desugared,
            }
        }
        check(
            generator.argument_expression(parameter)
            == f"({qualified}){{0}}",
            f"{qualified}: zero expression depends on underlying type",
        )


def test_bool_result_spelling(generator) -> None:
    declaration = {"type": {"qualType": "_Bool (void)"}}
    check(
        generator.result_declaration(declaration, "result")
        == "bool result",
        "_Bool result spelling was not normalized",
    )


def test_single_exit_fault_result(generator) -> None:
    fragment = """
        fault = p101_env_check_fault(env, "open");
        if(fault != 0)
        {
            P101_ERROR_RAISE_ERRNO(err, fault);
            p101_single_result_ = -1;
            goto p101_single_exit_;
        }
        p101_single_result_ = ret_val;
    """
    check(
        generator.explicit_fault_result(fragment) == "-1",
        "manual single-exit fault result was not recovered",
    )
    check(
        generator.explicit_fault_result(
            "p101_single_result_ = 0; goto p101_single_exit_;"
        )
        is None,
        "non-fault single-exit result was misclassified",
    )


def test_va_list_fixture_is_started(generator) -> None:
    declaration = {
        "inner": [
            {
                "kind": "ParmVarDecl",
                "name": "env",
                "type": {"qualType": "const struct p101_env *"},
            },
            {
                "kind": "ParmVarDecl",
                "name": "err",
                "type": {"qualType": "struct p101_error *"},
            },
            {
                "kind": "ParmVarDecl",
                "name": "arguments",
                "type": {"qualType": "va_list"},
            },
        ]
    }
    check(
        generator.has_va_list_parameter(declaration),
        "va_list parameter was not detected",
    )
    check(
        generator.fault_test_signature_suffix(declaration) == ", ...",
        "generated va_list test is not variadic",
    )
    check(
        generator.fault_test_call_suffix(declaration) == ", 0",
        "generated va_list test lacks its variadic sentinel",
    )
    setup = generator.va_list_setup(declaration)
    check(
        "va_start(arguments, err);" in setup,
        "generated va_list fixture is not started",
    )
    check(
        "memset" not in setup,
        "generated va_list fixture is still zero-initialized",
    )
    check(
        "va_end(arguments);" in generator.va_list_teardown(declaration),
        "generated va_list fixture is not ended",
    )


def test_portable_result_assertions(generator) -> None:
    datum_declaration = {
        "name": "p101_dbm_fetch",
        "type": {"qualType": "datum (void)"},
    }
    datum_assertion = generator.result_assertion(
        datum_declaration,
        {
            "kind": "value",
            "expression": "((datum){.dptr = NULL, .dsize = 0})",
        },
    )
    check(
        "result.dptr == expected_result.dptr" in datum_assertion
        and "result.dsize == expected_result.dsize" in datum_assertion,
        "aggregate results are not compared field by field",
    )
    check(
        "memcmp" not in datum_assertion,
        "aggregate result assertion compares indeterminate padding",
    )

    address_declaration = {
        "name": "p101_inet_addr",
        "type": {"qualType": "in_addr_t (void)"},
    }
    address_assertion = generator.result_assertion(
        address_declaration,
        {
            "kind": "value",
            "expression": "(in_addr_t)P101_INET_ADDR_NONE_VALUE",
        },
    )
    check(
        "INADDR_NONE" not in address_assertion
        and "(in_addr_t)-1" in address_assertion,
        "network failure assertion still depends on optional INADDR_NONE",
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


def test_outcome_contract() -> None:
    contract = json.loads(
        OUTCOME_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    check(
        contract.get("schema") == "p101-wrapper-outcome-contract-v1",
        "outcome contract schema drifted",
    )
    valid = {
        "direct-hard-failure",
        "short-partial-result",
        "delegated-failure",
        "deterministic-rejection",
        "genuinely-infallible",
        "non-returning-cleanup",
    }
    apis = contract.get("apis", {})
    check(bool(apis), "outcome contract is empty")
    required = {
        "library",
        "role",
        "accepts_error",
        "classification",
        "rationale",
        "source",
    }
    for name, record in apis.items():
        check(
            set(record) == required,
            f"{name}: outcome contract fields drifted",
        )
        classification = record.get("classification")
        check(
            classification in valid,
            f"{name}: invalid outcome classification",
        )
        check(bool(record.get("rationale")), f"{name}: missing rationale")
        if record.get("accepts_error"):
            check(
                classification
                in {"direct-hard-failure", "short-partial-result"},
                f"{name}: error-taking API is not directly injectable",
            )


def main() -> int:
    generator = load_generator()
    tests = (
        lambda: test_macro_reader(generator),
        lambda: test_versioned_libclang_include_discovery(generator),
        lambda: test_function_pointer_result(generator),
        lambda: test_portable_zero_typedefs(generator),
        lambda: test_bool_result_spelling(generator),
        lambda: test_single_exit_fault_result(generator),
        lambda: test_va_list_fixture_is_started(generator),
        lambda: test_portable_result_assertions(generator),
        test_checked_contract,
        test_outcome_contract,
    )
    for test in tests:
        test()
    print(f"wrapper failure contract tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
