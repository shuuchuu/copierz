from __future__ import annotations

from typing import TYPE_CHECKING

from copierz.config import (
    Config,
    TemplateSource,
    TrackedRepo,
    load_config,
    save_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_load_config_missing_file_returns_empty(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nope" / "config.toml")
    assert config.templates == []
    assert config.repos == []


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config(
        templates=[TemplateSource(name="tmpl", path=tmp_path / "template")],
        repos=[
            TrackedRepo(
                path=tmp_path / "project", template="tmpl", branches=["main", "dev"]
            )
        ],
    )
    save_config(config, config_path)

    loaded = load_config(config_path)
    assert loaded.templates == config.templates
    assert loaded.repos == config.repos


def test_workspace_root_defaults_to_repo_parent(tmp_path: Path) -> None:
    repo = TrackedRepo(path=tmp_path / "projects" / "proj", template="tmpl")
    assert repo.resolved_workspace_root() == tmp_path / "projects"


def test_workspace_root_can_be_overridden(tmp_path: Path) -> None:
    repo = TrackedRepo(
        path=tmp_path / "projects" / "proj",
        template="tmpl",
        workspace_root=tmp_path,
    )
    assert repo.resolved_workspace_root() == tmp_path


def test_save_and_load_round_trip_with_workspace_root_override(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config = Config(
        templates=[TemplateSource(name="tmpl", path=tmp_path / "template")],
        repos=[
            TrackedRepo(
                path=tmp_path / "project",
                template="tmpl",
                branches=["main"],
                workspace_root=tmp_path / "elsewhere",
            )
        ],
    )
    save_config(config, config_path)

    loaded = load_config(config_path)
    assert loaded.repos == config.repos


def test_template_by_name_and_repo_by_path(tmp_path: Path) -> None:
    template = TemplateSource(name="tmpl", path=tmp_path / "template")
    repo = TrackedRepo(path=tmp_path / "project", template="tmpl", branches=["main"])
    config = Config(templates=[template], repos=[repo])

    assert config.template_by_name("tmpl") is template
    assert config.template_by_name("missing") is None
    assert config.repo_by_path(tmp_path / "project") is repo
    assert config.repo_by_path(tmp_path / "missing") is None
