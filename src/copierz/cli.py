"""Entry point: launch the TUI, or print statuses for scripting."""

from __future__ import annotations

import argparse
from pathlib import Path

from copierz.config import load_config
from copierz.status import compute_all_statuses


def _cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    statuses = compute_all_statuses(config, fetch=args.fetch)
    if not statuses:
        print("No repos tracked. Run `copierz` and press 'a' to add one.")
        return 0
    repo_width = max(len(s.repo_path.name) for s in statuses)
    branch_width = max(len(s.branch) for s in statuses)
    status_width = max(len(s.status.value) for s in statuses)
    for status in statuses:
        print(
            f"{status.repo_path.name:<{repo_width}}  "
            f"{status.branch:<{branch_width}}  "
            f"{status.status.value:<{status_width}}  "
            f"{status.current_ref or '-':<16}  "
            f"{status.latest_ref or '-':<16}  "
            f"{status.detail}"
        )
    return 0


def _cmd_tui(args: argparse.Namespace) -> int:
    from copierz.tui.app import CopierzApp

    CopierzApp(config_path=args.config).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copierz",
        description="Track and update git repos generated from a copier template.",
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="path to the copierz config file"
    )
    parser.set_defaults(func=_cmd_tui)

    subparsers = parser.add_subparsers(dest="command")
    status_parser = subparsers.add_parser(
        "status", help="print the status of tracked repos and exit"
    )
    status_parser.add_argument(
        "--fetch", action="store_true", help="fetch remotes before computing status"
    )
    status_parser.set_defaults(func=_cmd_status)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))
