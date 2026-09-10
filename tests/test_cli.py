from __future__ import annotations

from typing import TYPE_CHECKING

from copierz import git
from copierz.cli import build_parser
from copierz.config import Config, TemplateSource, TrackedRepo, save_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_status_with_no_config_reports_nothing_tracked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    args = parser.parse_args(["--config", str(tmp_path / "missing.toml"), "status"])

    assert args.func(args) == 0
    assert "No repos tracked" in capsys.readouterr().out


def test_status_prints_tracked_repo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    template_repo: Path,
    subproject_repo: Path,
) -> None:
    branch = git.current_branch(subproject_repo)
    config_path = tmp_path / "config.toml"
    save_config(
        Config(
            templates=[TemplateSource(name="tmpl", path=template_repo)],
            repos=[
                TrackedRepo(path=subproject_repo, template="tmpl", branches=[branch])
            ],
        ),
        config_path,
    )
    parser = build_parser()
    args = parser.parse_args(["--config", str(config_path), "status"])

    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert subproject_repo.name in out
    assert "update available" in out


def test_default_command_is_tui() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    from copierz.cli import _cmd_tui

    assert args.func is _cmd_tui
