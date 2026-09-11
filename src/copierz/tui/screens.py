"""Modal/secondary screens: scanning for repos, reviewing/resolving an update."""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import TYPE_CHECKING, ClassVar, cast

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from copierz import git
from copierz.updater import ChangedFile, UpdateError, commit_update, review_changes

if TYPE_CHECKING:
    from pathlib import Path

    from textual.app import ComposeResult

    from copierz.models import BranchStatus
    from copierz.tui.app import CopierzApp


class ScanRootScreen(ModalScreen[str | None]):
    """Prompt for a root directory to scan for Copier-tracked repos."""

    CSS = """
    ScanRootScreen {
        align: center middle;
    }
    #dialog {
        width: 70;
        height: auto;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #dialog Input {
        margin-bottom: 1;
    }
    #buttons {
        height: auto;
        align: right middle;
    }
    """

    def __init__(self, initial_root: str) -> None:
        super().__init__()
        self.initial_root = initial_root

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                "Scan for git repos with a .copier-answers.yml under a root directory"
            )
            yield Input(
                value=self.initial_root, placeholder="Root directory", id="root"
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Scan", id="scan", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        root = self.query_one("#root", Input).value.strip()
        if not root:
            self.app.bell()
            return
        self.dismiss(root)


class ReviewScreen(Screen[None]):
    """List the working-tree changes left by an update and resolve conflicts."""

    BINDINGS: ClassVar = [
        Binding("e", "edit_selected", "Edit in $EDITOR"),
        Binding("s", "stage_selected", "Stage / mark resolved"),
        Binding("d", "diff_selected", "Diff/merge selected"),
        Binding("D", "diff_all", "View full diff"),
        Binding("c", "commit", "Commit"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, repo_path: Path, status: BranchStatus) -> None:
        super().__init__()
        self.repo_path = repo_path
        self.status = status
        self.files: list[ChangedFile] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"{self.repo_path.name}@{self.status.branch} — "
            f"{self.status.current_ref or '?'} -> {self.status.latest_ref or '?'}",
            id="review_title",
        )
        yield DataTable(id="changes")
        yield Static(id="review_message")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Status", "Conflict", "Path")
        self.refresh_changes()

    def refresh_changes(self) -> None:
        self.files = review_changes(self.repo_path)
        table = self.query_one(DataTable)
        table.clear()
        for changed_file in self.files:
            conflict_marker = "yes" if changed_file.conflicted else ""
            table.add_row(changed_file.status, conflict_marker, changed_file.path)
        message = "No pending changes." if not self.files else ""
        self.query_one("#review_message", Static).update(message)

    def _selected_file(self) -> ChangedFile | None:
        table = self.query_one(DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self.files):
            return None
        return self.files[table.cursor_row]

    def action_edit_selected(self) -> None:
        changed_file = self._selected_file()
        if changed_file is None:
            return
        editor = shlex.split(os.environ.get("EDITOR", "vi"))
        target = self.repo_path / changed_file.path
        with self.app.suspend():
            subprocess.run([*editor, str(target)], check=False)
        self.refresh_changes()

    def action_stage_selected(self) -> None:
        changed_file = self._selected_file()
        if changed_file is None:
            return
        git.stage(self.repo_path, changed_file.path)
        self.refresh_changes()

    def action_diff_selected(self) -> None:
        """Open the selected file in the user's configured git difftool/mergetool.

        Conflicted files get a proper 3-way `git mergetool`, which also
        marks the file resolved (staged) once its result is saved; other
        changed files get a plain `git difftool` against HEAD.
        """
        changed_file = self._selected_file()
        if changed_file is None:
            return
        with self.app.suspend():
            if changed_file.conflicted:
                git.mergetool(self.repo_path, changed_file.path)
            else:
                git.difftool(self.repo_path, changed_file.path)
        self.refresh_changes()

    def action_diff_all(self) -> None:
        """Open every changed file at once in a whole-repo dir-diff view."""
        with self.app.suspend():
            git.difftool_dir_diff(self.repo_path)
        self.refresh_changes()

    def action_commit(self) -> None:
        try:
            commit_update(
                self.repo_path, self.status.template_name, self.status.latest_ref
            )
        except UpdateError as exc:
            self.query_one("#review_message", Static).update(f"[red]{exc}[/red]")
            return
        self.refresh_changes()
        self.query_one("#review_message", Static).update("Committed.")

    def action_back(self) -> None:
        cast("CopierzApp", self.app).refresh_statuses()
        self.app.pop_screen()
