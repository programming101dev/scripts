#!/usr/bin/env python3
"""Validate the semantic p101 quality catalog against its existing oracles."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent.resolve()
DEFAULT_CONTRACT = SCRIPTS_ROOT / "contracts" / "p101-quality-contract.json"
GRAPH_PATH = SCRIPTS_ROOT / "contracts" / "p101-check-graph.json"
BOUNDARY_PATH = SCRIPTS_ROOT / "contracts" / "p101-boundaries.json"
REQUIRED_TOP_LEVEL = {
    "schema",
    "does_not_prove",
    "public_surfaces",
    "typed_outcome_sets",
    "typed_outcome_exclusions",
    "audit_responsibilities",
    "boundaries",
    "process_termination",
    "platform_evidence",
    "implementation_oracles",
}
REQUIRED_PLATFORMS = {"freebsd", "linux", "macos"}


class QualityContractError(ValueError):
    """The quality catalog is incomplete or refers to stale evidence."""


def require_text(row: dict[str, Any], key: str, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise QualityContractError(f"{context} has no {key}")
    return value


def workspace_file(relative: str, context: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise QualityContractError(f"{context} has an invalid path")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise QualityContractError(f"{context} path must be workspace-relative")
    try:
        resolved = (WORKSPACE / candidate).resolve(strict=True)
    except OSError as error:
        raise QualityContractError(f"{context} refers to missing path: {relative}") from error
    if resolved != WORKSPACE and WORKSPACE not in resolved.parents:
        raise QualityContractError(f"{context} path escapes the workspace: {relative}")
    if not resolved.is_file():
        raise QualityContractError(f"{context} path is not a file: {relative}")
    return resolved


def read_json(path: Path, context: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QualityContractError(f"cannot read {context}: {error}") from error
    if not isinstance(document, dict):
        raise QualityContractError(f"{context} is not an object")
    return document


def unescape_fact_field(value: str) -> str:
    output: list[str] = []
    index = 0
    escapes = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            replacement = escapes.get(value[index + 1])
            if replacement is not None:
                output.append(replacement)
                index += 2
                continue
        output.append(value[index])
        index += 1
    return "".join(output)


def workspace_relative_fact_path(value: str) -> str | None:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (WORKSPACE / path).resolve()
    try:
        relative = resolved.relative_to(WORKSPACE)
    except ValueError:
        return None
    parts = relative.parts
    if (
        len(parts) < 4
        or parts[0] != "libraries"
        or parts[2] != "include"
    ):
        return None
    return relative.as_posix()


def discover_public_enums(facts_root: Path) -> dict[tuple[str, str], list[str]]:
    if not facts_root.is_dir():
        raise QualityContractError(f"facts root is not a directory: {facts_root}")
    facts_files = sorted(facts_root.rglob("source-facts.tsv"))
    if not facts_files:
        raise QualityContractError(f"facts root has no source-facts.tsv files: {facts_root}")

    declared: set[tuple[str, str]] = set()
    variants: dict[tuple[str, str], list[str]] = {}
    for facts_file in facts_files:
        try:
            lines = facts_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            raise QualityContractError(f"cannot read C facts: {facts_file}: {error}") from error
        for line_number, line in enumerate(lines, 1):
            if not line.startswith("P101FACT\t"):
                continue
            fields = [unescape_fact_field(value) for value in line.split("\t")]
            if len(fields) < 3:
                raise QualityContractError(
                    f"{facts_file}:{line_number}: malformed P101FACT record"
                )
            if fields[1] != "4":
                raise QualityContractError(
                    f"{facts_file}:{line_number}: expected P101FACT v4"
                )
            if fields[2] not in {"ENUM", "ENUMERATOR"}:
                continue
            if len(fields) < 8:
                raise QualityContractError(
                    f"{facts_file}:{line_number}: malformed {fields[2]} fact"
                )
            source = workspace_relative_fact_path(fields[3])
            if source is None:
                continue
            if fields[2] == "ENUM":
                type_name = fields[7]
                if type_name.startswith("p101_"):
                    key = (source, type_name)
                    declared.add(key)
                    variants.setdefault(key, [])
            else:
                if len(fields) < 9:
                    raise QualityContractError(
                        f"{facts_file}:{line_number}: malformed ENUMERATOR fact"
                    )
                type_name = fields[8]
                if type_name.startswith("p101_"):
                    key = (source, type_name)
                    values = variants.setdefault(key, [])
                    if fields[7] not in values:
                        values.append(fields[7])

    undisclosed = set(variants) - declared
    if undisclosed:
        raise QualityContractError(
            f"enumerators have no public enum declaration: {sorted(undisclosed)}"
        )
    empty = {key for key in declared if not variants.get(key)}
    if empty:
        raise QualityContractError(f"public enums have no enumerators: {sorted(empty)}")
    return {key: variants[key] for key in sorted(declared)}


def acquire_public_enum_facts(output_directory: Path) -> Path:
    tool = WORKSPACE / "programs" / "p101-wrapper-audit" / "p101-c-facts"
    if not tool.is_file():
        raise QualityContractError(f"lib_c_facts front end is absent: {tool}")
    include_roots = sorted(
        path.resolve()
        for path in (WORKSPACE / "libraries").glob("lib_*/include")
        if path.is_dir()
    )
    if not include_roots:
        raise QualityContractError("workspace has no public library include roots")
    output_directory.mkdir(parents=True, exist_ok=True)
    facts = output_directory / "source-facts.tsv"
    command = [str(tool)]
    command.extend(f"--cflag=-I{path}" for path in include_roots)
    command.extend(str(path) for path in include_roots)
    try:
        with facts.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                command,
                cwd=WORKSPACE,
                stdout=stream,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
    except OSError as error:
        raise QualityContractError(f"cannot run lib_c_facts discovery: {error}") from error
    if result.returncode != 0:
        diagnostic = result.stderr.strip()
        if diagnostic:
            print(diagnostic)
        raise QualityContractError(
            f"lib_c_facts public-header discovery failed with exit {result.returncode}"
        )
    return output_directory


def graph_oracles(graph: dict[str, Any]) -> set[str]:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise QualityContractError("check graph has no nodes")
    return {
        require_text(node, "id", "check-graph node")
        for node in nodes
        if isinstance(node, dict)
    }


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def platform_name(system: str) -> str:
    return {
        "Darwin": "macos",
        "FreeBSD": "freebsd",
        "Linux": "linux",
    }.get(system, system.lower())


def merge_platform_receipts(
    paths: list[Path], required_platforms: set[str]
) -> dict[str, Any]:
    failures: list[str] = []
    platforms: dict[str, Path] = {}
    stack_identities: set[str] = set()
    graph_identities: set[str] = set()
    for path in paths:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            failures.append(f"{path}: unreadable receipt: {error}")
            continue
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema") != "p101-check-graph-receipt-v2"
        ):
            failures.append(f"{path}: unsupported receipt schema")
            continue
        claimed_digest = receipt.get("receipt_digest")
        unsigned = dict(receipt)
        unsigned.pop("receipt_digest", None)
        if claimed_digest != canonical_sha256(unsigned):
            failures.append(f"{path}: receipt digest mismatch")
            continue
        host = receipt.get("host")
        system = host.get("system") if isinstance(host, dict) else None
        if not isinstance(system, str) or not system:
            failures.append(f"{path}: missing host system")
            continue
        platform = platform_name(system)
        if platform in platforms:
            failures.append(
                f"duplicate platform receipt: {platform} "
                f"({platforms[platform]}, {path})"
            )
            continue
        platforms[platform] = path
        checks = receipt.get("checks")
        if (
            receipt.get("outcome") != "clean"
            or not isinstance(checks, dict)
            or checks.get("attempted") != checks.get("completed")
        ):
            failures.append(f"{path}: governed graph did not complete cleanly")
        stack = receipt.get("stack_contract")
        if not isinstance(stack, dict) or stack.get("valid") is not True:
            failures.append(f"{path}: stack contract is absent or invalid")
        else:
            identity = stack.get("contract_sha256")
            if not isinstance(identity, str) or not identity:
                failures.append(f"{path}: stack contract has no identity")
            else:
                stack_identities.add(identity)
        input_value = receipt.get("input")
        if not isinstance(input_value, dict):
            failures.append(f"{path}: input identity is absent")
        else:
            schema = input_value.get("schema")
            identity = input_value.get("identity")
            if not isinstance(schema, str) or not isinstance(identity, str):
                failures.append(f"{path}: graph identity is invalid")
            else:
                graph_identities.add(f"{schema}:{identity}")

    missing = required_platforms - set(platforms)
    extra = set(platforms) - required_platforms
    if missing:
        failures.append(f"missing required platforms: {', '.join(sorted(missing))}")
    if extra:
        failures.append(f"unexpected platforms: {', '.join(sorted(extra))}")
    if len(stack_identities) > 1:
        failures.append("platform receipts used different stack contracts")
    if len(graph_identities) > 1:
        failures.append("platform receipts used different check graphs")
    if failures:
        raise QualityContractError("; ".join(failures))
    return {
        "platforms": sorted(platforms),
        "stack_contract_sha256": next(iter(stack_identities), ""),
        "graph_identity": next(iter(graph_identities), ""),
        "receipt_count": len(platforms),
    }


def require_oracle(identifier: str, oracles: set[str], context: str) -> None:
    if identifier not in oracles:
        raise QualityContractError(f"{context} names unknown oracle: {identifier}")


def validate(
    document: dict[str, Any],
    discovered_enums: dict[tuple[str, str], list[str]] | None = None,
) -> dict[str, int]:
    if set(document) != REQUIRED_TOP_LEVEL:
        raise QualityContractError("quality contract has unexpected top-level fields")
    if document.get("schema") != "p101-quality-contract-v1":
        raise QualityContractError("unexpected quality-contract schema")
    require_text(document, "does_not_prove", "quality contract")

    graph = read_json(GRAPH_PATH, "check graph")
    oracles = graph_oracles(graph)

    surfaces = document.get("public_surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise QualityContractError("quality contract has no public surfaces")
    surface_ids: set[str] = set()
    for raw in surfaces:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "owner",
            "contract",
            "checker",
            "oracle",
        }:
            raise QualityContractError("public surface has invalid fields")
        identifier = require_text(raw, "id", "public surface")
        if not identifier.startswith("public:") or identifier in surface_ids:
            raise QualityContractError(f"invalid or duplicate public surface: {identifier}")
        surface_ids.add(identifier)
        require_text(raw, "owner", f"public surface {identifier}")
        workspace_file(raw["contract"], f"public surface {identifier} contract")
        workspace_file(raw["checker"], f"public surface {identifier} checker")
        require_oracle(
            require_text(raw, "oracle", f"public surface {identifier}"),
            oracles,
            f"public surface {identifier}",
        )

    refusal_sets = document.get("typed_outcome_sets")
    if not isinstance(refusal_sets, list) or not refusal_sets:
        raise QualityContractError("quality contract has no typed refusal sets")
    refusal_types: set[tuple[str, str]] = set()
    refusal_variant_count = 0
    for raw in refusal_sets:
        if not isinstance(raw, dict) or set(raw) != {
            "source",
            "type",
            "owner",
            "oracle",
            "variants",
        }:
            raise QualityContractError("typed refusal set has invalid fields")
        source_name = require_text(raw, "source", "typed refusal set")
        type_name = require_text(raw, "type", "typed refusal set")
        context = f"typed refusal set {type_name}"
        key = (source_name, type_name)
        if key in refusal_types:
            raise QualityContractError(f"duplicate {context}")
        refusal_types.add(key)
        require_text(raw, "owner", context)
        require_oracle(require_text(raw, "oracle", context), oracles, context)
        variants = raw.get("variants")
        if (
            not isinstance(variants, list)
            or not variants
            or len(variants) != len(set(variants))
            or any(not isinstance(value, str) or not value for value in variants)
        ):
            raise QualityContractError(f"{context} has invalid variants")
        workspace_file(source_name, context)
        if discovered_enums is not None and variants != discovered_enums.get(key):
            raise QualityContractError(
                f"{context} drifted: expected={variants} "
                f"observed={discovered_enums.get(key)}"
            )
        refusal_variant_count += len(variants)

    exclusions = document.get("typed_outcome_exclusions")
    if not isinstance(exclusions, list):
        raise QualityContractError("quality contract has no typed outcome exclusions")
    excluded_types: set[tuple[str, str]] = set()
    for raw in exclusions:
        if not isinstance(raw, dict) or set(raw) != {
            "source",
            "type",
            "owner",
            "reason",
        }:
            raise QualityContractError("typed outcome exclusion has invalid fields")
        source_name = require_text(raw, "source", "typed outcome exclusion")
        type_name = require_text(raw, "type", "typed outcome exclusion")
        context = f"typed outcome exclusion {type_name}"
        key = (source_name, type_name)
        if key in excluded_types or key in refusal_types:
            raise QualityContractError(f"duplicate or conflicting {context}")
        excluded_types.add(key)
        workspace_file(source_name, context)
        require_text(raw, "owner", context)
        require_text(raw, "reason", context)

    if discovered_enums is not None:
        classified = refusal_types | excluded_types
        discovered = set(discovered_enums)
        if classified != discovered:
            raise QualityContractError(
                "public enum classification drift: "
                f"unclassified={sorted(discovered - classified)} "
                f"stale={sorted(classified - discovered)}"
            )

    responsibilities = document.get("audit_responsibilities")
    if not isinstance(responsibilities, list) or not responsibilities:
        raise QualityContractError("quality contract has no audit responsibilities")
    responsibility_ids: set[str] = set()
    delegated_count = 0
    for raw in responsibilities:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "owner",
            "mode",
            "source",
            "marker",
            "oracle",
        }:
            raise QualityContractError("audit responsibility has invalid fields")
        identifier = require_text(raw, "id", "audit responsibility")
        context = f"audit responsibility {identifier}"
        if not identifier.startswith("audit:") or identifier in responsibility_ids:
            raise QualityContractError(f"invalid or duplicate {context}")
        responsibility_ids.add(identifier)
        require_text(raw, "owner", context)
        mode = require_text(raw, "mode", context)
        if mode not in {"local", "delegated"}:
            raise QualityContractError(f"{context} has invalid mode: {mode}")
        delegated_count += mode == "delegated"
        source = workspace_file(
            require_text(raw, "source", context), context
        ).read_text(encoding="utf-8")
        marker = require_text(raw, "marker", context)
        if marker not in source:
            raise QualityContractError(f"{context} marker is absent: {marker}")
        require_oracle(require_text(raw, "oracle", context), oracles, context)

    boundaries = document.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise QualityContractError("quality contract has no boundaries")
    quality_boundary_ids: set[str] = set()
    for raw in boundaries:
        if not isinstance(raw, dict) or set(raw) != {"id", "oracle"}:
            raise QualityContractError("quality boundary has invalid fields")
        identifier = require_text(raw, "id", "quality boundary")
        if identifier in quality_boundary_ids:
            raise QualityContractError(f"duplicate quality boundary: {identifier}")
        quality_boundary_ids.add(identifier)
        require_oracle(
            require_text(raw, "oracle", f"quality boundary {identifier}"),
            oracles,
            f"quality boundary {identifier}",
        )
    boundary_document = read_json(BOUNDARY_PATH, "boundary register")
    registered_boundary_ids = {
        require_text(raw, "id", "registered boundary")
        for raw in boundary_document.get("boundaries", [])
        if isinstance(raw, dict)
    }
    if quality_boundary_ids != registered_boundary_ids:
        raise QualityContractError(
            "quality boundary coverage drift: "
            f"missing={sorted(registered_boundary_ids - quality_boundary_ids)} "
            f"extra={sorted(quality_boundary_ids - registered_boundary_ids)}"
        )

    termination = document.get("process_termination")
    if not isinstance(termination, dict) or set(termination) != {
        "allowed_owner",
        "checker",
        "oracle",
        "policy_source",
        "policy_marker",
    }:
        raise QualityContractError("process-termination policy has invalid fields")
    if require_text(termination, "allowed_owner", "process termination") != "main":
        raise QualityContractError("only main may own process termination")
    workspace_file(termination["checker"], "process termination checker")
    policy = workspace_file(
        termination["policy_source"], "process termination policy"
    ).read_text(encoding="utf-8")
    marker = require_text(termination, "policy_marker", "process termination")
    if marker not in policy:
        raise QualityContractError(f"process termination marker is absent: {marker}")
    require_oracle(
        require_text(termination, "oracle", "process termination"),
        oracles,
        "process termination",
    )

    platform = document.get("platform_evidence")
    if not isinstance(platform, dict) or set(platform) != {
        "required",
        "receipt_schema",
        "producer",
        "oracle",
        "merge_driver",
        "merge_marker",
    }:
        raise QualityContractError("platform evidence has invalid fields")
    required = platform.get("required")
    if (
        not isinstance(required, list)
        or len(required) != len(set(required))
        or set(required) != REQUIRED_PLATFORMS
    ):
        raise QualityContractError("platform evidence must require FreeBSD, Linux, and macOS")
    if (
        require_text(platform, "receipt_schema", "platform evidence")
        != "p101-check-graph-receipt-v2"
    ):
        raise QualityContractError("platform evidence has an unsupported receipt schema")
    workspace_file(platform["producer"], "platform evidence producer")
    driver = workspace_file(
        platform["merge_driver"], "platform evidence merge driver"
    ).read_text(encoding="utf-8")
    merge_marker = require_text(platform, "merge_marker", "platform evidence")
    if merge_marker not in driver:
        raise QualityContractError(
            f"platform evidence merge marker is absent: {merge_marker}"
        )
    require_oracle(
        require_text(platform, "oracle", "platform evidence"),
        oracles,
        "platform evidence",
    )

    implementations = document.get("implementation_oracles")
    if not isinstance(implementations, list) or not implementations:
        raise QualityContractError("quality contract has no implementation oracles")
    kinds: set[str] = set()
    for raw in implementations:
        if not isinstance(raw, dict) or set(raw) != {"kind", "oracle"}:
            raise QualityContractError("implementation oracle has invalid fields")
        kind = require_text(raw, "kind", "implementation oracle")
        if kind in kinds:
            raise QualityContractError(f"duplicate implementation oracle kind: {kind}")
        kinds.add(kind)
        require_oracle(
            require_text(raw, "oracle", f"implementation oracle {kind}"),
            oracles,
            f"implementation oracle {kind}",
        )

    return {
        "public_surfaces": len(surface_ids),
        "typed_outcome_sets": len(refusal_types),
        "typed_outcome_variants": refusal_variant_count,
        "typed_outcome_exclusions": len(excluded_types),
        "audit_responsibilities": len(responsibility_ids),
        "delegated_responsibilities": delegated_count,
        "boundaries": len(quality_boundary_ids),
        "platforms": len(required),
        "implementation_oracles": len(kinds),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument(
        "--facts-root",
        type=Path,
        help="require exhaustive classification against lib_c_facts snapshots",
    )
    parser.add_argument(
        "--discover-workspace",
        type=Path,
        help="acquire all public-header enum facts with lib_c_facts into this directory",
    )
    parser.add_argument(
        "--merge-platform-receipts",
        nargs="+",
        type=Path,
        help="verify clean host-qualified graph receipts as one platform set",
    )
    parser.add_argument(
        "--require-platform",
        action="append",
        default=[],
        help="required normalized platform name for receipt merging",
    )
    arguments = parser.parse_args()
    try:
        if arguments.merge_platform_receipts:
            required = set(arguments.require_platform) or REQUIRED_PLATFORMS
            report = merge_platform_receipts(
                arguments.merge_platform_receipts, required
            )
            print(
                "p101 platform quality receipts: "
                f"{report['receipt_count']} receipts, "
                f"platforms={','.join(report['platforms'])}, "
                f"stack={report['stack_contract_sha256']}"
            )
            return 0
        if arguments.require_platform:
            parser.error("--require-platform requires --merge-platform-receipts")
        document = read_json(arguments.contract, "quality contract")
        if arguments.facts_root is not None and arguments.discover_workspace is not None:
            parser.error("--facts-root and --discover-workspace are mutually exclusive")
        facts_root = arguments.facts_root
        if arguments.discover_workspace is not None:
            facts_root = acquire_public_enum_facts(arguments.discover_workspace)
        discovered = discover_public_enums(facts_root) if facts_root is not None else None
        report = validate(document, discovered)
    except QualityContractError as error:
        print(f"p101-quality-contract: {error}")
        return 1
    print(
        "p101 quality contract: "
        f"{report['public_surfaces']} public surfaces, "
        f"{report['typed_outcome_sets']} typed outcome/refusal sets/"
        f"{report['typed_outcome_variants']} variants, "
        f"{report['typed_outcome_exclusions']} explicitly non-outcome enums, "
        f"{report['audit_responsibilities']} audit responsibilities "
        f"({report['delegated_responsibilities']} delegated), "
        f"{report['boundaries']} boundaries, "
        f"{report['platforms']} platforms, "
        f"{report['implementation_oracles']} implementation oracles"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
