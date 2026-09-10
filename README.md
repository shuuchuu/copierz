# `copierz`

A TUI to track a fleet of git repos generated from a
[Copier](https://copier.readthedocs.io/) template, see which repos/branches
are behind the template, and drive `copier update` and conflict resolution
from one place.

## Configuring

copierz reads `~/.config/copierz/config.toml` (or `$XDG_CONFIG_HOME`, or
`--config PATH`). You can create it by hand, or press `a` in the TUI:

```toml
[[templates]]
name = "shuuchuu"
path = "/home/mog/repos/shuuchuu/template"
# workspace_root defaults to path's parent directory. Set it explicitly if
# the template's `_src_path` in tracked repos is relative to something else.

[[repos]]
path = "/home/mog/repos/shuuchuu/caesar-cipher"
template = "shuuchuu"
branches = ["main", "solution"]
```

Only branches listed under `branches` are tracked; add/remove them from the
TUI (`a` / `x`) or by editing the file.

## Running

```console
$ copierz            # launch the TUI
$ copierz status      # print statuses and exit (for scripts/cron)
$ copierz status --fetch
```

## TUI keybindings

| Key     | Action                                           |
| ------- | ------------------------------------------------ |
| `r`     | Refresh statuses                                 |
| `f`     | `git fetch` every tracked repo, then refresh      |
| `u`     | Run `copier update` on the selected branch        |
| `enter` | Open the review/resolve screen for the selection |
| `a`     | Track a new repo/branch                          |
| `x`     | Stop tracking the selected repo/branch            |
| `q`     | Quit                                              |

In the review screen (opened automatically after an update, or via `enter`):

| Key      | Action                                             |
| -------- | --------------------------------------------------- |
| `e`      | Open the selected file in `$EDITOR`                  |
| `s`      | Stage the selected file (`git add`, i.e. mark resolved) |
| `d`      | View the file's diff in `$PAGER`                     |
| `c`      | Stage everything and commit                          |
| `escape` | Back to the repo list                                |

## How statuses are computed

For each tracked branch, copierz reads `.copier-answers.yml` as committed on
that branch (`git show branch:.copier-answers.yml`, no checkout needed) and
compares its `_commit` against the template's latest git tag (or `HEAD` if it
has none) — the same resolution Copier itself uses. If that branch happens to
be the one currently checked out, its working tree is also inspected for
uncommitted changes or unresolved conflicts from a prior update.

Statuses: `up to date`, `update available`, `dirty` (working tree has
uncommitted changes), `conflicts` (an update left unmerged files), `not
tracked` (no answers file on that branch), `error`.

## Updating

`copierz` never stashes, discards, or force-switches branches on your
behalf — it requires a clean working tree, checks out the target branch if
needed, and then runs `copier update` in place. Conflicts come out exactly as
a `git merge` conflict would (Copier runs a real 3-way `git merge-file`
under the hood): conflict markers in the file, and the file listed as
unmerged by `git status`. Resolve them with your usual git workflow, or via
the review screen's `$EDITOR`/stage/commit actions.
