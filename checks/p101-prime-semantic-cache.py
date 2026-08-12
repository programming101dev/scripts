#!/usr/bin/env python3
"""Materialize shared production and test C facts before policy consumers.

Admitted inputs are active C repositories from repos.txt. Production scopes
are primed for every library, program, template, and playground; test scopes
are additionally primed for libraries and programs. The tool publishes cache
evidence only. It does not judge source policy or prove scope completeness.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent

import sys

sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))
from c_facts import CFactError, prime  # noqa: E402


def active_repositories() -> list[tuple[Path, str]]:
    repositories: list[tuple[Path, str]] = []
    for number, raw in enumerate(
        (SCRIPTS_ROOT / "repos.txt").read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("|")
        if len(fields) != 3:
            raise ValueError(f"repos.txt:{number}: malformed repository row")
        relative = fields[1]
        language = fields[2]
        repository = (SCRIPTS_ROOT / relative).resolve()
        if language in {"c", "cxx"} and repository.is_dir():
            repositories.append((repository, relative))
    return repositories


def admitted_paths() -> list[Path]:
    paths: set[Path] = set()
    for repository, relative in active_repositories():
        if not relative.startswith(
            ("../libraries/", "../programs/", "../templates/")
        ) and relative != "../playgrounds":
            continue
        candidates = (
            repository.rglob("*")
            if relative.startswith("../programs/")
            else repository.iterdir()
        )
        for path in candidates:
            if not path.is_dir() or path.name not in {"src", "include", "test"}:
                continue
            relative_parts = path.relative_to(repository).parts
            if any(part.startswith("build") for part in relative_parts):
                continue
            if path.name == "test" and not relative.startswith(
                ("../libraries/", "../programs/")
            ):
                continue
            paths.add(path)
    return sorted(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    arguments = parser.parse_args()
    paths = admitted_paths()
    started = time.monotonic_ns()
    try:
        fact_count = prime(WORKSPACE, paths, cache=arguments.cache.resolve())
    except (CFactError, OSError, ValueError) as error:
        print(f"p101 semantic prime: {error}")
        return 1
    receipt = {
        "schema": "p101-semantic-prime-receipt-v1",
        "scope_count": len(paths),
        "fact_count": fact_count,
        "elapsed_ns": time.monotonic_ns() - started,
        "does_not_prove": (
            "This receipt proves that declared semantic scopes were acquired. "
            "It does not judge source policy or prove undeclared scope coverage."
        ),
    }
    arguments.receipt.parent.mkdir(parents=True, exist_ok=True)
    arguments.receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"p101 semantic prime: {receipt['scope_count']} scopes, "
        f"{receipt['fact_count']} facts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
