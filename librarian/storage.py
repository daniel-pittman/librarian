"""Low-level storage for the activity database.

This module owns three concerns:

1. **Line-level YAML editing.** ``activities.yaml`` is a hand-formatted file
   that humans read and edit directly. A round-trip through ``yaml.dump`` would
   reflow it, collapse literal-block scalars and reorder keys — destroying that
   formatting. So every write here is a *surgical* line-level edit: locate an
   entry's line range, splice in the changed lines, write the whole file back
   verbatim. Only the touched lines change.

2. **Concurrency safety.** Multiple librarian processes may run at once
   (several Claude Code sessions, a CLI invocation, the MCP server). Each write
   takes an exclusive ``fcntl`` advisory lock on a sidecar ``.lock`` file.

3. **The change ledger.** Every write appends one line to an append-only
   ledger so other sessions can poll "what changed since I last looked?".
"""

from __future__ import annotations

import fcntl
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Per-thread set of lock-file paths currently held. Lets :class:`write_lock`
# be reentrant within a single thread: a command can take the lock around its
# read-plan-write transaction and still call inner helpers like
# :func:`write_lines` that also use ``write_lock``, without deadlocking on a
# second ``fcntl.flock(LOCK_EX)`` against a fresh file descriptor to the same
# lock file. Different threads / processes still contend exclusively via the
# OS-level flock.
#
# Caveats: this in-process bookkeeping does NOT model fork() or asyncio. After
# ``os.fork()`` the child inherits the parent's threading.local view; the
# ``after_in_child`` hook below clears it so the child re-acquires its own
# flock instead of skipping. Multiple coroutines on the same thread are not
# supported — the second's __enter__ would no-op while the first is in its
# critical section. No librarian writer currently uses asyncio.
_held_locks = threading.local()


def _currently_held_locks() -> set[str]:
    """Return (creating on demand) the per-thread set of held lock-file paths."""
    held = getattr(_held_locks, "set", None)
    if held is None:
        held = set()
        _held_locks.set = held
    return held


def _reset_held_after_fork() -> None:
    """Clear the held-set in a forked child so it re-acquires its own flock."""
    held = getattr(_held_locks, "set", None)
    if held is not None:
        held.clear()


