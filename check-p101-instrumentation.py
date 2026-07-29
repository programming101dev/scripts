#!/usr/bin/env python3
"""Verify wrapper instrumentation through Clang-derived coverage records.

Admitted inputs are active library translation units, their compile databases,
and instrumentation-contract.json. The output is a deterministic text report.
The check cannot cover inactive platform translation units or third-party code.
"""

from __future__ import annotations

import argparse
import json
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit p101 tracing, fault, and resource instrumentation coverage.")
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name("instrumentation-contract.json"))
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    audit = workspace / "programs" / "p101-wrapper-audit" / "p101-wrapper-audit"
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    required: dict[str, list[str]] = contract["required"]
    observed: dict[str, list[dict[str, object]]] = {}
    failures: list[str] = []
    audited_repos = {"lib_c", "lib_posix", "lib_posix_optional", "lib_posix_xsi", "lib_unix"}

    with tempfile.TemporaryDirectory(prefix="p101-instrumentation-") as temp:
        for repo in sorted((workspace / "libraries").glob("lib_*")):
            if repo.name not in audited_repos or not (repo / "src").is_dir():
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
                observed.setdefault(str(record["function"]), []).append(record)

    for name, records in sorted(observed.items()):
        for record in records:
            if record["has_env"] and (not record["trace_entry"] or not record["trace_exit"]):
                failures.append(f"{record['path']}:{record['line']}: {name} lacks balanced entry/exit tracing")
            if record["has_error"] and not record["fault"]:
                failures.append(f"{record['path']}:{record['line']}: {name} has an error contract but no fault-injection point")

    for name, capabilities in sorted(required.items()):
        records = observed.get(name, [])
        if not records:
            failures.append(f"contract: required wrapper {name} was not found")
            continue
        for capability in capabilities:
            for record in records:
                if not bool(record.get(capability)):
                    failures.append(
                        f"{record['path']}:{record['line']}: contract requires {name} to provide {capability} instrumentation"
                    )

    print(f"instrumented wrapper functions: {sum(len(items) for items in observed.values())}")
    print(f"explicit capability contracts: {len(required)}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("p101 instrumentation coverage passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
