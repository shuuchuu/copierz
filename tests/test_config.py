from __future__ import annotations

from pathlib import Path

import pytest

from copierz.config import Config, ConfigError, load_config


def test_missing_file_gives_empty_config(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing.toml") == Config()


def test_roots_and_exclude_are_expanded(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'roots = ["~/repos", "{tmp_path}/a/../b"]\n'
        f'exclude = ["~/repos/x", "{tmp_path}/y@main"]\n'
    )

    config = load_config(config_path)

    assert config.roots == [Path.home() / "repos", tmp_path / "b"]
    assert config.is_excluded(Path.home() / "repos" / "x", "any")
    assert config.is_excluded(tmp_path / "y", "main")
    assert not config.is_excluded(tmp_path / "y", "other")


def test_exclude_patterns(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'exclude = ["*@backup/*", "{tmp_path}/old-*", "~/repos/*@scratch"]\n'
    )

    config = load_config(config_path)

    assert config.is_excluded(tmp_path / "any", "backup/main-2026-10-22")
    assert not config.is_excluded(tmp_path / "any", "main")
    assert config.is_excluded(tmp_path / "old-thing", "main")
    assert config.is_excluded(Path.home() / "repos" / "a" / "b", "scratch")
    assert not config.is_excluded(Path.home() / "repos" / "a", "scratch-2")


def test_old_format_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[[repos]]\npath = "/x"\ntemplate = "t"\n')

    with pytest.raises(ConfigError, match="roots"):
        load_config(config_path)
