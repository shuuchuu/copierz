from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from copierz.git import GitError
from copierz.template_source import latest_template_ref, read_answers

if TYPE_CHECKING:
    from pathlib import Path


def test_read_answers(subproject_repo: Path, template_repo: Path) -> None:
    branch = "main"
    answers = read_answers(subproject_repo, branch)
    if answers is None:
        # Older git default branch name.
        answers = read_answers(subproject_repo, "master")
    assert answers is not None
    assert answers.commit == "0.1.0"
    assert answers.src_path == str(template_repo)


def test_read_answers_missing_file(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "file.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"], check=True)
    branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert read_answers(repo, branch) is None


def test_latest_template_ref_picks_highest_pep440_tag(template_repo: Path) -> None:
    assert latest_template_ref(template_repo) == "0.2.0"


def test_latest_template_ref_head_when_no_tags(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "no-tags"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "file.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"], check=True)

    assert latest_template_ref(repo) == "HEAD"


def test_latest_template_ref_raises_on_bad_path(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        latest_template_ref(tmp_path / "does-not-exist")
