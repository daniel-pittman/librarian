"""The librarian command-line interface.

This module wires the storage, schema, file-inventory and core-analysis layers
into an argparse command dispatch. Every command is a ``cmd_*`` function that
takes ``(ctx, args)`` where ``ctx`` is a :class:`Context` snapshot of resolved
paths plus the loaded schema, and ``args`` is the raw argument list for that
subcommand.

Read commands work without a session label. Write commands require a
``--label`` argument (or the ``LIBRARIAN_SESSION_LABEL`` env var) so the change
ledger can attribute every mutation.

Schema awareness
----------------
The structured "blocks" on an entry (``ptr``, ``cpe`` or whatever the active
schema declares) are validated, filtered and updated against the loaded
schema — nothing about any particular block is hardcoded. With no schema
configured the tool still does fully generic CRUD/search/file-inventory; blocks
simply are not validated.
"""

from __future__ import annotations

import argparse
import csv
import functools
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import __version__
from .core import (
    best_similarity,
    canonical_name,
    extract_contacts,
    filter_entries,
    scan_dangling_refs,
    similarity_score,
    tag_kernel,
)
from .files import (
    load_files,
    rel_to_root,
    save_files,
    search_files,
    sha256_of,
    slugify_filename,
    today_iso,
    unique_file_id,
)
from .paths import LibrarianPaths, resolve_paths
from .schema import Schema, SchemaError, coerce_value, load_schema, validate_block
from .storage import (
    append_ledger,
    atomic_replace,
    find_entry_line_range,
    line_indent,
    load_activities,
    read_lines,
    write_lines,
    write_lock,
    yaml_quote,
)

# Text fields scanned for dangling cross-references. The list is built from the
# core fields plus every text/notes field declared by the active schema.
_CORE_TEXT_FIELDS = [("description", ["description"])]


# =============================================================================
# Command context
# =============================================================================


@dataclass
class Context:
    """Everything a command needs: resolved paths and the active schema."""

    paths: LibrarianPaths
    schema: Schema

    @property
    def text_ref_fields(self) -> list[tuple[str, list[str]]]:
        """Text fields to scan for dangling refs (core + schema text fields)."""
        fields = list(_CORE_TEXT_FIELDS)
        for block in self.schema.blocks:
            for field in block.fields:
                if field.type in ("text", "string"):
                    fields.append((f"{block.name}.{field.name}", [block.name, field.name]))
        return fields


def build_context(env: dict[str, str] | None = None) -> Context:
    """Resolve paths and load the active schema into a :class:`Context`."""
    paths = resolve_paths(env)
    schema = load_schema(paths.schema)
    return Context(paths=paths, schema=schema)


# =============================================================================
# Shared helpers
# =============================================================================


def _is_pure_read_invocation(args: list[str]) -> bool:
    """True when ``args`` clearly won't reach a write path.

    Conservative: matches only ``librarian <cmd> -h`` / ``librarian <cmd>
    --help`` (i.e. the help flag is the FIRST and only argument). That's
    the canonical "show usage" call and is guaranteed to exit before any
    write code runs.

    Why not also skip on ``--dry-run`` (or ``--help`` anywhere in argv)?
    Several writer commands (``add-tags``, ``remove-tags``, ``add-docs``,
    ``remove-docs``, ...) take user-controlled positional values and DON'T
    route them through argparse — a legitimate positional value can be the
    literal string ``--dry-run`` or ``--help`` (e.g. a tag named
    ``--dry-run``). Skipping the lock on a membership test would defeat
    the cross-process atomicity invariant the decorator was built to
    enforce. The cost of the narrower skip is one short lock-acquire/
    release on writer-help invocations beyond ``<cmd> --help`` — the
    ``.lock`` sidecar is still materialized once per fresh data home,
    which is a one-time cosmetic cost that's nowhere near a correctness
    regression.
    """
    return len(args) == 1 and args[0] in ("-h", "--help")


def _activities_locked(func):
    """Take ``write_lock(ctx.paths.activities)`` around the whole command,
    skipping it for clearly pure-read invocations (``--help`` / ``--dry-run``).

    Wraps a writer command so its read-plan-write transaction is exclusive
    across processes: ``read_lines`` / ``load_activities`` and the eventual
    ``write_lines`` / ``append_text`` happen under the same fcntl flock, so
    a concurrent writer cannot clobber a multi-step operation mid-flight.
    The lock is reentrant within a single thread, so the inner write helpers
    (which also use ``write_lock``) compose cleanly.

    Pure-read invocations skip the lock entirely (see
    :func:`_is_pure_read_invocation`). For commands that do expensive work
    OUTSIDE the lock-critical section (stdin reads, large similarity scans,
    etc.) prefer dropping the decorator and using explicit ``with
    write_lock(...):`` around just the read-plan-write phase.
    """

    @functools.wraps(func)
    def wrapper(ctx: Context, args: list[str]) -> int:
        if _is_pure_read_invocation(args):
            return func(ctx, args)
        with write_lock(ctx.paths.activities):
            return func(ctx, args)

    return wrapper


def _files_locked(func):
    """Like :func:`_activities_locked` but for file-inventory writers.

    Same pure-read skip semantics: ``--help`` / ``--dry-run`` invocations
    do not materialize ``files.yaml.lock``.
    """

    @functools.wraps(func)
    def wrapper(ctx: Context, args: list[str]) -> int:
        if _is_pure_read_invocation(args):
            return func(ctx, args)
        with write_lock(ctx.paths.files):
            return func(ctx, args)

    return wrapper


def _resolve_label(args: list[str], *, required: bool = True) -> str | None:
    """Pop ``--label LABEL`` from `args` and return the label.

    Falls back to the ``LIBRARIAN_SESSION_LABEL`` env var. Mutates `args` in
    place. When `required` and no label is found, prints an error and returns
    ``None`` so the caller can abort.
    """
    label = None
    if "--label" in args:
        i = args.index("--label")
        if i + 1 < len(args):
            candidate = args[i + 1]
            # Reject another flag as the label value. Without this guard a
            # typo like ``--label --dry-run --json '...'`` silently swallows
            # ``--dry-run`` as the label, drops it from argv, and the write
            # path then runs with ``label="--dry-run"`` — completely
            # defeating the dry-run intent and tagging the ledger with a
            # bogus label. ``sys.exit(1)`` here (not ``return None``) so the
            # caller can't accidentally treat the bad-value case as a
            # missing-label and fall through to a labelless dry-run.
            if candidate.startswith("-"):
                print(
                    f"ERROR: --label requires a value (got '{candidate}', "
                    f"which looks like another flag). Format: "
                    f"<context>:<short-purpose>, e.g. 'cli:main-curator'"
                )
                sys.exit(1)
            label = candidate
            args.pop(i + 1)
        args.pop(i)
    if not label:
        label = os.environ.get("LIBRARIAN_SESSION_LABEL")
    if not label and required:
        print(
            "ERROR: write operations require --label LABEL or the "
            "LIBRARIAN_SESSION_LABEL env var.\n"
            "       Format: <context>:<short-purpose>, e.g. 'cli:main-curator'"
        )
        return None
    return label


def print_entry(entry: dict, *, brief: bool = False) -> None:
    """Print one entry — id/date/title in brief mode, full YAML otherwise."""
    if brief:
        print(
            f"  {entry.get('date', '?'):12s} | "
            f"{entry.get('id', '?'):42s} | {entry.get('title', '?')[:80]}"
        )
    else:
        print(yaml.dump(entry, default_flow_style=False, allow_unicode=True, width=120))
        print("---")


def _during_window(year: str | None, during: str | None) -> tuple[str | None, str | None]:
    """Resolve a ``--year`` or ``--during START:END`` flag into a date window."""
    if year:
        return f"{year}-01-01", f"{year}-12-31"
    if during:
        parts = during.split(":")
        return (parts[0] or None, parts[1] if len(parts) > 1 and parts[1] else None)
    return None, None


# =============================================================================
# READ commands
# =============================================================================


