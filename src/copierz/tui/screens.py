"""Modal and secondary screens: adding a repo, and reviewing/resolving an update."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from copierz import git
from copierz.updater import ChangedFile, UpdateError, commit_update, review_changes

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from copierz.models import BranchStatus


class AddRepoScreen(ModalScreen[tuple[str, str, str] | None]):
    """Prompt for a repo path, branch, and template path to start tracking."""

    CSS = """
    AddRepoScreen {
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

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Track a new repo/branch")
            yield Input(placeholder="Repo path", id="repo_path")
            yield Input(
                placeholder="Branch (default: repo's current branch)", id="branch"
            )
            yield Input(placeholder="Template path", id="template_path")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", id="add", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        repo_path = self.query_one("#repo_path", Input).value.strip()
        branch = self.query_one("#branch", Input).value.strip()
        template_path = self.query_one("#template_path", Input).value.strip()
        if not repo_path or not template_path:
            self.app.bell()
            return
        if not branch:
            try:
                branch = git.current_branch(Path(repo_path).expanduser())
            except git.GitError:
                self.app.bell()
                return
        self.dismiss((repo_path, branch, template_path))


class ReviewScreen(Screen[None]):
    """List the working-tree changes left by an update and resolve conflicts."""

    BINDINGS: ClassVar = [
        Binding("e", "edit_selected", "Edit in $EDITOR"),
        Binding("s", "stage_selected", "Stage / mark resolved"),
        Binding("d", "diff_selected", "View diff"),
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
        changed_file = self._selected_file()
        if changed_file is None:
            return
        pager = os.environ.get("PAGER", "less")
        repo = shlex.quote(str(self.repo_path))
        target = shlex.quote(changed_file.path)
        with self.app.suspend():
            subprocess.run(
                f"git -C {repo} diff HEAD -- {target} | {pager}",
                shell=True,
                check=False,
            )

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
        self.app.pop_screen()
