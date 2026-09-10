from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from textual.widgets import DataTable, Static

from copierz import git
from copierz.config import Config, TemplateSource, TrackedRepo, save_config
from copierz.tui.app import CopierzApp

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config_path(tmp_path: Path, template_repo: Path, subproject_repo: Path) -> Path:
    branch = git.current_branch(subproject_repo)
    path = tmp_path / "config.toml"
    save_config(
        Config(
            templates=[TemplateSource(name="tmpl", path=template_repo)],
            repos=[
                TrackedRepo(path=subproject_repo, template="tmpl", branches=[branch])
            ],
        ),
        path,
    )
    return path


async def test_app_shows_tracked_branch_on_mount(config_path: Path) -> None:
    app = CopierzApp(config_path=config_path)
    async with app.run_test():
        table = app.query_one(DataTable)
        assert table.row_count == 1


async def test_app_with_no_config_shows_hint(tmp_path: Path) -> None:
    app = CopierzApp(config_path=tmp_path / "missing.toml")
    async with app.run_test():
        table = app.query_one(DataTable)
        assert table.row_count == 0
        message = app.query_one("#message", Static).content
        assert "Press 'a'" in str(message)


async def test_refresh_action_repopulates_table(config_path: Path) -> None:
    app = CopierzApp(config_path=config_path)
    async with app.run_test() as pilot:
        await pilot.press("r")
        table = app.query_one(DataTable)
        assert table.row_count == 1
