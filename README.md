# `copierz`

A CLI to track the git repos — and every branch of them — generated from
[Copier](https://copier.readthedocs.io/) templates, see which are behind
their template, and bring them up to date in one go.

## Configuring

copierz reads `~/.config/copierz/config.toml` (or `$XDG_CONFIG_HOME`, or
`--config PATH`):

```toml
# Scanned recursively on every run.
roots = ["~/repos"]

# Repos (`path`) or single branches (`path@branch`) to leave alone.
exclude = ["~/repos/shuuchuu/old-thing", "*@backup/*"]
```

Exclude entries take shell wildcards, `*` matching `/` too: `*@backup/*`
leaves out every `backup/` branch of every repo, which `update` would
otherwise bring up to date like any other.

There's no list of repos to maintain: on every run copierz scans the roots
for git repos, and every local branch of each (via `git show
branch:.copier-answers.yml`, no checkout needed) for a Copier answers file.
It stops descending at a repo, and skips hidden directories and linked
worktrees.

A branch can also carry Copier projects nested in subdirectories, each with
its own answers file (say a vendored `labs/`): each is tracked on its own,
shown as `branch:labs`, and gets an update commit of its own (`Update
template to 0.1.8 in labs`). Stacks are still made of branches: a stacked
branch is rebased once, whatever projects it carries. Exclude a nested
project with its path, `~/repos/slides/labs`. The template comes from each answers file's `_src_path`; a
relative one is resolved against the repo's own parent directory, which is
where `copier copy` is virtually always run from.

`--root PATH` (repeatable) scans other directories instead of the
configured roots.

## Usage

```console
$ copierz                    # same as `copierz status`
$ copierz status --fetch     # git fetch every repo first
$ copierz update --all       # update everything that's behind
$ copierz update ghcli       # one repo (or REPO@BRANCH, a path, `.` for cwd)
$ copierz resolve            # finish whatever stopped on conflicts
$ copierz continue           # same, non-interactively, once fixed by hand
$ copierz abort ghcli@main   # throw away a stopped update or rebase
$ copierz push --all --force # push what changed, rebased branches included
$ copierz log                # template changes since the oldest version in use
```

`copierz update` commits every update that applies cleanly (`--no-commit`
to review them first, `--no-verify` to skip pre-commit hooks). A tracked
`uv.lock` is relocked into the update commit, since the template may have
changed `pyproject.toml`; if `uv lock` fails, the update is left
uncommitted, with uv's error, for you to fix and `copierz continue`. Anything
that stops on conflicts is left for `copierz resolve`, which walks each one
through your `git mergetool`, then offers to review the result in your
`git difftool` and commit it.

## Pushing

`copierz push` fetches, then pushes every selected branch that is ahead of
the remote branch it tracks. Branches rebased by `update` need `--force`;
they're then pushed with `--force-with-lease` on the fetched remote tip, and
only if that tip is in the branch's reflog — i.e. the remote holds nothing
the branch didn't have, so the force can't drop anyone else's work. Branches
behind or diverged from their remote are never pushed, and `--dry-run` shows
what would happen.

## Scripting and agents

`status`, `update`, `continue`, `abort`, `push` and `log` take `--json` to print a
single JSON document on stdout (errors go to stderr, exit code 2):

- `status --json`: `{"branches": [{repo, branch, subproject, template,
  status, current, latest, parent, checkout, unmerged, detail}]}`, with
  `subproject` `null` for the repo's root project, and `status`
  one of `up_to_date`, `behind`, `blocked`, `in_progress`, `conflicts`,
  `error`.
- `update --json` / `continue --json`: `{"outcomes": [{repo, branch,
  subproject, outcome, checkout, detail}]}`, with `outcome` one of `updated`,
  `rebased`, `uncommitted`, `conflicts`, `skipped`, `failed`. The exit code
  is 1 if anything stopped (conflicts, uncommitted, failed).
- `abort --json --yes`: `{"aborted": [...]}`, same fields as `status`.
- `push --json`: `{"branches": [{repo, branch, state, remote, commits,
  detail}]}`, with `state` one of `up_to_date`, `ahead`, `rewritten`,
  `behind`, `diverged`, `no_upstream`. The exit code is 1 if a branch
  couldn't be pushed.
- `log --json`: `{"templates": [{template, oldest, latest, commits: [{sha,
  subject, refs}], in_use: {version: ["repo@branch", ...]}}]}`.

`resolve` is interactive; the non-interactive path is to edit the files
listed under `unmerged` in the `checkout` directory, `git add` them, and run
`copierz continue`.

## Stacked branches

Branches are often stacked: `solution` on `main`, `main-en` as a
translation commit on top of `main`, and so on, kept up to date by
rebasing. copierz detects this from the history: a branch is stacked on
another if it has commits of its own past their merge base, while the other
has none except template updates (commits touching the answers file). The
closest such branch is the parent; `copierz status` shows the stacks as a
tree.

A copy of a branch's own past tip (a backup, or a branch made from it with
no commits yet) looks just like a parent in the history, so copierz asks the
reflogs: if the copy was only created, reset or rebased onto a commit the
branch got to on its own, the branch isn't stacked on it. Reflogs expire
(after 90 days by default), so `exclude` backups you keep longer.

`copierz update` then only runs `copier update` on the bottom branch of each
stack, and rebases the branches above onto their parent once it's done.
Selecting any branch of a stack updates the whole stack. A branch that
stops on conflicts holds back the branches stacked on it, until `copierz
resolve` (or `continue`) finishes it and rebases them. During these rebases
the answers file follows the parent: a conflict there only comes from a
stale template update made on the branch itself, so it's resolved with the
parent's version, and a commit left empty by that is dropped. A `uv.lock`
conflict is resolved by relocking from the parent's lock, and a branch whose
lock is still stale once rebased (its own commits changed `pyproject.toml`)
gets a `Relock uv.lock` commit on top. Rebasing
rewrites those branches, so pushing them afterwards needs a force: see
`copierz push --force` below.

## Worktrees

copierz never switches your checkout to another branch, stashes, or
discards your changes. A branch checked out somewhere (your main checkout or
any worktree) is updated there, and only if it's clean. Any other branch is
updated in a temporary worktree under `~/.cache/copierz/worktrees/`, so a
dirty `main` doesn't hold back its siblings. That worktree is removed once
its branch is committed, and stays (its path is shown) while it has
conflicts to resolve.

## Statuses

- `up to date`: the branch's answers file records the template's latest
  version (highest PEP 440 git tag, else `HEAD`, as Copier itself does).
- `behind`: an update is available; for a stacked branch, it'll come from
  rebasing onto its parent.
- `behind, dirty`: the branch is checked out with uncommitted changes, so
  it can't be updated until you commit or stash them.
- `conflicts`: an update or rebase stopped on conflicts; run `copierz
  resolve`.
- `to commit`: an update was applied (or its conflicts resolved) but not
  committed yet; run `copierz resolve`.
- `error`: the template can't be found or read.

Conflicts come out exactly as a `git merge` conflict would (Copier runs a
real 3-way `git merge-file` under the hood): conflict markers in the file,
and the file listed as unmerged by `git status`.
