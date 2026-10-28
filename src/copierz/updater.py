"""Bring repo branches up to date: `copier update` stack roots, rebase the rest.

Branches that aren't checked out anywhere are updated in a temporary git
worktree under copierz's cache directory, so your own checkout never
switches branch and a dirty `main` doesn't block its siblings. A worktree
is removed once its branch is committed; it stays (and shows up in
`copierz status`) when an update or rebase stops on conflicts.

A tracked `uv.lock` is relocked along the way: into the update commit on a
stack's bottom branch, and in a commit of its own on a rebased branch whose
lock went stale.

A branch carrying several Copier projects (nested ones, see
`copierz.discovery`) gets one update commit per project, but is rebased
once, as a whole.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from enum import Enum
from itertools import groupby
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import copier

from copierz import git, lock
from copierz.models import StatusKind
from copierz.template_source import ANSWERS_FILE, project_dirs

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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


RELOCK_MESSAGE = "Relock uv.lock"


def commit_message(template: Path, ref: str | None, subdir: str = "") -> str:
    name = template.name if "template" in template.name else f"{template.name} template"
    message = f"Update {name} to {ref}" if ref else f"Update {name}"
    return f"{message} in {subdir}" if subdir else message


BLOCKING_KINDS = {StatusKind.BLOCKED, StatusKind.CONFLICTS, StatusKind.IN_PROGRESS}


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
            statuses: Statuses of the projects on branches of one repo,
                parents first, each branch's projects together (as returned
                by `copierz.status.repo_statuses`).

        Returns:
            One outcome per project that needed something done; a rebase
            counts for the branch's first project.
        """
        outcomes = []
        held_back: set[str] = set()
        for branch, branch_statuses in groupby(statuses, lambda s: s.target.branch):
            for outcome in self._update_branch(list(branch_statuses), held_back):
                if not outcome.kind.ok:
                    held_back.add(branch)
                self.report(outcome)
                outcomes.append(outcome)
        return outcomes

    def _update_branch(
        self, statuses: list[BranchStatus], held_back: set[str]
    ) -> list[Outcome]:
        first, parent = statuses[0], statuses[0].parent
        if parent is not None and parent in held_back:
            return [Outcome(first.target, OutcomeKind.SKIPPED, f"waiting for {parent}")]
        skipped = [
            Outcome(s.target, OutcomeKind.SKIPPED, f"{s.kind.value}: {s.detail}")
            for s in statuses
            if s.kind in BLOCKING_KINDS
        ]
        if skipped:  # The checkout isn't clean: nothing more can happen there.
            return skipped
        skipped = [
            Outcome(s.target, OutcomeKind.SKIPPED, f"{s.kind.value}: {s.detail}")
            for s in statuses
            if s.kind is StatusKind.ERROR
        ]
        if parent is None:
            return skipped + self._update_projects(
                [s for s in statuses if s.kind is not StatusKind.ERROR]
            )
        if skipped:
            return skipped
        repo, branch = first.target.repo, first.target.branch
        if git.merge_base(repo, parent, branch) == git.rev_parse(repo, parent):
            if all(s.kind is StatusKind.UP_TO_DATE for s in statuses):
                return []
            detail = f"stacked on {parent} but behind it; update it by hand"
            return [Outcome(first.target, OutcomeKind.FAILED, detail)]
        return [self.rebase(first.target, parent)]

    def _update_projects(self, statuses: list[BranchStatus]) -> list[Outcome]:
        """`copier update` each project of a branch, stopping at the first failure.

        Returns:
            One outcome per project that was behind.
        """
        outcomes = []
        stopped: Outcome | None = None
        for status in statuses:
            if status.kind is StatusKind.UP_TO_DATE:
                continue
            if stopped is not None:
                detail = f"waiting for {stopped.target.label}"
                outcomes.append(Outcome(status.target, OutcomeKind.SKIPPED, detail))
                continue
            outcome = self._copier_update(status)
            if not outcome.kind.ok:
                stopped = outcome
            outcomes.append(outcome)
        return outcomes

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
            with contextlib.suppress(OSError):  # Other worktrees of the repo left.
                checkout.parent.rmdir()

    def _copier_update(self, status: BranchStatus) -> Outcome:
        target = status.target
        checkout = self.checkout(target)
        if not git.is_clean(checkout):
            return Outcome(target, OutcomeKind.SKIPPED, "dirty", checkout)
        try:
            with contextlib.chdir(target.workspace_root):
                copier.run_update(
                    dst_path=checkout / target.subdir,
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

        A tracked `uv.lock` is relocked first, so the update commit carries
        a lock that matches its `pyproject.toml`.

        Returns:
            UPDATED, or UNCOMMITTED if committing is disabled or failed (e.g.
            on a pre-commit hook), or if `uv lock` failed.
        """
        target = status.target
        versions = f"{status.current_ref} → {status.latest_ref}"
        try:
            lock.relock(checkout / target.subdir)
        except lock.LockError as exc:
            detail = f"uv lock failed: {exc}"
            return Outcome(target, OutcomeKind.UNCOMMITTED, detail, checkout)
        if not self.commit:
            return Outcome(target, OutcomeKind.UNCOMMITTED, versions, checkout)
        git.stage_all(checkout)
        try:
            git.commit(
                checkout,
                commit_message(target.template, status.latest_ref, target.subdir),
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
            return self._rebased(target, parent, checkout)
        if git.is_rebasing(checkout):
            return Outcome(
                target, OutcomeKind.CONFLICTS, f"rebasing onto {parent}", checkout
            )
        return Outcome(target, OutcomeKind.FAILED, f"rebase onto {parent} failed")

    def _rebased(self, target: Target, parent: str | None, checkout: Path) -> Outcome:
        """Relock a freshly rebased branch if needed, then drop its worktree.

        The branch's own commits may change a project's `pyproject.toml` on
        top of a parent whose `uv.lock` moved. The locks of all the branch's
        projects are relocked in one commit, without hooks like the rebase
        itself: it only touches locks.

        Returns:
            REBASED, saying so if a lock is left stale because `uv lock`
            failed.
        """
        relocked, failures = False, []
        for subdir in project_dirs(checkout, "HEAD"):
            try:
                if lock.relock(checkout / subdir):
                    git.stage(checkout / subdir, lock.LOCK_FILE)
                    relocked = True
            except lock.LockError as exc:
                where = f"{subdir}/{lock.LOCK_FILE}" if subdir else lock.LOCK_FILE
                failures.append(f"; {where} left stale, uv lock failed: {exc}")
        if relocked:
            git.commit(checkout, RELOCK_MESSAGE, verify=False)
        detail = f"onto {parent}{', relocked' if relocked else ''}{''.join(failures)}"
        self.release(target, checkout)
        return Outcome(target, OutcomeKind.REBASED, detail)

    @staticmethod
    def _drive_rebase(checkout: Path) -> bool:
        """Carry a stopped rebase on while only answers files or locks conflict.

        Within a stack, answers files follow the parent branch: a conflict
        there comes from a stale template update made on the branch itself,
        and the parent's version is the newer one. A commit left empty by
        that is skipped. A `uv.lock` follows the parent too, relocked to take
        in the commit's own `pyproject.toml` changes.

        Returns:
            True once the rebase is complete, False if it stopped on other
            conflicts, `uv lock` failed (or for any other reason).
        """
        while git.is_rebasing(checkout):
            unmerged = [PurePosixPath(f) for f in git.unmerged_files(checkout)]
            if any(f.name not in {ANSWERS_FILE, lock.LOCK_FILE} for f in unmerged):
                return False
            for path in unmerged:
                if path.name == ANSWERS_FILE:
                    git.take_ours(checkout, str(path))
                    continue
                git.checkout_ours(checkout, str(path))
                try:
                    lock.relock(checkout / path.parent)
                except lock.LockError:
                    return False
                git.stage(checkout, str(path))
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
            return self._rebased(target, status.parent, checkout)
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
