"""Find every branch of every git repo under the roots that carries Copier answers.

A branch can carry several Copier projects: the repo itself, and projects
nested in its subdirectories (each with its own answers file), such as a
vendored repo. Each one is a target of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from copierz import git
from copierz.template_source import answers_path, project_dirs, read_answers

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from copierz.config import Config
    from copierz.template_source import Answers

SKIP_DIR_NAMES = {"node_modules"}


@dataclass(frozen=True)
class Target:
    """One Copier project on one branch of a repo.

    Attributes:
        repo: The repo's main checkout.
        branch: The branch.
        answers: The project's answers, as committed on the branch.
        subdir: Where the project is, relative to the repo root: "" for the
            repo itself, else a nested project's directory.
    """

    repo: Path
    branch: str
    answers: Answers
    subdir: str = ""

    @property
    def label(self) -> str:
        label = f"{self.repo.name}@{self.branch}"
        return f"{label}:{self.subdir}" if self.subdir else label

    @property
    def answers_file(self) -> str:
        """The project's answers file, relative to the repo root."""
        return answers_path(self.subdir)

    @property
    def workspace_root(self) -> Path:
        """Directory to run Copier from so that a relative `_src_path` resolves.

        Copier resolves a relative `_src_path` against the current working
        directory at update time, so it must match wherever `copier copy`
        was originally run from. That's virtually always the project's own
        parent directory (you `cd` there, then `copier copy <src> <name>`).

        Returns:
            The parent directory of the project (of the repo, for its root
            project).
        """
        return (self.repo / self.subdir).parent

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
        One target per Copier project on each non-excluded repo branch,
        sorted by repo path, branch name, then project (root first). A nested
        project is excluded with its path (`/path/to/repo/subdir`).
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
                for subdir in project_dirs(repo, branch):
                    if subdir and config.is_excluded(repo / subdir, branch):
                        continue
                    answers = read_answers(repo, branch, answers_path(subdir))
                    if answers is not None:
                        targets.append(Target(repo, branch, answers, subdir))
    return sorted(targets, key=lambda t: (str(t.repo), t.branch, t.subdir))


def refresh(targets: Iterable[Target]) -> list[Target]:
    """Re-read targets' answers, which a commit or rebase may have changed.

    Returns:
        The targets whose branch still carries an answers file.
    """
    return [
        Target(target.repo, target.branch, answers, target.subdir)
        for target in targets
        if (answers := read_answers(target.repo, target.branch, target.answers_file))
        is not None
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
