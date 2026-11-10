DOIT_CONFIG = {
    "backend": "json",
    "default_tasks": ["check"],
    "dep_file": ".doit.json",
    "verbosity": 2,
}


def task_check():
    return {
        "actions": [
            "uv run ruff check src/copierz tests",
            "uv run ruff format --check src/copierz tests",
            "uv run pyrefly check src/copierz tests",
        ],
    }


def task_test():
    return {
        "actions": [
            "uv run pytest",
        ],
    }


def task_publish():
    """Build and upload a release to PyPI, with the token of creds.env.

    creds.env (gitignored) holds one line: export UV_PUBLISH_TOKEN=pypi-...
    """
    return {
        "actions": [
            "test -f creds.env || { echo 'creds.env is missing: write"
            " export UV_PUBLISH_TOKEN=pypi-... in it (see README.md)' >&2; exit 1; }",
            "rm -rf dist && uv build",
            ". ./creds.env && uv publish",
        ],
        "verbosity": 2,
    }


def task_install_hooks():
    import subprocess

    def unset_hooks_path():
        # Projects generated before the switch to prek pointed git at .githooks,
        # and prek refuses to install while core.hooksPath is set.
        subprocess.run(["git", "config", "--unset-all", "core.hooksPath"], check=False)

    return {
        "actions": [unset_hooks_path, "uv run prek install"],
    }
