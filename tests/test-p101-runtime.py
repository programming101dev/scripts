#!/usr/bin/env python3
"""Regression tests for the one-model runtime policy engine."""

from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from p101_runtime import (
    RuntimeModelError,
    analyze_model,
    analyze_sanitizers,
    write_analysis,
)


def node(
    domain: str,
    kind: str,
    sequence: int,
    *,
    pid: int = 1,
    context: int = 1,
    **values: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": f"{domain}:test-run:{pid}:{context}:{sequence}:{kind}",
        "domain": domain,
        "kind": kind,
        "run_id": "test-run",
        "pid": pid,
        "context": context,
        "sequence": sequence,
        "monotonic_ns": sequence * 10,
        "wall_unix_ns": sequence * 100,
        "source": {"file": "student.c", "line": sequence, "function": "demo"},
    }
    value.update(values)
    return value


def location(item: dict[str, object]) -> dict[str, object]:
    return {
        "context": item["context"],
        "sequence": item["sequence"],
        "source": item["source"],
    }


def lifecycle_entry(
    acquired: dict[str, object],
    resource_class: str,
    identity: str,
    *,
    released: dict[str, object] | None = None,
    pid: int | None = None,
    size: int = 0,
) -> dict[str, object]:
    return {
        "pid": acquired["pid"] if pid is None else pid,
        "resource_class": resource_class,
        "identity": identity,
        "size": size,
        "live": released is None,
        "acquired": location(acquired),
        "released": None if released is None else location(released),
    }


def lifecycle_finding(
    kind: str,
    at: dict[str, object],
    resource_class: str,
    identity: str,
    *,
    previous: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "pid": at["pid"],
        "resource_class": resource_class,
        "identity": identity,
        "at": location(at),
        "previous": None if previous is None else location(previous),
    }


def model(
    *nodes: dict[str, object],
    lifecycle: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": "p101-run-model-v1",
        "event_schema": "p101-tool-event-format-v5",
        "identity_policy": "run-pid-context-event-sequence-kind",
        "ordering": "causal-edges-with-per-context-sequence-and-observed-timestamps",
        "summary": {
            "call_nodes": sum(item["domain"] == "call" for item in nodes),
            "resource_nodes": sum(item["domain"] == "resource" for item in nodes),
        },
        "nodes": list(nodes),
        "edges": [],
        "lifecycle": lifecycle or {"entries": [], "findings": []},
    }


