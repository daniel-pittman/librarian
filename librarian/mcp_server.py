"""Librarian MCP server — exposes the activity database to MCP clients.

This wraps the librarian CLI behind a FastMCP server so any MCP-capable client
(Claude Code, etc.) can search and mutate the database without copy/pasting
context between sessions.

* Read tools (search, get, filter, stats, list, ...) need no session label.
* Write tools require a ``session_label`` so the change ledger can attribute
  every mutation.
* If ``LIBRARIAN_MEMORY_DIR`` is set, the directory's Markdown files are
  exposed as MCP resources. There is **no personal default** — the resource
  surface is opt-in only.

Self-bootstrapping venv
-----------------------
The script self-bootstraps an **in-repo** ``.venv`` (pyyaml + mcp) on first run
and re-execs under it. The builder Python is discovered generically: the
interpreter that launched the script if it is new enough, else the first
``python3`` on ``PATH`` that satisfies the minimum version. There is no
hardcoded user pyenv path.

Run it directly::

    python3 -m librarian.mcp_server

Register it with an MCP client by pointing the client at that command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Self-bootstrap: build the in-repo .venv if missing, then re-exec under it.
# This must run before any third-party import (yaml, mcp).
# ---------------------------------------------------------------------------

# The repository root is two levels up from this file (librarian/mcp_server.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENV_DIR = _REPO_ROOT / ".venv"
_VENV_PY = _VENV_DIR / "bin" / "python3"
_VENV_DEPS = ("pyyaml", "mcp")
_MIN_PY = (3, 10)  # the `mcp` package requires >= 3.10


def _find_builder_python() -> str:
    """Return a ``python3`` executable able to build the venv (>= 3.10).

    Prefers the interpreter that launched this script; otherwise probes
    ``python3`` / ``python3.13`` / ... on ``PATH`` and common install
    locations. Every candidate is version-gated. No user-specific path is
    hardcoded — discovery is fully generic.
    """
    import shutil

    if sys.version_info >= _MIN_PY:
        return sys.executable

    candidates = [
        shutil.which("python3"),
        shutil.which("python3.13"),
        shutil.which("python3.12"),
        shutil.which("python3.11"),
        shutil.which("python3.10"),
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    probe = f"import sys; sys.exit(0 if sys.version_info >= {_MIN_PY} else 1)"
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not os.path.exists(candidate):
            continue
        seen.add(candidate)
        try:
            subprocess.check_call(
                [candidate, "-c", probe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return candidate
        except (subprocess.CalledProcessError, OSError):
            continue
    raise RuntimeError(
        f"No python3 >= {_MIN_PY[0]}.{_MIN_PY[1]} found to build {_VENV_DIR}; "
        f"install one and retry."
    )


def _bootstrap_venv() -> None:
    """Create the in-repo ``.venv`` if absent, then re-exec this script in it."""
    if not _VENV_PY.exists():
        builder = _find_builder_python()
        # Log to stderr so the MCP stdio transport (stdout) is not corrupted.
        print(f"[librarian-mcp] bootstrapping {_VENV_DIR} with {builder}", file=sys.stderr)
        subprocess.check_call([builder, "-m", "venv", str(_VENV_DIR)])
        subprocess.check_call(
            [str(_VENV_PY), "-m", "pip", "install", "--quiet", "--upgrade", "pip"]
        )
        subprocess.check_call([str(_VENV_PY), "-m", "pip", "install", "--quiet", *_VENV_DEPS])
    if os.path.realpath(sys.executable) != os.path.realpath(str(_VENV_PY)):
        os.execv(str(_VENV_PY), [str(_VENV_PY), __file__, *sys.argv[1:]])


# Only bootstrap when run as a script; importing the module (e.g. in tests)
# should not trigger a venv build or a re-exec.
if __name__ == "__main__":
    # When invoked by file path (``python path/to/mcp_server.py``) instead of
    # as a module (``python -m librarian.mcp_server``), Python leaves
    # ``__package__`` empty, and the relative imports below crash with
    # ``ImportError: attempted relative import with no known parent package``.
    # Repair the package context here -- before any relative import runs --
    # so both invocation styles work.
    if not __package__:
        sys.path.insert(0, str(_REPO_ROOT))
        __package__ = "librarian"
    _bootstrap_venv()

from mcp.server.fastmcp import FastMCP  # noqa: E402

from .paths import resolve_paths  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CLI_MODULE = "librarian.cli"
_PATHS = resolve_paths()

INSTRUCTIONS = """\
librarian — a local-first, plain-text activity tracker exposed over MCP.

