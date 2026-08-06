"""Acquire and decode the native P101FACT stream for policy checkers.

This module owns transport only.  Callers decide policy from resolved
declaration identities, source extents, and typed fact kinds.
"""

from __future__ import annotations

import json
import platform
import shlex
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable


class CFactError(RuntimeError):
    """The semantic fact producer or its versioned output was invalid."""


SOURCE_DIRECTORY_NAMES = ("src", "include", "test", "fuzz")
SOURCE_SUFFIXES = {".c", ".h"}


def _unescape(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            escaped = value[index + 1]
            output.append(
                {"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}.get(
                    escaped, escaped
                )
            )
            index += 2
        else:
            output.append(value[index])
            index += 1
    return "".join(output)


def _decode(line: str, number: int) -> dict[str, object] | None:
    if not line.startswith("P101FACT\t"):
        return None
    fields = [_unescape(value) for value in line.split("\t")]
    if len(fields) < 7 or fields[1] != "6":
        raise CFactError(f"malformed P101FACT v6 record at output line {number}")
    base: dict[str, object] = {
        "kind": fields[2],
        "path": fields[3],
        "module": fields[4],
        "is_header": fields[5] == "1",
        "line": int(fields[6]),
    }
    if fields[2] == "FUNCTION" and len(fields) == 13:
        base.update(
            value=fields[7],
            is_static=fields[8] == "1",
            is_declaration=fields[9] == "1",
            usr=fields[10],
            start=int(fields[11]),
            end=int(fields[12]),
        )
    elif fields[2] == "CALL" and len(fields) == 16:
        base.update(
            value=fields[7],
            has_env_parameter=fields[8] == "1",
            has_error_parameter=fields[9] == "1",
            is_indirect=fields[10] == "1",
            caller=fields[11],
            usr=fields[12],
            caller_usr=fields[13],
            start=int(fields[14]),
            end=int(fields[15]),
        )
    elif fields[2] == "INCLUDE" and len(fields) == 9:
        base.update(value=fields[7], is_local=fields[8] == "1")
    elif fields[2] in {"TYPE", "ENUM"} and len(fields) == 9:
        base.update(value=fields[7], usr=fields[8])
    elif fields[2] == "ENUMERATOR" and len(fields) == 11:
        base.update(
            value=fields[7],
            type=fields[8],
            usr=fields[9],
            parent_usr=fields[10],
        )
    elif fields[2] == "NOTE" and len(fields) == 13:
        base.update(
            value=fields[7],
            caller=fields[8],
            column=int(fields[9]),
            caller_usr=fields[10],
            start=int(fields[11]),
            end=int(fields[12]),
        )
    elif fields[2] == "MACRO" and len(fields) == 12:
        base.update(
            value=fields[7],
            is_definition=fields[8] == "1",
            caller_usr=fields[9],
            start=int(fields[10]),
            end=int(fields[11]),
        )
    else:
        return None
    return base


def decode_lines(lines: Iterable[str]) -> list[dict[str, object]]:
    """Decode a complete P101FACT stream, ignoring non-fact diagnostics."""
    facts: list[dict[str, object]] = []
    for number, line in enumerate(lines, 1):
        fact = _decode(line, number)
        if fact is not None:
            facts.append(fact)
    return facts


def _repository_root(workspace: Path, path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    workspace = workspace.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
        if candidate == workspace:
            break
    return None


def _repository_scan_paths(repository: Path) -> tuple[Path, ...]:
    paths = [
        repository / name
        for name in SOURCE_DIRECTORY_NAMES
        if (repository / name).is_dir()
    ]
    paths.extend(
        path
        for path in repository.iterdir()
        if path.is_file() and path.suffix in SOURCE_SUFFIXES
    )
    return tuple(sorted(paths)) or (repository,)


def _analysis_units(
    workspace: Path, admitted_paths: Iterable[Path]
) -> tuple[tuple[Path | None, tuple[Path, ...]], ...]:
    """Partition an aggregate source scope into repository-local parses.

    Local headers such as ``cli.h`` are meaningful only within their owning
    repository. A single parse of every program with every program include
    root would silently bind duplicate header names to whichever ``-I`` path
    sorted first.
    """
    workspace = workspace.resolve()
    grouped: dict[Path | None, set[Path]] = defaultdict(set)
    for admitted_path in admitted_paths:
        admitted_path = admitted_path.resolve()
        repository = _repository_root(workspace, admitted_path)
        if repository is not None:
            if admitted_path == repository:
                grouped[repository].update(
                    _repository_scan_paths(repository)
                )
            else:
                grouped[repository].add(admitted_path)
            continue
        child_repositories = (
            [
                child
                for child in admitted_path.iterdir()
                if child.is_dir() and (child / ".git").exists()
            ]
            if admitted_path.is_dir()
            else []
        )
        if child_repositories:
            for child in child_repositories:
                grouped[child.resolve()].update(_repository_scan_paths(child))
        else:
            grouped[None].add(admitted_path)
    return tuple(
        (repository, tuple(sorted(paths)))
        for repository, paths in sorted(
            grouped.items(),
            key=lambda item: "" if item[0] is None else str(item[0]),
        )
    )


def _compile_database_include_roots(repository: Path) -> set[Path]:
    """Read include search roots without executing compilation commands."""
    database = repository / "compile_commands.json"
    if not database.is_file():
        return set()
    try:
        records = json.loads(database.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CFactError(
            f"cannot read compilation database include roots: {database}: "
            f"{error}"
        ) from error
    if not isinstance(records, list):
        raise CFactError(f"compilation database is not an array: {database}")

    roots: set[Path] = set()
    separate_flags = {"-I", "-isystem", "-iquote", "-idirafter"}
    attached_flags = ("-isystem", "-iquote", "-idirafter", "-I")
    for record in records:
        if not isinstance(record, dict):
            raise CFactError(f"compilation database has a non-object row: {database}")
        directory_value = record.get("directory")
        if not isinstance(directory_value, str):
            raise CFactError(
                f"compilation database row has no directory: {database}"
            )
        directory = Path(directory_value)
        raw_arguments = record.get("arguments")
        if isinstance(raw_arguments, list) and all(
            isinstance(argument, str) for argument in raw_arguments
        ):
            arguments = list(raw_arguments)
        else:
            command = record.get("command")
            if not isinstance(command, str):
                raise CFactError(
                    f"compilation database row has no command arguments: "
                    f"{database}"
                )
            try:
                arguments = shlex.split(command)
            except ValueError as error:
                raise CFactError(
                    f"cannot decode compilation database command: {database}: "
                    f"{error}"
                ) from error

        index = 0
        while index < len(arguments):
            argument = arguments[index]
            include_value: str | None = None
            if argument in separate_flags:
                index += 1
                if index >= len(arguments):
                    raise CFactError(
                        f"compilation database has an incomplete "
                        f"{argument} option: {database}"
                    )
                include_value = arguments[index]
            else:
                for prefix in attached_flags:
                    if argument.startswith(prefix) and argument != prefix:
                        include_value = argument[len(prefix) :].removeprefix("=")
                        break
            if include_value:
                include_path = Path(include_value)
                if not include_path.is_absolute():
                    include_path = directory / include_path
                include_path = include_path.resolve()
                if include_path.is_dir():
                    roots.add(include_path)
            index += 1
    return roots


def acquire(
    workspace: Path,
    paths: Iterable[Path],
    *,
    compile_database: Path | None = None,
    additional_include_roots: Iterable[Path] = (),
) -> list[dict[str, object]]:
    producer = workspace / "programs" / "p101-wrapper-audit" / "p101-c-facts"
    if not producer.is_file():
        raise CFactError(f"semantic fact producer is absent: {producer}")
    admitted_paths = [path.resolve() for path in paths]
    shared_include_roots = {
        path.resolve()
        for path in (workspace / "libraries").glob("lib_*/include")
        if path.is_dir()
    }
    shared_include_roots.update(
        path.resolve()
        for path in additional_include_roots
        if path.is_dir()
    )
    workspace_root = workspace.resolve()
    units = (
        ((None, tuple(admitted_paths)),)
        if compile_database is not None
        else _analysis_units(workspace_root, admitted_paths)
    )
    facts: list[dict[str, object]] = []
    for repository, unit_paths in units:
        include_roots = set(shared_include_roots)
        if repository is not None:
            include_roots.update(
                _compile_database_include_roots(repository)
            )
            local_include = repository / "include"
            unity = repository / "test" / "unity"
            if local_include.is_dir():
                include_roots.add(local_include.resolve())
            if unity.is_dir():
                include_roots.add(unity.resolve())
        else:
            for admitted_path in unit_paths:
                for parent in admitted_path.parents:
                    unity = parent / "test" / "unity"
                    if unity.is_dir():
                        include_roots.add(unity.resolve())
                    if parent == workspace_root:
                        break
        command = [str(producer)]
        system = platform.system()
        if system == "Darwin":
            command.append("--cflag=-D_DARWIN_C_SOURCE")
        elif system == "Linux":
            command.append("--cflag=-D_GNU_SOURCE")
        elif system == "FreeBSD":
            command.extend(
                ("--cflag=-D_BSD_SOURCE", "--cflag=-D__BSD_VISIBLE")
            )
        command.extend(f"--cflag=-I{path}" for path in sorted(include_roots))
        if compile_database is not None:
            command.extend(("--compile-db", str(compile_database.resolve())))
        command.extend(str(path) for path in unit_paths)
        try:
            result = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise CFactError(
                f"cannot acquire semantic C facts: {error}"
            ) from error
        if result.returncode != 0:
            context = (
                str(repository.relative_to(workspace_root))
                if repository is not None
                else "shared scope"
            )
            raise CFactError(
                f"semantic C-fact acquisition failed for {context}: "
                + (result.stderr.strip() or f"exit {result.returncode}")
            )
        facts.extend(decode_lines(result.stdout.splitlines()))
    return facts
