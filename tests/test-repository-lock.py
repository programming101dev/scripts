#!/usr/bin/env python3
"""Regression tests for exact-revision workspace locking."""

from __future__ import annotations

import json
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


def run(*arguments: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin"},
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
            "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
            **environment,
        },
    )


def git(repository: Path, *arguments: str) -> str:
    completed = run("git", "-C", repository, *arguments)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


class RepositoryLockTests(unittest.TestCase):
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
