#!/usr/bin/env python3
"""Apply or verify clang-format over tracked workspace C/C++ sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
VENDORED_ROOTS = {"external", "third_party", "vendor", "vendored"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repositories() -> list[Path]:
    roots = [SCRIPTS_ROOT]
    for raw in (SCRIPTS_ROOT / "repos.txt").read_text(encoding="utf-8").splitlines():
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        fields = text.split("|")
        if len(fields) == 3:
            candidate = (SCRIPTS_ROOT / fields[1]).resolve()
            if candidate.is_dir():
                roots.append(candidate)
    return sorted(set(roots), key=os.fspath)


def tracked_sources(repository: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", os.fspath(repository), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot inventory tracked sources in {repository}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return [
        repository / Path(os.fsdecode(value))
        for value in result.stdout.split(b"\0")
        if value and Path(os.fsdecode(value)).suffix.lower() in EXTENSIONS
    ]


def is_vendored_source(repository: Path, source: Path) -> bool:
    relative = source.relative_to(repository)
    parts = relative.parts
    if parts and parts[0] in VENDORED_ROOTS:
        return True
    return len(parts) >= 2 and parts[0:2] == ("test", "unity")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formatter", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report formatting drift without modifying source files",
    )
    arguments = parser.parse_args()

    version = subprocess.run(
        [arguments.formatter, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    ).stdout.strip()

    tracked = [
        (repository, source)
        for repository in repositories()
        for source in tracked_sources(repository)
        if source.is_file()
    ]
    excluded = [
        source
        for repository, source in tracked
        if is_vendored_source(repository, source)
    ]
    excluded_set = set(excluded)
    sources = [
        source for _repository, source in tracked if source not in excluded_set
    ]
    before = {source: digest(source) for source in sources}
    changed_sources: set[Path] = set()
    formatter_failed = False
    diagnostics: list[str] = []
    for offset in range(0, len(sources), 128):
        batch = sources[offset : offset + 128]
        formatter_arguments = [arguments.formatter]
        if arguments.check:
            formatter_arguments.extend(["--dry-run", "--Werror"])
        else:
            formatter_arguments.append("-i")
        formatter_arguments.extend(
            ["-style=file", *(os.fspath(path) for path in batch)]
        )
        result = subprocess.run(
            formatter_arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            formatter_failed = True
            diagnostics.append(result.stdout)
            diagnostics.append(result.stderr)
            if arguments.check:
                for source in batch:
                    source_text = os.fspath(source)
                    if source_text in result.stdout or source_text in result.stderr:
                        changed_sources.add(source)
            else:
                break
    changed_sources.update(
        source for source in sources if before[source] != digest(source)
    )
    changed = [
        source.relative_to(WORKSPACE).as_posix()
        for source in sources
        if source in changed_sources
    ]
    passed = not formatter_failed and (not arguments.check or not changed)
    receipt = {
        "schema": "p101-format-workspace-receipt-v1",
        "formatter": arguments.formatter,
        "formatter_version": version,
        "mode": "check" if arguments.check else "apply",
        "source_count": len(sources),
        "excluded_vendored_count": len(excluded),
        "excluded_vendored": [
            source.relative_to(WORKSPACE).as_posix() for source in excluded
        ],
        "changed_count": len(changed),
        "changed": changed,
        "passed": passed,
        "does_not_prove": (
            "A clean formatting pass proves only that tracked C/C++ source bytes "
            "match the recorded clang-format version and repository style "
            "files. A different version may format the same sources "
            "differently."
        ),
    }
    arguments.receipt.parent.mkdir(parents=True, exist_ok=True)
    arguments.receipt.write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    if formatter_failed:
        for diagnostic in diagnostics:
            print(diagnostic, end="")
        if arguments.check:
            print("clang-format found formatting drift in tracked source files:")
        else:
            print("clang-format failed while formatting tracked source files:")
        for path in changed:
            print(f"  {path}")
        if arguments.check:
            print("Run update-all.sh locally to apply the required formatting.")
        return 1
    if changed:
        print("clang-format updated tracked source files:")
        for path in changed:
            print(f"  {path}")
    print(
        f"workspace formatting ({'check' if arguments.check else 'apply'}): "
        f"{len(sources)} first-party tracked C/C++ files; "
        f"{len(changed)} changed; {len(excluded)} vendored files excluded"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
