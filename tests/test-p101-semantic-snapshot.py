#!/usr/bin/env python3
"""Negative and positive controls for the shared semantic snapshot receipt."""

from __future__ import annotations

import gzip
import importlib.util
import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "p101_semantic_snapshot",
    SCRIPTS_ROOT / "checks" / "p101-semantic-snapshot.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
USAGE_SPEC = importlib.util.spec_from_file_location(
    "p101_semantic_usage",
    SCRIPTS_ROOT / "runtime" / "semantic_usage.py",
)
assert USAGE_SPEC is not None and USAGE_SPEC.loader is not None
USAGE_MODULE = importlib.util.module_from_spec(USAGE_SPEC)
USAGE_SPEC.loader.exec_module(USAGE_MODULE)


class SemanticSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.usage = self.root / "usage"
        self.usage.mkdir()

    def record_usage(self, kind: str, key: str) -> None:
        path = self.usage / "check.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "schema": MODULE.USAGE_SCHEMA,
                        "kind": kind,
                        "key": key,
                    }
                )
                + "\n"
            )

    def write_runtime_entry(self) -> Path:
        key = "a" * 64
        path = self.cache / f"{key}.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "schema": MODULE.RUNTIME_SCHEMA,
                        "key": key,
                        "fact_count": 1,
                    }
                )
                + "\n"
            )
            stream.write(json.dumps({"kind": "FILE", "path": "demo.c"}) + "\n")
        return path

    def write_raw_entry(self) -> Path:
        key = "b" * 64
        path = self.cache / "entries" / key
        path.mkdir(parents=True)
        artifact = path / "facts"
        artifact.write_text("P101FACT\t7\tFILE\tdemo.c\tdemo\t0\t1\n", encoding="utf-8")
        manifest = {
            "schema": MODULE.RAW_SCHEMA,
            "key": key,
            "namespace": "test",
            "artifacts": {
                "facts": {
                    "sha256": MODULE.hash_file(artifact),
                    "bytes": artifact.stat().st_size,
                }
            },
        }
        (path / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return artifact

    def test_mixed_snapshot_is_verified_and_receipted(self) -> None:
        self.write_runtime_entry()
        self.write_raw_entry()
        self.record_usage("runtime-facts", "a" * 64)
        self.record_usage("compile-database-facts", "b" * 64)
        receipt = self.root / "receipt.json"
        with mock.patch(
            "sys.argv",
            [
                "p101-semantic-snapshot",
                "--cache",
                str(self.cache),
                "--usage-directory",
                str(self.usage),
                "--receipt",
                str(receipt),
            ],
        ):
            self.assertEqual(MODULE.main(), 0)
        document = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], MODULE.RECEIPT_SCHEMA)
        self.assertEqual(document["entry_count"], 2)
        self.assertEqual(document["runtime_entry_count"], 1)
        self.assertEqual(document["compile_database_entry_count"], 1)
        self.assertEqual(document["entries"][0]["consumers"], ["check"])
        claimed = document.pop("receipt_digest")
        self.assertEqual(claimed, MODULE.canonical_digest(document))

    def test_corrupt_raw_artifact_is_rejected(self) -> None:
        artifact = self.write_raw_entry()
        self.record_usage("compile-database-facts", "b" * 64)
        artifact.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.SnapshotError, "integrity mismatch"
        ):
            MODULE.inspect(self.cache, self.usage)

    def test_unreferenced_stale_entries_are_not_in_the_snapshot(self) -> None:
        self.write_runtime_entry()
        self.assertEqual(MODULE.inspect(self.cache, self.usage), [])

    def test_missing_referenced_entry_is_rejected(self) -> None:
        self.record_usage("runtime-facts", "c" * 64)
        with self.assertRaisesRegex(MODULE.SnapshotError, "missing entry"):
            MODULE.inspect(self.cache, self.usage)

    def test_usage_writer_is_scoped_to_the_governed_cache(self) -> None:
        log = self.usage / "writer.jsonl"
        environment = {
            "P101_SEMANTIC_CACHE_ROOT": str(self.cache),
            "P101_SEMANTIC_USAGE_LOG": str(log),
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            USAGE_MODULE.record_usage(
                self.cache, "runtime-facts", "a" * 64
            )
            USAGE_MODULE.record_usage(
                self.root / "fixture-cache", "runtime-facts", "b" * 64
            )
        records = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["key"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