class RuntimePolicyTests(unittest.TestCase):
    def test_sanitizer_policy_normalizes_supported_runtime_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stderr = Path(directory) / "stderr.txt"
            stderr.write_text(
                "==7==ERROR: AddressSanitizer: heap-use-after-free on address 0x1\n"
                "    #0 0x1 in use student.c:17:3\n"
                "student.c:21:5: runtime error: signed integer overflow\n"
                "WARNING: ThreadSanitizer: data race\n"
                "    #0 write shared.c:9:2\n"
                "==7==ERROR: LeakSanitizer: detected memory leaks\n",
                encoding="utf-8",
            )

            result = analyze_sanitizers(stderr)

        self.assertEqual(
            [finding.diagnostic_id for finding in result.findings],
            [
                "P101-SAN-001",
                "P101-SAN-003",
                "P101-SAN-004",
                "P101-SAN-002",
            ],
        )
        self.assertEqual(result.findings[0].source["file"], "student.c")
        self.assertEqual(result.findings[0].source["line"], 17)
        self.assertEqual(result.status, 1)

    def test_missing_sanitizer_input_is_tool_trouble(self) -> None:
        result = analyze_sanitizers(Path("/definitely/missing/p101-stderr"))

        self.assertEqual(result.status, 2)
        self.assertIn("cannot read captured stderr", result.trouble[0])

    def test_runtime_rejects_a_cyclic_causal_graph(self) -> None:
        first = node("resource", "fd-open", 1, resource_identity="3")
        second = node("resource", "fd-close", 2, resource_identity="3")
        run_model = model(first, second)
        run_model["edges"] = [
            {"kind": "bad", "from": second["id"], "to": first["id"]}
        ]

        with self.assertRaisesRegex(RuntimeModelError, "causal graph contains a cycle"):
            analyze_model(run_model)

    def test_resource_policy_orders_child_events_after_their_fork(self) -> None:
        parent_open_five = node(
            "resource",
            "fd-open",
            1,
            pid=10,
            resource_class="fd",
            resource_identity="5",
        )
        parent_open_six = node(
            "resource",
            "fd-open",
            2,
            pid=10,
            resource_class="fd",
            resource_identity="6",
        )
        child_close_five = node(
            "resource",
            "fd-close",
            4,
            pid=11,
            resource_class="fd",
            resource_identity="5",
        )
        child_close_six = node(
            "resource",
            "fd-close",
            5,
            pid=11,
            resource_class="fd",
            resource_identity="6",
        )
        fork = node("resource", "fork", 3, pid=10, child_pid=11)
        parent_close_five = node(
            "resource",
            "fd-close",
            6,
            pid=10,
            resource_class="fd",
            resource_identity="5",
        )
        parent_close_six = node(
            "resource",
            "fd-close",
            7,
            pid=10,
            resource_class="fd",
            resource_identity="6",
        )
        run_model = model(
            parent_open_five,
            parent_open_six,
            child_close_five,
            child_close_six,
            fork,
            parent_close_five,
            parent_close_six,
            lifecycle={
                "entries": [
                    lifecycle_entry(
                        parent_open_five, "fd", "5", released=parent_close_five
                    ),
                    lifecycle_entry(
                        parent_open_six, "fd", "6", released=parent_close_six
                    ),
                    lifecycle_entry(
                        fork, "fd", "5", released=child_close_five, pid=11
                    ),
                    lifecycle_entry(
                        fork, "fd", "6", released=child_close_six, pid=11
                    ),
                ],
                "findings": [],
            },
        )
        run_model["edges"] = [
            {
                "kind": "process-child-event",
                "from": fork["id"],
                "to": child_close_five["id"],
            },
            {
                "kind": "process-child-event",
                "from": fork["id"],
                "to": child_close_six["id"],
            },
        ]

        analysis = analyze_model(run_model)

        self.assertEqual(analysis.resource.findings, [])
        child_metrics = next(
            item
            for item in analysis.resource.summary["process_metrics"]
            if item["pid"] == 11
        )
        self.assertEqual(child_metrics["fd_peak"], 2)
        self.assertEqual(child_metrics["fd_live"], 0)

    def test_resource_policy_finds_leak_and_double_close(self) -> None:
        open_three = node(
            "resource",
            "fd-open",
            1,
            resource_class="fd",
            resource_identity="3",
        )
        open_four = node(
            "resource",
            "fd-open",
            2,
            resource_class="fd",
            resource_identity="4",
        )
        close_four = node(
            "resource",
            "fd-close",
            3,
            resource_class="fd",
            resource_identity="4",
        )
        close_four_again = node(
            "resource",
            "fd-close",
            4,
            resource_class="fd",
            resource_identity="4",
        )
        analysis = analyze_model(
            model(
                open_three,
                open_four,
                close_four,
                close_four_again,
                lifecycle={
                    "entries": [
                        lifecycle_entry(open_three, "fd", "3"),
                        lifecycle_entry(
                            open_four, "fd", "4", released=close_four
                        ),
                    ],
                    "findings": [
                        lifecycle_finding("leak", open_three, "fd", "3"),
                        lifecycle_finding(
                            "double-release",
                            close_four_again,
                            "fd",
                            "4",
                            previous=close_four,
                        ),
                    ],
                },
            )
        )
        identifiers = {
            finding.diagnostic_id for finding in analysis.resource.findings
        }
        self.assertEqual(identifiers, {"P101-FD-001", "P101-FD-002"})
        self.assertEqual(analysis.resource.status, 1)

    def test_failed_exec_cancels_inheritance_finding(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "resource",
                    "exec",
                    1,
                    resource_class="fd",
                    resource_identity="3",
                    cloexec=False,
                    target="/missing",
                ),
                node("resource", "exec-fail", 2, target="/missing"),
                lifecycle={"entries": [], "findings": []},
            )
        )
        self.assertNotIn(
            "P101-FD-004",
            [finding.diagnostic_id for finding in analysis.resource.findings],
        )

    def test_resource_policy_exercises_every_generic_lifecycle_finding(self) -> None:
        def resource(
            sequence: int,
            operation: str,
            identity: str,
            related: str = "",
        ) -> dict[str, object]:
            return node(
                "resource",
                "resource",
                sequence,
                operation=operation,
                resource_class="teaching-resource",
                resource_identity=identity,
                related_identity=related,
                metadata="",
                size=1,
            )

        leaked = resource(1, "acquire", "leaked")
        acquired = resource(2, "acquire", "released")
        released = resource(3, "release", "released")
        double_release = resource(4, "release", "released")
        stray_release = resource(5, "release", "unknown")
        bad_replace = resource(6, "replace", "missing", "replacement")
        duplicate_first = resource(7, "acquire", "duplicate")
        duplicate_second = resource(8, "acquire", "duplicate")
        analysis = analyze_model(
            model(
                leaked,
                acquired,
                released,
                double_release,
                stray_release,
                bad_replace,
                duplicate_first,
                duplicate_second,
                lifecycle={
                    "entries": [
                        lifecycle_entry(leaked, "teaching-resource", "leaked"),
                        lifecycle_entry(
                            acquired,
                            "teaching-resource",
                            "released",
                            released=released,
                        ),
                        lifecycle_entry(
                            duplicate_first,
                            "teaching-resource",
                            "duplicate",
                        ),
                    ],
                    "findings": [
                        lifecycle_finding(
                            "leak", leaked, "teaching-resource", "leaked"
                        ),
                        lifecycle_finding(
                            "double-release",
                            double_release,
                            "teaching-resource",
                            "released",
                            previous=released,
                        ),
                        lifecycle_finding(
                            "stray-release",
                            stray_release,
                            "teaching-resource",
                            "unknown",
                        ),
                        lifecycle_finding(
                            "bad-replace",
                            bad_replace,
                            "teaching-resource",
                            "missing",
                        ),
                        lifecycle_finding(
                            "duplicate-acquire",
                            duplicate_second,
                            "teaching-resource",
                            "duplicate",
                            previous=duplicate_first,
                        ),
                    ],
                },
            )
        )
        identifiers = {
            finding.diagnostic_id for finding in analysis.resource.findings
        }
        self.assertEqual(
            identifiers,
            {
                "P101-RESOURCE-001",
                "P101-RESOURCE-002",
                "P101-RESOURCE-003",
                "P101-RESOURCE-004",
                "P101-RESOURCE-005",
            },
        )

    def test_resource_policy_finds_realloc_of_unknown_pointer(self) -> None:
        realloc = node(
            "resource",
            "realloc",
            1,
            resource_class="allocation",
            resource_identity="0x10",
            related_identity="0x20",
            size=32,
        )
        analysis = analyze_model(
            model(
                realloc,
                lifecycle={
                    "entries": [],
                    "findings": [
                        lifecycle_finding(
                            "bad-replace", realloc, "allocation", "0x10"
                        )
                    ],
                },
            )
        )
        self.assertEqual(
            {
                finding.diagnostic_id
                for finding in analysis.resource.findings
            },
            {"P101-ALLOC-004"},
        )

    def test_sync_policy_finds_lock_order_cycle(self) -> None:
        def sync(
            sequence: int,
            operation: str,
            identity: str,
            thread: str,
        ) -> dict[str, object]:
            return node(
                "resource",
                "resource",
                sequence,
                operation=operation,
                resource_class="pthread-mutex-held",
                resource_identity=f"{identity}@{thread}",
                related_identity="",
                metadata=thread,
                size=0,
            )

        analysis = analyze_model(
            model(
                sync(1, "acquire", "A", "thread=one"),
                sync(2, "acquire", "B", "thread=one"),
                sync(3, "release", "B", "thread=one"),
                sync(4, "release", "A", "thread=one"),
                sync(5, "acquire", "B", "thread=two"),
                sync(6, "acquire", "A", "thread=two"),
            )
        )
        self.assertIn(
            "P101-SYNC-001",
            [
                finding.diagnostic_id
                for finding in analysis.synchronization.findings
            ],
        )

    def test_trace_policy_finds_unmatched_and_open_calls(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "call",
                    "call-exit",
                    1,
                    name="p101_close",
                    arguments="-",
                    result="0",
                ),
                node(
                    "call",
                    "call-enter",
                    2,
                    name="p101_open",
                    arguments="-",
                    result="-",
                ),
            )
        )
        identifiers = {
            finding.diagnostic_id for finding in analysis.trace.findings
        }
        self.assertEqual(identifiers, {"P101-TRACE-001", "P101-TRACE-003"})

    def test_trace_policy_finds_mismatched_exit(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "call",
                    "call-enter",
                    1,
                    name="p101_open",
                    arguments="-",
                    result="-",
                ),
                node(
                    "call",
                    "call-exit",
                    2,
                    name="p101_close",
                    arguments="-",
                    result="0",
                ),
            )
        )
        self.assertIn(
            "P101-TRACE-002",
            [finding.diagnostic_id for finding in analysis.trace.findings],
        )

    def test_renderer_writes_every_compatibility_view(self) -> None:
        analysis = analyze_model(model())
        with tempfile.TemporaryDirectory(prefix="p101-runtime-test.") as name:
            output = Path(name)
            write_analysis(output, analysis)
            expected = {
                "resource-report.txt",
                "resource-report.json",
                "concurrency-report.txt",
                "concurrency-report.json",
                "trace-tree.txt",
                "trace-summary.txt",
                "sanitizer-report.txt",
                "sanitizer-report.json",
                "correlated-report.txt",
                "correlated-report.json",
                "resource-lifetimes.md",
            }
            self.assertEqual(
                expected,
                {path.name for path in output.iterdir() if path.is_file()},
            )


if __name__ == "__main__":
    unittest.main()
