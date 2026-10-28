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


def list_files(repo: Path, ref: str) -> list[str]:
    """List every file committed on `ref`.

    Returns:
        Their paths, relative to the repo root.
    """
    out = _run(repo, "ls-tree", "-r", "-z", "--name-only", ref).stdout
    return [path for path in out.split("\0") if path]


def show_file_at_ref(repo: Path, ref: str, file_path: str) -> str | None:
    result = _run(repo, "show", f"{ref}:{file_path}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def worktrees(repo: Path) -> dict[str, Path]:
    """Map each branch checked out anywhere (main checkout included) to its path.

    A worktree in the middle of a rebase is detached, but still counts as
    holding the branch being rebased.

    Returns:
        A `{branch: worktree path}` dict, skipping other detached HEADs and
        worktrees whose directory no longer exists.
    """
    out = _run(repo, "worktree", "list", "--porcelain").stdout
    result: dict[str, Path] = {}
    for block in out.split("\n\n"):
        fields = {
            key: value
            for key, _, value in (line.partition(" ") for line in block.splitlines())
        }
        if "worktree" not in fields:
            continue
        path = Path(fields["worktree"])
        if "prunable" in fields or not path.is_dir():
            continue
        branch = fields.get("branch") or _rebasing_branch(path)
        if branch:
            result[branch.removeprefix("refs/heads/")] = path
    return result


def _rebasing_branch(checkout: Path) -> str | None:
    for name in ("rebase-merge", "rebase-apply"):
        git_path = _run(checkout, "rev-parse", "--git-path", f"{name}/head-name")
        head_name = checkout / git_path.stdout.strip()
        if head_name.is_file():
            return head_name.read_text().strip()
    return None


def worktree_add(repo: Path, path: Path, branch: str) -> None:
    _run(repo, "worktree", "prune")
    _run(repo, "worktree", "add", "--quiet", str(path), branch)


def worktree_remove(repo: Path, path: Path) -> None:
    _run(repo, "worktree", "remove", "--force", str(path))


def rev_parse(repo: Path, ref: str) -> str:
    return _run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    args = ["merge-base", "--is-ancestor", ancestor, descendant]
    return _run(repo, *args, check=False).returncode == 0


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


def fork_point(repo: Path, parent: str, branch: str) -> str | None:
    """Where `branch` forked from `parent`, even if `parent` was rewritten since.

    Uses `parent`'s reflog, which records its tips before any rebase.

    Returns:
        The fork point's sha, or `None` if the reflog doesn't tell.
    """
    result = _run(repo, "merge-base", "--fork-point", parent, branch, check=False)
    return result.stdout.strip() or None


def rebase(checkout: Path, onto: str, upstream: str | None = None) -> bool:
    """Rebase the checked-out branch onto `onto`.

    Args:
        checkout: Where the branch to rebase is checked out.
        onto: What to rebase it onto.
        upstream: Only replay the commits after this one (`--onto` form);
            by default, those not in `onto`.

    Returns:
        True if it completed, False if it stopped (normally on conflicts).
    """
    args = ["--onto", onto, upstream] if upstream else [onto]
    return _run(checkout, "rebase", "--quiet", *args, check=False).returncode == 0


def rebase_onto(checkout: Path) -> str | None:
    """The commit a stopped rebase is replaying onto, if one is in progress.

    Returns:
        The commit's sha, or `None`.
    """
    for name in ("rebase-merge", "rebase-apply"):
        git_path = _run(checkout, "rev-parse", "--git-path", f"{name}/onto").stdout
        onto = checkout / git_path.strip()
        if onto.is_file():
            return onto.read_text().strip()
    return None


def rebase_continue(checkout: Path) -> bool:
    """Continue a stopped rebase, keeping each commit message as is.

    A commit whose changes all turned out to be upstream already (nothing
    left staged once its conflicts are resolved) is skipped instead.

    Returns:
        True if the rebase completed, False if it stopped again.
    """
    nothing_staged = _run(checkout, "diff", "--cached", "--quiet", check=False)
    action = "--skip" if nothing_staged.returncode == 0 else "--continue"
    result = _run(checkout, "rebase", action, check=False, env={"GIT_EDITOR": "true"})
    return result.returncode == 0


def differs_from_head(checkout: Path, file_path: str) -> bool:
    """Whether `file_path` (relative to `checkout`) differs from HEAD's.

    Returns:
        True if it does, staged or not.
    """
    result = _run(checkout, "diff", "--quiet", "HEAD", "--", file_path, check=False)
    return result.returncode != 0


def is_tracked(checkout: Path, file_path: str) -> bool:
    result = _run(checkout, "ls-files", "--error-unmatch", "--", file_path, check=False)
    return result.returncode == 0


def checkout_ours(checkout: Path, file_path: str) -> None:
    """Write HEAD's side of a conflicted file (the new base, during a rebase).

    The file stays unmerged until it's staged.
    """
    _run(checkout, "checkout", "--ours", "--", file_path)


def take_ours(checkout: Path, file_path: str) -> None:
    """Resolve a conflict with HEAD's version (the new base, during a rebase)."""
    checkout_ours(checkout, file_path)
    stage(checkout, file_path)


def rebase_abort(checkout: Path) -> None:
    _run(checkout, "rebase", "--abort")


def discard_changes(checkout: Path) -> None:
    """Reset tracked files to HEAD and delete untracked (non-ignored) files."""
    _run(checkout, "reset", "--hard", "--quiet", "HEAD")
    _run(checkout, "clean", "-fd", "--quiet")


def fetch(repo: Path, remote: str | None = None) -> None:
    args = [remote] if remote else ["--all", "--tags"]
    _run(repo, "fetch", "--quiet", *args, check=False)


def upstream(repo: Path, branch: str) -> tuple[str, str] | None:
    """The remote and remote branch that `branch` tracks.

    Returns:
        `(remote, branch on the remote)`, or `None` if it tracks nothing.
    """
    remote = _run(repo, "config", f"branch.{branch}.remote", check=False).stdout
    merge = _run(repo, "config", f"branch.{branch}.merge", check=False).stdout
    if not remote.strip() or not merge.strip() or remote.strip() == ".":
        return None
    return remote.strip(), merge.strip().removeprefix("refs/heads/")


def resolve(repo: Path, ref: str) -> str | None:
    result = _run(repo, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False)
    return result.stdout.strip() or None


def reflog(repo: Path, branch: str) -> set[str]:
    """Every commit `branch` has pointed to, as far as its reflog goes.

    Returns:
        The commits' shas.
    """
    return {sha for sha, _ in reflog_entries(repo, branch)}


def reflog_entries(repo: Path, branch: str) -> list[tuple[str, int]]:
    """When `branch` moved to each commit it pointed to, newest first.

    Returns:
        `(sha, unix time)` pairs.
    """
    out = _run(
        repo,
        "reflog",
        "show",
        "--date=unix",
        "--format=%H %gd",
        f"refs/heads/{branch}",
        check=False,
    ).stdout
    entries = []
    for line in out.splitlines():
        sha, _, selector = line.partition(" ")  # selector: `branch@{<time>}`
        if sha:
            entries.append((sha, int(selector.rpartition("@{")[2].rstrip("}"))))
    return entries


def push(
    repo: Path, remote: str, branch: str, remote_branch: str, lease: str | None = None
) -> None:
    """Push `branch` to `remote_branch`.

    With `lease`, force the push, but only over that commit.
    """
    args = ["push", "--quiet"]
    if lease is not None:
        args.append(f"--force-with-lease=refs/heads/{remote_branch}:{lease}")
    _run(repo, *args, remote, f"refs/heads/{branch}:refs/heads/{remote_branch}")


def stage_all(checkout: Path) -> None:
    _run(checkout, "add", "-A")


def stage(checkout: Path, file_path: str) -> None:
    _run(checkout, "add", "--", file_path)


def commit(checkout: Path, message: str, verify: bool = True) -> None:
    args = ["commit", "--quiet", "-m", message]
    if not verify:
        args.append("--no-verify")
    _run(checkout, *args)


def log_entries(repo: Path, revision_range: str) -> list[tuple[str, str, str]]:
    """List commits in `revision_range`, newest first.

    Returns:
        `(short sha, subject, ref names)` tuples, ref names as `%D` prints them.
    """
    out = _run(repo, "log", "--format=%h%x00%s%x00%D", revision_range).stdout
    entries = []
    for line in out.splitlines():
        sha, subject, refs = line.split("\0")
        entries.append((sha, subject, refs))
    return entries


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