def cmd_search(ctx: Context, args: list[str]) -> int:
    """Full-text search across every entry. ``search <query> [--brief] ...``"""
    parser = argparse.ArgumentParser(prog="librarian search")
    parser.add_argument("query", help="search text")
    parser.add_argument("--brief", action="store_true", help="brief output")
    parser.add_argument("--year", help="restrict to entries active in this year")
    parser.add_argument("--during", help="entries active during YYYY-MM-DD:YYYY-MM-DD")
    parser.add_argument("--after", help="entries starting after this date")
    parser.add_argument("--before", help="entries starting before this date")
    parser.add_argument(
        "--changed-since", help="entries last changed (per ledger) at or after this timestamp"
    )
    parser.add_argument(
        "--changed-until", help="entries last changed (per ledger) at or before this timestamp"
    )
    parsed = parser.parse_args(args)

    during_start, during_end = _during_window(parsed.year, parsed.during)
    _, activities = load_activities(ctx.paths.activities)
    results = filter_entries(
        activities,
        query=parsed.query,
        during_start=during_start,
        during_end=during_end,
        after=parsed.after,
        before=parsed.before,
    )
    try:
        results = _apply_changed_window(
            results,
            changed_since=parsed.changed_since,
            changed_until=parsed.changed_until,
            ledger_path=ctx.paths.ledger,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Found {len(results)} entries matching '{parsed.query}':\n")
    for entry in results:
        print_entry(entry, brief=parsed.brief)

    # Cross-surface the file inventory: note any inventory files that match.
    file_hits = len(search_files(load_files(ctx.paths.files), parsed.query))
    if file_hits:
        print(
            f"({file_hits} inventory file(s) also match — run "
            f'`file-search "{parsed.query}"` to see them.)'
        )
    return 0


def cmd_get(ctx: Context, args: list[str]) -> int:
    """Fetch one complete entry by id. ``get <entry-id>``"""
    if not args:
        print("Usage: librarian get <entry-id>")
        return 1
    entry_id = args[0]
    _, activities = load_activities(ctx.paths.activities)
    for entry in activities:
        if entry.get("id") == entry_id:
            print_entry(entry, brief=False)
            _print_resolved_file_refs(ctx, entry)
            return 0
    # Fall back to a fuzzy id-substring match.
    matches = [e for e in activities if entry_id in (e.get("id", "") or "")]
    if matches:
        print(f"No exact match for '{entry_id}'. Partial matches:")
        for entry in matches:
            print_entry(entry, brief=True)
        return 0
    print(f"No entry found with id containing '{entry_id}'")
    return 1


def cmd_filter(ctx: Context, args: list[str]) -> int:
    """Filter entries by structured criteria. ``filter [--block-field ...] ...``

    ``--block-field BLOCK.FIELD VALUE`` filters on any schema block field —
    e.g. ``--block-field ptr.category scholarly``. The legacy ``--category``
    and ``--cpe`` flags are kept as convenience aliases when the active schema
    declares ``ptr`` / ``cpe`` blocks.
    """
    parser = argparse.ArgumentParser(prog="librarian filter")
    parser.add_argument("--block-field", nargs=2, metavar=("BLOCK.FIELD", "VALUE"))
    parser.add_argument("--has-block", help="only entries carrying this block")
    parser.add_argument("--no-block", help="only entries without this block")
    parser.add_argument("--category", help="alias for --block-field ptr.category")
    parser.add_argument("--subcategory", help="alias for --block-field ptr.subcategory")
    parser.add_argument("--cpe", action="store_true", help="alias for --has-block cpe")
    parser.add_argument("--no-cpe", action="store_true", help="alias for --no-block cpe")
    parser.add_argument("--after", help="entries starting after this date")
    parser.add_argument("--before", help="entries starting before this date")
    parser.add_argument("--during", help="entries active during a range")
    parser.add_argument("--year", help="entries active during a year")
    parser.add_argument("--tag", action="append", dest="tags", help="tag (repeatable)")
    parser.add_argument(
        "--changed-since", help="entries last changed (per ledger) at or after this timestamp"
    )
    parser.add_argument(
        "--changed-until", help="entries last changed (per ledger) at or before this timestamp"
    )
    parser.add_argument("--brief", action="store_true", help="brief output")
    parser.add_argument("--count", action="store_true", help="print count only")
    parsed = parser.parse_args(args)

    during_start, during_end = _during_window(parsed.year, parsed.during)

    # Resolve a single block-field filter from the explicit flag or an alias.
    block_field = None
    if parsed.block_field:
        path, value = parsed.block_field
        if "." not in path:
            print("ERROR: --block-field path must be BLOCK.FIELD")
            return 1
        block, field = path.split(".", 1)
        block_field = (block, field, value)
    elif parsed.category:
        block_field = ("ptr", "category", parsed.category)
    elif parsed.subcategory:
        block_field = ("ptr", "subcategory", parsed.subcategory)

    has_block = None
    if parsed.has_block or parsed.cpe:
        has_block = (parsed.has_block or "cpe", True)
    elif parsed.no_block or parsed.no_cpe:
        has_block = (parsed.no_block or "cpe", False)

    _, activities = load_activities(ctx.paths.activities)
    results = filter_entries(
        activities,
        after=parsed.after,
        before=parsed.before,
        during_start=during_start,
        during_end=during_end,
        tags=parsed.tags,
        has_block=has_block,
        block_field=block_field,
    )
    try:
        results = _apply_changed_window(
            results,
            changed_since=parsed.changed_since,
            changed_until=parsed.changed_until,
            ledger_path=ctx.paths.ledger,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if parsed.count:
        print(len(results))
        return 0
    print(f"Found {len(results)} entries:\n")
    for entry in results:
        print_entry(entry, brief=parsed.brief)
    return 0


def cmd_list(ctx: Context, args: list[str]) -> int:
    """List entries, optionally date-filtered. ``list [--full] [--year ...]``"""
    parser = argparse.ArgumentParser(prog="librarian list")
    parser.add_argument("--full", action="store_true", help="full output")
    parser.add_argument("--year", help="entries active during this year")
    parser.add_argument("--during", help="entries active during a range")
    parser.add_argument("--after", help="entries starting after this date")
    parser.add_argument("--before", help="entries starting before this date")
    parser.add_argument(
        "--changed-since", help="entries last changed (per ledger) at or after this timestamp"
    )
    parser.add_argument(
        "--changed-until", help="entries last changed (per ledger) at or before this timestamp"
    )
    parsed = parser.parse_args(args)

    during_start, during_end = _during_window(parsed.year, parsed.during)
    _, activities = load_activities(ctx.paths.activities)
    if during_start or during_end or parsed.after or parsed.before:
        activities = filter_entries(
            activities,
            during_start=during_start,
            during_end=during_end,
            after=parsed.after,
            before=parsed.before,
        )
    try:
        activities = _apply_changed_window(
            activities,
            changed_since=parsed.changed_since,
            changed_until=parsed.changed_until,
            ledger_path=ctx.paths.ledger,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Total entries: {len(activities)}\n")
    brief = not parsed.full
    if brief:
        print(f"  {'Date':12s} | {'ID':42s} | {'Title':80s}")
        print(f"  {'-' * 12} | {'-' * 42} | {'-' * 80}")
    for entry in activities:
        print_entry(entry, brief=brief)
    return 0


def cmd_stats(ctx: Context, args: list[str]) -> int:
    """Print database statistics, grouped by the active schema's blocks."""
    _, activities = load_activities(ctx.paths.activities)
    print(f"Total entries: {len(activities)}\n")

    # Per-block, per-enum-field breakdowns driven entirely by the schema.
    for block in ctx.schema.blocks:
        with_block = [e for e in activities if block.name in e]
        print(f"{block.label} ({block.name}): {len(with_block)} entries")
        for field in block.fields:
            if field.type != "enum":
                continue
            counts: dict[str, int] = {}
            for entry in with_block:
                value = (entry.get(block.name) or {}).get(field.name, "(unset)")
                counts[str(value)] = counts.get(str(value), 0) + 1
            if counts:
                print(f"  by {field.name}:")
                for value, count in sorted(counts.items(), key=lambda kv: -kv[1]):
                    print(f"    {value:32s} {count:4d}")
        # Sum any int field (e.g. cpe.credits) as a convenience total.
        for field in block.fields:
            if field.type != "int":
                continue
            total = 0
            for entry in with_block:
                raw = (entry.get(block.name) or {}).get(field.name)
                if isinstance(raw, int):
                    total += raw
                elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
                    total += int(raw)
            print(f"  total {field.name}: {total}")
        print()

    if ctx.schema.is_empty:
        print("(no schema configured — running in generic mode)\n")

    # Year histogram — schema-independent.
    years: dict[str, int] = {}
    for entry in activities:
        year = (entry.get("date", "????") or "????")[:4]
        years[year] = years.get(year, 0) + 1
    print("By year:")
    for year, count in sorted(years.items()):
        print(f"  {year}: {count}")

    with_docs = sum(1 for e in activities if e.get("docs"))
    print(f"\nEntries with documentation: {with_docs}")
    return 0


def cmd_tags(ctx: Context, args: list[str]) -> int:
    """List every tag with its usage count."""
    _, activities = load_activities(ctx.paths.activities)
    counts: dict[str, int] = {}
    for entry in activities:
        for tag in entry.get("tags", []) or []:
            counts[tag] = counts.get(tag, 0) + 1
    print(f"Unique tags: {len(counts)}\n")
    for tag, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {tag:45s} {count:4d}")
    return 0


def cmd_tag_audit(ctx: Context, args: list[str]) -> int:
    """Detect case-/separator-variant tag clusters. ``tag-audit [--json]``"""
    parser = argparse.ArgumentParser(prog="librarian tag-audit")
    parser.add_argument("--json", dest="as_json", action="store_true")
    parsed = parser.parse_args(args)

    _, activities = load_activities(ctx.paths.activities)
    counts: dict[str, int] = {}
    for entry in activities:
        for tag in entry.get("tags", []) or []:
            counts[tag] = counts.get(tag, 0) + 1

    by_kernel: dict[str, list[tuple[str, int]]] = {}
    for tag, count in counts.items():
        by_kernel.setdefault(tag_kernel(tag), []).append((tag, count))
    clusters = [variants for variants in by_kernel.values() if len(variants) > 1]
    for cluster in clusters:
        cluster.sort(key=lambda tc: -tc[1])
    clusters.sort(key=lambda cl: -sum(c for _, c in cl))

    if parsed.as_json:
        print(
            json.dumps(
                [
                    {
                        "canonical_candidate": cl[0][0],
                        "variants": [{"tag": t, "count": c} for t, c in cl],
                        "total_uses": sum(c for _, c in cl),
                    }
                    for cl in clusters
                ],
                indent=2,
            )
        )
        return 0
    if not clusters:
        print("No case-variant tag clusters detected. Tag set is clean.")
        return 0
    print(f"Found {len(clusters)} case-variant cluster(s):\n")
    for cluster in clusters:
        print(f"  Cluster (canonical candidate: '{cluster[0][0]}')")
        for tag, count in cluster:
            print(f"    {tag:35s} {count:3d} uses")
        print()
    return 0


def cmd_validate(ctx: Context, args: list[str]) -> int:
    """Validate the database for structural and schema issues.

    Reports: duplicate ids, missing core fields, schema-block violations,
    entries with no docs, dangling backticked cross-references, and file
    inventory integrity problems (``MISSING FILE`` / ``DANGLING FILE REF``).
    """
    _, activities = load_activities(ctx.paths.activities)
    issues: list[str] = []
    ids: set[str] = set()

    for i, entry in enumerate(activities):
        eid = entry.get("id", f"MISSING_ID_AT_{i}")
        if eid in ids:
            issues.append(f"DUPLICATE ID: {eid}")
        ids.add(eid)

        for field in ("date", "title", "description"):
            if not entry.get(field):
                issues.append(f"MISSING {field.upper()}: {eid}")
        # `docs_optional: true` acknowledges an entry that legitimately has no
        # artifact, suppressing the NO DOCS warning so genuine gaps stand out.
        if not entry.get("docs") and not entry.get("docs_optional"):
            issues.append(f"NO DOCS: {eid}")

        # Schema-block validation — every block the entry carries that the
        # schema knows about is checked field-by-field.
        for block in ctx.schema.blocks:
            if block.name not in entry:
                continue
            for issue in validate_block(block, entry.get(block.name) or {}):
                issues.append(f"{issue.split(':', 1)[0]}: {eid} — {issue.split(':', 1)[1].strip()}")

    # File-inventory records (loaded once and reused for both the dangling-ref
    # exclude set and the file-inventory integrity checks below).
    records = load_files(ctx.paths.files)
    inventory_ids = {r.get("id") for r in records if r.get("id")}

    # Build the exclude set for the dangling-ref scanner: any backticked token
    # that matches a tag-in-use or a file-inventory id is NOT a dangling entry
    # reference and should be suppressed (would otherwise produce false
    # positives -- a `c3-lab-output` tag mention, a `<file-id>` mention, etc.).
    tags_in_use: set[str] = set()
    for entry in activities:
        for tag in entry.get("tags") or []:
            if isinstance(tag, str):
                tags_in_use.add(tag)

    # Dangling cross-references across core + schema text fields.
    for src, target, field in scan_dangling_refs(
        activities,
        ids,
        ctx.text_ref_fields,
        exclude=tags_in_use | inventory_ids,
    ):
        issues.append(f"DANGLING REF: {src} -> '{target}' (in {field})")

    # File-inventory integrity. (records + inventory_ids were loaded above.)
    for record in records:
        fpath = record.get("path", "")
        if not fpath or not (ctx.paths.root / fpath).exists():
            issues.append(f"MISSING FILE: {record.get('id', '?')} -> '{fpath}'")
    for entry in activities:
        eid = entry.get("id", "?")
        for doc in entry.get("docs", []) or []:
            if isinstance(doc, str) and doc.startswith("file:"):
                if doc[len("file:") :] not in inventory_ids:
                    issues.append(f"DANGLING FILE REF: {eid} -> '{doc}'")

    if issues:
        print(f"Found {len(issues)} issues:\n")
        for issue in sorted(issues):
            print(f"  {issue}")
    else:
        print("No issues found!")
    print(f"\nTotal entries validated: {len(activities)}")
    return 0


def cmd_export(ctx: Context, args: list[str]) -> int:
    """Export filtered entries to CSV or JSON. ``export --format csv|json ...``"""
    parser = argparse.ArgumentParser(prog="librarian export")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--after")
    parser.add_argument("--before")
    parser.add_argument("--during")
    parser.add_argument("--year")
    parser.add_argument("--tag", action="append", dest="tags")
    parser.add_argument("--output", "-o", help="write to this file instead of stdout")
    parsed = parser.parse_args(args)

    during_start, during_end = _during_window(parsed.year, parsed.during)
    _, activities = load_activities(ctx.paths.activities)
    results = filter_entries(
        activities,
        after=parsed.after,
        before=parsed.before,
        during_start=during_start,
        during_end=during_end,
        tags=parsed.tags,
    )

    if parsed.format == "json":
        output = json.dumps(results, indent=2, default=str)
    else:
        buf = io.StringIO()
        writer = csv.writer(buf)
        # The CSV columns are the core fields plus one column per schema enum
        # field — so the export adapts to whatever schema is active.
        enum_cols = [
            (b.name, f.name) for b in ctx.schema.blocks for f in b.fields if f.type == "enum"
        ]
        header = ["id", "date", "title"]
        header += [f"{b}_{f}" for b, f in enum_cols]
        header += ["tags", "docs"]
        writer.writerow(header)
        for entry in results:
            row = [entry.get("id", ""), entry.get("date", ""), entry.get("title", "")]
            for block_name, field_name in enum_cols:
                row.append((entry.get(block_name) or {}).get(field_name, ""))
            row.append("; ".join(entry.get("tags", []) or []))
            row.append("; ".join(str(d) for d in entry.get("docs", []) or []))
            writer.writerow(row)
        output = buf.getvalue()

    if parsed.output:
        Path(parsed.output).write_text(output, encoding="utf-8")
        print(f"Exported {len(results)} entries to {parsed.output}")
    else:
        print(output)
    return 0


def cmd_project(ctx: Context, args: list[str]) -> int:
    """Return entries related to a project by tag and keyword. ``project <name>``"""
    if not args:
        print("Usage: librarian project <project-name> [--strict|--broad] [--brief]")
        return 1
    project = args[0].lower()
    brief = "--brief" in args
    strict = "--strict" in args
    broad = "--broad" in args

    _, activities = load_activities(ctx.paths.activities)
    # Tag match: the project name appears as a tag (case-insensitive).
    tag_hits = {
        i
        for i, e in enumerate(activities)
        if any(project == t.lower() for t in (e.get("tags", []) or []))
    }
    # Keyword match: the project name appears anywhere in the entry text.
    keyword_hits = {i for i, e in enumerate(activities) if project in yaml.dump(e).lower()}

    if broad:
        chosen, label = keyword_hits, "keyword-matched"
    else:
        chosen, label = tag_hits, "tag-matched"
    entries = [activities[i] for i in sorted(chosen)]
    print(f"Found {len(entries)} {label} entries for '{project}':\n")
    for entry in entries:
        print_entry(entry, brief=brief)

    if not strict and not broad:
        extra = sorted(keyword_hits - tag_hits)
        if extra:
            print(f"\n--- {len(extra)} additional keyword-only matches ---\n")
            for i in extra:
                print_entry(activities[i], brief=True)
    return 0


def cmd_similar(ctx: Context, args: list[str]) -> int:
    """Find entries similar to given text or an existing entry. ``similar <text>``"""
    threshold = 0.35
    compare_id = None
    query_text = None
    i = 0
    while i < len(args):
        if args[i] == "--threshold" and i + 1 < len(args):
            threshold = float(args[i + 1])
            i += 2
        elif args[i] == "--id" and i + 1 < len(args):
            compare_id = args[i + 1]
            i += 2
        else:
            query_text = " ".join(args[i:])
            break

    _, activities = load_activities(ctx.paths.activities)
    if compare_id:
        source = next((e for e in activities if e.get("id") == compare_id), None)
        if source is None:
            print(f"ERROR: entry '{compare_id}' not found")
            return 1
        query_text = f"{source.get('title', '')} {source.get('description', '')}"
    if not query_text:
        print("Usage: librarian similar <text> | --id <entry-id>")
        return 1

    results = []
    for entry in activities:
        if compare_id and entry.get("id") == compare_id:
            continue
        score = best_similarity(query_text, entry)
        if score >= threshold:
            results.append((score, entry))
    results.sort(key=lambda x: -x[0])

    if not results:
        print(f"No similar entries found (threshold: {threshold})")
        return 0
    print(f"Found {len(results)} similar entries (threshold: {threshold}):\n")
    for score, entry in results[:10]:
        print(
            f"  {int(min(1.0, score) * 100):3d}% | {entry.get('date', '?'):12s} | "
            f"{entry.get('id', '?')}"
        )
        print(f"       | {entry.get('title', '?')[:80]}\n")
    return 0


def cmd_contact(ctx: Context, args: list[str]) -> int:
    """Rolodex lookup over ``Name (email)`` patterns. ``contact <query> | --all``"""
    parser = argparse.ArgumentParser(prog="librarian contact")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--all", dest="show_all", action="store_true")
    parser.add_argument("--institution")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parsed = parser.parse_args(args)

    if not parsed.query and not parsed.show_all and not parsed.institution:
        parser.print_help()
        return 1

    _, activities = load_activities(ctx.paths.activities)
    contacts = extract_contacts(activities)
    if not contacts:
        print("(rolodex empty — no Name (email) patterns found)")
        return 0

    matches = []
    for email, info in contacts.items():
        name = canonical_name(info["names"])
        if parsed.show_all:
            matches.append((email, name, info))
        elif parsed.institution:
            if parsed.institution.lower() in email.lower():
                matches.append((email, name, info))
        elif parsed.query and (
            parsed.query.lower() in email.lower() or parsed.query.lower() in name.lower()
        ):
            matches.append((email, name, info))
    matches.sort(key=lambda m: m[1].lower())

    if not matches:
        print(f"No contacts match '{parsed.query or 'filter'}'.")
        return 0
    if parsed.format == "json":
        print(
            json.dumps(
                [
                    {
                        "name": name,
                        "email": email,
                        "name_variants": sorted(info["names"]),
                        "source_entries": info["sources"],
                    }
                    for email, name, info in matches
                ],
                indent=2,
            )
        )
        return 0
    print(f"Found {len(matches)} contact(s):\n")
    for email, name, info in matches:
        print(f"  {name}\n    {email}")
        print(f"    Sources: {', '.join(info['sources'][:3])}\n")
    return 0


def _parse_iso_utc(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Ledger timestamps are always written UTC-aware (``...Z``), but a
    ``--since`` value typed by a user is commonly a bare date or naive
    datetime (``2026-05-01``). ``datetime.fromisoformat`` returns a naive
    object for those, and comparing naive against aware raises TypeError —
    the bug that made every ``changes --since`` query crash. Normalizing
    here (assume UTC when no tzinfo is present) keeps every comparison
    aware-vs-aware. Returns ``None`` if the value cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ledger_change_index(ledger_path: Path) -> dict[str, tuple[datetime, datetime]]:
    """Map each entry id to its (first_change, last_change) ledger timestamps.

    Reads the append-only ledger once and records, per entry id, the earliest
    timestamp (effectively "created") and the latest ("last touched"). All
    operations count toward the last-touched time. Entries with no ledger line
    are simply absent from the map — callers treat that as "no recorded
    change", which is correct for the changes-since-last-pull use case (the
    bulk-imported pre-ledger corpus has no lines and should not match a
    ``--changed-since``).
    """
    index: dict[str, tuple[datetime, datetime]] = {}
    if not ledger_path.exists():
        return index
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) < 3:
            continue
        ts = _parse_iso_utc(parts[0])
        if ts is None:
            continue
        entry_id = parts[2]
        existing = index.get(entry_id)
        if existing is None:
            index[entry_id] = (ts, ts)
        else:
            first, last = existing
            index[entry_id] = (min(first, ts), max(last, ts))
    return index


def _apply_changed_window(
    activities: list[dict],
    *,
    changed_since: str | None,
    changed_until: str | None,
    ledger_path: Path,
) -> list[dict]:
    """Restrict entries to those whose last ledger change falls in a window.

    ``changed_since`` / ``changed_until`` are ISO timestamps (naive values are
    treated as UTC). Filtering is on each entry's *last* recorded change.
    Entries with no ledger record are excluded whenever either bound is set.
    Returns the list unchanged when neither bound is provided. Raises
    :class:`ValueError` if a bound cannot be parsed.
    """
    if not changed_since and not changed_until:
        return activities
    since_dt = None
    if changed_since:
        since_dt = _parse_iso_utc(changed_since)
        if since_dt is None:
            raise ValueError(f"cannot parse --changed-since '{changed_since}'")
    until_dt = None
    if changed_until:
        until_dt = _parse_iso_utc(changed_until)
        if until_dt is None:
            raise ValueError(f"cannot parse --changed-until '{changed_until}'")
        # A bare date as an *upper* bound should include the whole day, not
        # just its 00:00:00 instant — otherwise `--changed-until 2026-05-28`
        # silently drops every change made later that same day. An explicit
        # time (anything with a 'T' or space separator) is honored as given.
        if "T" not in changed_until and " " not in changed_until:
            until_dt = until_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    index = _ledger_change_index(ledger_path)
    kept = []
    for entry in activities:
        record = index.get(entry.get("id"))
        if record is None:
            continue  # no ledger history → not a recorded change
        last_change = record[1]
        if since_dt and last_change < since_dt:
            continue
        if until_dt and last_change > until_dt:
            continue
        kept.append(entry)
    return kept


def cmd_changes(ctx: Context, args: list[str]) -> int:
    """Show change-ledger entries. ``changes [--since ...] [--op ...] ...``"""
    parser = argparse.ArgumentParser(prog="librarian changes")
    parser.add_argument("--since", help="ISO timestamp; entries at or after it")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--label-pattern", help="substring filter on label")
    parser.add_argument("--op", help="filter to a specific operation")
    parser.add_argument("--id", help="filter to a specific entry id")
    parsed = parser.parse_args(args)

    ledger = ctx.paths.ledger
    if not ledger.exists():
        print("[]" if parsed.format == "json" else f"No ledger entries ({ledger})")
        return 0

    since_dt = None
    if parsed.since:
        since_dt = _parse_iso_utc(parsed.since)
        if since_dt is None:
            print(f"ERROR: cannot parse --since '{parsed.since}'")
            return 1

    entries = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=3)
        if len(parts) < 3:
            continue
        ts_str, op, entry_id = parts[0], parts[1], parts[2]
        meta_str = parts[3] if len(parts) > 3 else ""
        ts = _parse_iso_utc(ts_str)
        if ts is None:
            continue
        if since_dt and ts < since_dt:
            continue
        if parsed.op and op != parsed.op:
            continue
        if parsed.id and entry_id != parsed.id:
            continue
        meta = {}
        for token in meta_str.split():
            if "=" in token:
                k, v = token.split("=", 1)
                meta[k] = v
        if parsed.label_pattern and parsed.label_pattern not in meta.get("label", ""):
            continue
        entries.append(
            {
                "timestamp": ts_str,
                "op": op,
                "id": entry_id,
                "label": meta.get("label", "unlabeled"),
                "details": meta.get("details"),
            }
        )
    entries = entries[-parsed.limit :]

    if parsed.format == "json":
        _, activities = load_activities(ctx.paths.activities)
        by_id = {e.get("id"): e for e in activities}
        for e in entries:
            e["current"] = by_id.get(e["id"]) if e["op"] != "delete" else None
        print(json.dumps(entries, indent=2, default=str))
        return 0
    for e in entries:
        details = f"  {e['details']}" if e.get("details") else ""
        print(f"{e['timestamp']}  {e['op']:20s} {e['id']:42s}  label={e['label']}{details}")
    print(f"\n{len(entries)} entries shown" if entries else "(no matching entries)")
    return 0


# =============================================================================
# WRITE commands
# =============================================================================


def _find_field_line(lines: list[str], start: int, end: int, field: str) -> int | None:
    """Return the index of ``<field>:`` within ``[start, end)``, or None."""
    for i in range(start, end):
        if lines[i].strip().startswith(f"{field}:"):
            return i
    return None


def _find_entry_field_line(lines: list[str], start: int, end: int, field: str) -> int | None:
    """Like :func:`_find_field_line` but indent-anchored to entry fields.

    Returns the index of ``<field>:`` only when the line sits at the entry's
    top-level field indent (the ``- id:`` line's indent plus two). This avoids
    matches inside a description literal-block body (e.g. prose containing
    ``docs:`` or a block name), which would otherwise let the caller splice
    into the middle of the description or refuse a legitimate write.
    """
    expected_indent = line_indent(lines[start]) + 2
    for i in range(start, end):
        if line_indent(lines[i]) != expected_indent:
            continue
        stripped = lines[i].lstrip()
        # Recognize both `field:` and `field :` (space-before-colon). The
        # latter is tolerated by find_entry_line_range, so a hand-edited
        # entry can reach the duplicate-block guard with that form.
        if stripped.startswith(f"{field}:") or stripped.startswith(f"{field} :"):
            return i
    return None


# Entry ids must be ledger-safe slugs. The change ledger is space-delimited
# (``<ts> <op> <id> label=...``), so an id containing whitespace would be
# truncated to its first token when parsed back — breaking the ledger-derived
# ``--changed-since`` / ``--changed-until`` lookups, which key on the full id.
# Restrict ids to lowercase letters, digits and hyphens (also keeps cross-ref
# rewriting in ``rename-id`` unambiguous).
_VALID_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*[a-z0-9]")


def _is_valid_id(entry_id: str) -> bool:
    """True if ``entry_id`` is a valid slug id (lowercase, digits, hyphens)."""
    return bool(_VALID_ID_RE.fullmatch(entry_id))


_TRUE_TOKENS = frozenset({"true", "yes", "1"})
_FALSE_TOKENS = frozenset({"false", "no", "0"})


def _parse_bool(value: object) -> bool:
    """Coerce a boolean-ish value to a real ``bool``, strictly.

    Accepts an actual ``bool`` as-is, or a recognized string token
    (``true/yes/1`` or ``false/no/0``, case-insensitive). Raises
    :class:`ValueError` on anything else so a typo like ``ture`` is rejected
    rather than silently coerced to ``False``. Used by both ``create`` and
    ``update-field`` so the two entry points agree on the same field.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    raise ValueError(f"expected a boolean (true/false), got {value!r}")


def _repoint_references(
    lines: list[str],
    old_id: str,
    new_id: str,
    *,
    skip_range: tuple[int, int] | None = None,
    skip_ranges: list[tuple[int, int]] | None = None,
) -> int:
    """Rewrite every cross-reference to ``old_id`` to point at ``new_id``.

    Matches are bounded by id-character lookarounds, so a rewrite of
    ``ongoing-coi`` leaves ``ongoing-coi-training`` untouched. Catches both
    backticked refs and plain-text mentions in any text field (description,
    block notes, etc.). Mutates ``lines`` in place and returns the number of
    references rewritten.

    ``skip_range`` and ``skip_ranges`` (half-open ``(start, end)`` tuples)
    name line ranges that are left untouched. ``delete --repoint-to`` passes
    the single source range so its self-refs can't inflate the count;
    ``merge`` passes every source range together via ``skip_ranges`` so
    references that will be deleted in step 6 don't get counted as work
    done. The two arguments combine — callers may pass either, or both.
    """
    pattern = re.compile(rf"(?<![a-z0-9-]){re.escape(old_id)}(?![a-z0-9-])")
    ranges: list[tuple[int, int]] = []
    if skip_range is not None:
        ranges.append(skip_range)
    if skip_ranges:
        ranges.extend(skip_ranges)
    repointed = 0
    for i, line in enumerate(lines):
        if any(s <= i < e for s, e in ranges):
            continue
        new_line, n = pattern.subn(new_id, line)
        if n:
            repointed += n
            lines[i] = new_line
    return repointed


def _splice_block_into_entry(
    lines: list[str],
    start: int,
    end: int,
    block_name: str,
    block_data: dict,
    block_def,
    schema_block_names: list[str],
) -> int:
    """Render and splice a schema block into a target entry's line range.

    The insertion point preserves the schema-declaration order that
    ``_render_entry`` establishes at create time: walk schema blocks after
    ``block_name`` and splice just before the first one already on the entry;
    fall back to inserting before the ``docs:`` line when no later block
    exists. Mutates ``lines`` in place; returns the number of lines inserted.

    ``block_def`` may be ``None``, in which case the block is treated as a
    generic (schema-unknown) block: every ``(key, value)`` pair in
    ``block_data`` is emitted in dict order using ``_render_generic_scalar``,
    mirroring the unknown-block path in ``_render_entry``. This is the
    fall-through ``merge`` uses to carry over source blocks the schema
    doesn't declare.

    Raises :class:`ValueError` when the ``docs:`` field can't be located
    (entry malformed). Shared by :func:`cmd_set_block` and :func:`cmd_merge`
    so any future fix to block insertion lands in both commands.
    """
    # Guard the .index() lookup so an unknown (generic) block name doesn't
    # raise a misleading "'foo' is not in list" error. A generic block has
    # no later-in-schema neighbor by construction; fall through to docs:.
    insert_idx = None
    if block_name in schema_block_names:
        later_names = schema_block_names[schema_block_names.index(block_name) + 1 :]
    else:
        later_names = []
    for later_name in later_names:
        candidate = _find_entry_field_line(lines, start, end, later_name)
        if candidate is not None:
            insert_idx = candidate
            break
    if insert_idx is None:
        insert_idx = _find_entry_field_line(lines, start, end, "docs")
    if insert_idx is None:
        raise ValueError("could not locate insertion point ('docs:' field missing)")

    field_indent = line_indent(lines[insert_idx])
    sub_indent = field_indent + 2
    new_lines = [f"{' ' * field_indent}{block_name}:\n"]
    if block_def is not None:
        for fdef in block_def.fields:
            if fdef.name not in block_data:
                continue
            rendered = _render_scalar(fdef, block_data[fdef.name])
            new_lines.append(f"{' ' * sub_indent}{fdef.name}: {rendered}\n")
    else:
        for key, value in block_data.items():
            new_lines.append(f"{' ' * sub_indent}{key}: {_render_generic_scalar(value)}\n")

    lines[insert_idx:insert_idx] = new_lines
    return len(new_lines)


def _scan_list_items(
    lines: list[str], parent_idx: int, end: int
) -> tuple[list[tuple[int, str]], int | None, int]:
    """Scan the YAML list under ``parent_idx`` for its ``- item`` lines.

    Walks lines after ``parent_idx`` collecting every line that starts with
    ``- ``, skipping blank lines and ``#`` comments interleaved with items
    (a layout YAML accepts). Stops at the first structural line that isn't
    an item, blank, or comment — typically the next field or entry.

    The blank/comment skip is load-bearing: without it ``last_item_idx``
    would land on the first non-item line (causing add-* callers to insert
    new items above the existing ones) and ``items`` would miss everything
    after the gap (silently breaking duplicate detection and remove-*).

    Returns three values:

    - ``items``: list of ``(line_index, stripped_value)`` tuples in order.
    - ``first_item_idx``: line index of the first item, or ``None`` if the
      list is empty. Callers use this to read the item indent so new
      items match the existing style (flush vs. nested).
    - ``last_item_idx``: line index of the last item, or ``parent_idx`` if
      the list is empty. Callers use this as the "insert after this line"
      anchor.

    Centralizes the loop shape that was previously duplicated across
    ``cmd_add_docs``, ``cmd_add_tags`` and ``cmd_remove_tags`` — three
    near-identical copies that had to be patched in lockstep for both the
    same-indent (v1.1.1) and blank-line-gap (v1.1.2) fixes.
    """
    items: list[tuple[int, str]] = []
    for i in range(parent_idx + 1, end):
        stripped = lines[i].strip()
        if stripped.startswith("- "):
            items.append((i, stripped[2:].strip().strip("\"'")))
        elif stripped == "" or stripped.startswith("#"):
            continue
        else:
            break
    first = items[0][0] if items else None
    last = items[-1][0] if items else parent_idx
    return items, first, last


def cmd_create(ctx: Context, args: list[str]) -> int:
    """Create a new entry from JSON on stdin or ``--json``. ``create [--json ...]``

    Required fields: ``id``, ``date``, ``title``, ``description``, ``tags``.
    Optional: ``end_date``, ``docs`` and any schema block. Schema blocks present
    in the input are validated before the entry is written.

    NOTE: does NOT use ``@_activities_locked``. The expensive operations —
    JSON-stdin read and the O(N) fuzzy-similarity scan over every entry —
    happen OUTSIDE the lock; only the short uniqueness-recheck + append +
    ledger phase is locked. A concurrent writer can land between the
    pre-lock fuzzy scan and the locked append; the re-check inside the lock
    catches a same-id collision and aborts cleanly.
    """
    # Don't require --label yet; a dry-run never writes and shouldn't be
    # gated by the label requirement. We re-check after argparse below.
    label = _resolve_label(args, required=False)
    parser = argparse.ArgumentParser(prog="librarian create")
    parser.add_argument("--json", help="entry as a JSON string")
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(args)
    if not parsed.dry_run and not label:
        # Actual write — label is mandatory. Emit the same advice
        # ``_resolve_label`` would have produced.
        print(
            "ERROR: write operations require --label LABEL or the "
            "LIBRARIAN_SESSION_LABEL env var.\n"
            "       Format: <context>:<short-purpose>, e.g. 'cli:main-curator'"
        )
        return 1

    if parsed.json:
        data = json.loads(parsed.json)
    elif not sys.stdin.isatty():
        data = json.loads(sys.stdin.read())
    else:
        print("ERROR: provide the entry as --json or pipe JSON to stdin")
        return 1

    missing = [f for f in ("id", "date", "title", "description", "tags") if f not in data]
    if missing:
        print(f"ERROR: missing required fields: {missing}")
        return 1

    # Require a genuine string id: a bare-integer JSON id (e.g. 20260728)
    # would stringify cleanly here but persist as an int, and the ledger keys
    # on the stringified token — so the --changed-since lookup would then miss
    # it (the same silent-drop class as a whitespace id).
    if not isinstance(data["id"], str) or not _is_valid_id(data["id"]):
        print(f"ERROR: '{data['id']}' is not a valid id (lowercase, digits, hyphens)")
        return 1

    # Normalize docs_optional to a real boolean so a stringy "false" doesn't
    # render as truthy — keeping create in agreement with update-field.
    if "docs_optional" in data:
        try:
            data["docs_optional"] = _parse_bool(data["docs_optional"])
        except ValueError as exc:
            print(f"ERROR: docs_optional {exc}")
            return 1

    # Pre-lock pass — load entries once for fuzzy duplicate warning + block
    # validation + indent detection + YAML render. None of these mutate.
    _, activities = load_activities(ctx.paths.activities)

    # Schema-block validation uses only ctx.schema, not the loaded entries,
    # so it's safe outside the lock.
    for block in ctx.schema.blocks:
        if block.name in data:
            block_issues = validate_block(block, data[block.name] or {})
            if block_issues:
                print(f"ERROR: schema validation failed for block '{block.name}':")
                for issue in block_issues:
                    print(f"  {issue}")
                return 1

    # Pre-lock id-existence check: short-circuits the common case ("already
    # tracked") without taking the lock. The post-lock re-check is the
    # authoritative one and handles the concurrent-writer race.
    if data["id"] in {e.get("id") for e in activities}:
        print(f"ERROR: entry with id '{data['id']}' already exists")
        return 1

    # Fuzzy duplicate warning (non-blocking; informational only). Note this
    # scan operates on the PRE-LOCK snapshot, so a near-duplicate landed by
    # a concurrent writer between the snapshot and the locked append won't
    # appear in the warning. That's an acceptable trade for not holding the
    # lock through the O(N) similarity scan — the warning is advisory and
    # a hard id-collision is still caught by the post-lock re-check.
    query_text = f"{data.get('title', '')} {data.get('description', '')}"
    similar = sorted(
        ((best_similarity(query_text, e), e) for e in activities),
        key=lambda x: -x[0],
    )
    similar = [(s, e) for s, e in similar if s >= 0.4]
    if similar:
        print("WARNING: similar entries already exist:")
        for score, entry in similar[:5]:
            print(
                f"  {int(min(1.0, score) * 100)}% similar: "
                f"[{entry.get('date', '')}] {entry.get('id', '')}"
            )
        print()

    # Pre-render with a tentative indent for the dry-run preview. The
    # authoritative indent is re-detected INSIDE the lock so a peer
    # cmd_create that landed an indent-4 entry between our snapshot and
    # our locked write can't make us emit an indent-2 entry into an
    # indent-4 file (which would be invalid YAML).
    indent = _detect_entry_indent(ctx.paths)
    yaml_text = _render_entry(ctx, data, indent=indent)
    if parsed.dry_run:
        print("Dry run — would append:\n")
        print(yaml_text)
        return 0

    # Lock-protected write. Re-load activities to absorb any concurrent
    # additions between the pre-lock scan and now, then re-check id
    # uniqueness so the append never races into a duplicate, and
    # re-detect the entry indent so concurrent writes can't cause us to
    # emit a mixed-indent file.
    with write_lock(ctx.paths.activities):
        _, current = load_activities(ctx.paths.activities)
        if data["id"] in {e.get("id") for e in current}:
            print(f"ERROR: entry with id '{data['id']}' already exists")
            return 1
        ctx.paths.ensure_home()
        # Re-detect indent inside the lock and re-render the entry if the
        # detected indent diverged from the pre-lock pass.
        locked_indent = _detect_entry_indent(ctx.paths)
        if locked_indent != indent:
            yaml_text = _render_entry(ctx, data, indent=locked_indent)
        # Build the final file content atomically: existing-or-fresh
        # ``activities:`` prefix concatenated with the new entry. Routing
        # through ``atomic_replace`` closes the round-3 #4 window where a
        # non-atomic ``Path.write_text("activities:\n")`` first-create
        # was visible to unlocked readers as a 0-byte file.
        if ctx.paths.activities.exists():
            existing_content = ctx.paths.activities.read_text(encoding="utf-8")
        else:
            existing_content = "activities:\n"
        atomic_replace(ctx.paths.activities, existing_content + "\n" + yaml_text)
        append_ledger(
            ctx.paths.ledger, "create", data["id"], label, details=f"tags={len(data['tags'])}"
        )
    print(f"Created entry '{data['id']}'")
    return 0


def _detect_entry_indent(paths: LibrarianPaths) -> int:
    """Return the leading-space indent used by existing ``- id:`` entry lines.

    A file whose entries sit under ``activities:`` at a 2-space indent must
    have new entries appended at the same indent, or the YAML breaks. Defaults
    to 2 (the style of a freshly-created file) when no entry line is found.
    """
    for line in read_lines(paths.activities):
        stripped = line.lstrip()
        if stripped.startswith("- id:") or stripped.startswith("- id :"):
            return line_indent(line)
    return 2


def _render_entry(ctx: Context, data: dict, *, indent: int = 2) -> str:
    """Render an entry mapping to hand-formatted YAML text (not ``yaml.dump``).

    Core fields come first, then every schema block present on the entry,
    then docs and tags. Schema block fields are emitted in their declared
    order so the output matches the schema definition. ``indent`` is the
    leading-space indent of the entry's ``- id:`` line.
    """
    base = " " * indent  # indent of the `- id:` line
    field = base + "  "  # indent of entry fields (id, date, ...)
    sub = field + "  "  # indent of nested block fields / list items

    out: list[str] = [f"{base}- id: {data['id']}", f"{field}date: '{data['date']}'"]
    if "end_date" in data and data["end_date"] is not None:
        out.append(f"{field}end_date: '{data['end_date']}'")
    out.append(f"{field}title: {yaml_quote(data['title'])}")

    # Description as a literal block scalar so paragraph breaks survive.
    out.append(f"{field}description: |")
    for line in str(data["description"]).split("\n"):
        out.append(f"{sub}{line.rstrip()}")

    # Schema blocks, in schema order.
    for block in ctx.schema.blocks:
        if block.name not in data:
            continue
        block_data = data[block.name] or {}
        out.append(f"{field}{block.name}:")
        for fdef in block.fields:
            if fdef.name not in block_data:
                continue
            out.append(f"{sub}{fdef.name}: {_render_scalar(fdef, block_data[fdef.name])}")
    # Any block the schema does not know about is still written (generic mode
    # / mixed schemas) using a generic renderer.
    known_blocks = {b.name for b in ctx.schema.blocks}
    core_keys = {"id", "date", "end_date", "title", "description", "tags", "docs", "docs_optional"}
    for key, value in data.items():
        if key in core_keys or key in known_blocks:
            continue
        if isinstance(value, dict):
            out.append(f"{field}{key}:")
            for sub_key, sub_val in value.items():
                out.append(f"{sub}{sub_key}: {_render_generic_scalar(sub_val)}")

    docs = data.get("docs", []) or []
    if docs:
        out.append(f"{field}docs:")
        for doc in docs:
            out.append(f"{sub}- {yaml_quote(str(doc))}")
    else:
        out.append(f"{field}docs: []")

    # Persist docs_optional iff the caller supplied it (true suppresses the
    # NO DOCS warning; false is written explicitly so create and update-field
    # agree on representation). Entries that never mention it stay clean.
    if "docs_optional" in data:
        out.append(f"{field}docs_optional: {'true' if data['docs_optional'] else 'false'}")

    out.append(f"{field}tags:")
    for tag in data.get("tags", []) or []:
        out.append(f"{sub}- {tag}")
    return "\n".join(out) + "\n"


def _render_scalar(field, value) -> str:
    """Render a typed schema-field value for YAML output."""
    if value is None:
        return "null"
    if field.type == "int":
        return str(value)
    if field.type == "bool":
        # Case-insensitive on string input so "TRUE"/"YES"/"True" don't get
        # silently flipped to false (validate_block already accepts these).
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "yes", "1"):
                return "true"
            if low in ("false", "no", "0"):
                return "false"
            raise ValueError(f"'{value}' is not a boolean for field '{field.name}'")
        raise ValueError(f"'{value!r}' is not a boolean for field '{field.name}'")
    return yaml_quote(str(value))


def _render_generic_scalar(value) -> str:
    """Render an unknown-block field value for YAML output.

    Scalars (None / bool / int / str) are rendered directly. Lists and dicts
    are serialized with ``yaml.dump`` so the structure round-trips faithfully
    instead of being stringified into a Python ``repr`` that loads back as a
    text scalar.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, dict)):
        return yaml.dump(value, default_flow_style=True).rstrip("\n")
    return yaml_quote(str(value))


