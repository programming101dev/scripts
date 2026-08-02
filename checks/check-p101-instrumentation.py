#!/usr/bin/env python3
"""Verify wrapper instrumentation through Clang-derived coverage records.

Admitted inputs are active library translation units, their compile databases,
and instrumentation-contract.json. The output is a deterministic text report.
The check cannot cover inactive platform translation units or third-party code.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import tempfile
from pathlib import Path


def compile_database(repo: Path) -> Path | None:
    marker = repo / ".last-build-dir"
    candidates: list[Path] = []
    if marker.is_file():
        candidates.append(repo / marker.read_text(encoding="utf-8").strip() / "compile_commands.json")
    candidates.extend(sorted(repo.glob("build-*/compile_commands.json")))
    candidates.append(repo / "build" / "compile_commands.json")
    return next((path for path in candidates if path.is_file()), None)


def api_manifest_functions(repo: Path) -> dict[str, str]:
    path = repo / "api-manifest.tsv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as stream:
        return {
            row["function"]: row.get("current_source", "")
            for row in csv.DictReader(stream, delimiter="\t")
            if row.get("function", "")
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit p101 tracing, fault, and resource instrumentation coverage.")
    scripts_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--workspace", type=Path, default=scripts_root.parent)
    parser.add_argument("--contract", type=Path, default=scripts_root / "contracts" / "instrumentation-contract.json")
    parser.add_argument("--receipt", type=Path, help="write a machine-readable receipt for this platform")
    parser.add_argument("--merge-receipts", nargs="+", type=Path, help="verify and merge platform receipts instead of auditing this host")
    parser.add_argument("--require-platform", action="append", default=[], help="platform name required by --merge-receipts")
    args = parser.parse_args()

    if args.merge_receipts:
        return merge_receipts(args.merge_receipts, set(args.require_platform))
    if args.require_platform:
        parser.error("--require-platform requires --merge-receipts")

    workspace = args.workspace.resolve()
    audit = workspace / "programs" / "p101-wrapper-audit" / "p101-wrapper-audit"
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != "p101-instrumentation-contract-v2":
        print("FAIL: instrumentation contract must use schema p101-instrumentation-contract-v2")
        return 2

    required: dict[str, list[str]] = contract["required"]
    library_roles: dict[str, str] = contract["library_roles"]
    observed: dict[str, list[dict[str, object]]] = {}
    failures: list[str] = []
    valid_roles = {"native-wrapper", "traced-api", "infrastructure"}
    libraries = {
        name: workspace / "libraries" / name
        for name in sorted(library_roles)
        if (workspace / "libraries" / name / "src").is_dir()
    }

    for name, role in sorted(library_roles.items()):
        if role not in valid_roles:
            failures.append(f"contract: {name} has unknown library role {role}")
    for name in sorted(library_roles.keys() - libraries.keys()):
        failures.append(f"contract: classified library {name} does not exist")

    with tempfile.TemporaryDirectory(prefix="p101-instrumentation-") as temp:
        for name, repo in sorted(libraries.items()):
            role = library_roles.get(name)
            if role == "infrastructure" or role not in valid_roles:
                continue
            database = compile_database(repo)
            if database is None:
                failures.append(f"{repo.name}: no compile database")
                continue
            output = Path(temp) / f"{repo.name}.json"
            command = [
                str(audit),
                "--compile-db",
                str(database),
                "--compile-db-only",
                "--instrumentation-output",
                str(output),
            ]
            allow = repo / ".p101-wrapper-audit-allow"
            if allow.is_file():
                command.extend(["--allow-file", str(allow)])
            command.append(str(repo / "src"))
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
            if result.returncode > 1 or not output.is_file():
                failures.append(f"{repo.name}: coverage extraction failed: {result.stderr.strip()}")
                continue
            for record in json.loads(output.read_text(encoding="utf-8"))["functions"]:
                if not bool(record.get("public")):
                    continue
                record["library"] = name
                record["library_role"] = role
                observed.setdefault(str(record["function"]), []).append(record)

    for library, repo in sorted(libraries.items()):
        if library_roles.get(library) == "infrastructure":
            continue
        manifest = api_manifest_functions(repo)
        if not manifest:
            failures.append(f"{library}: missing or empty api-manifest.tsv")
            continue
        expected = set(manifest)
        actual = {
            name
            for name, records in observed.items()
            if any(record["library"] == library for record in records)
        }
        for name in sorted(actual - expected):
            failures.append(f"{library}:{name}: public API is absent from api-manifest.tsv")
        for name in sorted(expected - actual):
            failures.append(f"{library}:{name}: API manifest entry has no public definition on this platform")
        for name in sorted(expected & actual):
            expected_path = str((workspace / manifest[name]).resolve())
            actual_paths = {
                str(record["path"])
                for record in observed[name]
                if record["library"] == library
            }
            if expected_path not in actual_paths:
                failures.append(
                    f"{library}:{name}: manifest source {manifest[name]!r} "
                    f"does not match {', '.join(sorted(actual_paths))}"
                )

    for name, records in sorted(observed.items()):
        for record in records:
            if record["has_env"] and (not record["trace_entry"] or not record["trace_exit"]):
                failures.append(f"{record['path']}:{record['line']}: {name} lacks balanced entry/exit tracing")
            if record["library_role"] == "native-wrapper" and record["has_error"] and not record["fault"]:
                failures.append(f"{record['path']}:{record['line']}: {name} has an error contract but no fault-injection point")

    for name, capabilities in sorted(required.items()):
        records = observed.get(name, [])
        if not records:
            failures.append(f"contract: required wrapper {name} was not found")
            continue
        if any(record["library_role"] != "native-wrapper" for record in records):
            failures.append(f"contract: required wrapper {name} is not owned by a native-wrapper library")
        for capability in capabilities:
            if capability not in {"fault", "fd", "allocation", "resource"}:
                failures.append(f"contract: {name} names unknown capability {capability}")
                continue
            for record in records:
                if not bool(record.get(capability)):
                    failures.append(
                        f"{record['path']}:{record['line']}: contract requires {name} to provide {capability} instrumentation"
                    )

    print(f"instrumented wrapper functions: {sum(len(items) for items in observed.values())}")
    print(f"classified libraries: {len(library_roles)}")
    print(f"explicit capability contracts: {len(required)}")
    receipt = {
        "schema": "p101-instrumentation-platform-receipt-v1",
        "platform": platform.system(),
        "machine": platform.machine(),
        "contract_sha256": hashlib.sha256(args.contract.read_bytes()).hexdigest(),
        "classified_libraries": sorted(library_roles),
        "functions": sorted(
            {
                f"{record['library']}:{record['path']}:{record['line']}:{record['function']}"
                for records in observed.values()
                for record in records
            }
        ),
        "function_capabilities": sorted(
            (
                {
                    "library": record["library"],
                    "function": record["function"],
                    "has_env": bool(record["has_env"]),
                    "has_error": bool(record["has_error"]),
                    "trace_entry": bool(record["trace_entry"]),
                    "trace_exit": bool(record["trace_exit"]),
                    "fault": bool(record["fault"]),
                    "fd": bool(record["fd"]),
                    "allocation": bool(record["allocation"]),
                    "resource": bool(record["resource"]),
                }
                for records in observed.values()
                for record in records
            ),
            key=lambda item: (str(item["library"]), str(item["function"])),
        ),
        "explicit_capability_contracts": len(required),
        "failures": failures,
        "passed": not failures,
    }
    if args.receipt is not None:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("p101 instrumentation coverage passed")
    return 0


def merge_receipts(paths: list[Path], required_platforms: set[str]) -> int:
    receipts: list[dict[str, object]] = []
    failures: list[str] = []

    for path in paths:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path}: unreadable receipt: {exc}")
            continue
        if receipt.get("schema") != "p101-instrumentation-platform-receipt-v1":
            failures.append(f"{path}: unsupported receipt schema")
            continue
        receipts.append(receipt)

    platforms = {str(receipt.get("platform", "")) for receipt in receipts}
    missing = required_platforms - platforms
    if missing:
        failures.append(f"missing required platforms: {', '.join(sorted(missing))}")
    hashes = {str(receipt.get("contract_sha256", "")) for receipt in receipts}
    if len(hashes) > 1:
        failures.append("platform receipts used different instrumentation contracts")
    for receipt in receipts:
        if not bool(receipt.get("passed")):
            failures.append(f"{receipt.get('platform', '?')}: platform instrumentation audit did not pass")

    union_functions = {
        str(function)
        for receipt in receipts
        for function in receipt.get("functions", [])
        if isinstance(receipt.get("functions"), list)
    }
    print(f"platform receipts: {len(receipts)}")
    print(f"platforms covered: {', '.join(sorted(platforms))}")
    print(f"union instrumented functions: {len(union_functions)}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("cross-platform instrumentation receipts passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
