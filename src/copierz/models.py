"""Data types describing the update status of a tracked repo/branch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from copierz.discovery import Target


class StatusKind(Enum):
    UP_TO_DATE = "up to date"
    BEHIND = "behind"
    BLOCKED = "behind, dirty"
    IN_PROGRESS = "to commit"
    CONFLICTS = "conflicts"
    ERROR = "error"


@dataclass
class BranchStatus:
    """Where one target stands relative to its template's latest version.

    Attributes:
        target: The repo branch.
        kind: The status itself.
        parent: The branch it's stacked on, if any (see `copierz.stacks`).
        checkout: Where the branch is checked out (main checkout or a
            worktree), if anywhere.
        latest_ref: The template's latest version, if it could be resolved.
        detail: A human-readable explanation, mostly for non-trivial kinds.
    """

    target: Target
    kind: StatusKind
    parent: str | None = None
    checkout: Path | None = None
    latest_ref: str | None = None
    detail: str = ""

    @property
    def current_ref(self) -> str | None:
        return self.target.answers.commit
