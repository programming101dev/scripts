#!/usr/bin/env python3
"""Snapshot and compare the governed p101 public API manifests."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


SCHEMA = "p101-public-api-snapshot-v2"


def snapshot(root: Path) -> dict:
    libraries = root / "libraries"
    if not libraries.is_dir():
        raise ValueError(f"{root} has no libraries directory")
    records: list[dict[str, str]] = []
    for manifest in sorted(libraries.glob("lib_*/api-manifest.tsv")):
        with manifest.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if (
                reader.fieldnames is None
                or "function" not in reader.fieldnames
                or "function_usr" not in reader.fieldnames
            ):
                raise ValueError(f"{manifest} has no function identity columns")
            for row in reader:
                name = (row.get("function") or "").strip()
                usr = (row.get("function_usr") or "").strip()
                if not name or not usr:
                    continue
                records.append(
                    {
                        "function": name,
                        "function_usr": usr,
                        "library": manifest.parent.name,
                        "provenance": (row.get("provenance") or "").strip(),
                        "header": (row.get("current_header") or "").strip(),
                        "linux": (row.get("linux") or "").strip(),
                        "macos": (row.get("macos") or "").strip(),
                        "freebsd": (row.get("freebsd") or "").strip(),
                    }
                )
    if not records:
        raise ValueError("no public API records were found")
    seen: set[str] = set()
    duplicate_identities: set[str] = set()
    for record in records:
        if record["function_usr"] in seen:
            duplicate_identities.add(record["function_usr"])
        seen.add(record["function_usr"])
    if duplicate_identities:
        raise ValueError(
            "public API identities are not unique: "
            + ", ".join(sorted(duplicate_identities))
        )
    return {
        "schema": SCHEMA,
        "records": sorted(records, key=lambda item: item["function_usr"]),
        "summary": {
            "functions": len(records),
            "libraries": len({record["library"] for record in records}),
        },
        "blind_spots": [
            "manifest comparison detects governed surface changes, not C ABI layout",
            "struct layout and calling-convention checks require platform ABI tooling",
        ],
    }


def load(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError(f"{path} does not use {SCHEMA}")
    if not isinstance(document.get("records"), list):
        raise ValueError(f"{path} has no records array")
    return document


def compare(old: dict, new: dict) -> dict:
    old_records = {item["function_usr"]: item for item in old["records"]}
    new_records = {item["function_usr"]: item for item in new["records"]}
    findings: list[dict] = []

    def add(identifier: str, function: str, message: str, **evidence: str) -> None:
        findings.append(
            {
                "id": identifier,
                "severity": "error",
                "location": {
                    "file": evidence.get("new_header")
                    or evidence.get("old_header")
                    or "api-manifest.tsv",
                    "line": 0,
                    "function": function,
                },
                "message": message,
                "evidence": evidence,
            }
        )

    for usr, previous in sorted(old_records.items()):
        name = previous["function"]
        current = new_records.get(usr)
        if current is None:
            add(
                "P101-API-001",
                name,
                "public API was removed",
                old_library=previous["library"],
                old_header=previous["header"],
            )
            continue
        if previous["library"] != current["library"]:
            add(
                "P101-API-002",
                name,
                "public API moved to another link library",
                old_library=previous["library"],
                new_library=current["library"],
                old_header=previous["header"],
                new_header=current["header"],
            )
        elif previous["header"] and previous["header"] != current["header"]:
            add(
                "P101-API-003",
                name,
                "public API moved to another header",
                library=current["library"],
                old_header=previous["header"],
                new_header=current["header"],
            )
        for platform_name in ("linux", "macos", "freebsd"):
            if (
                previous.get(platform_name) == "yes"
                and current.get(platform_name) != "yes"
            ):
                add(
                    "P101-API-004",
                    name,
                    f"public API lost {platform_name} support",
                    library=current["library"],
                    platform=platform_name,
                    old_header=previous["header"],
                    new_header=current["header"],
                )
    additions = [
        new_records[usr]["function"]
        for usr in sorted(set(new_records) - set(old_records))
    ]
    return {
        "schema": "p101-public-api-diff-v1",
        "findings": findings,
        "additions": additions,
        "summary": {
            "old_functions": len(old_records),
            "new_functions": len(new_records),
            "additions": len(additions),
            "breaking_changes": len(findings),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot or compare governed p101 public API manifests."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("workspace", type=Path)
    snapshot_parser.add_argument("-o", "--output", type=Path, required=True)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("old", type=Path)
    compare_parser.add_argument("new", type=Path)
    compare_parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "snapshot":
            report = snapshot(args.workspace.resolve())
        else:
            report = compare(load(args.old), load(args.new))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError) as error:
        print(f"p101 api diff: {error}", file=sys.stderr)
        return 2
    if args.operation == "snapshot":
        print(
            f"p101 API snapshot: {report['summary']['functions']} functions, "
            f"{report['summary']['libraries']} libraries"
        )
        return 0
    print(
        f"p101 API diff: {report['summary']['additions']} additions, "
        f"{report['summary']['breaking_changes']} breaking changes"
    )
    for finding in report["findings"]:
        print(
            f"{finding['id']}: {finding['location']['function']}: "
            f"{finding['message']}"
        )
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
