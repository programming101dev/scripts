#!/usr/bin/env python3
"""Negative and positive controls for the shared semantic snapshot receipt."""

from __future__ import annotations

import concurrent.futures
import gzip
import importlib.util
import json
import os
import sys
import tempfile
import unittest
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
RUNTIME_ROOT = SCRIPTS_ROOT / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
C_FACTS_SPEC = importlib.util.spec_from_file_location(
    "p101_runtime_c_facts",
    RUNTIME_ROOT / "c_facts.py",
)
assert C_FACTS_SPEC is not None and C_FACTS_SPEC.loader is not None
C_FACTS_MODULE = importlib.util.module_from_spec(C_FACTS_SPEC)
C_FACTS_SPEC.loader.exec_module(C_FACTS_MODULE)


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
        path = self.cache / f"{key}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema": MODULE.RUNTIME_SCHEMA,
                    "key": key,
                    "fact_count": 1,
                    "facts": [{"kind": "FILE", "path": "demo.c"}],
                },
                stream,
            )
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

    def test_terminal_prune_keeps_only_referenced_v2_entries(self) -> None:
        runtime = self.write_runtime_entry()
        self.write_raw_entry()
        self.record_usage("runtime-facts", "a" * 64)
        self.record_usage("compile-database-facts", "b" * 64)
        stale_runtime = self.cache / f"{'c' * 64}.json.gz"
        stale_runtime.write_bytes(runtime.read_bytes())
        legacy = self.cache / f"{'d' * 64}.jsonl.gz"
        legacy.write_bytes(b"legacy")
        stale_raw = self.cache / "entries" / ("e" * 64)
        stale_raw.mkdir()
        (stale_raw / "facts").write_bytes(b"stale")
        locks = self.cache / ".locks"
        locks.mkdir()
        (locks / "old.lock").touch()

        entries = MODULE.inspect(self.cache, self.usage)
        result = MODULE.prune_cache(self.cache, entries)

        self.assertTrue(runtime.is_file())
        self.assertTrue((self.cache / "entries" / ("b" * 64)).is_dir())
        self.assertFalse(stale_runtime.exists())
        self.assertFalse(legacy.exists())
        self.assertFalse(stale_raw.exists())
        self.assertFalse(locks.exists())
        self.assertEqual(result["entries_removed"], 3)

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

    def test_producer_identity_ignores_equivalent_build_lane_paths(self) -> None:
        repository = self.root / "programs" / "p101-audit"
        repository.mkdir(parents=True)
        launcher = repository / "audit-facts"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        first = repository / "candidate-one" / "audit-facts"
        second = repository / "candidate-two" / "audit-facts"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"same native producer\n")
        second.write_bytes(first.read_bytes())
        marker = repository / ".last-build-dir"
        marker.write_text("candidate-one\n", encoding="utf-8")
        first_identity = C_FACTS_MODULE._producer_identity(launcher)
        marker.write_text("candidate-two\n", encoding="utf-8")
        second_identity = C_FACTS_MODULE._producer_identity(launcher)
        self.assertEqual(first_identity, second_identity)

    def test_producer_identity_binds_dynamic_library_sources(self) -> None:
        repository = self.root / "programs" / "p101-audit"
        repository.mkdir(parents=True)
        launcher = repository / "audit-facts"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        native = repository / "build-test" / "audit-facts"
        native.parent.mkdir()
        native.write_bytes(b"unchanged native producer\n")
        (repository / ".last-build-dir").write_text(
            "build-test\n", encoding="utf-8"
        )
        dependency = self.root / "libraries" / "lib_c_facts" / "src"
        dependency.mkdir(parents=True)
        source = dependency / "facts.c"
        source.write_text("int version = 1;\n", encoding="utf-8")

        first_identity = C_FACTS_MODULE._producer_identity(launcher)
        source.write_text("int version = 2;\n", encoding="utf-8")
        second_identity = C_FACTS_MODULE._producer_identity(launcher)

        self.assertNotEqual(first_identity, second_identity)

    def test_file_digest_cache_invalidates_changed_bytes(self) -> None:
        path = self.root / "content.c"
        path.write_text("one\n", encoding="utf-8")
        first = C_FACTS_MODULE._file_content_digest(path)
        path.write_text("two\n", encoding="utf-8")
        second = C_FACTS_MODULE._file_content_digest(path)
        self.assertNotEqual(first, second)

    def test_concurrent_runtime_fact_misses_run_the_producer_once(self) -> None:
        workspace = self.root / "workspace"
        repository = workspace / "libraries" / "lib_demo"
        source = repository / "src"
        producer_repository = workspace / "programs" / "p101-audit"
        native = producer_repository / "build-test" / "audit-facts"
        source.mkdir(parents=True)
        (repository / ".git").mkdir()
        producer_repository.mkdir(parents=True)
        native.parent.mkdir()
        (source / "demo.c").write_text(
            "int demo(void) { return 0; }\n", encoding="utf-8"
        )
        counter = self.root / "producer-count"
        launcher = producer_repository / "audit-facts"
        launcher.write_text(
            "#!/bin/sh\n"
            'exec "$(dirname "$0")/build-test/audit-facts" "$@"\n',
            encoding="utf-8",
        )
        native.write_text(
            "#!/bin/sh\n"
            'printf x >> "$P101_TEST_FACT_COUNTER"\n'
            "sleep 0.2\n"
            "printf 'P101FACT\\t7\\tFILE\\tdemo.c\\tdemo\\t0\\t1\\n'\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
        native.chmod(0o755)
        (producer_repository / ".last-build-dir").write_text(
            "build-test\n", encoding="utf-8"
        )

        def acquire() -> list[dict[str, object]]:
            return C_FACTS_MODULE.acquire(
                workspace,
                [source],
                additional_include_roots=(),
                cache=self.cache,
            )

        with mock.patch.dict(
            os.environ,
            {"P101_TEST_FACT_COUNTER": str(counter)},
            clear=False,
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: acquire(), range(2)))
        self.assertEqual(counter.read_text(encoding="utf-8"), "x")
        self.assertEqual([len(facts) for facts in results], [1, 1])


if __name__ == "__main__":
    unittest.main()
