"""Keep a project's `uv.lock` in step with its `pyproject.toml`.

A template update or a rebase can change `pyproject.toml` (new dev tools,
version bumps) without touching `uv.lock`, which `uv` then refuses to use
as is (`uv lock --check`, `uv sync --locked`).
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from copierz import git

if TYPE_CHECKING:
    from pathlib import Path

LOCK_FILE = "uv.lock"


class LockError(RuntimeError):
    """`uv lock` failed."""


def relock(checkout: Path) -> bool:
    """Run `uv lock` in `checkout` if it tracks a `uv.lock`.

    Returns:
        Whether `uv.lock` now differs from HEAD's.

    Raises:
        LockError: if `uv lock` failed, or uv isn't installed.
    """
    if not git.is_tracked(checkout, LOCK_FILE):
        return False
    uv = shutil.which("uv")
    if uv is None:
        msg = "uv not found"
        raise LockError(msg)
    result = subprocess.run(
        [uv, "lock", "--quiet"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LockError(result.stderr.strip() or f"exit code {result.returncode}")
    return LOCK_FILE in git.changed_vs_head(checkout)
