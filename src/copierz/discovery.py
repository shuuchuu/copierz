"""Scan a directory tree for git repos with Copier answers, and track them."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from copierz import git
from copierz.config import TemplateSource, TrackedRepo
from copierz.template_source import read_answers

if TYPE_CHECKING:
    from copierz.config import Config
    from copierz.template_source import Answers

SKIP_DIR_NAMES = {"node_modules"}


@dataclass
class DiscoveredBranch:
    branch: str
    answers: Answers


@dataclass
class DiscoveredRepo:
    path: Path
    branches: list[DiscoveredBranch]


def find_repos(root: Path) -> list[DiscoveredRepo]:
    """Find git repos under `root` with at least one branch carrying Copier answers.

    Every local branch of every repo found is checked (via `git show`, no
    checkout needed), not just the currently checked-out one.

    Returns:
        One entry per repo that has at least one such branch.
    """
    discovered = []
    for repo_path in _iter_git_repo_roots(root):
        branches = [
            DiscoveredBranch(branch=branch, answers=answers)
            for branch in git.list_local_branches(repo_path)
            if (answers := read_answers(repo_path, branch)) is not None
        ]
        if branches:
            discovered.append(DiscoveredRepo(path=repo_path, branches=branches))
    return discovered


def _iter_git_repo_roots(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    if git.is_git_repo(path):
        return [path]
    found = []
    for child in sorted(path.iterdir()):
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and child.name not in SKIP_DIR_NAMES
        ):
            found.extend(_iter_git_repo_roots(child))
    return found


def resolve_template_path(answers: Answers, workspace_root: Path) -> Path:
    """Resolve `answers.src_path`, which Copier may have recorded as relative.

    Returns:
        The absolute, resolved path to the template.
    """
    src_path = Path(answers.src_path).expanduser()
    if not src_path.is_absolute():
        src_path = workspace_root / src_path
    return src_path.resolve()


@dataclass
class ScanSummary:
    repos_found: int = 0
    branches_found: int = 0
    templates_added: int = 0
    branches_added: int = 0
    warnings: list[str] = field(default_factory=list)


def scan_and_track(config: Config, root: Path) -> ScanSummary:
    """Discover repos under `root` and add any new templates/repos/branches.

    Already-tracked repos/branches are left alone: this only adds what's
    missing, so re-running a scan is safe.

    Returns:
        A summary of what was found and added.
    """
    root = root.expanduser().resolve()
    discovered = find_repos(root)
    summary = ScanSummary(
        repos_found=len(discovered),
        branches_found=sum(len(r.branches) for r in discovered),
    )

    for repo in discovered:
        template_paths = Counter(
            resolve_template_path(b.answers, root) for b in repo.branches
        )
        chosen_path, _ = template_paths.most_common(1)[0]
        if len(template_paths) > 1:
            summary.warnings.append(
                f"{repo.path.name}: branches reference different templates; "
                f"using {chosen_path}"
            )

        template = next((t for t in config.templates if t.path == chosen_path), None)
        if template is None:
            template = TemplateSource(
                name=chosen_path.name, path=chosen_path, workspace_root=root
            )
            config.templates.append(template)
            summary.templates_added += 1

        tracked = config.repo_by_path(repo.path)
        if tracked is None:
            tracked = TrackedRepo(path=repo.path, template=template.name, branches=[])
            config.repos.append(tracked)

        for branch in repo.branches:
            branch_template_path = resolve_template_path(branch.answers, root)
            if branch_template_path != chosen_path:
                summary.warnings.append(
                    f"{repo.path.name}@{branch.branch}: skipped, "
                    f"uses a different template ({branch_template_path})"
                )
                continue
            if branch.branch not in tracked.branches:
                tracked.branches.append(branch.branch)
                summary.branches_added += 1

    return summary
