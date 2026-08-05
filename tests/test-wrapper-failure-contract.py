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
    source = "void example(void *env, void *err, va_list renamed_arguments) {}"
    parameter_start = source.index("va_list")
    parameter_name = "renamed_arguments"
    declaration = {
        "_p101_source_text": source,
        "name": "p101_example_v",
        "type": {
            "qualType": (
                "int (const struct p101_env *, struct p101_error *, "
                "struct __va_list_tag *)"
            )
        },
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
                "name": parameter_name,
                "type": {"qualType": "struct __va_list_tag *"},
                "range": {
                    "begin": {"offset": parameter_start},
                    "end": {
                        "offset": source.index(parameter_name),
                        "tokLen": len(parameter_name),
                    },
                },
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
    generated = generator.fault_test(
        "p101_example_v",
        declaration,
        {
            "linux": ["EIO"],
            "macos": ["EIO"],
            "freebsd": ["EIO"],
            "posix": ["EIO"],
        },
        {
            "kind": "value",
            "expression": "-1",
            "error_domain": "errno",
        },
    )
    check(
        "p101_example_v(env, err, arguments)" in generated
        and "p101_example_v(native_env, native_err, arguments)" in generated,
        "generated va_list calls do not use the started public-type fixture",
    )
    check(
        "__va_list_tag" not in generated,
        "generated va_list fixture leaked a platform-private AST type",
    )


def test_public_aggregate_fixture_ignores_private_ast_alias(generator) -> None:
    source = "int example(struct shmid_ds *renamed_buffer) {}"
    parameter_start = source.index("struct shmid_ds")
    parameter_name = "renamed_buffer"
    declaration = {"_p101_source_text": source}
    parameter = {
        "kind": "ParmVarDecl",
        "name": parameter_name,
        "type": {"qualType": "struct __shmid_ds_new *"},
        "range": {
            "begin": {"offset": parameter_start},
            "end": {
                "offset": source.index(parameter_name),
                "tokLen": len(parameter_name),
            },
        },
    }
    fixture = generator.native_pointer_fixture(
        "p101_shmctl",
        parameter,
        4,
        declaration,
    )
    check(fixture is not None, "aggregate pointer fixture was not built")
    check(
        fixture[0] == ["            struct shmid_ds native_argument_4 = {0};"],
        "generated fixture leaked a platform-private aggregate tag",
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


def test_fallible_wrapper_has_isolated_native_smoke(generator) -> None:
    declaration = {
        "name": "p101_example",
        "type": {"qualType": "int (const struct p101_env *, struct p101_error *)"},
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
        ],
    }
    source = generator.fault_test(
        "p101_example",
        declaration,
        {
            "linux": ["EIO"],
            "macos": ["EIO"],
            "freebsd": ["EIO"],
            "posix": ["EIO"],
        },
        {
            "kind": "value",
            "expression": "-1",
            "error_domain": "errno",
        },
    )
    for marker in (
        "pid_t native_pid    = fork();",
        "(void)alarm(2U);",
        "p101_env_set_fault_injector(env, NULL, NULL);",
        "p101_example(env, err);",
        "waitpid(native_pid, &native_status, 0)",
        "WIFEXITED(native_status)",
    ):
        check(marker in source, f"native smoke omits {marker}")


def test_fixture_roles_do_not_depend_on_parameter_names(generator) -> None:
    env_parameter = {
        "name": "renamed_context",
        "type": {"qualType": "const struct p101_env *"},
    }
    error_parameter = {
        "name": "renamed_failure",
        "type": {"qualType": "struct p101_error *"},
    }
    text_parameter = {
        "name": "renamed_text",
        "type": {"qualType": "const char *"},
    }
    check(
        generator.argument_expression(env_parameter) == "env",
        "environment fixture depends on the parameter name",
    )
    check(
        generator.argument_expression(error_parameter) == "err",
        "error fixture depends on the parameter name",
    )
    check(
        generator.argument_expression(text_parameter) == "NULL",
        "text fault fixture depends on a format/path variable name",
    )
    declaration = {
        "name": "p101_parse_renamed",
        "inner": [
            {"kind": "ParmVarDecl", **env_parameter},
            {"kind": "ParmVarDecl", **error_parameter},
            {"kind": "ParmVarDecl", **text_parameter},
            {
                "kind": "ParmVarDecl",
                "name": "renamed_fallback",
                "type": {"qualType": "int"},
            },
        ],
    }
    check(
        generator.indexed_fallback_expression(
            declaration,
            "P101_FAULT_RETURN_PARSED_ARG3",
            ["renamed_context", "int", "renamed_fallback", "expression"],
        )
        == "0",
        "indexed fallback fixture depends on the parameter name",
    )


def test_native_fixtures_use_types_and_api_positions(generator) -> None:
    callback = {
        "name": "renamed_callback",
        "type": {"qualType": "int (*)(const char *, int)"},
    }
    callback_fixture = generator.native_pointer_fixture(
        "p101_glob",
        callback,
        4,
    )
    check(callback_fixture is not None, "callback fixture was not recognized")
    check(
        callback_fixture[1] == "native_path_error_callback",
        "callback fixture depends on its parameter name",
    )

    vector = {
        "name": "renamed_vector",
        "type": {"qualType": "char *const *restrict"},
    }
    vector_fixture = generator.native_pointer_fixture(
        "p101_execv",
        vector,
        3,
    )
    check(vector_fixture is not None, "argument vector fixture was not built")
    check(
        "char *native_argument_3[2]" in vector_fixture[0][0]
        and vector_fixture[1] == "native_argument_3",
        "argument vector does not preserve its resolved C type",
    )

    pipe_parameter = {
        "name": "renamed_output",
        "type": {"qualType": "int *"},
    }
    pipe_fixture = generator.native_contract_fixture(
        "p101_pipe",
        pipe_parameter,
        2,
    )
    check(pipe_fixture is not None, "pipe output contract was not applied")
    check(
        "int native_argument_2[2]" in pipe_fixture[0][0],
        "array extent contract depends on a parameter name",
    )

    alignment = {
        "name": "renamed_scalar",
        "type": {"qualType": "size_t"},
    }
    alignment_fixture = generator.native_contract_fixture(
        "p101_posix_memalign",
        alignment,
        3,
    )
    check(
        alignment_fixture is not None
        and alignment_fixture[1] == "sizeof(void *)",
        "POSIX alignment contract was not applied by API position",
    )

    spawn_actions = {
        "name": "renamed_actions",
        "type": {
            "qualType": "const posix_spawn_file_actions_t *",
        },
    }
    spawn_null_fixture = generator.native_contract_fixture(
        "p101_posix_spawnp",
        spawn_actions,
        4,
    )
    check(
        spawn_null_fixture is not None
        and spawn_null_fixture[1] == "NULL",
        "posix_spawnp does not use the public NULL actions contract",
    )

    initialized_actions = generator.native_contract_fixture(
        "p101_posix_spawn_file_actions_addclose",
        {
            "name": "renamed_actions",
            "type": {"qualType": "posix_spawn_file_actions_t *"},
        },
        2,
    )
    check(
        initialized_actions is not None
        and any(
            "posix_spawn_file_actions_init" in line
            for line in initialized_actions[2]
        )
        and any(
            "posix_spawn_file_actions_destroy" in line
            for line in initialized_actions[3]
        ),
        "file-actions native fixture does not obey its lifecycle",
    )

    spawn_environment = generator.native_contract_fixture(
        "p101_posix_spawnp",
        {
            "name": "renamed_vector",
            "type": {"qualType": "char *const *restrict"},
        },
        7,
    )
    check(
        spawn_environment is not None
        and "PATH=/usr/bin:/bin" in spawn_environment[0][0],
        "posix_spawnp environment fixture is not a valid environment vector",
    )

    semaphore = generator.native_contract_fixture(
        "p101_sem_wait",
        {
            "name": "renamed_semaphore",
            "type": {"qualType": "sem_t *"},
        },
        2,
    )
    check(
        semaphore is not None
        and any(
            "O_CREAT | O_EXCL" in line and "1U" in line
            for line in semaphore[0]
        )
        and any("sem_close" in line for line in semaphore[3])
        and any("sem_unlink" in line for line in semaphore[3]),
        "semaphore wait fixture is not live and nonblocking",
    )

    signal_set = generator.native_contract_fixture(
        "p101_sigwait",
        {
            "name": "renamed_signal_set",
            "type": {"qualType": "const sigset_t *restrict"},
        },
        2,
    )
    check(
        signal_set is not None
        and any("sigaddset" in line and "SIGUSR1" in line for line in signal_set[0])
        and any("sigprocmask(SIG_BLOCK" in line for line in signal_set[0])
        and any("raise(SIGUSR1)" in line for line in signal_set[0])
        and any("sigprocmask(SIG_SETMASK" in line for line in signal_set[3]),
        "sigwait fixture does not arrange a pending blocked signal",
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
        for canary in record["runtime_canary_arguments"]:
            check(
                set(canary) == {"index", "type"},
                f"{name}: canary identity must use index and resolved type",
            )
            check(
                isinstance(canary["index"], int)
                and isinstance(canary["type"], str)
                and bool(canary["type"]),
                f"{name}: invalid type-based canary identity",
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
        lambda: test_public_aggregate_fixture_ignores_private_ast_alias(
            generator
        ),
        lambda: test_portable_result_assertions(generator),
        lambda: test_fallible_wrapper_has_isolated_native_smoke(generator),
        lambda: test_fixture_roles_do_not_depend_on_parameter_names(generator),
        lambda: test_native_fixtures_use_types_and_api_positions(generator),
        test_checked_contract,
        test_outcome_contract,
    )
    for test in tests:
        test()
    print(f"wrapper failure contract tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
