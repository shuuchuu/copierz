"""Work out which branches of a repo are stacked on which.

Several repos keep dependent branches (`solution` on `main`, `main-en` as a
translation commit on top of `main`, ...) rebased onto a parent branch. A
template update must then go through `copier update` on the bottom branch
only, and reach the others by rebasing them, or the stack falls apart into
divergent copies of the same update.

History alone can't tell a parent from a copy of a branch's own past tip (a
backup branch, a fresh branch with no commits yet): both are ancestors the
branch has commits on top of. Reflogs can, see `_copies`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from copierz import git
from copierz.template_source import ANSWERS_FILE

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _is_stacked_on(
    repo: Path, branch: str, parent: str, answers_files: Sequence[str]
) -> int | None:
    """Whether `branch` sits on top of `parent`, possibly pending a rebase.

    That's the case when `branch` has commits of its own past their merge
    base, and either `parent` has none past it except template updates
    (commits touching an answers file, the root one or a nested project's),
    or `parent` was rewritten (rebased) since `branch` forked from it — i.e.
    `branch` was in sync with `parent` until `parent` got updated.

    Returns:
        How deep the fork point is (higher means a closer parent), or `None`
        if `branch` isn't stacked on `parent`.
    """
    tip = git.rev_parse(repo, branch)
    base = git.merge_base(repo, branch, parent)
    if base is None or base == tip:
        return None
    parent_only = f"{base}..{parent}"
    if git.count_commits(repo, parent_only) == git.count_commits(
        repo, parent_only, *answers_files
    ):
        return git.count_commits(repo, base)
    # The reflog of `parent` knows its tips from before a rebase.
    fork = git.fork_point(repo, parent, branch)
    if fork is not None and fork != tip and not git.is_ancestor(repo, fork, parent):
        return git.count_commits(repo, fork)
    return None


def _copies(repo: Path, branches: list[str]) -> set[tuple[str, str]]:
    """Find the branches that are copies of another branch's past tip.

    `copy` is a copy of `branch` when `branch` was at `copy`'s tip before
    `copy` got there: `copy` followed `branch`, as a backup of it, or a
    branch made from it with no commits yet. `branch` isn't stacked on it,
    whatever the history says. A branch stacked on a parent is the other
    way round: it's created, reset or rebased onto the parent's tip after
    the parent got there. When both got there within the same second (a
    script moving them together), it's not a copy.

    Returns:
        `(copy, branch)` pairs.
    """
    entries = {branch: git.reflog_entries(repo, branch) for branch in branches}
    copies = set()
    for copy, copy_entries in entries.items():
        if not copy_entries:
            continue
        tip, copied_at = copy_entries[0]
        for branch, branch_entries in entries.items():
            arrivals = [time for sha, time in branch_entries if sha == tip]
            if branch != copy and arrivals and min(arrivals) < copied_at:
                copies.add((copy, branch))
    return copies


def find_parents(
    repo: Path, branches: list[str], answers_files: Sequence[str] = (ANSWERS_FILE,)
) -> dict[str, str | None]:
    """Map each branch to the branch it's stacked on, among `branches`.

    When a branch sits on several (e.g. `solution` on both `gui` and
    `main`, `gui` itself being on `main`), the closest one wins. A copy of
    the branch's own past tip is never its parent.

    Args:
        repo: The repo.
        branches: Its branches to consider.
        answers_files: The answers files of the repo's projects (the root
            one, and nested ones): commits touching them are template
            updates.

    Returns:
        `{branch: parent or None}` for every branch in `branches`.
    """
    copies = _copies(repo, branches)
    parents: dict[str, str | None] = {}
    for branch in branches:
        best: tuple[int, str] | None = None
        for candidate in branches:
            if candidate == branch or (candidate, branch) in copies:
                continue
            depth = _is_stacked_on(repo, branch, candidate, answers_files)
            if depth is not None and (best is None or depth > best[0]):
                best = (depth, candidate)
        parents[branch] = best[1] if best else None
    _break_cycles(parents)
    return parents


def _break_cycles(parents: dict[str, str | None]) -> None:
    # Two branches that each only got template updates since they forked
    # look stacked on each other; treat the first one by name as the root.
    for start in sorted(parents):
        path: list[str] = []
        branch: str | None = start
        while branch is not None and branch not in path:
            path.append(branch)
            branch = parents.get(branch)
        if branch is not None:
            cycle = path[path.index(branch) :]
            parents[min(cycle)] = None


def topological_order(parents: dict[str, str | None]) -> list[str]:
    """Order branches as a depth-first walk of their stacks.

    Returns:
        Every branch in `parents`, each one directly followed by the
        branches stacked on it (alphabetically at each level).
    """
    children: dict[str | None, list[str]] = {}
    for branch in sorted(parents):
        children.setdefault(parents[branch], []).append(branch)
    ordered: list[str] = []

    def visit(branch: str) -> None:
        ordered.append(branch)
        for child in children.get(branch, []):
            visit(child)

    for root in children.get(None, []):
        visit(root)
    return ordered


def depth(parents: dict[str, str | None], branch: str) -> int:
    level = 0
    while (parent := parents.get(branch)) is not None:
        level += 1
        branch = parent
    return level
