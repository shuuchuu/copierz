"""Thin wrapper around the `git` CLI for the operations copierz needs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A `git` invocation failed."""


def _run(
    repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env} if env else None,
    )
    if check and result.returncode != 0:
        msg = f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}"
        raise GitError(msg)
    return result


def _lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def is_repo_root(path: Path) -> bool:
    """Whether `path` is the root of a repository, not one of its linked worktrees.

    Linked worktrees (copierz's own, or any the user made) share their
    branches with the main checkout, so they must not be discovered as
    separate repos.

    Returns:
        True for a directory holding a `.git` directory, or a `.git` file
        that doesn't point into another repo's `worktrees/`.
    """
    dot_git = path / ".git"
    if dot_git.is_dir():
        return True
    if dot_git.is_file():
        return "/worktrees/" not in dot_git.read_text()
    return False


def is_git_repo(path: Path) -> bool:
    return _run(path, "rev-parse", "--git-dir", check=False).returncode == 0


def list_local_branches(repo: Path) -> list[str]:
    out = _run(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").stdout
    return _lines(out)


def show_file_at_ref(repo: Path, ref: str, file_path: str) -> str | None:
    result = _run(repo, "show", f"{ref}:{file_path}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def worktrees(repo: Path) -> dict[str, Path]:
    """Map each branch checked out anywhere (main checkout included) to its path.

    Returns:
        A `{branch: worktree path}` dict, skipping detached HEADs and
        worktrees whose directory no longer exists.
    """
    out = _run(repo, "worktree", "list", "--porcelain").stdout
    result: dict[str, Path] = {}
    for block in out.split("\n\n"):
        fields = dict(line.partition(" ")[::2] for line in block.splitlines())
        if "worktree" not in fields or "branch" not in fields:
            continue
        path = Path(fields["worktree"])
        if "prunable" in fields or not path.is_dir():
            continue
        result[fields["branch"].removeprefix("refs/heads/")] = path
    return result


def worktree_add(repo: Path, path: Path, branch: str) -> None:
    _run(repo, "worktree", "prune")
    _run(repo, "worktree", "add", "--quiet", str(path), branch)


def worktree_remove(repo: Path, path: Path) -> None:
    _run(repo, "worktree", "remove", "--force", str(path))


def rev_parse(repo: Path, ref: str) -> str:
    return _run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def merge_base(repo: Path, a: str, b: str) -> str | None:
    result = _run(repo, "merge-base", a, b, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def count_commits(repo: Path, revision_range: str, *paths: str) -> int:
    args = ["rev-list", "--count", revision_range]
    if paths:
        args += ["--", *paths]
    return int(_run(repo, *args).stdout.strip())


def is_clean(checkout: Path) -> bool:
    return _run(checkout, "status", "--porcelain").stdout.strip() == ""


def status_short(checkout: Path) -> str:
    return _run(checkout, "status", "--short").stdout


def unmerged_files(checkout: Path) -> list[str]:
    return _lines(_run(checkout, "diff", "--name-only", "--diff-filter=U").stdout)


def changed_vs_head(checkout: Path) -> list[str]:
    """Tracked files whose working-tree content differs from HEAD (staged or not).

    Returns:
        Paths relative to the checkout root.
    """
    return _lines(_run(checkout, "diff", "HEAD", "--name-only").stdout)


def is_rebasing(checkout: Path) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        git_path = _run(checkout, "rev-parse", "--git-path", name).stdout.strip()
        if (checkout / git_path).exists():
            return True
    return False


def rebase(checkout: Path, onto: str) -> bool:
    """Rebase the checked-out branch onto `onto`.

    Returns:
        True if it completed, False if it stopped (normally on conflicts).
    """
    return _run(checkout, "rebase", "--quiet", onto, check=False).returncode == 0


def rebase_continue(checkout: Path) -> bool:
    """Continue a stopped rebase, keeping each commit message as is.

    Returns:
        True if the rebase completed, False if it stopped again.
    """
    result = _run(
        checkout, "rebase", "--continue", check=False, env={"GIT_EDITOR": "true"}
    )
    return result.returncode == 0


def rebase_abort(checkout: Path) -> None:
    _run(checkout, "rebase", "--abort")


def discard_changes(checkout: Path) -> None:
    """Reset tracked files to HEAD and delete untracked (non-ignored) files."""
    _run(checkout, "reset", "--hard", "--quiet", "HEAD")
    _run(checkout, "clean", "-fd", "--quiet")


def fetch(repo: Path) -> None:
    _run(repo, "fetch", "--all", "--tags", check=False)


def stage_all(checkout: Path) -> None:
    _run(checkout, "add", "-A")


def commit(checkout: Path, message: str, verify: bool = True) -> None:
    args = ["commit", "--quiet", "-m", message]
    if not verify:
        args.append("--no-verify")
    _run(checkout, *args)


def log_oneline(repo: Path, revision_range: str) -> str:
    return _run(repo, "log", "--oneline", "--decorate", revision_range).stdout


def difftool_dir_diff(checkout: Path) -> None:
    """Launch the user's configured `git difftool` in whole-repo dir-diff mode.

    Compares the working tree against HEAD as two directories in a single
    tool invocation. If the tool supports editing (meld, vscode's diff
    editor), edits made there are copied back into the working tree once
    the tool exits.
    """
    subprocess.run(
        ["git", "-C", str(checkout), "difftool", "--dir-diff", "-y", "HEAD"],
        check=False,
    )


def mergetool(checkout: Path) -> None:
    """Launch the user's configured `git mergetool` on every unmerged file.

    Blocks until the tool is closed. Presents a proper 3-way merge and
    marks each file resolved (staged) once its result is saved.
    """
    subprocess.run(["git", "-C", str(checkout), "mergetool", "-y"], check=False)
