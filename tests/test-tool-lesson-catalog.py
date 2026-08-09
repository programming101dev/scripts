#!/usr/bin/env python3
"""Negative controls for the generated native lesson catalog."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


GENERATOR = Path(__file__).resolve().parents[1] / "generators" / "generate-tool-lesson-catalog.py"


class ToolLessonCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="p101-lesson-catalog-")
        self.root = Path(self.temporary.name)
        self.playground = self.root / "playgrounds"
        self.lessons = self.playground / "lessons"
        self.lessons.mkdir(parents=True)
        (self.lessons / "sample.md").write_text("# Sample\n\nSubstantive guidance.\n", encoding="utf-8")
        self.catalog = self.lessons / "manifest.json"
        self.header = self.root / "lesson_catalog.h"
        self.source = self.root / "lesson_catalog.c"
        self.document = {
            "schema": "p101-finding-lesson-catalog-v2",
            "url_base": "https://example.test/",
            "lessons": [
                {
                    "lesson_id": "P101-LESSON-SAMPLE",
                    "path": "sample.md",
                    "finding_ids": ["P101-SAMPLE-001"],
                }
            ],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_generator(self, *extra: str) -> subprocess.CompletedProcess[str]:
        command = [
            str(GENERATOR),
            "--catalog",
            str(self.catalog),
            "--header",
            str(self.header),
            "--source",
            str(self.source),
            *extra,
        ]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def write_catalog(self) -> None:
        self.catalog.write_text(json.dumps(self.document), encoding="utf-8")

    def test_generates_typed_lookup_and_detects_drift(self) -> None:
        self.write_catalog()
        generated = self.run_generator()
        self.assertEqual(generated.returncode, 0, generated.stderr)
        header = self.header.read_text(encoding="utf-8")
        source = self.source.read_text(encoding="utf-8")
        self.assertIn("P101_TOOL_FINDING_SAMPLE_001", header)
        self.assertIn('"P101-SAMPLE-001"', source)
        self.assertIn('"P101-LESSON-SAMPLE"', source)
        self.assertIn('"lessons/sample.md"', source)
        self.assertIn('"https://example.test/lessons/sample.md"', source)
        checked = self.run_generator("--check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.source.write_text(source + "drift\n", encoding="utf-8")
        drifted = self.run_generator("--check")
        self.assertEqual(drifted.returncode, 1)
        self.assertIn("generated lesson catalog drift", drifted.stderr)

    def test_rejects_duplicate_finding_routes(self) -> None:
        duplicate = dict(self.document["lessons"][0])
        duplicate["lesson_id"] = "P101-LESSON-SECOND"
        self.document["lessons"].append(duplicate)
        self.write_catalog()
        result = self.run_generator()
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate lesson route", result.stderr)


if __name__ == "__main__":
    unittest.main()
