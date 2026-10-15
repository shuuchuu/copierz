from __future__ import annotations

from typing import TYPE_CHECKING

from copierz import git
from tests.conftest import commit_file, git_out

if TYPE_CHECKING:
    from pathlib import Path


def test_worktrees_lists_main_checkout_and_linked_worktrees(
    tmp_path: Path, project: Path
) -> None:
    git_out(project, "branch", "other")
    linked = tmp_path / "linked"
    git.worktree_add(project, linked, "other")

    assert git.worktrees(project) == {"main": project, "other": linked}
    assert git.is_repo_root(project)
    assert not git.is_repo_root(linked)

    git.worktree_remove(project, linked)
    assert git.worktrees(project) == {"main": project}


def test_changed_vs_head_sees_staged_and_unstaged(project: Path) -> None:
    (project / "greeting.txt").write_text("changed\n")
    (project / "new.txt").write_text("new\n")
    git.stage_all(project)
    (project / ".copier-answers.yml").write_text("changed\n")

    assert set(git.changed_vs_head(project)) == {
        "greeting.txt",
        "new.txt",
        ".copier-answers.yml",
    }


def test_rebase_conflict_is_detected_and_abortable(project: Path) -> None:
    git_out(project, "checkout", "-q", "-b", "child")
    commit_file(project, "greeting.txt", "child\n", "child change")
    git_out(project, "checkout", "-q", "main")
    commit_file(project, "greeting.txt", "main\n", "main change")
    git_out(project, "checkout", "-q", "child")

    assert not git.rebase(project, "main")
    assert git.is_rebasing(project)
    assert git.unmerged_files(project) == ["greeting.txt"]

    git.rebase_abort(project)
    assert not git.is_rebasing(project)
    assert git.is_clean(project)


def test_count_commits_can_filter_by_path(project: Path) -> None:
    base = git.rev_parse(project, "main")
    commit_file(project, "a.txt", "a\n", "a")
    commit_file(project, ".copier-answers.yml", "x\n", "answers")

    assert git.count_commits(project, f"{base}..main") == 2
    assert git.count_commits(project, f"{base}..main", ".copier-answers.yml") == 1