The server wraps a YAML database of activity "entries". Each entry has core
fields (id, date, title, description, tags, docs) plus optional structured
"blocks" defined by a pluggable schema (e.g. post-tenure-review or
continuing-education-credit blocks).

Workflow rules:

1. Search before creating. Call `librarian_similar` or `librarian_search`
   first to avoid duplicate entries.
2. Never edit the YAML directly — every change must go through the write
   tools so the change ledger captures it.
3. Every write tool requires `session_label`, formatted `<context>:<purpose>`
   (e.g. `cli:main-curator`). The label feeds the ledger so other sessions can
   audit who/where each change came from.
4. Tag entries richly and consistently — run `librarian_tags` first to reuse
   established tag forms.
5. Structured blocks are validated against the active schema. Inspect the
   schema with `librarian_schema`.
"""

mcp = FastMCP("librarian", instructions=INSTRUCTIONS)


def _run_cli(
    args: list[str], *, stdin: str | None = None, extra_env: dict[str, str] | None = None
) -> dict:
    """Invoke the librarian CLI as a subprocess and capture its result.

    The current environment (including any LIBRARIAN_* overrides) is passed
    through so the child sees the same data home as the server.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-m", _CLI_MODULE, *args],
        capture_output=True,
        text=True,
        input=stdin,
        env=env,
        cwd=str(_REPO_ROOT),
    )
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _validate_label(label: str) -> str:
    """Validate a session label. Raise ValueError on malformed input."""
    if not isinstance(label, str) or not label.strip():
        raise ValueError(
            "session_label is required and must be non-empty. "
            "Format: <context>:<short-purpose>, e.g. 'cli:demo-prep'"
        )
    label = label.strip()
    if any(ch.isspace() for ch in label):
        raise ValueError("session_label may not contain whitespace")
    if len(label) > 120:
        raise ValueError("session_label must be 120 characters or fewer")
    return label


def _out(result: dict) -> str:
    """Return stdout, or an ERROR string built from stderr/stdout on failure."""
    if result["ok"]:
        return result["stdout"]
    msg = (result["stderr"] or result["stdout"]).strip()
    # The CLI prints its own `ERROR: ...` to stdout on failure; don't double
    # the prefix when we fall back to stdout.
    return msg if msg.startswith("ERROR:") else f"ERROR: {msg}"


def _capped(result: dict, cap: int = 50_000) -> str:
    """Like :func:`_out`, but truncate very long successful output.

    Used by the potentially large read tools (search / list / project) so a
    CLI failure still surfaces its stderr instead of returning empty output.
    """
    out = _out(result)
    return out if len(out) <= cap else out[:cap] + "\n\n[... truncated]"


# =============================================================================
# READ tools
# =============================================================================


@mcp.tool()
def librarian_search(
    query: str, changed_since: str | None = None, changed_until: str | None = None
) -> str:
    """Full-text search across all entries. Use first when asked "is X tracked?".

    `changed_since` / `changed_until` restrict results to entries whose last
    ledger-recorded change falls in that window (ISO timestamp; naive = UTC).
    Entries with no ledger history are excluded when either bound is set.
    """
    args = ["search", query]
    if changed_since:
        args += ["--changed-since", changed_since]
    if changed_until:
        args += ["--changed-until", changed_until]
    return _capped(_run_cli(args))


@mcp.tool()
def librarian_get(entry_id: str) -> str:
    """Fetch the complete YAML for one entry by its id."""
    return _out(_run_cli(["get", entry_id]))


