#!/usr/bin/env python3
"""Validation and selection mechanics for the governed p101 check graph."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable


class GraphError(ValueError):
    """The graph is malformed or cannot be selected as requested."""


def _git_paths(repository: Path, arguments: list[str]) -> set[Path]:
    completed = subprocess.run(
        ["git", "-C", os.fspath(repository), *arguments, "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode(
            "utf-8", errors="replace"
        ).strip()
        raise GraphError(
            f"cannot discover changes in {repository}: "
            f"{diagnostic or f'exit {completed.returncode}'}"
        )
    return {
        Path(os.fsdecode(value))
        for value in completed.stdout.split(b"\0")
        if value
    }


def workspace_changed_paths(
    workspace: Path, repositories: Iterable[Path]
) -> set[str]:
    """Return committed-ahead, staged, unstaged, and untracked paths.

    Each repository is compared with its configured upstream. A repository
    without an upstream is admitted conservatively as a whole rather than
    being mistaken for an unchanged tree.
    """
    workspace = workspace.resolve()
    changed: set[str] = set()
    for repository in sorted(
        {path.resolve() for path in repositories}, key=os.fspath
    ):
        try:
            prefix = repository.relative_to(workspace)
        except ValueError as error:
            raise GraphError(
                f"repository escapes workspace: {repository}"
            ) from error
        upstream = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "rev-parse",
                "--verify",
                "--symbolic-full-name",
                "@{u}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if upstream.returncode != 0:
            changed.add(prefix.as_posix())
            continue
        paths = _git_paths(
            repository,
            ["diff", "--name-only", f"{upstream.stdout.strip()}...HEAD"],
        )
        paths.update(
            _git_paths(repository, ["diff", "--name-only", "HEAD"])
        )
        paths.update(
            _git_paths(
                repository,
                ["ls-files", "--others", "--exclude-standard"],
            )
        )
        for path in paths:
            changed.add((prefix / path).as_posix())
    return changed


def require_text(row: dict[str, Any], key: str, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GraphError(f"{context} has no {key}")
    return value


def validate(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema") != "p101-check-graph-v1":
        raise GraphError("unexpected check-graph schema")
    require_text(document, "does_not_prove", "graph")
    default_jobs = document.get("default_jobs")
    if (
        not isinstance(default_jobs, int)
        or isinstance(default_jobs, bool)
        or default_jobs <= 0
    ):
        raise GraphError("default_jobs must be a positive integer")
    capacities = document.get("resource_capacities", {})
    if not isinstance(capacities, dict) or any(
        not isinstance(name, str)
        or not name
        or not isinstance(amount, int)
        or isinstance(amount, bool)
        or amount <= 0
        for name, amount in capacities.items()
    ):
        raise GraphError("resource_capacities must map names to positive integers")
    nodes = document.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise GraphError("graph has no nodes")
    require_complete_inputs = document.get("require_complete_inputs", False)
    if not isinstance(require_complete_inputs, bool):
        raise GraphError("require_complete_inputs must be boolean")

    identifiers: set[str] = set()
    for raw in nodes:
        if not isinstance(raw, dict):
            raise GraphError("graph node is not an object")
        identifier = require_text(raw, "id", "node")
        context = f"node {identifier}"
        if identifier in identifiers:
            raise GraphError(f"duplicate node id: {identifier}")
        identifiers.add(identifier)
        for key in ("title", "group", "guarantee"):
            require_text(raw, key, context)
        if not isinstance(raw.get("required"), bool):
            raise GraphError(f"{context} has no required policy")
        command = raw.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(value, str) or not value for value in command)
        ):
            raise GraphError(f"{context} has an invalid argv command")
        dependencies = raw.get("depends_on")
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str) or not value for value in dependencies
        ):
            raise GraphError(f"{context} has invalid dependencies")
        impact_dependencies = raw.get("impact_dependencies", [])
        if (
            not isinstance(impact_dependencies, list)
            or any(
                not isinstance(value, str) or not value
                for value in impact_dependencies
            )
            or not set(impact_dependencies).issubset(dependencies)
        ):
            raise GraphError(
                f"{context} has invalid impact dependencies"
            )
        if "affected" in raw and not isinstance(raw["affected"], bool):
            raise GraphError(f"{context} affected policy must be boolean")
        if "wait_for_selected" in raw and not isinstance(
            raw["wait_for_selected"], bool
        ):
            raise GraphError(
                f"{context} wait_for_selected policy must be boolean"
            )
        resources = raw.get("resources")
        if (
            not isinstance(resources, dict)
            or not isinstance(resources.get("writes"), list)
            or not isinstance(resources.get("units"), dict)
            or any(
                not isinstance(path, str) or not path
                for path in resources.get("writes", [])
            )
        ):
            raise GraphError(f"{context} has invalid resources")
        for resource, amount in resources["units"].items():
            if resource not in capacities:
                raise GraphError(f"{context} uses unknown resource {resource!r}")
            if (
                not isinstance(amount, int)
                or isinstance(amount, bool)
                or amount <= 0
                or amount > capacities[resource]
            ):
                raise GraphError(f"{context} has invalid {resource!r} resource units")
        if "cacheable" in raw and not isinstance(raw["cacheable"], bool):
            raise GraphError(f"{context} cacheable must be boolean")
        if "replace_outputs" in raw and not isinstance(raw["replace_outputs"], bool):
            raise GraphError(f"{context} replace_outputs must be boolean")
        if "invalidates_source_identity" in raw and not isinstance(
            raw["invalidates_source_identity"], bool
        ):
            raise GraphError(
                f"{context} invalidates_source_identity must be boolean"
            )
        receipts = raw.get("receipts", [])
        if not isinstance(receipts, list) or any(
            not isinstance(path, str) or not path for path in receipts
        ):
            raise GraphError(f"{context} has invalid receipts")
        inputs = raw.get("inputs")
        if inputs is not None and (
            not isinstance(inputs, list)
            or not inputs
            or any(
                not isinstance(pattern, str)
                or not pattern
                or pattern.startswith("/")
                or ".." in pattern.split("/")
                for pattern in inputs
            )
        ):
            raise GraphError(f"{context} has invalid inputs")
        if "inputs_complete" in raw and not isinstance(
            raw["inputs_complete"], bool
        ):
            raise GraphError(f"{context} inputs_complete must be boolean")
        if raw.get("inputs_complete") is True and inputs is None:
            raise GraphError(
                f"{context} claims complete inputs without declaring them"
            )
        if require_complete_inputs and raw.get("inputs_complete") is not True:
            raise GraphError(
                f"{context} lacks a complete admitted-input declaration"
            )
        environment_files = raw.get("environment_files", [])
        if not isinstance(environment_files, list) or any(
            not isinstance(name, str)
            or not name.startswith("P101_")
            or not name.replace("_", "").isalnum()
            for name in environment_files
        ):
            raise GraphError(f"{context} has invalid environment files")

    for node in nodes:
        unknown = set(node["depends_on"]) - identifiers
        if unknown:
            raise GraphError(
                f"node {node['id']} has unknown dependencies: {sorted(unknown)}"
            )

    order = topological_order(nodes)
    coverage = document.get("coverage")
    if not isinstance(coverage, dict):
        raise GraphError("graph has no coverage contract")
    required_nodes = coverage.get("required_nodes")
    if not isinstance(required_nodes, list) or len(required_nodes) != len(
        set(required_nodes)
    ):
        raise GraphError("coverage required_nodes is invalid")
    expected = {node["id"] for node in nodes if node["required"]}
    if set(required_nodes) != expected:
        raise GraphError(
            "required-node coverage drift: "
            f"missing={sorted(expected - set(required_nodes))} "
            f"extra={sorted(set(required_nodes) - expected)}"
        )
    return order


def topological_order(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {node["id"]: node for node in nodes}
    remaining = {
        identifier: set(node["depends_on"]) for identifier, node in by_id.items()
    }
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [
            identifier
            for identifier, dependencies in remaining.items()
            if not dependencies
        ]
        if not ready:
            raise GraphError(f"dependency cycle among: {sorted(remaining)}")
        for identifier in ready:
            ordered.append(by_id[identifier])
            del remaining[identifier]
            for dependencies in remaining.values():
                dependencies.discard(identifier)
    return ordered


def dependency_closure(
    identifiers: Iterable[str], by_id: dict[str, dict[str, Any]]
) -> set[str]:
    selected = set(identifiers)
    pending = list(selected)
    while pending:
        identifier = pending.pop()
        if identifier not in by_id:
            raise GraphError(f"unknown selected node: {identifier}")
        for dependency in by_id[identifier]["depends_on"]:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return selected


def impact_dependency_closure(
    identifiers: Iterable[str], by_id: dict[str, dict[str, Any]]
) -> set[str]:
    """Add only dependencies whose artifacts an affected node consumes."""
    selected = set(identifiers)
    pending = list(selected)
    while pending:
        identifier = pending.pop()
        if identifier not in by_id:
            raise GraphError(f"unknown selected node: {identifier}")
        for dependency in by_id[identifier].get("impact_dependencies", []):
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return selected


def impact_closure(
    changed_paths: Iterable[str],
    nodes: list[dict[str, Any]],
    *,
    propagate_dependencies: bool = True,
) -> set[str]:
    """Return nodes admitting changed paths and, optionally, downstream nodes.

    Nodes without an explicit input declaration are selected conservatively.
    This makes incomplete migration slower, never unsoundly green.
    """
    changed = {
        path.removeprefix("./").rstrip("/")
        for path in changed_paths
        if path.removeprefix("./").rstrip("/")
    }
    if not changed:
        raise GraphError("impact selection requires at least one changed path")
    by_id = {node["id"]: node for node in nodes}
    impacted: set[str] = set()
    for node in nodes:
        if node.get("affected") is False:
            continue
        patterns = node.get("inputs")
        if (
            node.get("inputs_complete") is not True
            or not isinstance(patterns, list)
        ):
            impacted.add(node["id"])
            continue
        if any(
            fnmatch.fnmatch(path, pattern)
            or (
                pattern.endswith("/**")
                and (
                    path == pattern[:-3]
                    or path.startswith(pattern[:-2])
                )
            )
            for path in changed
            for pattern in patterns
        ):
            impacted.add(node["id"])

    if propagate_dependencies:
        changed_set = set(impacted)
        while changed_set:
            dependency = changed_set.pop()
            for node in nodes:
                if (
                    dependency in node.get("depends_on", [])
                    and node["id"] not in impacted
                ):
                    impacted.add(node["id"])
                    changed_set.add(node["id"])
    return impacted & by_id.keys()


def select_nodes(
    ordered: list[dict[str, Any]],
    only: set[str],
    skipped_groups: set[str],
    start: str | None,
) -> list[dict[str, Any]]:
    by_id = {node["id"]: node for node in ordered}
    selected = dependency_closure(only, by_id) if only else set(by_id)
    selected = {
        identifier
        for identifier in selected
        if by_id[identifier]["group"] not in skipped_groups
    }
    if start is not None:
        if start not in by_id:
            raise GraphError(f"unknown --from node: {start}")
        start_index = next(
            index for index, node in enumerate(ordered) if node["id"] == start
        )
        allowed = {node["id"] for node in ordered[start_index:]}
        selected &= allowed
    return [node for node in ordered if node["id"] in selected]


def expand_command(command: list[str], variables: dict[str, str]) -> list[str]:
    expanded: list[str] = []
    for token in command:
        try:
            value = token.format_map(variables)
        except KeyError as error:
            raise GraphError(
                f"command references unknown variable: {error.args[0]}"
            ) from error
        if value:
            expanded.append(value)
    return expanded
