#!/usr/bin/env python3
"""Require deterministic native-vs-wrapper differential fixtures."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from c_facts import CFactError, acquire  # noqa: E402

CONTRACT = SCRIPTS_ROOT / "contracts" / "native-wrapper-parity.tsv"


def main() -> int:
    failures: list[str] = []
    admitted: set[tuple[str, str]] = set()
    libraries: set[str] = set()
    with CONTRACT.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    sources: dict[Path, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for number, row in enumerate(rows, start=2):
        library = row.get("library", "")
        wrapper = row.get("wrapper", "")
        native = row.get("native", "")
        source_name = row.get("test_source", "")
        wrapper_usr = row.get("wrapper_usr", "")
        native_usr = row.get("native_usr", "")
        key = (library, wrapper_usr)
        if not all(
            (library, wrapper, native, source_name, wrapper_usr, native_usr)
        ):
            failures.append(f"{CONTRACT.name}:{number}: incomplete row")
            continue
        if wrapper_usr == native_usr:
            failures.append(
                f"{CONTRACT.name}:{number}: wrapper and native identities match"
            )
            continue
        if key in admitted:
            failures.append(
                f"{CONTRACT.name}:{number}: duplicate {library}:{wrapper}"
            )
            continue
        admitted.add(key)
        libraries.add(library)
        source = (WORKSPACE / "libraries" / library / source_name).resolve()
        if not source.is_file():
            failures.append(f"{library}:{wrapper}: missing {source_name}")
            continue
        sources[source].append((number, row))
        manifest = (
            WORKSPACE
            / "libraries"
            / library
            / "test"
            / "unit-test-manifest.tsv"
        )
        try:
            with manifest.open(encoding="utf-8") as stream:
                manifest_rows = list(csv.DictReader(stream, delimiter="\t"))
        except (OSError, UnicodeError, csv.Error) as error:
            failures.append(f"{library}:{wrapper}: cannot read test manifest: {error}")
            continue
        if not any(
            record.get("function_usr") == wrapper_usr
            for record in manifest_rows
        ):
            failures.append(
                f"{library}:{wrapper}: identity {wrapper_usr} is absent "
                "from unit-test manifest"
            )

    if sources:
        try:
            facts = acquire(WORKSPACE, sources)
        except CFactError as error:
            failures.append(str(error))
            facts = []
        calls_by_source_and_caller: dict[tuple[Path, str], set[str]] = defaultdict(set)
        macros_by_source_and_caller: dict[tuple[Path, str], set[str]] = defaultdict(set)
        for fact in facts:
            if not fact.get("caller_usr"):
                continue
            key = (Path(str(fact["path"])).resolve(), str(fact["caller_usr"]))
            if fact["kind"] == "CALL":
                calls_by_source_and_caller[key].add(str(fact.get("usr", "")))
            elif fact["kind"] == "MACRO" and not fact.get("is_definition"):
                macros_by_source_and_caller[key].add(str(fact.get("value", "")))
        for source, source_rows in sources.items():
            for number, row in source_rows:
                if not any(
                    row["wrapper_usr"] in calls
                    and (
                        row["native_usr"] in calls
                        or row["native"] in macros_by_source_and_caller.get(
                            (path, caller), set()
                        )
                    )
                    for (path, caller), calls in calls_by_source_and_caller.items()
                    if path == source
                ):
                    failures.append(
                        f"{row['library']}:{row['wrapper']}: {row['test_source']} "
                        "has no resolved test function that invokes both "
                        f"{row['wrapper_usr']} and {row['native_usr']}"
                    )

    print(
        f"native wrapper parity: {len(rows)} admitted differential fixtures "
        f"across {len(libraries)} libraries"
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("native wrapper parity passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
