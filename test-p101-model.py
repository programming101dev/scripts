#!/usr/bin/env python3
"""Regression tests for p101 verify and p101 compare."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from p101_receipt import fingerprint_fields, fingerprint_file


SCRIPT = Path(__file__).resolve().with_name("p101-model.py")
SPEC = importlib.util.spec_from_file_location("p101_model", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


class ModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="p101-model-test.")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_analysis(self, name: str, finding_id: str | None = None) -> Path:
        directory = self.root / name
        directory.mkdir()
        model = {
            "schema": "p101-run-model-v1",
            "event_schema": "p101-tool-event-format-v4",
            "identity_policy": "pid-context-event-sequence-kind",
            "ordering": "causal-edges-with-per-context-sequence-and-observed-timestamps",
            "summary": {"call_nodes": 2, "resource_nodes": 0},
            "nodes": [
                {
                    "id": "call:1:1:1:call-enter",
                    "domain": "call",
                    "kind": "call-enter",
                    "pid": 1,
                    "context": 1,
                    "sequence": 1,
                    "name": "p101_demo",
                    "arguments": "value=1",
                    "result": "-",
                    "monotonic_ns": 100,
                    "wall_unix_ns": None,
                    "source": {"file": "student.c", "line": 7, "function": "demo"},
                },
                {
                    "id": "call:1:1:2:call-exit",
                    "domain": "call",
                    "kind": "call-exit",
                    "pid": 1,
                    "context": 1,
                    "sequence": 2,
                    "name": "p101_demo",
                    "arguments": "-",
                    "result": "0",
                    "monotonic_ns": 200,
                    "wall_unix_ns": None,
                    "source": {"file": "student.c", "line": 7, "function": "demo"},
                },
            ],
            "edges": [
                {
                    "kind": "call-return",
                    "from": "call:1:1:1:call-enter",
                    "to": "call:1:1:2:call-exit",
                }
            ],
        }
        (directory / "run-model.json").write_text(
            json.dumps(model) + "\n", encoding="utf-8"
        )
        finding = (
            [
                {
                    "id": finding_id,
                    "location": {
                        "file": "student.c",
                        "line": 7,
                        "function": "demo",
                    },
                    "kind": "leak",
                    "lesson": {
                        "primary": {
                            "lesson_id": "P101-LAB-101",
                            "title": "Descriptor leak",
                            "url": "https://example.test/fd-leak",
                        },
                        "related": [],
                    },
                }
            ]
            if finding_id
            else []
        )
        schemas = {
            "correlated-report.json": "p101-analysis-findings-v1",
            "resource-report.json": "p101-resource-policy-findings-v1",
            "concurrency-report.json": "p101-sync-check-findings-v1",
        }
        for filename, schema in schemas.items():
            (directory / filename).write_text(
                json.dumps(
                    {
                        "schema": schema,
                        "findings": finding if filename.startswith("correlated") else [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        for filename in set(MODEL.ANALYSIS_FILES.values()) - {
            "run-model.json",
            *schemas.keys(),
        }:
            (directory / filename).write_text(f"{filename}\n", encoding="utf-8")
        result = "findings" if finding_id else "clean"
        receipt = [
            "p101 analysis receipt",
            "schema=p101-analysis-receipt-v1",
            "capture_verification=verified",
            "fingerprint=fnv1a64",
            "fingerprint_security=change-detection-only",
        ]
        for role in ("resources", "calls"):
            receipt.append(
                fingerprint_fields(
                    "input", role, fingerprint_file(directory / "run-model.json")
                )
            )
        for role in sorted(MODEL.ANALYSIS_TOOL_ROLES):
            receipt.append(
                f'tool={role}\tpath_json="/tmp/{role}"'
                "\tversion=binary-fnv1a64:0000000000000000\tbytes=1"
            )
        for role in sorted(MODEL.ANALYSIS_STATUS_ROLES):
            status = 1 if finding_id and role == "report_renderer" else 0
            receipt.append(f"status={role}\texit={status}")
        for role, filename in MODEL.ANALYSIS_FILES.items():
            receipt.append(
                fingerprint_fields(
                    "artifact", role, fingerprint_file(directory / filename)
                )
            )
        receipt.extend([f"result={result}", ""])
        (directory / "analysis-receipt.txt").write_text(
            "\n".join(receipt), encoding="utf-8"
        )
        return directory

    def test_verify_accepts_graph_and_expectations(self) -> None:
        analysis = self.make_analysis("clean")
        expectations = self.root / "expect.txt"
        expectations.write_text(
            "p101-expectations-v1\n"
            "result=clean\n"
            "finding_count=0\n"
            "forbid=P101-*\n"
            "require_edge=call-return\n"
            "require_call=p101_*\n"
            "forbid_call=malloc\n"
            "min_edges=call-return:1\n"
            "min_nodes=2\n",
            encoding="utf-8",
        )
        self.assertEqual(MODEL.verify(analysis, expectations), 0)

    def test_verify_rejects_broken_edge(self) -> None:
        analysis = self.make_analysis("broken")
        model_path = analysis / "run-model.json"
        model = json.loads(model_path.read_text(encoding="utf-8"))
        model["edges"][0]["to"] = "missing"
        model_path.write_text(json.dumps(model), encoding="utf-8")
        with self.assertRaises(MODEL.ModelError):
            MODEL.validate_model(analysis)

    def test_verify_rejects_artifact_changed_after_analysis(self) -> None:
        analysis = self.make_analysis("changed")
        with (analysis / "correlated-report.json").open("a", encoding="utf-8") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(MODEL.ModelError, "fingerprint mismatch"):
            MODEL.receipt_result(analysis)

    def test_compare_uses_semantic_finding_identity(self) -> None:
        before = self.make_analysis("before")
        after = self.make_analysis("after", "P101-FD-001")
        self.assertEqual(MODEL.compare(before, after), 1)
        self.assertEqual(MODEL.compare(after, after), 0)

    def test_rule_pack_gates_findings(self) -> None:
        clean = self.make_analysis("rule-clean")
        finding = self.make_analysis("rule-finding", "P101-FD-001")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(MODEL.check_rules(clean, ["secure-c"], False), 0)
            self.assertEqual(MODEL.check_rules(finding, ["secure-c"], False), 1)

    def test_rule_pack_json_is_machine_readable(self) -> None:
        finding = self.make_analysis("rule-json", "P101-FD-001")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(MODEL.check_rules(finding, ["resource-clean"], True), 1)
        document = json.loads(output.getvalue())
        self.assertEqual(document["schema"], "p101-rule-check-v1")
        self.assertEqual(
            document["violations"][0]["id"], "P101-POLICY-RESOURCE-001"
        )

    def test_rule_pack_size_is_bounded(self) -> None:
        path = self.root / "oversized.json"
        path.write_text(
            " " * (MODEL.MAX_RULE_PACK_BYTES + 1), encoding="utf-8"
        )
        with self.assertRaisesRegex(MODEL.ModelError, "byte safety limit"):
            MODEL.load_rule_pack(str(path))

    def test_explain_walks_causal_neighborhood(self) -> None:
        finding = self.make_analysis("explain", "P101-FD-001")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(MODEL.explain(finding, "P101-FD-001"), 0)
        self.assertIn("call-return", output.getvalue())
        self.assertIn("student.c:7", output.getvalue())
        self.assertIn("P101-LAB-101", output.getvalue())


if __name__ == "__main__":
    unittest.main()
