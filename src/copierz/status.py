"""Compute the copier-update status of tracked repos and branches."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from copierz import git
from copierz.models import BranchStatus, StatusKind
from copierz.template_source import latest_template_ref, read_answers

if TYPE_CHECKING:
    from copierz.config import Config, TrackedRepo


def compute_branch_status(
    config: Config, repo: TrackedRepo, branch: str
) -> BranchStatus:
    if not git.is_git_repo(repo.path):
        return BranchStatus(
            repo.path,
            branch,
            repo.template,
            StatusKind.ERROR,
            detail="not a git repository",
        )

    template = config.template_by_name(repo.template)
    if template is None:
        return BranchStatus(
            repo.path,
            branch,
            repo.template,
            StatusKind.ERROR,
            detail=f"unknown template {repo.template!r}",
        )

    try:
        answers = read_answers(repo.path, branch)
    except git.GitError as exc:
        return BranchStatus(
            repo.path, branch, repo.template, StatusKind.ERROR, detail=str(exc)
        )
    if answers is None:
        return BranchStatus(
            repo.path,
            branch,
            repo.template,
            StatusKind.NOT_TRACKED,
            detail="no .copier-answers.yml on this branch",
        )

    try:
        latest = latest_template_ref(template.path)
    except git.GitError as exc:
        return BranchStatus(
            repo.path,
            branch,
            repo.template,
            StatusKind.ERROR,
            current_ref=answers.commit,
            detail=str(exc),
        )

    is_active_branch = False
    with contextlib.suppress(git.GitError):
        is_active_branch = git.current_branch(repo.path) == branch

    if is_active_branch and git.unmerged_files(repo.path):
        return BranchStatus(
            repo.path,
            branch,
            repo.template,
            StatusKind.CONFLICTS,
            current_ref=answers.commit,
            latest_ref=latest,
            detail="update in progress, unresolved conflicts",
        )

    if is_active_branch and not git.is_clean(repo.path):
        return BranchStatus(
            repo.path,
            branch,
            repo.template,
            StatusKind.DIRTY,
            current_ref=answers.commit,
            latest_ref=latest,
            detail="working tree has uncommitted changes",
        )

    if answers.commit == latest:
        return BranchStatus(
            repo.path,
            branch,
            repo.template,
            StatusKind.UP_TO_DATE,
            current_ref=answers.commit,
            latest_ref=latest,
        )

    return BranchStatus(
        repo.path,
        branch,
        repo.template,
        StatusKind.UPDATE_AVAILABLE,
        current_ref=answers.commit,
        latest_ref=latest,
    )


def compute_all_statuses(config: Config, fetch: bool = False) -> list[BranchStatus]:
    statuses = []
    for repo in config.repos:
        if fetch and git.is_git_repo(repo.path):
            git.fetch(repo.path)
        for branch in repo.branches:
            statuses.append(compute_branch_status(config, repo, branch))
    return statuses
