"""Read content identities produced once by the governed workspace graph.

The manifest is an optimization receipt, not an authority boundary. Consumers
may use an admitted digest only while the file still has the recorded size and
modification time; otherwise they fall back to reading the current bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


SCHEMA = "p101-workspace-content-manifest-v1"


class ContentManifestError(ValueError):
    """A configured workspace content manifest is malformed or stale."""


class ContentManifest:
    def __init__(self, path: Path) -> None:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema") != SCHEMA:
            raise ContentManifestError(f"unsupported content manifest: {path}")
        workspace = document.get("workspace")
        records = document.get("files")
        if not isinstance(workspace, str) or not isinstance(records, list):
            raise ContentManifestError(f"malformed content manifest: {path}")
        self.workspace = Path(workspace).resolve()
        self.records: dict[str, dict[str, object]] = {}
        for record in records:
            if not isinstance(record, dict):
                raise ContentManifestError(f"malformed content record: {path}")
            relative = record.get("path")
            kind = record.get("kind")
            digest = record.get("sha256")
            size = record.get("bytes")
            modified_ns = record.get("modified_ns")
            changed_ns = record.get("changed_ns")
            device = record.get("device")
            inode = record.get("inode")
            if (
                not isinstance(relative, str)
                or not relative
                or relative.startswith("/")
                or ".." in Path(relative).parts
                or relative in self.records
                or kind not in {"file", "symlink"}
                or not isinstance(digest, str)
                or len(digest) != 64
                or not isinstance(size, int)
                or not isinstance(modified_ns, int)
                or not isinstance(changed_ns, int)
                or not isinstance(device, int)
                or not isinstance(inode, int)
            ):
                raise ContentManifestError(f"malformed content record: {path}")
            try:
                bytes.fromhex(digest)
            except ValueError as error:
                raise ContentManifestError(
                    f"malformed content digest: {path}"
                ) from error
            self.records[relative] = record

    def _relative(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.workspace).as_posix()
        except ValueError:
            return None

    def digest(self, path: Path) -> bytes | None:
        relative = self._relative(path)
        record = self.records.get(relative or "")
        if record is None or record.get("kind") != "file":
            return None
        try:
            status = path.lstat()
        except OSError:
            return None
        if (
            status.st_size != record["bytes"]
            or status.st_mtime_ns != record["modified_ns"]
            or status.st_ctime_ns != record["changed_ns"]
            or status.st_dev != record["device"]
            or status.st_ino != record["inode"]
        ):
            return None
        return bytes.fromhex(str(record["sha256"]))

_LOADED: tuple[str, ContentManifest | None] | None = None


def configured_manifest() -> ContentManifest | None:
    global _LOADED

    configured = os.environ.get("P101_CONTENT_MANIFEST", "")
    if _LOADED is not None and _LOADED[0] == configured:
        return _LOADED[1]
    if not configured:
        _LOADED = (configured, None)
        return None
    try:
        manifest = ContentManifest(Path(configured))
    except (OSError, ValueError, json.JSONDecodeError):
        manifest = None
    _LOADED = (configured, manifest)
    return manifest


def manifest_digest(path: Path) -> bytes | None:
    """Return an admitted digest without reading the file, when available."""
    manifest = configured_manifest()
    return manifest.digest(path) if manifest is not None else None


def hash_file(path: Path) -> bytes:
    digest_value = manifest_digest(path)
    if digest_value is not None:
        return digest_value
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.digest()


def hash_tree(root: Path, paths: Iterable[Path]) -> str | None:
    manifest = configured_manifest()
    if manifest is None:
        return None
    digest = hashlib.sha256()
    root = root.resolve()
    admitted = False
    for path in sorted(paths):
        payload_digest = manifest.digest(path)
        if payload_digest is None:
            return None
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return None
        admitted = True
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(payload_digest)
    return digest.hexdigest() if admitted else None
