"""Find every branch of every git repo under the roots that carries Copier answers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from copierz import git
from copierz.template_source import read_answers

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from copierz.config import Config
    from copierz.template_source import Answers

SKIP_DIR_NAMES = {"node_modules"}


@dataclass(frozen=True)
class Target:
    """One branch of one repo generated from a Copier template."""

    repo: Path
    branch: str
    answers: Answers

    @property
    def label(self) -> str:
        return f"{self.repo.name}@{self.branch}"

    @property
    def workspace_root(self) -> Path:
        """Directory to run Copier from so that a relative `_src_path` resolves.

        Copier resolves a relative `_src_path` against the current working
        directory at update time, so it must match wherever `copier copy`
        was originally run from. That's virtually always the repo's own
        parent directory (you `cd` there, then `copier copy <src> <name>`).

        Returns:
            The repo's parent directory.
        """
        return self.repo.parent

    @property
    def template(self) -> Path:
        src_path = Path(self.answers.src_path).expanduser()
        if not src_path.is_absolute():
            src_path = self.workspace_root / src_path
        return src_path.resolve()


def discover(config: Config, roots: Iterable[Path] | None = None) -> list[Target]:
    """Scan the roots for repos, and every local branch for an answers file.

    Branches are read with `git show`, so no checkout is needed. Scanning
    stops descending at a repo (a vendored repo nested inside a tracked one
    is left alone), and skips hidden directories and linked worktrees.

    Returns:
        One target per non-excluded repo branch carrying Copier answers,
        sorted by repo path then branch name.
    """
    targets = []
    seen: set[Path] = set()
    for root in roots if roots is not None else config.roots:
        for repo in _iter_repo_roots(root.expanduser().resolve()):
            if repo in seen:
                continue
            seen.add(repo)
            for branch in git.list_local_branches(repo):
                if config.is_excluded(repo, branch):
                    continue
                answers = read_answers(repo, branch)
                if answers is not None:
                    targets.append(Target(repo, branch, answers))
    return sorted(targets, key=lambda t: (str(t.repo), t.branch))


def refresh(targets: Iterable[Target]) -> list[Target]:
    """Re-read targets' answers, which a commit or rebase may have changed.

    Returns:
        The targets whose branch still carries an answers file.
    """
    return [
        Target(target.repo, target.branch, answers)
        for target in targets
        if (answers := read_answers(target.repo, target.branch)) is not None
    ]


def _iter_repo_roots(path: Path) -> Iterator[Path]:
    if not path.is_dir():
        return
    if (path / ".git").exists():
        if git.is_repo_root(path):
            yield path
        return
    for child in sorted(path.iterdir()):
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and child.name not in SKIP_DIR_NAMES
        ):
            yield from _iter_repo_roots(child)
