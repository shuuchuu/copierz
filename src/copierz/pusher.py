"""Push updated branches, force-pushing those rebased only when it's safe.

A stacked branch rebased by `copierz update` no longer contains the commit
its remote counterpart points to. Pushing it requires a force, which is only
safe when the remote holds nothing the local branch didn't have: copierz
checks that the remote tip is in the branch's reflog, and pushes with
`--force-with-lease` on that tip so a push made since in the meantime makes
it fail instead of being overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from copierz import git

if TYPE_CHECKING:
    from copierz.discovery import Target


class PushState(Enum):
    UP_TO_DATE = "up to date"
    AHEAD = "ahead"
    REWRITTEN = "rewritten"
    BEHIND = "behind"
    DIVERGED = "diverged"
    NO_UPSTREAM = "no upstream"


@dataclass
class PushPlan:
    """How a branch stands relative to the remote branch it tracks.

    Attributes:
        target: The repo branch.
        state: What pushing it would take.
        remote: The remote it tracks, if any.
        remote_branch: The branch it tracks on that remote.
        remote_tip: The remote branch's commit, as last fetched.
        commits: How many commits the push would send.
    """

    target: Target
    state: PushState
    remote: str | None = None
    remote_branch: str | None = None
    remote_tip: str | None = None
    commits: int = 0

    @property
    def tracking(self) -> str:
        return f"{self.remote}/{self.remote_branch}" if self.remote else ""

    @property
    def detail(self) -> str:
        details = {
            PushState.AHEAD: f"{self.commits} commit(s) to push to {self.tracking}",
            PushState.REWRITTEN: (
                f"rebased: {self.commits} commit(s) to force-push to {self.tracking}"
            ),
            PushState.BEHIND: f"{self.tracking} has commits to pull first",
            PushState.DIVERGED: (
                f"{self.tracking} has commits this branch never had: integrate them"
            ),
            PushState.NO_UPSTREAM: "tracks no remote branch",
        }
        return details.get(self.state, "")


def plan(target: Target) -> PushPlan:
    """Work out what pushing `target` would take, from the last fetched state.

    Returns:
        The plan.
    """
    repo, branch = target.repo, target.branch
    tracked = git.upstream(repo, branch)
    if tracked is None:
        return PushPlan(target, PushState.NO_UPSTREAM)
    remote, remote_branch = tracked
    local = git.rev_parse(repo, branch)
    remote_tip = git.resolve(repo, f"refs/remotes/{remote}/{remote_branch}")

    def make(state: PushState, commits: int = 0) -> PushPlan:
        return PushPlan(target, state, remote, remote_branch, remote_tip, commits)

    if remote_tip is None:
        return make(PushState.AHEAD, git.count_commits(repo, local))
    if remote_tip == local:
        return make(PushState.UP_TO_DATE)
    ahead = git.count_commits(repo, f"{remote_tip}..{local}")
    if git.is_ancestor(repo, remote_tip, local):
        return make(PushState.AHEAD, ahead)
    if git.is_ancestor(repo, local, remote_tip):
        return make(PushState.BEHIND)
    if remote_tip in git.reflog(repo, branch):
        return make(PushState.REWRITTEN, ahead)
    return make(PushState.DIVERGED)


def push(push_plan: PushPlan, force: bool = False) -> bool:
    """Push the branch if its plan allows it.

    Args:
        push_plan: What `plan` returned for the branch.
        force: Whether to force-push a rebased branch (with a lease).

    Returns:
        Whether something was pushed.
    """
    target, remote, remote_branch = (
        push_plan.target,
        push_plan.remote,
        push_plan.remote_branch,
    )
    if remote is None or remote_branch is None:
        return False
    if push_plan.state is PushState.AHEAD:
        git.push(target.repo, remote, target.branch, remote_branch)
        return True
    if push_plan.state is PushState.REWRITTEN and force:
        lease = push_plan.remote_tip
        git.push(target.repo, remote, target.branch, remote_branch, lease=lease)
        return True
    return False
