"""Compute the copier-update status of discovered repo branches."""

from __future__ import annotations

from itertools import groupby
from typing import TYPE_CHECKING

from copierz import git
from copierz.models import BranchStatus, StatusKind
from copierz.stacks import find_parents, topological_order
from copierz.template_source import ANSWERS_FILE, latest_template_ref

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

    Returns:
        One status per target, each parent branch before its children.
    """
    repo = targets[0].repo
    by_branch = {t.branch: t for t in targets}
    parents = find_parents(repo, list(by_branch))
    checkouts = git.worktrees(repo)
    return [
        branch_status(by_branch[branch], parents[branch], checkouts.get(branch), latest)
        for branch in topological_order(parents)
    ]


def branch_status(
    target: Target, parent: str | None, checkout: Path | None, latest: LatestRefs
) -> BranchStatus:
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

    if checkout is not None:
        unmerged = git.unmerged_files(checkout)
        if git.is_rebasing(checkout):
            return status(
                StatusKind.CONFLICTS,
                f"rebase onto {parent} stopped",
                latest_ref,
                unmerged,
            )
        if unmerged:
            return status(
                StatusKind.CONFLICTS, f"in {', '.join(unmerged)}", latest_ref, unmerged
            )
        if ANSWERS_FILE in git.changed_vs_head(checkout):
            return status(StatusKind.IN_PROGRESS, "update not committed", latest_ref)

    if target.answers.commit == latest_ref:
        return status(StatusKind.UP_TO_DATE, ref=latest_ref)
    if checkout is not None and not git.is_clean(checkout):
        return status(StatusKind.BLOCKED, "commit or stash first", latest_ref)
    detail = f"rebase onto {parent}" if parent else ""
    return status(StatusKind.BEHIND, detail, latest_ref)
