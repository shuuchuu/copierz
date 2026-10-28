from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from copierz.stacks import depth, find_parents, topological_order
from tests.conftest import commit_file, git_out

if TYPE_CHECKING:
    from pathlib import Path


def _branch_with_commit(repo: Path, branch: str, start: str, name: str) -> None:
    git_out(repo, "checkout", "-q", "-b", branch, start)
    commit_file(repo, name, f"{branch}\n", f"work on {branch}")


def test_closest_ancestor_is_the_parent(project: Path) -> None:
    _branch_with_commit(project, "gui", "main", "gui.txt")
    _branch_with_commit(project, "solution", "gui", "solution.txt")
    _branch_with_commit(project, "main-en", "main", "readme-en.txt")

    parents = find_parents(project, ["main", "gui", "solution", "main-en"])

    assert parents == {
        "main": None,
        "gui": "main",
        "solution": "gui",
        "main-en": "main",
    }
    assert topological_order(parents) == ["main", "gui", "solution", "main-en"]
    assert depth(parents, "solution") == 2


def test_still_stacked_when_parent_only_got_template_updates(project: Path) -> None:
    _branch_with_commit(project, "solution", "main", "solution.txt")
    git_out(project, "checkout", "-q", "main")
    commit_file(project, ".copier-answers.yml", "_commit: 0.2.0\n", "Update")

    assert find_parents(project, ["main", "solution"])["solution"] == "main"


def test_independent_when_parent_moved_on(project: Path) -> None:
    _branch_with_commit(project, "solution", "main", "solution.txt")
    git_out(project, "checkout", "-q", "main")
    commit_file(project, "other.txt", "x\n", "unrelated work")

    assert find_parents(project, ["main", "solution"])["solution"] is None


def test_mutually_stacked_branches_get_a_root(project: Path) -> None:
    # Both only got their own template update since forking.
    _branch_with_commit(project, "a", "main", ".copier-answers.yml")
    _branch_with_commit(project, "b", "main", ".copier-answers.yml")

    parents = find_parents(project, ["a", "b"])

    assert parents == {"a": None, "b": "a"}


def test_still_stacked_on_a_parent_rebased_since(project: Path) -> None:
    # A rebase of solution, on gui, stopped and was aborted after gui and
    # solution were rebased: gradio still points at the old solution.
    _branch_with_commit(project, "gui", "main", "gui.txt")
    _branch_with_commit(project, "solution", "gui", "solution.txt")
    _branch_with_commit(project, "gradio", "solution", "gradio.txt")
    git_out(project, "checkout", "-q", "main")
    commit_file(project, "main.txt", "x\n", "unrelated work on main")
    for branch, parent in [("gui", "main"), ("solution", "gui")]:
        git_out(project, "checkout", "-q", branch)
        git_out(project, "rebase", "-q", parent)

    parents = find_parents(project, ["main", "gui", "solution", "gradio"])

    assert parents["gradio"] == "solution"


def _at(monkeypatch: pytest.MonkeyPatch, seconds: int) -> None:
    # Reflog entries take the committer date: put the next git commands
    # `seconds` after the fixture's commits.
    monkeypatch.setenv("GIT_COMMITTER_DATE", f"{2_000_000_000 + seconds} +0000")


def test_backup_of_a_branch_is_not_its_parent(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # caesar-cipher's case: main moved on after the backup, so its history
    # looks stacked on it.
    _at(monkeypatch, 1)
    git_out(project, "branch", "backup/main", "main")
    _at(monkeypatch, 2)
    commit_file(project, "main.txt", "x\n", "work on main")

    parents = find_parents(project, ["main", "backup/main"])

    assert parents == {"main": None, "backup/main": None}


def test_new_branch_rebased_onto_another_is_not_its_parent(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A branch started for some later work, rebased onto main before its
    # first commit, while main moved on.
    _at(monkeypatch, 1)
    git_out(project, "branch", "later", "main")
    commit_file(project, "main.txt", "x\n", "work on main")
    _at(monkeypatch, 2)
    git_out(project, "checkout", "-q", "later")
    git_out(project, "rebase", "-q", "main")
    git_out(project, "checkout", "-q", "main")
    _at(monkeypatch, 3)
    commit_file(project, "main.txt", "y\n", "more work on main")

    assert find_parents(project, ["main", "later"])["main"] is None


def test_branch_created_from_a_fresh_copy_is_stacked_on_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Branches checked out from a clone's remote branches: solution was only
    # created where it is, but solution-en was created from it afterwards,
    # so it's still solution's child.
    _at(monkeypatch, 1)
    _branch_with_commit(project, "work", "main", "solution.txt")
    _at(monkeypatch, 2)
    git_out(project, "branch", "solution", "work")
    git_out(project, "checkout", "-q", "main")
    git_out(project, "branch", "-D", "work")
    _at(monkeypatch, 3)
    _branch_with_commit(project, "solution-en", "solution", "solution-en.txt")

    assert find_parents(project, ["main", "solution", "solution-en"]) == {
        "main": None,
        "solution": "main",
        "solution-en": "solution",
    }


@pytest.mark.parametrize("later", [0, 1])
def test_branch_reset_onto_its_parent_is_stacked_on_it(
    project: Path, monkeypatch: pytest.MonkeyPatch, later: int
) -> None:
    # linting's case: solution was rebased, then solution-en rebuilt on it
    # by a reset to solution and a cherry-pick, by a script (same second)
    # or by hand.
    _branch_with_commit(project, "solution", "main", "solution.txt")
    _branch_with_commit(project, "solution-en", "solution", "solution-en.txt")
    translation = git_out(project, "rev-parse", "solution-en")
    git_out(project, "checkout", "-q", "main")
    commit_file(project, "main.txt", "x\n", "work on main")
    _at(monkeypatch, 1)
    git_out(project, "checkout", "-q", "solution")
    git_out(project, "rebase", "-q", "main")
    _at(monkeypatch, 1 + later)
    git_out(project, "checkout", "-q", "-B", "solution-en", "solution")
    git_out(project, "cherry-pick", translation)

    parents = find_parents(project, ["main", "solution", "solution-en"])

    assert parents["solution-en"] == "solution"