@_activities_locked
def cmd_delete(ctx: Context, args: list[str]) -> int:
    """Delete an entry by id.

    ``delete <entry-id> [--repoint-to <target-id>] [--confirm]`` (dry-run default).

    With ``--repoint-to <target-id>``, every backticked or plain-text reference
    to ``<entry-id>`` across other entries is rewritten to point at
    ``<target-id>`` before the source entry is removed, so a delete no longer
    leaves dangling cross-references behind. Without it, behavior is unchanged
    (references are left as-is for the caller to fix).
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    # Pre-screen -h alongside other args, matching set-block's safety pattern.
    # Safe to compare argv tokens directly here: _is_valid_id forbids entry ids
    # that start with `-`, so a user can never legitimately name an entry "-h".
    if ("-h" in args or "--help" in args) and len(args) > 1:
        print("ERROR: -h/--help must be used alone (no other arguments)")
        return 2
    # Bare `delete` (no entry id) is a user error worth distinguishing from
    # argparse's generic "missing argument" exit code 2.
    if not args:
        print(
            "ERROR: delete requires an entry id (usage: librarian delete <entry-id> [--repoint-to <id>] [--confirm])"
        )
        return 1
    parser = argparse.ArgumentParser(prog="librarian delete")
    parser.add_argument("entry_id")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument(
        "--repoint-to",
        dest="repoint_to",
        help="rewrite inbound references to this id before deleting",
    )
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    entry_id = parsed.entry_id
    repoint_to = parsed.repoint_to

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1

    # Validate --repoint-to up front so a dry-run with a bad target still errors.
    if repoint_to is not None:
        if repoint_to == entry_id:
            print(f"ERROR: --repoint-to target '{repoint_to}' is the entry being deleted")
            return 1
        target_start, _ = find_entry_line_range(lines, repoint_to)
        if target_start is None:
            print(f"ERROR: --repoint-to target '{repoint_to}' not found")
            return 1

    # Count inbound references (outside the source entry's own lines), so the
    # dry-run preview matches what the ledger will record on confirm.
    repoint_count = 0
    if repoint_to is not None:
        pattern = re.compile(rf"(?<![a-z0-9-]){re.escape(entry_id)}(?![a-z0-9-])")
        for i, line in enumerate(lines):
            if start <= i < end:
                continue
            repoint_count += len(pattern.findall(line))

    print(f"Entry '{entry_id}' spans lines {start + 1}-{end} ({end - start} lines).")
    if repoint_to is not None:
        print(f"Would repoint {repoint_count} inbound reference(s) to '{repoint_to}'.")
    if not parsed.confirm:
        print("\nDry run — pass --confirm to actually delete.")
        return 0

    # Repoint inbound references first (skipping the soon-to-be-deleted source
    # range so a self-ref in source's own description can't inflate the count),
    # then remove the source entry's lines. Capture the helper's return value
    # rather than reusing the dry-run pre-count, so the ledger entry can never
    # drift from what the helper actually rewrote on disk — merge (PR 3) will
    # widen this surface, and the pre-count loop wouldn't see those edits.
    if repoint_to is not None:
        repoint_count = _repoint_references(lines, entry_id, repoint_to, skip_range=(start, end))
    del lines[start:end]
    write_lines(ctx.paths.activities, lines)
    details = f"lines={end - start}"
    if repoint_to is not None:
        details += f" repoint-to={repoint_to} refs={repoint_count}"
    append_ledger(ctx.paths.ledger, "delete", entry_id, label, details=details)
    if repoint_to is not None:
        print(
            f"Deleted entry '{entry_id}' ({end - start} lines removed); "
            f"{repoint_count} reference(s) repointed to '{repoint_to}'"
        )
    else:
        print(f"Deleted entry '{entry_id}' ({end - start} lines removed)")
    return 0


@_activities_locked
def cmd_update_field(ctx: Context, args: list[str]) -> int:
    """Update a top-level field. ``update-field <id> <field> <value>``

    Supported fields: ``title``, ``date``, ``end_date``, ``docs_optional``.
    ``docs_optional`` is a boolean (``true``/``false``) that suppresses the
    NO DOCS validation warning for an entry that legitimately has no artifact.
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 3:
        print("Usage: librarian update-field <id> <title|date|end_date|docs_optional> <value>")
        return 1
    entry_id, field, value = args[0], args[1], " ".join(args[2:])
    if field not in ("title", "date", "end_date", "docs_optional"):
        print(f"ERROR: field '{field}' not supported (use title, date, end_date, docs_optional)")
        return 1

    # docs_optional is rendered as an unquoted YAML boolean; every other
    # supported field is a quoted scalar.
    if field == "docs_optional":
        try:
            rendered = "true" if _parse_bool(value) else "false"
        except ValueError as exc:
            print(f"ERROR: docs_optional {exc}")
            return 1
    else:
        rendered = yaml_quote(value)

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1

    idx = _find_field_line(lines, start, end, field)
    if idx is not None:
        indent = " " * line_indent(lines[idx])
        # Drop any continuation lines (a multi-line scalar value).
        field_indent = line_indent(lines[idx])
        cont = idx + 1
        while cont < end:
            if not lines[cont].strip():
                cont += 1
                continue
            if line_indent(lines[cont]) > field_indent:
                cont += 1
            else:
                break
        lines[idx:cont] = [f"{indent}{field}: {rendered}\n"]
        write_lines(ctx.paths.activities, lines)
        append_ledger(ctx.paths.ledger, "update-field", entry_id, label, f"{field}={value[:80]}")
        print(f"Updated {field} on '{entry_id}' to: {rendered}")
        return 0

    # end_date / docs_optional can be added after the date line if absent.
    if field in ("end_date", "docs_optional"):
        date_idx = _find_field_line(lines, start, end, "date")
        if date_idx is not None:
            indent = " " * line_indent(lines[date_idx])
            lines.insert(date_idx + 1, f"{indent}{field}: {rendered}\n")
            write_lines(ctx.paths.activities, lines)
            append_ledger(
                ctx.paths.ledger, "update-field", entry_id, label, f"{field}={value[:80]} (added)"
            )
            print(f"Added {field} on '{entry_id}' to: {rendered}")
            return 0
    print(f"ERROR: field '{field}' not found in entry '{entry_id}'")
    return 1


