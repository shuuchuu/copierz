"""Read a subproject's recorded answers and resolve a template's latest ref."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from packaging.version import InvalidVersion, Version

from copierz.git import GitError, show_file_at_ref

if TYPE_CHECKING:
    from pathlib import Path

ANSWERS_FILE = ".copier-answers.yml"


@dataclass
class Answers:
    src_path: str
    commit: str | None


def read_answers(
    repo: Path, ref: str, answers_relpath: str = ANSWERS_FILE
) -> Answers | None:
    """Read `.copier-answers.yml` as committed on `ref`, without checking it out.

    Returns:
        The parsed answers, or `None` if `ref` has no answers file.
    """
    raw = show_file_at_ref(repo, ref, answers_relpath)
    if raw is None:
        return None
    data = yaml.safe_load(raw) or {}
    src_path = data.get("_src_path")
    if not src_path:
        return None
    return Answers(src_path=src_path, commit=data.get("_commit"))


def latest_template_ref(template_path: Path, use_prereleases: bool = False) -> str:
    """Mirror Copier's own tag-resolution: highest PEP 440 git tag, else `HEAD`.

    This lets copierz tell whether an update is available without actually
    invoking Copier (which would need a checkout/render).

    Returns:
        The latest matching tag name, or `"HEAD"` if the template has none.

    Raises:
        GitError: if the template's tags could not be listed.
    """
    abspath = template_path.resolve()
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", str(abspath)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"could not list tags for template at {abspath}: {result.stderr.strip()}"
        raise GitError(msg)
    tags: list[tuple[Version, str]] = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        ref = line.split("\t", 1)[1].removeprefix("refs/tags/")
        try:
            version = Version(ref)
        except InvalidVersion:
            continue
        if version.is_prerelease and not use_prereleases:
            continue
        tags.append((version, ref))
    if not tags:
        return "HEAD"
    tags.sort(key=lambda pair: pair[0], reverse=True)
    return tags[0][1]
