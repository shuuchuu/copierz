"""End-to-end tests driving a real (tiny) Copier template through an update."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from copierz import git
from copierz.config import Config, TemplateSource, TrackedRepo
from copierz.updater import UpdateError, commit_update, review_changes, run_update

if TYPE_CHECKING:
    from pathlib import Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


@pytest.fixture
def real_template(tmp_path: Path) -> Path:
    # A tiny but real Copier template with two tagged versions. Mirrors the
    # shape of the actual shuuchuu template: a `_subdirectory` pointing at
    # the rendered content, and the special
    # `{{_copier_conf.answers_file}}.jinja` file that Copier requires in
    # order to write/update `.copier-answers.yml` in generated projects.
    repo = _init_repo(tmp_path / "template")
    (repo / "copier.yml").write_text(
        "_subdirectory: template\ngreeting:\n  type: str\n  default: hello\n"
    )
    template_dir = repo / "template"
    template_dir.mkdir()
    (template_dir / "greeting.txt.jinja").write_text("{{ greeting }}\n")
    (template_dir / "{{_copier_conf.answers_file}}.jinja").write_text(
        "# Changes here will be overwritten by Copier\n"
        "{{ _copier_answers|to_nice_yaml -}}\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "v1")
    _git(repo, "tag", "0.1.0")

    (template_dir / "greeting.txt.jinja").write_text("{{ greeting }} world\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "v2")
    _git(repo, "tag", "0.2.0")
    return repo


@pytest.fixture
def rendered_project(tmp_path: Path, real_template: Path) -> Path:
    # As if `copier copy` had produced it at template version 0.1.0.
    repo = _init_repo(tmp_path / "project")
    (repo / "greeting.txt").write_text("hello\n")
    (repo / ".copier-answers.yml").write_text(
        "# Changes here will be overwritten by Copier\n"
        "_commit: 0.1.0\n"
        f"_src_path: {real_template}\n"
        "greeting: hello\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _config(template: Path, repo: Path, branch: str) -> tuple[Config, TrackedRepo]:
    template_source = TemplateSource(name="tmpl", path=template)
    tracked = TrackedRepo(path=repo, template="tmpl", branches=[branch])
    return Config(templates=[template_source], repos=[tracked]), tracked


def test_run_update_applies_cleanly(
    real_template: Path, rendered_project: Path
) -> None:
    branch = git.current_branch(rendered_project)
    config, repo = _config(real_template, rendered_project, branch)

    run_update(config, repo, branch)

    assert (rendered_project / "greeting.txt").read_text() == "hello world\n"
    assert git.unmerged_files(rendered_project) == []
    assert any(c.path == "greeting.txt" for c in review_changes(rendered_project))


def test_run_update_then_commit(real_template: Path, rendered_project: Path) -> None:
    branch = git.current_branch(rendered_project)
    config, repo = _config(real_template, rendered_project, branch)
    run_update(config, repo, branch)

    commit_update(rendered_project, "tmpl", "0.2.0")

    assert git.is_clean(rendered_project)
    log = subprocess.run(
        ["git", "-C", str(rendered_project), "log", "-1", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "0.2.0" in log


def test_run_update_produces_conflict(
    real_template: Path, rendered_project: Path
) -> None:
    (rendered_project / "greeting.txt").write_text("hi\n")
    git.stage_all(rendered_project)
    git.commit(rendered_project, "diverge")
    branch = git.current_branch(rendered_project)
    config, repo = _config(real_template, rendered_project, branch)

    run_update(config, repo, branch)

    assert git.unmerged_files(rendered_project) == ["greeting.txt"]
    content = (rendered_project / "greeting.txt").read_text()
    assert "<<<<<<< before updating" in content

    with pytest.raises(UpdateError):
        commit_update(rendered_project, "tmpl", "0.2.0")


def test_run_update_requires_clean_tree(
    real_template: Path, rendered_project: Path
) -> None:
    (rendered_project / "greeting.txt").write_text("uncommitted\n")
    branch = git.current_branch(rendered_project)
    config, repo = _config(real_template, rendered_project, branch)

    with pytest.raises(UpdateError):
        run_update(config, repo, branch)


def test_run_update_switches_branch_when_clean(
    real_template: Path, rendered_project: Path
) -> None:
    original_branch = git.current_branch(rendered_project)
    _git(rendered_project, "checkout", "-q", "-b", "other")
    _git(rendered_project, "checkout", "-q", original_branch)
    config, repo = _config(real_template, rendered_project, "other")

    run_update(config, repo, "other")

    assert git.current_branch(rendered_project) == "other"


def test_commit_update_raises_when_nothing_to_commit(rendered_project: Path) -> None:
    with pytest.raises(UpdateError):
        commit_update(rendered_project, "tmpl", "0.2.0")


def test_run_update_resolves_relative_src_path_against_repos_own_parent(
    tmp_path: Path,
) -> None:
    # Regression test: the template and the tracked repo deliberately do NOT
    # share a parent directory (unlike the `real_template`/`rendered_project`
    # fixtures above, which live side by side under the same tmp_path and so
    # can't catch a resolution bug like this). This mirrors a real reported
    # case: a repo at .../repos/mog/invoicez recording `_src_path: ../shuuchuu/
    # template`, relative to its own parent .../repos/mog, while the template
    # itself lives under a sibling of .../repos/mog named .../repos/shuuchuu.
    workspace = tmp_path / "workspace"
    template = _init_repo(workspace / "shuuchuu" / "template")
    (template / "copier.yml").write_text(
        "_subdirectory: template\ngreeting:\n  type: str\n  default: hello\n"
    )
    template_dir = template / "template"
    template_dir.mkdir()
    (template_dir / "greeting.txt.jinja").write_text("{{ greeting }}\n")
    (template_dir / "{{_copier_conf.answers_file}}.jinja").write_text(
        "# Changes here will be overwritten by Copier\n"
        "{{ _copier_answers|to_nice_yaml -}}\n"
    )
    _git(template, "add", ".")
    _git(template, "commit", "-q", "-m", "v1")
    _git(template, "tag", "0.1.0")
    (template_dir / "greeting.txt.jinja").write_text("{{ greeting }} world\n")
    _git(template, "add", ".")
    _git(template, "commit", "-q", "-m", "v2")
    _git(template, "tag", "0.2.0")

    repo = _init_repo(workspace / "elsewhere" / "project")
    (repo / "greeting.txt").write_text("hello\n")
    (repo / ".copier-answers.yml").write_text(
        "# Changes here will be overwritten by Copier\n"
        "_commit: 0.1.0\n"
        "_src_path: ../shuuchuu/template\n"
        "greeting: hello\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")

    branch = git.current_branch(repo)
    template_source = TemplateSource(name="tmpl", path=template)
    tracked = TrackedRepo(path=repo, template="tmpl", branches=[branch])
    config = Config(templates=[template_source], repos=[tracked])

    run_update(config, tracked, branch)

    assert (repo / "greeting.txt").read_text() == "hello world\n"
    assert git.unmerged_files(repo) == []
