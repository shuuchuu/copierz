from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from copierz.cli import UsageError, build_parser
from tests.conftest import git_out

if TYPE_CHECKING:
    from pathlib import Path


def _run(*argv: str) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


def test_status_lists_branches_and_suggests_next_step(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'roots = ["{tmp_path}"]\n')

    assert _run("--config", str(config)) == 0

    out = capsys.readouterr().out
    assert "project" in out
    assert "0.1.0 → 0.2.0" in out
    assert "copierz update --all" in out


def test_update_all_then_everything_is_up_to_date(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    git_out(project, "branch", "other")

    assert _run("--root", str(tmp_path), "update", "--all") == 0
    assert _run("--root", str(tmp_path), "status") == 0

    assert "Everything is up to date." in capsys.readouterr().out


def test_unknown_selector_is_a_usage_error(project: Path, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="nope"):
        _run("--root", str(tmp_path), "update", "nope")


def test_no_roots_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="roots"):
        _run("--config", str(tmp_path / "missing.toml"), "status")
