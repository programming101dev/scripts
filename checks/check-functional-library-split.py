#!/usr/bin/env python3
"""Validate the functional p101 wrapper-library ownership contract."""

from __future__ import annotations

import csv
import re
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
DOMAINS = (
    "io", "filesystem", "memory", "process", "thread", "sync", "ipc",
    "network", "terminal", "time", "identity", "text", "locale", "math",
    "search", "dynamic_linking", "diagnostics", "database", "cli", "random",
    "host",
)
RETIRED = {
    "lib_posix",
    "lib_posix_optional",
    "lib_posix_xsi",
    "lib_unix",
}
OLD_INCLUDE = re.compile(
    r"[<\"]p101_(?:posix|posix_optional|posix_xsi|unix)/"
)
OLD_TARGET = re.compile(
    r"(?m)^[ \t]*p101_(?:posix|posix_optional|posix_xsi|unix)[ \t]*$"
)


def table(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def active_repository_paths() -> list[Path]:
    active: list[Path] = []
    for raw_line in (SCRIPTS_ROOT / "repos.txt").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) >= 2:
            active.append((SCRIPTS_ROOT / fields[1]).resolve())
    return active


def main() -> int:
    failures: list[str] = []
    active_paths = active_repository_paths()
    active = {
        path.name
        for path in active_paths
        if path.parent.name == "libraries"
    }
    expected = {f"lib_{domain}" for domain in DOMAINS}
    missing = expected - active
    retired_active = RETIRED & active
    if missing:
        failures.append(f"repos.txt lacks functional libraries: {', '.join(sorted(missing))}")
    if retired_active:
        failures.append(f"repos.txt still admits retired libraries: {', '.join(sorted(retired_active))}")

    central_rows = table(SCRIPTS_ROOT / "contracts" / "wrapper-library-map.tsv")
    central: dict[str, tuple[str, str, str]] = {}
    for row in central_rows:
        name = row["function"]
        if name in central:
            failures.append(f"central manifest duplicates {name}")
            continue
        central[name] = (row["domain"], row["current_source"], row["current_header"])

    local: dict[str, str] = {}
    for domain in DOMAINS:
        repo = WORKSPACE / "libraries" / f"lib_{domain}"
        manifest = repo / "api-manifest.tsv"
        if not manifest.is_file():
            failures.append(f"{repo.name}: missing api-manifest.tsv")
            continue
        for row in table(manifest):
            name = row["function"]
            if name in local:
                failures.append(f"{name}: owned by both {local[name]} and {domain}")
            local[name] = domain
            if any(row[platform] != "yes" for platform in ("linux", "macos", "freebsd")):
                failures.append(f"{name}: not admitted on all three platforms")
            for field in ("current_source", "current_header"):
                path = WORKSPACE / row[field]
                if not path.is_file():
                    failures.append(f"{name}: missing {field} {row[field]}")
            expected_row = central.get(name)
            actual_row = (domain, row["current_source"], row["current_header"])
            if expected_row != actual_row:
                failures.append(f"{name}: central/per-library ownership drift")

    for name in sorted(central.keys() - local.keys()):
        failures.append(f"{name}: central ownership has no per-library row")
    for name in sorted(local.keys() - central.keys()):
        failures.append(f"{name}: per-library row is absent from central ownership")

    for root in active_paths:
        if not root.is_dir():
            failures.append(f"active repository is missing: {root.relative_to(WORKSPACE)}")
            continue
        for path in root.rglob("*"):
            if not path.is_file() or ".git" in path.parts or any(part.startswith("build") for part in path.parts):
                continue
            if any(retired in path.parts for retired in RETIRED):
                continue
            if path.name == "api-manifest.tsv" or "design" in path.parts:
                continue
            if path.suffix not in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cmake", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if OLD_INCLUDE.search(text):
                failures.append(f"{path.relative_to(WORKSPACE)}: retired public include")
            if path.name in {"config.cmake", "CMakeLists.txt"} and OLD_TARGET.search(text):
                failures.append(f"{path.relative_to(WORKSPACE)}: retired link target")

    print(f"functional libraries: {len(expected)}")
    print(f"uniquely owned wrappers: {len(local)}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("functional library split passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
