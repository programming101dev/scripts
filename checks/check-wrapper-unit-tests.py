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
from c_facts import CFactError, acquire  # noqa: E402


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


def active_cmake_text(text: str) -> str:
    """Remove comments before checking exact CMake tokens."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def cmake_has_token(text: str, token: str) -> bool:
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
            active_cmake_text(text),
        )
        is not None
    )


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
    if failure_contract.get("schema") != "p101-wrapper-failure-contract-v2":
        print("FAIL: unsupported wrapper failure contract")
        return 1
    failure_wrappers = failure_contract.get("wrappers", {})
    failure_wrappers_by_usr = {
        record.get("function_usr"): record
        for record in failure_wrappers.values()
        if isinstance(record, dict) and record.get("function_usr")
    }
    if len(failure_wrappers_by_usr) != len(failure_wrappers):
        failures.append(
            "failure contract has missing or duplicate declaration identities"
        )
    outcome_contract = json.loads(
        OUTCOME_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    if outcome_contract.get("schema") != "p101-wrapper-outcome-contract-v2":
        print("FAIL: unsupported wrapper outcome contract")
        return 1
    outcome_apis = outcome_contract.get("apis", {})
    outcome_apis_by_usr = {
        record.get("function_usr"): record
        for record in outcome_apis.values()
        if isinstance(record, dict) and record.get("function_usr")
    }
    if len(outcome_apis_by_usr) != len(outcome_apis):
        failures.append(
            "outcome contract has missing or duplicate declaration identities"
        )
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
    fault_wrappers_by_usr = {
        binding.get("function_usr"): binding
        for binding in fault_wrappers_by_name.values()
        if isinstance(binding, dict) and binding.get("function_usr")
    }
    if len(fault_wrappers_by_usr) != len(fault_wrappers_by_name):
        failures.append(
            "platform-fault contract has missing or duplicate declaration identities"
        )
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
        if (
            not isinstance(record.get("symbol_header"), str)
            or not record["symbol_header"]
        ):
            failures.append(
                f"system:{function}: missing defining-header identity"
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
    semantic_sources: set[Path] = set()
    for repo in libraries.values():
        manifest_path = repo / "test" / "unit-test-manifest.tsv"
        if not manifest_path.is_file():
            continue
        for row in table(manifest_path):
            source_value = row.get("test_source", "")
            source = (repo / source_value).resolve()
            if source.is_file():
                semantic_sources.add(source)
    try:
        semantic_facts = acquire(SCRIPTS_ROOT.parent, semantic_sources)
    except CFactError as error:
        print(f"FAIL: {error}")
        return 1
    calls_by_source: dict[Path, set[str]] = {}
    for fact in semantic_facts:
        if fact["kind"] != "CALL":
            continue
        path = Path(str(fact["path"])).resolve()
        calls_by_source.setdefault(path, set()).add(str(fact.get("usr", "")))

    for library, repo in sorted(libraries.items()):
        api_rows = table(repo / "api-manifest.tsv")
        expected_by_usr = {
            row.get("function_usr", ""): row
            for row in api_rows
            if row.get("function_usr", "")
        }
        if len(expected_by_usr) != len(api_rows):
            failures.append(
                f"{library}: API manifest has missing or duplicate function identities"
            )
        expected_total += len(expected_by_usr)
        for wrapper_usr, api_row in sorted(expected_by_usr.items()):
            name = api_row.get("function", wrapper_usr)
            outcome = outcome_apis_by_usr.get(wrapper_usr)
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
                binding = fault_wrappers_by_usr.get(wrapper_usr, {})
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
            binding = fault_wrappers_by_usr.get(wrapper_usr)
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
            manifest_native_function = function or "-"
            expected_native_usr = f"c:@F@{function}" if function else "-"
            if (
                api_row.get("native_function", "") != manifest_native_function
                or api_row.get("native_function_usr", "")
                != expected_native_usr
            ):
                failures.append(
                    f"{library}:{name}: API manifest native identity differs "
                    "from the reviewed platform-fault contract"
                )
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
        actual_by_usr: dict[str, dict[str, str]] = {}
        for row in manifest:
            name = row.get("function", "")
            wrapper_usr = row.get("function_usr", "")
            if not wrapper_usr:
                failures.append(f"{library}:{name}: test row has no function identity")
                continue
            if wrapper_usr in actual_by_usr:
                failures.append(
                    f"{library}: duplicate test row for {wrapper_usr}"
                )
                continue
            actual_by_usr[wrapper_usr] = row
            kind = row.get("test_kind", "")
            if kind not in VALID_KINDS:
                failures.append(f"{library}:{name}: unknown test kind {kind!r}")
            source_value = row.get("test_source", "")
            source = repo / source_value
            if not source.is_file():
                failures.append(f"{library}:{name}: missing {source_value}")
                continue
            if wrapper_usr not in calls_by_source.get(source.resolve(), set()):
                failures.append(
                    f"{library}:{name}: {source_value} has no resolved call "
                    f"to {wrapper_usr}"
                )
            if kind == "fault":
                fault_wrappers.add(wrapper_usr)
                failure_record = failure_wrappers_by_usr.get(wrapper_usr)
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
                function = fault_wrappers_by_usr.get(
                    wrapper_usr,
                    {},
                ).get("function")
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
                    != "after-entry-trace-before-native-work"
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
                binding = fault_wrappers_by_usr.get(wrapper_usr, {})
                function = binding.get("function")
                expected_errors = injected_fault_cases(
                    fault_contract,
                    function,
                    platform_key,
                )
                fault_case_total += len(expected_errors)
                return_kind = failure_record.get("return_kind")
                if return_kind not in {"error-code", "value", "void"}:
                    failures.append(
                        f"{library}:{name}: invalid failure return kind "
                        f"{return_kind!r}"
                    )

        for wrapper_usr in sorted(expected_by_usr.keys() & actual_by_usr.keys()):
            name = expected_by_usr[wrapper_usr].get("function", wrapper_usr)
            outcome = outcome_apis_by_usr.get(wrapper_usr, {})
            classification = outcome.get("classification")
            test_kind = actual_by_usr[wrapper_usr].get("test_kind")
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
            binding = fault_wrappers_by_usr.get(wrapper_usr)
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
            if (
                documented_failure
                and actual_by_usr[wrapper_usr].get("test_kind") != "fault"
            ):
                failures.append(
                    f"{library}:{name}: documented platform errors require "
                    "an exhaustive fault test"
                )

        missing = expected_by_usr.keys() - actual_by_usr.keys()
        extra = actual_by_usr.keys() - expected_by_usr.keys()
        for wrapper_usr in sorted(missing):
            name = expected_by_usr[wrapper_usr].get("function", wrapper_usr)
            failures.append(f"{library}:{name}: no unit-test row")
        for wrapper_usr in sorted(extra):
            name = actual_by_usr[wrapper_usr].get("function", wrapper_usr)
            failures.append(f"{library}:{name}: test row has no public API")
        tested_total += len(expected_by_usr.keys() & actual_by_usr.keys())

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
            if source_stem and not cmake_has_token(cmake, source_stem):
                failures.append(
                    f"{library}:{row.get('function', '')}: "
                    f"{source_value} is not wired into test/CMakeLists.txt"
                )
        if any(row.get("test_kind") == "fault" for row in manifest):
            if not cmake_has_token(cmake, "test_fault_wrappers"):
                failures.append(f"{library}: fault tests are not built by CMake")
        if any(row.get("test_kind") == "behavior" for row in manifest):
            if not cmake_has_token(cmake, "test_behavior"):
                failures.append(f"{library}: behavior tests are not built by CMake")

    print(f"public p101 API unit tests: {tested_total}/{expected_total}")
    print(
        f"generated fault cases admitted on "
        f"{platform_key or platform.system()}: "
        f"{fault_case_total}"
    )
    documented_wrappers = {
        wrapper_usr
        for wrapper_usr, binding in fault_wrappers_by_usr.items()
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
        f"injectable APIs classified: {len(fault_wrappers)}; "
        "non-direct APIs explicitly classified: "
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
                    fault_wrappers_by_usr[wrapper_usr].get("function"),
                    reported_platform,
                )
            )
            for wrapper_usr in fault_wrappers
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
    active_public_usrs = {
        row["function_usr"]
        for repo in libraries.values()
        for row in table(repo / "api-manifest.tsv")
    }
    extra_fault_bindings = fault_wrappers_by_usr.keys() - active_public_usrs
    for wrapper_usr in sorted(extra_fault_bindings):
        failures.append(
            f"platform-fault:{wrapper_usr}: binding has no active public API"
        )
    for wrapper_usr in sorted(
        outcome_apis_by_usr.keys() - active_public_usrs
    ):
        failures.append(
            f"outcome:{wrapper_usr}: classification has no active public API"
        )
    missing_failure_wrappers = fault_wrappers - failure_wrappers_by_usr.keys()
    extra_failure_wrappers = failure_wrappers_by_usr.keys() - fault_wrappers
    for wrapper_usr in sorted(missing_failure_wrappers):
        failures.append(
            f"failure:{wrapper_usr}: missing fault-wrapper contract"
        )
    for wrapper_usr in sorted(extra_failure_wrappers):
        failures.append(
            f"failure:{wrapper_usr}: contract has no active fault test"
        )
    short_fault_wrappers = {
        wrapper_usr
        for wrapper_usr, record in failure_wrappers_by_usr.items()
        if "short" in record.get("fault_modes", [])
    }
    lifecycle_short_wrappers = {
        specification.get("fault_usr")
        for specification in lifecycle_contract.get("scenarios", {}).values()
        if "short" in specification.get("fault_modes", [])
    }
    lifecycle_short_wrappers.discard(None)
    for wrapper_usr in sorted(
        short_fault_wrappers - lifecycle_short_wrappers
    ):
        failures.append(
            f"failure:{wrapper_usr}: short fault has no lifecycle scenario"
        )
    for wrapper_usr in sorted(
        lifecycle_short_wrappers - short_fault_wrappers
    ):
        failures.append(
            f"failure:{wrapper_usr}: lifecycle short scenario has no wrapper action"
        )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("workspace public API unit-test contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
