"""Load and save the copierz configuration file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w


@dataclass
class TemplateSource:
    """A copier template that one or more repos are tracking."""

    name: str
    path: Path
    workspace_root: Path | None = None

    def resolved_workspace_root(self) -> Path:
        """Directory to run Copier from so that relative `_src_path` values resolve.

        Copier resolves a relative `_src_path` against the current working
        directory at update time, so it must match how the answers file was
        originally produced (e.g. running `copier copy template dest` from the
        parent of both).

        Returns:
            The configured workspace root, or the template's parent directory.
        """
        return self.workspace_root or self.path.parent


@dataclass
class TrackedRepo:
    """A repo/branch set that should be kept up to date with a template."""

    path: Path
    template: str
    branches: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Full copierz configuration."""

    templates: list[TemplateSource] = field(default_factory=list)
    repos: list[TrackedRepo] = field(default_factory=list)

    def template_by_name(self, name: str) -> TemplateSource | None:
        return next((t for t in self.templates if t.name == name), None)

    def repo_by_path(self, path: Path) -> TrackedRepo | None:
        return next((r for r in self.repos if r.path == path), None)


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "copierz" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    templates = [
        TemplateSource(
            name=t["name"],
            path=Path(t["path"]),
            workspace_root=(
                Path(t["workspace_root"]) if t.get("workspace_root") else None
            ),
        )
        for t in data.get("templates", [])
    ]
    repos = [
        TrackedRepo(
            path=Path(r["path"]),
            template=r["template"],
            branches=list(r.get("branches", [])),
        )
        for r in data.get("repos", [])
    ]
    return Config(templates=templates, repos=repos)


def save_config(config: Config, path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "templates": [
            {
                "name": t.name,
                "path": str(t.path),
                **(
                    {"workspace_root": str(t.workspace_root)}
                    if t.workspace_root
                    else {}
                ),
            }
            for t in config.templates
        ],
        "repos": [
            {"path": str(r.path), "template": r.template, "branches": r.branches}
            for r in config.repos
        ],
    }
    path.write_text(tomli_w.dumps(data))
