#!/usr/bin/env python3
"""Regression tests for the p101 finding-to-lesson contract."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

import p101_lessons


def make_catalog_fixture(root: Path) -> tuple[Path, dict[str, object]]:
    """Create the smallest complete catalog workspace accepted by the loader."""
    lessons = root / "playgrounds" / "lessons"
    case = root / "playgrounds" / "corpus" / "cases" / "orientation"
    evidence = root / "programs" / "p101-example"
    lessons.mkdir(parents=True)
    case.mkdir(parents=True)
    evidence.mkdir(parents=True)
    (lessons / "concept.md").write_text(
        "# Concept\n\n" + "Substantive lesson guidance. " * 30,
        encoding="utf-8",
    )
    (case / "lesson.md").write_text(
        "# Orientation\n\n" + "Clear repair guidance. " * 12,
        encoding="utf-8",
    )
    (case / "expected.json").write_text(
        json.dumps(
            {
                "issue_id": "P101-LAB-ORIENTATION",
                "title": "Orientation",
                "name": "orientation",
                "tracks": ["core"],
                "expected_findings": ["P101-CASE-001"],
                "lesson_finding_ids": [],
                "fix_goal": "Repair the case",
                "fix_steps": ["Make one focused repair"],
                "lab_order": 1,
            }
        ),
        encoding="utf-8",
    )
    (evidence / "evidence.txt").write_text(
        "P101-TEST-001\n", encoding="utf-8"
    )
    document: dict[str, object] = {
        "schema": p101_lessons.SCHEMA,
        "url_base": "https://example.test/",
        "case_glob": "../corpus/cases/*/expected.json",
        "case_prerequisite_mode": "previous-in-track",
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
                "title": "Test concept",
                "path": "concept.md",
                "track": "core",
                "prerequisites": [],
                "finding_ids": ["P101-TEST-001"],
                "verification": "programs/p101-audit/audit-doctor",
                "acceptance_profile": "example",
            }
        ],
    }
    path = lessons / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


class LessonCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = Path(__file__).resolve().parents[2]
        cls.catalog_path = cls.workspace / "playgrounds" / "lessons" / "manifest.json"
        cls.catalog = p101_lessons.load_catalog(cls.catalog_path)

    def test_lesson_cli_runs_from_an_arbitrary_working_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-lesson-cwd.") as temporary:
            completed = subprocess.run(
                [str(SCRIPTS_ROOT / "runtime" / "p101_lessons.py"), "list"],
                cwd=temporary,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("P101-LAB-", completed.stdout)

    def test_all_emitted_diagnostics_have_lessons(self) -> None:
        missing, stale_ignored = p101_lessons.validate_coverage(
            self.catalog, self.workspace
        )
        self.assertEqual(missing, set())
        self.assertEqual(stale_ignored, set())

    def test_full_receipt_cannot_pass_when_every_native_profile_skips(
        self,
    ) -> None:
        receipt = p101_lessons._receipt(
            self.catalog,
            "full",
            [{"status": "SKIP", "label": "unsupported-platform"}],
            1,
        )
        self.assertEqual(receipt["summary"]["result"], "FAIL")

    def test_runtime_finding_has_primary_and_related_lessons(self) -> None:
        lessons = self.catalog.by_finding_id["P101-FD-001"]
        self.assertEqual(lessons[0].lesson_id, "P101-LAB-101")
        self.assertIn("P101-LAB-108", {lesson.lesson_id for lesson in lessons})
        self.assertNotIn("..", lessons[0].url)

    def test_tool_finding_has_concept_lesson(self) -> None:
        lesson = self.catalog.by_finding_id["P101-ERR-004"][0]
        self.assertEqual(lesson.lesson_id, "P101-LESSON-ERROR-CONTRACTS")
        self.assertTrue(lesson.verification.startswith("programs/p101-audit/audit-doctor"))

    def test_every_diagnostic_has_native_and_repair_evidence(self) -> None:
        coverage = p101_lessons.coverage_document(self.catalog)
        diagnostic_count = len(self.catalog.by_finding_id)
        self.assertEqual(coverage["summary"]["diagnostic_ids"], diagnostic_count)
        self.assertEqual(coverage["summary"]["protocol_pairs"], diagnostic_count)
        self.assertEqual(coverage["summary"]["uncovered_ids"], 0)
        self.assertEqual(
            set(coverage["summary"]["platform_contract"]),
            {"macos", "linux", "freebsd"},
        )
        self.assertEqual(
            coverage["summary"]["platforms_verified_by_full_receipt"], []
        )
        for row in coverage["diagnostics"]:
            self.assertTrue(row["native_reference"])
            self.assertTrue(row["repair_oracle"])
            self.assertEqual(
                set(row["platforms"]), {"macos", "linux", "freebsd"}
            )

    def test_coverage_document_counts_unmapped_diagnostics(self) -> None:
        with patch.object(
            p101_lessons,
            "validate_coverage",
            return_value=({"P101-UNMAPPED-999"}, set()),
        ):
            coverage = p101_lessons.coverage_document(self.catalog)
        self.assertEqual(coverage["summary"]["uncovered_ids"], 1)

    def test_every_protocol_pair_maps_broken_and_clears_repaired(self) -> None:
        for finding_id in self.catalog.by_finding_id:
            pair = p101_lessons.protocol_pair(self.catalog, finding_id)
            self.assertEqual(pair["finding_id"], finding_id)
            self.assertEqual(
                pair["broken"]["findings"][0]["lesson"]["primary"]["lesson_id"],
                pair["lesson_id"],
            )
            self.assertEqual(pair["repaired"]["findings"], [])

    def test_structural_acceptance_writes_receipt_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "acceptance"
            args = Namespace(
                catalog=self.catalog_path,
                output=output,
                quick=False,
                full=False,
            )
            self.assertEqual(p101_lessons.command_verify_all(args), 0)
            receipt = json.loads(
                (output / "receipt.json").read_text(encoding="utf-8")
            )
            coverage = json.loads(
                (output / "coverage.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                receipt["schema"], "p101-lesson-acceptance-receipt-v1"
            )
            self.assertEqual(
                receipt["protocol_pairs"], len(self.catalog.by_finding_id)
            )
            self.assertEqual(coverage["summary"]["uncovered_ids"], 0)

    def test_native_cases_run_in_parallel_and_keep_catalog_order(self) -> None:
        barrier = threading.Barrier(2)

        def fake_run_case(
            _catalog: object,
            case_name: str,
            _output: Path,
            *,
            repaired: bool,
        ) -> dict[str, object]:
            self.assertFalse(repaired)
            barrier.wait(timeout=2)
            return {"label": case_name, "status": "PASS"}

        with patch.object(
            p101_lessons, "_run_case", side_effect=fake_run_case
        ):
            results = p101_lessons._run_cases(
                self.catalog,
                ["first", "second"],
                Path("/unused"),
                2,
            )

        self.assertEqual(
            [result["label"] for result in results],
            ["first", "second"],
        )

    def test_native_failures_print_the_complete_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "failed.log").write_text(
                "first diagnostic\nsecond diagnostic\n", encoding="utf-8"
            )
            stream = io.StringIO()
            with redirect_stdout(stream):
                p101_lessons._print_native_failures(
                    [
                        {
                            "label": "profile-example",
                            "status": "FAIL",
                            "command": ["./test.sh"],
                            "cwd": "/workspace/example",
                            "exit": 2,
                            "log": "failed.log",
                        }
                    ],
                    output,
                )
        text = stream.getvalue()
        self.assertIn("native lesson failure: profile-example", text)
        self.assertIn("$ ./test.sh", text)
        self.assertIn("first diagnostic\nsecond diagnostic", text)

    def test_progress_honors_prerequisites_and_lesson_only_receipts(self) -> None:
        digest = p101_lessons.catalog_digest(self.catalog)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orientation = root / "orientation" / "receipt.json"
            fd_leak = root / "fd-leak" / "receipt.json"
            orientation.parent.mkdir()
            fd_leak.parent.mkdir()
            common = {
                "schema": "p101-lesson-acceptance-receipt-v1",
                "mode": "student-verify",
                "catalog_sha256": digest,
                "summary": {"result": "PASS"},
            }
            orientation.write_text(
                json.dumps(
                    {
                        **common,
                        "lesson_id": "P101-LAB-ORIENTATION",
                        "acceptance": {
                            "lesson_id": "P101-LAB-ORIENTATION"
                        },
                    }
                ),
                encoding="utf-8",
            )
            fd_leak.write_text(
                json.dumps(
                    {
                        **common,
                        "lesson_id": "P101-LAB-101",
                        "finding_id": "P101-FD-001",
                        "acceptance": {"lesson_id": "P101-LAB-101"},
                    }
                ),
                encoding="utf-8",
            )
            progress = p101_lessons.progress_document(self.catalog, [root])
        self.assertEqual(progress["summary"]["lessons_verified"], 2)
        self.assertEqual(progress["summary"]["lessons_completed"], 1)
        self.assertEqual(
            progress["verified_out_of_order_lesson_ids"],
            ["P101-LAB-101"],
        )
        self.assertIn("P101-LAB-001", progress["available_lesson_ids"])

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
            self.assertEqual(stale_ignored, set())

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
            ["P101-NOT-CATALOGED-999", "P101-UNKNOWN-000"],
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
        filename = "p101-html-report.py"
        path = SCRIPTS_ROOT / "runtime" / filename
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

    def test_cohort_summary_groups_findings_by_lesson(self) -> None:
        path = SCRIPTS_ROOT / "runtime" / "p101-cohort-summary.py"
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
        path = SCRIPTS_ROOT / "runtime" / "p101-view.py"
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

    def test_catalog_loader_rejects_malformed_contracts(self) -> None:
        def reject(
            mutate: object,
            *,
            filesystem: object | None = None,
        ) -> None:
            with tempfile.TemporaryDirectory() as directory:
                path, document = make_catalog_fixture(Path(directory))
                candidate = deepcopy(document)
                mutate(candidate)  # type: ignore[operator]
                path.write_text(json.dumps(candidate), encoding="utf-8")
                if filesystem is not None:
                    filesystem(path, candidate)  # type: ignore[operator]
                with self.assertRaises(p101_lessons.LessonCatalogError):
                    p101_lessons.load_catalog(path)

        profile = lambda document: document["acceptance_profiles"][0]
        lesson = lambda document: document["lessons"][0]
        cases = [
            lambda d: d.__setitem__("schema", "wrong"),
            lambda d: d.__setitem__("url_base", "relative"),
            lambda d: d.__setitem__("case_glob", ""),
            lambda d: d.__setitem__("ignored_diagnostic_ids", "bad"),
            lambda d: d.__setitem__(
                "default_platforms", ["macos", "linux", "plan9"]
            ),
            lambda d: d.__setitem__("default_platforms", ["macos"]),
            lambda d: d.__setitem__("case_prerequisite_mode", "none"),
            lambda d: d.__setitem__("acceptance_profiles", []),
            lambda d: d.__setitem__("acceptance_profiles", ["bad"]),
            lambda d: profile(d).pop("description"),
            lambda d: profile(d).__setitem__("description", ""),
            lambda d: profile(d).__setitem__("kind", "guess"),
            lambda d: profile(d).__setitem__("quick", 1),
            lambda d: profile(d).__setitem__("command", [""]),
            lambda d: profile(d).__setitem__("platforms", ["plan9"]),
            lambda d: profile(d).__setitem__("cwd", "../../outside"),
            lambda d: profile(d).__setitem__("cwd", "missing"),
            lambda d: profile(d).__setitem__(
                "evidence_paths", ["../../outside"]
            ),
            lambda d: profile(d).__setitem__(
                "evidence_paths", ["programs/p101-example/missing"]
            ),
            lambda d: d["acceptance_profiles"].append(
                deepcopy(d["acceptance_profiles"][0])
            ),
            lambda d: d.__setitem__("lessons", "bad"),
            lambda d: d.__setitem__("lessons", ["bad"]),
            lambda d: lesson(d).pop("title"),
            lambda d: lesson(d).__setitem__("title", ""),
            lambda d: lesson(d).__setitem__("acceptance_profile", ""),
            lambda d: lesson(d).__setitem__("path", "../../outside.md"),
            lambda d: lesson(d).__setitem__("path", "missing.md"),
            lambda d: lesson(d).__setitem__(
                "acceptance_profile", "missing-profile"
            ),
            lambda d: lesson(d).__setitem__(
                "prerequisites", ["P101-LESSON-MISSING"]
            ),
            lambda d: lesson(d).__setitem__("finding_ids", ["not-an-id"]),
            lambda d: profile(d).__setitem__("finding_ids", ["not-an-id"]),
            lambda d: lesson(d).__setitem__("finding_ids", ["P101-OTHER-001"]),
            lambda d: d.__setitem__("case_glob", "no-cases/*.json"),
        ]
        for index, mutate in enumerate(cases):
            with self.subTest(index=index):
                reject(mutate)

        reject(
            lambda d: lesson(d).__setitem__("path", "concept.md"),
            filesystem=lambda path, _:
                (path.parent / "concept.md").write_text("too short", encoding="utf-8"),
        )
        reject(
            lambda _d: None,
            filesystem=lambda path, _:
                (path.parent.parent / "corpus/cases/orientation/lesson.md").unlink(),
        )
        reject(
            lambda _d: None,
            filesystem=lambda path, _:
                (path.parent.parent / "corpus/cases/orientation/lesson.md").write_text(
                    "short", encoding="utf-8"
                ),
        )

    def test_catalog_loader_rejects_malformed_cases_and_acceptance(self) -> None:
        def reject_case(mutate: object) -> None:
            with tempfile.TemporaryDirectory() as directory:
                path, _ = make_catalog_fixture(Path(directory))
                expected = path.parent.parent / "corpus/cases/orientation/expected.json"
                case = json.loads(expected.read_text(encoding="utf-8"))
                mutate(case)  # type: ignore[operator]
                expected.write_text(json.dumps(case), encoding="utf-8")
                with self.assertRaises(p101_lessons.LessonCatalogError):
                    p101_lessons.load_catalog(path)

        mutations = [
            lambda c: c.__setitem__("issue_id", ""),
            lambda c: c.__setitem__("title", ""),
            lambda c: c.__setitem__("name", 3),
            lambda c: c.__setitem__("tracks", []),
            lambda c: c.__setitem__("expected_findings", "bad"),
            lambda c: c.__setitem__("logic_issue_id", 7),
            lambda c: c.__setitem__("fix_goal", ""),
            lambda c: c.__setitem__("fix_steps", []),
            lambda c: (
                c.__setitem__("expected_findings", []),
                c.__setitem__("lesson_finding_ids", ["P101-CASE-001"]),
            ),
            lambda c: c.__setitem__("expected_findings", ["invalid"]),
            lambda c: c.__setitem__("issue_id", "P101-LESSON-TEST"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                reject_case(mutate)

        with tempfile.TemporaryDirectory() as directory:
            path, document = make_catalog_fixture(Path(directory))
            second = deepcopy(document["acceptance_profiles"][0])
            second["profile_id"] = "second"
            document["acceptance_profiles"].append(second)
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(p101_lessons.LessonCatalogError):
                p101_lessons.load_catalog(path)

        with tempfile.TemporaryDirectory() as directory:
            path, _ = make_catalog_fixture(Path(directory))
            evidence = path.parent.parent.parent / "programs/p101-example/evidence.txt"
            evidence.write_text("no diagnostic here", encoding="utf-8")
            with self.assertRaises(p101_lessons.LessonCatalogError):
                p101_lessons.load_catalog(path)

    def test_catalog_accepts_generated_typed_finding_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = make_catalog_fixture(Path(directory))
            evidence = path.parent.parent.parent / "programs/p101-example/evidence.txt"
            evidence.write_text("P101_TOOL_FINDING_TEST_001\n", encoding="utf-8")
            catalog = p101_lessons.load_catalog(path)
        self.assertIn("P101-TEST-001", catalog.by_finding_id)

    def test_low_level_input_and_evidence_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.json"
            invalid = root / "invalid.json"
            array = root / "array.json"
            invalid.write_text("{", encoding="utf-8")
            array.write_text("[]", encoding="utf-8")
            for path in (missing, invalid, array):
                with self.assertRaises(p101_lessons.LessonCatalogError):
                    p101_lessons._read_object(path)
            with self.assertRaises(p101_lessons.LessonCatalogError):
                p101_lessons._strings([""], "field", invalid)

            reports = root / "reports"
            reports.mkdir()
            valid = reports / "valid.json"
            malformed = reports / "malformed.json"
            text = reports / "finding.log"
            ignored = reports / "ignored.bin"
            valid.write_text(
                json.dumps(
                    {
                        "findings": [
                            {"id": "P101-FD-001"},
                            None,
                            {"id": 1},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            malformed.write_text('{"id": "P101-ERR-004"', encoding="utf-8")
            text.write_text("P101-SYNC-001", encoding="utf-8")
            ignored.write_text("P101-FD-001", encoding="utf-8")
            paths = list(p101_lessons.iter_evidence_paths([valid, reports, missing]))
            self.assertIn(valid, paths)
            self.assertIn(text, paths)
            self.assertNotIn(ignored, paths)
            self.assertEqual(
                p101_lessons.finding_ids_from_evidence(valid), {"P101-FD-001"}
            )
            self.assertEqual(
                p101_lessons.finding_ids_from_evidence(malformed),
                {"P101-ERR-004"},
            )
            self.assertEqual(
                p101_lessons.finding_ids_from_evidence(text), {"P101-SYNC-001"}
            )
            self.assertEqual(
                p101_lessons.finding_ids_from_evidence(missing), set()
            )
            object_json = reports / "object.json"
            object_json.write_text('{"value": "P101-FD-001"}', encoding="utf-8")
            self.assertEqual(
                p101_lessons.finding_ids_from_evidence(object_json),
                {"P101-FD-001"},
            )
            with patch.object(Path, "read_text", side_effect=OSError("denied")):
                self.assertEqual(
                    p101_lessons._evidence_text(root, ["reports/valid.json"]),
                    "",
                )

    def test_annotation_discovery_and_contract_defensive_edges(self) -> None:
        unchanged: dict[str, object] = {"findings": "not-a-list"}
        self.assertIs(
            p101_lessons.annotate_document(unchanged, self.catalog), unchanged
        )
        document = {"findings": [None, {"message": "no id"}]}
        annotated = p101_lessons.annotate_document(document, self.catalog)
        self.assertEqual(annotated["lesson_catalog"]["mapped_findings"], 0)

        empty_lesson = p101_lessons.Lesson(
            "P101-EMPTY",
            "Empty",
            "x",
            "u",
            "core",
            (),
            ("P101-EMPTY-001",),
            "verify",
            1,
        )
        incomplete = p101_lessons.Catalog(
            self.catalog.path,
            (empty_lesson,),
            frozenset(),
            (),
            self.catalog.default_platforms,
        )
        with self.assertRaises(p101_lessons.LessonCatalogError):
            p101_lessons._validate_acceptance_contract(incomplete)

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "programs/p101-sample/src"
            source.mkdir(parents=True)
            (source / "skip.txt").write_text(
                "P101-SKIP-001", encoding="utf-8"
            )
            (source / "bad.c").write_bytes(b"\xff")
            (source / "nested").mkdir()
            self.assertEqual(
                p101_lessons.discover_diagnostic_ids(workspace), set()
            )

    def test_platform_and_acceptance_helper_edges(self) -> None:
        for system, expected in (
            ("Darwin", "macos"),
            ("FreeBSD", "freebsd"),
            ("Linux", "linux"),
            ("Haiku", "haiku"),
        ):
            with self.subTest(system=system), patch(
                "p101_lessons.platform.system", return_value=system
            ):
                self.assertEqual(p101_lessons.host_platform(), expected)
        with self.assertRaises(p101_lessons.LessonCatalogError):
            p101_lessons.acceptance_for(self.catalog, "P101-NOT-REAL-999")
        with self.assertRaises(p101_lessons.LessonCatalogError):
            p101_lessons.resolve_target(self.catalog, "P101-NOT-REAL-999")
        empty = p101_lessons.Lesson(
            "empty", "Empty", "x", "u", "t", (), (), "verify", 1
        )
        with self.assertRaises(p101_lessons.LessonCatalogError):
            p101_lessons._acceptance_for_lesson(self.catalog, empty, None)
        with self.assertRaises(p101_lessons.LessonCatalogError):
            p101_lessons._case_expected(self.catalog, empty)

    def test_run_helpers_and_receipt_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            passed = p101_lessons._run_logged(
                [sys.executable, "-c", "print('ok')"], root, output, "a label"
            )
            failed = p101_lessons._run_logged(
                [str(root / "missing-command")], root, output, "missing"
            )
            self.assertEqual(passed["status"], "PASS")
            self.assertEqual(failed["status"], "FAIL")
            profile = self.catalog.profiles[0]
            with patch("p101_lessons.host_platform", return_value="plan9"):
                skipped = p101_lessons._run_profile(
                    self.catalog, profile, output
                )
            self.assertEqual(skipped["status"], "SKIP")
            with patch(
                "p101_lessons._run_logged", return_value={"status": "PASS"}
            ) as run_profile:
                self.assertEqual(
                    p101_lessons._run_profile(
                        self.catalog, profile, output
                    )["status"],
                    "PASS",
                )
            run_profile.assert_called_once()
            with patch("p101_lessons._run_logged", return_value={"status": "PASS"}) as run:
                p101_lessons._run_case(
                    self.catalog, "fd-leak", output, repaired=False
                )
                p101_lessons._run_case(
                    self.catalog, "fd-leak", output, repaired=True
                )
            self.assertEqual(run.call_count, 2)

            digest = p101_lessons.catalog_digest(self.catalog)
            valid = root / "valid" / "receipt.json"
            stale = root / "stale" / "receipt.json"
            invalid = root / "invalid" / "receipt.json"
            for path in (valid, stale, invalid):
                path.parent.mkdir()
            valid.write_text(
                json.dumps(
                    {
                        "schema": p101_lessons.RECEIPT_SCHEMA,
                        "mode": "full",
                        "catalog_sha256": digest,
                        "platform": "macos",
                        "summary": {"result": "PASS"},
                    }
                ),
                encoding="utf-8",
            )
            stale.write_text(
                json.dumps(
                    {
                        "schema": p101_lessons.RECEIPT_SCHEMA,
                        "mode": "quick",
                        "catalog_sha256": "old",
                        "platform": 2,
                        "summary": [],
                    }
                ),
                encoding="utf-8",
            )
            invalid.write_text("{", encoding="utf-8")
            self.assertEqual(
                p101_lessons._verified_full_platforms(
                    self.catalog, [root, valid]
                ),
                {"macos"},
            )
            coverage = p101_lessons.coverage_document(self.catalog, [root])
            self.assertEqual(
                coverage["summary"]["platforms_verified_by_full_receipt"],
                ["macos"],
            )
            generated = p101_lessons._output_directory(None, "p101-test.")
            self.assertTrue(generated.is_dir())

    def test_protocol_pair_rejects_broken_annotation_contracts(self) -> None:
        with patch("p101_lessons.annotate_document", return_value={}):
            with self.assertRaises(p101_lessons.LessonCatalogError):
                p101_lessons.protocol_pair(self.catalog, "P101-FD-001")

        calls = 0

        def corrupt_fixed(
            document: dict[str, object], catalog: p101_lessons.Catalog
        ) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return p101_lessons.annotate_document(
                    document, catalog
                )
            document["lesson_catalog"] = {"mapped_findings": 1}
            return document

        original = p101_lessons.annotate_document

        def corrupt_with_original(
            document: dict[str, object], catalog: p101_lessons.Catalog
        ) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return original(document, catalog)
            document["lesson_catalog"] = {"mapped_findings": 1}
            return document

        calls = 0
        with patch(
            "p101_lessons.annotate_document",
            side_effect=corrupt_with_original,
        ):
            with self.assertRaises(p101_lessons.LessonCatalogError):
                p101_lessons.protocol_pair(self.catalog, "P101-FD-001")

    def test_progress_handles_invalid_stale_and_legacy_receipts(self) -> None:
        digest = p101_lessons.catalog_digest(self.catalog)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipts = [
                ("invalid", "{"),
                ("wrong-schema", json.dumps({"schema": "old"})),
                (
                    "stale",
                    json.dumps(
                        {
                            "schema": p101_lessons.RECEIPT_SCHEMA,
                            "catalog_sha256": "old",
                        }
                    ),
                ),
                (
                    "not-complete",
                    json.dumps(
                        {
                            "schema": p101_lessons.RECEIPT_SCHEMA,
                            "catalog_sha256": digest,
                            "mode": "quick",
                            "summary": {"result": "FAIL"},
                        }
                    ),
                ),
                (
                    "acceptance-only",
                    json.dumps(
                        {
                            "schema": p101_lessons.RECEIPT_SCHEMA,
                            "catalog_sha256": digest,
                            "mode": "student-verify",
                            "summary": {"result": "PASS"},
                            "acceptance": {
                                "lesson_id": "P101-LAB-ORIENTATION"
                            },
                            "finding_id": "P101-FD-001",
                        }
                    ),
                ),
                (
                    "unknown-lesson",
                    json.dumps(
                        {
                            "schema": p101_lessons.RECEIPT_SCHEMA,
                            "catalog_sha256": digest,
                            "mode": "student-verify",
                            "summary": {"result": "PASS"},
                            "lesson_id": "P101-LAB-NOT-IN-CATALOG",
                            "finding_id": "P101-FD-001",
                        }
                    ),
                ),
            ]
            for name, text in receipts:
                path = root / name / "receipt.json"
                path.parent.mkdir()
                path.write_text(text, encoding="utf-8")
            progress = p101_lessons.progress_document(self.catalog, [root])
            markdown = p101_lessons.progress_markdown(progress)
            self.assertEqual(progress["summary"]["invalid_receipts"], 1)
            self.assertEqual(progress["summary"]["stale_receipts"], 1)
            self.assertIn("P101-LAB-ORIENTATION", markdown)
            self.assertIn("Verified repairs", markdown)
            empty = p101_lessons.progress_document(
                self.catalog, [root / "does-not-exist"]
            )
            empty["available_lesson_ids"] = []
            empty_markdown = p101_lessons.progress_markdown(empty)
            self.assertIn("No lessons are currently available.", empty_markdown)
            self.assertIn("No verified lesson receipts yet.", empty_markdown)

    def test_all_command_handlers_success_and_failure_paths(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(out), redirect_stderr(err):
            root = Path(directory)
            report = root / "report.json"
            clean = root / "clean.json"
            report.write_text(
                json.dumps(
                    {
                        "findings": [
                            {"id": "P101-FD-001"},
                            {"id": "P101-NOT-CATALOGED-999"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            clean.write_text(json.dumps({"findings": []}), encoding="utf-8")
            common = {"catalog": self.catalog_path}

            self.assertEqual(
                p101_lessons.command_show(
                    Namespace(**common, finding_id="P101-FD-001")
                ),
                0,
            )
            self.assertEqual(
                p101_lessons.command_list(Namespace(**common)), 0
            )
            self.assertEqual(
                p101_lessons.command_guide(
                    Namespace(**common, paths=[clean], markdown=False)
                ),
                0,
            )
            self.assertEqual(
                p101_lessons.command_guide(
                    Namespace(**common, paths=[clean], markdown=True)
                ),
                0,
            )
            mapped = root / "mapped.log"
            mapped.write_text("P101-FD-001", encoding="utf-8")
            self.assertEqual(
                p101_lessons.command_guide(
                    Namespace(**common, paths=[mapped], markdown=False)
                ),
                0,
            )
            ignored = root / "ignored.log"
            ignored.write_text("P101-WRAP-000", encoding="utf-8")
            self.assertEqual(
                p101_lessons.command_guide(
                    Namespace(**common, paths=[ignored], markdown=False)
                ),
                1,
            )
            self.assertEqual(
                p101_lessons.command_guide(
                    Namespace(**common, paths=[report], markdown=True)
                ),
                1,
            )
            for as_json, output in (
                (False, None),
                (True, root / "coverage.json"),
            ):
                self.assertEqual(
                    p101_lessons.command_coverage(
                        Namespace(
                            **common,
                            receipts=[],
                            json=as_json,
                            output=output,
                        )
                    ),
                    0,
                )
            exercise = root / "exercise"
            self.assertEqual(
                p101_lessons.command_run(
                    Namespace(
                        **common,
                        finding_id="P101-FD-001",
                        output=exercise,
                        protocol_only=True,
                    )
                ),
                0,
            )
            verify_pass = root / "verify-pass"
            verify_fail = root / "verify-fail"
            self.assertEqual(
                p101_lessons.command_verify_one(
                    Namespace(
                        **common,
                        finding_id="P101-FD-001",
                        paths=[clean],
                        output=verify_pass,
                    )
                ),
                0,
            )
            self.assertEqual(
                p101_lessons.command_verify_one(
                    Namespace(
                        **common,
                        finding_id="P101-FD-001",
                        paths=[report],
                        output=verify_fail,
                    )
                ),
                1,
            )
            self.assertEqual(
                p101_lessons.command_verify_one(
                    Namespace(
                        **common,
                        finding_id="P101-LAB-ORIENTATION",
                        paths=[clean],
                        output=root / "verify-no-finding-id",
                    )
                ),
                2,
            )
            progress = root / "progress.md"
            self.assertEqual(
                p101_lessons.command_progress(
                    Namespace(
                        **common,
                        paths=[verify_pass],
                        json=False,
                        output=progress,
                    )
                ),
                0,
            )
            self.assertEqual(
                p101_lessons.command_progress(
                    Namespace(
                        **common,
                        paths=[root / "missing"],
                        json=True,
                        output=None,
                    )
                ),
                0,
            )

        error = p101_lessons.LessonCatalogError("broken")
        handlers = [
            (
                p101_lessons.command_check,
                Namespace(catalog=self.catalog_path, workspace=Path(".")),
            ),
            (
                p101_lessons.command_show,
                Namespace(catalog=self.catalog_path, finding_id="x"),
            ),
            (
                p101_lessons.command_list,
                Namespace(catalog=self.catalog_path),
            ),
            (
                p101_lessons.command_guide,
                Namespace(catalog=self.catalog_path, paths=[], markdown=False),
            ),
            (
                p101_lessons.command_coverage,
                Namespace(
                    catalog=self.catalog_path,
                    receipts=[],
                    json=False,
                    output=None,
                ),
            ),
            (
                p101_lessons.command_run,
                Namespace(
                    catalog=self.catalog_path,
                    finding_id="x",
                    output=None,
                    protocol_only=True,
                ),
            ),
            (
                p101_lessons.command_verify_one,
                Namespace(
                    catalog=self.catalog_path,
                    finding_id="x",
                    paths=[],
                    output=None,
                ),
            ),
            (
                p101_lessons.command_verify_all,
                Namespace(
                    catalog=self.catalog_path,
                    output=None,
                    quick=False,
                    full=False,
                ),
            ),
            (
                p101_lessons.command_progress,
                Namespace(
                    catalog=self.catalog_path,
                    paths=[],
                    json=False,
                    output=None,
                ),
            ),
        ]
        with redirect_stderr(io.StringIO()):
            for handler, args in handlers:
                with self.subTest(handler=handler.__name__), patch(
                    "p101_lessons.load_catalog", side_effect=error
                ):
                    self.assertEqual(handler(args), 2)

    def test_check_run_verify_and_acceptance_modes(self) -> None:
        common = {"catalog": self.catalog_path}
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with patch(
                "p101_lessons.validate_coverage", return_value=(set(), set())
            ):
                self.assertEqual(
                    p101_lessons.command_check(
                        Namespace(**common, workspace=self.workspace)
                    ),
                    0,
                )
            for result in (
                ({"P101-MISSING-001"}, set()),
                (set(), {"P101-STALE-001"}),
            ):
                with patch(
                    "p101_lessons.validate_coverage", return_value=result
                ):
                    self.assertEqual(
                        p101_lessons.command_check(
                            Namespace(**common, workspace=self.workspace)
                        ),
                        1,
                    )

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch(
                    "p101_lessons._run_profile",
                    return_value={"label": "profile", "status": "PASS"},
                ), patch(
                    "p101_lessons._run_case",
                    return_value={"label": "case", "status": "PASS"},
                ):
                    self.assertEqual(
                        p101_lessons.command_run(
                            Namespace(
                                **common,
                                finding_id="P101-ERR-004",
                                output=root / "run-profile",
                                protocol_only=False,
                            )
                        ),
                        0,
                    )
                    self.assertEqual(
                        p101_lessons.command_run(
                            Namespace(
                                **common,
                                finding_id="P101-LAB-ORIENTATION",
                                output=root / "run-case",
                                protocol_only=False,
                            )
                        ),
                        0,
                    )
                    self.assertEqual(
                        p101_lessons.command_verify_one(
                            Namespace(
                                **common,
                                finding_id="P101-LAB-ORIENTATION",
                                paths=[],
                                output=root / "verify-case",
                            )
                        ),
                        0,
                    )
                    for mode in ("quick", "full"):
                        self.assertEqual(
                            p101_lessons.command_verify_all(
                                Namespace(
                                    **common,
                                    output=root / mode,
                                    quick=mode == "quick",
                                    full=mode == "full",
                                )
                            ),
                            0,
                        )

                self.assertEqual(
                    p101_lessons.command_run(
                        Namespace(
                            **common,
                            finding_id="P101-FD-001",
                            output=root / "failed-run",
                            protocol_only=True,
                        )
                    ),
                    0,
                )
                with self.assertRaises(p101_lessons.LessonCatalogError):
                    p101_lessons._output_directory(root / "failed-run", "x")

                with patch(
                    "p101_lessons._run_case",
                    return_value={"label": "case", "status": "FAIL"},
                ):
                    self.assertEqual(
                        p101_lessons.command_verify_one(
                            Namespace(
                                **common,
                                finding_id="P101-LAB-ORIENTATION",
                                paths=[],
                                output=root / "verify-failed",
                            )
                        ),
                        1,
                    )
                self.assertEqual(
                    p101_lessons.command_verify_one(
                        Namespace(
                            **common,
                            finding_id="P101-LESSON-ERROR-CONTRACTS",
                            paths=[],
                            output=root / "verify-no-report",
                        )
                    ),
                    2,
                )

    def test_cli_parser_and_main_dispatch(self) -> None:
        commands = [
            ["check"],
            ["show", "P101-FD-001"],
            ["list"],
            ["guide", "report.json"],
            ["run", "P101-FD-001", "--protocol-only"],
            ["verify-one", "P101-FD-001", "report.json"],
            ["verify", "--quick"],
            ["verify", "--full"],
            ["coverage", "-j"],
            ["progress", "-j", "receipts"],
        ]
        for argv in commands:
            with self.subTest(argv=argv):
                args = p101_lessons.parse_arguments(argv)
                self.assertTrue(callable(args.function))
        with patch(
            "p101_lessons.parse_arguments",
            return_value=Namespace(function=lambda _args: 7),
        ):
            self.assertEqual(p101_lessons.main([]), 7)
            with patch.object(sys, "argv", ["p101", "list"]):
                self.assertEqual(p101_lessons.main(), 7)


if __name__ == "__main__":
    unittest.main()