# Register the after-fork hook only where the platform supports it. Python
# 3.7+ provides this on POSIX; Windows has no fork() and skips it.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_held_after_fork)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_activities(yaml_path: Path) -> tuple[dict, list[dict]]:
    """Load and parse the activities file.

    Returns a ``(meta, activities)`` tuple. A missing file yields empty
    structures so first-run commands degrade gracefully rather than crashing.
    """
    if not yaml_path.exists():
        return {}, []
    with open(yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("meta", {}) or {}, data.get("activities", []) or []


def read_lines(yaml_path: Path) -> list[str]:
    """Return the raw lines of the activities file (empty list if absent)."""
    if not yaml_path.exists():
        return []
    with open(yaml_path, encoding="utf-8") as fh:
        return fh.readlines()


# ---------------------------------------------------------------------------
# Locking + atomic writes
# ---------------------------------------------------------------------------


class write_lock:
    """Exclusive advisory file lock for a YAML resource.

    Uses :func:`fcntl.flock` on a sidecar ``<resource>.lock`` file, which works
    across processes on the same machine. Use as a context manager::

        with write_lock(paths.activities):
            ...  # critical section

    Reentrant within a single thread: a writer command can wrap its whole
    read-plan-write transaction in this context and still call inner helpers
    like :func:`write_lines` (which also use ``write_lock``). The first
    ``__enter__`` acquires the OS-level lock; nested ``__enter__`` calls are
    bookkeeping-only and release nothing on ``__exit__`` until the
    outermost frame exits. Across threads or processes, exclusivity holds.
    """

    def __init__(self, resource_path: Path):
        self._lock_path = resource_path.with_suffix(resource_path.suffix + ".lock")
        self._lock_key = str(self._lock_path)
        self._fh = None
        self._was_held = False

    def __enter__(self) -> write_lock:
        # Always reset per-instance bookkeeping at __enter__ so a reused
        # instance can't carry a stale `_was_held=True` from a prior context
        # and leak the next acquire (silently skipping flock).
        self._was_held = False
        self._fh = None
        held = _currently_held_locks()
        if self._lock_key in held:
            # Already held by an outer frame on this thread; treat as a no-op
            # acquire/release pair.
            self._was_held = True
            return self
        # Ensure the parent directory exists so the lock file can be created.
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._lock_path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
        except OSError:
            fh.close()
            raise
        self._fh = fh
        held.add(self._lock_key)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._was_held:
            return False
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            _currently_held_locks().discard(self._lock_key)
        return False  # never suppress exceptions


def atomic_replace(yaml_path: Path, new_content: str) -> None:
    """Atomically replace ``yaml_path`` with ``new_content``.

    Writes a temp sidecar then ``os.replace`` over the target. Preserves
    the target's existing mode bits when present — ``os.replace`` would
    otherwise adopt the tmp file's umask-default mode and silently revert
    a ``chmod 600`` the user (or downstream packaging) applied for
    access restriction. On failure (write error, copymode error) the
    temp sidecar is removed so the user doesn't accumulate orphan
    ``.tmp`` files after an interrupted write.

    Caveats (acknowledged for the multi-user data home case): ``os.replace``
    swaps the inode, so the file's uid/gid after the write reflect the
    running process's uid/gid, not the target's pre-write owner. In a
    shared-data-home deployment (mode 660 service-user file), the first
    write by a different uid silently rechowns the file. ``shutil.copystat``
    + ``os.chown`` would close this, but ``chown`` to an arbitrary uid/gid
    needs ``CAP_CHOWN`` — not assumable. Single-user installations are
    unaffected.

    Caller must hold the resource's :class:`write_lock`.
    """
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    # Resolve symlinks before writing so the atomic-replace operates on the
    # link's TARGET, not the link itself. Without this, ``os.replace`` would
    # silently destroy the symlink — a user who aliased their data home into
    # a synced folder (Syncthing, Dropbox, NFS mount) would see the link
    # replaced by a local file on the first write, and the synced
    # destination would silently stop receiving updates.
    if yaml_path.is_symlink():
        target_path = yaml_path.resolve()
        # The target's parent may differ from the symlink's parent (the
        # user pointed the link at a path that doesn't exist yet). Without
        # this mkdir the temp open() fails with a raw FileNotFoundError
        # (round-5 #4).
        target_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        target_path = yaml_path
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        if target_path.exists():
            import shutil

            shutil.copymode(target_path, tmp_path)
        os.replace(tmp_path, target_path)
    except BaseException:
        # Clean up the orphan sidecar on any failure (write error,
        # copymode, replace, KeyboardInterrupt) so an interrupted write
        # doesn't leave the user with a stray ``.tmp`` file and (on first
        # run) an empty target.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def write_lines(yaml_path: Path, lines: list[str]) -> None:
    """Write `lines` to the activities file under an exclusive lock.

    Atomic from a concurrent reader's POV via temp+``os.replace``.
    """
    with write_lock(yaml_path):
        atomic_replace(yaml_path, "".join(lines))


# ``append_text`` was removed in v1.7.1. ``cmd_create`` now inlines the
# read-existing + concat + atomic_replace dance directly inside its lock-
# protected write phase, and no other caller used it. The atomic-rewrite
# trade (full O(N) rewrite per create) is documented at the create call
# site rather than buried in a one-line helper.


# ---------------------------------------------------------------------------
# YAML value quoting
# ---------------------------------------------------------------------------


def yaml_quote(value: str) -> str:
    """Quote a string for safe single-line YAML output.

    Picks the quote style that avoids escaping where possible:

    * contains a single quote only  -> double-quote it
    * contains a double quote only  -> single-quote it
    * contains neither              -> single-quote it (the default)
    * contains both                 -> double-quote and backslash-escape
    """
    has_single = "'" in value
    has_double = '"' in value
    if has_single and has_double:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if has_single:
        return f'"{value}"'
    return f"'{value}'"


# ---------------------------------------------------------------------------
# Entry line-range location
# ---------------------------------------------------------------------------


def find_entry_line_range(lines: list[str], entry_id: str) -> tuple[int | None, int | None]:
    """Find the ``[start, end)`` line range of an entry within raw YAML lines.

    ``start`` is the index of the entry's ``- id:`` line; ``end`` is the index
    just past the entry's last content line (i.e. the start of the next entry,
    or the end of the file). Both are 0-indexed. Returns ``(None, None)`` when
    the entry id is not found.
    """
    start: int | None = None
    end: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- id:") or stripped.startswith("- id :"):
            id_val = stripped.split(":", 1)[1].strip().strip("\"'")
            if id_val == entry_id:
                start = i
            elif start is not None and end is None:
                end = i
                break
    if start is not None and end is None:
        # Last entry in the file: walk back to the final non-blank line.
        for i in range(len(lines) - 1, start, -1):
            if lines[i].strip():
                end = i + 1
                break
        if end is None:
            end = start + 1
    return start, end


def line_indent(line: str) -> int:
    """Return the number of leading spaces on `line`."""
    return len(line) - len(line.lstrip())


# ---------------------------------------------------------------------------
# Change ledger
# ---------------------------------------------------------------------------


def append_ledger(
    ledger_path: Path,
    op: str,
    entry_id: str,
    label: str | None,
    details: str | None = None,
) -> None:
    """Append one line to the append-only change ledger.

    Line format::

        <ISO-UTC timestamp> <op> <entry_id> label=<label> [details=<details>]

    A ledger-write failure prints a warning to stderr but never aborts the
    underlying CRUD operation — the YAML remains the source of truth.
    """
    label = label or "unlabeled"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{timestamp} {op} {entry_id} label={label}"
    if details:
        safe = str(details).replace("\n", " ").replace("\r", " ")
        line += f" details={safe}"
    line += "\n"
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(line)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError as exc:  # pragma: no cover - filesystem failure is rare
        print(f"WARNING: failed to write ledger entry: {exc}", file=sys.stderr)
