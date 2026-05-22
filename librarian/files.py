"""The normalised artifact-file inventory.

Activity entries reference supporting documents (PDFs, posters, slide decks,
certificates) by putting a ``file:<id>`` token in their ``docs`` list rather
than a raw path. The path itself is stored once, here, in ``files.yaml``. That
indirection means moving or renaming a file is a single inventory edit — no
entry reference ever has to change.

``files.yaml`` holds only simple scalars (no multi-paragraph prose), so unlike
``activities.yaml`` it is safe to load -> mutate -> dump in full.

Each record carries: ``id``, ``path`` (relative to the inventory root),
``category``, ``title``, an optional ``description``, a ``sha256`` content
digest, and an ``added`` date.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .storage import write_lock

# Canonical field order for serialised records — keeps files.yaml diffs stable.
_FILE_KEY_ORDER = ("id", "path", "category", "title", "description", "sha256", "added")


def load_files(files_path: Path) -> list[dict]:
    """Load the file inventory. Returns a list of records (possibly empty)."""
    if not files_path.exists():
        return []
    with open(files_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("files", []) or []


def save_files(files_path: Path, records: list[dict]) -> None:
    """Atomically write the inventory, sorted by id with canonical field order."""

    def _ordered(record: dict) -> dict:
        # Emit known keys in canonical order, then preserve any extras so an
        # unexpected key is never silently dropped.
        out = {k: record[k] for k in _FILE_KEY_ORDER if k in record}
        for k in record:
            if k not in out:
                out[k] = record[k]
        return out

    ordered = [_ordered(r) for r in sorted(records, key=lambda r: r.get("id", ""))]
    with write_lock(files_path):
        files_path.parent.mkdir(parents=True, exist_ok=True)
        with open(files_path, "w", encoding="utf-8") as fh:
            yaml.dump(
                {"files": ordered},
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )


def slugify_filename(path: str) -> str:
    """Derive a lowercase slug id from a file path's stem."""
    slug = re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-")
    return slug or "file"


def unique_file_id(base: str, existing_ids: set[str]) -> str:
    """Return `base`, or `base-2`, `base-3`, ... if it collides with an id."""
    if base not in existing_ids:
        return base
    n = 2
    while f"{base}-{n}" in existing_ids:
        n += 1
    return f"{base}-{n}"


def rel_to_root(path: str, root: Path) -> str:
    """Normalise `path` to be relative to the inventory `root`.

    Absolute paths under the root become root-relative; paths already relative
    are returned unchanged; absolute paths outside the root are kept absolute.
    """
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)
    return str(p)


def sha256_of(abs_path: Path) -> str:
    """Return the hex sha256 digest of a file, streamed in 1 MiB chunks."""
    digest = hashlib.sha256()
    with open(abs_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def today_iso() -> str:
    """Return today's date (UTC) as a ``YYYY-MM-DD`` string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def search_files(records: list[dict], query: str) -> list[dict]:
    """Return inventory records whose text fields contain `query`.

    Case-insensitive; searches id, title, description, category and path.
    """
    q = query.lower()
    out = []
    for r in records:
        haystack = " ".join(
            str(r.get(k, "")) for k in ("id", "title", "description", "category", "path")
        ).lower()
        if q in haystack:
            out.append(r)
    return out
