from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from copierz.config import Config
from copierz.discovery import find_repos, resolve_template_path, scan_and_track
from copierz.template_source import Answers

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


def _commit_answers(repo: Path, src_path: str, commit: str = "0.1.0") -> None:
    (repo / ".copier-answers.yml").write_text(
        f"_commit: {commit}\n_src_path: {src_path}\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add answers")


def test_find_repos_discovers_nested_tracked_repo(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    root = tmp_path / "workspace"
    tracked = _init_repo(root / "projects" / "foo")
    _commit_answers(tracked, str(template))

    discovered = find_repos(root)

    assert len(discovered) == 1
    assert discovered[0].path == tracked
    assert len(discovered[0].branches) == 1
    assert discovered[0].branches[0].answers.commit == "0.1.0"


def test_find_repos_ignores_repo_without_answers(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    plain = _init_repo(root / "plain")
    (plain / "file.txt").write_text("x")
    _git(plain, "add", ".")
    _git(plain, "commit", "-q", "-m", "x")

    assert find_repos(root) == []


def test_find_repos_checks_every_local_branch(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    repo = _init_repo(tmp_path / "workspace" / "repo")
    _commit_answers(repo, str(template))
    default_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "extra.txt").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "extra")

    discovered = find_repos(tmp_path / "workspace")

    assert len(discovered) == 1
    branches = {b.branch for b in discovered[0].branches}
    assert branches == {default_branch, "other"}


def test_find_repos_does_not_descend_into_nested_git_repos(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    outer = _init_repo(tmp_path / "workspace" / "outer")
    _commit_answers(outer, str(template))
    inner = _init_repo(outer / "vendored")
    _commit_answers(inner, str(template))

    discovered = find_repos(tmp_path / "workspace")

    assert [d.path for d in discovered] == [outer]


def test_find_repos_skips_hidden_directories(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    hidden = _init_repo(tmp_path / "workspace" / ".cache" / "repo")
    _commit_answers(hidden, str(template))

    assert find_repos(tmp_path / "workspace") == []


def test_resolve_template_path_absolute(tmp_path: Path) -> None:
    answers = Answers(src_path=str(tmp_path / "template"), commit="0.1.0")
    assert resolve_template_path(answers, tmp_path) == (tmp_path / "template").resolve()


def test_resolve_template_path_relative(tmp_path: Path) -> None:
    answers = Answers(src_path="template", commit="0.1.0")
    assert resolve_template_path(answers, tmp_path) == (tmp_path / "template").resolve()


def test_scan_and_track_adds_template_repo_and_branch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    template = workspace / "template"
    template.mkdir(parents=True)
    repo = _init_repo(workspace / "proj")
    _commit_answers(repo, "template", commit="0.1.0")
    config = Config()

    summary = scan_and_track(config, workspace)

    assert summary.repos_found == 1
    assert summary.branches_found == 1
    assert summary.templates_added == 1
    assert summary.branches_added == 1
    assert summary.warnings == []
    assert len(config.templates) == 1
    assert config.templates[0].path == template.resolve()
    assert len(config.repos) == 1
    tracked = config.repos[0]
    assert tracked.path == repo
    assert tracked.template == config.templates[0].name


def test_scan_and_track_is_idempotent(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    repo = _init_repo(tmp_path / "workspace" / "proj")
    _commit_answers(repo, "template")
    config = Config()
    scan_and_track(config, tmp_path / "workspace")

    summary = scan_and_track(config, tmp_path / "workspace")

    assert summary.templates_added == 0
    assert summary.branches_added == 0
    assert len(config.templates) == 1
    assert len(config.repos) == 1


def test_scan_and_track_reuses_existing_template_by_path(tmp_path: Path) -> None:
    from copierz.config import TemplateSource

    workspace = tmp_path / "workspace"
    template = workspace / "template"
    template.mkdir(parents=True)
    repo = _init_repo(workspace / "proj")
    _commit_answers(repo, "template")
    config = Config(templates=[TemplateSource(name="mine", path=template.resolve())])

    summary = scan_and_track(config, workspace)

    assert summary.templates_added == 0
    assert len(config.templates) == 1
    assert config.repos[0].template == "mine"


def test_scan_and_track_resolves_relative_src_path_per_repo(tmp_path: Path) -> None:
    # Regression test: scanning from an ancestor above where a repo's
    # relative `_src_path` was originally recorded (its own parent
    # directory) must not corrupt the resolved template path. Mirrors a
    # real case: scanning from .../repos while one repo lives directly
    # under .../repos/shuuchuu (relative to it) and another lives under
    # .../repos/mog/invoicez, recording `_src_path: ../shuuchuu/template`
    # relative to .../repos/mog.
    root = tmp_path / "repos"
    template = root / "shuuchuu" / "template"
    template.mkdir(parents=True)

    sibling_repo = _init_repo(root / "shuuchuu" / "proj")
    _commit_answers(sibling_repo, "template")

    nested_repo = _init_repo(root / "mog" / "invoicez")
    _commit_answers(nested_repo, "../shuuchuu/template")

    config = Config()

    summary = scan_and_track(config, root)

    assert summary.warnings == []
    assert summary.templates_added == 1
    assert len(config.templates) == 1
    assert config.templates[0].path == template.resolve()
    tracked_templates = {r.path: r.template for r in config.repos}
    assert tracked_templates[sibling_repo] == config.templates[0].name
    assert tracked_templates[nested_repo] == config.templates[0].name


def test_scan_and_track_warns_on_conflicting_templates_per_repo(tmp_path: Path) -> None:
    template_a = tmp_path / "template-a"
    template_a.mkdir()
    template_b = tmp_path / "template-b"
    template_b.mkdir()
    repo = _init_repo(tmp_path / "workspace" / "proj")
    _commit_answers(repo, str(template_a))
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / ".copier-answers.yml").write_text(
        f"_commit: 0.1.0\n_src_path: {template_b}\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "switch template")
    config = Config()

    summary = scan_and_track(config, tmp_path / "workspace")

    assert summary.warnings
    assert summary.branches_added == 1
