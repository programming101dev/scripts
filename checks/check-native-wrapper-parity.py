#!/usr/bin/env python3
"""Require deterministic native-vs-wrapper differential fixtures."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
CONTRACT = SCRIPTS_ROOT / "contracts" / "native-wrapper-parity.tsv"


def main() -> int:
    failures: list[str] = []
    admitted: set[tuple[str, str]] = set()
    libraries: set[str] = set()
    with CONTRACT.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    for number, row in enumerate(rows, start=2):
        library = row.get("library", "")
        wrapper = row.get("wrapper", "")
        native = row.get("native", "")
        source_name = row.get("test_source", "")
        key = (library, wrapper)
        if not all((library, wrapper, native, source_name)):
            failures.append(f"{CONTRACT.name}:{number}: incomplete row")
            continue
        if key in admitted:
            failures.append(
                f"{CONTRACT.name}:{number}: duplicate {library}:{wrapper}"
            )
            continue
        admitted.add(key)
        libraries.add(library)
        source = WORKSPACE / "libraries" / library / source_name
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            failures.append(f"{library}:{wrapper}: cannot read {source}: {error}")
            continue
        differential = re.compile(
            rf"^[^\n]*\b{re.escape(wrapper)}\s*\([^\n]*"
            rf"\b{re.escape(native)}\s*\(",
            re.MULTILINE,
        )
        reverse_differential = re.compile(
            rf"^[^\n]*\b{re.escape(native)}\s*\([^\n]*"
            rf"\b{re.escape(wrapper)}\s*\(",
            re.MULTILINE,
        )
        if differential.search(text) is None and reverse_differential.search(text) is None:
            failures.append(
                f"{library}:{wrapper}: {source_name} does not compare it "
                f"directly with {native} in one assertion"
            )
        manifest = (
            WORKSPACE
            / "libraries"
            / library
            / "test"
            / "unit-test-manifest.tsv"
        )
        try:
            manifest_text = manifest.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            failures.append(f"{library}:{wrapper}: cannot read test manifest: {error}")
            continue
        if re.search(rf"(?m)^{re.escape(wrapper)}\t", manifest_text) is None:
            failures.append(f"{library}:{wrapper}: absent from unit-test manifest")

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
