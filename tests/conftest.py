"""Shared fixtures: a bare template repo and a subproject that consumes it."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

ANSWERS_TEMPLATE = """\
_commit: {commit}
_src_path: {src_path}
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


@pytest.fixture
def template_repo(tmp_path: Path) -> Path:
    # A minimal git repo with two tags, standing in for a copier template.
    repo = init_repo(tmp_path / "template")
    (repo / "copier.yml").write_text("project_name:\n  type: str\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "tag", "0.1.0")
    (repo / "copier.yml").write_text(
        "project_name:\n  type: str\nextra:\n  type: str\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add extra question")
    _git(repo, "tag", "0.2.0")
    return repo


@pytest.fixture
def subproject_repo(tmp_path: Path, template_repo: Path) -> Path:
    # Tracks template_repo at its first tag, with an absolute `_src_path`.
    repo = init_repo(tmp_path / "project")
    (repo / ".copier-answers.yml").write_text(
        ANSWERS_TEMPLATE.format(commit="0.1.0", src_path=str(template_repo))
    )
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo
