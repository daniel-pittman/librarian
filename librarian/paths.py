"""Resolution of the librarian data home and its per-resource paths.

The tool keeps all of its state inside a single *data home* directory:

    activities.yaml      — the entry database
    files.yaml           — the artifact-file inventory
    schema.yaml          — the active schema (optional; schema-less if absent)
    changes.log          — the append-only change ledger
    artifacts/           — registered artifact files

The default data home is an XDG-style config directory:

    $XDG_CONFIG_HOME/librarian/      (when XDG_CONFIG_HOME is set)
    ~/.config/librarian/             (otherwise)

Every individual location is overridable by an environment variable so that
tests can sandbox the tool and power users can split state across volumes:

    LIBRARIAN_HOME        — the whole data home directory
    LIBRARIAN_YAML_PATH   — activities.yaml
    LIBRARIAN_FILES_PATH  — files.yaml
    LIBRARIAN_LEDGER_PATH — the change ledger
    LIBRARIAN_SCHEMA_PATH — schema.yaml
    LIBRARIAN_ROOT        — the root that inventory file paths resolve against
    LIBRARIAN_MEMORY_DIR  — optional MCP memory-resource directory

The per-resource variables win over LIBRARIAN_HOME, which in turn wins over
the XDG default. Nothing in this module touches the filesystem — it only
computes paths — except :func:`ensure_data_home`, which is called explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _xdg_config_home() -> Path:
    """Return the XDG config base directory.

    Honours ``XDG_CONFIG_HOME`` when it is set to a non-empty value, otherwise
    falls back to the spec-mandated ``~/.config``.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / ".config"


def default_data_home() -> Path:
    """Return the default librarian data home (``<xdg-config>/librarian``)."""
    return _xdg_config_home() / "librarian"


@dataclass(frozen=True)
class LibrarianPaths:
    """A fully-resolved set of librarian filesystem locations.

    Instances are immutable snapshots — build one with :func:`resolve_paths`
    at the start of a command and pass it down. Resolving once (rather than
    re-reading the environment per access) keeps a single command's view of
    the world internally consistent.
    """

    home: Path
    activities: Path
    files: Path
    ledger: Path
    schema: Path
    root: Path
    artifacts: Path
    memory_dir: Path | None

    def ensure_home(self) -> None:
        """Create the data home and artifacts directory if they are absent.

        The ledger's parent is also created so the first write never fails.
        Existing directories are left untouched (``exist_ok=True``).
        """
        self.home.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)


def resolve_paths(env: dict[str, str] | None = None) -> LibrarianPaths:
    """Compute every librarian path from the environment.

    Args:
        env: Environment mapping to read from. Defaults to ``os.environ``.
            Passing an explicit dict makes the resolution deterministic and
            trivially unit-testable.

    Returns:
        A :class:`LibrarianPaths` snapshot. No filesystem access is performed.
    """
    env = os.environ if env is None else env

    # 1. The data home: explicit override, else the XDG default.
    home_override = env.get("LIBRARIAN_HOME", "").strip()
    home = Path(home_override).expanduser() if home_override else default_data_home()

    def _resolve(var: str, default: Path) -> Path:
        """Per-resource override helper: env var wins, else `default`."""
        value = env.get(var, "").strip()
        return Path(value).expanduser() if value else default

    activities = _resolve("LIBRARIAN_YAML_PATH", home / "activities.yaml")
    files = _resolve("LIBRARIAN_FILES_PATH", home / "files.yaml")
    ledger = _resolve("LIBRARIAN_LEDGER_PATH", home / "changes.log")
    schema = _resolve("LIBRARIAN_SCHEMA_PATH", home / "schema.yaml")

    # Inventory paths are stored relative to this root. By default the root is
    # the data home itself, so the bundled `artifacts/` folder is the natural
    # place to keep registered files.
    root = _resolve("LIBRARIAN_ROOT", home)
    artifacts = root / "artifacts"

    # The MCP memory directory has NO personal default — it is opt-in only.
    memory_override = env.get("LIBRARIAN_MEMORY_DIR", "").strip()
    memory_dir = Path(memory_override).expanduser() if memory_override else None

    return LibrarianPaths(
        home=home,
        activities=activities,
        files=files,
        ledger=ledger,
        schema=schema,
        root=root,
        artifacts=artifacts,
        memory_dir=memory_dir,
    )
