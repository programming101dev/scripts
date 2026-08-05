#!/usr/bin/env python3
"""Require one compiled, invoked unit-test case for every public p101 API."""

from __future__ import annotations

import csv
import json
import platform
import re
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from wrapper_fault_contract import (  # noqa: E402
    current_platform_key,
    fault_domain,
    has_explicit_platform_faults,
    has_documented_faults,
    injected_fault_cases,
    load_contract,
)


REPOS_PATH = SCRIPTS_ROOT / "repos.txt"
CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "instrumentation-contract.json"
FAULT_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-platform-faults.json"
)
FAILURE_CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-failure-contract.json"
OUTCOME_CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-outcome-contract.json"
LIFECYCLE_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-lifecycle-contract.json"
)
VALID_KINDS = {"fault", "behavior", "behavior-existing"}


def table(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def active_library_repositories() -> dict[str, Path]:
    libraries: dict[str, Path] = {}
    for line in REPOS_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 2:
            continue
        relative = fields[1]
        if not relative.startswith("../libraries/lib_"):
            continue
        repo = (SCRIPTS_ROOT / relative).resolve()
        libraries[repo.name] = repo
    return libraries


def main() -> int:
    failures: list[str] = []
    repositories = active_library_repositories()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    try:
        fault_contract = load_contract(FAULT_CONTRACT_PATH)
    except ValueError:
        print("FAIL: unsupported wrapper platform-fault contract")
        return 1
    failure_contract = json.loads(
        FAILURE_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    if failure_contract.get("schema") != "p101-wrapper-failure-contract-v1":
        print("FAIL: unsupported wrapper failure contract")
        return 1
    failure_wrappers = failure_contract.get("wrappers", {})
    outcome_contract = json.loads(
        OUTCOME_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    if outcome_contract.get("schema") != "p101-wrapper-outcome-contract-v1":
        print("FAIL: unsupported wrapper outcome contract")
        return 1
    outcome_apis = outcome_contract.get("apis", {})
    valid_outcome_classes = {
        "direct-hard-failure",
        "short-partial-result",
        "delegated-failure",
        "deterministic-rejection",
        "genuinely-infallible",
        "non-returning-cleanup",
    }
    outcome_counts = {
        classification: 0
        for classification in valid_outcome_classes
    }
    lifecycle_contract = json.loads(
        LIFECYCLE_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    errno_names = set(fault_contract.get("errno_names", []))
    errno_functions = fault_contract.get("functions", {})
    system_faults = fault_contract.get("system_faults", {})
    fault_wrappers_by_name = fault_contract.get("wrappers", {})
    platform_coverage = fault_contract.get("platform_coverage", {})
    platform_key = current_platform_key()
    for function, record in sorted(errno_functions.items()):
        posix = record.get("posix", {})
        if posix.get("status") not in {"documented", "not-listed"}:
            failures.append(f"errno:{function}: invalid POSIX status")
        for error_name in posix.get("effective_errors", []):
            if error_name not in errno_names:
                failures.append(
                    f"errno:{function}: unknown POSIX error {error_name}"
                )
        platform_records = record.get("platforms", {})
        for required_platform in ("linux", "macos", "freebsd"):
            platform_record = platform_records.get(required_platform)
            if platform_record is None:
                failures.append(
                    f"errno:{function}: missing {required_platform} manual record"
                )
                continue
            if platform_record.get("status") not in {
                "documented",
                "no-manual",
            }:
                failures.append(
                    f"errno:{function}: invalid {required_platform} status"
                )
            for error_name in platform_record.get("effective_errors", []):
                if error_name not in errno_names:
                    failures.append(
                        f"errno:{function}: unknown {required_platform} "
                        f"error {error_name}"
                    )
            platform_lacks_explicit_faults = (
                platform_record.get("status") == "documented"
                and not has_explicit_platform_faults(
                    fault_contract,
                    function,
                    required_platform,
                )
                and bool(posix.get("effective_errors", []))
            )
            expected_source_kind = (
                "platform-manual"
                if (
                    platform_record.get("status") == "documented"
                    and not platform_lacks_explicit_faults
                )
                else "posix-fallback"
            )
            expected_errors = (
                posix.get("effective_errors", [])
                if platform_lacks_explicit_faults
                else platform_record.get("effective_errors", [])
            )
            if platform_record.get("effective_errors") != expected_errors:
                failures.append(
                    f"errno:{function}: {required_platform} manual lacks "
                    "resolved faults and erases POSIX errors"
                )
            if (
                platform_record.get("effective_source_kind")
                != expected_source_kind
            ):
                failures.append(
                    f"errno:{function}: invalid {required_platform} "
                    "effective source kind"
                )
            expected_source = (
                platform_record.get("source")
                if expected_source_kind == "platform-manual"
                else posix.get("source")
            )
            if platform_record.get("effective_source") != expected_source:
                failures.append(
                    f"errno:{function}: invalid {required_platform} "
                    "effective source"
                )
            expected_source_path = (
                platform_record.get("source_path")
                if expected_source_kind == "platform-manual"
                else None
            )
            if (
                platform_record.get("effective_source_path")
                != expected_source_path
            ):
                failures.append(
                    f"errno:{function}: invalid {required_platform} "
                    "effective source path"
                )
    valid_system_coverage = {
        "exhaustive-symbolic",
        "platform-documented-plus-smoke",
        "representative-unbounded-class",
    }
    for function, record in sorted(system_faults.items()):
        if function not in errno_functions:
            failures.append(
                f"system:{function}: absent from native function catalogue"
            )
        if record.get("coverage_kind") not in valid_system_coverage:
            failures.append(
                f"system:{function}: invalid coverage classification"
            )
        posix = record.get("posix")
        if not isinstance(posix, dict):
            failures.append(f"system:{function}: missing POSIX record")
        else:
            if not isinstance(posix.get("codes"), list):
                failures.append(f"system:{function}: invalid POSIX codes")
            if not posix.get("source"):
                failures.append(f"system:{function}: missing POSIX source")
        platform_records = record.get("platforms", {})
        for required_platform in ("linux", "macos", "freebsd"):
            platform_record = platform_records.get(required_platform)
            if not isinstance(platform_record, dict):
                failures.append(
                    f"system:{function}: missing {required_platform} record"
                )
                continue
            codes = platform_record.get("codes")
            if not isinstance(codes, list):
                failures.append(
                    f"system:{function}: invalid {required_platform} codes"
                )
            elif len(codes) != len(set(codes)):
                failures.append(
                    f"system:{function}: duplicate {required_platform} codes"
                )
            if platform_record.get("source_kind") != "platform-manual":
                failures.append(
                    f"system:{function}: invalid {required_platform} "
                    "source kind"
                )
            if not platform_record.get("source"):
                failures.append(
                    f"system:{function}: missing {required_platform} source"
                )
    native_bindings = [
        binding
        for binding in fault_wrappers_by_name.values()
        if binding.get("role") == "native-wrapper"
        and binding.get("function") in errno_functions
    ]
    for required_platform in ("linux", "macos", "freebsd"):
        manual_functions = sum(
            record["platforms"][required_platform][
                "effective_source_kind"
            ]
            == "platform-manual"
            for record in errno_functions.values()
        )
        manual_wrappers = sum(
            errno_functions[binding["function"]]["platforms"][
                required_platform
            ]["effective_source_kind"]
            == "platform-manual"
            for binding in native_bindings
        )
        expected_coverage = {
            "authoritative_sources": sorted(
                {
                    record["platforms"][required_platform]["source"]
                    for record in errno_functions.values()
                    if record["platforms"][required_platform]["source"]
                    is not None
                }
            ),
            "manual_override_functions": manual_functions,
            "posix_fallback_functions": len(errno_functions)
            - manual_functions,
            "manual_override_wrappers": manual_wrappers,
            "posix_fallback_wrappers": len(native_bindings)
            - manual_wrappers,
        }
        if platform_coverage.get(required_platform) != expected_coverage:
            failures.append(
                f"errno: stale {required_platform} coverage summary"
            )
    expected_libraries = {
        library
        for library, role in contract.get("library_roles", {}).items()
        if role != "infrastructure"
    }
    libraries = {
        library: repositories[library]
        for library in sorted(expected_libraries & repositories.keys())
        if (repositories[library] / "api-manifest.tsv").is_file()
    }
    for library in sorted(expected_libraries - repositories.keys()):
        failures.append(f"{library}: classified public-API library is absent from repos.txt")
    for library in sorted(
        (expected_libraries & repositories.keys()) - libraries.keys()
    ):
        failures.append(f"{library}: missing api-manifest.tsv")
    if not libraries:
        print("FAIL: no active library API manifests were found")
        return 1

    tested_total = 0
    expected_total = 0
    fault_case_total = 0
    fault_wrappers: set[str] = set()
    for library, repo in sorted(libraries.items()):
        api_rows = table(repo / "api-manifest.tsv")
        expected = {row.get("function", "") for row in api_rows}
        expected.discard("")
        expected_total += len(expected)
        for name in sorted(expected):
            outcome = outcome_apis.get(name)
            if outcome is None:
                failures.append(
                    f"{library}:{name}: absent from wrapper outcome contract"
                )
            else:
                classification = outcome.get("classification")
                if classification not in valid_outcome_classes:
                    failures.append(
                        f"{library}:{name}: invalid outcome classification "
                        f"{classification!r}"
                    )
                else:
                    outcome_counts[classification] += 1
                if outcome.get("library") != library:
                    failures.append(
                        f"{library}:{name}: outcome contract assigns "
                        f"{outcome.get('library')!r}"
                    )
                if not outcome.get("rationale"):
                    failures.append(
                        f"{library}:{name}: outcome contract lacks rationale"
                    )
                if (
                    outcome.get("accepts_error")
                    and classification
                    not in {
                        "direct-hard-failure",
                        "short-partial-result",
                    }
                ):
                    failures.append(
                        f"{library}:{name}: APIs accepting p101_error must "
                        "be directly injectable"
                    )
                binding = fault_wrappers_by_name.get(name, {})
                native_function = binding.get("function")
                if (
                    binding.get("role") == "native-wrapper"
                    and has_documented_faults(
                        fault_contract,
                        native_function,
                    )
                    and not outcome.get("accepts_error")
                ):
                    failures.append(
                        f"{library}:{name}: {native_function} has "
                        "documented failures but no p101_error parameter"
                    )
            binding = fault_wrappers_by_name.get(name)
            if binding is None:
                failures.append(
                    f"{library}:{name}: absent from platform-fault contract"
                )
                continue
            if binding.get("library") != library:
                failures.append(
                    f"{library}:{name}: platform-fault contract assigns "
                    f"{binding.get('library')!r}"
                )
            function = binding.get("function")
            if (
                binding.get("role") == "native-wrapper"
                and function not in errno_functions
            ):
                failures.append(
                    f"{library}:{name}: missing native errno function record"
                )
        manifest_path = repo / "test" / "unit-test-manifest.tsv"
        if not manifest_path.is_file():
            failures.append(f"{library}: missing test/unit-test-manifest.tsv")
            continue

        manifest = table(manifest_path)
        actual: dict[str, dict[str, str]] = {}
        for row in manifest:
            name = row.get("function", "")
            if name in actual:
                failures.append(f"{library}: duplicate test row for {name}")
                continue
            actual[name] = row
            kind = row.get("test_kind", "")
            if kind not in VALID_KINDS:
                failures.append(f"{library}:{name}: unknown test kind {kind!r}")
            source_value = row.get("test_source", "")
            source = repo / source_value
            if not source.is_file():
                failures.append(f"{library}:{name}: missing {source_value}")
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            marker = f"P101_TEST_CASE({name})"
            if kind != "behavior-existing" and marker not in text:
                failures.append(
                    f"{library}:{name}: {source_value} lacks {marker}"
                )
            if re.search(rf"\b{re.escape(name)}\s*\(", text) is None:
                failures.append(
                    f"{library}:{name}: {source_value} never invokes wrapper"
                )
            if kind == "fault":
                fault_wrappers.add(name)
                failure_record = failure_wrappers.get(name)
                if failure_record is None:
                    failures.append(
                        f"{library}:{name}: absent from failure contract"
                    )
                    continue
                if failure_record.get("library") != library:
                    failures.append(
                        f"{library}:{name}: failure contract assigns "
                        f"{failure_record.get('library')!r}"
                    )
                function = fault_wrappers_by_name.get(name, {}).get("function")
                expected_domain = fault_domain(fault_contract, function)
                if failure_record.get("error_domain") != expected_domain:
                    failures.append(
                        f"{library}:{name}: injected error domain differs "
                        f"(expected {expected_domain}; found "
                        f"{failure_record.get('error_domain')!r})"
                    )
                if failure_record.get("errno") != "preserved":
                    failures.append(
                        f"{library}:{name}: invalid injected errno policy"
                    )
                if (
                    failure_record.get("fault_boundary")
                    != "before-observable-work"
                ):
                    failures.append(
                        f"{library}:{name}: invalid fault boundary policy"
                    )
                if (
                    failure_record.get("writable_arguments")
                    != "unchanged"
                ):
                    failures.append(
                        f"{library}:{name}: invalid writable argument policy"
                    )
                if failure_record.get("resource_events") != "none":
                    failures.append(
                        f"{library}:{name}: invalid resource-event policy"
                    )
                if failure_record.get("fault_modes") not in (
                    ["error"],
                    ["error", "short"],
                ):
                    failures.append(
                        f"{library}:{name}: invalid fault-mode contract"
                    )
                binding = fault_wrappers_by_name.get(name, {})
                function = binding.get("function")
                expected_errors = injected_fault_cases(
                    fault_contract,
                    function,
                    platform_key,
                )
                fault_case_total += len(expected_errors)
                function_match = re.search(
                    rf"static void test_{re.escape(name)}\b(.*?)(?=\n/\* "
                    r"P101_TEST_CASE|\nint main)",
                    text,
                    re.DOTALL,
                )
                if function_match is None:
                    failures.append(
                        f"{library}:{name}: cannot inspect fault test body"
                    )
                    continue
                arrays_match = re.search(
                    r"#ifdef __linux__\s*"
                    r"static const int\s+errors\[\]\s*=\s*\{([^}]*)\};\s*"
                    r"static const char \*const\s+error_names\[\]\s*=\s*"
                    r"\{([^}]*)\};\s*"
                    r"#elif defined\(__APPLE__\)\s*"
                    r"static const int\s+errors\[\]\s*=\s*\{([^}]*)\};\s*"
                    r"static const char \*const\s+error_names\[\]\s*=\s*"
                    r"\{([^}]*)\};\s*"
                    r"#elif defined\(__FreeBSD__\)\s*"
                    r"static const int\s+errors\[\]\s*=\s*\{([^}]*)\};\s*"
                    r"static const char \*const\s+error_names\[\]\s*=\s*"
                    r"\{([^}]*)\};\s*"
                    r"#else\s*"
                    r"static const int\s+errors\[\]\s*=\s*\{([^}]*)\};\s*"
                    r"static const char \*const\s+error_names\[\]\s*=\s*"
                    r"\{([^}]*)\};\s*"
                    r"#endif",
                    function_match.group(1),
                )
                if arrays_match is None:
                    failures.append(
                        f"{library}:{name}: generated platform arrays are absent"
                    )
                else:
                    actual_by_platform = {
                        "linux": re.findall(
                            r"\b[A-Z][A-Z0-9_]+\b",
                            arrays_match.group(1),
                        ),
                        "macos": re.findall(
                            r"\b[A-Z][A-Z0-9_]+\b",
                            arrays_match.group(3),
                        ),
                        "freebsd": re.findall(
                            r"\b[A-Z][A-Z0-9_]+\b",
                            arrays_match.group(5),
                        ),
                        "posix": re.findall(
                            r"\b[A-Z][A-Z0-9_]+\b",
                            arrays_match.group(7),
                        ),
                    }
                    labels_by_platform = {
                        "linux": json.loads(f"[{arrays_match.group(2)}]"),
                        "macos": json.loads(f"[{arrays_match.group(4)}]"),
                        "freebsd": json.loads(f"[{arrays_match.group(6)}]"),
                        "posix": json.loads(f"[{arrays_match.group(8)}]"),
                    }
                    expected_by_platform = {
                        checked_platform: injected_fault_cases(
                            fault_contract,
                            function,
                            (
                                None
                                if checked_platform == "posix"
                                else checked_platform
                            ),
                        )
                        for checked_platform in (
                            "linux",
                            "macos",
                            "freebsd",
                            "posix",
                        )
                    }
                    for checked_platform, platform_errors in (
                        expected_by_platform.items()
                    ):
                        actual_errors = actual_by_platform[checked_platform]
                        if actual_errors != platform_errors:
                            failures.append(
                                f"{library}:{name}: generated "
                                f"{checked_platform} fault cases differ "
                                f"(expected {','.join(platform_errors)}; "
                                f"found {','.join(actual_errors)})"
                            )
                        if labels_by_platform[checked_platform] != (
                            platform_errors
                        ):
                            failures.append(
                                f"{library}:{name}: generated "
                                f"{checked_platform} outcome labels differ "
                                f"(expected {','.join(platform_errors)}; "
                                "found "
                                f"{','.join(labels_by_platform[checked_platform])})"
                            )
                expected_error_assertion = (
                    "p101_error_is_error(err, P101_ERROR_SYSTEM, "
                    "state.code)"
                    if expected_domain == "system"
                    else "p101_error_is_errno(err, state.code)"
                )
                if expected_error_assertion not in function_match.group(1):
                    failures.append(
                        f"{library}:{name}: does not verify propagated "
                        f"{expected_domain} code"
                    )
                required_fault_steps = (
                    "for(size_t index = 0U; "
                    "index < sizeof(errors) / sizeof(errors[0]); index++)",
                    "struct fault_state state = {0, errors[index]};",
                    "failures_before = failures;",
                    "EXPECT(p101_error_has_no_error(err));",
                    "fault_resource_events = 0U;",
                    "errno                 = P101_TEST_ERRNO_SENTINEL;",
                    "EXPECT(state.checks == 1);",
                    "EXPECT(errno == P101_TEST_ERRNO_SENTINEL);",
                    "EXPECT(fault_resource_events == 0U);",
                    "error_names[index]",
                    "failures == failures_before",
                    "p101_error_reset(err);",
                    "p101_env_set_fault_injector(env, NULL, NULL);",
                    "pid_t native_pid    = fork();",
                    "(void)alarm(2U);",
                    "EXPECT(waitpid(native_pid, &native_status, 0) == native_pid);",
                    "EXPECT(WIFEXITED(native_status));",
                )
                for required_step in required_fault_steps:
                    if required_step not in function_match.group(1):
                        failures.append(
                            f"{library}:{name}: generated fault test omits "
                            f"{required_step!r}"
                        )
                return_kind = failure_record.get("return_kind")
                if return_kind == "error-code":
                    if "EXPECT(result == state.code);" not in (
                        function_match.group(1)
                    ):
                        failures.append(
                            f"{library}:{name}: does not verify error-code "
                            "return"
                        )
                elif return_kind == "value":
                    if not re.search(
                        r"EXPECT\((?:result|isnan\(result\)|"
                        r"memcmp\(&result)",
                        function_match.group(1),
                    ):
                        failures.append(
                            f"{library}:{name}: does not verify failure "
                            "return value"
                        )
                elif return_kind != "void":
                    failures.append(
                        f"{library}:{name}: invalid failure return kind "
                        f"{return_kind!r}"
                    )
                expected_canaries = len(
                    failure_record.get("runtime_canary_arguments", [])
                )
                actual_canaries = len(
                    re.findall(
                        r"EXPECT\(memcmp\(argument_[0-9]+,",
                        function_match.group(1),
                    )
                )
                if actual_canaries != expected_canaries:
                    failures.append(
                        f"{library}:{name}: writable-argument canaries "
                        f"differ (expected {expected_canaries}; "
                        f"found {actual_canaries})"
                    )

        for name in sorted(expected & actual.keys()):
            outcome = outcome_apis.get(name, {})
            classification = outcome.get("classification")
            test_kind = actual[name].get("test_kind")
            if classification in {
                "direct-hard-failure",
                "short-partial-result",
            }:
                if test_kind != "fault":
                    failures.append(
                        f"{library}:{name}: {classification} requires a "
                        "generated fault test"
                    )
            elif (
                classification in valid_outcome_classes
                and test_kind == "fault"
            ):
                failures.append(
                    f"{library}:{name}: {classification} must use a "
                    "behavior test"
                )
            binding = fault_wrappers_by_name.get(name)
            if binding is None:
                continue
            function = binding.get("function")
            if binding.get("role") != "native-wrapper" or function is None:
                continue
            documented_failure = any(
                bool(
                    injected_fault_cases(
                        fault_contract,
                        function,
                        required_platform,
                    )
                )
                and (
                    bool(
                        fault_contract.get("system_faults", {})
                        .get(function, {})
                        .get("platforms", {})
                        .get(required_platform, {})
                        .get("codes", [])
                    )
                    or bool(
                        errno_functions[function]["platforms"][
                            required_platform
                        ]["effective_errors"]
                    )
                )
                for required_platform in ("linux", "macos", "freebsd")
            )
            if documented_failure and actual[name].get("test_kind") != "fault":
                failures.append(
                    f"{library}:{name}: documented platform errors require "
                    "an exhaustive fault test"
                )

        missing = expected - actual.keys()
        extra = actual.keys() - expected
        for name in sorted(missing):
            failures.append(f"{library}:{name}: no unit-test row")
        for name in sorted(extra):
            failures.append(f"{library}:{name}: test row has no public API")
        tested_total += len(expected & actual.keys())

        cmake_path = repo / "test" / "CMakeLists.txt"
        if not cmake_path.is_file():
            failures.append(f"{library}: missing test/CMakeLists.txt")
            continue
        cmake = cmake_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        for row in manifest:
            source_value = row.get("test_source", "")
            source_stem = Path(source_value).stem
            if source_stem and source_stem not in cmake:
                failures.append(
                    f"{library}:{row.get('function', '')}: "
                    f"{source_value} is not wired into test/CMakeLists.txt"
                )
        if any(row.get("test_kind") == "fault" for row in manifest):
            if "test_fault_wrappers" not in cmake:
                failures.append(f"{library}: fault tests are not built by CMake")
        if any(row.get("test_kind") == "behavior" for row in manifest):
            if "test_behavior" not in cmake:
                failures.append(f"{library}: behavior tests are not built by CMake")

    print(f"public p101 API unit tests: {tested_total}/{expected_total}")
    print(
        f"generated fault cases required on "
        f"{platform_key or platform.system()}: "
        f"{fault_case_total}/{fault_case_total}"
    )
    documented_wrappers = {
        name
        for name, binding in fault_wrappers_by_name.items()
        if binding.get("role") == "native-wrapper"
        and binding.get("function") in errno_functions
        and any(
            (
                fault_contract.get("system_faults", {})
                .get(binding["function"], {})
                .get("platforms", {})
                .get(required_platform, {})
                .get("codes", [])
            )
            or errno_functions[binding["function"]]["platforms"][
                required_platform
            ]["effective_errors"]
            for required_platform in ("linux", "macos", "freebsd")
        )
    }
    print(
        "fallible native wrappers with injection coverage: "
        f"{len(documented_wrappers & fault_wrappers)}/"
        f"{len(documented_wrappers)}"
    )
    print(
        f"injectable APIs: {len(fault_wrappers)}/{len(fault_wrappers)}; "
        "explicitly classified non-direct APIs: "
        f"{expected_total - len(fault_wrappers)}/"
        f"{expected_total - len(fault_wrappers)}"
    )
    print(
        "isolated native smoke paths for fallible APIs: "
        f"{len(fault_wrappers)}/{len(fault_wrappers)}"
    )
    print(
        "explicit API outcome classes: "
        + ", ".join(
            f"{classification}={outcome_counts[classification]}"
            for classification in (
                "direct-hard-failure",
                "short-partial-result",
                "delegated-failure",
                "deterministic-rejection",
                "genuinely-infallible",
                "non-returning-cleanup",
            )
        )
    )
    platform_documented_case_total = 0
    for reported_platform in ("linux", "macos", "freebsd"):
        manual_count = sum(
            errno_functions[binding["function"]]["platforms"][
                reported_platform
            ]["effective_source_kind"]
            == "platform-manual"
            for binding in native_bindings
        )
        fallback_count = len(native_bindings) - manual_count
        projected_cases = sum(
            len(
                injected_fault_cases(
                    fault_contract,
                    fault_wrappers_by_name[name].get("function"),
                    reported_platform,
                )
            )
            for name in fault_wrappers
        )
        documented_cases = sum(
            len(
                injected_fault_cases(
                    fault_contract,
                    binding["function"],
                    reported_platform,
                )
            )
            for binding in native_bindings
            if (
                fault_contract.get("system_faults", {})
                .get(binding["function"], {})
                .get("platforms", {})
                .get(reported_platform, {})
                .get("codes", [])
            )
            or errno_functions[binding["function"]]["platforms"][
                reported_platform
            ]["effective_errors"]
        )
        documented_wrapper_count = sum(
            bool(
                fault_contract.get("system_faults", {})
                .get(binding["function"], {})
                .get("platforms", {})
                .get(reported_platform, {})
                .get("codes", [])
                or errno_functions[binding["function"]]["platforms"][
                    reported_platform
                ]["effective_errors"]
            )
            for binding in native_bindings
        )
        platform_documented_case_total += documented_cases
        representative_classes = sum(
            specification["coverage_kind"]
            == "representative-unbounded-class"
            and bool(
                specification["platforms"][reported_platform]["codes"]
            )
            for specification in fault_contract.get(
                "system_faults",
                {},
            ).values()
        )
        print(
            f"{reported_platform}: {manual_count} wrapper manual overrides, "
            f"{fallback_count} POSIX fallbacks; "
            f"documented fault outcomes {documented_cases}/"
            f"{documented_cases} across "
            f"{documented_wrapper_count} wrappers; "
            f"{representative_classes} unbounded failure classes; "
            f"{projected_cases}/{projected_cases} generated cases "
            "including instrumentation smoke tests"
        )
    print(
        "three-platform documented fault matrix: "
        f"{platform_documented_case_total}/"
        f"{platform_documented_case_total}"
    )
    extra_fault_bindings = fault_wrappers_by_name.keys() - {
        row["function"]
        for repo in libraries.values()
        for row in table(repo / "api-manifest.tsv")
    }
    for name in sorted(extra_fault_bindings):
        failures.append(
            f"platform-fault:{name}: binding has no active public API"
        )
    active_public_apis = {
        row["function"]
        for repo in libraries.values()
        for row in table(repo / "api-manifest.tsv")
    }
    for name in sorted(outcome_apis.keys() - active_public_apis):
        failures.append(
            f"outcome:{name}: classification has no active public API"
        )
    missing_failure_wrappers = fault_wrappers - failure_wrappers.keys()
    extra_failure_wrappers = failure_wrappers.keys() - fault_wrappers
    for name in sorted(missing_failure_wrappers):
        failures.append(f"failure:{name}: missing fault-wrapper contract")
    for name in sorted(extra_failure_wrappers):
        failures.append(f"failure:{name}: contract has no active fault test")
    short_fault_wrappers = {
        name
        for name, record in failure_wrappers.items()
        if "short" in record.get("fault_modes", [])
    }
    lifecycle_short_wrappers = {
        specification.get("fault_name")
        for specification in lifecycle_contract.get("scenarios", {}).values()
        if "short" in specification.get("fault_modes", [])
    }
    lifecycle_short_wrappers.discard(None)
    for name in sorted(short_fault_wrappers - lifecycle_short_wrappers):
        failures.append(
            f"failure:{name}: short fault has no lifecycle scenario"
        )
    for name in sorted(lifecycle_short_wrappers - short_fault_wrappers):
        failures.append(
            f"failure:{name}: lifecycle short scenario has no wrapper action"
        )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("workspace public API unit-test contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