@_activities_locked
def cmd_update_description(ctx: Context, args: list[str]) -> int:
    """Replace an entry's description (read from stdin). ``update-description <id>``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if not args:
        print("Usage: echo 'new description' | librarian update-description <id>")
        return 1
    entry_id = args[0]
    if sys.stdin.isatty():
        print("ERROR: pipe the new description to stdin")
        return 1
    new_desc = sys.stdin.read().strip()
    if not new_desc:
        print("ERROR: empty description")
        return 1

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1
    desc_start = _find_field_line(lines, start, end, "description")
    if desc_start is None:
        print(f"ERROR: no description field in entry '{entry_id}'")
        return 1

    # The description block runs until the next line at the same indent or less.
    desc_indent = line_indent(lines[desc_start])
    desc_end = end
    for i in range(desc_start + 1, end):
        if lines[i].strip() and line_indent(lines[i]) <= desc_indent:
            desc_end = i
            break

    indent = " " * desc_indent
    new_lines = [f"{indent}description: |\n"]
    for line in new_desc.split("\n"):
        new_lines.append(f"{indent}  {line.rstrip()}\n")
    lines[desc_start:desc_end] = new_lines
    write_lines(ctx.paths.activities, lines)
    append_ledger(ctx.paths.ledger, "update-description", entry_id, label, f"chars={len(new_desc)}")
    print(f"Updated description on '{entry_id}' ({len(new_desc)} chars)")
    return 0


def _scalar_end(lines: list[str], idx: int, key: str, limit: int) -> int:
    """Return the line index just past a (possibly multi-line) scalar value.

    Handles quoted scalars that continue across lines by tracking quote state,
    so a low-indent continuation line is not mistaken for a sibling field.
    """
    value_part = lines[idx].split(f"{key}:", 1)[1].lstrip()
    quote = value_part[0] if value_part[:1] in ("'", '"') else None

    def closes(text: str, qchar: str) -> bool:
        pos = 0
        while pos < len(text):
            ch = text[pos]
            if qchar == '"' and ch == "\\":
                pos += 2
                continue
            if ch == qchar:
                if qchar == "'" and text[pos + 1 : pos + 2] == "'":
                    pos += 2
                    continue
                return True
            pos += 1
        return False

    if quote and not closes(value_part[1:], quote):
        for j in range(idx + 1, limit):
            if closes(lines[j], quote):
                return j + 1
        return limit
    if not quote:
        key_indent = line_indent(lines[idx])
        for j in range(idx + 1, limit):
            if lines[j].strip() and line_indent(lines[j]) <= key_indent:
                return j
        return limit
    return idx + 1


@_activities_locked
def cmd_update_notes(ctx: Context, args: list[str]) -> int:
    """Update a block's notes/text field. ``update-notes <id> [--block B] [--field F]``

    Defaults to the first block declared by the schema and its ``notes`` field.
    The new text is read from stdin.
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    block_name = None
    field_name = "notes"
    if "--block" in args:
        i = args.index("--block")
        block_name = args[i + 1]
        del args[i : i + 2]
    if "--field" in args:
        i = args.index("--field")
        field_name = args[i + 1]
        del args[i : i + 2]
    # Backwards-compatible alias from the original tool.
    if "--section" in args:
        i = args.index("--section")
        block_name = args[i + 1]
        del args[i : i + 2]
    if not args:
        print("Usage: echo 'notes' | librarian update-notes <id> [--block B] [--field F]")
        return 1
    entry_id = args[0]
    if block_name is None:
        if ctx.schema.is_empty:
            print("ERROR: no schema configured; pass --block to name the block")
            return 1
        block_name = ctx.schema.blocks[0].name
    if sys.stdin.isatty():
        print("ERROR: pipe the new notes text to stdin")
        return 1
    new_notes = sys.stdin.read().strip()

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1

    in_block = False
    for i in range(start, end):
        stripped = lines[i].strip()
        if stripped.startswith(f"{block_name}:"):
            in_block = True
        elif in_block and stripped.startswith(f"{field_name}:"):
            indent = " " * line_indent(lines[i])
            field_end = _scalar_end(lines, i, field_name, end)
            lines[i:field_end] = [f"{indent}{field_name}: {yaml_quote(new_notes)}\n"]
            write_lines(ctx.paths.activities, lines)
            append_ledger(
                ctx.paths.ledger,
                f"update-notes/{block_name}",
                entry_id,
                label,
                f"chars={len(new_notes)}",
            )
            print(f"Updated {block_name}.{field_name} on '{entry_id}'")
            return 0
    print(f"ERROR: could not find {block_name}.{field_name} in entry '{entry_id}'")
    return 1


