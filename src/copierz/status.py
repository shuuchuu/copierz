"""Compute the copier-update status of discovered repo branches."""

from __future__ import annotations

from collections import defaultdict
from itertools import groupby
from typing import TYPE_CHECKING

from copierz import git
from copierz.models import BranchStatus, StatusKind
from copierz.stacks import find_parents, topological_order
from copierz.template_source import latest_template_ref

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from copierz.discovery import Target


class LatestRefs:
    """Cache each template's latest version, resolving it at most once."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[str | None, str]] = {}

    def get(self, template: Path) -> str:
        """Return the latest version of `template`.

        Raises:
            GitError: if the template's versions can't be read.
        """
        if template not in self._cache:
            try:
                self._cache[template] = (latest_template_ref(template), "")
            except git.GitError as exc:
                self._cache[template] = (None, str(exc))
        ref, error = self._cache[template]
        if ref is None:
            raise git.GitError(error)
        return ref


def compute_statuses(
    targets: list[Target], latest: LatestRefs | None = None
) -> list[BranchStatus]:
    """Compute every target's status, grouped by repo, parents before children.

    Returns:
        One status per target.
    """
    latest = latest or LatestRefs()
    statuses = []
    for _, repo_targets in groupby(targets, key=lambda t: t.repo):
        statuses.extend(repo_statuses(list(repo_targets), latest))
    return statuses


def repo_statuses(targets: list[Target], latest: LatestRefs) -> list[BranchStatus]:
    """Compute the statuses of targets that all belong to the same repo.

    Stacks are made of branches, whatever projects they carry: a stopped
    rebase shows on the branch's first project (its root one, usually).

    Returns:
        One status per target, each parent branch before its children, and
        a branch's projects in their original order.
    """
    repo = targets[0].repo
    by_branch: dict[str, list[Target]] = defaultdict(list)
    for target in targets:
        by_branch[target.branch].append(target)
    answers_files = sorted({t.answers_file for t in targets})
    parents = find_parents(repo, list(by_branch), answers_files)
    checkouts = git.worktrees(repo)
    tips = {git.rev_parse(repo, branch): branch for branch in by_branch}
    for branch, checkout in checkouts.items():
        # Mid-rebase, the branch still points at its old commits: what it's
        # being rebased onto is the reliable parent.
        if branch in by_branch and (onto := git.rebase_onto(checkout)) in tips:
            parents[branch] = tips[onto]
    statuses = []
    for branch in topological_order(parents):
        projects = by_branch[branch]
        subdirs = [t.subdir for t in projects]
        statuses.extend(
            branch_status(
                target,
                parents[branch],
                checkouts.get(branch),
                latest,
                subdirs,
                rebase_shown=target is projects[0],
            )
            for target in projects
        )
    return statuses


def _owner(path: str, subdirs: Iterable[str]) -> str:
    """Find the project `path` (relative to the repo root) belongs to.

    Returns:
        The directory of the deepest project holding it.
    """
    containing = [s for s in subdirs if not s or path.startswith(f"{s}/")]
    return max(containing, key=len, default="")


def _stopped_status(
    target: Target,
    parent: str | None,
    checkout: Path,
    rebasing: bool,
    subdirs: Iterable[str],
) -> tuple[StatusKind, str, list[str]] | None:
    """Tell whether an update or rebase of `target` stopped in `checkout`.

    Returns:
        The status kind, detail and unmerged files if one did, else `None`.
    """
    unmerged = git.unmerged_files(checkout)
    if rebasing:
        return StatusKind.CONFLICTS, f"rebase onto {parent} stopped", unmerged
    subdirs = list(subdirs)
    unmerged = [f for f in unmerged if _owner(f, subdirs) == target.subdir]
    if unmerged:
        return StatusKind.CONFLICTS, f"in {', '.join(unmerged)}", unmerged
    if target.answers_file in git.changed_vs_head(checkout):
        return StatusKind.IN_PROGRESS, "update not committed", []
    return None


def branch_status(
    target: Target,
    parent: str | None,
    checkout: Path | None,
    latest: LatestRefs,
    subdirs: Iterable[str] = ("",),
    rebase_shown: bool = True,
) -> BranchStatus:
    """Compute the status of one project on one branch.

    Args:
        target: The project.
        parent: The branch its branch is stacked on, if any.
        checkout: Where its branch is checked out, if anywhere.
        latest: The templates' latest versions.
        subdirs: Every project of the branch, `target`'s included: a conflict
            counts against the project whose directory holds the file.
        rebase_shown: Whether a stopped rebase of the branch shows on this
            project, or blocks it.

    Returns:
        The project's status.
    """

    def status(
        kind: StatusKind,
        detail: str = "",
        ref: str | None = None,
        unmerged: Iterable[str] = (),
    ) -> BranchStatus:
        return BranchStatus(
            target, kind, parent, checkout, ref, detail, tuple(unmerged)
        )

    if not target.template.is_dir():
        return status(StatusKind.ERROR, f"template not found at {target.template}")
    try:
        latest_ref = latest.get(target.template)
    except git.GitError as exc:
        return status(StatusKind.ERROR, str(exc))

    rebasing = checkout is not None and git.is_rebasing(checkout)
    if checkout is not None and (not rebasing or rebase_shown):
        stopped = _stopped_status(target, parent, checkout, rebasing, subdirs)
        if stopped is not None:
            kind, detail, unmerged = stopped
            return status(kind, detail, latest_ref, unmerged)

    if target.answers.commit == latest_ref:
        return status(StatusKind.UP_TO_DATE, ref=latest_ref)
    if rebasing:
        return status(
            StatusKind.BLOCKED, f"waiting for the rebase onto {parent}", latest_ref
        )
    if checkout is not None and not git.is_clean(checkout):
        return status(StatusKind.BLOCKED, "commit or stash first", latest_ref)
    detail = f"rebase onto {parent}" if parent else ""
    return status(StatusKind.BEHIND, detail, latest_ref)
