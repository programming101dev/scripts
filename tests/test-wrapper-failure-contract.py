#!/usr/bin/env python3
"""Regression tests for generated wrapper failure-semantics mechanics."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-failure-contract.json"
OUTCOME_CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-outcome-contract.json"
PORTABLE_INPUT_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-portable-input-contract.json"
)


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


def test_function_selection_uses_semantic_extent(generator) -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "misleading_spelling",
                "range": {
                    "begin": {"offset": 10},
                    "end": {"offset": 19, "tokLen": 1},
                },
                "inner": [{"kind": "CompoundStmt"}],
            }
        ],
    }
    declarations = generator.function_declarations(
        ast,
        {(10, 20): ("display_only", "c:@F@semantic_identity")},
    )
    check(
        set(declarations) == {"display_only"}
        and declarations["display_only"]["_p101_function_usr"]
        == "c:@F@semantic_identity",
        "AST definition selection depends on the declaration spelling",
    )


def test_protocol_declaration_bit_selects_definitions(generator) -> None:
    source = generator.WORKSPACE / "libraries" / "lib_c" / "src" / "complex.c"
    facts = [
        {
            "kind": "FUNCTION",
            "path": str(source),
            "usr": "c:@F@semantic_identity",
            "is_declaration": False,
            "start": 10,
            "end": 20,
        },
        {
            "kind": "FUNCTION",
            "path": str(source),
            "usr": "c:@F@semantic_identity",
            "is_declaration": True,
            "start": 30,
            "end": 40,
        },
    ]
    extents = generator.semantic_definition_extents(
        source,
        {"c:@F@semantic_identity": "display_only"},
        facts,
    )
    check(
        extents
        == {(10, 20): ("display_only", "c:@F@semantic_identity")},
        "v6 declaration semantics do not distinguish definitions",
    )


def test_single_exit_fault_result(generator) -> None:
    source_text = (
        "int renamed(void) { int arbitrary; if(other()) "
        "{ arbitrary = -1; } arbitrary = 0; return arbitrary; }\n"
    )

    def extent(fragment: str, start: int = 0) -> dict[str, dict[str, int]]:
        offset = source_text.index(fragment, start)
        return {
            "begin": {"offset": offset},
            "end": {"offset": offset + len(fragment) - 1, "tokLen": 1},
        }

    selector_range = extent("other()")
    branch_start = source_text.index("if(other())")
    branch_end = source_text.index("}", branch_start) + 1
    result_id = "0xsemantic-result"
    declaration = {
        "kind": "FunctionDecl",
        "inner": [
            {
                "kind": "IfStmt",
                "range": {
                    "begin": {"offset": branch_start},
                    "end": {"offset": branch_end - 1, "tokLen": 1},
                },
                "inner": [
                    {
                        "kind": "BinaryOperator",
                        "opcode": "=",
                        "inner": [
                            {
                                "kind": "DeclRefExpr",
                                "referencedDecl": {"id": result_id},
                            },
                            {
                                "kind": "IntegerLiteral",
                                "range": extent("-1"),
                            },
                        ],
                    }
                ],
            },
            {
                "kind": "ReturnStmt",
                "range": extent("return arbitrary;"),
                "inner": [
                    {
                        "kind": "DeclRefExpr",
                        "referencedDecl": {"id": result_id},
                    }
                ],
            },
        ],
    }
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "semantic.c"
        source.write_text(source_text, encoding="utf-8")
        calls = [
            {
                "caller_usr": "c:@F@renamed",
                "usr": "c:@F@selector",
                "start": selector_range["begin"]["offset"],
                "end": (
                    selector_range["end"]["offset"]
                    + selector_range["end"]["tokLen"]
                ),
            }
        ]
        check(
            generator.semantic_single_exit_fault_result(
                declaration,
                source,
                "c:@F@renamed",
                calls,
                {"c:@F@selector"},
            )
            == "-1",
            "manual single-exit result was not recovered by AST identity",
        )
        check(
            generator.semantic_single_exit_fault_result(
                declaration,
                source,
                "c:@F@renamed",
                calls,
                {"c:@F@different-selector"},
            )
            is None,
            "non-selector branch was misclassified as injected failure",
        )


def test_single_exit_fault_result_follows_selector_value(generator) -> None:
    source_text = (
        "int renamed(void) { int result; int selected; "
        "selected = choose(); if(selected) { result = -1; } "
        "result = 0; return result; }\n"
    )

    def extent(fragment: str) -> dict[str, dict[str, int]]:
        offset = source_text.index(fragment)
        return {
            "begin": {"offset": offset},
            "end": {"offset": offset + len(fragment) - 1, "tokLen": 1},
        }

    result_id = "0xsemantic-result"
    selector_id = "0xsemantic-selector-result"
    selector_range = extent("choose()")
    selector_assignment = extent("selected = choose()")
    branch_range = extent("if(selected) { result = -1; }")
    declaration = {
        "kind": "FunctionDecl",
        "inner": [
            {
                "kind": "BinaryOperator",
                "opcode": "=",
                "range": selector_assignment,
                "inner": [
                    {
                        "kind": "DeclRefExpr",
                        "referencedDecl": {"id": selector_id},
                    },
                    {"kind": "CallExpr", "range": selector_range},
                ],
            },
            {
                "kind": "IfStmt",
                "range": branch_range,
                "inner": [
                    {
                        "kind": "DeclRefExpr",
                        "referencedDecl": {"id": selector_id},
                    },
                    {
                        "kind": "BinaryOperator",
                        "opcode": "=",
                        "inner": [
                            {
                                "kind": "DeclRefExpr",
                                "referencedDecl": {"id": result_id},
                            },
                            {
                                "kind": "IntegerLiteral",
                                "range": extent("-1"),
                            },
                        ],
                    },
                ],
            },
            {
                "kind": "ReturnStmt",
                "range": extent("return result;"),
                "inner": [
                    {
                        "kind": "DeclRefExpr",
                        "referencedDecl": {"id": result_id},
                    }
                ],
            },
        ],
    }
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "semantic.c"
        source.write_text(source_text, encoding="utf-8")
        result = generator.semantic_single_exit_fault_result(
            declaration,
            source,
            "c:@F@renamed",
            [
                {
                    "caller_usr": "c:@F@renamed",
                    "usr": "c:@F@selector",
                    "start": selector_range["begin"]["offset"],
                    "end": (
                        selector_range["end"]["offset"]
                        + selector_range["end"]["tokLen"]
                    ),
                }
            ],
            {"c:@F@selector"},
        )
        check(
            result == "-1",
            "selector-result data flow did not identify the failure result",
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
        "c:@F@p101_example_v",
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
        "c:@F@p101_shmctl",
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
        "c:@F@p101_example",
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
        "native_waitpid_nointr(native_pid, &native_status)",
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
        "c:@F@p101_glob",
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
        "c:@F@p101_execv",
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
        "c:@F@p101_pipe",
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
        "c:@F@p101_posix_memalign",
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
        "c:@F@p101_posix_spawnp",
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
        "c:@F@p101_posix_spawn_file_actions_addclose",
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
        "c:@F@p101_posix_spawnp",
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
        "c:@F@p101_sem_wait",
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
        "c:@F@p101_sigwait",
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

    catalog = generator.native_contract_fixture(
        "p101_catgets",
        "c:@F@p101_catgets",
        {
            "name": "renamed_catalog",
            "type": {"qualType": "nl_catd"},
        },
        2,
    )
    check(
        catalog is not None
        and catalog[1] == "(nl_catd)0"
        and catalog[3] == [],
        "message-catalog fixture does not leave outcome policy to the contract",
    )

    setrlimit = generator.native_contract_fixture(
        "p101_setrlimit",
        "c:@F@p101_setrlimit",
        {
            "name": "renamed_resource",
            "type": {"qualType": "int"},
        },
        2,
    )
    check(
        setrlimit is not None and setrlimit[1] == "-1",
        "setrlimit fixture could mutate a real process limit",
    )

    aio_fsync = generator.native_contract_fixture(
        "p101_aio_fsync",
        "c:@F@p101_aio_fsync",
        {
            "name": "renamed_control_block",
            "type": {"qualType": "struct aiocb *"},
        },
        3,
    )
    check(
        aio_fsync is not None
        and any("tmpfile()" in line for line in aio_fsync[0])
        and any(".aio_fildes" in line for line in aio_fsync[0])
        and any("fclose" in line for line in aio_fsync[3]),
        "aio_fsync fixture does not own and clean up a valid descriptor",
    )

    socket_domain = generator.native_contract_fixture(
        "p101_socket",
        "c:@F@p101_socket",
        {
            "name": "renamed_domain",
            "type": {"qualType": "int"},
        },
        2,
    )
    check(
        socket_domain is not None and socket_domain[1] == "AF_INET",
        "socket domain fixture depends on a parameter name",
    )

    send_socket = generator.native_contract_fixture(
        "p101_send",
        "c:@F@p101_send",
        {
            "name": "renamed_descriptor",
            "type": {"qualType": "int"},
        },
        2,
    )
    send_payload = generator.native_contract_fixture(
        "p101_send",
        "c:@F@p101_send",
        {
            "name": "renamed_payload",
            "type": {"qualType": "const void *"},
        },
        3,
    )
    send_length = generator.native_contract_fixture(
        "p101_send",
        "c:@F@p101_send",
        {
            "name": "renamed_extent",
            "type": {"qualType": "size_t"},
        },
        4,
    )
    check(
        send_socket is not None
        and any("socketpair(AF_UNIX, SOCK_STREAM" in line for line in send_socket[0])
        and send_payload is not None
        and send_payload[1] == '"p101"'
        and send_length is not None
        and send_length[1] == "4U",
        "send fixture is not a valid typed socket operation",
    )

    interface_name = generator.native_contract_fixture(
        "p101_if_nametoindex",
        "c:@F@p101_if_nametoindex",
        {
            "name": "renamed_interface",
            "type": {"qualType": "const char *"},
        },
        2,
    )
    check(
        interface_name is not None
        and any("if_nameindex()" in line for line in interface_name[0])
        and any("if_freenameindex" in line for line in interface_name[3]),
        "interface fixture does not discover and release a real interface",
    )


def test_native_smoke_outcomes_are_asserted(generator) -> None:
    declaration = {
        "name": "p101_example",
        "type": {
            "qualType": (
                "int (const struct p101_env *, struct p101_error *)"
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
        ],
    }
    errors = {
        "linux": ["EIO"],
        "macos": ["EIO"],
        "freebsd": ["EIO"],
        "posix": ["EIO"],
    }
    failure = {
        "kind": "value",
        "expression": "-1",
        "error_domain": "errno",
    }
    success_source = generator.fault_test(
        "p101_example",
        "c:@F@p101_example",
        declaration,
        errors,
        failure,
    )
    check(
        "if(p101_error_has_error(native_err))" in success_source,
        "native smoke does not require success by default",
    )
    check(
        "bool               native_passed = true;" in success_source
        and "native_passed = false;" in success_source
        and "native_child_status = native_passed ? EXIT_SUCCESS"
        in success_source
        and "_Exit(" not in success_source,
        "native smoke can bypass caller-owned cleanup after a failed assertion",
    )
    error_source = generator.fault_test(
        "p101_example",
        "c:@F@p101_example",
        declaration,
        errors,
        failure,
        {
            "outcome": "error",
            "error_domain": "errno",
            "error_code": "EINVAL",
            "result_kind": "equals",
            "result_expression": "-1",
        },
    )
    check(
        "p101_error_is_errno(native_err, EINVAL)" in error_source
        and "if(native_result != -1)" in error_source,
        "declared native failure is not asserted exactly",
    )
    check_source = generator.fault_test(
        "p101_example",
        "c:@F@p101_example",
        declaration,
        errors,
        failure,
        {
            "outcome": "error",
            "error_domain": "check",
            "error_code": "-1",
            "result_kind": "equals",
            "result_expression": "-1",
        },
    )
    check(
        "p101_error_is_error(native_err, P101_ERROR_CHECK, -1)"
        in check_source,
        "declared native check rejection is not asserted exactly",
    )


def test_native_resource_fixtures_are_live_and_cleanup_is_checked(generator) -> None:
    msgget = generator.native_contract_fixture(
        "p101_msgget",
        "c:@F@p101_msgget",
        {"type": {"qualType": "int"}},
        3,
    )
    check(
        msgget is not None
        and msgget[1] == "IPC_CREAT | 0600"
        and any("msgctl(native_result, IPC_RMID" in line for line in msgget[3])
        and any("native_passed = false" in line for line in msgget[3]),
        "message-queue native smoke does not prove caller-owned cleanup",
    )

    semget = generator.native_contract_fixture(
        "p101_semget",
        "c:@F@p101_semget",
        {"type": {"qualType": "int"}},
        4,
    )
    check(
        semget is not None
        and any("semctl(native_result, 0, IPC_RMID)" in line for line in semget[3])
        and any("native_passed = false" in line for line in semget[3]),
        "semaphore native smoke does not prove caller-owned cleanup",
    )

    shmget = generator.native_contract_fixture(
        "p101_shmget",
        "c:@F@p101_shmget",
        {"type": {"qualType": "int"}},
        4,
    )
    check(
        shmget is not None
        and any("shmctl(native_result, IPC_RMID" in line for line in shmget[3])
        and any("native_passed = false" in line for line in shmget[3]),
        "shared-memory native smoke does not prove caller-owned cleanup",
    )


def test_native_fixture_recovery_never_discards_cleanup_status(generator) -> None:
    fixtures = [
        generator.native_pointer_fixture(
            "p101_dbm_open",
            "c:@F@p101_dbm_open",
            {"type": {"qualType": "DBM *"}},
            2,
        ),
        generator.native_contract_fixture(
            "p101_mkfifo",
            "c:@F@p101_mkfifo",
            {"type": {"qualType": "const char *"}},
            2,
        ),
        generator.native_contract_fixture(
            "p101_msgrcv",
            "c:@F@p101_msgrcv",
            {"type": {"qualType": "int"}},
            2,
        ),
        generator.native_contract_fixture(
            "p101_shm_open",
            "c:@F@p101_shm_open",
            {"type": {"qualType": "const char *"}},
            2,
        ),
        generator.native_contract_fixture(
            "p101_shmdt",
            "c:@F@p101_shmdt",
            {"type": {"qualType": "const void *"}},
            2,
        ),
        generator.native_contract_fixture(
            "p101_sem_open",
            "c:@F@p101_sem_open",
            {"type": {"qualType": "const char *"}},
            2,
        ),
    ]
    check(
        all(fixture is not None for fixture in fixtures),
        "native cleanup regression fixture was not generated",
    )
    fixture_source = "\n".join(
        line
        for fixture in fixtures
        if fixture is not None
        for section in (fixture[0], fixture[2], fixture[3])
        for line in section
    )
    check(
        re.search(
            r"\(void\)(?:msgctl|sem_unlink|shm_unlink|shmctl|"
            r"snprintf|unlink)\s*\(",
            fixture_source,
        )
        is None,
        "native fixture discards setup or recovery cleanup status",
    )
    check(
        "strcat(" not in fixture_source
        and all(
            suffix in fixture_source
            for suffix in (
                "p101-wrapper-dbm-%ld",
                "p101-wrapper-dbm-%ld.db",
                "p101-wrapper-dbm-%ld.dir",
                "p101-wrapper-dbm-%ld.pag",
            )
        ),
        "DBM fixture does not clean every portable backing-file spelling",
    )
    helper = generator.NATIVE_CALLBACK_DEFINITIONS[
        "native_condition_signal_thread"
    ]
    check(
        "(void)pthread_" not in helper
        and "context->status" in helper
        and "lock_status" in helper
        and "signal_status" in helper
        and "unlock_status" in helper,
        "condition helper discards a pthread coordination status",
    )
    wait_fixture = generator.native_contract_fixture(
        "p101_wait",
        "c:@F@p101_wait",
        {"type": {"qualType": "int *"}},
        2,
    )
    check(
        wait_fixture is not None
        and any(
            "native_waitpid_nointr" in line
            for line in wait_fixture[3]
        )
        and not any(
            re.search(r"\bwaitpid\s*\(", line)
            for line in wait_fixture[3]
        ),
        "process fixture cleanup can fail spuriously on EINTR",
    )


def test_checked_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    check(
        contract.get("schema") == "p101-wrapper-failure-contract-v2",
        "failure contract schema drifted",
    )
    check(
        contract.get("semantics", {}).get("parse_environment")
        == (
            "c17-posix2008-xopen700-explicit-platform-feature-profile;"
            "selected-sdk;libclang-headers-only"
        ),
        "failure contract parse environment is not pinned",
    )
    wrappers = contract.get("wrappers", {})
    check(bool(wrappers), "failure contract is empty")
    required = {
        "function_usr",
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
            record["fault_boundary"]
            == "after-entry-trace-before-native-work",
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
        contract.get("schema") == "p101-wrapper-outcome-contract-v2",
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
        "function_usr",
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


def test_native_smoke_contract() -> None:
    contract = json.loads(
        (
            SCRIPTS_ROOT
            / "contracts"
            / "wrapper-native-smoke-contract.json"
        ).read_text(encoding="utf-8")
    )
    check(
        contract.get("schema")
        == "p101-wrapper-native-smoke-contract-v2",
        "native-smoke contract schema drifted",
    )
    check(
        contract.get("default_outcome") == "success",
        "native smokes do not require success by default",
    )
    exceptions = contract.get("exceptions", [])
    check(bool(exceptions), "native-smoke exception catalog is empty")
    exception_usrs = {
        record.get("function_usr")
        for record in exceptions
        if isinstance(record, dict) and record.get("function_usr")
    }
    check(
        len(exception_usrs) == len(exceptions),
        "native-smoke exceptions lack unique semantic identities",
    )
    for record in exceptions:
        name = record.get("function_usr", "?")
        check(
            record.get("outcome") in {"error", "success-or-error"},
            f"{name}: invalid exception outcome",
        )
        check(bool(record.get("rationale")), f"{name}: missing rationale")
        if record.get("outcome") == "error":
            check(
                record.get("error_domain")
                in {"check", "errno", "system", "user"}
                and bool(record.get("error_code")),
                f"{name}: error outcome lacks exact evidence",
            )
            result_kind = record.get("result_kind")
            check(
                result_kind in {"equals", "text"},
                f"{name}: error outcome lacks an exact result assertion",
            )
            if result_kind == "equals":
                check(
                    bool(record.get("result_expression")),
                    f"{name}: equality result lacks an expression",
                )
            else:
                check(
                    isinstance(record.get("result_text"), str),
                    f"{name}: text result lacks exact text",
                )
        else:
            check(
                bool(record.get("allowed_error_codes"))
                and bool(record.get("error_result_expression")),
                f"{name}: conditional outcome lacks exact evidence",
            )


def test_portable_input_contract() -> None:
    contract = json.loads(
        PORTABLE_INPUT_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    check(
        contract.get("schema")
        == "p101-wrapper-portable-input-contract-v2",
        "portable-input contract schema drifted",
    )
    check(
        contract.get("supported_platforms")
        == ["linux", "macos", "freebsd"],
        "portable-input contract does not bind every supported platform",
    )
    check(
        contract.get("default_policy") == "defer-to-native-platform",
        "context-dependent inputs are being pre-rejected by default",
    )
    check(
        contract.get("evidence_contract") == "wrapper-platform-faults.json",
        "portable-input rules are not bound to platform-manual evidence",
    )
    rules = contract.get("rules", [])
    check(bool(rules), "portable-input contract is empty")
    rule_usrs = {
        record.get("function_usr")
        for record in rules
        if isinstance(record, dict) and record.get("function_usr")
    }
    check(
        len(rule_usrs) == len(rules),
        "portable-input rules lack unique semantic identities",
    )
    for record in rules:
        name = record.get("function_usr", "?")
        check(
            isinstance(record, dict)
            and set(record) == {"function_usr", "constraints"},
            f"{name}: portable-input identity record drifted",
        )
        entries = record["constraints"]
        check(bool(entries), f"{name}: portable-input rules are empty")
        for entry in entries:
            check(
                set(entry)
                == {
                    "cases",
                    "constraint",
                    "error_code",
                    "evidence_platforms",
                    "parameter_index",
                    "type",
                },
                f"{name}: portable-input rule fields drifted",
            )
            check(
                type(entry["parameter_index"]) is int
                and entry["parameter_index"] >= 2
                and bool(entry["type"])
                and bool(entry["constraint"])
                and bool(entry["error_code"]),
                f"{name}: invalid portable-input rule",
            )
            check(
                bool(entry["evidence_platforms"])
                and set(entry["evidence_platforms"])
                <= {"linux", "macos", "freebsd"},
                f"{name}: portable-input rule lacks supported-platform evidence",
            )
            check(
                bool(entry["cases"]),
                f"{name}: portable-input rule has no executable cases",
            )
            for case in entry["cases"]:
                check(
                    case.get("input_kind")
                    in {
                        "catalog-failure",
                        "catalog-zero",
                        "negative-one",
                        "null",
                        "text-root-only",
                        "text-with-extra-slash",
                        "text-without-leading-slash",
                    }
                    and case.get("result_kind")
                    in {
                        "argument",
                        "catalog-failure",
                        "negative-one",
                        "null",
                        "pointer-failure",
                    },
                    f"{name}: invalid executable portable-input case",
                )


def test_generated_portable_rejection(generator) -> None:
    declaration = {
        "name": "p101_example",
        "type": {
            "qualType": (
                "int (const struct p101_env *, struct p101_error *, int)"
            )
        },
        "inner": [
            {
                "kind": "ParmVarDecl",
                "type": {"qualType": "const struct p101_env *"},
            },
            {
                "kind": "ParmVarDecl",
                "type": {"qualType": "struct p101_error *"},
            },
            {
                "kind": "ParmVarDecl",
                "type": {"qualType": "int"},
            },
        ],
    }
    source = generator.portable_rejection_tests(
        "p101_example",
        declaration,
        ["env", "err", "0"],
        [
            {
                "cases": [
                    {
                        "input_kind": "negative-one",
                        "result_kind": "negative-one",
                    }
                ],
                "error_code": "EINVAL",
                "parameter_index": 2,
            }
        ],
    )
    for marker in (
        "p101_example(env, err, -1)",
        "p101_error_is_errno(err, EINVAL)",
        "errno == P101_TEST_ERRNO_SENTINEL",
        "portable_result == -1",
        "fault_resource_events == 0U",
    ):
        check(marker in source, f"portable rejection omits {marker}")


def test_generated_harness_has_one_main_exit(generator) -> None:
    declaration = {
        "name": "p101_example",
        "type": {
            "qualType": (
                "int (const struct p101_env *, struct p101_error *)"
            )
        },
        "inner": [
            {
                "kind": "ParmVarDecl",
                "type": {"qualType": "const struct p101_env *"},
            },
            {
                "kind": "ParmVarDecl",
                "type": {"qualType": "struct p101_error *"},
            },
        ],
    }
    source = generator.fault_source(
        "lib_example",
        "",
        {"p101_example": declaration},
        {"p101_example": "c:@F@p101_example"},
        ["p101_example"],
        {
            "c:@F@p101_example": {
                "linux": ["EIO"],
                "macos": ["EIO"],
                "freebsd": ["EIO"],
                "posix": ["EIO"],
            }
        },
        {
            "c:@F@p101_example": {
                "kind": "value",
                "expression": "-1",
                "error_domain": "errno",
            }
        },
        {},
        {},
    )
    main_source = source[source.index("int main(void)") :]
    check("_Exit(" not in source, "generated harness bypasses main")
    check(
        "(void)fclose(outcome_stream)" not in source,
        "generated harness ignores receipt cleanup",
    )
    check(
        "(void)unsetenv(" not in source,
        "generated harness ignores logging-environment setup failure",
    )
    check(
        "(void)snprintf(" not in source
        and "P101_NATIVE_FORMAT_PID_PATH_OR_SKIP" in source,
        "generated harness ignores bounded path-formatting failure",
    )
    check(
        "#include <netinet/in.h>" in source,
        "generated network fixtures lack portable IPv4 declarations",
    )
    check(
        "p101_format_ok_ = native_format_pid_path(" in source
        and "(long)getpid()" not in source,
        "generated path formatting is not an inspectable checked operation",
    )
    check(
        not re.search(r"\b(?:strcat|strcpy|sprintf)\s*\(", source),
        "generated harness uses an unbounded string operation",
    )
    check(
        main_source.count("return ") == 1
        and "return status;" in main_source,
        "generated main has more than one exit point",
    )
    check(
        "if(outcome_stream != NULL)" in source,
        "generated receipt writer still needs an early return",
    )
    check(
        "if(status == EXIT_SUCCESS && failures != 0)" in main_source,
        "generated child can hide receipt-cleanup failure",
    )
    check(
        "native_child_process = true;" in source
        and "failures            = 0;" in source,
        "generated child inherits unrelated parent failures",
    )
    check(
        "while(result < 0 && errno == EINTR);" in source
        and source.count("waitpid(") == 1,
        "generated harness waits can fail spuriously on EINTR",
    )


def main() -> int:
    generator = load_generator()
    tests = (
        lambda: test_macro_reader(generator),
        lambda: test_versioned_libclang_include_discovery(generator),
        lambda: test_function_pointer_result(generator),
        lambda: test_portable_zero_typedefs(generator),
        lambda: test_bool_result_spelling(generator),
        lambda: test_function_selection_uses_semantic_extent(generator),
        lambda: test_protocol_declaration_bit_selects_definitions(generator),
        lambda: test_single_exit_fault_result(generator),
        lambda: test_single_exit_fault_result_follows_selector_value(generator),
        lambda: test_va_list_fixture_is_started(generator),
        lambda: test_public_aggregate_fixture_ignores_private_ast_alias(
            generator
        ),
        lambda: test_portable_result_assertions(generator),
        lambda: test_fallible_wrapper_has_isolated_native_smoke(generator),
        lambda: test_fixture_roles_do_not_depend_on_parameter_names(generator),
        lambda: test_native_fixtures_use_types_and_api_positions(generator),
        lambda: test_native_smoke_outcomes_are_asserted(generator),
        lambda: test_native_resource_fixtures_are_live_and_cleanup_is_checked(
            generator
        ),
        lambda: test_native_fixture_recovery_never_discards_cleanup_status(
            generator
        ),
        test_checked_contract,
        test_outcome_contract,
        test_native_smoke_contract,
        test_portable_input_contract,
        lambda: test_generated_portable_rejection(generator),
        lambda: test_generated_harness_has_one_main_exit(generator),
    )
    for test in tests:
        test()
    print(f"wrapper failure contract tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
