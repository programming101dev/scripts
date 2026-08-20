#!/usr/bin/env python3
"""Contract tests for the diagnostic-to-playground catalog."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

import p101_lessons


def make_catalog_fixture(root: Path) -> tuple[Path, dict[str, object]]:
    """Create one valid diagnostic example and its owning-tool evidence."""
    lessons = root / "playgrounds" / "lessons"
    evidence = root / "programs" / "p101-example"
    reference = root / "examples" / "p101-example" / "correct.c"
    lessons.mkdir(parents=True)
    evidence.mkdir(parents=True)
    reference.parent.mkdir(parents=True)
    reference.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (evidence / "test.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (lessons / "concept.md").write_text(
        "# Example diagnostic\n\n"
        "This lesson explains one stable diagnostic and the smallest repair.\n\n"
        "<a id=\"P101-TEST-001\"></a>\n\n"
        "## P101-TEST-001 — Example defect\n\n"
        "Broken input:\n\n```text\nThe contract is violated.\n```\n\n"
        "Expected diagnostic:\n\n```text\nP101-TEST-001: Example defect\n```\n\n"
        "Repaired input:\n\n```text\nRestore the declared contract.\n```\n\n"
        "Expected clean result:\n\n```text\nNo P101-TEST-001 finding.\n```\n\n"
        "## Platform boundary\n\nOnly admitted fixture evidence is checked.\n\n"
        "Correct reference: https://example.test/correct.c\n\n"
        "## Verification boundary\n\nRun the owning tool test.\n\n"
        "The owning tool suite proves emission and removal on each platform.\n",
        encoding="utf-8",
    )
    (evidence / "evidence.txt").write_text("P101-TEST-001\n", encoding="utf-8")
    document: dict[str, object] = {
        "schema": p101_lessons.SCHEMA,
        "url_base": "https://example.test/",
        "scope": {
            "admitted_content": "One defect-and-repair example per diagnostic.",
            "example_count": 1,
            "supporting_responsibilities": [
                {"id": "routing", "description": "Resolve the lesson URL."}
            ],
            "excluded_content": ["course structure"],
        },
        "ignored_diagnostic_ids": [],
        "default_platforms": ["macos", "linux", "freebsd"],
        "acceptance_profiles": [
            {
                "profile_id": "example",
                "kind": "native-tool-suite",
                "description": "Example native evidence",
                "finding_ids": ["P101-TEST-001"],
                "command": ["true"],
                "cwd": "programs/p101-example",
                "evidence_paths": ["programs/p101-example/evidence.txt"],
                "platforms": ["macos", "linux", "freebsd"],
                "quick": True,
            }
        ],
        "lessons": [
            {
                "lesson_id": "P101-LESSON-TEST",
                "title": "Example diagnostic",
                "path": "concept.md",
                "finding_ids": ["P101-TEST-001"],
                "verification": "programs/p101-example/test.sh",
                "acceptance_profile": "example",
                "reference_examples": [
                    {
                        "repository": "p101-example",
                        "path": "correct.c",
                        "url": "https://example.test/correct.c",
                    }
                ],
            }
        ],
    }
    path = lessons / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


class LessonCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog_path = WORKSPACE / "playgrounds" / "lessons" / "manifest.json"
        cls.catalog = p101_lessons.load_catalog(cls.catalog_path)

    def test_playground_scope_is_exactly_the_registered_diagnostics(self) -> None:
        self.assertEqual(len(self.catalog.by_finding_id), 111)
        self.assertEqual(self.catalog.scope["example_count"], 111)
        self.assertEqual(len(self.catalog.lessons), 14)
        self.assertEqual(
            set(self.catalog.scope["excluded_content"]),
            {
                "course, week, assignment, project, quiz, and grading structure",
                "canonical correct programs owned by examples repositories",
                "general programming defects not emitted by a registered p101 diagnostic",
                "tool orchestration, regression engines, and workspace build policy",
            },
        )

    def test_every_lesson_contains_exactly_its_registered_examples(self) -> None:
        for lesson in self.catalog.lessons:
            text = (WORKSPACE / "playgrounds" / lesson.path).read_text(
                encoding="utf-8"
            )
            headings = p101_lessons.DIAGNOSTIC_PATTERN.findall(
                "\n".join(line for line in text.splitlines() if line.startswith("## "))
            )
            self.assertEqual(set(headings), set(lesson.finding_ids))
            self.assertEqual(text.count("\nBroken input:\n"), len(lesson.finding_ids))
            self.assertEqual(text.count("\nExpected diagnostic:\n"), len(lesson.finding_ids))
            self.assertEqual(text.count("\nRepaired input:\n"), len(lesson.finding_ids))
            self.assertEqual(text.count("\nExpected clean result:\n"), len(lesson.finding_ids))

    def test_all_emitted_diagnostics_have_examples(self) -> None:
        missing, stale_ignored = p101_lessons.validate_coverage(
            self.catalog, WORKSPACE
        )
        self.assertEqual(missing, set())
        self.assertEqual(stale_ignored, set())
        discovered = p101_lessons.discover_diagnostic_ids(WORKSPACE)
        self.assertTrue(
            {
                "P101-POLICY-SYNC-001",
                "P101-POLICY-SECURE-001",
                "P101-POLICY-SECURE-002",
                "P101-POLICY-SECURE-003",
                "P101-POLICY-SECURE-004",
            }.issubset(discovered)
        )

    def test_every_diagnostic_has_owning_tool_acceptance(self) -> None:
        coverage = p101_lessons.coverage_document(self.catalog)
        self.assertEqual(coverage["summary"]["diagnostic_ids"], 111)
        self.assertEqual(coverage["summary"]["native_suite_ids"], 111)
        self.assertEqual(coverage["summary"]["native_case_ids"], 0)
        self.assertEqual(coverage["summary"]["uncovered_ids"], 0)
        for row in coverage["diagnostics"]:
            self.assertIn(row["native_evidence"], {"native-tool-suite", "native-policy-suite"})
            self.assertTrue(row["native_reference"])
            self.assertEqual(set(row["platforms"]), {"macos", "linux", "freebsd"})
        for profile in self.catalog.profiles:
            self.assertNotIn("templates/template-c/test.sh", " ".join(profile.command))
            self.assertTrue(
                any(
                    p101_lessons.ENVIRONMENT_ARGUMENT.fullmatch(argument)
                    for argument in profile.command
                )
            )

    def test_protocol_pairs_route_to_the_shared_lesson(self) -> None:
        for finding_id in self.catalog.by_finding_id:
            pair = p101_lessons.protocol_pair(self.catalog, finding_id)
            primary = pair["broken"]["findings"][0]["lesson"]["primary"]
            self.assertEqual(primary["lesson_id"], pair["lesson_id"])
            self.assertEqual(pair["repaired"]["findings"], [])

    def test_annotation_preserves_evidence_and_adds_lesson(self) -> None:
        document = {
            "findings": [{"id": "P101-FD-001", "evidence": {"fd": 3}}]
        }
        annotated = p101_lessons.annotate_document(document, self.catalog)
        finding = annotated["findings"][0]
        self.assertEqual(finding["evidence"], {"fd": 3})
        self.assertEqual(
            finding["lesson"]["primary"]["lesson_id"],
            "P101-LESSON-GENERIC-RESOURCES",
        )

    def test_cli_works_outside_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [str(SCRIPTS_ROOT / "runtime" / "p101_lessons.py"), "list"],
                cwd=directory,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(completed.stdout.splitlines()), 111)

    def test_check_command_reports_the_exact_count(self) -> None:
        completed = subprocess.run(
            [str(SCRIPTS_ROOT / "runtime" / "p101_lessons.py"), "check"],
            cwd=SCRIPTS_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("111 diagnostic IDs", completed.stdout)

    def test_loader_rejects_scope_or_example_drift(self) -> None:
        mutations = [
            lambda d: d.__setitem__("scope", None),
            lambda d: d["scope"].__setitem__("example_count", 2),
            lambda d: d["scope"].__setitem__("supporting_responsibilities", []),
            lambda d: d["scope"].__setitem__("excluded_content", []),
            lambda d: d["lessons"][0].__setitem__(
                "finding_ids", ["P101-TEST-002"]
            ),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                path, document = make_catalog_fixture(Path(directory))
                candidate = deepcopy(document)
                mutate(candidate)
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(p101_lessons.LessonCatalogError):
                    p101_lessons.load_catalog(path)

    def test_loader_rejects_duplicate_diagnostic_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, document = make_catalog_fixture(Path(directory))
            duplicate = deepcopy(document["lessons"][0])
            duplicate["lesson_id"] = "P101-LESSON-DUPLICATE"
            document["lessons"].append(duplicate)
            document["scope"]["example_count"] = 2
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(p101_lessons.LessonCatalogError):
                p101_lessons.load_catalog(path)

    def test_loader_rejects_missing_reference_or_verification_command(self) -> None:
        mutations = [
            lambda d: d["lessons"][0].__setitem__("reference_examples", []),
            lambda d: d["lessons"][0].__setitem__(
                "verification", "programs/p101-example/missing-test"
            ),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                path, document = make_catalog_fixture(Path(directory))
                mutate(document)
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(p101_lessons.LessonCatalogError):
                    p101_lessons.load_catalog(path)

    def test_loader_rejects_missing_native_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = make_catalog_fixture(Path(directory))
            evidence = path.parents[2] / "programs" / "p101-example" / "evidence.txt"
            evidence.write_text("no diagnostic ID\n", encoding="utf-8")
            with self.assertRaises(p101_lessons.LessonCatalogError):
                p101_lessons.load_catalog(path)

    def test_structural_verification_writes_all_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "verification"
            result = p101_lessons.command_verify_all(
                Namespace(
                    catalog=self.catalog_path,
                    output=output,
                    quick=False,
                    full=False,
                    jobs=1,
                )
            )
            receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(receipt["protocol_pairs"], 111)
        self.assertEqual(receipt["summary"]["result"], "PASS")

    def test_native_profile_uses_qualified_tool_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, document = make_catalog_fixture(root)
            document["acceptance_profiles"][0]["command"] = [
                "${P101_FIXTURE_TOOL}"
            ]
            path.write_text(json.dumps(document), encoding="utf-8")
            catalog = p101_lessons.load_catalog(path)
            profile = catalog.profiles[0]
            output = root / "output"
            output.mkdir()
            with patch.dict("os.environ", {}, clear=True):
                skipped = p101_lessons._run_profile(catalog, profile, output)
            self.assertEqual(skipped["status"], "SKIP")
            self.assertIn("P101_FIXTURE_TOOL", skipped["reason"])
            with patch.dict(
                "os.environ", {"P101_FIXTURE_TOOL": "/usr/bin/true"}, clear=True
            ):
                executed = p101_lessons._run_profile(catalog, profile, output)
            self.assertEqual(executed["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
