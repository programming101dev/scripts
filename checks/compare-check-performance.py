#!/usr/bin/env python3
"""Compare repeated check-graph receipts without accepting noisy speed claims."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


SCHEMA = "p101-check-graph-receipt-v2"


class PerformanceError(ValueError):
    """The samples cannot support the requested performance claim."""


def load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA:
        raise PerformanceError(f"{path}: unsupported receipt schema")
    if document.get("outcome") != "clean":
        raise PerformanceError(f"{path}: run outcome is not clean")
    return document


def record_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = document.get("records")
    if not isinstance(records, list):
        raise PerformanceError("receipt has no node records")
    return {
        str(record["id"]): record
        for record in records
        if isinstance(record, dict)
        and record.get("outcome") in {"clean", "reused"}
    }


def verify_identity(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> list[str]:
    reference = record_map(baseline[0])
    identifiers = sorted(reference)
    for document in [*baseline, *candidate]:
        records = record_map(document)
        if sorted(records) != identifiers:
            raise PerformanceError("sample node sets differ")
        for identifier in identifiers:
            expected = reference[identifier]
            actual = records[identifier]
            if actual.get("input_identity") != expected.get("input_identity"):
                raise PerformanceError(
                    f"{identifier}: source/workload/tool identity differs"
                )
            if actual.get("result") != expected.get("result"):
                raise PerformanceError(f"{identifier}: result identity differs")
    return identifiers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, action="append", required=True)
    parser.add_argument("--candidate", type=Path, action="append", required=True)
    parser.add_argument("--minimum-samples", type=int, default=5)
    parser.add_argument("--minimum-improvement", type=float, default=0.10)
    arguments = parser.parse_args()
    if (
        arguments.minimum_samples < 3
        or not 0.0 < arguments.minimum_improvement < 1.0
    ):
        raise PerformanceError("invalid performance acceptance policy")
    if (
        len(arguments.baseline) < arguments.minimum_samples
        or len(arguments.candidate) < arguments.minimum_samples
    ):
        raise PerformanceError(
            f"at least {arguments.minimum_samples} baseline and candidate "
            "samples are required"
        )

    baseline = [load(path) for path in arguments.baseline]
    candidate = [load(path) for path in arguments.candidate]
    identifiers = verify_identity(baseline, candidate)
    baseline_ns = statistics.median(
        int(document["elapsed_ns"]) for document in baseline
    )
    candidate_ns = statistics.median(
        int(document["elapsed_ns"]) for document in candidate
    )
    improvement = (
        (baseline_ns - candidate_ns) / baseline_ns
        if baseline_ns > 0
        else 0.0
    )
    print(
        "check performance: "
        f"{len(baseline)} baseline, {len(candidate)} candidate samples, "
        f"{len(identifiers)} identity-matched nodes"
    )
    print(
        f"median: {baseline_ns / 1e9:.3f}s -> "
        f"{candidate_ns / 1e9:.3f}s ({improvement * 100.0:.1f}% faster)"
    )
    if improvement < arguments.minimum_improvement:
        print(
            "FAIL: median improvement is below "
            f"{arguments.minimum_improvement * 100.0:.1f}%"
        )
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, json.JSONDecodeError, PerformanceError) as error:
        print(f"compare-check-performance: {error}")
        raise SystemExit(2) from error
