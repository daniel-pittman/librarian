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
import io
import json
import os
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
    append_text,
    find_entry_line_range,
    line_indent,
    load_activities,
    read_lines,
    write_lines,
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
            label = args[i + 1]
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
        if not entry.get("docs"):
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
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    parser = argparse.ArgumentParser(prog="librarian create")
    parser.add_argument("--json", help="entry as a JSON string")
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(args)

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

    _, activities = load_activities(ctx.paths.activities)
    if data["id"] in {e.get("id") for e in activities}:
        print(f"ERROR: entry with id '{data['id']}' already exists")
        return 1

    # Validate any schema blocks present on the new entry.
    for block in ctx.schema.blocks:
        if block.name in data:
            block_issues = validate_block(block, data[block.name] or {})
            if block_issues:
                print(f"ERROR: schema validation failed for block '{block.name}':")
                for issue in block_issues:
                    print(f"  {issue}")
                return 1

    # Fuzzy duplicate warning (non-blocking).
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

    # Match the indentation style of existing entries so the appended YAML
    # stays parseable. If the file already has `- id:` lines, copy their
    # indent; otherwise default to 2-space (the style of a fresh file).
    indent = _detect_entry_indent(ctx.paths)
    yaml_text = _render_entry(ctx, data, indent=indent)
    if parsed.dry_run:
        print("Dry run — would append:\n")
        print(yaml_text)
        return 0

    ctx.paths.ensure_home()
    # Ensure the file starts with an `activities:` key on first use.
    if not ctx.paths.activities.exists():
        ctx.paths.activities.write_text("activities:\n", encoding="utf-8")
    append_text(ctx.paths.activities, "\n" + yaml_text)
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
    core_keys = {"id", "date", "end_date", "title", "description", "tags", "docs"}
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
        return "true" if value in (True, "true", "yes", "1") else "false"
    return yaml_quote(str(value))


