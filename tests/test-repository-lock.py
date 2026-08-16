#!/usr/bin/env python3
"""Regression tests for exact-revision workspace locking."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
LOCK_TOOL = SCRIPTS_ROOT / "workspace" / "repos-lock.py"
CLONE_TOOL = SCRIPTS_ROOT / "distribution" / "clone-repos.sh"
REFRESH_TOOL = SCRIPTS_ROOT / "distribution" / "refresh-repo.sh"
TEST_ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def run(*arguments: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=TEST_ENVIRONMENT,
    )


def run_with_env(
    environment: dict[str, str],
    *arguments: str | Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={
            **TEST_ENVIRONMENT,
            **environment,
        },
    )


def run_with_input(
    input_text: str,
    *arguments: str | Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=TEST_ENVIRONMENT,
    )


def git(repository: Path, *arguments: str) -> str:
    completed = run("git", "-C", repository, *arguments)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


class RepositoryLockTests(unittest.TestCase):
    def test_workspace_candidate_binds_revisions_and_acceptance_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-workspace-candidate.") as temporary:
            root = Path(temporary)
            scripts_remote = root / "scripts.git"
            scripts = root / "scripts"
            workspace = scripts / "workspace"
            libraries = root / "libraries"
            library_remote = root / "lib_one.git"
            library = libraries / "lib_one"
            evidence = root / "evidence"
            libraries.mkdir()
            evidence.mkdir()

            self.assertEqual(
                run("git", "init", "--quiet", "--bare", scripts_remote).returncode,
                0,
            )
            self.assertEqual(
                run("git", "clone", "--quiet", scripts_remote, scripts).returncode,
                0,
            )
            git(scripts, "config", "user.name", "p101 candidate test")
            git(scripts, "config", "user.email", "candidate@invalid.example")
            workspace.mkdir()
            shutil.copy2(LOCK_TOOL, workspace / "repos-lock.py")
            (scripts / "README.md").write_text("scripts\n", encoding="utf-8")
            git(scripts, "add", ".")
            git(scripts, "commit", "--quiet", "-m", "scripts")
            git(scripts, "branch", "-M", "main")
            git(scripts, "push", "--quiet", "-u", "origin", "main")

            self.assertEqual(
                run("git", "init", "--quiet", "--bare", library_remote).returncode,
                0,
            )
            self.assertEqual(
                run("git", "clone", "--quiet", library_remote, library).returncode,
                0,
            )
            git(library, "config", "user.name", "p101 candidate test")
            git(library, "config", "user.email", "candidate@invalid.example")
            (library / "value.txt").write_text("published\n", encoding="utf-8")
            git(library, "add", "value.txt")
            git(library, "commit", "--quiet", "-m", "published")
            git(library, "branch", "-M", "main")
            git(library, "push", "--quiet", "-u", "origin", "main")
            (library / "value.txt").write_text("candidate\n", encoding="utf-8")
            git(library, "add", "value.txt")
            git(library, "commit", "--quiet", "-m", "candidate")
            candidate_commit = git(library, "rev-parse", "HEAD")

            manifest = scripts / "repos.txt"
            manifest.write_text(
                f"{library_remote}|../libraries/lib_one|c\n", encoding="utf-8"
            )
            git(scripts, "add", "repos.txt")
            git(scripts, "commit", "--quiet", "-m", "manifest")
            git(scripts, "push", "--quiet")

            candidate_lock = evidence / "repos.candidate.lock"
            candidate_stack_contract = evidence / "candidate-stack-contract.json"
            candidate_receipt = evidence / "workspace-candidate.json"
            acceptance_receipt = evidence / "acceptance-receipt.json"
            base_arguments = (
                workspace / "repos-lock.py",
                "--scripts-root",
                scripts,
                "--manifest",
                manifest,
                "--lock",
                candidate_lock,
            )
            refreshed = run(
                *base_arguments,
                "refresh",
                "--require-clean",
                "--allow-ahead",
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            candidate_stack_contract.write_text(
                '{"schema":"test-stack-contract"}\n', encoding="utf-8"
            )
            candidate_stack_digest = hashlib.sha256(
                candidate_stack_contract.read_bytes()
            ).hexdigest()
            acceptance_document = {
                "schema": "p101-check-graph-receipt-v2",
                "outcome": "clean",
                "host": {
                    "system": "Darwin",
                    "release": "test",
                    "machine": "test",
                    "python": "test",
                },
                "workspace_lock": {
                    "valid": True,
                    "lock_sha256": hashlib.sha256(
                        candidate_lock.read_bytes()
                    ).hexdigest(),
                },
                "stack_contract": {
                    "valid": True,
                    "contract_sha256": candidate_stack_digest,
                },
            }
            acceptance_document["receipt_digest"] = "sha256:" + hashlib.sha256(
                json.dumps(
                    acceptance_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            acceptance_receipt.write_text(
                json.dumps(acceptance_document) + "\n", encoding="utf-8"
            )
            wrong_lock_document = json.loads(json.dumps(acceptance_document))
            wrong_lock_document["workspace_lock"]["lock_sha256"] = "0" * 64
            wrong_lock_document.pop("receipt_digest")
            wrong_lock_document["receipt_digest"] = "sha256:" + hashlib.sha256(
                json.dumps(
                    wrong_lock_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            acceptance_receipt.write_text(
                json.dumps(wrong_lock_document) + "\n", encoding="utf-8"
            )
            wrong_lock = run(
                *base_arguments,
                "candidate",
                "--receipt",
                candidate_receipt,
                "--candidate-stack-contract",
                candidate_stack_contract,
                "--acceptance-receipt",
                acceptance_receipt,
            )
            self.assertEqual(wrong_lock.returncode, 2)
            self.assertIn("candidate lock", wrong_lock.stderr)
            acceptance_receipt.write_text(
                json.dumps(acceptance_document) + "\n", encoding="utf-8"
            )
            created = run(
                *base_arguments,
                "candidate",
                "--receipt",
                candidate_receipt,
                "--candidate-stack-contract",
                candidate_stack_contract,
                "--acceptance-receipt",
                acceptance_receipt,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            document = json.loads(candidate_receipt.read_text(encoding="utf-8"))
            self.assertEqual(document["schema"], "p101-workspace-candidate-v1")
            self.assertEqual(document["validation"]["status"], "passed")
            self.assertEqual(
                [row["path"] for row in document["repositories"] if row["publish"]],
                ["../libraries/lib_one"],
            )
            verified = run(
                *base_arguments,
                "verify-candidate",
                "--candidate",
                candidate_receipt,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            entries = run(
                *base_arguments,
                "candidate-entries",
                "--candidate",
                candidate_receipt,
            )
            self.assertEqual(entries.returncode, 0, entries.stderr)
            self.assertIn(candidate_commit, entries.stdout)

            qualification_coordinates = run(
                *base_arguments,
                "candidate-qualification",
                "--candidate",
                candidate_receipt,
            )
            self.assertEqual(
                qualification_coordinates.returncode,
                0,
                qualification_coordinates.stderr,
            )
            candidate_id, lock_digest, stack_digest, qualification_ref = (
                qualification_coordinates.stdout.strip().split("|")
            )
            self.assertEqual(stack_digest, candidate_stack_digest)
            scripts_commit = git(scripts, "rev-parse", "HEAD")
            platform_receipts: list[Path] = []
            system_for_platform = {
                "linux": "Linux",
                "macos": "Darwin",
                "freebsd": "FreeBSD",
            }
            for platform_name in ("linux", "macos", "freebsd"):
                platform_receipt = evidence / f"{platform_name}-qualification.json"
                platform_acceptance_receipt = (
                    evidence / f"{platform_name}-acceptance.json"
                )
                platform_acceptance = json.loads(json.dumps(acceptance_document))
                platform_acceptance["host"]["system"] = system_for_platform[
                    platform_name
                ]
                platform_acceptance.pop("receipt_digest")
                platform_acceptance["receipt_digest"] = (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            platform_acceptance,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                )
                platform_acceptance_receipt.write_text(
                    json.dumps(platform_acceptance) + "\n", encoding="utf-8"
                )
                platform_receipts.append(platform_receipt)
                platform_result = run(
                    *base_arguments,
                    "platform-qualification",
                    "--candidate-id",
                    candidate_id,
                    "--candidate-lock-sha256",
                    lock_digest,
                    "--candidate-stack-contract-sha256",
                    stack_digest,
                    "--qualification-ref",
                    qualification_ref,
                    "--scripts-commit",
                    scripts_commit,
                    "--platform",
                    platform_name,
                    "--github-repository",
                    "programming101dev/scripts",
                    "--github-run-id",
                    "12345",
                    "--github-run-attempt",
                    "1",
                    "--acceptance-receipt",
                    platform_acceptance_receipt,
                    "--receipt",
                    platform_receipt,
                )
                self.assertEqual(platform_result.returncode, 0, platform_result.stderr)
            qualification_receipt = evidence / "workspace-qualification.json"
            aggregate_arguments: list[str | Path] = [
                *base_arguments,
                "aggregate-qualification",
                "--candidate-id",
                candidate_id,
                "--candidate-lock-sha256",
                lock_digest,
                "--candidate-stack-contract-sha256",
                stack_digest,
                "--qualification-ref",
                qualification_ref,
                "--scripts-commit",
                scripts_commit,
            ]
            for platform_receipt in platform_receipts:
                aggregate_arguments.extend(
                    ("--platform-receipt", platform_receipt)
                )
            aggregate_arguments.extend(("--receipt", qualification_receipt))
            aggregated = run(*aggregate_arguments)
            self.assertEqual(aggregated.returncode, 0, aggregated.stderr)
            qualified = run(
                *base_arguments,
                "verify-qualification",
                "--candidate",
                candidate_receipt,
                "--qualification",
                qualification_receipt,
                "--scripts-commit",
                scripts_commit,
                "--github-run-id",
                "12345",
            )
            self.assertEqual(qualified.returncode, 0, qualified.stderr)
            aggregate_document = json.loads(
                qualification_receipt.read_text(encoding="utf-8")
            )
            original_aggregate = json.dumps(aggregate_document) + "\n"
            aggregate_document["platform_receipts"][0]["receipt"][
                "acceptance_receipt_digest"
            ] = "sha256:" + "0" * 64
            aggregate_document.pop("receipt_digest")
            aggregate_unsigned = json.dumps(
                aggregate_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            aggregate_document["receipt_digest"] = "sha256:" + hashlib.sha256(
                aggregate_unsigned
            ).hexdigest()
            qualification_receipt.write_text(
                json.dumps(aggregate_document) + "\n", encoding="utf-8"
            )
            tampered_embedded = run(
                *base_arguments,
                "verify-qualification",
                "--candidate",
                candidate_receipt,
                "--qualification",
                qualification_receipt,
            )
            self.assertEqual(tampered_embedded.returncode, 2)
            self.assertIn("embedded", tampered_embedded.stderr)
            qualification_receipt.write_text(original_aggregate, encoding="utf-8")
            changed_platform = json.loads(
                platform_receipts[0].read_text(encoding="utf-8")
            )
            changed_platform["github_run_id"] = "99999"
            changed_platform.pop("receipt_digest")
            unsigned = json.dumps(
                changed_platform,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            changed_platform["receipt_digest"] = "sha256:" + hashlib.sha256(
                unsigned
            ).hexdigest()
            platform_receipts[0].write_text(
                json.dumps(changed_platform) + "\n", encoding="utf-8"
            )
            mixed_run = run(*aggregate_arguments)
            self.assertEqual(mixed_run.returncode, 2)
            self.assertIn("another workflow run", mixed_run.stderr)

            original_stack_contract = candidate_stack_contract.read_text(
                encoding="utf-8"
            )
            candidate_stack_contract.write_text("{}\n", encoding="utf-8")
            changed_stack_contract = run(
                *base_arguments,
                "candidate-status",
                "--candidate",
                candidate_receipt,
            )
            self.assertEqual(changed_stack_contract.returncode, 2)
            self.assertIn("stack contract is missing or changed", changed_stack_contract.stderr)
            candidate_stack_contract.write_text(
                original_stack_contract, encoding="utf-8"
            )

            original_acceptance = acceptance_receipt.read_text(encoding="utf-8")
            acceptance_receipt.write_text("{}\n", encoding="utf-8")
            changed_evidence = run(
                *base_arguments,
                "candidate-status",
                "--candidate",
                candidate_receipt,
            )
            self.assertEqual(changed_evidence.returncode, 2)
            self.assertIn("missing or changed", changed_evidence.stderr)
            acceptance_receipt.write_text(original_acceptance, encoding="utf-8")

            (library / "later.txt").write_text("later\n", encoding="utf-8")
            git(library, "add", "later.txt")
            git(library, "commit", "--quiet", "-m", "later")
            moved_candidate = run(
                *base_arguments,
                "verify-candidate",
                "--candidate",
                candidate_receipt,
            )
            self.assertEqual(moved_candidate.returncode, 2)
            self.assertIn("does not match candidate", moved_candidate.stderr)
            git(library, "reset", "--hard", candidate_commit)

            document["does_not_prove"] = "tampered"
            candidate_receipt.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tampered = run(
                *base_arguments,
                "candidate-status",
                "--candidate",
                candidate_receipt,
            )
            self.assertEqual(tampered.returncode, 2)
            self.assertIn("candidate identity", tampered.stderr)

    def test_ephemeral_lock_admits_clean_ahead_commits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-pre-push-lock.") as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            workspace = scripts / "workspace"
            libraries = root / "libraries"
            remote = root / "remote.git"
            consumer = libraries / "lib_one"
            workspace.mkdir(parents=True)
            libraries.mkdir()
            shutil.copy2(LOCK_TOOL, workspace / "repos-lock.py")

            self.assertEqual(run("git", "init", "--quiet", "--bare", remote).returncode, 0)
            self.assertEqual(run("git", "clone", "--quiet", remote, consumer).returncode, 0)
            git(consumer, "config", "user.name", "p101 pre-push test")
            git(consumer, "config", "user.email", "pre-push@invalid.example")
            (consumer / "value.txt").write_text("published\n", encoding="utf-8")
            git(consumer, "add", "value.txt")
            git(consumer, "commit", "--quiet", "-m", "published")
            git(consumer, "branch", "-M", "main")
            git(consumer, "push", "--quiet", "-u", "origin", "main")
            (consumer / "value.txt").write_text("candidate\n", encoding="utf-8")
            git(consumer, "add", "value.txt")
            git(consumer, "commit", "--quiet", "-m", "candidate")

            manifest = scripts / "repos.txt"
            lock = root / "candidate.lock"
            manifest.write_text(f"{remote}|../libraries/lib_one|c\n", encoding="utf-8")
            base_arguments = (
                workspace / "repos-lock.py",
                "--scripts-root",
                scripts,
                "--manifest",
                manifest,
            )
            strict = run(*base_arguments, "--lock", lock, "refresh", "--require-clean")
            self.assertEqual(strict.returncode, 2)
            self.assertIn("is not the configured upstream", strict.stderr)

            admitted = run(
                *base_arguments,
                "--lock",
                lock,
                "refresh",
                "--require-clean",
                "--allow-ahead",
            )
            self.assertEqual(admitted.returncode, 0, admitted.stderr)
            verified = run_with_env(
                {"P101_REPOS_LOCK": os.fspath(lock)},
                *base_arguments,
                "verify",
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)

            self.assertEqual(
                run(
                    "git",
                    "--git-dir",
                    remote,
                    "update-ref",
                    "-d",
                    "refs/heads/main",
                ).returncode,
                0,
            )
            git(consumer, "fetch", "--prune", "origin")
            gone_upstream = run(
                *base_arguments,
                "--lock",
                lock,
                "refresh",
                "--require-clean",
                "--allow-ahead",
            )
            self.assertEqual(gone_upstream.returncode, 0, gone_upstream.stderr)

    def test_lock_is_deterministic_verifiable_and_used_by_clone(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-repository-lock.") as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            workspace = scripts / "workspace"
            distribution = scripts / "distribution"
            libraries = root / "libraries"
            remote = root / "remote.git"
            publisher = root / "publisher"
            consumer = libraries / "lib_one"
            workspace.mkdir(parents=True)
            distribution.mkdir()
            libraries.mkdir()
            shutil.copy2(LOCK_TOOL, workspace / "repos-lock.py")
            shutil.copy2(CLONE_TOOL, distribution / "clone-repos.sh")
            shutil.copy2(REFRESH_TOOL, distribution / "refresh-repo.sh")

            self.assertEqual(run("git", "init", "--quiet", "--bare", remote).returncode, 0)
            self.assertEqual(run("git", "init", "--quiet", publisher).returncode, 0)
            git(publisher, "config", "user.name", "p101 lock test")
            git(publisher, "config", "user.email", "lock-test@invalid.example")
            (publisher / "value.txt").write_text("one\n", encoding="utf-8")
            git(publisher, "add", "value.txt")
            git(publisher, "commit", "--quiet", "-m", "one")
            git(publisher, "branch", "-M", "main")
            git(publisher, "remote", "add", "origin", str(remote))
            git(publisher, "push", "--quiet", "-u", "origin", "main")
            run("git", "--git-dir", remote, "symbolic-ref", "HEAD", "refs/heads/main")
            self.assertEqual(run("git", "clone", "--quiet", remote, consumer).returncode, 0)

            manifest = scripts / "repos.txt"
            lock = scripts / "repos.lock"
            receipt = root / "receipt.json"
            manifest.write_text(f"{remote}|../libraries/lib_one|c\n", encoding="utf-8")
            base_arguments = (
                workspace / "repos-lock.py",
                "--scripts-root",
                scripts,
                "--manifest",
                manifest,
                "--lock",
                lock,
            )
            refreshed = run(*base_arguments, "refresh")
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            first_lock = lock.read_bytes()
            self.assertEqual(run(*base_arguments, "refresh").returncode, 0)
            self.assertEqual(lock.read_bytes(), first_lock)

            verified = run(*base_arguments, "verify", "--receipt", receipt)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            document = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertTrue(document["passed"])
            self.assertEqual(document["repository_count"], 1)
            locked_commit = git(consumer, "rev-parse", "HEAD")

            (publisher / "value.txt").write_text("two\n", encoding="utf-8")
            git(publisher, "add", "value.txt")
            git(publisher, "commit", "--quiet", "-m", "two")
            git(publisher, "push", "--quiet")
            shutil.rmtree(consumer)

            cloned = run(distribution / "clone-repos.sh")
            self.assertEqual(cloned.returncode, 0, cloned.stderr)
            self.assertEqual(git(consumer, "rev-parse", "HEAD"), locked_commit)
            detached = run(
                "git", "-C", consumer, "symbolic-ref", "--quiet", "--short", "HEAD"
            )
            self.assertNotEqual(detached.returncode, 0)

            git(consumer, "config", "user.name", "p101 lock test")
            git(consumer, "config", "user.email", "lock-test@invalid.example")
            git(consumer, "checkout", "--quiet", "-b", "local-ahead")
            (consumer / "value.txt").write_text("local commit\n", encoding="utf-8")
            git(consumer, "add", "value.txt")
            git(consumer, "commit", "--quiet", "-m", "local ahead")
            (consumer / "value.txt").write_text("local\n", encoding="utf-8")
            before_head = git(consumer, "rev-parse", "HEAD")
            before_status = git(consumer, "status", "--porcelain=v1")
            summarized = run(distribution / "clone-repos.sh")
            self.assertEqual(summarized.returncode, 1, summarized.stdout + summarized.stderr)
            self.assertIn("modified worktree prevents locked revision alignment", summarized.stderr)
            self.assertIn(" M value.txt", summarized.stderr)
            aborted = run_with_input(
                "q\n", distribution / "clone-repos.sh", "--interactive"
            )
            self.assertEqual(aborted.returncode, 3, aborted.stdout + aborted.stderr)
            self.assertIn("FAILED: align", aborted.stderr)
            self.assertIn("Aborting at locked repository alignment", aborted.stderr)
            self.assertIn("rerun the calling command with --latest", aborted.stderr)
            self.assertEqual(git(consumer, "rev-parse", "HEAD"), before_head)
            self.assertEqual(git(consumer, "status", "--porcelain=v1"), before_status)

            git(consumer, "reset", "--hard", locked_commit)
            (consumer / "value.txt").write_text("local\n", encoding="utf-8")
            dirty = run(*base_arguments, "verify", "--receipt", receipt)
            self.assertEqual(dirty.returncode, 0, dirty.stderr)
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))[
                    "dirty_repository_count"
                ],
                1,
            )
            strict_clean = run(*base_arguments, "verify", "--require-clean")
            self.assertEqual(strict_clean.returncode, 1)
            self.assertIn("worktree is not clean", strict_clean.stderr)

            manifest.write_text(
                f"{remote}|../libraries/lib_one|c\n# changed manifest\n",
                encoding="utf-8",
            )
            stale = run(*base_arguments, "entries")
            self.assertEqual(stale.returncode, 2)
            self.assertIn("manifest digest does not match", stale.stderr)

    def test_empty_bootstrap_repository_is_lockable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-bootstrap-lock.") as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            workspace = scripts / "workspace"
            distribution = scripts / "distribution"
            libraries = root / "libraries"
            remote = root / "bootstrap.git"
            consumer = libraries / "lib_bootstrap"
            workspace.mkdir(parents=True)
            distribution.mkdir()
            libraries.mkdir()
            shutil.copy2(LOCK_TOOL, workspace / "repos-lock.py")
            shutil.copy2(CLONE_TOOL, distribution / "clone-repos.sh")
            shutil.copy2(REFRESH_TOOL, distribution / "refresh-repo.sh")

            self.assertEqual(run("git", "init", "--quiet", "--bare", remote).returncode, 0)
            self.assertEqual(
                run("git", "clone", "--quiet", remote, consumer).returncode,
                0,
            )
            manifest = scripts / "repos.txt"
            lock = scripts / "repos.lock"
            manifest.write_text(
                f"{remote}|../libraries/lib_bootstrap|c-bootstrap\n",
                encoding="utf-8",
            )
            base_arguments = (
                workspace / "repos-lock.py",
                "--scripts-root",
                scripts,
                "--manifest",
                manifest,
                "--lock",
                lock,
            )
            refreshed = run(*base_arguments, "refresh")
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            row = json.loads(lock.read_text(encoding="utf-8"))["repositories"][0]
            self.assertEqual(row["commit"], "")
            self.assertEqual(run(*base_arguments, "verify").returncode, 0)

            shutil.rmtree(consumer)
            cloned = run(distribution / "clone-repos.sh")
            self.assertEqual(cloned.returncode, 0, cloned.stderr)
            self.assertNotEqual(
                run("git", "-C", consumer, "rev-parse", "--verify", "HEAD").returncode,
                0,
            )

    def test_repository_path_must_stay_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-lock-path.") as temporary:
            root = Path(temporary)
            scripts = root / "workspace-root" / "scripts"
            scripts.mkdir(parents=True)
            manifest = scripts / "repos.txt"
            lock = scripts / "repos.lock"
            manifest.write_text(
                "https://invalid.example/repo.git|../../outside|c\n",
                encoding="utf-8",
            )
            attempted = run(
                LOCK_TOOL,
                "--scripts-root",
                scripts,
                "--manifest",
                manifest,
                "--lock",
                lock,
                "refresh",
            )
            self.assertEqual(attempted.returncode, 2)
            self.assertIn("repository path escapes workspace", attempted.stderr)


if __name__ == "__main__":
    unittest.main()
