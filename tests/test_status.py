from __future__ import annotations

from typing import TYPE_CHECKING

from copierz import git
from copierz.config import Config, TemplateSource, TrackedRepo
from copierz.models import StatusKind
from copierz.status import compute_all_statuses, compute_branch_status

if TYPE_CHECKING:
    from pathlib import Path


def _config_for(template_repo: Path, subproject_repo: Path, branch: str) -> Config:
    template = TemplateSource(name="tmpl", path=template_repo)
    repo = TrackedRepo(path=subproject_repo, template="tmpl", branches=[branch])
    return Config(templates=[template], repos=[repo])


def test_update_available(template_repo: Path, subproject_repo: Path) -> None:
    branch = git.current_branch(subproject_repo)
    config = _config_for(template_repo, subproject_repo, branch)
    repo = config.repos[0]

    status = compute_branch_status(config, repo, branch)

    assert status.status is StatusKind.UPDATE_AVAILABLE
    assert status.current_ref == "0.1.0"
    assert status.latest_ref == "0.2.0"


def test_up_to_date(template_repo: Path, subproject_repo: Path) -> None:
    (subproject_repo / ".copier-answers.yml").write_text(
        f"_commit: 0.2.0\n_src_path: {template_repo}\n"
    )
    branch = git.current_branch(subproject_repo)
    git.stage_all(subproject_repo)
    git.commit(subproject_repo, "bump to 0.2.0")
    config = _config_for(template_repo, subproject_repo, branch)

    status = compute_branch_status(config, config.repos[0], branch)

    assert status.status is StatusKind.UP_TO_DATE


def test_dirty_working_tree_on_active_branch(
    template_repo: Path, subproject_repo: Path
) -> None:
    branch = git.current_branch(subproject_repo)
    (subproject_repo / "README.md").write_text("dirty\n")
    config = _config_for(template_repo, subproject_repo, branch)

    status = compute_branch_status(config, config.repos[0], branch)

    assert status.status is StatusKind.DIRTY


def test_not_tracked_when_answers_file_missing(
    template_repo: Path, tmp_path: Path
) -> None:
    import subprocess

    repo_path = tmp_path / "plain"
    repo_path.mkdir()
    subprocess.run(["git", "-C", str(repo_path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.name", "Test"], check=True
    )
    (repo_path / "file.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-q", "-m", "x"], check=True)
    branch = git.current_branch(repo_path)
    config = _config_for(template_repo, repo_path, branch)

    status = compute_branch_status(config, config.repos[0], branch)

    assert status.status is StatusKind.NOT_TRACKED


def test_error_on_unknown_template(subproject_repo: Path) -> None:
    branch = git.current_branch(subproject_repo)
    repo = TrackedRepo(path=subproject_repo, template="missing", branches=[branch])
    config = Config(templates=[], repos=[repo])

    status = compute_branch_status(config, repo, branch)

    assert status.status is StatusKind.ERROR


def test_error_on_non_git_repo(tmp_path: Path, template_repo: Path) -> None:
    repo = TrackedRepo(path=tmp_path / "nope", template="tmpl", branches=["main"])
    config = Config(
        templates=[TemplateSource(name="tmpl", path=template_repo)], repos=[repo]
    )

    status = compute_branch_status(config, repo, "main")

    assert status.status is StatusKind.ERROR


def test_compute_all_statuses_covers_every_branch(
    template_repo: Path, subproject_repo: Path
) -> None:
    branch = git.current_branch(subproject_repo)
    config = _config_for(template_repo, subproject_repo, branch)

    statuses = compute_all_statuses(config)

    assert len(statuses) == 1
    assert statuses[0].branch == branch
