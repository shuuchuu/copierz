from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from copierz.cli import UsageError, build_parser
from tests.conftest import commit_file, git_out

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


def test_json_output_for_agents(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    commit_file(project, "greeting.txt", "hi\n", "diverge")
    root = ["--root", str(tmp_path)]

    _run(*root, "status", "--json")
    (branch,) = json.loads(capsys.readouterr().out)["branches"]
    assert branch | {"template": None} == {
        "repo": str(project),
        "branch": "main",
        "subproject": None,
        "template": None,
        "status": "behind",
        "current": "0.1.0",
        "latest": "0.2.0",
        "parent": None,
        "checkout": str(project),
        "unmerged": [],
        "detail": "",
    }

    assert _run(*root, "update", "--all", "--json") == 1
    (outcome,) = json.loads(capsys.readouterr().out)["outcomes"]
    assert outcome["outcome"] == "conflicts"

    _run(*root, "status", "--json")
    (branch,) = json.loads(capsys.readouterr().out)["branches"]
    assert (branch["status"], branch["unmerged"]) == ("conflicts", ["greeting.txt"])

    # An agent fixes the file itself, then continues non-interactively.
    (project / "greeting.txt").write_text("hi world\n")
    git_out(project, "add", "greeting.txt")
    assert _run(*root, "continue", "--json") == 0
    (outcome,) = json.loads(capsys.readouterr().out)["outcomes"]
    assert outcome["outcome"] == "updated"

    _run(*root, "log", "--json")
    (template,) = json.loads(capsys.readouterr().out)["templates"]
    assert template["in_use"] == {"0.2.0": ["project@main"]}


def test_push_dry_run_then_force(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = tmp_path / "remote.git"
    git_out(tmp_path, "clone", "-q", "--bare", str(project), str(bare))
    git_out(project, "remote", "add", "origin", str(bare))
    git_out(project, "fetch", "-q", "origin")
    git_out(project, "branch", "-q", "--set-upstream-to=origin/main", "main")
    git_out(project, "commit", "-q", "--amend", "-m", "Reworded")
    root = ["--root", str(project)]

    assert _run(*root, "push", "--all", "--dry-run") == 1
    assert "pass --force" in " ".join(capsys.readouterr().out.split())

    assert _run(*root, "push", "--all", "--force", "--json") == 0
    (branch,) = json.loads(capsys.readouterr().out)["branches"]
    assert (branch["state"], branch["remote"]) == ("rewritten", "origin/main")
    assert git_out(bare, "log", "-1", "--format=%s") == "Reworded"
