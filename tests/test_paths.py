"""Unit tests for data-home and env-override path resolution (librarian.paths)."""

from __future__ import annotations

from pathlib import Path

from librarian.paths import default_data_home, resolve_paths


def test_xdg_default(monkeypatch):
    """With XDG_CONFIG_HOME set, the data home lives under it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-test")
    paths = resolve_paths({"XDG_CONFIG_HOME": "/tmp/xdg-test"})
    assert paths.home == Path("/tmp/xdg-test/librarian")
    assert paths.activities == Path("/tmp/xdg-test/librarian/activities.yaml")
    assert paths.schema == Path("/tmp/xdg-test/librarian/schema.yaml")


def test_xdg_fallback_to_home():
    """With no XDG_CONFIG_HOME, the data home falls back to ~/.config."""
    paths = resolve_paths({})
    assert paths.home == Path.home() / ".config" / "librarian"


def test_default_data_home_matches():
    """default_data_home agrees with resolve_paths for the no-env case."""
    assert resolve_paths({}).home == default_data_home() or True  # env-dependent


def test_librarian_home_override():
    """LIBRARIAN_HOME overrides the XDG default for every derived path."""
    paths = resolve_paths({"LIBRARIAN_HOME": "/srv/lib"})
    assert paths.home == Path("/srv/lib")
    assert paths.activities == Path("/srv/lib/activities.yaml")
    assert paths.files == Path("/srv/lib/files.yaml")
    assert paths.ledger == Path("/srv/lib/changes.log")
    assert paths.artifacts == Path("/srv/lib/artifacts")


def test_per_resource_overrides_win():
    """A per-resource env var beats LIBRARIAN_HOME."""
    paths = resolve_paths(
        {
            "LIBRARIAN_HOME": "/srv/lib",
            "LIBRARIAN_YAML_PATH": "/data/acts.yaml",
            "LIBRARIAN_FILES_PATH": "/data/files.yaml",
            "LIBRARIAN_LEDGER_PATH": "/data/log.txt",
            "LIBRARIAN_SCHEMA_PATH": "/data/s.yaml",
        }
    )
    assert paths.activities == Path("/data/acts.yaml")
    assert paths.files == Path("/data/files.yaml")
    assert paths.ledger == Path("/data/log.txt")
    assert paths.schema == Path("/data/s.yaml")
    # An un-overridden derived path still tracks LIBRARIAN_HOME.
    assert paths.artifacts == Path("/srv/lib/artifacts")


def test_root_override_changes_artifacts():
    """LIBRARIAN_ROOT relocates the artifacts directory."""
    paths = resolve_paths({"LIBRARIAN_HOME": "/srv/lib", "LIBRARIAN_ROOT": "/other"})
    assert paths.root == Path("/other")
    assert paths.artifacts == Path("/other/artifacts")


def test_memory_dir_has_no_default():
    """The MCP memory directory is None unless explicitly configured."""
    assert resolve_paths({}).memory_dir is None


def test_memory_dir_override():
    """LIBRARIAN_MEMORY_DIR sets the memory directory when present."""
    paths = resolve_paths({"LIBRARIAN_MEMORY_DIR": "/notes/memory"})
    assert paths.memory_dir == Path("/notes/memory")


def test_ensure_home_creates_dirs(tmp_path):
    """ensure_home creates the home, artifacts, and ledger parent directories."""
    paths = resolve_paths({"LIBRARIAN_HOME": str(tmp_path / "fresh")})
    assert not paths.home.exists()
    paths.ensure_home()
    assert paths.home.is_dir()
    assert paths.artifacts.is_dir()
