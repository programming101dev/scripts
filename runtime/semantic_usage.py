"""Record semantic-cache entries used by one governed check.

The graph gives each check a private append-only ledger. Cache producers call
``record_usage`` only when their cache root is the graph's shared semantic
store, so fixture caches cannot contaminate the run receipt.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


USAGE_SCHEMA = "p101-semantic-usage-v1"


def record_usage(cache: Path, kind: str, key: str) -> None:
    """Append one content key to the current check's semantic usage ledger."""
    configured_cache = os.environ.get("P101_SEMANTIC_CACHE_ROOT", "")
    configured_log = os.environ.get("P101_SEMANTIC_USAGE_LOG", "")
    if not configured_cache or not configured_log:
        return
    if cache.resolve() != Path(configured_cache).resolve():
        return
    record = {
        "schema": USAGE_SCHEMA,
        "kind": kind,
        "key": key,
    }
    log_path = Path(configured_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.write(
                json.dumps(record, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            stream.flush()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
