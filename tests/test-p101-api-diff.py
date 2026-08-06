#!/usr/bin/env python3
"""Tests for governed API snapshots and compatibility diffs."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "runtime" / "p101-api-diff.py"
SPEC = importlib.util.spec_from_file_location("p101_api_diff", PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load {PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_manifest(root: Path, library: str, rows: list[str]) -> None:
    directory = root / "libraries" / library
    directory.mkdir(parents=True)
    (directory / "api-manifest.tsv").write_text(
        "function\tfunction_usr\tprovenance\tcurrent_header\tlinux\tmacos\tfreebsd\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p101-api-diff-") as temporary:
        root = Path(temporary)
        write_manifest(
            root,
            "lib_one",
            [
                "p101_keep\tc:@F@p101_keep\tPOSIX\tinclude/one.h\tyes\tyes\tyes",
                "p101_remove\tc:@F@p101_remove\tPOSIX\tinclude/one.h\tyes\tyes\tyes",
                "p101_platform\tc:@F@p101_platform\tPOSIX\tinclude/one.h\tyes\tyes\tyes",
            ],
        )
        old = MODULE.snapshot(root)
        manifest = root / "libraries" / "lib_one" / "api-manifest.tsv"
        manifest.write_text(
            "function\tfunction_usr\tprovenance\tcurrent_header\tlinux\tmacos\tfreebsd\n"
            "p101_keep\tc:@F@p101_keep\tPOSIX\tinclude/one.h\tyes\tyes\tyes\n"
            "p101_platform\tc:@F@p101_platform\tPOSIX\tinclude/one.h\tyes\tno\tyes\n"
            "p101_added\tc:@F@p101_added\tPOSIX\tinclude/one.h\tyes\tyes\tyes\n",
            encoding="utf-8",
        )
        report = MODULE.compare(old, MODULE.snapshot(root))
        check(report["summary"]["additions"] == 1, "addition count drift")
        check(report["summary"]["breaking_changes"] == 2, "breaking count drift")
        check({item["id"] for item in report["findings"]} == {
            "P101-API-001",
            "P101-API-004",
        }, "API diagnostic identities drifted")
    print("p101 API diff tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
