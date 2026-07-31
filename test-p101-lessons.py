#!/usr/bin/env python3
"""Regression tests for the p101 finding-to-lesson contract."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import p101_lessons


class LessonCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = Path(__file__).resolve().parent.parent
        cls.catalog_path = cls.workspace / "playgrounds" / "lessons" / "manifest.json"
        cls.catalog = p101_lessons.load_catalog(cls.catalog_path)

    def test_all_emitted_diagnostics_have_lessons(self) -> None:
        missing, stale_ignored = p101_lessons.validate_coverage(
            self.catalog, self.workspace
        )
        self.assertEqual(missing, set())
        self.assertEqual(stale_ignored, set())

    def test_runtime_finding_has_primary_and_related_lessons(self) -> None:
        lessons = self.catalog.by_finding_id["P101-FD-001"]
        self.assertEqual(lessons[0].lesson_id, "P101-LAB-101")
        self.assertIn("P101-LAB-108", {lesson.lesson_id for lesson in lessons})
        self.assertNotIn("..", lessons[0].url)

    def test_tool_finding_has_concept_lesson(self) -> None:
        lesson = self.catalog.by_finding_id["P101-ERR-004"][0]
        self.assertEqual(lesson.lesson_id, "P101-LESSON-ERROR-CONTRACTS")
        self.assertTrue(lesson.verification.startswith("p101 doctor"))

    def test_new_emitted_diagnostic_fails_completeness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "programs" / "p101-example" / "src"
            source.mkdir(parents=True)
            (source / "main.c").write_text(
                'const char *id = "P101-NEW-999";\n', encoding="utf-8"
            )
            missing, stale_ignored = p101_lessons.validate_coverage(
                self.catalog, workspace
            )
            self.assertEqual(missing, {"P101-NEW-999"})
            self.assertEqual(
                stale_ignored,
                {"P101-SYNC-000", "P101-UNKNOWN-000", "P101-WRAP-000"},
            )

    def test_annotation_preserves_evidence_and_adds_mapping_summary(self) -> None:
        document = {
            "schema": "example",
            "findings": [
                {"id": "P101-FD-001", "evidence": {"fd": 3}},
                {"id": "P101-NOT-CATALOGED-999"},
                {"id": "P101-UNKNOWN-000"},
            ],
        }
        annotated = p101_lessons.annotate_document(document, self.catalog)
        self.assertEqual(annotated["findings"][0]["evidence"], {"fd": 3})
        self.assertEqual(
            annotated["findings"][0]["lesson"]["primary"]["lesson_id"],
            "P101-LAB-101",
        )
        self.assertEqual(
            annotated["lesson_catalog"]["unmapped_finding_ids"],
            ["P101-NOT-CATALOGED-999"],
        )
        self.assertEqual(annotated["lesson_catalog"]["mapped_findings"], 1)

    def test_report_annotation_is_atomic_and_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(
                json.dumps({"findings": [{"id": "P101-SYNC-001"}]}),
                encoding="utf-8",
            )
            p101_lessons.annotate_report(path, self.catalog)
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                document["findings"][0]["lesson"]["primary"]["lesson_id"],
                "P101-LESSON-SYNCHRONIZATION",
            )
            self.assertFalse(path.with_name("report.json.tmp").exists())

    def test_html_renderers_link_to_the_primary_lesson(self) -> None:
        finding = {
            "id": "P101-FD-001",
            "lesson": {
                "primary": {
                    "title": "Descriptor leak",
                    "url": "https://example.test/fd-leak",
                }
            },
        }
        for filename in ("p101-html-report.py", "p101-check-report.py"):
            path = Path(__file__).resolve().parent / filename
            spec = importlib.util.spec_from_file_location(
                "p101_lesson_test_" + filename.replace("-", "_"), path
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            cell = module.lesson_cell(finding)
            self.assertIn("Descriptor leak", cell)
            self.assertIn("https://example.test/fd-leak", cell)
            if filename == "p101-check-report.py":
                with tempfile.TemporaryDirectory() as directory:
                    doctor = Path(directory) / "doctor"
                    doctor.mkdir()
                    (doctor / "module-map.json").write_text(
                        json.dumps({"findings": [{"id": "P101-MOD-001"}]}),
                        encoding="utf-8",
                    )
                    rows = module.lesson_rows(Path(directory))
                self.assertIn("P101-MOD-001", rows)
                self.assertIn("module-boundaries.md", rows)

    def test_cohort_summary_groups_findings_by_lesson(self) -> None:
        path = Path(__file__).resolve().parent / "p101-cohort-summary.py"
        spec = importlib.util.spec_from_file_location("p101_lesson_test_cohort", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "correlated-report.json"
            report.write_text(
                json.dumps(
                    {
                        "findings": [
                            {"id": "P101-FD-001"},
                            {"id": "P101-FD-001"},
                            {"id": "P101-ERR-004"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            summary = module.collect([report], self.catalog)
        self.assertEqual(summary["lessons"]["P101-LAB-101"]["findings"], 2)
        self.assertEqual(
            summary["lessons"]["P101-LESSON-ERROR-CONTRACTS"]["findings"], 1
        )

    def test_terminal_view_appends_lesson_links(self) -> None:
        path = Path(__file__).resolve().parent / "p101-view.py"
        spec = importlib.util.spec_from_file_location("p101_lesson_test_view", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "correlated-report.json"
            report.write_text(
                json.dumps(
                    p101_lessons.annotate_document(
                        {
                            "findings": [
                                {"id": "P101-FD-001", "policy": "resource"}
                            ]
                        },
                        self.catalog,
                    )
                ),
                encoding="utf-8",
            )
            appendix = module.lesson_appendix(
                Path(directory), report.name, "resource"
            )
        self.assertIn("P101-FD-001", appendix)
        self.assertIn("corpus/cases/fd-leak/lesson.md", appendix)


if __name__ == "__main__":
    unittest.main()
