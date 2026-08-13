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
from c_facts import CFactError, acquire, prime  # noqa: E402


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
    parser.add_argument(
        "--bundle",
        type=Path,
        help="write the materialized shared fact model for native policy tools",
    )
    arguments = parser.parse_args()
    paths = admitted_paths()
    started = time.monotonic_ns()
    try:
        if arguments.bundle is None:
            facts = None
            fact_count = prime(WORKSPACE, paths, cache=arguments.cache.resolve())
        else:
            facts = acquire(WORKSPACE, paths, cache=arguments.cache.resolve())
            fact_count = len(facts)
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
    if arguments.bundle is not None and facts is not None:
        arguments.bundle.parent.mkdir(parents=True, exist_ok=True)
        function_parameters: dict[str, tuple[bool, bool]] = {}
        for fact in facts:
            if fact.get("kind") != "PARAMETER":
                continue
            caller_usr = str(fact.get("caller_usr") or "")
            canonical_type = str(fact.get("canonical_type") or "")
            if not caller_usr:
                continue
            has_env, has_error = function_parameters.get(
                caller_usr, (False, False)
            )
            has_env = has_env or canonical_type in {
                "const struct p101_env *",
                "struct p101_env *",
            }
            has_error = has_error or canonical_type in {
                "const struct p101_error *",
                "struct p101_error *",
            }
            function_parameters[caller_usr] = has_env, has_error

        def field(value: object) -> str:
            text = "" if value is None else str(value)
            if text == "-":
                return r"\-"
            return (
                text.replace("\\", r"\\")
                .replace("\t", r"\t")
                .replace("\n", r"\n")
                .replace("\r", r"\r")
            )

        with arguments.bundle.open("w", encoding="utf-8") as stream:
            stream.write("P101SEMANTIC\t3\n")
            for fact in facts:
                kind = fact.get("kind")
                if kind not in {
                    "FUNCTION",
                    "CALL",
                    "INCLUDE",
                    "TYPE",
                    "ENUM",
                    "ENUMERATOR",
                    "NOTE",
                }:
                    continue
                value = fact.get("value")
                if kind == "NOTE":
                    note = str(value or "")
                    if not note.startswith(
                        ("CALLEE_SEMANTIC_ROLE:", "SEMANTIC_ROLE:")
                    ) and note not in {
                        "TRACE_USE",
                        "TYPE_SEMANTIC_ROLE:p101:trace-scope",
                        "FUNCTION_RETURN",
                        "FUNCTION_EARLY_RETURN",
                        "CALL_NOT_ISOLATED",
                        "CALL_RESULT_DISCARDED",
                    }:
                        continue
                is_definition = bool(fact.get("is_definition", False))
                if kind == "FUNCTION":
                    is_definition = not bool(
                        fact.get("is_declaration", False)
                    )
                has_env, has_error = function_parameters.get(
                    str(fact.get("usr") or ""), (False, False)
                )
                stream.write(
                    "\t".join(
                        field(value)
                        for value in (
                            kind,
                            fact.get("path"),
                            value,
                            fact.get("usr"),
                            fact.get("caller_usr"),
                            fact.get("resolved"),
                            fact.get("line", 0),
                            value if kind == "MACRO" else None,
                            int(is_definition),
                            fact.get("type"),
                            fact.get("canonical_type"),
                            fact.get("return_type"),
                            fact.get("caller"),
                            fact.get("column", 0),
                            fact.get("start", 0),
                            fact.get("end", 0),
                            fact.get("parameter_index", 0),
                            int(bool(fact.get("is_header", False))),
                            int(bool(fact.get("is_static", False))),
                            int(
                                kind == "FUNCTION"
                                and not bool(fact.get("is_static", False))
                            ),
                            int(bool(fact.get("is_variadic", False))),
                            int(bool(fact.get("is_local", False))),
                            int(bool(fact.get("is_indirect", False))),
                            int(has_env),
                            int(has_error),
                            fact.get("parent_usr"),
                        )
                    )
                    + "\n"
                )
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
