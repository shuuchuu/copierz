from __future__ import annotations

from typing import TYPE_CHECKING

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