def _render_generic_scalar(value) -> str:
    """Render an unknown-block scalar value for YAML output."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return yaml_quote(str(value))


def cmd_delete(ctx: Context, args: list[str]) -> int:
    """Delete an entry by id. ``delete <entry-id> [--confirm]`` (dry-run default)."""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if not args:
        print("Usage: librarian delete <entry-id> --confirm")
        return 1
    entry_id = args[0]
    confirm = "--confirm" in args
    lines = read_lines(ctx.paths.activities)
    start, end = find_entry_line_range(lines, entry_id)
    if start is None:
        print(f"ERROR: entry '{entry_id}' not found")
        return 1
    print(f"Entry '{entry_id}' spans lines {start + 1}-{end} ({end - start} lines).")
    if not confirm:
        print("\nDry run — pass --confirm to actually delete.")
        return 0
    del lines[start:end]
    write_lines(ctx.paths.activities, lines)
    append_ledger(ctx.paths.ledger, "delete", entry_id, label, details=f"lines={end - start}")
    print(f"Deleted entry '{entry_id}' ({end - start} lines removed)")
    return 0


def cmd_update_field(ctx: Context, args: list[str]) -> int:
    """Update a top-level field. ``update-field <id> <field> <value>``

    Supported fields: ``title``, ``date``, ``end_date``.
    """
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if len(args) < 3:
        print("Usage: librarian update-field <id> <title|date|end_date> <value>")
        return 1
    entry_id, field, value = args[0], args[1], " ".join(args[2:])
    if field not in ("title", "date", "end_date"):
        print(f"ERROR: field '{field}' not supported (use title, date, end_date)")
        return 1

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
        lines[idx:cont] = [f"{indent}{field}: {yaml_quote(value)}\n"]
        write_lines(ctx.paths.activities, lines)
        append_ledger(ctx.paths.ledger, "update-field", entry_id, label, f"{field}={value[:80]}")
        print(f"Updated {field} on '{entry_id}' to: {value}")
        return 0

    # end_date can be added after the date line if it does not yet exist.
    if field == "end_date":
        date_idx = _find_field_line(lines, start, end, "date")
        if date_idx is not None:
            indent = " " * line_indent(lines[date_idx])
            lines.insert(date_idx + 1, f"{indent}end_date: {yaml_quote(value)}\n")
            write_lines(ctx.paths.activities, lines)
            append_ledger(
                ctx.paths.ledger, "update-field", entry_id, label, f"{field}={value[:80]} (added)"
            )
            print(f"Added {field} on '{entry_id}' to: {value}")
            return 0
    print(f"ERROR: field '{field}' not found in entry '{entry_id}'")
    return 1


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
    # so dependent enums resolve against the live parent value.
    block_data = dict(entry.get(block_name) or {})
    block_data[field_name] = coerced
    issues = [
        issue for issue in validate_block(block_def, block_data) if field_name.upper() in issue
    ]
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


def _tags_line(lines: list[str], start: int, end: int) -> int | None:
    """Return the index of the ``tags:`` line within an entry."""
    return _find_field_line(lines, start, end, "tags")


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
        # Single pass — keeping `to_delete` (line indices) and `removed`
        # (tag values) in sync via two separate list comprehensions over
        # `items` would invite drift if the match predicate ever grew.
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


def cmd_rename_id(ctx: Context, args: list[str]) -> int:
    """Rename an entry id and repoint cross-references. ``rename-id <old> <new>``

    Repoints every cross-reference to the old id -- backticked or plain-text --
    in descriptions and notes. Matches are bounded by id-character lookarounds,
    so an id is only rewritten when it appears as a whole token (a rename of
    ``ongoing-coi`` leaves ``ongoing-coi-training`` untouched).
    """
    import re

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
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*[a-z0-9]", new_id):
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

    # Repoint every cross-reference to the old id, bounded by id-character
    # lookarounds. This catches both backticked refs (``old-id``) and plain-
    # text mentions in prose, while leaving longer id-shaped strings that
    # happen to start with the old id untouched.
    pattern = re.compile(rf"(?<![a-z0-9-]){re.escape(old_id)}(?![a-z0-9-])")
    repointed = 0
    for i, line in enumerate(lines):
        new_line, n = pattern.subn(new_id, line)
        if n:
            repointed += n
            lines[i] = new_line
    write_lines(ctx.paths.activities, lines)
    append_ledger(ctx.paths.ledger, "rename-id", new_id, label, f"from={old_id} refs={repointed}")
    print(f"Renamed '{old_id}' -> '{new_id}' ({repointed} cross-reference(s) repointed)")
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
    """Recompute sha256 for one file or the whole inventory. ``file-rehash <id>|--all``"""
    label = _resolve_label(args, required=True)
    if label is None:
        return 1
    if not args:
        print("Usage: librarian file-rehash <id> | --all")
        return 1
    records = load_files(ctx.paths.files)
    if args[0] == "--all":
        targets, scope = records, "--all"
    else:
        scope = args[0]
        targets = [r for r in records if r.get("id") == scope]
        if not targets:
            print(f"ERROR: file id '{scope}' not found")
            return 1
    rehashed, missing = 0, []
    for record in targets:
        abs_path = ctx.paths.root / record.get("path", "")
        if not abs_path.exists():
            missing.append(record.get("id"))
            continue
        record["sha256"] = sha256_of(abs_path)
        rehashed += 1
    save_files(ctx.paths.files, records)
    append_ledger(ctx.paths.ledger, "file-rehash", scope, label, f"rehashed={rehashed}")
    print(f"Rehashed {rehashed} file(s).")
    if missing:
        print(f"Skipped {len(missing)} missing from disk: {', '.join(missing)}")
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
        print()
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
    # write
    "create": cmd_create,
    "delete": cmd_delete,
    "update-field": cmd_update_field,
    "update-description": cmd_update_description,
    "update-notes": cmd_update_notes,
    "update-nested-field": cmd_update_nested_field,
    "add-tags": cmd_add_tags,
    "remove-tags": cmd_remove_tags,
    "add-docs": cmd_add_docs,
    "remove-docs": cmd_remove_docs,
    "rename-id": cmd_rename_id,
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
