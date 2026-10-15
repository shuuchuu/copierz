"""Load the copierz configuration file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """The configuration file is invalid."""


@dataclass
class Config:
    """Where to look for Copier-generated repos, and what to leave out.

    Attributes:
        roots: Directories scanned recursively for git repos on every run.
        exclude: Repos (`/path/to/repo`) or single branches
            (`/path/to/repo@branch`) to ignore even though they carry a
            Copier answers file.
    """

    roots: list[Path] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def is_excluded(self, repo: Path, branch: str) -> bool:
        return str(repo) in self.exclude or f"{repo}@{branch}" in self.exclude


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "copierz" / "config.toml"


def default_cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "copierz"


def _expand(raw: str) -> str:
    path, sep, branch = raw.partition("@")
    return str(Path(path).expanduser().resolve()) + sep + branch


def load_config(path: Path | None = None) -> Config:
    """Read the config file, expanding `~` and normalizing every path.

    Returns:
        The parsed config, or an empty one if the file doesn't exist.

    Raises:
        ConfigError: if the file still uses the old `templates`/`repos` format.
    """
    path = path or default_config_path()
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    if "repos" in data or "templates" in data:
        msg = (
            f"{path} uses the old format: replace its [[templates]]/[[repos]] "
            'entries with `roots = ["~/repos"]` (repos are now discovered on '
            "every run)"
        )
        raise ConfigError(msg)
    return Config(
        roots=[Path(_expand(root)) for root in data.get("roots", [])],
        exclude=[_expand(entry) for entry in data.get("exclude", [])],
    )
