"""librarian — a local-first, plain-text activity tracker.

The package is organised into focused modules:

* :mod:`librarian.paths` — resolves the XDG-style data home and the
  per-resource env-var overrides.
* :mod:`librarian.schema` — the pluggable schema engine: loads ``schema.yaml``
  and validates structured "blocks" on each entry.
* :mod:`librarian.storage` — low-level YAML I/O: line-level editing that
  preserves hand-formatting, fcntl advisory locking, and the change ledger.
* :mod:`librarian.files` — the normalised artifact-file inventory.
* :mod:`librarian.cli` — the argparse command dispatch.

The console entry point is :func:`librarian.cli.main`.
"""

from pathlib import Path

# The package version is the single source of truth in the VERSION file at the
# repository root, so the CLI, packaging metadata, and CI all agree.
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
try:
    __version__ = _VERSION_FILE.read_text().strip()
except OSError:  # pragma: no cover - VERSION file should always be present
    __version__ = "0.0.0"

__all__ = ["__version__"]
