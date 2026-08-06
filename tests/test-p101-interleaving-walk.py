#!/usr/bin/env python3
"""Tests for bounded synchronization interleaving exploration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
PATH = ROOT / "runtime" / "p101-interleaving-walk.py"
SPEC = importlib.util.spec_from_file_location("p101_interleaving_walk", PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load {PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def node(
    identifier: str,
    sequence: int,
    thread: str,
    resource_class: str,
    resource: str,
    operation: str,
) -> dict:
    return {
        "id": identifier,
        "domain": "resource",
        "kind": "resource",
        "pid": 10,
        "context": 1,
        "sequence": sequence,
        "source": {"file": "threads.c", "line": sequence, "function": thread},
        "resource_class": resource_class,
        "resource_identity": resource,
        "related_identity": "",
        "metadata": thread,
        "operation": operation,
        "size": 0,
    }


def main() -> int:
    nodes = [
        node("t1-hold-a", 1, "thread=1", "pthread-mutex-held", "A", "acquire"),
        node("t1-wait-b", 2, "thread=1", "pthread-mutex-wait", "B", "acquire"),
        node("t1-wait-b-done", 3, "thread=1", "pthread-mutex-wait", "B", "release"),
        node("t1-release-a", 4, "thread=1", "pthread-mutex-held", "A", "release"),
        node("t2-hold-b", 5, "thread=2", "pthread-mutex-held", "B", "acquire"),
        node("t2-wait-a", 6, "thread=2", "pthread-mutex-wait", "A", "acquire"),
        node("t2-wait-a-done", 7, "thread=2", "pthread-mutex-wait", "A", "release"),
        node("t2-release-b", 8, "thread=2", "pthread-mutex-held", "B", "release"),
    ]
    model = {
        "schema": "p101-run-model-v1",
        "nodes": nodes,
        "edges": [],
        "lifecycle": {"entries": [], "findings": []},
    }
    report = MODULE.explore(model, 256)
    check(report["summary"]["schedules_explored"] > 1, "multiple schedules required")
    check(report["summary"]["counterexample_schedules"] > 0, "counterexample required")
    check(any(
        finding["id"] == "P101-SYNC-002"
        for case in report["counterexamples"]
        for finding in case["findings"]
    ), "deadlock diagnostic was not emitted")
    print("p101 interleaving walk tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