@mcp.tool()
def librarian_filter(
    category: str | None = None,
    block_field: str | None = None,
    after: str | None = None,
    before: str | None = None,
    year: str | None = None,
    tag: str | None = None,
    has_block: str | None = None,
    changed_since: str | None = None,
    changed_until: str | None = None,
    brief: bool = False,
) -> str:
    """Filter entries by schema block field, date range, tag, or block presence.

    `block_field` is `BLOCK.FIELD=VALUE` (e.g. `ptr.category=scholarly`).
    `changed_since` / `changed_until` restrict results to entries whose last
    ledger-recorded change falls in that window (ISO timestamp; naive = UTC).
    Combine with `tag` to find what changed since you last pulled — useful
    before exporting a slice into an external system (e.g. tag="grant",
    changed_since="2026-05-01"). Entries with no ledger history are excluded
    when either bound is set.
    """
    args = ["filter"]
    if category:
        args += ["--category", category]
    if block_field and "=" in block_field:
        path, value = block_field.split("=", 1)
        args += ["--block-field", path, value]
    if after:
        args += ["--after", after]
    if before:
        args += ["--before", before]
    if year:
        args += ["--year", year]
    if tag:
        args += ["--tag", tag]
    if has_block:
        args += ["--has-block", has_block]
    if changed_since:
        args += ["--changed-since", changed_since]
    if changed_until:
        args += ["--changed-until", changed_until]
    if brief:
        args.append("--brief")
    return _out(_run_cli(args))


@mcp.tool()
def librarian_stats() -> str:
    """Summary statistics, grouped by the active schema's blocks."""
    return _out(_run_cli(["stats"]))


@mcp.tool()
def librarian_tags() -> str:
    """List every tag in use, with frequency counts."""
    return _out(_run_cli(["tags"]))


@mcp.tool()
def librarian_validate() -> str:
    """Validate the database for parse, schema, and inventory-integrity issues."""
    return _out(_run_cli(["validate"]))


@mcp.tool()
def librarian_schema() -> str:
    """Describe the active schema — its blocks, fields, types, and enum values.

    Output enumerates each enum field's allowed values (including dependent
    enums like category -> subcategory), so valid choices are discoverable
    without reading schema.yaml.
    """
    return _out(_run_cli(["schema"]))


@mcp.tool()
def librarian_env() -> str:
    """Show the resolved data-home paths and their sources (the 'truth sources').

    Reports where each librarian resource (activities, files, ledger, schema,
    root, ...) resolves to, which LIBRARIAN_* variable set it, and whether it
    exists — so you can discover the active setup without inspecting the
    environment. Paths are local to this machine.
    """
    return _out(_run_cli(["env"]))


@mcp.tool()
def librarian_list(
    brief: bool = True, changed_since: str | None = None, changed_until: str | None = None
) -> str:
    """List all entries in date order.

    `changed_since` / `changed_until` restrict results to entries whose last
    ledger-recorded change falls in that window (ISO timestamp; naive = UTC).
    Entries with no ledger history are excluded when either bound is set.
    """
    args = ["list"] if brief else ["list", "--full"]
    if changed_since:
        args += ["--changed-since", changed_since]
    if changed_until:
        args += ["--changed-until", changed_until]
    return _capped(_run_cli(args))


@mcp.tool()
def librarian_similar(text: str) -> str:
    """Fuzzy-match `text` against existing entries. USE THIS BEFORE create."""
    return _out(_run_cli(["similar", text]))


@mcp.tool()
def librarian_project(project_name: str) -> str:
    """Return all entries tagged or keyword-matched for a project."""
    return _capped(_run_cli(["project", project_name]))


@mcp.tool()
def librarian_contact(
    query: str | None = None, institution: str | None = None, show_all: bool = False
) -> str:
    """Rolodex lookup over `Name (email)` patterns embedded in descriptions."""
    if show_all:
        args = ["contact", "--all"]
    elif institution:
        args = ["contact", "--institution", institution]
    elif query:
        args = ["contact", query]
    else:
        return "ERROR: provide a query, institution, or set show_all=True"
    return _out(_run_cli(args))


