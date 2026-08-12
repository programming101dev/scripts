"""Shared selection mechanics for the wrapper platform-fault contract."""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any


PLATFORM_KEYS = {
    "Darwin": "macos",
    "Linux": "linux",
    "FreeBSD": "freebsd",
}


def load_contract(path: Path) -> dict[str, Any]:
    """Load the checked-in contract and reject an incompatible schema."""
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema") != "p101-wrapper-platform-faults-v3":
        raise ValueError("unsupported wrapper platform-fault contract")
    return contract


def current_platform_key() -> str | None:
    """Return the contract key for the current host."""
    return PLATFORM_KEYS.get(platform.system())


def header_conditional_fault_symbols(
    contract: dict[str, Any],
    platform_key: str | None,
    header: str | None = None,
) -> set[str]:
    """Return symbols whose availability is decided by the active header."""
    if platform_key is None:
        return set()
    platform_record = contract.get(
        "header_conditional_fault_symbols", {}
    ).get(platform_key, {})
    if not isinstance(platform_record, dict):
        raise ValueError(
            f"invalid header-conditional fault metadata for {platform_key}"
        )
    selected = (
        {header: platform_record.get(header, [])}
        if header is not None
        else platform_record
    )
    symbols: set[str] = set()
    for header_name, values in selected.items():
        if not isinstance(header_name, str) or not isinstance(values, list):
            raise ValueError(
                f"invalid header-conditional fault metadata for {platform_key}"
            )
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(
                f"invalid header-conditional symbols for {header_name}"
            )
        symbols.update(values)
    return symbols


def has_explicit_platform_faults(
    contract: dict[str, Any],
    function: str,
    platform_key: str,
    visiting: set[str] | None = None,
) -> bool:
    """Return whether a platform page or its references names fault codes."""
    seen = set() if visiting is None else visiting
    if function in seen:
        return False
    record = contract.get("functions", {}).get(function)
    if record is None:
        return False
    platform_record = record.get("platforms", {}).get(platform_key)
    if (
        platform_record is None
        or platform_record.get("status")
        not in {"documented", "runtime-observed"}
    ):
        return False
    if platform_record.get("errors", []):
        return True
    return any(
        has_explicit_platform_faults(
            contract,
            reference,
            platform_key,
            seen | {function},
        )
        for reference in platform_record.get("references", [])
    )


def effective_fault_selection(
    contract: dict[str, Any],
    function: str,
    platform_key: str | None,
) -> tuple[list[str], str, str, str | None, str]:
    """Select the effective codes, error domain, authority, and coverage."""
    system_record = contract.get("system_faults", {}).get(function)
    if system_record is not None:
        if platform_key is not None:
            selected = system_record["platforms"][platform_key]
            codes = sorted(set(selected["codes"]))
            if not codes and system_record["posix"]["codes"]:
                selected = system_record["posix"]
                return (
                    sorted(set(selected["codes"])),
                    "system",
                    "posix-fallback",
                    selected.get("source"),
                    system_record["coverage_kind"],
                )
            return (
                codes,
                "system",
                selected["source_kind"],
                selected.get("source"),
                system_record["coverage_kind"],
            )
        selected = system_record["posix"]
        return (
            sorted(set(selected["codes"])),
            "system",
            "posix-fallback",
            selected.get("source"),
            system_record["coverage_kind"],
        )

    record = contract["functions"][function]
    platform_record = (
        record["platforms"].get(platform_key)
        if platform_key is not None
        else None
    )
    if (
        platform_record is not None
        and platform_record.get("status")
        in {"documented", "runtime-observed"}
    ):
        posix = record["posix"]
        platform_lacks_explicit_faults = (
            platform_record.get("status") == "documented"
            and not has_explicit_platform_faults(
                contract,
                function,
                platform_key,
            )
            and bool(posix["effective_errors"])
        )
        if platform_lacks_explicit_faults:
            return (
                sorted(set(posix["effective_errors"])),
                "errno",
                "posix-fallback",
                posix.get("source"),
                "exhaustive-symbolic",
            )
        platform_errors = sorted(
            set(platform_record["effective_errors"])
        )
        if (
            platform_record.get("effective_source_kind")
            == "posix-fallback"
            or (not platform_errors and posix["effective_errors"])
        ):
            return (
                platform_errors
                or sorted(set(posix["effective_errors"])),
                "errno",
                "posix-fallback",
                platform_record.get("effective_source")
                or posix.get("source"),
                "exhaustive-symbolic",
            )
        return (
            platform_errors,
            "errno",
            platform_record.get(
                "effective_source_kind",
                "platform-manual",
            ),
            platform_record.get("effective_source")
            or platform_record.get("source"),
            "exhaustive-symbolic",
        )
    posix = record["posix"]
    return (
        sorted(set(posix["effective_errors"])),
        "errno",
        "posix-fallback",
        posix.get("source"),
        "exhaustive-symbolic",
    )


def injected_fault_cases(
    contract: dict[str, Any],
    function: str | None,
    platform_key: str | None,
) -> list[str]:
    """Return exhaustive documented cases or one instrumentation smoke case."""
    if function is None:
        return ["EIO"]
    errors, _domain, _selection, _source, _coverage = (
        effective_fault_selection(
            contract,
            function,
            platform_key,
        )
    )
    return errors or ["EIO"]


def has_documented_faults(
    contract: dict[str, Any],
    function: str | None,
) -> bool:
    """Return whether any supported authority documents a failure outcome."""
    if function is None:
        return False
    system_record = contract.get("system_faults", {}).get(function)
    if system_record is not None:
        return bool(system_record["posix"]["codes"]) or any(
            value["codes"]
            for value in system_record["platforms"].values()
        )
    record = contract.get("functions", {}).get(function)
    if record is None:
        return False
    return bool(record["posix"]["effective_errors"]) or any(
        value["effective_errors"]
        for value in record["platforms"].values()
    )


def fault_domain(
    contract: dict[str, Any],
    function: str | None,
) -> str:
    """Return the wrapper-visible error domain for one native function."""
    if function is not None and function in contract.get("system_faults", {}):
        return "system"
    return "errno"


def fault_symbol_header(
    contract: dict[str, Any],
    function: str | None,
) -> str:
    """Return the declared header that defines this fault-code domain."""
    if function is None:
        return "errno.h"
    system_record = contract.get("system_faults", {}).get(function)
    if system_record is None:
        return "errno.h"
    header = system_record.get("symbol_header")
    if not isinstance(header, str) or not header:
        raise ValueError(
            f"system fault contract for {function} has no symbol_header"
        )
    return header
