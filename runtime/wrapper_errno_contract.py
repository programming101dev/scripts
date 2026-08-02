"""Shared selection mechanics for the wrapper errno contract."""

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
    if contract.get("schema") != "p101-wrapper-errno-contract-v1":
        raise ValueError("unsupported wrapper errno contract")
    return contract


def current_platform_key() -> str | None:
    """Return the contract key for the current host."""
    return PLATFORM_KEYS.get(platform.system())


def effective_error_selection(
    contract: dict[str, Any],
    function: str,
    platform_key: str | None,
) -> tuple[list[str], str, str | None]:
    """Select a platform manual when present, otherwise the POSIX baseline."""
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
            "platform-manual",
            platform_record.get("source"),
        )
    posix = record["posix"]
    return (
        sorted(set(posix["effective_errors"])),
        "posix-fallback",
        posix.get("source"),
    )


def injected_error_cases(
    contract: dict[str, Any],
    function: str | None,
    platform_key: str | None,
) -> list[str]:
    """Return exhaustive documented cases or one instrumentation smoke case."""
    if function is None:
        return ["EIO"]
    errors, _selection, _source = effective_error_selection(
        contract,
        function,
        platform_key,
    )
    return errors or ["EIO"]