@mcp.tool()
def librarian_changes_since(
    since: str | None = None,
    limit: int = 50,
    label_pattern: str | None = None,
    op: str | None = None,
    entry_id: str | None = None,
) -> str:
    """Poll the change ledger. Returns ledger lines as JSON, bundled with the
    current state of each affected entry.
    """
    args = ["changes", "--format", "json", "--limit", str(limit)]
    if since:
        args += ["--since", since]
    if label_pattern:
        args += ["--label-pattern", label_pattern]
    if op:
        args += ["--op", op]
    if entry_id:
        args += ["--id", entry_id]
    # Route through _out so a CLI failure surfaces its stderr instead of
    # returning empty stdout — a silent-empty result previously masked a
    # crash in `changes --since` and made the ledger look empty.
    return _out(_run_cli(args))


# =============================================================================
# WRITE tools — session_label required
# =============================================================================


@mcp.tool()
def librarian_create(entry_json: str, session_label: str) -> str:
    """Create a new entry from a JSON string.

    Required keys: id, date, title, description, tags. Optional: end_date,
    docs, and any schema block. Schema blocks are validated before the write.
    """
    label = _validate_label(session_label)
    try:
        json.loads(entry_json)
    except json.JSONDecodeError as exc:
        return f"ERROR: entry_json is not valid JSON: {exc}"
    return _out(
        _run_cli(["create", "--json", entry_json], extra_env={"LIBRARIAN_SESSION_LABEL": label})
    )


@mcp.tool()
def librarian_update_field(entry_id: str, field: str, value: str, session_label: str) -> str:
    """Update a top-level field: title, date, end_date, or docs_optional.

    docs_optional is a boolean ("true"/"false") that suppresses the NO DOCS
    validation warning for an entry that legitimately has no artifact.
    """
    label = _validate_label(session_label)
    return _out(
        _run_cli(
            ["update-field", entry_id, field, value], extra_env={"LIBRARIAN_SESSION_LABEL": label}
        )
    )


@mcp.tool()
def librarian_update_description(entry_id: str, new_description: str, session_label: str) -> str:
    """Replace an entry's description (paragraph breaks are preserved)."""
    label = _validate_label(session_label)
    return _out(
        _run_cli(
            ["update-description", entry_id],
            stdin=new_description,
            extra_env={"LIBRARIAN_SESSION_LABEL": label},
        )
    )


@mcp.tool()
def librarian_update_notes(
    entry_id: str, new_notes: str, session_label: str, block: str = "", field: str = "notes"
) -> str:
    """Update a block's notes/text field. `block` defaults to the schema's
    first block; `field` defaults to `notes`.
    """
    label = _validate_label(session_label)
    args = ["update-notes", entry_id, "--field", field]
    if block:
        args += ["--block", block]
    return _out(_run_cli(args, stdin=new_notes, extra_env={"LIBRARIAN_SESSION_LABEL": label}))


@mcp.tool()
def librarian_update_block_field(
    entry_id: str, block: str, field: str, value: str, session_label: str
) -> str:
    """Update one schema-block field (e.g. block='ptr', field='category').

    The value is validated against the active schema before the write.
    """
    label = _validate_label(session_label)
    return _out(
        _run_cli(
            ["update-nested-field", entry_id, f"{block}.{field}", value],
            extra_env={"LIBRARIAN_SESSION_LABEL": label},
        )
    )


@mcp.tool()
def librarian_set_block(entry_id: str, block: str, fields_json: str, session_label: str) -> str:
    """Add a schema block to an existing entry.

    ``fields_json`` is a JSON object of field values for the block (e.g.
    ``{"group": "primary", "credits": 10}``). The block must be declared by
    the active schema and must not already be present on the entry; the full
    block is validated atomically before the write. To edit an existing
    block's fields, use ``librarian_update_block_field``.
    """
    label = _validate_label(session_label)
    return _out(
        _run_cli(
            ["set-block", entry_id, block, "--json", fields_json],
            extra_env={"LIBRARIAN_SESSION_LABEL": label},
        )
    )


