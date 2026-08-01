#!/usr/bin/env python3
"""Require one compiled, invoked unit-test case for every public p101 API."""

from __future__ import annotations

import csv
import json
import platform
import re
from pathlib import Path

from wrapper_errno_contract import (
    current_platform_key,
    injected_error_cases,
    load_contract,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPOS_PATH = SCRIPT_DIR / "repos.txt"
CONTRACT_PATH = SCRIPT_DIR / "instrumentation-contract.json"
ERRNO_CONTRACT_PATH = SCRIPT_DIR / "wrapper-errno-contract.json"
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
        repo = (SCRIPT_DIR / relative).resolve()
        libraries[repo.name] = repo
    return libraries


def main() -> int:
    failures: list[str] = []
    repositories = active_library_repositories()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    try:
        errno_contract = load_contract(ERRNO_CONTRACT_PATH)
    except ValueError:
        print("FAIL: unsupported wrapper errno contract")
        return 1
    errno_names = set(errno_contract.get("errno_names", []))
    errno_functions = errno_contract.get("functions", {})
    errno_wrappers = errno_contract.get("wrappers", {})
    platform_coverage = errno_contract.get("platform_coverage", {})
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
            expected_source_kind = (
                "platform-manual"
                if platform_record.get("status") == "documented"
                else "posix-fallback"
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
    native_bindings = [
        binding
        for binding in errno_wrappers.values()
        if binding.get("role") == "native-wrapper"
        and binding.get("function") in errno_functions
    ]
    for required_platform in ("linux", "macos", "freebsd"):
        documented_functions = sum(
            record["platforms"][required_platform]["status"] == "documented"
            for record in errno_functions.values()
        )
        manual_wrappers = sum(
            errno_functions[binding["function"]]["platforms"][
                required_platform
            ]["status"]
            == "documented"
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
            "manual_override_functions": documented_functions,
            "posix_fallback_functions": len(errno_functions)
            - documented_functions,
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
    errno_case_total = 0
    fault_wrappers: set[str] = set()
    for library, repo in sorted(libraries.items()):
        api_rows = table(repo / "api-manifest.tsv")
        expected = {row.get("function", "") for row in api_rows}
        expected.discard("")
        expected_total += len(expected)
        for name in sorted(expected):
            binding = errno_wrappers.get(name)
            if binding is None:
                failures.append(f"{library}:{name}: absent from errno contract")
                continue
            if binding.get("library") != library:
                failures.append(
                    f"{library}:{name}: errno contract assigns "
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
                binding = errno_wrappers.get(name, {})
                function = binding.get("function")
                expected_errors = injected_error_cases(
                    errno_contract,
                    function,
                    platform_key,
                )
                errno_case_total += len(expected_errors)
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
                    r"static const int errors\[\]\s*=\s*\{([^}]*)\};\s*"
                    r"#elif defined\(__APPLE__\)\s*"
                    r"static const int errors\[\]\s*=\s*\{([^}]*)\};\s*"
                    r"#elif defined\(__FreeBSD__\)\s*"
                    r"static const int errors\[\]\s*=\s*\{([^}]*)\};\s*"
                    r"#else\s*"
                    r"static const int errors\[\]\s*=\s*\{([^}]*)\};\s*"
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
                            r"\bE[A-Z0-9_]+\b",
                            arrays_match.group(1),
                        ),
                        "macos": re.findall(
                            r"\bE[A-Z0-9_]+\b",
                            arrays_match.group(2),
                        ),
                        "freebsd": re.findall(
                            r"\bE[A-Z0-9_]+\b",
                            arrays_match.group(3),
                        ),
                        "posix": re.findall(
                            r"\bE[A-Z0-9_]+\b",
                            arrays_match.group(4),
                        ),
                    }
                    expected_by_platform = {
                        checked_platform: injected_error_cases(
                            errno_contract,
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
                                f"{checked_platform} errno cases differ "
                                f"(expected {','.join(platform_errors)}; "
                                f"found {','.join(actual_errors)})"
                            )
                if "p101_error_is_errno(err, state.errnum)" not in (
                    function_match.group(1)
                ):
                    failures.append(
                        f"{library}:{name}: does not verify propagated errno"
                    )
                required_fault_steps = (
                    "for(size_t index = 0U; "
                    "index < sizeof(errors) / sizeof(errors[0]); index++)",
                    "struct fault_state state = {0, errors[index]};",
                    "EXPECT(state.checks == 1);",
                    "p101_error_reset(err);",
                    "p101_env_set_fault_injector(env, NULL, NULL);",
                )
                for required_step in required_fault_steps:
                    if required_step not in function_match.group(1):
                        failures.append(
                            f"{library}:{name}: generated fault test omits "
                            f"{required_step!r}"
                        )

        for name in sorted(expected & actual.keys()):
            binding = errno_wrappers[name]
            function = binding.get("function")
            if binding.get("role") != "native-wrapper" or function is None:
                continue
            documented_failure = any(
                errno_functions[function]["platforms"][required_platform][
                    "effective_errors"
                ]
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
        f"documented/injected errno cases on "
        f"{platform_key or platform.system()}: {errno_case_total}"
    )
    documented_wrappers = {
        name
        for name, binding in errno_wrappers.items()
        if binding.get("role") == "native-wrapper"
        and binding.get("function") in errno_functions
        and any(
            errno_functions[binding["function"]]["platforms"][
                required_platform
            ]["effective_errors"]
            for required_platform in ("linux", "macos", "freebsd")
        )
    }
    print(
        "native wrappers with supported-platform documented faults: "
        f"{len(documented_wrappers & fault_wrappers)}/"
        f"{len(documented_wrappers)}"
    )
    print(
        f"fault-capable wrappers: {len(fault_wrappers)}; "
        f"behavior-only wrappers: {expected_total - len(fault_wrappers)}"
    )
    for reported_platform in ("linux", "macos", "freebsd"):
        manual_count = sum(
            errno_functions[binding["function"]]["platforms"][
                reported_platform
            ]["status"]
            == "documented"
            for binding in native_bindings
        )
        fallback_count = len(native_bindings) - manual_count
        projected_cases = sum(
            len(
                injected_error_cases(
                    errno_contract,
                    errno_wrappers[name].get("function"),
                    reported_platform,
                )
            )
            for name in fault_wrappers
        )
        documented_cases = sum(
            len(
                errno_functions[binding["function"]]["platforms"][
                    reported_platform
                ]["effective_errors"]
            )
            for binding in native_bindings
        )
        documented_wrapper_count = sum(
            bool(
                errno_functions[binding["function"]]["platforms"][
                    reported_platform
                ]["effective_errors"]
            )
            for binding in native_bindings
        )
        print(
            f"{reported_platform}: {manual_count} wrapper manual overrides, "
            f"{fallback_count} POSIX fallbacks; "
            f"{documented_cases} documented faults across "
            f"{documented_wrapper_count} wrappers; "
            f"{projected_cases} injected cases including smoke tests"
        )
    extra_errno_wrappers = errno_wrappers.keys() - {
        row["function"]
        for repo in libraries.values()
        for row in table(repo / "api-manifest.tsv")
    }
    for name in sorted(extra_errno_wrappers):
        failures.append(f"errno:{name}: binding has no active public API")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("workspace public API unit-test contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
