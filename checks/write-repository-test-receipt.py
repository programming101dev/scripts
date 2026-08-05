#!/usr/bin/env python3
"""Turn repository-test worker results into one deterministic JSON receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SCHEMA = "p101-repository-test-receipt-v1"


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    records: list[dict[str, object]] = []
    for path in sorted(arguments.results.glob("*.result")):
        fields = path.read_text(encoding="utf-8").rstrip("\n").split("|")
        if len(fields) != 6:
            raise SystemExit(f"invalid repository-test result: {path}")
        repository, unit, fuzz, test_log, fuzz_log, duration_text = fields
        try:
            duration_seconds = int(duration_text)
        except ValueError as error:
            raise SystemExit(
                f"invalid repository-test duration in {path}: {duration_text!r}"
            ) from error
        records.append(
            {
                "repository": repository,
                "unit": unit,
                "fuzz": fuzz,
                "test_log": test_log,
                "fuzz_log": fuzz_log,
                "duration_seconds": duration_seconds,
            }
        )

    passed = bool(records) and all(
        record["unit"] not in {"FAIL", "MISSING"}
        and record["fuzz"] != "FAIL"
        for record in records
    )
    receipt: dict[str, object] = {
        "schema": SCHEMA,
        "passed": passed,
        "repositories": records,
        "does_not_prove": (
            "A PASS proves only that each repository-owned test launcher admitted "
            "by repos.txt completed in this campaign. It does not prove that an "
            "unregistered test, platform, branch, or third-party dependency ran."
        ),
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    arguments.output.write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
