#!/usr/bin/env python3
"""Negative and positive controls for shared semantic fact production."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "p101_semantic_prime", ROOT / "checks" / "p101-prime-semantic-cache.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SemanticPrimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.scripts = self.workspace / "scripts"
        self.scripts.mkdir()
        (self.scripts / "repos.txt").write_text(
            "url|../libraries/lib_demo|c\n"
            "url|../templates/template-c|c\n"
            "url|../examples/demo|c\n",
            encoding="utf-8",
        )
        for relative in (
            "libraries/lib_demo/src",
            "libraries/lib_demo/include",
            "libraries/lib_demo/test",
            "templates/template-c/src",
            "templates/template-c/test",
            "examples/demo/src",
        ):
            (self.workspace / relative).mkdir(parents=True)

    def patched_roots(self):
        return mock.patch.multiple(
            MODULE,
            SCRIPTS_ROOT=self.scripts,
            WORKSPACE=self.workspace,
        )

    def test_admitted_paths_exclude_examples_and_template_tests(self) -> None:
        with self.patched_roots():
            admitted = {
                path.relative_to(self.workspace.resolve()).as_posix()
                for path in MODULE.admitted_paths()
            }
        self.assertEqual(
            admitted,
            {
                "libraries/lib_demo/include",
                "libraries/lib_demo/src",
                "libraries/lib_demo/test",
                "templates/template-c/src",
            },
        )

    def test_main_writes_a_replayable_receipt(self) -> None:
        receipt = self.workspace / "receipt.json"
        cache = self.workspace / "cache"
        with self.patched_roots(), mock.patch.object(
            MODULE, "prime", return_value=1
        ) as prime, mock.patch(
            "sys.argv",
            ["p101-prime", "--cache", str(cache), "--receipt", str(receipt)],
        ):
            self.assertEqual(MODULE.main(), 0)
        document = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "p101-semantic-prime-receipt-v1")
        self.assertEqual(document["fact_count"], 1)
        self.assertTrue(document["does_not_prove"])
        prime.assert_called_once()

    def test_main_writes_a_native_fact_bundle_without_losing_zeroes(self) -> None:
        receipt = self.workspace / "receipt.json"
        bundle = self.workspace / "facts.tsv"
        cache = self.workspace / "cache"
        facts = [
            {
                "kind": "CALL",
                "path": "programs/demo/src/main.c",
                "value": "p101_demo",
                "usr": None,
                "caller_usr": "c:@F@main",
                "resolved": True,
                "line": 0,
            },
            {"kind": "TYPE", "path": "ignored.c", "line": 4},
        ]
        with self.patched_roots(), mock.patch.object(
            MODULE, "acquire", return_value=facts
        ) as acquire, mock.patch(
            "sys.argv",
            [
                "p101-prime",
                "--cache",
                str(cache),
                "--receipt",
                str(receipt),
                "--bundle",
                str(bundle),
            ],
        ):
            self.assertEqual(MODULE.main(), 0)
        bundle_lines = bundle.read_text(encoding="utf-8").splitlines()
        self.assertEqual(bundle_lines[0], "P101SEMANTIC\t3")
        self.assertEqual(len(bundle_lines), 3)
        self.assertEqual(len(bundle_lines[1].split("\t")), 26)
        self.assertEqual(len(bundle_lines[2].split("\t")), 26)
        self.assertTrue(
            bundle_lines[1].startswith(
                "CALL\tprograms/demo/src/main.c\tp101_demo\t"
            )
        )
        self.assertTrue(bundle_lines[2].startswith("TYPE\tignored.c\t"))
        acquire.assert_called_once()


if __name__ == "__main__":
    unittest.main()
