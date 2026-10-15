"""Command-line interface: status, update, resolve, abort, log."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version
from rich.console import Console
from rich.table import Table
from rich.text import Text

from copierz import git
from copierz.config import ConfigError, default_cache_dir, load_config
from copierz.discovery import discover, refresh
from copierz.models import StatusKind
from copierz.stacks import depth
from copierz.status import LatestRefs, compute_statuses, repo_statuses
from copierz.updater import Outcome, OutcomeKind, Updater

if TYPE_CHECKING:
    from collections.abc import Iterable

    from copierz.discovery import Target
    from copierz.models import BranchStatus

console = Console(highlight=False)

STATUS_STYLE = {
    StatusKind.UP_TO_DATE: "green",
    StatusKind.BEHIND: "yellow",
    StatusKind.BLOCKED: "red",
    StatusKind.IN_PROGRESS: "cyan",
    StatusKind.CONFLICTS: "bold red",
    StatusKind.ERROR: "bold red",
}

OUTCOME_STYLE = {
    OutcomeKind.UPDATED: "green",
    OutcomeKind.REBASED: "green",
    OutcomeKind.UNCOMMITTED: "cyan",
    OutcomeKind.CONFLICTS: "bold red",
    OutcomeKind.SKIPPED: "yellow",
    OutcomeKind.FAILED: "bold red",
}

PENDING = {StatusKind.CONFLICTS, StatusKind.IN_PROGRESS}


class UsageError(RuntimeError):
    """The command line doesn't make sense (e.g. a selector matches nothing)."""


