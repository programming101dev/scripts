#!/usr/bin/env python3
"""Regression tests for replayable p101 capture analysis."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))
ANALYZE_PATH = SCRIPTS_ROOT / "runtime" / "p101-analyze.py"
SPEC = importlib.util.spec_from_file_location("p101_analyze", ANALYZE_PATH)
assert SPEC is not None and SPEC.loader is not None
ANALYZE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ANALYZE
SPEC.loader.exec_module(ANALYZE)


FAKE_TOOL = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name

call_log = os.environ.get("P101_FAKE_CALL_LOG")
if call_log:
    with Path(call_log).open("a") as stream:
        stream.write(name + "\\t" + "\\t".join(sys.argv[1:]) + "\\n")

mutate_path = os.environ.get("P101_FAKE_MUTATE_CAPTURE")
if mutate_path:
    with Path(mutate_path).open("a") as stream:
        stream.write("changed during analysis\\n")

mutate_tool = os.environ.get("P101_FAKE_MUTATE_TOOL")
if mutate_tool:
    with Path(mutate_tool).open("a") as stream:
        stream.write("# changed during analysis\\n")

output = Path(sys.argv[sys.argv.index("-o") + 1])
if os.environ.get("P101_FAKE_FINDING"):
    nodes = [{
        "id": "resource:test-run:1:1:1:fd-open",
        "domain": "resource",
        "kind": "fd-open",
        "run_id": "test-run",
        "pid": 1,
        "context": 1,
        "sequence": 1,
        "resource_class": "fd",
        "resource_identity": "3",
        "monotonic_ns": 1,
        "wall_unix_ns": 1,
        "source": {"file": "student.c", "line": 7, "function": "demo"},
    }]
else:
    nodes = []
lifecycle = {
    "entries": ([{
        "pid": 1,
        "resource_class": "fd",
        "identity": "3",
        "size": 0,
        "live": True,
        "acquired": {
            "context": 1,
            "sequence": 1,
            "source": {"file": "student.c", "line": 7, "function": "demo"},
        },
        "released": None,
    }] if nodes else []),
    "findings": ([{
        "kind": "leak",
        "pid": 1,
        "resource_class": "fd",
        "identity": "3",
        "at": {
            "context": 1,
            "sequence": 1,
            "source": {"file": "student.c", "line": 7, "function": "demo"},
        },
        "previous": None,
    }] if nodes else []),
}
output.write_text(__import__("json").dumps({
    "schema": "p101-run-model-v1",
    "event_schema": "p101-tool-event-format-v5",
    "identity_policy": "run-pid-context-event-sequence-kind",
    "ordering": "causal-edges-with-per-context-sequence-and-observed-timestamps",
    "summary": {"call_nodes": 0, "resource_nodes": len(nodes)},
    "nodes": nodes,
    "edges": [],
    "lifecycle": lifecycle,
}) + "\\n")

print(f"{name} output")
raise SystemExit(int(os.environ.get("P101_FAKE_MODEL_STATUS", "0")))
"""


class AnalyzeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="p101-analyze-test.")
        self.root = Path(self.temporary.name)
        self.capture = self.root / "capture"
        self.capture.mkdir()
        self.tools = {}
        for name in ("fake-model",):
            path = self.root / name
            path.write_text(FAKE_TOOL, encoding="utf-8")
            path.chmod(0o755)
            self.tools[name] = path
        self.write_capture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_capture(
        self,
        *,
        completed: bool = False,
        command_status: int = 0,
        status_kind: str = "exit",
    ) -> None:
        contents = {
            "manifest.txt": "manifest\\n",
            "command.txt": "./student-program\\n",
            "stdout.txt": "hello\\n",
            "stderr.txt": "",
            "resources.log": "",
            "calls.log": "",
            "summary.txt": "captured\\n",
        }
        if completed:
            for role, name in ANALYZE.CAPTURE_FILES.items():
                if name not in contents:
                    contents[name] = f"{role}\\n"
        for name, text in contents.items():
            (self.capture / name).write_text(text, encoding="utf-8")
        lines = [
            "inspect-capture receipt",
            "schema=p101-run-receipt-v1",
            "run_id=test-run",
            "event_schema=p101-tool-event-format-v5",
            "event_log_version=5",
            "ordering=per-context-sequence",
            "durability=buffered-until-close",
            "fingerprint=fnv1a64",
            "fingerprint_security=change-detection-only",
            f"analysis={'completed' if completed else 'deferred'}",
            f"status=command\t{status_kind}={command_status}",
        ]
        if completed:
            for role in sorted(ANALYZE.COMPLETED_STATUS_ROLES - {"command"}):
                lines.append(f"status={role}\texit=0")
            artifact_roles = ANALYZE.CAPTURE_FILES
        else:
            artifact_roles = (
                "manifest",
                "command",
                "stdout",
                "stderr",
                "resources",
                "calls",
                "summary",
            )
        for role in artifact_roles:
            value = ANALYZE.fingerprint_file(
                self.capture / ANALYZE.CAPTURE_FILES[role]
            )
            lines.append(ANALYZE.fingerprint_fields("artifact", role, value))
        lines.extend(
            [
                "does_not_prove=complete instrumentation, external truth, "
                "global process ordering, or cryptographic authenticity",
                "",
            ]
        )
        (self.capture / "receipt.txt").write_text("\n".join(lines), encoding="utf-8")

    def command(self, output: Path, *extra: str) -> list[str]:
        return [
            os.fspath(ANALYZE_PATH),
            "-o",
            os.fspath(output),
            "--model-tool",
            os.fspath(self.tools["fake-model"]),
            *extra,
            os.fspath(self.capture),
        ]

    def run_analyze(
        self, output: Path, *extra: str, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if environment:
            env.update(environment)
        return subprocess.run(
            self.command(output, *extra),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def test_verified_capture_produces_complete_separate_bundle(self) -> None:
        output = self.root / "analysis"
        call_log = self.root / "tool-calls.txt"
        completed = self.run_analyze(
            output, environment={"P101_FAKE_CALL_LOG": os.fspath(call_log)}
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for filename in set(ANALYZE.OUTPUT_FILES.values()) | {
            "analysis-receipt.txt"
        }:
            self.assertTrue((output / filename).is_file(), filename)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("capture_verification=verified\n", receipt)
        self.assertIn("tool=event_model\t", receipt)
        self.assertIn("version=binary-fnv1a64:", receipt)
        self.assertIn("lesson_catalog_path_json=", receipt)
        self.assertIn("lesson_catalog_sha256=", receipt)
        self.assertIn("status=report_renderer\texit=0", receipt)
        self.assertEqual(
            (self.capture / "resources.log").read_text(encoding="utf-8"), ""
        )
        calls = call_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 1)
        model_call = calls[0]
        self.assertTrue(model_call.startswith("fake-model\t"))
        arguments = model_call.split("\t")
        report_input = Path(arguments[arguments.index("-r") + 1]).parent
        self.assertNotEqual(report_input, self.capture)
        self.assertTrue(report_input.name.startswith("p101-analysis-input."))

    def test_modified_capture_is_refused_before_output_is_created(self) -> None:
        output = self.root / "analysis"
        (self.capture / "resources.log").write_text("modified\n", encoding="utf-8")
        completed = self.run_analyze(output)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("fingerprint mismatch", completed.stderr)
        self.assertFalse(output.exists())

    def test_completed_observe_bundle_can_be_reanalyzed(self) -> None:
        self.write_capture(completed=True)
        output = self.root / "analysis"
        completed = self.run_analyze(output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("capture_verification=verified\n", receipt)
        self.assertIn("result=clean\n", receipt)

    def test_force_records_modified_capture_override(self) -> None:
        output = self.root / "analysis"
        (self.capture / "resources.log").write_text("modified\n", encoding="utf-8")
        completed = self.run_analyze(output, "--force")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("capture_verification=overridden\n", receipt)
        self.assertIn("fingerprint mismatch", receipt)
        self.assertIn("result=findings\n", receipt)

    def test_missing_receipt_requires_force(self) -> None:
        (self.capture / "receipt.txt").unlink()
        refused_output = self.root / "refused"
        refused = self.run_analyze(refused_output)
        self.assertEqual(refused.returncode, 2)
        self.assertFalse(refused_output.exists())

        forced_output = self.root / "forced"
        forced = self.run_analyze(forced_output, "--force")
        self.assertEqual(forced.returncode, 1, forced.stderr)
        receipt = (forced_output / "analysis-receipt.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("capture_verification=overridden\n", receipt)
        self.assertIn("result=findings\n", receipt)

    def test_incomplete_receipt_requires_force(self) -> None:
        receipt_path = self.capture / "receipt.txt"
        lines = receipt_path.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if not line.startswith("artifact=calls\t")]
        receipt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        refused_output = self.root / "refused"
        refused = self.run_analyze(refused_output)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("artifact fingerprints are incomplete", refused.stderr)

        forced_output = self.root / "forced"
        forced = self.run_analyze(forced_output, "--force")
        self.assertEqual(forced.returncode, 1, forced.stderr)
        receipt = (forced_output / "analysis-receipt.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("capture_verification=overridden\n", receipt)
        self.assertIn("result=findings\n", receipt)

    def test_failed_captured_command_is_recorded_as_a_finding(self) -> None:
        self.write_capture(command_status=7)
        output = self.root / "analysis"
        completed = self.run_analyze(output)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("status=capture_command\texit=1", receipt)
        self.assertIn("result=findings\n", receipt)

    def test_signaled_captured_command_makes_analysis_trouble(self) -> None:
        self.write_capture(command_status=9, status_kind="signal")
        output = self.root / "analysis"
        completed = self.run_analyze(output)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("status=capture_command\texit=2\tsignal=9", receipt)
        self.assertIn("result=trouble\n", receipt)

    def test_findings_are_preserved_without_becoming_tool_trouble(self) -> None:
        output = self.root / "analysis"
        completed = self.run_analyze(
            output, environment={"P101_FAKE_FINDING": "1"}
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("status=resource_policy\texit=1", receipt)
        self.assertIn("result=findings", receipt)
        report = json.loads(
            (output / "correlated-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["findings"][0]["lesson"]["primary"]["lesson_id"],
            "P101-LAB-101",
        )
        self.assertEqual(report["lesson_catalog"]["unmapped_finding_ids"], [])

    def test_explicit_missing_lesson_catalog_is_trouble(self) -> None:
        output = self.root / "analysis"
        completed = self.run_analyze(
            output,
            "--lesson-catalog",
            os.fspath(self.root / "missing-lessons.json"),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("lesson catalog not found", completed.stderr)
        self.assertFalse(output.exists())

    def test_capture_change_during_analysis_is_reported_as_trouble(self) -> None:
        output = self.root / "analysis"
        completed = self.run_analyze(
            output,
            environment={
                "P101_FAKE_MUTATE_CAPTURE": os.fspath(
                    self.capture / "resources.log"
                )
            },
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("capture_verification=failed-after-analysis\n", receipt)
        self.assertIn("status=capture_stability\texit=2", receipt)
        self.assertIn("result=trouble", receipt)

    def test_tool_change_during_analysis_is_reported_as_trouble(self) -> None:
        output = self.root / "analysis"
        completed = self.run_analyze(
            output,
            environment={
                "P101_FAKE_MUTATE_TOOL": os.fspath(self.tools["fake-model"])
            },
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        receipt = (output / "analysis-receipt.txt").read_text(encoding="utf-8")
        self.assertIn("status=tool_stability\texit=2", receipt)
        self.assertIn("result=trouble", receipt)

    def test_existing_output_is_never_reused(self) -> None:
        output = self.root / "analysis"
        output.mkdir()
        completed = self.run_analyze(output)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("output path already exists", completed.stderr)


if __name__ == "__main__":
    unittest.main()
