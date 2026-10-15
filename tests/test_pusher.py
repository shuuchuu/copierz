from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from copierz import git
from copierz.config import Config
from copierz.discovery import discover
from copierz.pusher import PushState, plan, push
from tests.conftest import commit_file, git_out

if TYPE_CHECKING:
    from pathlib import Path

    from copierz.discovery import Target


@pytest.fixture
def remote(tmp_path: Path, project: Path) -> Path:
    # A bare remote that `project`'s main tracks, plus another clone of it.
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(project), str(bare)], check=True
    )
    git_out(project, "remote", "add", "origin", str(bare))
    git_out(project, "fetch", "-q", "origin")
    git_out(project, "branch", "-q", "--set-upstream-to=origin/main", "main")
    return bare


def _target(project: Path, branch: str = "main") -> Target:
    return next(t for t in discover(Config(roots=[project])) if t.branch == branch)


def _push_from_elsewhere(tmp_path: Path, bare: Path) -> None:
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    git_out(other, "config", "user.email", "o@example.com")
    git_out(other, "config", "user.name", "Other")
    commit_file(other, "other.txt", "x\n", "Someone else's work")
    git_out(other, "push", "-q", "origin", "main")


def test_ahead_is_pushed(project: Path, remote: Path) -> None:
    commit_file(project, "new.txt", "x\n", "New work")

    push_plan = plan(_target(project))
    assert (push_plan.state, push_plan.commits) == (PushState.AHEAD, 1)
    assert push(push_plan)

    assert git_out(remote, "rev-parse", "main") == git_out(project, "rev-parse", "main")
    assert plan(_target(project)).state is PushState.UP_TO_DATE


def test_rebased_branch_needs_force_and_uses_a_lease(
    project: Path, remote: Path
) -> None:
    commit_file(project, "new.txt", "x\n", "New work")
    git_out(project, "push", "-q", "origin", "main")
    git_out(project, "commit", "-q", "--amend", "-m", "Reworded")

    push_plan = plan(_target(project))
    assert push_plan.state is PushState.REWRITTEN
    assert not push(push_plan)
    assert push(push_plan, force=True)

    assert git_out(remote, "log", "-1", "--format=%s", "main") == "Reworded"


def test_never_overwrites_commits_it_never_had(
    tmp_path: Path, project: Path, remote: Path
) -> None:
    _push_from_elsewhere(tmp_path, remote)
    commit_file(project, "mine.txt", "x\n", "My work")
    git_out(project, "fetch", "-q", "origin")

    push_plan = plan(_target(project))
    assert push_plan.state is PushState.DIVERGED
    assert not push(push_plan, force=True)


def test_behind_is_not_pushed(tmp_path: Path, project: Path, remote: Path) -> None:
    _push_from_elsewhere(tmp_path, remote)
    git_out(project, "fetch", "-q", "origin")

    assert plan(_target(project)).state is PushState.BEHIND


def test_lease_fails_if_the_remote_moved_since_the_fetch(
    tmp_path: Path, project: Path, remote: Path
) -> None:
    commit_file(project, "new.txt", "x\n", "New work")
    git_out(project, "push", "-q", "origin", "main")
    git_out(project, "commit", "-q", "--amend", "-m", "Reworded")
    push_plan = plan(_target(project))
    _push_from_elsewhere(tmp_path, remote)  # Not fetched.

    with pytest.raises(git.GitError):
        push(push_plan, force=True)
    assert git_out(remote, "log", "-1", "--format=%s", "main") == "Someone else's work"


def test_branch_without_upstream(project: Path) -> None:
    assert plan(_target(project)).state is PushState.NO_UPSTREAM
