from __future__ import annotations

from typing import TYPE_CHECKING

from copierz import git
from copierz.config import Config
from copierz.discovery import discover
from tests.conftest import commit_file, git_out, init_repo

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _answers(src_path: str) -> str:
    return f"_commit: 0.1.0\n_src_path: {src_path}\n"


def test_discovers_every_branch_with_answers(tmp_path: Path, project: Path) -> None:
    git_out(project, "checkout", "-q", "-b", "no-answers")
    git_out(project, "rm", "-q", ".copier-answers.yml")
    git_out(project, "commit", "-q", "-m", "drop answers")
    git_out(project, "checkout", "-q", "-b", "other", "main")

    targets = discover(Config(roots=[tmp_path]))

    assert [(t.repo, t.branch) for t in targets] == [
        (project, "main"),
        (project, "other"),
    ]


def test_skips_nested_repos_hidden_dirs_and_linked_worktrees(
    tmp_path: Path, make_project: Callable[[Path, str], Path]
) -> None:
    root = tmp_path / "root"
    outer = make_project(root / "outer", "/t")
    make_project(outer / "vendored", "/t")
    make_project(root / ".hidden" / "repo", "/t")
    git_out(outer, "branch", "other")
    git.worktree_add(outer, root / "linked", "other")

    targets = discover(Config(roots=[root]))

    assert {(t.repo, t.branch) for t in targets} == {
        (outer, "main"),
        (outer, "other"),
    }


def test_exclusions(tmp_path: Path, make_project: Callable[[Path, str], Path]) -> None:
    excluded = make_project(tmp_path / "excluded", "/t")
    partly = make_project(tmp_path / "partly", "/t")
    git_out(partly, "branch", "other")

    targets = discover(
        Config(roots=[tmp_path], exclude=[str(excluded), f"{partly}@main"])
    )

    assert [(t.repo, t.branch) for t in targets] == [(partly, "other")]


def test_relative_src_path_resolves_against_repos_own_parent(tmp_path: Path) -> None:
    # Mirrors a real case: .../repos/mog/invoicez recording
    # `_src_path: ../shuuchuu/template`, relative to its own parent
    # .../repos/mog, while the scan starts higher up at .../repos.
    repo = init_repo(tmp_path / "repos" / "mog" / "invoicez")
    commit_file(repo, ".copier-answers.yml", _answers("../shuuchuu/template"), "init")

    (target,) = discover(Config(roots=[tmp_path / "repos"]))

    assert target.template == tmp_path / "repos" / "shuuchuu" / "template"