@mcp.tool()
def librarian_add_tags(entry_id: str, tags: list[str], session_label: str) -> str:
    """Add tags to an entry (idempotent — duplicates are no-ops)."""
    label = _validate_label(session_label)
    if not tags:
        return "ERROR: tags list is empty"
    return _out(
        _run_cli(["add-tags", entry_id, *tags], extra_env={"LIBRARIAN_SESSION_LABEL": label})
    )


@mcp.tool()
def librarian_remove_tags(entry_id: str, tags: list[str], session_label: str) -> str:
    """Remove tags from an entry."""
    label = _validate_label(session_label)
    if not tags:
        return "ERROR: tags list is empty"
    return _out(
        _run_cli(["remove-tags", entry_id, *tags], extra_env={"LIBRARIAN_SESSION_LABEL": label})
    )


@mcp.tool()
def librarian_add_docs(entry_id: str, docs: list[str], session_label: str) -> str:
    """Add doc references (URLs or `file:<id>` tokens) to an entry."""
    label = _validate_label(session_label)
    if not docs:
        return "ERROR: docs list is empty"
    return _out(
        _run_cli(["add-docs", entry_id, *docs], extra_env={"LIBRARIAN_SESSION_LABEL": label})
    )


@mcp.tool()
def librarian_remove_docs(entry_id: str, doc: str, session_label: str) -> str:
    """Remove a single doc reference from an entry."""
    label = _validate_label(session_label)
    return _out(
        _run_cli(["remove-docs", entry_id, doc], extra_env={"LIBRARIAN_SESSION_LABEL": label})
    )


@mcp.tool()
def librarian_delete(
    entry_id: str,
    session_label: str,
    confirm: bool = False,
    repoint_to: str | None = None,
) -> str:
    """Delete an entry. Requires confirm=True; otherwise returns a dry-run preview.

    ``repoint_to``, if set, rewrites every backticked or plain-text reference
    to ``entry_id`` to point at that target id before the source is removed —
    so the delete does not leave dangling cross-references behind.
    """
    label = _validate_label(session_label)
    args = ["delete", entry_id]
    if repoint_to:
        args += ["--repoint-to", repoint_to]
    if confirm:
        args.append("--confirm")
    return _out(_run_cli(args, extra_env={"LIBRARIAN_SESSION_LABEL": label}))


@mcp.tool()
def librarian_rename_id(old_id: str, new_id: str, session_label: str) -> str:
    """Rename an entry id, repointing every cross-reference to the old id --
    both backticked and plain-text -- in descriptions and notes. Matches are
    bounded by id-character lookarounds, so a rename of ``ongoing-coi`` will
    not touch ``ongoing-coi-training``."""
    label = _validate_label(session_label)
    return _out(
        _run_cli(["rename-id", old_id, new_id], extra_env={"LIBRARIAN_SESSION_LABEL": label})
    )


@mcp.tool()
def librarian_merge(
    source_ids: list[str],
    target_id: str,
    session_label: str,
    confirm: bool = False,
    on_block_conflict: str = "abort",
    with_provenance: bool = True,
    append_sources: bool = False,
) -> str:
    """Merge one or more source entries into a target, atomically.

    Tags and docs are unioned onto the target (de-duplicated, order-stable).
    Schema blocks the target lacks are carried over from sources; same-block
    conflicts respect ``on_block_conflict`` (``abort`` / ``keep-target`` /
    ``keep-source``; default ``abort``). Every backticked or plain-text
    reference to each source id is repointed to the target before the source
    entries are deleted, so the merge does not leave dangling cross-references.

    By default the target's description stays as-is and source descriptions
    are returned in the dry-run output for manual folding via
    ``librarian_update_description`` (description merging is editorial).
    Set ``append_sources=True`` to append each source's description verbatim
    under a ``## From <source-id>`` plain-text header in the target's
    literal-block description (a mechanical fold for callers who want it).
    """
    label = _validate_label(session_label)
    args = ["merge", *source_ids, "--into", target_id]
    if on_block_conflict and on_block_conflict != "abort":
        args += ["--on-block-conflict", on_block_conflict]
    if append_sources:
        args.append("--append-sources")
    if not with_provenance:
        args.append("--no-provenance")
    if confirm:
        args.append("--confirm")
    return _out(_run_cli(args, extra_env={"LIBRARIAN_SESSION_LABEL": label}))


