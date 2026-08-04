#!/usr/bin/env python3
"""Contract tests for the content-addressed p101 C-facts cache."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "checks" / "p101-facts-cache.py"


class FactsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.repo = self.root / "libraries" / "lib_demo"
        self.source = self.repo / "src"
        self.include = self.repo / "include"
        self.source.mkdir(parents=True)
        self.include.mkdir()
        sibling_include = self.root / "libraries" / "lib_dependency" / "include"
        sibling_include.mkdir(parents=True)
        self.dependency_header = sibling_include / "dependency.h"
        self.dependency_header.write_text("int dependency(void);\n", encoding="utf-8")
        (self.source / "demo.c").write_text("int demo(void) { return 1; }\n", encoding="utf-8")
        (self.include / "demo.h").write_text("int demo(void);\n", encoding="utf-8")
        self.database = self.repo / "compile_commands.json"
        self.database.write_text("[]\n", encoding="utf-8")
        self.producer = self.root / "producer"
        self.producer.write_text("producer-v1\n", encoding="utf-8")
        self.facts = self.root / "facts.tsv"
        self.facts.write_text("P101FACT\t4\tFILE\tdemo.c\tdemo\t0\t1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def command(self, operation: str, artifact: str) -> list[str]:
        return [
            str(CACHE),
            operation,
            "--cache",
            str(self.cache),
            "--namespace",
            "library-full",
            "--producer",
            str(self.producer),
            "--compile-db",
            str(self.database),
            "--path",
            str(self.source),
            "--path",
            str(self.include),
            "--dependency-root",
            str(self.root / "libraries"),
            "--artifact",
            artifact,
        ]

    def test_store_restore_and_source_invalidation(self) -> None:
        stored = subprocess.run(
            self.command("store", f"facts={self.facts}"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(stored.returncode, 0, stored.stderr)
        restored_path = self.root / "restored.tsv"
        restored = subprocess.run(
            self.command("restore", f"facts={restored_path}"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(restored_path.read_bytes(), self.facts.read_bytes())

        (self.source / "demo.c").write_text("int demo(void) { return 2; }\n", encoding="utf-8")
        missed = subprocess.run(
            self.command("restore", f"facts={self.root / 'stale.tsv'}"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(missed.returncode, 1, missed.stderr)
        self.assertIn("MISS", missed.stdout)

    def test_entry_can_gain_an_additional_artifact(self) -> None:
        subprocess.run(self.command("store", f"facts={self.facts}"), check=True)
        instrumentation = self.root / "instrumentation.json"
        instrumentation.write_text('{"functions":[]}\n', encoding="utf-8")
        subprocess.run(
            self.command("store", f"instrumentation={instrumentation}"),
            check=True,
        )
        restored_facts = self.root / "again.tsv"
        restored_instrumentation = self.root / "again.json"
        restored = subprocess.run(
            self.command("restore", f"facts={restored_facts}")
            + ["--artifact", f"instrumentation={restored_instrumentation}"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(restored_facts.read_bytes(), self.facts.read_bytes())
        self.assertEqual(restored_instrumentation.read_bytes(), instrumentation.read_bytes())

    def test_shared_header_change_invalidates_entry(self) -> None:
        subprocess.run(self.command("store", f"facts={self.facts}"), check=True)
        self.dependency_header.write_text("long dependency(void);\n", encoding="utf-8")
        missed = subprocess.run(
            self.command("restore", f"facts={self.root / 'dependency-stale.tsv'}"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(missed.returncode, 1, missed.stderr)

    def test_corrupt_artifact_is_rejected(self) -> None:
        subprocess.run(self.command("store", f"facts={self.facts}"), check=True)
        entry = next((self.cache / "entries").iterdir())
        (entry / "facts").write_text("corrupt\n", encoding="utf-8")
        rejected = subprocess.run(
            self.command("restore", f"facts={self.root / 'corrupt.tsv'}"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(rejected.returncode, 2, rejected.stdout)
        self.assertIn("hash mismatch", rejected.stderr)

    def test_concurrent_stores_merge_without_losing_artifacts(self) -> None:
        instrumentation = self.root / "instrumentation.json"
        instrumentation.write_text('{"functions":[]}\n', encoding="utf-8")
        first = subprocess.Popen(
            self.command("store", f"facts={self.facts}"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second = subprocess.Popen(
            self.command("store", f"instrumentation={instrumentation}"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_output, first_error = first.communicate()
        second_output, second_error = second.communicate()
        self.assertEqual(first.returncode, 0, first_output + first_error)
        self.assertEqual(second.returncode, 0, second_output + second_error)

        restored = subprocess.run(
            self.command("restore", f"facts={self.root / 'merged.tsv'}")
            + ["--artifact", f"instrumentation={self.root / 'merged.json'}"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)


if __name__ == "__main__":
    unittest.main()
