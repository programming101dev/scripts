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
CLANG_AST_SPEC = importlib.util.spec_from_file_location(
    "p101_runtime_clang_ast",
    RUNTIME_ROOT / "clang_ast.py",
)
assert CLANG_AST_SPEC is not None and CLANG_AST_SPEC.loader is not None
CLANG_AST_MODULE = importlib.util.module_from_spec(CLANG_AST_SPEC)
CLANG_AST_SPEC.loader.exec_module(CLANG_AST_MODULE)


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
        artifact.write_text("P101FACT\t8\tFILE\tdemo.c\tdemo\t0\t1\n", encoding="utf-8")
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

    def write_ast_entry(self) -> Path:
        key = "c" * 64
        path = self.cache / "ast" / key
        path.mkdir(parents=True)
        dependency = self.root / "demo.c"
        dependency.write_text("int demo(void) { return 0; }\n", encoding="utf-8")
        payload = path / "ast.json.gz"
        with gzip.open(payload, "wt", encoding="utf-8") as stream:
            json.dump({"kind": "TranslationUnitDecl"}, stream)
        status = dependency.stat()
        manifest = {
            "schema": MODULE.AST_SCHEMA,
            "key": key,
            "payload_bytes": payload.stat().st_size,
            "payload_sha256": MODULE.hash_file(payload),
            "dependencies": [
                {
                    "path": str(dependency),
                    "bytes": status.st_size,
                    "modified_ns": status.st_mtime_ns,
                    "changed_ns": status.st_ctime_ns,
                    "device": status.st_dev,
                    "inode": status.st_ino,
                }
            ],
        }
        (path / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return payload

    def test_mixed_snapshot_is_verified_and_receipted(self) -> None:
        self.write_runtime_entry()
        self.write_raw_entry()
        self.write_ast_entry()
        self.record_usage("runtime-facts", "a" * 64)
        self.record_usage("compile-database-facts", "b" * 64)
        self.record_usage("clang-ast", "c" * 64)
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
        self.assertEqual(document["entry_count"], 3)
        self.assertEqual(document["runtime_entry_count"], 1)
        self.assertEqual(document["compile_database_entry_count"], 1)
        self.assertEqual(document["clang_ast_entry_count"], 1)
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

    def test_terminal_prune_keeps_only_referenced_entries(self) -> None:
        runtime = self.write_runtime_entry()
        self.write_raw_entry()
        self.write_ast_entry()
        self.record_usage("runtime-facts", "a" * 64)
        self.record_usage("compile-database-facts", "b" * 64)
        self.record_usage("clang-ast", "c" * 64)
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
        self.assertTrue((self.cache / "ast" / ("c" * 64)).is_dir())
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

    def test_prime_count_validates_sidecar_without_decoding_facts(self) -> None:
        key = "f" * 64
        facts = [{"kind": "FILE", "path": "demo.c"}]
        C_FACTS_MODULE._snapshot_store(self.cache, key, facts)

        with mock.patch.object(
            C_FACTS_MODULE,
            "_snapshot_restore",
            side_effect=AssertionError("prime decoded the fact payload"),
        ):
            self.assertEqual(
                C_FACTS_MODULE._snapshot_restore_count(self.cache, key), 1
            )

    def test_materialized_snapshot_is_decoded_once_per_process(self) -> None:
        key = "9" * 64
        facts = [{"kind": "FILE", "path": "demo.c"}]
        C_FACTS_MODULE._snapshot_store(self.cache, key, facts)
        C_FACTS_MODULE._MATERIALIZED_SNAPSHOTS.clear()

        first = C_FACTS_MODULE._snapshot_restore(self.cache, key)
        payload = self.cache / f"{key}.json.gz"
        payload.write_bytes(b"the memo must avoid a second decode")
        second = C_FACTS_MODULE._snapshot_restore(self.cache, key)

        self.assertIs(first, second)
        self.assertEqual(second, facts)

    def test_clang_ast_parse_is_shared_by_content_key(self) -> None:
        source = self.root / "demo.c"
        source.write_text("int demo(void) { return 0; }\n", encoding="utf-8")

        def produce(
            _clang: str,
            admitted_source: Path,
            _arguments: tuple[str, ...],
            _workspace: Path,
            dependency_path: Path,
        ) -> dict[str, object]:
            dependency_path.write_text(
                f"demo: {admitted_source}\n", encoding="utf-8"
            )
            return {"kind": "TranslationUnitDecl"}

        environment = {"P101_FACTS_CACHE": str(self.cache)}
        CLANG_AST_MODULE._compiler_identity.cache_clear()
        CLANG_AST_MODULE._MATERIALIZED.clear()
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
            CLANG_AST_MODULE, "_invoke", side_effect=produce
        ) as invoke:
            first = CLANG_AST_MODULE.acquire(
                "/usr/bin/true", source, ("-std=c17",), self.root
            )
            CLANG_AST_MODULE._MATERIALIZED.clear()
            second = CLANG_AST_MODULE.acquire(
                "/usr/bin/true", source, ("-std=c17",), self.root
            )
        self.assertEqual(first, second)
        self.assertEqual(invoke.call_count, 1)

    def test_clang_ast_dependency_change_forces_reparse(self) -> None:
        source = self.root / "demo.c"
        header = self.root / "demo.h"
        source.write_text('#include "demo.h"\nint demo(void);\n', encoding="utf-8")
        header.write_text("int demo(void);\n", encoding="utf-8")

        def produce(
            _clang: str,
            admitted_source: Path,
            _arguments: tuple[str, ...],
            _workspace: Path,
            dependency_path: Path,
        ) -> dict[str, object]:
            dependency_path.write_text(
                f"demo: {admitted_source} {header}\n", encoding="utf-8"
            )
            return {"kind": "TranslationUnitDecl"}

        environment = {"P101_FACTS_CACHE": str(self.cache)}
        CLANG_AST_MODULE._compiler_identity.cache_clear()
        CLANG_AST_MODULE._MATERIALIZED.clear()
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
            CLANG_AST_MODULE, "_invoke", side_effect=produce
        ) as invoke:
            CLANG_AST_MODULE.acquire(
                "/usr/bin/true", source, ("-std=c17",), self.root
            )
            header.write_text("int demo(int value);\n", encoding="utf-8")
            CLANG_AST_MODULE.acquire(
                "/usr/bin/true", source, ("-std=c17",), self.root
            )
        self.assertEqual(invoke.call_count, 2)

    def test_prime_count_rejects_payload_changed_after_sidecar(self) -> None:
        key = "f" * 64
        C_FACTS_MODULE._snapshot_store(
            self.cache, key, [{"kind": "FILE", "path": "demo.c"}]
        )
        with (self.cache / f"{key}.json.gz").open("ab") as stream:
            stream.write(b"corrupt")
        self.assertIsNone(
            C_FACTS_MODULE._snapshot_restore_count(self.cache, key)
        )

    def test_fact_workers_use_conservative_default_and_explicit_override(
        self,
    ) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "os.cpu_count", return_value=8
        ):
            self.assertEqual(C_FACTS_MODULE._fact_worker_count(41), 4)
            self.assertEqual(C_FACTS_MODULE._fact_worker_count(1), 1)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "os.cpu_count", return_value=2
        ):
            self.assertEqual(C_FACTS_MODULE._fact_worker_count(41), 2)
        with mock.patch.dict(
            os.environ, {"P101_FACTS_JOBS": "4"}, clear=True
        ):
            self.assertEqual(C_FACTS_MODULE._fact_worker_count(41), 4)
        with mock.patch.dict(
            os.environ, {"P101_FACTS_JOBS": "0"}, clear=True
        ):
            with self.assertRaisesRegex(
                C_FACTS_MODULE.CFactError, "positive decimal integer"
            ):
                C_FACTS_MODULE._fact_worker_count(2)

    def test_canonical_root_scan_does_not_merge_component_scopes(self) -> None:
        repository = self.root / "repo"
        root_source = repository / "src"
        root_test = repository / "test"
        component_source = repository / "components" / "doctor" / "src"
        root_source.mkdir(parents=True)
        root_test.mkdir(parents=True)
        component_source.mkdir(parents=True)
        (root_source / "root.c").write_text("int root;\n", encoding="utf-8")
        (root_test / "test_root.c").write_text(
            "int test_root;\n", encoding="utf-8"
        )
        (component_source / "doctor.c").write_text(
            "int doctor;\n", encoding="utf-8"
        )
        paths = C_FACTS_MODULE._canonical_scan_paths(
            repository, (root_source,)
        )
        self.assertEqual(paths, (root_source,))

    def test_component_scope_inherits_owner_and_private_include_roots(self) -> None:
        workspace = self.root / "workspace"
        repository = workspace / "programs" / "p101-audit"
        component = repository / "components" / "workspace"
        owner_include = repository / "include"
        component_include = component / "include"
        component_unity = component / "test" / "unity"
        database_include = repository / "generated-include"
        for path in (
            repository / ".git",
            owner_include,
            component_include,
            component_unity,
            database_include,
        ):
            path.mkdir(parents=True)
        (repository / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(repository),
                        "arguments": [
                            "clang",
                            "-I",
                            str(database_include),
                            "-c",
                            "source.c",
                        ],
                        "file": "source.c",
                    }
                ]
            ),
            encoding="utf-8",
        )

        roots = C_FACTS_MODULE._analysis_include_roots(workspace, component)

        self.assertEqual(
            roots,
            {
                owner_include.resolve(),
                component_include.resolve(),
                component_unity.resolve(),
                database_include.resolve(),
            },
        )

    def test_analysis_units_separate_production_test_and_fuzz_scopes(self) -> None:
        workspace = self.root / "workspace"
        repository = workspace / "libraries" / "lib_demo"
        (repository / ".git").mkdir(parents=True)
        paths = []
        for name in ("src", "include", "test", "fuzz"):
            path = repository / name
            path.mkdir()
            paths.append(path)
        units = C_FACTS_MODULE._analysis_units(workspace, paths)
        self.assertEqual(len(units), 3)
        admitted_sets = {tuple(path.name for path in unit[1]) for unit in units}
        self.assertEqual(
            admitted_sets,
            {("fuzz",), ("include", "src"), ("test",)},
        )

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
            f"printf 'P101FACT\\t8\\tFILE\\t{source / 'demo.c'}"
            "\\tdemo\\t0\\t1\\n'\n",
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
            {
                "P101_AUDIT_FACTS": str(launcher),
                "P101_TEST_FACT_COUNTER": str(counter),
            },
            clear=False,
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: acquire(), range(2)))
            file_facts = C_FACTS_MODULE.acquire(
                workspace,
                [source / "demo.c"],
                additional_include_roots=(),
                cache=self.cache,
            )
        self.assertEqual(counter.read_text(encoding="utf-8"), "x")
        self.assertEqual([len(facts) for facts in results], [1, 1])
        self.assertEqual(len(file_facts), 1)


if __name__ == "__main__":
    unittest.main()
