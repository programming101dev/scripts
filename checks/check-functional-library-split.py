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


def validate_functional_layout(repo: Path, domain: str) -> list[str]:
    """Return violations of the one-header/one-source domain layout."""
    failures: list[str] = []
    expected_source = repo / "src" / f"{domain}.c"
    expected_header = repo / "include" / f"p101_{domain}" / f"{domain}.h"
    implementation_sources = sorted(
        path
        for path in (repo / "src").rglob("*.c")
        if path.is_file()
    )
    public_headers = sorted(
        path
        for path in (repo / "include").rglob("*.h")
        if path.is_file()
    )
    if implementation_sources != [expected_source]:
        rendered = ", ".join(
            str(path.relative_to(repo)) for path in implementation_sources
        ) or "<none>"
        failures.append(
            f"{repo.name}: expected only src/{domain}.c; found {rendered}"
        )
    if public_headers != [expected_header]:
        rendered = ", ".join(
            str(path.relative_to(repo)) for path in public_headers
        ) or "<none>"
        failures.append(
            f"{repo.name}: expected only include/p101_{domain}/{domain}.h; "
            f"found {rendered}"
        )
    for origin in ("posix", "posix_xsi", "posix_optional", "unix"):
        if (repo / "src" / origin).exists():
            failures.append(
                f"{repo.name}: obsolete implementation origin directory src/{origin}"
            )
    config = repo / "config.cmake"
    if not config.is_file():
        failures.append(f"{repo.name}: missing config.cmake")
        return failures
    config_text = config.read_text(encoding="utf-8")
    for variable, expected in (
        (f"p101_{domain}_SOURCES", f"src/{domain}.c"),
        (
            f"p101_{domain}_HEADERS",
            f"include/p101_{domain}/{domain}.h",
        ),
    ):
        match = re.search(
            rf"set\({re.escape(variable)}\s+(.*?)\)",
            config_text,
            re.DOTALL,
        )
        values = re.findall(r"[^\s()]+", match.group(1)) if match else []
        if values != [expected]:
            failures.append(
                f"{repo.name}: {variable} must contain only {expected}"
            )
    return failures


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
    central: dict[str, tuple[str, str, str, str]] = {}
    for row in central_rows:
        usr = row["function_usr"]
        if usr in central:
            failures.append(f"central manifest duplicates {usr}")
            continue
        central[usr] = (
            row["function"],
            row["domain"],
            row["current_source"],
            row["current_header"],
        )

    local: dict[str, str] = {}
    for domain in DOMAINS:
        repo = WORKSPACE / "libraries" / f"lib_{domain}"
        manifest = repo / "api-manifest.tsv"
        failures.extend(validate_functional_layout(repo, domain))
        if not manifest.is_file():
            failures.append(f"{repo.name}: missing api-manifest.tsv")
            continue
        for row in table(manifest):
            name = row["function"]
            usr = row["function_usr"]
            if usr in local:
                failures.append(f"{usr}: owned by both {local[usr]} and {domain}")
            local[usr] = domain
            if any(row[platform] != "yes" for platform in ("linux", "macos", "freebsd")):
                failures.append(f"{name}: not admitted on all three platforms")
            expected_current_source = (
                f"libraries/lib_{domain}/src/{domain}.c"
            )
            expected_current_header = (
                f"libraries/lib_{domain}/include/p101_{domain}/{domain}.h"
            )
            if row["current_source"] != expected_current_source:
                failures.append(
                    f"{name}: current_source is not the domain implementation "
                    f"{expected_current_source}"
                )
            if row["current_header"] != expected_current_header:
                failures.append(
                    f"{name}: current_header is not the domain interface "
                    f"{expected_current_header}"
                )
            for field in ("current_source", "current_header"):
                path = WORKSPACE / row[field]
                if not path.is_file():
                    failures.append(f"{name}: missing {field} {row[field]}")
            expected_row = central.get(usr)
            actual_row = (
                name,
                domain,
                row["current_source"],
                row["current_header"],
            )
            if expected_row != actual_row:
                failures.append(f"{name} ({usr}): central/per-library ownership drift")

    for usr in sorted(central.keys() - local.keys()):
        failures.append(f"{usr}: central ownership has no per-library row")
    for usr in sorted(local.keys() - central.keys()):
        failures.append(f"{usr}: per-library row is absent from central ownership")

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
