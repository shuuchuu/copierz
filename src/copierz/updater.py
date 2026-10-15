"""Bring repo branches up to date: `copier update` stack roots, rebase the rest.

Branches that aren't checked out anywhere are updated in a temporary git
worktree under copierz's cache directory, so your own checkout never
switches branch and a dirty `main` doesn't block its siblings. A worktree
is removed once its branch is committed; it stays (and shows up in
`copierz status`) when an update or rebase stops on conflicts.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import copier

from copierz import git
from copierz.models import StatusKind
from copierz.template_source import ANSWERS_FILE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from copierz.discovery import Target
    from copierz.models import BranchStatus


class OutcomeKind(Enum):
    UPDATED = "updated"
    REBASED = "rebased"
    UNCOMMITTED = "not committed"
    CONFLICTS = "conflicts"
    SKIPPED = "skipped"
    FAILED = "failed"

    @property
    def ok(self) -> bool:
        return self in {OutcomeKind.UPDATED, OutcomeKind.REBASED}


@dataclass
class Outcome:
    target: Target
    kind: OutcomeKind
    detail: str = ""
    checkout: Path | None = None


@contextlib.contextmanager
def _chdir(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def commit_message(template: Path, ref: str | None) -> str:
    name = template.name if "template" in template.name else f"{template.name} template"
    return f"Update {name} to {ref}" if ref else f"Update {name}"


class Updater:
    """Apply template updates to repo branches, one repo stack at a time."""

    def __init__(
        self,
        cache_dir: Path,
        commit: bool = True,
        verify: bool = True,
        report: Callable[[Outcome], None] | None = None,
    ) -> None:
        self.worktrees_dir = cache_dir / "worktrees"
        self.commit = commit
        self.verify = verify
        self.report = report or (lambda _: None)

    def update_repo(self, statuses: list[BranchStatus]) -> list[Outcome]:
        """Update the given branches of a single repo.

        Stack roots go through `copier update`; every other branch is
        rebased onto its parent once that parent is up to date. A branch
        that fails holds back the branches stacked on it.

        Args:
            statuses: Statuses of branches of one repo, parents first (as
                returned by `copierz.status.repo_statuses`).

        Returns:
            One outcome per branch that needed something done.
        """
        outcomes = []
        held_back: set[str] = set()
        for status in statuses:
            outcome = self._update_branch(status, held_back)
            if outcome is None:
                continue
            if not outcome.kind.ok:
                held_back.add(status.target.branch)
            self.report(outcome)
            outcomes.append(outcome)
        return outcomes

    def _update_branch(
        self, status: BranchStatus, held_back: set[str]
    ) -> Outcome | None:
        target, parent = status.target, status.parent
        if parent is not None and parent in held_back:
            return Outcome(target, OutcomeKind.SKIPPED, f"waiting for {parent}")
        if status.kind in {
            StatusKind.ERROR,
            StatusKind.BLOCKED,
            StatusKind.CONFLICTS,
            StatusKind.IN_PROGRESS,
        }:
            return Outcome(
                target, OutcomeKind.SKIPPED, f"{status.kind.value}: {status.detail}"
            )
        if parent is None:
            if status.kind is StatusKind.UP_TO_DATE:
                return None
            return self._copier_update(status)
        repo = target.repo
        if git.merge_base(repo, parent, target.branch) == git.rev_parse(repo, parent):
            if status.kind is StatusKind.UP_TO_DATE:
                return None
            return Outcome(
                target,
                OutcomeKind.FAILED,
                f"stacked on {parent} but behind it; update it by hand",
            )
        return self.rebase(target, parent)

    def checkout(self, target: Target) -> Path:
        """Where `target`'s branch is checked out, adding a worktree if nowhere.

        Returns:
            The path of the main checkout or worktree holding the branch.
        """
        existing = git.worktrees(target.repo).get(target.branch)
        if existing is not None:
            return existing
        repo_key = hashlib.sha1(str(target.repo).encode()).hexdigest()[:8]
        path = (
            self.worktrees_dir
            / f"{target.repo.name}-{repo_key}"
            / target.branch.replace("/", "--")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        git.worktree_add(target.repo, path, target.branch)
        return path

    def owns(self, checkout: Path) -> bool:
        return checkout.is_relative_to(self.worktrees_dir)

    def release(self, target: Target, checkout: Path) -> None:
        """Remove `checkout` if it's a worktree copierz created."""
        if self.owns(checkout):
            git.worktree_remove(target.repo, checkout)

    def _copier_update(self, status: BranchStatus) -> Outcome:
        target = status.target
        checkout = self.checkout(target)
        if not git.is_clean(checkout):
            return Outcome(target, OutcomeKind.SKIPPED, "dirty", checkout)
        try:
            with _chdir(target.workspace_root):
                copier.run_update(
                    dst_path=checkout,
                    defaults=True,
                    skip_answered=True,
                    overwrite=True,
                    conflict="inline",
                    quiet=True,
                )
        except Exception as exc:  # Copier raises many unrelated types
            git.discard_changes(checkout)
            self.release(target, checkout)
            return Outcome(target, OutcomeKind.FAILED, f"copier update: {exc}")

        if unmerged := git.unmerged_files(checkout):
            return Outcome(
                target, OutcomeKind.CONFLICTS, f"in {', '.join(unmerged)}", checkout
            )
        return self.finish(status, checkout)

    def finish(self, status: BranchStatus, checkout: Path) -> Outcome:
        """Commit an applied (conflict-free) update, then drop its worktree.

        Returns:
            UPDATED, or UNCOMMITTED if committing is disabled or failed (e.g.
            on a pre-commit hook).
        """
        target = status.target
        versions = f"{status.current_ref} → {status.latest_ref}"
        if not self.commit:
            return Outcome(target, OutcomeKind.UNCOMMITTED, versions, checkout)
        git.stage_all(checkout)
        try:
            git.commit(
                checkout,
                commit_message(target.template, status.latest_ref),
                verify=self.verify,
            )
        except git.GitError as exc:
            return Outcome(
                target, OutcomeKind.UNCOMMITTED, f"commit failed: {exc}", checkout
            )
        self.release(target, checkout)
        return Outcome(target, OutcomeKind.UPDATED, versions)

    def rebase(self, target: Target, parent: str) -> Outcome:
        checkout = self.checkout(target)
        if not git.is_clean(checkout):
            return Outcome(target, OutcomeKind.SKIPPED, "dirty", checkout)
        # If the parent was itself just rebased, a plain `git rebase parent`
        # would also replay the parent's old commits under the branch: only
        # replay the branch's own commits, from where it forked.
        upstream = git.fork_point(target.repo, parent, target.branch)
        if git.rebase(checkout, parent, upstream) or self._drive_rebase(checkout):
            self.release(target, checkout)
            return Outcome(target, OutcomeKind.REBASED, f"onto {parent}")
        if git.is_rebasing(checkout):
            return Outcome(
                target, OutcomeKind.CONFLICTS, f"rebasing onto {parent}", checkout
            )
        return Outcome(target, OutcomeKind.FAILED, f"rebase onto {parent} failed")

    @staticmethod
    def _drive_rebase(checkout: Path) -> bool:
        """Carry a stopped rebase on while only the answers file conflicts.

        Within a stack, the answers file follows the parent branch: a
        conflict there comes from a stale template update made on the branch
        itself, and the parent's version is the newer one. A commit left
        empty by that is skipped.

        Returns:
            True once the rebase is complete, False if it stopped on other
            conflicts (or for any other reason).
        """
        while git.is_rebasing(checkout):
            unmerged = git.unmerged_files(checkout)
            if unmerged == [ANSWERS_FILE]:
                git.take_ours(checkout, ANSWERS_FILE)
            elif unmerged:
                return False
            if not git.rebase_continue(checkout) and not git.unmerged_files(checkout):
                return False
        return True

    def resume(self, status: BranchStatus) -> Outcome:
        """Carry on a stopped update or rebase whose conflicts are resolved.

        Returns:
            REBASED or UPDATED (or UNCOMMITTED, see `finish`) when done, or
            CONFLICTS if unmerged files remain.
        """
        target, checkout = status.target, status.checkout
        if checkout is None:
            return Outcome(target, OutcomeKind.SKIPPED, "nothing to resume")
        if unmerged := git.unmerged_files(checkout):
            detail = f"still unmerged: {', '.join(unmerged)}"
            return Outcome(target, OutcomeKind.CONFLICTS, detail, checkout)
        if not git.is_rebasing(checkout):
            return self.finish(status, checkout)
        if self._drive_rebase(checkout):
            self.release(target, checkout)
            return Outcome(target, OutcomeKind.REBASED, f"onto {status.parent}")
        if git.is_rebasing(checkout):
            return Outcome(
                target,
                OutcomeKind.CONFLICTS,
                f"rebasing onto {status.parent}",
                checkout,
            )
        return Outcome(target, OutcomeKind.FAILED, "git rebase --continue failed")

    def abort(self, status: BranchStatus) -> None:
        """Throw away a stopped update or rebase, back to the branch's last commit."""
        checkout = status.checkout
        if checkout is None:
            return
        if git.is_rebasing(checkout):
            git.rebase_abort(checkout)
        elif not self.owns(checkout):
            git.discard_changes(checkout)
        self.release(status.target, checkout)
