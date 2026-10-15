"""Shared fixtures: template repos, and subprojects generated from them."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
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


def git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_file(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def make_template() -> Callable[[Path], Path]:
    """Build a tiny but real Copier template with tags 0.1.0 and 0.2.0.

    Mirrors the shape of the actual shuuchuu template: a `_subdirectory`
    pointing at the rendered content, and the special
    `{{_copier_conf.answers_file}}.jinja` file that Copier requires in order
    to write/update `.copier-answers.yml` in generated projects. 0.2.0
    changes `greeting.txt` from "hello" to "hello world".

    Returns:
        A function building such a template at the given path.
    """

    def make(path: Path) -> Path:
        repo = init_repo(path)
        (repo / "copier.yml").write_text(
            "_subdirectory: template\ngreeting:\n  type: str\n  default: hello\n"
        )
        template_dir = repo / "template"
        template_dir.mkdir()
        (template_dir / "{{_copier_conf.answers_file}}.jinja").write_text(
            "# Changes here will be overwritten by Copier\n"
            "{{ _copier_answers|to_nice_yaml -}}\n"
        )
        commit_file(template_dir, "greeting.txt.jinja", "{{ greeting }}\n", "v1")
        _git(repo, "tag", "0.1.0")
        commit_file(template_dir, "greeting.txt.jinja", "{{ greeting }} world\n", "v2")
        _git(repo, "tag", "0.2.0")
        return repo

    return make


@pytest.fixture
def make_project() -> Callable[[Path, str], Path]:
    """Build a repo as if `copier copy` had rendered it at template 0.1.0.

    Returns:
        A function building such a repo at a path, given its `_src_path`.
    """

    def make(path: Path, src_path: str) -> Path:
        repo = init_repo(path)
        (repo / ".copier-answers.yml").write_text(
            "# Changes here will be overwritten by Copier\n"
            "_commit: 0.1.0\n"
            f"_src_path: {src_path}\n"
            "greeting: hello\n"
        )
        commit_file(repo, "greeting.txt", "hello\n", "initial")
        _git(repo, "branch", "-M", "main")
        return repo

    return make


@pytest.fixture
def real_template(tmp_path: Path, make_template: Callable[[Path], Path]) -> Path:
    return make_template(tmp_path / "template")


@pytest.fixture
def project(
    tmp_path: Path, real_template: Path, make_project: Callable[[Path, str], Path]
) -> Path:
    return make_project(tmp_path / "project", str(real_template))
