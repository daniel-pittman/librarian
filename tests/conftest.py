"""Shared pytest fixtures for the librarian test suite.

Every test runs against a *copy* of the synthetic fixture corpus in an isolated
temporary directory, so tests never touch each other's state and the committed
fixtures are never mutated.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# The fixtures directory holding the committed synthetic corpus.
FIXTURES = Path(__file__).parent / "fixtures"
# The repository root — used to invoke the CLI as a module.
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def sandbox(tmp_path: Path):
    """Build an isolated librarian data home from the synthetic fixtures.

    Copies the sample activities, files inventory, schema and artifact files
    into ``tmp_path`` and returns a small namespace describing the sandbox.
    The returned object also carries a :meth:`run` helper that invokes the CLI
    with the sandbox's environment already wired up.
    """
    home = tmp_path / "home"
    home.mkdir()
    artifacts = home / "artifacts"
    shutil.copytree(FIXTURES / "artifacts", artifacts)
    shutil.copy2(FIXTURES / "sample_activities.yaml", home / "activities.yaml")
    shutil.copy2(FIXTURES / "sample_files.yaml", home / "files.yaml")
    shutil.copy2(FIXTURES / "sample_schema.yaml", home / "schema.yaml")
    ledger = home / "changes.log"

    env = {
        "LIBRARIAN_HOME": str(home),
        "LIBRARIAN_ROOT": str(home),
        "LIBRARIAN_SESSION_LABEL": "test:suite",
        # Keep XDG out of the picture so the default never leaks in.
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }

    class Sandbox:
        """A configured librarian sandbox for one test."""

        def __init__(self):
            self.home = home
            self.activities = home / "activities.yaml"
            self.files = home / "files.yaml"
            self.schema = home / "schema.yaml"
            self.ledger = ledger
            self.artifacts = artifacts
            self.env = env

        def run(self, *args: str, stdin: str | None = None, extra_env: dict | None = None):
            """Invoke the librarian CLI inside the sandbox.

            Returns a ``(stdout, stderr, returncode)`` tuple.
            """
            full_env = {**_base_env(), **self.env}
            if extra_env:
                full_env.update(extra_env)
            result = subprocess.run(
                [sys.executable, "-m", "librarian.cli", *args],
                capture_output=True,
                text=True,
                input=stdin,
                cwd=str(REPO_ROOT),
                env=full_env,
            )
            return result.stdout, result.stderr, result.returncode

        def load_activities(self) -> list[dict]:
            """Parse and return the current activities list."""
            data = yaml.safe_load(self.activities.read_text()) or {}
            return data.get("activities", []) or []

        def entry(self, entry_id: str) -> dict:
            """Return the entry with the given id (raises if absent)."""
            for e in self.load_activities():
                if e.get("id") == entry_id:
                    return e
            raise KeyError(entry_id)

        def load_files(self) -> list[dict]:
            """Parse and return the current file-inventory records."""
            data = yaml.safe_load(self.files.read_text()) or {}
            return data.get("files", []) or []

    return Sandbox()


def _base_env() -> dict:
    """A minimal base environment for subprocess calls (PATH + Python config)."""
    import os

    keep = ("PATH", "HOME", "PYTHONPATH", "VIRTUAL_ENV", "LANG", "LC_ALL")
    return {k: v for k, v in os.environ.items() if k in keep}
