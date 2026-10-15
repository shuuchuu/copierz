#!/bin/sh
# Runs after bumpver has rewritten the version files but before it commits,
# so uv.lock (which pins the project's own version) ends up in the same commit.
set -e
uv lock
git add uv.lock
