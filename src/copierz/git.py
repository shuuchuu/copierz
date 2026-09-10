"""Thin wrapper around the `git` CLI for the operations copierz needs."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class GitError(RuntimeError):
    """A `git` invocation failed."""


def _run(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        msg = f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}"
        raise GitError(msg)
    return result


def is_git_repo(path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def current_branch(repo: Path) -> str:
    return _run(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def list_local_branches(repo: Path) -> list[str]:
    out = _run(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def is_clean(repo: Path) -> bool:
    return _run(repo, "status", "--porcelain").stdout.strip() == ""


def unmerged_files(repo: Path) -> list[str]:
    out = _run(repo, "diff", "--name-only", "--diff-filter=U").stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def changed_files(repo: Path) -> list[tuple[str, str]]:
    """Return `(status_code, path)` pairs, as reported by `git status --porcelain`."""
    out = _run(repo, "status", "--porcelain").stdout
    result = []
    for line in out.splitlines():
        if not line:
            continue
        result.append((line[:2], line[3:]))
    return result


def fetch(repo: Path) -> None:
    _run(repo, "fetch", "--all", "--tags", check=False)


def checkout(repo: Path, branch: str) -> None:
    _run(repo, "checkout", branch)


def show_file_at_ref(repo: Path, ref: str, file_path: str) -> str | None:
    result = _run(repo, "show", f"{ref}:{file_path}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def stage(repo: Path, file_path: str) -> None:
    _run(repo, "add", "--", file_path)


def stage_all(repo: Path) -> None:
    _run(repo, "add", "-A")


def commit(repo: Path, message: str) -> None:
    _run(repo, "commit", "-m", message)


def diff_for_file(repo: Path, file_path: str) -> str:
    return _run(repo, "diff", "HEAD", "--", file_path, check=False).stdout
