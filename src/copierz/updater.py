"""Run Copier updates and manage the resulting working-tree changes."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import copier

from copierz import git

if TYPE_CHECKING:
    from collections.abc import Iterator

    from copierz.config import Config, TrackedRepo


class UpdateError(RuntimeError):
    """A copier update could not be run, or its result could not be committed."""


@contextlib.contextmanager
def _chdir(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def run_update(config: Config, repo: TrackedRepo, branch: str) -> None:
    """Check out `branch` if needed and run `copier update` on it in place.

    Requires a clean working tree: copierz never stashes or discards changes
    on the caller's behalf. Leaves the result (possibly containing conflict
    markers and unmerged files, exactly as a `git merge` would) uncommitted
    for review.

    Raises:
        UpdateError: if the working tree isn't clean, or the template/repo
            isn't set up as expected.
    """
    if not git.is_git_repo(repo.path):
        msg = f"{repo.path} is not a git repository"
        raise UpdateError(msg)

    template = config.template_by_name(repo.template)
    if template is None:
        msg = f"unknown template {repo.template!r}"
        raise UpdateError(msg)

    current = git.current_branch(repo.path)
    if current != branch:
        if not git.is_clean(repo.path):
            msg = (
                f"cannot switch {repo.path.name} to {branch!r}: "
                f"working tree on {current!r} is dirty"
            )
            raise UpdateError(msg)
        git.checkout(repo.path, branch)

    if not git.is_clean(repo.path):
        msg = (
            f"working tree for {repo.path.name}@{branch} is dirty; "
            "commit or stash your changes first"
        )
        raise UpdateError(msg)

    with _chdir(template.resolved_workspace_root()):
        copier.run_update(
            dst_path=repo.path,
            defaults=True,
            skip_answered=True,
            overwrite=True,
            conflict="inline",
            quiet=True,
        )


@dataclass
class ChangedFile:
    status: str
    path: str
    conflicted: bool


def review_changes(repo_path: Path) -> list[ChangedFile]:
    unmerged = set(git.unmerged_files(repo_path))
    return [
        ChangedFile(status=code, path=path, conflicted=path in unmerged)
        for code, path in git.changed_files(repo_path)
    ]


def commit_update(repo_path: Path, template_name: str, ref: str | None) -> None:
    if git.unmerged_files(repo_path):
        msg = "cannot commit: unresolved conflicts remain"
        raise UpdateError(msg)
    if git.is_clean(repo_path):
        msg = "nothing to commit"
        raise UpdateError(msg)
    git.stage_all(repo_path)
    message = f"Update {template_name} template"
    if ref:
        message += f" to {ref}"
    git.commit(repo_path, message)
