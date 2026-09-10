"""Data types describing the update status of a tracked repo/branch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class StatusKind(Enum):
    UP_TO_DATE = "up to date"
    UPDATE_AVAILABLE = "update available"
    CONFLICTS = "conflicts"
    DIRTY = "dirty"
    NOT_TRACKED = "not tracked"
    ERROR = "error"


@dataclass
class BranchStatus:
    repo_path: Path
    branch: str
    template_name: str
    status: StatusKind
    current_ref: str | None = None
    latest_ref: str | None = None
    detail: str = ""
