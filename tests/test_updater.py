"""End-to-end tests driving a real (tiny) Copier template through updates."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from copierz import git
from copierz.config import Config
from copierz.discovery import discover, refresh
from copierz.models import StatusKind
from copierz.status import LatestRefs, repo_statuses
from copierz.updater import OutcomeKind, Updater
from tests.conftest import commit_file, git_out, init_repo

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from copierz.models import BranchStatus


@pytest.fixture
def updater(tmp_path: Path) -> Updater:
    return Updater(tmp_path / "cache")


def _statuses(repo: Path) -> dict[str, BranchStatus]:
    targets = refresh(discover(Config(roots=[repo])))
    return {s.target.branch: s for s in repo_statuses(targets, LatestRefs())}


def _ordered(repo: Path) -> list[BranchStatus]:
    return list(_statuses(repo).values())


def _move_away(repo: Path) -> None:
    # Check out an unrelated branch, so no tracked branch is checked out.
    git_out(repo, "checkout", "-q", "--orphan", "elsewhere")
    git_out(repo, "rm", "-rfq", ".")
    commit_file(repo, "unrelated.txt", "x\n", "unrelated")


def test_update_in_place_commits(project: Path, updater: Updater) -> None:
    (outcome,) = updater.update_repo(_ordered(project))

    assert outcome.kind is OutcomeKind.UPDATED
    assert (project / "greeting.txt").read_text() == "hello world\n"
    assert git.is_clean(project)
    assert git_out(project, "log", "-1", "--format=%s") == "Update template to 0.2.0"
    assert _statuses(project)["main"].kind is StatusKind.UP_TO_DATE


def test_branch_not_checked_out_is_updated_in_a_temporary_worktree(
    project: Path, updater: Updater
) -> None:
    _move_away(project)

    (outcome,) = updater.update_repo(_ordered(project))

    assert outcome.kind is OutcomeKind.UPDATED
    assert git.worktrees(project) == {"elsewhere": project}
    assert not any(updater.worktrees_dir.iterdir())
    assert git.show_file_at_ref(project, "main", "greeting.txt") == "hello world\n"
    assert not (project / "greeting.txt").exists()


def test_stacked_branch_is_rebased_not_copier_updated(
    project: Path, updater: Updater
) -> None:
    git_out(project, "checkout", "-q", "-b", "solution")
    commit_file(project, "solution.txt", "42\n", "Add solution")

    outcomes = updater.update_repo(_ordered(project))

    assert [(o.target.branch, o.kind) for o in outcomes] == [
        ("main", OutcomeKind.UPDATED),
        ("solution", OutcomeKind.REBASED),
    ]
    assert git_out(project, "log", "--format=%s", "main..solution") == "Add solution"
    assert (project / "greeting.txt").read_text() == "hello world\n"
    assert {s.kind for s in _ordered(project)} == {StatusKind.UP_TO_DATE}


def test_conflict_stops_in_worktree_and_holds_back_stacked_branches(
    project: Path, updater: Updater
) -> None:
    commit_file(project, "greeting.txt", "hi\n", "diverge")
    git_out(project, "checkout", "-q", "-b", "solution")
    commit_file(project, "solution.txt", "42\n", "Add solution")
    _move_away(project)

    outcomes = updater.update_repo(_ordered(project))

    assert [(o.target.branch, o.kind) for o in outcomes] == [
        ("main", OutcomeKind.CONFLICTS),
        ("solution", OutcomeKind.SKIPPED),
    ]
    main = _statuses(project)["main"]
    assert main.kind is StatusKind.CONFLICTS
    assert main.checkout is not None
    assert updater.owns(main.checkout)
    assert "<<<<<<< before updating" in (main.checkout / "greeting.txt").read_text()

    # Resolve by hand, as `copierz resolve` would after the mergetool.
    (main.checkout / "greeting.txt").write_text("hi world\n")
    git.stage_all(main.checkout)
    assert _statuses(project)["main"].kind is StatusKind.IN_PROGRESS
    assert updater.finish(_statuses(project)["main"], main.checkout).kind is (
        OutcomeKind.UPDATED
    )
    assert not main.checkout.exists()

    (outcome,) = updater.update_repo(_ordered(project))
    assert (outcome.target.branch, outcome.kind) == ("solution", OutcomeKind.REBASED)
    assert git.show_file_at_ref(project, "solution", "greeting.txt") == "hi world\n"


def test_dirty_checkout_blocks_only_that_branch(
    project: Path, updater: Updater
) -> None:
    git_out(project, "branch", "other")
    (project / "greeting.txt").write_text("work in progress\n")

    statuses = _statuses(project)
    assert statuses["main"].kind is StatusKind.BLOCKED

    outcomes = {o.target.branch: o.kind for o in updater.update_repo(_ordered(project))}
    assert outcomes["main"] is OutcomeKind.SKIPPED
    assert (project / "greeting.txt").read_text() == "work in progress\n"


def test_abort_drops_a_temporary_worktree(project: Path, updater: Updater) -> None:
    commit_file(project, "greeting.txt", "hi\n", "diverge")
    _move_away(project)
    updater.update_repo(_ordered(project))
    main = _statuses(project)["main"]

    updater.abort(main)

    assert git.worktrees(project) == {"elsewhere": project}
    assert _statuses(project)["main"].kind is StatusKind.BEHIND


def test_abort_in_main_checkout_restores_last_commit(
    project: Path, tmp_path: Path
) -> None:
    commit_file(project, "greeting.txt", "hi\n", "diverge")
    updater = Updater(tmp_path / "cache", commit=False)
    updater.update_repo(_ordered(project))

    updater.abort(_statuses(project)["main"])

    assert git.is_clean(project)
    assert (project / "greeting.txt").read_text() == "hi\n"


def test_relative_src_path_resolves_against_repos_own_parent(
    tmp_path: Path,
    make_template: Callable[[Path], Path],
    make_project: Callable[[Path, str], Path],
    updater: Updater,
) -> None:
    # The template and the repo deliberately don't share a parent directory.
    make_template(tmp_path / "workspace" / "shuuchuu" / "template")
    repo = make_project(
        tmp_path / "workspace" / "elsewhere" / "project", "../shuuchuu/template"
    )
    _move_away(repo)

    (outcome,) = updater.update_repo(_ordered(repo))

    assert outcome.kind is OutcomeKind.UPDATED, outcome.detail
    assert git.show_file_at_ref(repo, "main", "greeting.txt") == "hello world\n"


def test_missing_template_is_an_error(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    commit_file(repo, ".copier-answers.yml", "_src_path: ../nope\n", "init")

    (status,) = _ordered(repo)

    assert status.kind is StatusKind.ERROR
    assert "template not found" in status.detail


def test_stale_template_updates_on_stacked_branches_are_dropped(
    project: Path, updater: Updater
) -> None:
    # What per-branch updates left behind in linting: each branch got its
    # own update commit, so the stack no longer lines up. Rebasing settles
    # the answers file on the parent's version and drops the empty commit.
    git_out(project, "checkout", "-q", "-b", "solution")
    commit_file(project, "solution.txt", "42\n", "Add solution")
    answers = (project / ".copier-answers.yml").read_text()
    stale = answers.replace("_commit: 0.1.0", "_commit: 0.1.0-1-gstale")
    commit_file(project, ".copier-answers.yml", stale, "Stale update")

    statuses = _statuses(project)
    assert statuses["solution"].parent == "main"
    outcomes = updater.update_repo(list(statuses.values()))

    assert [(o.target.branch, o.kind) for o in outcomes] == [
        ("main", OutcomeKind.UPDATED),
        ("solution", OutcomeKind.REBASED),
    ]
    assert git_out(project, "log", "--format=%s", "main..solution") == "Add solution"


def test_resume_continues_a_rebase_resolved_by_hand(
    project: Path, updater: Updater
) -> None:
    git_out(project, "checkout", "-q", "-b", "solution")
    commit_file(project, "greeting.txt", "hello solution\n", "Add solution")
    _move_away(project)

    outcomes = updater.update_repo(_ordered(project))
    assert [o.kind for o in outcomes] == [OutcomeKind.UPDATED, OutcomeKind.CONFLICTS]
    solution = _statuses(project)["solution"]
    assert solution.unmerged == ("greeting.txt",)
    assert solution.checkout is not None
    assert updater.resume(solution).kind is OutcomeKind.CONFLICTS

    (solution.checkout / "greeting.txt").write_text("hello world solution\n")
    git.stage_all(solution.checkout)
    outcome = updater.resume(_statuses(project)["solution"])

    assert outcome.kind is OutcomeKind.REBASED
    assert not solution.checkout.exists()
    assert git.show_file_at_ref(project, "solution", "greeting.txt") == (
        "hello world solution\n"
    )


def test_rebase_replays_only_own_commits_when_parent_was_rewritten(
    project: Path, updater: Updater
) -> None:
    # ghcli's case: gradio sits on solution, on gui, on main. Once gui is
    # rebased onto an updated main, its commit's patch changes; rebasing
    # solution, then gradio, must not replay gui's old commit under them.
    commit_file(project, "f.txt", "a\nb\nc\nd\ne\n", "base")
    git_out(project, "checkout", "-q", "-b", "gui")
    commit_file(project, "f.txt", "a\nb\nC\nd\ne\n", "Add GUI starting point")
    git_out(project, "checkout", "-q", "-b", "solution")
    commit_file(project, "f.txt", "a\nb\nCC\nd\ne\n", "Add GUI")
    git_out(project, "checkout", "-q", "-b", "gradio")
    commit_file(project, "h.txt", "gradio\n", "Add gradio UI")
    git_out(project, "checkout", "-q", "main")
    commit_file(project, "f.txt", "A\nb\nc\nd\ne\n", "Change main")
    git_out(project, "checkout", "-q", "gui")
    git_out(project, "rebase", "-q", "main")
    _move_away(project)

    statuses = _statuses(project)
    for branch, parent in [("solution", "gui"), ("gradio", "solution")]:
        outcome = updater.rebase(statuses[branch].target, parent)
        assert outcome.kind is OutcomeKind.REBASED, (branch, outcome.detail)

    assert git_out(project, "log", "--format=%s", "solution..gradio") == "Add gradio UI"
    assert git_out(project, "log", "--format=%s", "gui..solution") == "Add GUI"