@_activities_locked
def cmd_update_nested_field(ctx: Context, args: list[str]) -> int:
    """Update a single schema-block field. ``update-nested-field <id> BLOCK.FIELD VALUE``

    The value is validated against the active schema — enum membership,
    dependent enums and field types are all checked before the write.
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 3:
        print("Usage: librarian update-nested-field <id> <block.field> <value>")
        return 1
    entry_id, path, raw_value = args[0], args[1], " ".join(args[2:])
    if "." not in path:
        print("ERROR: path must be BLOCK.FIELD")
        return 1
    block_name, field_name = path.split(".", 1)

    block_def = ctx.schema.block(block_name)
    field_def = block_def.field(field_name) if block_def else None
    if ctx.schema.is_empty or block_def is None:
        print(f"ERROR: block '{block_name}' is not declared by the active schema")
        return 1
    if field_def is None:
        print(f"ERROR: field '{field_name}' is not declared on block '{block_name}'")
        return 1

    # Coerce the CLI string to the field's native type.
    try:
        coerced = coerce_value(field_def, raw_value)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    _, activities = load_activities(ctx.paths.activities)
    entry = next((e for e in activities if e.get("id") == entry_id), None)
    if entry is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1

    # Validate the new value in the context of the entry's current block data,
    # so dependent enums resolve against the live parent value. Filter by the
    # precise `BLOCK.FIELD:` label rather than a bare substring so issues
    # about unrelated sibling fields don't leak into this update's error path
    # (a substring like "CATEGORY" would otherwise match "PTR.SUBCATEGORY:").
    block_data = dict(entry.get(block_name) or {})
    block_data[field_name] = coerced
    label_prefix = f"{block_name.upper()}.{field_name.upper()}:"
    issues = [issue for issue in validate_block(block_def, block_data) if label_prefix in issue]
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 1

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    # Locate the block, then the field within it.
    block_idx = None
    block_indent = None
    block_end = end
    for i in range(start, end):
        stripped = lines[i].strip()
        if block_idx is None:
            if stripped.startswith(f"{block_name}:"):
                block_idx = i
                block_indent = line_indent(lines[i])
        else:
            if stripped and line_indent(lines[i]) <= block_indent:
                block_end = i
                break
    if block_idx is None:
        print(
            f"ERROR: block '{block_name}' not present on entry '{entry_id}'. "
            f"Recreate the entry to add a new block."
        )
        return 1

    child_indent = block_indent + 2
    rendered = _render_scalar(field_def, coerced)
    new_line = f"{' ' * child_indent}{field_name}: {rendered}\n"

    field_idx = None
    for i in range(block_idx + 1, block_end):
        if line_indent(lines[i]) == child_indent and lines[i].strip().startswith(f"{field_name}:"):
            field_idx = i
            break
    if field_idx is not None:
        field_end = _scalar_end(lines, field_idx, field_name, block_end)
        lines[field_idx:field_end] = [new_line]
        action = "updated"
    else:
        lines.insert(block_end, new_line)
        action = "inserted"
    write_lines(ctx.paths.activities, lines)
    append_ledger(
        ctx.paths.ledger,
        "update-nested-field",
        entry_id,
        label,
        f"{path}={raw_value[:80]}({action})",
    )
    print(f"{action.capitalize()} {path}={raw_value} on '{entry_id}'")
    return 0


@_activities_locked
def cmd_set_block(ctx: Context, args: list[str]) -> int:
    """Add a schema block to an existing entry.

    ``set-block <id> <block> [--json <json>]`` (also accepts JSON on stdin).
    The block must be declared by the active schema and must NOT already be
    present on the entry (use ``update-nested-field`` to edit existing
    fields). The supplied JSON object is validated as a complete block
    against the schema before any write: unknown fields are rejected, and
    required fields must be present.
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1

    # Reject `-h`/`--help` mixed with positional/`--json` args. argparse
    # short-circuits on help anywhere in argv and exits 0, which would
    # otherwise turn `set-block <id> <block> -h ...` into a silent no-op
    # that returns success without performing the write.
    if ("-h" in args or "--help" in args) and len(args) > 1:
        print("ERROR: -h/--help must be used alone (no other arguments)")
        return 2

    # Accept JSON via --json or stdin (matching cmd_create) so a CLI user can
    # supply whitespace-sensitive content without losing it to argv joining.
    parser = argparse.ArgumentParser(prog="librarian set-block")
    parser.add_argument("entry_id")
    parser.add_argument("block")
    parser.add_argument("--json", help="block fields as a JSON string")
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
        # argparse exits 0 for --help and 2 for a usage error; preserve that
        # so `set-block -h` (used alone) returns 0 instead of looking like
        # a failure.
        return int(exc.code) if isinstance(exc.code, int) else 1
    entry_id, block_name = parsed.entry_id, parsed.block

    if parsed.json is not None:
        raw_json = parsed.json
    elif not sys.stdin.isatty():
        raw_json = sys.stdin.read()
    else:
        print("ERROR: provide block fields with --json '<json>' or pipe JSON on stdin")
        return 1

    # Distinguish "no schema at all" from "schema present but missing this block"
    # so the error guides the right fix.
    if ctx.schema.is_empty:
        print("ERROR: no schema configured; set-block requires an active schema")
        return 1
    block_def = ctx.schema.block(block_name)
    if block_def is None:
        print(f"ERROR: block '{block_name}' is not declared by the active schema")
        return 1

    try:
        block_data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        print(f"ERROR: block fields are not valid JSON: {exc}")
        return 1
    if not isinstance(block_data, dict):
        print("ERROR: block fields must be a JSON object")
        return 1
    if not block_data:
        print(f"ERROR: block payload is empty; supply at least one field for '{block_name}'")
        return 1

    # Reject unknown fields so typos surface immediately, before validation.
    known_fields = {f.name for f in block_def.fields}
    unknown = sorted(k for k in block_data if k not in known_fields)
    if unknown:
        print(f"ERROR: unknown field(s) for block '{block_name}': {unknown}")
        return 1

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1

    # Existence check BEFORE value-level validation (newline / coerce /
    # validate_block) so a user who hits set-block on an entry that already
    # has the block gets the actionable error rather than a value report
    # that turns out to be moot. Structural-input errors (bad JSON, unknown
    # fields, empty payload) still fire first, since those have to be sorted
    # out before existence even matters.
    if _find_entry_field_line(lines, start, end, block_name) is not None:
        print(
            f"ERROR: block '{block_name}' already present on entry '{entry_id}'. "
            f"Use update-nested-field to edit its fields."
        )
        return 1

    # Reject any string value containing a newline, regardless of its declared
    # schema type. yaml_quote emits a single-quoted scalar that can't carry a
    # raw LF, so the splice would corrupt the file on the next read. A
    # type-agnostic guard (any str) closes the entire bug class, including
    # date / date? values whose ISO regex happily anchors before a trailing LF.
    for fname, val in block_data.items():
        if isinstance(val, str) and ("\n" in val or "\r" in val):
            print(
                f"ERROR: field '{fname}' contains a newline; "
                f"multi-line values are not supported by set-block "
                f"(write the entry's description instead)"
            )
            return 1

    # Coerce stringy primitives (int, bool, date) to their native types before
    # validation, so e.g. {"credits": "08"} becomes int 8 and round-trips as a
    # number rather than persisting as the string "08". Errors here use the
    # same INVALID BLOCK.FIELD shape that validate_block uses, for consistency.
    for fdef in block_def.fields:
        raw = block_data.get(fdef.name)
        if isinstance(raw, str) and fdef.type in ("int", "bool", "date", "date?"):
            try:
                block_data[fdef.name] = coerce_value(fdef, raw)
            except ValueError as exc:
                print(f"ERROR: INVALID {block_name.upper()}.{fdef.name.upper()}: {exc}")
                return 1

    # Validate the entire block atomically (catches missing required fields,
    # bad enum values, dependent-enum mismatches, etc.).
    issues = validate_block(block_def, block_data)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 1

    # Splice the rendered block into the entry via the shared helper (also
    # used by merge). Schema-declaration order is preserved for both callers.
    try:
        _splice_block_into_entry(
            lines,
            start,
            end,
            block_name,
            block_data,
            block_def,
            [b.name for b in ctx.schema.blocks],
        )
    except ValueError as exc:
        print(f"ERROR: {exc} on '{entry_id}'")
        return 1
    write_lines(ctx.paths.activities, lines)
    append_ledger(
        ctx.paths.ledger,
        "set-block",
        entry_id,
        label,
        f"block={block_name} fields={len(block_data)}",
    )
    print(f"Added block '{block_name}' to '{entry_id}' with {len(block_data)} field(s)")
    return 0


def _tags_line(lines: list[str], start: int, end: int) -> int | None:
    """Return the index of the ``tags:`` line within an entry."""
    return _find_field_line(lines, start, end, "tags")


@_activities_locked
def cmd_add_tags(ctx: Context, args: list[str]) -> int:
    """Add tags to an entry. ``add-tags <id> <tag> [tag ...]`` (idempotent)."""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 2:
        print("Usage: librarian add-tags <id> <tag> [tag ...]")
        return 1
    entry_id, new_tags = args[0], args[1:]

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1
    tags_idx = _tags_line(lines, start, end)
    if tags_idx is None:
        print(f"ERROR: could not find tags line for entry '{entry_id}'")
        return 1

    content = lines[tags_idx].split("tags:", 1)[1].strip()
    indent = " " * line_indent(lines[tags_idx])
    if content.startswith("[") and content != "[]":
        # Inline list: parse, append, rewrite.
        existing = [t.strip().strip("\"'") for t in content.strip("[] ").split(",") if t.strip()]
        added = [t for t in new_tags if t not in existing]
        if not added:
            print(f"No new tags to add — all already present on '{entry_id}'")
            return 0
        existing += added
        lines[tags_idx] = f"{indent}tags: [{', '.join(repr(t) for t in existing)}]\n"
    else:
        # Multi-line list (or empty `[]`): collect existing item lines.
        items, first_item_idx, last_item_idx = _scan_list_items(lines, tags_idx, end)
        existing = [v for _, v in items]
        added = [t for t in new_tags if t not in existing]
        if not added:
            print(f"No new tags to add — all already present on '{entry_id}'")
            return 0
        if content == "[]":
            lines[tags_idx] = f"{indent}tags:\n"
        # YAML accepts list items at the same indent as the parent key OR two
        # deeper. Match whichever style the existing items use; if there are
        # none, default to the deeper (nested) style.
        if first_item_idx is not None:
            item_indent = " " * line_indent(lines[first_item_idx])
        else:
            item_indent = indent + "  "
        insert_at = last_item_idx + 1
        for offset, tag in enumerate(added):
            lines.insert(insert_at + offset, f"{item_indent}- {tag}\n")
    write_lines(ctx.paths.activities, lines)
    append_ledger(ctx.paths.ledger, "add-tags", entry_id, label, f"+{','.join(added)}")
    print(f"Added tags {added} to '{entry_id}'")
    return 0


@_activities_locked
def cmd_remove_tags(ctx: Context, args: list[str]) -> int:
    """Remove tags from an entry. ``remove-tags <id> <tag> [tag ...]``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 2:
        print("Usage: librarian remove-tags <id> <tag> [tag ...]")
        return 1
    entry_id, drop = args[0], args[1:]

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1
    tags_idx = _tags_line(lines, start, end)
    if tags_idx is None:
        print(f"ERROR: could not find tags line for entry '{entry_id}'")
        return 1

    content = lines[tags_idx].split("tags:", 1)[1].strip()
    indent = " " * line_indent(lines[tags_idx])
    if content.startswith("[") and content != "[]":
        existing = [t.strip().strip("\"'") for t in content.strip("[] ").split(",") if t.strip()]
        removed = [t for t in drop if t in existing]
        if not removed:
            print(f"Tags {drop} not found on '{entry_id}'")
            return 0
        remaining = [t for t in existing if t not in drop]
        lines[tags_idx] = f"{indent}tags: [{', '.join(repr(t) for t in remaining)}]\n"
    else:
        items, _, _ = _scan_list_items(lines, tags_idx, end)
        # Build the deletion plan in one pass: keeping `to_delete` (line
        # indices) and `removed` (tag values) in sync via two separate list
        # comprehensions over `items` would invite drift if the match
        # predicate ever grew.
        matches = [(idx, tag) for idx, tag in items if tag in drop]
        to_delete = [idx for idx, _ in matches]
        removed = [tag for _, tag in matches]
        if not removed:
            print(f"Tags {drop} not found on '{entry_id}'")
            return 0
        for i in sorted(to_delete, reverse=True):
            del lines[i]
    write_lines(ctx.paths.activities, lines)
    append_ledger(ctx.paths.ledger, "remove-tags", entry_id, label, f"-{','.join(removed)}")
    print(f"Removed tags {removed} from '{entry_id}'")
    return 0


@_activities_locked
def cmd_add_docs(ctx: Context, args: list[str]) -> int:
    """Add doc references (URLs or ``file:<id>``) to an entry. ``add-docs <id> <doc> ...``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 2:
        print("Usage: librarian add-docs <id> <doc> [doc ...]")
        return 1
    entry_id, new_docs = args[0], args[1:]

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1
    docs_idx = _find_field_line(lines, start, end, "docs")
    if docs_idx is None:
        print(f"ERROR: could not find docs field in entry '{entry_id}'")
        return 1

    content = lines[docs_idx].split("docs:", 1)[1].strip()
    indent = " " * line_indent(lines[docs_idx])
    if content == "[]":
        new_lines = [f"{indent}docs:\n"]
        for doc in new_docs:
            new_lines.append(f'{indent}  - "{doc}"\n')
        lines[docs_idx : docs_idx + 1] = new_lines
        added = list(new_docs)
    else:
        items, first_item_idx, last_item_idx = _scan_list_items(lines, docs_idx, end)
        existing = [v for _, v in items]
        # YAML accepts list items at the same indent as the parent key OR two
        # deeper. Match whichever style the existing items use; if there are
        # none, default to the deeper (nested) style.
        if first_item_idx is not None:
            item_indent = " " * line_indent(lines[first_item_idx])
        else:
            item_indent = indent + "  "
        added = [d for d in new_docs if d not in existing]
        for offset, doc in enumerate(added):
            lines.insert(last_item_idx + 1 + offset, f'{item_indent}- "{doc}"\n')
        if not added:
            print(f"No new docs to add — all already present on '{entry_id}'")
            return 0
    write_lines(ctx.paths.activities, lines)
    append_ledger(ctx.paths.ledger, "add-docs", entry_id, label, f"count={len(added)}")
    print(f"Added {len(added)} doc(s) to '{entry_id}'")
    return 0


@_activities_locked
def cmd_remove_docs(ctx: Context, args: list[str]) -> int:
    """Remove a doc reference from an entry. ``remove-docs <id> <doc>``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 2:
        print("Usage: librarian remove-docs <id> <doc>")
        return 1
    entry_id, doc = args[0], args[1]

    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1
    for i in range(start, end):
        stripped = lines[i].strip()
        if stripped.startswith("- ") and doc in stripped:
            del lines[i]
            write_lines(ctx.paths.activities, lines)
            append_ledger(ctx.paths.ledger, "remove-docs", entry_id, label, f"doc={doc[:80]}")
            print(f"Removed doc from '{entry_id}'")
            return 0
    print(f"Doc '{doc[:50]}' not found in entry '{entry_id}'")
    return 1


@_activities_locked
def cmd_rename_id(ctx: Context, args: list[str]) -> int:
    """Rename an entry id and repoint cross-references. ``rename-id <old> <new>``

    Repoints every cross-reference to the old id -- backticked or plain-text --
    in descriptions and notes. Matches are bounded by id-character lookarounds,
    so an id is only rewritten when it appears as a whole token (a rename of
    ``ongoing-coi`` leaves ``ongoing-coi-training`` untouched).
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 2:
        print("Usage: librarian rename-id <old-id> <new-id>")
        return 1
    old_id, new_id = args[0], args[1]
    if old_id == new_id:
        print("ERROR: old-id and new-id are identical")
        return 1
    if not _is_valid_id(new_id):
        print(f"ERROR: '{new_id}' is not a valid id (lowercase, digits, hyphens)")
        return 1

    _, activities = load_activities(ctx.paths.activities)
    ids = {e.get("id") for e in activities}
    if old_id not in ids:
        print(f"ERROR: entry '{old_id}' not found")
        return 1
    if new_id in ids:
        print(f"ERROR: id '{new_id}' already exists")
        return 1

    lines = read_lines(ctx.paths.activities)
    start, _ = find_entry_line_range(lines, old_id)
    indent = " " * line_indent(lines[start])
    lines[start] = f"{indent}- id: {new_id}\n"

    # Repoint every cross-reference via the shared helper (also used by
    # delete --repoint-to). The id-character lookarounds keep longer
    # id-shaped strings that happen to start with old_id untouched.
    repointed = _repoint_references(lines, old_id, new_id)
    write_lines(ctx.paths.activities, lines)
    append_ledger(ctx.paths.ledger, "rename-id", new_id, label, f"from={old_id} refs={repointed}")
    print(f"Renamed '{old_id}' -> '{new_id}' ({repointed} cross-reference(s) repointed)")
    return 0


