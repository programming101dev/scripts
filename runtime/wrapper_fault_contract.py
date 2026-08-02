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
    if contract.get("schema") != "p101-wrapper-platform-faults-v1":
        raise ValueError("unsupported wrapper platform-fault contract")
    return contract


def current_platform_key() -> str | None:
    """Return the contract key for the current host."""
    return PLATFORM_KEYS.get(platform.system())


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
            return (
                sorted(set(selected["codes"])),
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
        and platform_record.get("status") == "documented"
    ):
        return (
            sorted(set(platform_record["effective_errors"])),
            "errno",
            "platform-manual",
            platform_record.get("source"),
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


def fault_domain(
    contract: dict[str, Any],
    function: str | None,
) -> str:
    """Return the wrapper-visible error domain for one native function."""
    if function is not None and function in contract.get("system_faults", {}):
        return "system"
    return "errno"
