from __future__ import annotations

from typing import TYPE_CHECKING

from copierz import git

if TYPE_CHECKING:
    from pathlib import Path


def test_is_git_repo(tmp_path: Path, subproject_repo: Path) -> None:
    assert git.is_git_repo(subproject_repo)
    assert not git.is_git_repo(tmp_path / "not-a-repo")


def test_current_branch(subproject_repo: Path) -> None:
    assert git.current_branch(subproject_repo) in {"main", "master"}


def test_is_clean(subproject_repo: Path) -> None:
    assert git.is_clean(subproject_repo)
    (subproject_repo / "README.md").write_text("changed\n")
    assert not git.is_clean(subproject_repo)


def test_unmerged_files_empty_when_clean(subproject_repo: Path) -> None:
    assert git.unmerged_files(subproject_repo) == []


def test_show_file_at_ref(subproject_repo: Path) -> None:
    branch = git.current_branch(subproject_repo)
    content = git.show_file_at_ref(subproject_repo, branch, "README.md")
    assert content == "hello\n"
    assert git.show_file_at_ref(subproject_repo, branch, "missing.txt") is None


def test_stage_and_commit(subproject_repo: Path) -> None:
    (subproject_repo / "README.md").write_text("changed\n")
    git.stage(subproject_repo, "README.md")
    git.commit(subproject_repo, "update readme")
    assert git.is_clean(subproject_repo)


def test_changed_files_reports_status_codes(subproject_repo: Path) -> None:
    (subproject_repo / "new.txt").write_text("new\n")
    (subproject_repo / "README.md").write_text("changed\n")
    changed = {path: code for code, path in git.changed_files(subproject_repo)}
    assert changed["new.txt"] == "??"
    assert changed["README.md"] == " M"
