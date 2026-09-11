"""The main copierz screen: a table of tracked repo/branch update statuses."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from copierz import git
from copierz.config import (
    Config,
    TrackedRepo,
    default_config_path,
    load_config,
    save_config,
)
from copierz.discovery import scan_and_track
from copierz.models import BranchStatus, StatusKind
from copierz.status import compute_all_statuses, compute_branch_status
from copierz.updater import UpdateError, run_update

from .screens import ReviewScreen, ScanRootScreen

STATUS_STYLE = {
    StatusKind.UP_TO_DATE: "green",
    StatusKind.UPDATE_AVAILABLE: "yellow",
    StatusKind.CONFLICTS: "bold red",
    StatusKind.DIRTY: "red",
    StatusKind.NOT_TRACKED: "dim",
    StatusKind.ERROR: "bold red",
}


class CopierzApp(App[None]):
    """Track copier-templated repos/branches and drive their updates."""

    TITLE = "copierz"

    BINDINGS: ClassVar = [
        Binding("r", "refresh", "Refresh"),
        Binding("f", "fetch_refresh", "Fetch + refresh"),
        Binding("u", "update_selected", "Update"),
        Binding("enter", "review_selected", "Review / resolve"),
        Binding("a", "add_repo", "Scan for repos"),
        Binding("x", "remove_selected", "Stop tracking"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.config_path = config_path or default_config_path()
        self.config: Config = load_config(self.config_path)
        self.statuses: list[BranchStatus] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="table")
        yield Static(id="message")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "Repo", "Branch", "Template", "Status", "Current", "Latest", "Detail"
        )
        self.refresh_statuses()

    def set_message(self, text: str) -> None:
        self.query_one("#message", Static).update(text)

    def refresh_statuses(self, fetch: bool = False) -> None:
        self.statuses = compute_all_statuses(self.config, fetch=fetch)
        self._render_table()
        if not self.config.repos:
            self.set_message("No repos tracked yet. Press 'a' to scan for some.")

    def _render_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for status in self.statuses:
            style = STATUS_STYLE.get(status.status, "")
            table.add_row(
                status.repo_path.name,
                status.branch,
                status.template_name,
                Text(status.status.value, style=style),
                (status.current_ref or "-")[:16],
                (status.latest_ref or "-")[:16],
                status.detail,
            )

    def _selected_status(self) -> BranchStatus | None:
        table = self.query_one(DataTable)
        if table.cursor_row is None or not self.statuses:
            return None
        if table.cursor_row >= len(self.statuses):
            return None
        return self.statuses[table.cursor_row]

    def _tracked_repo_for(self, status: BranchStatus) -> TrackedRepo | None:
        return self.config.repo_by_path(status.repo_path)

    def action_refresh(self) -> None:
        self.refresh_statuses(fetch=False)
        self.set_message("Refreshed.")

    def action_fetch_refresh(self) -> None:
        self.set_message("Fetching...")
        self.refresh_statuses(fetch=True)
        self.set_message("Fetched and refreshed.")

    def action_update_selected(self) -> None:
        status = self._selected_status()
        if status is None:
            return
        repo = self._tracked_repo_for(status)
        if repo is None:
            return
        try:
            run_update(self.config, repo, status.branch)
        except UpdateError as exc:
            self.set_message(f"[red]Update failed:[/red] {exc}")
            return
        new_status = compute_branch_status(self.config, repo, status.branch)

        with self.suspend():
            if new_status.status is StatusKind.CONFLICTS:
                git.mergetool(repo.path)
            elif new_status.status is StatusKind.DIRTY:
                git.difftool_dir_diff(repo.path)

        new_status = compute_branch_status(self.config, repo, status.branch)
        self.statuses[self.statuses.index(status)] = new_status
        self._render_table()
        if new_status.status is StatusKind.CONFLICTS:
            self.set_message("Update applied with conflicts — resolve them below.")
        else:
            self.set_message("Update applied. Review and commit the changes.")
        self.push_screen(ReviewScreen(repo.path, new_status))

    def action_review_selected(self) -> None:
        status = self._selected_status()
        if status is None:
            return
        self.push_screen(ReviewScreen(status.repo_path, status))

    def action_add_repo(self) -> None:
        def handle_result(root_str: str | None) -> None:
            if root_str is None:
                return
            root = Path(root_str).expanduser().resolve()
            summary = scan_and_track(self.config, root)
            save_config(self.config, self.config_path)
            self.refresh_statuses()

            message = (
                f"Scanned {root}: found {summary.repos_found} repo(s), "
                f"{summary.branches_found} branch(es); "
                f"added {summary.templates_added} template(s), "
                f"{summary.branches_added} new branch(es)."
            )
            if summary.warnings:
                message += " " + "; ".join(summary.warnings)
            self.set_message(message)

        self.push_screen(ScanRootScreen(str(Path.cwd())), handle_result)

    def action_remove_selected(self) -> None:
        status = self._selected_status()
        if status is None:
            return
        repo = self._tracked_repo_for(status)
        if repo is None:
            return
        if status.branch in repo.branches:
            repo.branches.remove(status.branch)
        if not repo.branches:
            self.config.repos.remove(repo)
        save_config(self.config, self.config_path)
        self.refresh_statuses()
        self.set_message(f"Stopped tracking {status.repo_path.name}@{status.branch}.")