def _pretty(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _load_targets(args: argparse.Namespace) -> list[Target]:
    config = load_config(args.config)
    roots = args.root or config.roots
    if not roots:
        msg = (
            'no roots to scan: add `roots = ["~/repos"]` to '
            f"{args.config or '~/.config/copierz/config.toml'}, or pass --root"
        )
        raise UsageError(msg)
    return discover(config, roots)


def _matches(selector: str, target: Target) -> bool:
    repo_part, _, branch = selector.partition("@")
    if branch and branch != target.branch:
        return False
    if repo_part == target.repo.name:
        return True
    candidate = Path(repo_part).expanduser().resolve()
    return candidate == target.repo or (
        repo_part == "." and candidate.is_relative_to(target.repo)
    )


def _select(
    statuses: list[BranchStatus], selectors: Iterable[str]
) -> list[BranchStatus]:
    selectors = list(selectors)
    for selector in selectors:
        if not any(_matches(selector, s.target) for s in statuses):
            msg = f"{selector!r} matches no Copier-generated repo branch"
            raise UsageError(msg)
    return [s for s in statuses if any(_matches(sel, s.target) for sel in selectors)]


def _whole_stacks(
    all_statuses: list[BranchStatus], selected: list[BranchStatus]
) -> list[BranchStatus]:
    """Extend a selection to every branch sharing a stack with a selected one.

    Returns:
        The selected statuses plus their stack siblings, in their original order.
    """
    parents = {(s.target.repo, s.target.branch): s.parent for s in all_statuses}

    def root(status: BranchStatus) -> tuple[Path, str]:
        key = (status.target.repo, status.target.branch)
        while (parent := parents.get(key)) is not None:
            key = (key[0], parent)
        return key

    roots = {root(s) for s in selected}
    return [s for s in all_statuses if root(s) in roots]


def _by_repo(statuses: list[BranchStatus]) -> dict[Path, list[BranchStatus]]:
    grouped: dict[Path, list[BranchStatus]] = defaultdict(list)
    for status in statuses:
        grouped[status.target.repo].append(status)
    return grouped


def _versions(status: BranchStatus) -> str:
    current, latest = status.current_ref or "?", status.latest_ref
    if latest is None or current == latest:
        return current
    return f"{current} → {latest}"


def _print_statuses(statuses: list[BranchStatus]) -> None:
    table = Table(box=None, show_header=True, header_style="dim", pad_edge=False)
    table.add_column("repo / branch")
    table.add_column("version")
    table.add_column("status")
    table.add_column("detail", style="dim")
    templates = {s.target.template for s in statuses}
    for repo, repo_statuses_ in _by_repo(statuses).items():
        header = Text(repo.name, style="bold")
        if len(templates) > 1:
            header.append(f"  {repo_statuses_[0].target.template.name}", style="dim")
        table.add_row(header)
        parents = {s.target.branch: s.parent for s in repo_statuses_}
        for status in repo_statuses_:
            level = depth(parents, status.target.branch)
            branch = "  " + "   " * (level - 1) + " └ " if level else "  "
            detail = status.detail
            if status.kind in PENDING and status.checkout is not None:
                detail = f"{detail} ({_pretty(status.checkout)})".strip()
            table.add_row(
                branch + status.target.branch,
                _versions(status),
                Text(status.kind.value, style=STATUS_STYLE[status.kind]),
                detail,
            )
    console.print(table)


def _print_summary(statuses: list[BranchStatus]) -> None:
    counts: dict[StatusKind, int] = defaultdict(int)
    for status in statuses:
        counts[status.kind] += 1
    parts = [
        Text(f"{count} {kind.value}", style=STATUS_STYLE[kind])
        for kind, count in counts.items()
        if kind is not StatusKind.UP_TO_DATE
    ]
    if not parts:
        console.print(Text("Everything is up to date.", style="green"))
        return
    hints = []
    if counts[StatusKind.BEHIND]:
        hints.append("`copierz update --all`")
    if counts[StatusKind.CONFLICTS] or counts[StatusKind.IN_PROGRESS]:
        hints.append("`copierz resolve`")
    summary = Text(", ").join(parts)
    if hints:
        summary.append(f" — next: {' then '.join(hints)}", style="dim")
    console.print(summary)


def _report(outcome: Outcome) -> None:
    line = Text(f"{outcome.target.label}  ", style="bold")
    line.append(outcome.kind.value, style=OUTCOME_STYLE[outcome.kind])
    if outcome.detail:
        line.append(f"  {outcome.detail}")
    if outcome.checkout is not None:
        line.append(f"  ({_pretty(outcome.checkout)})", style="dim")
    console.print(line)


def _updater(args: argparse.Namespace) -> Updater:
    return Updater(
        default_cache_dir(),
        commit=not getattr(args, "no_commit", False),
        verify=not getattr(args, "no_verify", False),
        report=_report,
    )


def _cmd_status(args: argparse.Namespace) -> int:
    targets = _load_targets(args)
    if args.fetch:
        for repo in sorted({t.repo for t in targets}):
            git.fetch(repo)
    statuses = compute_statuses(targets)
    if args.selectors:
        statuses = _select(statuses, args.selectors)
    if not statuses:
        console.print("No Copier-generated repos found under the configured roots.")
        return 0
    _print_statuses(statuses)
    _print_summary(statuses)
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    targets = _load_targets(args)
    statuses = compute_statuses(targets)
    selected = statuses if args.all else _select(statuses, args.selectors or ["."])
    selected = _whole_stacks(statuses, selected)
    updater = _updater(args)
    outcomes = []
    for repo_statuses_ in _by_repo(selected).values():
        outcomes.extend(updater.update_repo(repo_statuses_))
    if not outcomes:
        console.print(Text("Everything is up to date.", style="green"))
        return 0
    stopped = [
        o for o in outcomes if o.kind is not OutcomeKind.SKIPPED and not o.kind.ok
    ]
    if any(o.kind in {OutcomeKind.CONFLICTS, OutcomeKind.UNCOMMITTED} for o in stopped):
        console.print(Text("Next: `copierz resolve`", style="dim"))
    return 1 if stopped else 0


def _ask(prompt: str, choices: str, default: str) -> str:
    while True:
        try:
            answer = input(f"{prompt} ").strip().lower() or default
        except EOFError:
            return "q"
        if answer[:1] in choices:
            return answer[:1]


def _merge_step(updater: Updater, status: BranchStatus, checkout: Path) -> bool | None:
    git.mergetool(checkout)
    remaining = git.unmerged_files(checkout)
    if not remaining:
        return None
    console.print(f"Still unresolved: {', '.join(remaining)}")
    choice = _ask("[r]etry mergetool, [s]kip, [a]bort update, [q]uit?", "rsaq", "r")
    if choice == "r":
        return None
    if choice == "a":
        updater.abort(status)
    return choice != "q"


def _rebase_step(updater: Updater, status: BranchStatus, checkout: Path) -> bool | None:
    if git.rebase_continue(checkout):
        updater.release(status.target, checkout)
        _report(Outcome(status.target, OutcomeKind.REBASED, f"onto {status.parent}"))
        return True
    if git.is_rebasing(checkout) and git.unmerged_files(checkout):
        return None
    console.print(f"`git rebase --continue` failed in {_pretty(checkout)}")
    return True


def _commit_step(updater: Updater, status: BranchStatus, checkout: Path) -> bool | None:
    choice = _ask(
        "[c]ommit, [d]iff (difftool), [s]kip, [a]bort update, [q]uit? [c]",
        "cdsaq",
        "c",
    )
    if choice == "d":
        git.difftool_dir_diff(checkout)
        return None
    if choice == "c":
        _report(updater.finish(status, checkout))
    elif choice == "a":
        updater.abort(status)
    return choice != "q"


def _resolve_one(updater: Updater, status: BranchStatus) -> bool:
    """Walk one stopped update or rebase to a commit, interactively.

    Conflicts go to `git mergetool`, then a stopped rebase is continued, or
    an applied update is offered for review (difftool) and commit.

    Returns:
        False if the user asked to quit.
    """
    checkout = status.checkout
    if checkout is None:
        return True
    console.rule(Text(status.target.label, style="bold"))
    console.print(f"{status.detail} in {_pretty(checkout)}", style="dim")
    while True:
        if git.unmerged_files(checkout):
            step = _merge_step
        elif git.is_rebasing(checkout):
            step = _rebase_step
        else:
            step = _commit_step
        if (done := step(updater, status, checkout)) is not None:
            return done


def _cmd_resolve(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        msg = "resolve is interactive and needs a terminal"
        raise UsageError(msg)
    targets = _load_targets(args)
    statuses = compute_statuses(targets)
    if args.selectors:
        statuses = _select(statuses, args.selectors)
    pending = [s for s in statuses if s.kind in PENDING]
    if not pending:
        console.print("Nothing to resolve.")
        return 0
    updater = _updater(args)
    touched: set[Path] = set()
    for status in pending:
        touched.add(status.target.repo)
        if not _resolve_one(updater, status):
            break
    # Rebase whatever is stacked on the branches just committed.
    latest = LatestRefs()
    for repo in sorted(touched):
        repo_targets = [t for t in targets if t.repo == repo]
        refreshed = repo_statuses(refresh(repo_targets), latest)
        updater.update_repo(refreshed)
    return 0


def _cmd_abort(args: argparse.Namespace) -> int:
    targets = _load_targets(args)
    statuses = _select(compute_statuses(targets), args.selectors)
    pending = [s for s in statuses if s.kind in PENDING]
    if not pending:
        console.print("No stopped update or rebase among the selected branches.")
        return 0
    updater = _updater(args)
    for status in pending:
        checkout = status.checkout
        if checkout is None:
            continue
        discards_own_checkout = not updater.owns(checkout) and not git.is_rebasing(
            checkout
        )
        if discards_own_checkout and not args.yes:
            console.print(git.status_short(checkout), end="")
            if (
                _ask(f"Discard these changes in {_pretty(checkout)}? [y/N]", "yn", "n")
                != "y"
            ):
                continue
        updater.abort(status)
        console.print(f"{status.target.label}: aborted")
    return 0


def _version_key(ref: str) -> tuple[int, Version | str]:
    try:
        return (0, Version(ref))
    except InvalidVersion:
        return (1, ref)


def _cmd_log(args: argparse.Namespace) -> int:
    statuses = compute_statuses(_load_targets(args))
    by_template: dict[Path, list[BranchStatus]] = defaultdict(list)
    for status in statuses:
        if status.latest_ref is not None:
            by_template[status.target.template].append(status)
    for template, template_statuses in sorted(by_template.items()):
        latest = template_statuses[0].latest_ref
        in_use: dict[str, list[str]] = defaultdict(list)
        for status in template_statuses:
            in_use[status.current_ref or "?"].append(status.target.label)
        oldest = min(in_use, key=_version_key)
        console.rule(Text(f"{template.name}  {oldest} → {latest}", style="bold"))
        if oldest != latest:
            console.print(git.log_oneline(template, f"{oldest}..{latest}"), end="")
        for ref in sorted(in_use, key=_version_key):
            console.print(
                Text(f"{ref}: ", style="bold")
                + Text(", ".join(in_use[ref]), style="dim")
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copierz",
        description=(
            "Track and update the git repos (and every branch of them) "
            "generated from Copier templates."
        ),
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="path to the copierz config file"
    )
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        help="directory to scan instead of the configured roots (repeatable)",
    )
    parser.set_defaults(func=_cmd_status, selectors=[], fetch=False)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    selector_help = "REPO or REPO@BRANCH (REPO: directory name or path, `.` for cwd)"

    status = subparsers.add_parser(
        "status", help="show where each branch stands (default)"
    )
    status.add_argument("selectors", nargs="*", metavar="SELECTOR", help=selector_help)
    status.add_argument(
        "--fetch", action="store_true", help="git fetch every repo first"
    )
    status.set_defaults(func=_cmd_status)

    update = subparsers.add_parser(
        "update",
        help="update branches to their template's latest version",
        description=(
            "Run `copier update` on each stack's bottom branch and rebase the "
            "branches stacked on it, committing clean results. Selecting any "
            "branch of a stack updates the whole stack. Branches that aren't "
            "checked out are updated in temporary worktrees."
        ),
    )
    update.add_argument(
        "selectors",
        nargs="*",
        metavar="SELECTOR",
        help=f"{selector_help}; default: `.`",
    )
    update.add_argument("--all", action="store_true", help="update every repo")
    update.add_argument(
        "--no-commit", action="store_true", help="leave applied updates uncommitted"
    )
    update.add_argument(
        "--no-verify", action="store_true", help="skip pre-commit hooks when committing"
    )
    update.set_defaults(func=_cmd_update)

    resolve = subparsers.add_parser(
        "resolve",
        help="walk through stopped updates/rebases: mergetool, review, commit",
    )
    resolve.add_argument("selectors", nargs="*", metavar="SELECTOR", help=selector_help)
    resolve.add_argument(
        "--no-verify", action="store_true", help="skip pre-commit hooks when committing"
    )
    resolve.set_defaults(func=_cmd_resolve)

    abort = subparsers.add_parser("abort", help="throw away a stopped update or rebase")
    abort.add_argument("selectors", nargs="+", metavar="SELECTOR", help=selector_help)
    abort.add_argument(
        "--yes", action="store_true", help="don't ask before discarding changes"
    )
    abort.set_defaults(func=_cmd_abort)

    log = subparsers.add_parser(
        "log", help="show template changes since the oldest version in use"
    )
    log.set_defaults(func=_cmd_log)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        code = args.func(args)
    except (UsageError, ConfigError) as exc:
        console.print(Text(f"copierz: {exc}", style="red"))
        code = 2
    raise SystemExit(code)