@_activities_locked
def cmd_merge(ctx: Context, args: list[str]) -> int:
    """Merge source entries into a target, atomically.

    ``merge <source-id> [<source-id> ...] --into <target-id>
    [--confirm] [--on-block-conflict abort|keep-target|keep-source]
    [--no-provenance]``

    Tags and docs are unioned onto the target (target's order first, then new
    items in source order, de-duplicated). Schema blocks the target lacks are
    carried over from sources; same-block conflicts respect
    ``--on-block-conflict`` (default ``abort``). Every backticked or
    plain-text reference to each source id is repointed to the target before
    the source entries are deleted, so the merge does not leave dangling
    cross-references. The target's description is kept as-is; source
    descriptions are printed for the caller to fold in manually with
    ``update-description`` (description merging is editorial, not mechanical).

    Atomic: the read, plan, validate, and write all happen in memory before
    a single ``write_lines`` call. Any failure (validation, conflict abort,
    missing field) returns without touching the file.
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if ("-h" in args or "--help" in args) and len(args) > 1:
        print("ERROR: -h/--help must be used alone (no other arguments)")
        return 2

    parser = argparse.ArgumentParser(prog="librarian merge")
    parser.add_argument("source_ids", nargs="+", help="entry id(s) to merge")
    parser.add_argument("--into", required=True, dest="target_id", help="target entry id")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview only (the default until --confirm is passed); overrides --confirm",
    )
    parser.add_argument(
        "--on-block-conflict",
        choices=["abort", "keep-target", "keep-source"],
        default="abort",
        dest="on_conflict",
    )
    parser.add_argument(
        "--no-provenance",
        action="store_true",
        help="omit the plain-text 'Consolidates former entries: ...' note",
    )
    parser.add_argument(
        "--append-sources",
        action="store_true",
        help=(
            "append each source's description under a `## From <id>` header "
            "in the target's literal-block description (opt-in; off by default "
            "since description merging is editorial)"
        ),
    )
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1

    # --dry-run wins over --confirm so the README's "dry-run is the default
    # until --confirm" wording holds and an explicit --dry-run is honored.
    if parsed.dry_run:
        parsed.confirm = False

    target_id = parsed.target_id
    on_conflict = parsed.on_conflict
    with_provenance = not parsed.no_provenance
    append_sources = parsed.append_sources

    # De-dup sources; skip self-merges silently.
    source_ids: list[str] = []
    seen_sources: set[str] = set()
    for sid in parsed.source_ids:
        if sid == target_id or sid in seen_sources:
            continue
        source_ids.append(sid)
        seen_sources.add(sid)
    if not source_ids:
        print(
            f"ERROR: no sources to merge (target '{target_id}' was the only "
            f"id supplied, or all sources were duplicates)"
        )
        return 1

    # Load activities (parsed) to compute the plan from data.
    _, activities = load_activities(ctx.paths.activities)
    by_id = {e.get("id"): e for e in activities}

    if target_id not in by_id:
        print(f"ERROR: target entry '{target_id}' not found")
        return 1
    missing = [sid for sid in source_ids if sid not in by_id]
    if missing:
        print(f"ERROR: source entries not found: {missing}")
        return 1

    target = by_id[target_id]
    sources = [by_id[sid] for sid in source_ids]

    # Tags union: target's order first, then new tags in source-order.
    seen_tags = set(target.get("tags") or [])
    new_tags: list[str] = []
    for s in sources:
        for tag in s.get("tags") or []:
            if tag not in seen_tags:
                new_tags.append(tag)
                seen_tags.add(tag)

    # Docs union: same shape.
    seen_docs = set(target.get("docs") or [])
    new_docs: list[str] = []
    for s in sources:
        for doc in s.get("docs") or []:
            if doc not in seen_docs:
                new_docs.append(doc)
                seen_docs.add(doc)

    # Block plan. Source blocks classify as carry-over (target lacks it),
    # conflict (both have it), or duplicate-source (a later source also has
    # the same block another source already supplied — first wins, the rest
    # are surfaced in the plan so the user can see what got dropped).
    # Both schema-declared blocks and generic / non-schema blocks are
    # considered: a source block the schema doesn't declare still represents
    # real data the user provided, and silently dropping it would be the
    # same data-loss bug as missing a schema block.
    schema_known_names = {b.name for b in ctx.schema.blocks}
    _core_keys_for_merge = {
        "id",
        "date",
        "end_date",
        "title",
        "description",
        "tags",
        "docs",
        "docs_optional",
    }

    def _source_block_names(entry: dict) -> list[str]:
        """Return the entry's block keys (schema-declared first, then generic),
        skipping core fields and non-mapping values."""
        out = [b.name for b in ctx.schema.blocks if b.name in entry]
        for key, value in entry.items():
            if (
                key not in _core_keys_for_merge
                and key not in schema_known_names
                and isinstance(value, dict)
            ):
                out.append(key)
        return out

    # Refuse to merge a source carrying top-level non-core, non-block keys
    # whose values aren't mappings (list, scalar). The merge has no safe way
    # to fold those into the target (no schema definition, no shape rule),
    # and silently deleting them with the source is the same data-loss class
    # that round-1 #2 set out to eliminate. The user is told exactly which
    # keys are blocking so they can edit the source first.
    non_carryable: list[tuple[str, str]] = []
    for s in sources:
        sid = s.get("id")
        for key, value in s.items():
            if key in _core_keys_for_merge or key in schema_known_names:
                continue
            if isinstance(value, dict):
                continue
            non_carryable.append((sid, key))
    if non_carryable:
        print("ERROR: source(s) have top-level non-block fields that merge cannot safely carry:")
        for sid, key in non_carryable:
            print(f"  '{sid}': '{key}'")
        print(
            "Fold the values into target's description (or convert each key "
            "to a schema-declared block) before merging."
        )
        return 1

    target_block_names = set(_source_block_names(target))
    blocks_to_carry: dict[str, tuple[str, dict]] = {}
    conflicts: list[tuple[str, str]] = []
    dropped_first_wins: list[tuple[str, str]] = []
    for s in sources:
        sid = s.get("id")
        for bn in _source_block_names(s):
            if bn in target_block_names:
                conflicts.append((bn, sid))
                continue
            if bn in blocks_to_carry:
                dropped_first_wins.append((bn, sid))
                continue
            blocks_to_carry[bn] = (sid, s[bn])

    # Apply on-block-conflict policy.
    dropped_for_keep_target: list[tuple[str, str]] = []
    replaced_target_blocks: list[tuple[str, str]] = []
    if conflicts:
        if on_conflict == "abort":
            print("ERROR: source(s) carry block(s) that already exist on the target:")
            for bn, sid in conflicts:
                print(f"  block '{bn}' from source '{sid}' conflicts with target's '{bn}'")
            print(
                "Pass --on-block-conflict keep-target (drop source's block) "
                "or keep-source (replace target's block) to resolve."
            )
            return 1
        if on_conflict == "keep-target":
            dropped_for_keep_target = list(conflicts)
        else:  # keep-source: first conflicting source wins
            seen_replace: set[str] = set()
            for bn, sid in conflicts:
                if bn not in seen_replace:
                    blocks_to_carry[bn] = (sid, by_id[sid][bn])
                    replaced_target_blocks.append((bn, sid))
                    seen_replace.add(bn)
                else:
                    dropped_first_wins.append((bn, sid))

    # Validate every carried-over SCHEMA-DECLARED block. Generic blocks have no
    # schema definition, so they're carried over by structure without typed
    # validation (matching _render_entry's generic-block path).
    for bn, (sid, block_data) in blocks_to_carry.items():
        block_def = ctx.schema.block(bn)
        if block_def is None:
            continue
        issues = validate_block(block_def, block_data)
        if issues:
            print(f"ERROR: block '{bn}' carried from source '{sid}' fails schema validation:")
            for issue in issues:
                print(f"  {issue}")
            return 1

    # Read lines so we can compute repoint counts for the preview. Guard
    # against any source id that load_activities saw but find_entry_line_range
    # can't locate (file changed between load and read, malformed range, etc.)
    # so the count loop doesn't crash with TypeError on (None, None) unpack.
    lines = read_lines(ctx.paths.activities)
    source_ranges: dict[str, tuple[int, int]] = {}
    for sid in source_ids:
        s_start, s_end = find_entry_line_range(lines, sid)
        if s_start is None:
            print(
                f"ERROR: source '{sid}' present in loaded data but not locatable "
                f"in the activities file (concurrent edit?); aborting without write"
            )
            return 1
        source_ranges[sid] = (s_start, s_end)
    all_source_ranges = list(source_ranges.values())

    # Target's own range is also excluded from step 1's repoint. Plain-text
    # prose mentions of source ids inside the target's description / notes
    # (e.g. "originally tracked under source-a") would otherwise be silently
    # rewritten to self-references that the dangling-ref scanner skips
    # (scan_dangling_refs treats self-refs as not-dangling), making the
    # rewrite invisible after the fact. Backticked mentions inside the same
    # range ARE rewritten by the focused pass at step 1b; carried block
    # content is rewritten in step 2's per-block pre-splice walk.
    t_pre_start, t_pre_end = find_entry_line_range(lines, target_id)
    if t_pre_start is not None:
        repoint_skip_ranges = all_source_ranges + [(t_pre_start, t_pre_end)]
    else:
        repoint_skip_ranges = all_source_ranges

    # Pre-count inbound refs per source for step 1 (cross-file repoint),
    # excluding source ranges + target's range so this matches what
    # ``_repoint_references`` will actually do.
    repoint_counts: dict[str, int] = {}
    for sid in source_ids:
        pattern = re.compile(rf"(?<![a-z0-9-]){re.escape(sid)}(?![a-z0-9-])")
        n = 0
        for i, line in enumerate(lines):
            if any(s <= i < e for s, e in repoint_skip_ranges):
                continue
            n += len(pattern.findall(line))
        repoint_counts[sid] = n

    # Also account for the rewrites that happen INSIDE the merge's own
    # steps so the preview total matches the ledger total. PR #16's review
    # lesson: preview must equal what the commit actually records. Steps
    # counted here:
    #   1b. Backticked source-id mentions inside the target's range that
    #       get rewritten to `target_id` after step 1 skipped that range.
    #   2.  ``_rewrite_ids`` traversal over every carried block's string
    #       values, rewriting plain-text source-id mentions inside the
    #       block data to ``target_id``.
    #   5.  When ``--append-sources`` is on, every backticked source-id
    #       mention (including self) in each source's description gets
    #       rewritten to ``target_id`` before splicing.
    preview_extra = 0
    if t_pre_start is not None:
        for sid in source_ids:
            bt_pat = re.compile(rf"`{re.escape(sid)}`")
            for i in range(t_pre_start, t_pre_end):
                preview_extra += len(bt_pat.findall(lines[i]))

    def _count_ids_in_value(value) -> int:
        """Recursively count plain-text source-id mentions in a block value
        (the same word-boundary pattern ``_rewrite_ids`` uses at exec
        time)."""
        if isinstance(value, str):
            total = 0
            for sid in source_ids:
                wp = re.compile(rf"(?<![a-z0-9-]){re.escape(sid)}(?![a-z0-9-])")
                total += len(wp.findall(value))
            return total
        if isinstance(value, dict):
            return sum(_count_ids_in_value(v) for v in value.values())
        if isinstance(value, list):
            return sum(_count_ids_in_value(v) for v in value)
        return 0

    for _bn, (_sid, block_data) in blocks_to_carry.items():
        preview_extra += _count_ids_in_value(block_data)

    if append_sources:
        for s in sources:
            body = (s.get("description") or "").strip()
            if not body:
                continue
            for sid in source_ids:
                bt_pat = re.compile(rf"`{re.escape(sid)}`")
                preview_extra += len(bt_pat.findall(body))

    preview_total = sum(repoint_counts.values()) + preview_extra

    # Print the plan.
    print(f"Merge plan: {len(source_ids)} source(s) -> '{target_id}'")
    print(f"  Sources: {', '.join(source_ids)}")
    print(f"  Tags to add: {len(new_tags)}" + (f" ({new_tags})" if new_tags else ""))
    print(f"  Docs to add: {len(new_docs)}")
    if blocks_to_carry:
        print(
            "  Blocks to carry over: "
            + ", ".join(f"{bn} from {sid}" for bn, (sid, _) in blocks_to_carry.items())
        )
    if replaced_target_blocks:
        print(
            "  Target blocks replaced (--on-block-conflict keep-source): "
            + ", ".join(f"{bn} <- {sid}" for bn, sid in replaced_target_blocks)
        )
    if dropped_for_keep_target:
        print(
            "  Source blocks dropped (--on-block-conflict keep-target): "
            + ", ".join(f"{bn} from {sid}" for bn, sid in dropped_for_keep_target)
        )
    if dropped_first_wins:
        print(
            "  Duplicate-source blocks dropped (first source wins): "
            + ", ".join(f"{bn} from {sid}" for bn, sid in dropped_first_wins)
        )
    print("  Inbound references to repoint (step 1, cross-file):")
    for sid in source_ids:
        print(f"    {sid}: {repoint_counts[sid]}")
    if preview_extra:
        print(f"  Additional internal rewrites (steps 1b/2/5): {preview_extra}")
    print(f"  Total references to repoint (matches ledger): {preview_total}")

    # Source-description preview. When --append-sources is on the merge will
    # fold these in automatically; otherwise the user picks what to carry by
    # hand via update-description.
    if append_sources:
        print("\nSource descriptions (will be appended automatically by --append-sources):")
    else:
        print("\nSource descriptions (fold what you want into the target via update-description):")
    print("---")
    for s in sources:
        sid = s.get("id")
        print(f"## From {sid}")
        print((s.get("description") or "").strip())
        print()
    print("---")

    if not parsed.confirm:
        print("\nDry run — pass --confirm to actually merge.")
        return 0

    # --- Execute (all mutations on `lines` in memory; one write at the end) ---

    schema_block_names = [b.name for b in ctx.schema.blocks]

    # 1. Repoint references for each source. Excluding ALL source ranges (not
    #    just the source being processed) from each repoint avoids counting
    #    work whose effect is undone at step 6 when those ranges are deleted.
    #    Capture each return value so the ledger refs= number can never drift
    #    from what the helper actually wrote — PR #16's review lesson.
    actual_repoint_total = 0
    for sid in source_ids:
        actual_repoint_total += _repoint_references(
            lines, sid, target_id, skip_ranges=repoint_skip_ranges
        )

    # 1b. The target's own range was skipped so PLAIN-text prose mentions of
    #     source ids in target's description (e.g. "Originally tracked under
    #     source-a") stay as the user wrote them. But BACKTICKED mentions
    #     inside the target are deliberate live cross-references and would
    #     dangle once the source is deleted; rewrite just those.
    if t_pre_start is not None:
        t_lo, t_hi = find_entry_line_range(lines, target_id)
        if t_lo is not None:
            for sid in source_ids:
                backtick_pattern = re.compile(rf"`{re.escape(sid)}`")
                replacement = f"`{target_id}`"
                for i in range(t_lo, t_hi):
                    new_line, n = backtick_pattern.subn(replacement, lines[i])
                    if n:
                        lines[i] = new_line
                        actual_repoint_total += n

    # 2. Carry blocks onto the target. For keep-source we may have to delete
    #    the target's existing block first; re-locate target's range before
    #    each splice because splices shift line indices below the target.
    #    _splice_block_into_entry's block_def=None path renders generic
    #    (schema-unknown) blocks, mirroring _render_entry's generic-block
    #    path; that's how a source block the schema doesn't declare is still
    #    carried over rather than silently dropped.
    #
    #    Before each splice, rewrite source-id mentions inside the carried
    #    block's string values to target_id — those mentions came from inside
    #    a source range step 1 skipped, so the line-level repoint won't catch
    #    them. Doing this on the parsed data (not the spliced lines) keeps
    #    the target's own description prose untouched.
    id_patterns = {
        sid: re.compile(rf"(?<![a-z0-9-]){re.escape(sid)}(?![a-z0-9-])") for sid in source_ids
    }

    def _rewrite_ids(value):
        """Walk a block's value tree, rewriting source-id mentions in strings.
        Returns (new_value, count_of_substitutions)."""
        if isinstance(value, str):
            count = 0
            for pattern in id_patterns.values():
                value, n = pattern.subn(target_id, value)
                count += n
            return value, count
        if isinstance(value, dict):
            count = 0
            new = {}
            for k, v in value.items():
                new[k], n = _rewrite_ids(v)
                count += n
            return new, count
        if isinstance(value, list):
            count = 0
            new = []
            for v in value:
                rv, n = _rewrite_ids(v)
                new.append(rv)
                count += n
            return new, count
        return value, 0

    for bn, (_sid, block_data) in blocks_to_carry.items():
        # _rewrite_ids returns a fresh tree; no mutation of the dict during
        # iteration. `block_data` is the only downstream reader so we don't
        # need to write the rewritten value back into blocks_to_carry.
        block_data, n = _rewrite_ids(block_data)
        actual_repoint_total += n
        t_start, t_end = find_entry_line_range(lines, target_id)
        existing_idx = _find_entry_field_line(lines, t_start, t_end, bn)
        if existing_idx is not None:
            block_indent = line_indent(lines[existing_idx])
            block_end = t_end
            # The terminator must be an actual entry-field line (or the next
            # entry's `- id:` line), not a flush-left comment inside the block.
            # Comments and blanks are skipped; otherwise a hand-edited block
            # with a stray `# comment` inside would cause keep-source to
            # delete only its prefix and leave stale fields behind.
            for i in range(existing_idx + 1, t_end):
                stripped = lines[i].strip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent_here = line_indent(lines[i])
                if indent_here > block_indent:
                    continue  # still inside the block (child line)
                # At indent <= block_indent: either the next field at entry-field
                # indent (== block_indent), or the next entry's `- id:` line.
                block_end = i
                break
            del lines[existing_idx:block_end]
            t_start, t_end = find_entry_line_range(lines, target_id)
        block_def = ctx.schema.block(bn)  # may be None (generic block)
        try:
            _splice_block_into_entry(
                lines, t_start, t_end, bn, block_data, block_def, schema_block_names
            )
        except ValueError as exc:
            print(f"ERROR: {exc} on '{target_id}'")
            return 1

    # (No separate "step 2b" loop is needed any more: the pre-splice rewrite
    # at step 2's top scoped the carried-block-content fix to the block's own
    # data, leaving the target's existing description prose untouched.)

    # 3. Union tags into target's tags list. Handle inline + multi-line, the
    #    same shapes cmd_add_tags supports, so a hand-edited target survives.
    if new_tags:
        t_start, t_end = find_entry_line_range(lines, target_id)
        tags_idx = _find_entry_field_line(lines, t_start, t_end, "tags")
        if tags_idx is None:
            print(f"ERROR: tags: field missing on '{target_id}'")
            return 1
        content = lines[tags_idx].split("tags:", 1)[1].strip()
        indent = " " * line_indent(lines[tags_idx])
        if content.startswith("[") and content != "[]":
            # Parse with yaml.safe_load so embedded commas (rare for tags but
            # the same code path serves docs below where URLs commonly carry
            # commas) and mixed quote styles round-trip safely; render with
            # yaml_quote which produces valid YAML escapes (vs. Python repr,
            # which emits backslash escapes that single-quoted YAML rejects).
            try:
                parsed_inline = yaml.safe_load(content) or []
            except yaml.YAMLError:
                parsed_inline = []
            existing_inline = [str(t) for t in parsed_inline] + list(new_tags)
            lines[tags_idx] = (
                f"{indent}tags: [" + ", ".join(yaml_quote(t) for t in existing_inline) + "]\n"
            )
        else:
            items, first_item_idx, last_item_idx = _scan_list_items(lines, tags_idx, t_end)
            if content == "[]":
                lines[tags_idx] = f"{indent}tags:\n"
            item_indent = (
                " " * line_indent(lines[first_item_idx])
                if first_item_idx is not None
                else indent + "  "
            )
            for offset, tag in enumerate(new_tags):
                lines.insert(last_item_idx + 1 + offset, f"{item_indent}- {tag}\n")

    # 4. Union docs into target's docs list. Handle inline non-empty + empty
    #    `[]` + multi-line — the same three shapes cmd_add_tags supports —
    #    so an inline `docs: ["x"]` target doesn't get the new items spliced
    #    underneath as block-style children, which would produce invalid YAML.
    if new_docs:
        t_start, t_end = find_entry_line_range(lines, target_id)
        docs_idx = _find_entry_field_line(lines, t_start, t_end, "docs")
        if docs_idx is None:
            print(f"ERROR: docs: field missing on '{target_id}'")
            return 1
        content = lines[docs_idx].split("docs:", 1)[1].strip()
        indent = " " * line_indent(lines[docs_idx])
        if content.startswith("[") and content != "[]":
            try:
                parsed_inline = yaml.safe_load(content) or []
            except yaml.YAMLError:
                parsed_inline = []
            existing_inline = [str(d) for d in parsed_inline] + list(new_docs)
            lines[docs_idx] = (
                f"{indent}docs: [" + ", ".join(yaml_quote(d) for d in existing_inline) + "]\n"
            )
        elif content == "[]":
            lines[docs_idx] = f"{indent}docs:\n"
            item_indent = indent + "  "
            for offset, doc in enumerate(new_docs):
                lines.insert(docs_idx + 1 + offset, f"{item_indent}- {yaml_quote(doc)}\n")
        else:
            items, first_item_idx, last_item_idx = _scan_list_items(lines, docs_idx, t_end)
            item_indent = (
                " " * line_indent(lines[first_item_idx])
                if first_item_idx is not None
                else indent + "  "
            )
            for offset, doc in enumerate(new_docs):
                lines.insert(last_item_idx + 1 + offset, f"{item_indent}- {yaml_quote(doc)}\n")

    # 5. Description-body additions: the provenance one-liner (default on)
    #    and the optional `--append-sources` full-source fold. Plain text only
    #    — backticked source ids would trip the validate dangling-ref scanner
    #    once the source entries are deleted. The target's description MUST
    #    be a literal-block scalar (`description: |`); refuse on an inline or
    #    empty scalar because YAML would silently fold an appended line into
    #    the value rather than treating it as a body line.
    if with_provenance or append_sources:
        # Name only the description-body operations that are actually active
        # so the error advice ("re-run with --no-provenance") doesn't tell a
        # user who already passed --no-provenance to pass it again.
        active = []
        if with_provenance:
            active.append("provenance note")
        if append_sources:
            active.append("--append-sources content")
        active_label = " + ".join(active)
        opt_outs = []
        if with_provenance:
            opt_outs.append("--no-provenance")
        if append_sources:
            opt_outs.append("drop --append-sources")
        # Join with `and` (not `or`) when BOTH opt-outs are needed: dropping
        # either alone still leaves the other active, which keeps the same
        # shape check failing. The user has to drop every active body-write
        # to skip it.
        opt_out_advice = " and ".join(opt_outs) if len(opt_outs) > 1 else opt_outs[0]

        t_start, t_end = find_entry_line_range(lines, target_id)
        desc_idx = _find_entry_field_line(lines, t_start, t_end, "description")
        if desc_idx is None:
            print(
                f"ERROR: target '{target_id}' has no description field; cannot "
                f"add {active_label} (re-run with {opt_out_advice} to skip)"
            )
            return 1
        desc_content = lines[desc_idx].split("description:", 1)[1].strip()
        if not desc_content:
            print(
                f"ERROR: target '{target_id}' has an empty description field; cannot "
                f"add {active_label} (re-run with {opt_out_advice} to skip)"
            )
            return 1
        if not (desc_content.startswith("|") or desc_content.startswith(">")):
            print(
                f"ERROR: target '{target_id}' has an inline description scalar; "
                f"convert it to a `description: |` literal block (or re-run with "
                f"{opt_out_advice}) before merging"
            )
            return 1
        # Folded-scalar (`>`) gates `--append-sources` specifically. YAML's
        # folded form collapses single newlines into spaces, so every appended
        # `## From <sid>` header, body line, and blank separator would fuse
        # into one paragraph. The provenance one-liner alone is bounded (it's
        # one line, folded becomes a space) so `>` + only-provenance is OK;
        # `>` + append-sources is not.
        if append_sources and desc_content.startswith(">"):
            print(
                f"ERROR: target '{target_id}' uses a folded-scalar description "
                f"(`description: >`); --append-sources requires a literal-block "
                f"scalar (`description: |`) because YAML folds newlines into "
                f"spaces and would mangle the appended source bodies. "
                f"Convert the target to `|` first, or drop --append-sources."
            )
            return 1
        desc_indent = line_indent(lines[desc_idx])
        # Locate the body's end AND derive body_indent from the first non-blank
        # body line — explicit indent indicators (`|4`) or hand-edits may put
        # body at deeper than desc_indent + 2, and hard-coding +2 would either
        # shallow-terminate the literal block (turning the note into an unknown
        # top-level mapping key) or fold it into the value silently.
        body_end = t_end
        body_indent: int | None = None
        for i in range(desc_idx + 1, t_end):
            stripped = lines[i].strip()
            line_idx_indent = line_indent(lines[i])
            if stripped and line_idx_indent <= desc_indent:
                body_end = i
                break
            if stripped and body_indent is None:
                body_indent = line_idx_indent
        if body_indent is None:
            body_indent = desc_indent + 2

        new_body_lines: list[str] = []
        if append_sources:
            # `## From <source-id>` header — plain text so backticks don't
            # trip the dangling-ref scanner after the source is deleted.
            # Each source body has any BACKTICKED reference to ANY source
            # (including the source's own self-mentions) rewritten to
            # target_id before splicing, otherwise source A's
            # `` "see `B-id` for context" `` would survive verbatim into the
            # target after B is deleted in step 6. Plain-text mentions are
            # deliberately preserved — see the inner block at the rewrite
            # call site for why. Use splitlines()
            # (not split("\n")) so a CRLF-authored description doesn't strand
            # `\r` characters in the YAML literal block.
            for s in sources:
                sid = s.get("id")
                body = (s.get("description") or "").strip()
                if not body:
                    # Empty source body: emit a marker so a reader doesn't see
                    # a bare `## From <sid>` header followed by a blank line
                    # and wonder whether content went missing.
                    new_body_lines.append(f"{' ' * body_indent}## From {sid} (no description)\n")
                    new_body_lines.append(f"{' ' * body_indent}\n")
                    continue
                # Rewrite ALL backticked source-id mentions in the body to
                # target_id, including the source's own self-backticks. Once
                # this body lives inside the target's description, every
                # backticked source id is a reference to an entry that step 6
                # is about to delete — leaving any of them in place produces
                # a fresh dangling ref the moment the merge commits.
                #
                # Use a backtick-anchored pattern (NOT id_patterns, which is
                # a word-boundary regex appropriate for block data values).
                # Word-boundary on prose would silently rewrite plain-text
                # mentions like "Originally tracked under <sid> before
                # consolidation" — fabricating a historical claim the user
                # didn't write.
                for other_sid in source_ids:
                    body, n = re.subn(rf"`{re.escape(other_sid)}`", f"`{target_id}`", body)
                    actual_repoint_total += n
                new_body_lines.append(f"{' ' * body_indent}## From {sid}\n")
                for line in body.splitlines():
                    new_body_lines.append(f"{' ' * body_indent}{line}\n")
                new_body_lines.append(f"{' ' * body_indent}\n")
        if with_provenance:
            new_body_lines.append(
                f"{' ' * body_indent}Consolidates former entries: {', '.join(source_ids)}.\n"
            )
        lines[body_end:body_end] = new_body_lines

    # 6. Delete source entries in descending order so earlier source ranges
    #    stay valid as later ones are removed.
    final_source_ranges: list[tuple[int, int]] = []
    for sid in source_ids:
        s_start, s_end = find_entry_line_range(lines, sid)
        if s_start is not None:
            final_source_ranges.append((s_start, s_end))
    for s_start, s_end in sorted(final_source_ranges, key=lambda r: r[0], reverse=True):
        del lines[s_start:s_end]

    # 7. Single write + single ledger entry. The details string distinguishes
    #    carried / replaced / dropped block decisions so the audit trail
    #    records what actually happened, not just what was carried.
    write_lines(ctx.paths.activities, lines)
    block_decisions: list[str] = []
    carried_only = [
        bn
        for bn in blocks_to_carry.keys()
        if (bn, blocks_to_carry[bn][0]) not in replaced_target_blocks
    ]
    if carried_only:
        block_decisions.append("carried:" + ",".join(carried_only))
    if replaced_target_blocks:
        block_decisions.append("replaced:" + ",".join(bn for bn, _ in replaced_target_blocks))
    if dropped_for_keep_target:
        block_decisions.append(
            "dropped-keep-target:" + ",".join(bn for bn, _ in dropped_for_keep_target)
        )
    if dropped_first_wins:
        block_decisions.append("dropped-first-wins:" + ",".join(bn for bn, _ in dropped_first_wins))
    blocks_repr = "|".join(block_decisions) if block_decisions else "none"
    details = (
        f"sources={','.join(source_ids)} "
        f"blocks={blocks_repr} "
        f"tags={len(new_tags)} docs={len(new_docs)} "
        f"refs={actual_repoint_total}"
    )
    append_ledger(ctx.paths.ledger, "merge", target_id, label, details=details)
    print(
        f"Merged {len(source_ids)} source(s) into '{target_id}' "
        f"({actual_repoint_total} reference(s) repointed; "
        f"{len(new_tags)} tag(s) + {len(new_docs)} doc(s) added)"
    )
    return 0


# =============================================================================
# File-inventory commands
# =============================================================================


def _print_resolved_file_refs(ctx: Context, entry: dict) -> None:
    """Print the resolved path for any ``file:<id>`` doc references on an entry."""
    refs = [d for d in (entry.get("docs") or []) if isinstance(d, str) and d.startswith("file:")]
    if not refs:
        return
    by_id = {r.get("id"): r for r in load_files(ctx.paths.files)}
    print("Resolved file references:")
    for ref in refs:
        record = by_id.get(ref[len("file:") :])
        if record is None:
            print(f"  {ref}  -> (DANGLING — not in inventory)")
        else:
            print(f"  {ref}  -> {record.get('path')}  [{record.get('category')}]")
    print("---")


@_files_locked
def cmd_file_add(ctx: Context, args: list[str]) -> int:
    """Register a file in the inventory. ``file-add <path> --category C --title T``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    parser = argparse.ArgumentParser(prog="librarian file-add")
    parser.add_argument("path")
    parser.add_argument("--category", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--id", dest="file_id")
    parsed = parser.parse_args(args)

    rel = rel_to_root(parsed.path, ctx.paths.root)
    abs_path = ctx.paths.root / rel
    if not abs_path.exists():
        print(f"ERROR: file does not exist: {abs_path}")
        return 1

    records = load_files(ctx.paths.files)
    existing_ids = {r.get("id") for r in records}
    for r in records:
        if r.get("path") == rel:
            print(f"ERROR: path already registered as id '{r.get('id')}': {rel}")
            return 1
    if parsed.file_id:
        if parsed.file_id in existing_ids:
            print(f"ERROR: file id '{parsed.file_id}' already exists")
            return 1
        file_id = parsed.file_id
    else:
        file_id = unique_file_id(slugify_filename(rel), existing_ids)

    # Exact-content dedup warning (non-blocking).
    digest = sha256_of(abs_path)
    same_hash = [r.get("id") for r in records if r.get("sha256") == digest]
    if same_hash:
        print(f"WARNING: identical content already registered as: {', '.join(same_hash)}")
    # Fuzzy near-duplicate warning (non-blocking).
    new_text = f"{parsed.title} {parsed.description}".strip()
    fuzzy = []
    for r in records:
        existing_text = f"{r.get('title', '')} {r.get('description', '') or ''}"
        score = max(
            similarity_score(parsed.title, r.get("title", "")) * 1.2,
            similarity_score(new_text, existing_text),
        )
        if score >= 0.6:
            fuzzy.append((score, r))
    if fuzzy:
        fuzzy.sort(key=lambda x: -x[0])
        print("WARNING: similar file(s) already in the inventory:")
        for score, r in fuzzy[:5]:
            print(f"  {int(score * 100):3d}%  {r.get('id')}  ({r.get('title')})")

    record = {"id": file_id, "path": rel, "category": parsed.category, "title": parsed.title}
    if parsed.description:
        record["description"] = parsed.description
    record["sha256"] = digest
    record["added"] = today_iso()
    records.append(record)
    save_files(ctx.paths.files, records)
    append_ledger(ctx.paths.ledger, "file-add", file_id, label, f"path={rel}")
    print(f"Registered file '{file_id}'  [{parsed.category}]  {rel}")
    return 0


def cmd_file_list(ctx: Context, args: list[str]) -> int:
    """List inventory files, or run a coverage report. ``file-list [--orphans]``"""
    parser = argparse.ArgumentParser(prog="librarian file-list")
    parser.add_argument("--category")
    parser.add_argument("--orphans", action="store_true")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parsed = parser.parse_args(args)

    records = load_files(ctx.paths.files)
    if parsed.orphans:
        _, activities = load_activities(ctx.paths.activities)
        referenced = set()
        for e in activities:
            for d in e.get("docs", []) or []:
                if isinstance(d, str) and d.startswith("file:"):
                    referenced.add(d[len("file:") :])
        ids = {r.get("id") for r in records}
        report = {
            "unreferenced": sorted(ids - referenced),
            "dangling_refs": sorted(referenced - ids),
            "missing_from_disk": sorted(
                r["id"] for r in records if not (ctx.paths.root / r.get("path", "")).exists()
            ),
        }
        if parsed.format == "json":
            print(json.dumps(report, indent=2))
            return 0
        print(f"Inventory coverage ({len(records)} files registered):\n")
        for header, items in report.items():
            print(f"  {header} ({len(items)}):")
            for item in items:
                print(f"    {item}")
        return 0

    if parsed.category:
        records = [r for r in records if r.get("category") == parsed.category]
    if parsed.format == "json":
        print(json.dumps(records, indent=2, default=str))
        return 0
    print(f"Files: {len(records)}\n")
    current = None
    for r in sorted(records, key=lambda x: (x.get("category", ""), x.get("id", ""))):
        if r.get("category") != current:
            current = r.get("category")
            print(f"[{current}]")
        print(f"  {r.get('id', '?'):42s}  {r.get('path', '?')}")
    return 0


def cmd_file_get(ctx: Context, args: list[str]) -> int:
    """Show one inventory file and the entries that reference it. ``file-get <id>``"""
    if not args:
        print("Usage: librarian file-get <id>")
        return 1
    file_id = args[0]
    record = next((r for r in load_files(ctx.paths.files) if r.get("id") == file_id), None)
    if record is None:
        print(f"ERROR: file id '{file_id}' not found")
        return 1
    print(yaml.dump(record, default_flow_style=False, allow_unicode=True, width=120))
    on_disk = (ctx.paths.root / record.get("path", "")).exists()
    print(f"on disk: {'YES' if on_disk else 'NO — MISSING'}")
    _, activities = load_activities(ctx.paths.activities)
    refs = [e.get("id") for e in activities if f"file:{file_id}" in (e.get("docs", []) or [])]
    if refs:
        print(f"referenced by {len(refs)} entr{'y' if len(refs) == 1 else 'ies'}:")
        for r in refs:
            print(f"  {r}")
    else:
        print("referenced by: (none)")
    return 0


@_files_locked
def cmd_file_move(ctx: Context, args: list[str]) -> int:
    """Move a registered file on disk and update its inventory path. ``file-move <id> <path>``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 2:
        print("Usage: librarian file-move <id> <new-path>")
        return 1
    file_id, new_path = args[0], rel_to_root(args[1], ctx.paths.root)
    records = load_files(ctx.paths.files)
    record = next((r for r in records if r.get("id") == file_id), None)
    if record is None:
        print(f"ERROR: file id '{file_id}' not found")
        return 1
    old_abs = ctx.paths.root / record.get("path", "")
    new_abs = ctx.paths.root / new_path
    if not old_abs.exists():
        print(f"ERROR: source file missing on disk: {old_abs}")
        return 1
    if new_abs.exists():
        print(f"ERROR: destination already exists: {new_abs}")
        return 1
    if any(r.get("path") == new_path for r in records):
        print(f"ERROR: path already registered: {new_path}")
        return 1
    new_abs.parent.mkdir(parents=True, exist_ok=True)
    old_abs.rename(new_abs)
    old_path = record["path"]
    record["path"] = new_path
    save_files(ctx.paths.files, records)
    append_ledger(ctx.paths.ledger, "file-move", file_id, label, f"to={new_path}")
    print(f"Moved '{file_id}':\n  {old_path}\n  -> {new_path}")
    return 0


@_files_locked
def cmd_file_update(ctx: Context, args: list[str]) -> int:
    """Update a file's category/title/description. ``file-update <id> [--title ...]``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    parser = argparse.ArgumentParser(prog="librarian file-update")
    parser.add_argument("file_id")
    parser.add_argument("--category")
    parser.add_argument("--title")
    parser.add_argument("--description")
    parsed = parser.parse_args(args)
    if not parsed.category and not parsed.title and parsed.description is None:
        print("Nothing to update — pass --category, --title and/or --description")
        return 1
    records = load_files(ctx.paths.files)
    record = next((r for r in records if r.get("id") == parsed.file_id), None)
    if record is None:
        print(f"ERROR: file id '{parsed.file_id}' not found")
        return 1
    changed = []
    if parsed.category:
        record["category"] = parsed.category
        changed.append("category")
    if parsed.title:
        record["title"] = parsed.title
        changed.append("title")
    if parsed.description is not None:
        record["description"] = parsed.description
        changed.append("description")
    save_files(ctx.paths.files, records)
    append_ledger(
        ctx.paths.ledger, "file-update", parsed.file_id, label, f"fields={','.join(changed)}"
    )
    print(f"Updated {', '.join(changed)} on '{parsed.file_id}'")
    return 0


def cmd_file_rehash(ctx: Context, args: list[str]) -> int:
    """Recompute sha256 for one file or the whole inventory. ``file-rehash <id>|--all``

    NOTE: this command intentionally does NOT use ``@_files_locked``. SHA-256
    on a large registered file (videos, archives, datasets) can take seconds
    to minutes, and holding the cross-process flock through the whole loop
    would serialize every concurrent ``file-*`` writer behind it. Instead the
    expensive hashing runs OUTSIDE the lock, and the lock is taken only for
    the short load/apply-deltas/save phase. If a concurrent writer adds or
    moves a record while hashing is in progress, this run prints a notice
    so the user knows to rerun rather than silently skipping.
    """
    # Help / usage short-circuit. Without the decorator wrapper, the
    # ``_is_pure_read_invocation`` skip never runs for this command, so a
    # ``file-rehash --help`` invocation would otherwise fall through to
    # the id-lookup path and report "file id '--help' not found". Scan
    # the whole argv so ``file-rehash <id> --help`` is also caught (not
    # just the help-first case).
    if any(a in ("-h", "--help") for a in args):
        print("Usage: librarian file-rehash <id> | --all")
        print("  Recompute sha256 for one registered file or the whole inventory.")
        return 0
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if not args:
        print("Usage: librarian file-rehash <id> | --all")
        return 1

    # Phase 1 — snapshot inventory and resolve targets (no lock; pure read).
    snapshot = load_files(ctx.paths.files)
    if args[0] == "--all":
        targets, scope = snapshot, "--all"
    else:
        scope = args[0]
        targets = [r for r in snapshot if r.get("id") == scope]
        if not targets:
            print(f"ERROR: file id '{scope}' not found")
            return 1

    # Phase 2 — compute hashes OUTSIDE the lock. This is the expensive part.
    # We store (path_snapshot, sha) per id so phase 3 can verify the record's
    # path didn't change underneath us via a concurrent ``file-move`` — if
    # it did, we'd be writing a digest for path P_old onto a record whose
    # path is now P_new, silently corrupting the inventory.
    hash_results: dict[str, tuple[str, str]] = {}  # id -> (rel_path, sha256)
    missing: list[str] = []
    for record in targets:
        rid = record.get("id")
        if rid is None:
            # Malformed record with no ``id:`` — skip entirely. Pre-PR the
            # tight per-record loop wrote sha back directly so this could
            # corrupt one id-less record; the snapshot pattern would corrupt
            # all of them if we keyed on ``None``.
            continue
        rel_path = record.get("path", "")
        abs_path = ctx.paths.root / rel_path
        if not abs_path.exists():
            missing.append(rid)
            continue
        try:
            sha = sha256_of(abs_path)
        except (FileNotFoundError, OSError) as exc:
            # TOCTOU: the file existed at the ``.exists()`` check above but
            # was removed (or made unreadable) before/during the streaming
            # hash. Pre-PR the wide files-lock made the window milliseconds;
            # the lock-scope shrink widens it to potentially minutes for a
            # large file, so this race is now reachable. Treat as missing
            # rather than letting the exception bubble up and abandon every
            # hash already computed for the other records.
            missing.append(rid)
            print(
                f"WARNING: {rid}: file vanished or became unreadable during "
                f"hashing ({exc.__class__.__name__}); skipping."
            )
            continue
        hash_results[rid] = (rel_path, sha)

    # Phase 3 — apply deltas under the lock. Re-load to absorb any concurrent
    # writes that landed during phase 2, then patch in our hashes by id ONLY
    # when the record's path still matches what phase 2 hashed.
    skipped_path_drift: list[str] = []
    added_during_hash: list[str] = []  # records present in phase 3 but not phase 2
    # ``missing`` captures rids whose path was unreachable during phase 2.
    # Phase 3 must NOT also flag those rids as "added during hashing" — they
    # were known at phase 2, just not hashable. Set-membership skip below.
    missing_set = set(missing)
    with write_lock(ctx.paths.files):
        records = load_files(ctx.paths.files)
        rehashed = 0
        post_lock_ids = set()
        for record in records:
            rid = record.get("id")
            if rid is None:
                continue
            post_lock_ids.add(rid)
            snapshot = hash_results.get(rid)
            if snapshot is None:
                # Record present at phase 3 but no entry in hash_results.
                # Genuinely-new (concurrent file-add) is what we want to
                # surface to --all. Exclude rids that phase 2 already routed
                # into ``missing`` (path didn't exist on disk) so the user
                # doesn't see both "Skipped missing" AND "added during
                # hashing" for the same id — they're the same record.
                if scope == "--all" and rid not in missing_set:
                    added_during_hash.append(rid)
                continue
            snap_path, new_sha = snapshot
            if record.get("path", "") != snap_path:
                # Concurrent ``file-move`` rewrote this id's path between
                # phases. Skip rather than overwrite the wrong digest.
                skipped_path_drift.append(rid)
                continue
            record["sha256"] = new_sha
            rehashed += 1
        # Detect single-id-rehash where the record vanished between phases
        # (e.g. concurrent ``file-delete``). Phase-2 found it; phase-3
        # didn't — rehashed stays 0 and we owe the user a clear notice.
        scope_id_vanished = (
            scope != "--all"
            and scope in hash_results
            and scope not in post_lock_ids
            and scope not in skipped_path_drift
        )
        # Skip the inventory rewrite + ledger entry when nothing actually
        # changed: no new digests written, no path-drift skips, no
        # vanished id, no concurrent additions. Avoids an unnecessary
        # mtime bump (wakes sync/watcher tooling) and avoids a misleading
        # ``rehashed=0`` ledger row indistinguishable from a healthy
        # no-op.
        wrote_anything = (
            rehashed > 0 or skipped_path_drift or added_during_hash or scope_id_vanished
        )
        if rehashed > 0:
            save_files(ctx.paths.files, records)
        if wrote_anything:
            detail = f"rehashed={rehashed}"
            if scope_id_vanished:
                detail += " vanished=1"
            if skipped_path_drift:
                detail += f" path-drift={len(skipped_path_drift)}"
            if added_during_hash:
                detail += f" added-during={len(added_during_hash)}"
            append_ledger(ctx.paths.ledger, "file-rehash", scope, label, detail)

    print(f"Rehashed {rehashed} file(s).")
    if missing:
        # Guard the join against any unexpected non-string (defensive — the
        # phase-2 skip already filters ``None`` rids).
        print(f"Skipped {len(missing)} missing from disk: {', '.join(str(m) for m in missing)}")
    if skipped_path_drift:
        print(
            f"Skipped {len(skipped_path_drift)} whose path changed during "
            f"hashing (rerun to pick them up): "
            f"{', '.join(str(m) for m in skipped_path_drift)}"
        )
    if scope_id_vanished:
        print(
            f"WARNING: '{scope}' was removed from the inventory while hashing "
            f"was in progress (concurrent file-delete). Nothing was written."
        )
    if added_during_hash:
        print(
            f"Note: {len(added_during_hash)} record(s) were added during "
            f"hashing and were not included in this --all rehash "
            f"(rerun to pick them up): "
            f"{', '.join(str(m) for m in added_during_hash)}"
        )
    return 0


def cmd_file_search(ctx: Context, args: list[str]) -> int:
    """Full-text search across the file inventory. ``file-search <query>``"""
    parser = argparse.ArgumentParser(prog="librarian file-search")
    parser.add_argument("query")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parsed = parser.parse_args(args)
    hits = search_files(load_files(ctx.paths.files), parsed.query)
    if parsed.format == "json":
        print(json.dumps(hits, indent=2, default=str))
        return 0
    print(f"Found {len(hits)} file(s) matching '{parsed.query}':\n")
    for r in hits:
        print(f"  {r.get('id', '?'):42s}  [{r.get('category', '?')}]")
        print(f"     {r.get('path', '?')}")
        if r.get("description"):
            print(f"     {r['description']}")
        print()
    return 0


def cmd_schema(ctx: Context, args: list[str]) -> int:
    """Describe the active schema. ``schema [--json]``"""
    as_json = "--json" in args
    if ctx.schema.is_empty:
        msg = {"configured": False, "path": str(ctx.paths.schema)}
        print(
            json.dumps(msg, indent=2)
            if as_json
            else f"No schema configured (expected at {ctx.paths.schema}).\n"
            f"The tool is running in generic mode — blocks are not validated."
        )
        return 0
    if as_json:
        print(
            json.dumps(
                {
                    "name": ctx.schema.name,
                    "description": ctx.schema.description,
                    "blocks": {
                        b.name: {
                            "label": b.label,
                            "fields": [
                                {
                                    "name": f.name,
                                    "type": f.type,
                                    "depends_on": f.depends_on,
                                    "required": f.required,
                                    "values": f.values,
                                }
                                for f in b.fields
                            ],
                        }
                        for b in ctx.schema.blocks
                    },
                },
                indent=2,
            )
        )
        return 0
    print(f"Schema: {ctx.schema.name}")
    if ctx.schema.description:
        print(f"  {ctx.schema.description}")
    print()
    for block in ctx.schema.blocks:
        print(f"  block '{block.name}'  ({block.label})")
        for field in block.fields:
            extra = f" depends_on={field.depends_on}" if field.depends_on else ""
            req = " [required]" if field.required else ""
            print(f"    {field.name:18s} {field.type}{extra}{req}")
            # Enumerate enum values so valid choices are discoverable without
            # reading schema.yaml. Dependent enums print their per-parent map.
            if field.type == "enum" and field.values is not None:
                if field.is_dependent_enum and isinstance(field.values, dict):
                    print(f"        values (by {field.depends_on}):")
                    for parent, children in field.values.items():
                        print(f"          {parent}: {', '.join(map(str, children))}")
                elif isinstance(field.values, list):
                    print(f"        values: {', '.join(map(str, field.values))}")
        print()
    return 0


def cmd_env(ctx: Context, args: list[str]) -> int:
    """Show the resolved data-home paths and their sources. ``env [--json]``

    Prints where each librarian resource resolves to, which ``LIBRARIAN_*``
    variable (if any) overrode it, and whether it currently exists on disk —
    so an agent or operator can discover the active configuration (the "truth
    sources") without reading the environment or config files directly. All
    paths are local to this machine; never paste this output into a public
    repository.
    """
    p = ctx.paths
    # (label, resolved-path-or-None, controlling env var, fallback-source).
    # The fallback names where a path comes from when its own env var is unset:
    # the XDG "default" for home, the data "home" for per-resource paths, and
    # "derived" for artifacts (always <root>/artifacts).
    rows = [
        ("home", p.home, "LIBRARIAN_HOME", "default"),
        ("activities", p.activities, "LIBRARIAN_YAML_PATH", "home"),
        ("files", p.files, "LIBRARIAN_FILES_PATH", "home"),
        ("ledger", p.ledger, "LIBRARIAN_LEDGER_PATH", "home"),
        ("schema", p.schema, "LIBRARIAN_SCHEMA_PATH", "home"),
        ("root", p.root, "LIBRARIAN_ROOT", "home"),
        ("artifacts", p.artifacts, None, "derived"),  # always <root>/artifacts
        ("memory_dir", p.memory_dir, "LIBRARIAN_MEMORY_DIR", "unset"),
    ]

    def source(var: str | None, fallback: str) -> str:
        if var is not None and os.environ.get(var, "").strip():
            return var
        return fallback

    if "--json" in args:
        out: dict = {}
        for label, path, var, fallback in rows:
            out[label] = {
                "path": str(path) if path is not None else None,
                "source": source(var, fallback) if path is not None else "unset",
                "exists": bool(path and Path(path).exists()),
            }
        out["schema_configured"] = not ctx.schema.is_empty
        print(json.dumps(out, indent=2))
        return 0

    print("librarian environment\n")
    for label, path, var, fallback in rows:
        if path is None:
            print(f"  {label:12s} (unset)")
            continue
        state = "exists" if Path(path).exists() else "MISSING"
        print(f"  {label:12s} {path}  [source={source(var, fallback)}, {state}]")
    print(f"\n  schema: {'configured' if not ctx.schema.is_empty else 'generic mode (none)'}")
    return 0


# =============================================================================
# Command dispatch
# =============================================================================

COMMANDS = {
    # read
    "search": cmd_search,
    "get": cmd_get,
    "filter": cmd_filter,
    "list": cmd_list,
    "stats": cmd_stats,
    "tags": cmd_tags,
    "tag-audit": cmd_tag_audit,
    "validate": cmd_validate,
    "export": cmd_export,
    "project": cmd_project,
    "similar": cmd_similar,
    "contact": cmd_contact,
    "changes": cmd_changes,
    "schema": cmd_schema,
    "env": cmd_env,
    # write
    "create": cmd_create,
    "delete": cmd_delete,
    "update-field": cmd_update_field,
    "update-description": cmd_update_description,
    "update-notes": cmd_update_notes,
    "update-nested-field": cmd_update_nested_field,
    "set-block": cmd_set_block,
    "add-tags": cmd_add_tags,
    "remove-tags": cmd_remove_tags,
    "add-docs": cmd_add_docs,
    "remove-docs": cmd_remove_docs,
    "rename-id": cmd_rename_id,
    "merge": cmd_merge,
    # file inventory
    "file-add": cmd_file_add,
    "file-list": cmd_file_list,
    "file-get": cmd_file_get,
    "file-move": cmd_file_move,
    "file-update": cmd_file_update,
    "file-rehash": cmd_file_rehash,
    "file-search": cmd_file_search,
}

_USAGE = f"""librarian {__version__} — a local-first, plain-text activity tracker.

Usage: librarian <command> [args...]

Commands: {", ".join(COMMANDS)}

Run `librarian <command> --help` for command-specific options.
Data home: set LIBRARIAN_HOME, or defaults to $XDG_CONFIG_HOME/librarian.
"""


def main(argv: list[str] | None = None) -> int:
    """Console entry point: parse argv, dispatch to a command, return exit code."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(__version__)
        return 0

    command = argv[0]
    handler = COMMANDS.get(command)
    if handler is None:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(COMMANDS)}")
        return 1

    try:
        ctx = build_context()
    except SchemaError as exc:
        print(f"ERROR: invalid schema.yaml — {exc}")
        return 1
    return handler(ctx, argv[1:])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
