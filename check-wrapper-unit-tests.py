#!/usr/bin/env python3
"""Require one compiled, invoked unit-test case for every public p101 API."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPOS_PATH = SCRIPT_DIR / "repos.txt"
CONTRACT_PATH = SCRIPT_DIR / "instrumentation-contract.json"
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
    for library, repo in sorted(libraries.items()):
        api_rows = table(repo / "api-manifest.tsv")
        expected = {row.get("function", "") for row in api_rows}
        expected.discard("")
        expected_total += len(expected)
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
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("workspace public API unit-test contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