# =============================================================================
# File-inventory tools
# =============================================================================


@mcp.tool()
def librarian_file_add(
    path: str,
    category: str,
    title: str,
    session_label: str,
    description: str = "",
    file_id: str = "",
) -> str:
    """Register a file in the inventory. The path must exist on disk."""
    label = _validate_label(session_label)
    args = ["file-add", path, "--category", category, "--title", title]
    if description:
        args += ["--description", description]
    if file_id:
        args += ["--id", file_id]
    return _out(_run_cli(args, extra_env={"LIBRARIAN_SESSION_LABEL": label}))


@mcp.tool()
def librarian_file_list(category: str = "", orphans: bool = False) -> str:
    """List inventory files; orphans=True runs an inventory coverage report."""
    args = ["file-list"]
    if category:
        args += ["--category", category]
    if orphans:
        args.append("--orphans")
    return _out(_run_cli(args))


@mcp.tool()
def librarian_file_get(file_id: str) -> str:
    """Show one inventory file record and which entries reference it."""
    return _out(_run_cli(["file-get", file_id]))


@mcp.tool()
def librarian_file_move(file_id: str, new_path: str, session_label: str) -> str:
    """Move a registered file on disk and update its inventory path together."""
    label = _validate_label(session_label)
    return _out(
        _run_cli(["file-move", file_id, new_path], extra_env={"LIBRARIAN_SESSION_LABEL": label})
    )


@mcp.tool()
def librarian_file_update(
    file_id: str,
    session_label: str,
    category: str = "",
    title: str = "",
    description: str = "",
) -> str:
    """Update a registered file's category, title, and/or description."""
    label = _validate_label(session_label)
    args = ["file-update", file_id]
    if category:
        args += ["--category", category]
    if title:
        args += ["--title", title]
    if description:
        args += ["--description", description]
    return _out(_run_cli(args, extra_env={"LIBRARIAN_SESSION_LABEL": label}))


@mcp.tool()
def librarian_file_search(query: str) -> str:
    """Full-text search across the file inventory."""
    return _out(_run_cli(["file-search", query]))


@mcp.tool()
def librarian_file_rehash(session_label: str, file_id: str = "") -> str:
    """Recompute sha256 for one file, or the whole inventory if file_id is empty."""
    label = _validate_label(session_label)
    args = ["file-rehash", file_id if file_id else "--all"]
    return _out(_run_cli(args, extra_env={"LIBRARIAN_SESSION_LABEL": label}))


# =============================================================================
# Resources — optional memory directory (no personal default)
# =============================================================================


@mcp.resource("memory://index")
def memory_index() -> str:
    """The memory directory's index file, if LIBRARIAN_MEMORY_DIR is configured."""
    if _PATHS.memory_dir is None:
        return "(LIBRARIAN_MEMORY_DIR is not set — memory resources are disabled)"
    index = _PATHS.memory_dir / "MEMORY.md"
    return index.read_text(encoding="utf-8") if index.exists() else f"(no MEMORY.md at {index})"


@mcp.resource("memory://{filename}")
def memory_file(filename: str) -> str:
    """Read a named file from the configured memory directory.

    Path traversal is rejected — only files directly inside the memory
    directory are readable.
    """
    if _PATHS.memory_dir is None:
        return "(LIBRARIAN_MEMORY_DIR is not set — memory resources are disabled)"
    safe_name = Path(filename).name
    path = _PATHS.memory_dir / safe_name
    if path.resolve().parent != _PATHS.memory_dir.resolve():
        return "ERROR: path traversal attempt rejected"
    return path.read_text(encoding="utf-8") if path.exists() else f"(no memory file at {path})"


if __name__ == "__main__":
    mcp.run()
