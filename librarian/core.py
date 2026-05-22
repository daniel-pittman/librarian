"""Schema-agnostic analysis helpers shared across CLI commands.

This module holds the pieces of logic that several commands need and that do
not touch the filesystem: entry filtering, fuzzy similarity, the dangling
cross-reference scanner, and the contact-rolodex extractor. Keeping them here
keeps :mod:`librarian.cli` focused on argument parsing and I/O.
"""

from __future__ import annotations

import re

import yaml

# ---------------------------------------------------------------------------
# Text search + filtering
# ---------------------------------------------------------------------------

# Common English stop words excluded from similarity scoring.
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "at",
        "to",
        "for",
        "on",
        "is",
        "was",
        "by",
        "with",
        "from",
        "-",
        "—",
        "this",
        "that",
    }
)


def match_text(entry: dict, query: str) -> bool:
    """True if `query` appears anywhere in the entry (case-insensitive).

    The entry is dumped to YAML so the search covers every nested field —
    title, description, block contents, tags and docs alike.
    """
    text = yaml.dump(entry, default_flow_style=False, allow_unicode=True).lower()
    return query.lower() in text


def filter_entries(
    activities: list[dict],
    *,
    query: str | None = None,
    after: str | None = None,
    before: str | None = None,
    during_start: str | None = None,
    during_end: str | None = None,
    tags: list[str] | None = None,
    has_block: tuple[str, bool] | None = None,
    has_docs: bool | None = None,
    block_field: tuple[str, str, str] | None = None,
) -> list[dict]:
    """Filter activity entries by a combination of criteria.

    Args:
        activities: The full entry list.
        query: Free-text substring filter.
        after / before: Filter on the entry start date (``entry['date']``).
        during_start / during_end: Filter to entries *active* during a window.
            An entry is active during ``[start, end]`` when it started on or
            before ``end`` and had not ended before ``start`` (an entry with
            no ``end_date`` is treated as still ongoing).
        tags: Keep entries carrying at least one of these tags.
        has_block: ``(block_name, want)`` — keep entries that do (``want=True``)
            or do not (``want=False``) carry that block.
        has_docs: When ``True``, keep only entries with a non-empty docs list.
        block_field: ``(block, field, value)`` — keep entries whose block field
            equals (or, for substring fields, contains) ``value``.

    Returns:
        The matching entries, in input order.
    """
    results = []
    for entry in activities:
        if query and not match_text(entry, query):
            continue

        entry_date = entry.get("date", "") or ""
        if after and entry_date < after:
            continue
        if before and entry_date > before:
            continue

        if during_start or during_end:
            entry_end = entry.get("end_date", "") or ""
            if during_end and entry_date and entry_date > during_end:
                continue
            if during_start and entry_end and entry_end < during_start:
                continue

        if tags:
            entry_tags = entry.get("tags", []) or []
            if not any(t in entry_tags for t in tags):
                continue

        if has_block is not None:
            block_name, want = has_block
            if want and block_name not in entry:
                continue
            if not want and block_name in entry:
                continue

        if has_docs is True and not entry.get("docs"):
            continue

        if block_field is not None:
            block_name, field_name, wanted = block_field
            block = entry.get(block_name) or {}
            actual = str(block.get(field_name, ""))
            # Substring match keeps the original tool's `--subcategory`
            # partial-match ergonomics.
            if wanted not in actual:
                continue

        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Fuzzy similarity
# ---------------------------------------------------------------------------


def similarity_score(a: str, b: str) -> float:
    """Word-overlap similarity between two strings, in ``[0.0, 1.0]``.

    The score is the count of shared non-stop-words divided by the size of the
    smaller (stop-word-stripped) word set — a containment-style metric that is
    forgiving of one string being much longer than the other.
    """
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return 0.0
    meaningful = (a_words & b_words) - _STOP_WORDS
    smaller = min(len(a_words - _STOP_WORDS), len(b_words - _STOP_WORDS))
    if smaller == 0:
        return 0.0
    return len(meaningful) / smaller


def best_similarity(query_text: str, entry: dict) -> float:
    """Best similarity of `query_text` against an entry.

    Title matches are weighted 1.2x because a title hit is a stronger duplicate
    signal than an incidental description-word overlap.
    """
    title = entry.get("title", "") or ""
    full = f"{title} {entry.get('description', '') or ''}"
    return max(similarity_score(query_text, title) * 1.2, similarity_score(query_text, full))


# ---------------------------------------------------------------------------
# Tag normalisation
# ---------------------------------------------------------------------------


def tag_kernel(tag: str) -> str:
    """Normalise a tag for case-/separator-variant detection.

    Strips case and every non-alphanumeric character so ``bili-core``,
    ``BiliCore`` and ``bilicore`` all collapse to the same kernel.
    """
    return re.sub(r"[^a-z0-9]", "", tag.casefold())


# ---------------------------------------------------------------------------
# Dangling cross-reference scanner
# ---------------------------------------------------------------------------

# Backtick-wrapped tokens shaped like an entry id. Captured broadly, then
# filtered: an id-shaped token is one that is a valid slug AND contains a digit
# (entry ids in practice always carry a date component or a numeric tail), so a
# backticked tag such as `c3-lab-output` or a code term like `connect-src` is
# not mistaken for an entry reference.
_BACKTICKED_RE = re.compile(r"`([a-z0-9][a-z0-9-]+[a-z0-9])`")
_HAS_DIGIT_RE = re.compile(r"\d")


def scan_dangling_refs(
    activities: list[dict], ids: set[str], text_fields: list[tuple[str, list[str]]]
) -> list[tuple[str, str, str]]:
    """Find backticked, id-shaped cross-references that do not resolve.

    Args:
        activities: The entry list.
        ids: The set of all live entry ids.
        text_fields: A list of ``(label, path)`` pairs naming the text fields
            to scan. ``path`` is a list of keys: ``["description"]`` for a
            top-level field, ``["ptr", "notes"]`` for a nested one.

    Returns:
        ``(source_id, dangling_target, field_label)`` tuples. Self-references
        and references that resolve are not reported.
    """
    findings: list[tuple[str, str, str]] = []
    for entry in activities:
        eid = entry.get("id") or "?"
        for label, path in text_fields:
            text = _dig(entry, path)
            if not text:
                continue
            for match in _BACKTICKED_RE.finditer(text):
                ref = match.group(1)
                # Must look like an id (carry a digit) and not be a self-ref.
                if not _HAS_DIGIT_RE.search(ref) or ref == eid:
                    continue
                if ref not in ids:
                    findings.append((eid, ref, label))
    return findings


def _dig(entry: dict, path: list[str]) -> str:
    """Follow a key path into an entry, returning '' if any step is missing."""
    node: object = entry
    for key in path:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return node if isinstance(node, str) else ""


# ---------------------------------------------------------------------------
# Contact rolodex extraction
# ---------------------------------------------------------------------------

# An email immediately inside a "(...)" or "<...>" wrapper — the strong signal
# that an email is being attached to a preceding name.
_WRAPPED_EMAIL_RE = re.compile(r"[(<]\s*([\w.+-]+@[\w-]+\.[\w.-]+)", re.UNICODE)

# Lowercase particles that legitimately appear inside a name (van, de, von...).
_NAME_PARTICLES = frozenset(
    {"von", "van", "de", "del", "da", "di", "du", "la", "le", "el", "des", "der", "den"}
)
# Honorifics that may precede a name.
_NAME_TITLES = frozenset({"Dr", "Dr.", "Mr", "Mr.", "Ms", "Ms.", "Mrs", "Mrs.", "Prof", "Prof."})
# Capitalised role nouns that should stop a name walk-back.
_ROLE_STOP_WORDS = frozenset(
    {
        "Manager",
        "Director",
        "Investigator",
        "Coordinator",
        "Advisor",
        "Lead",
        "Officer",
        "Chair",
        "President",
        "Dean",
        "Faculty",
        "Senior",
        "Project",
        "Technical",
        "Principal",
        "Assistant",
        "Associate",
        "Engineer",
        "Analyst",
        "Scientist",
        "Specialist",
        "Researcher",
        "Member",
        "Intern",
        "Founder",
    }
)


def _is_name_token(token: str, *, is_last_name: bool) -> bool:
    """Decide whether one whitespace-delimited token can be part of a name."""
    bare = token.rstrip(",;:.")
    if not bare:
        return False
    if token in _NAME_TITLES:
        return True
    if bare in _ROLE_STOP_WORDS:
        return False
    if bare[0].islower():
        # A lowercase word is only valid as a mid-name particle.
        return (not is_last_name) and bare.lower() in _NAME_PARTICLES
    return bare[0].isupper() and any(ch.isalpha() for ch in bare)


def _walk_back_for_name(text: str, email_start: int) -> str:
    """Extract the capitalised name sequence ending just before an email."""
    snippet = text[max(0, email_start - 80) : email_start]
    tokens = snippet.split()
    name_parts: list[str] = []
    for token in reversed(tokens):
        is_last_name = not name_parts
        if not _is_name_token(token, is_last_name=is_last_name):
            break
        name_parts.append(token.strip(",;:"))
    if not name_parts:
        return ""
    # Cap at 4 tokens — anything longer almost always swept in a role title.
    name_parts = name_parts[:4]
    return " ".join(reversed(name_parts)).strip(" .,;:-")


def extract_contacts(activities: list[dict]) -> dict[str, dict]:
    """Build a rolodex from ``Name (email@domain)`` patterns in descriptions.

    Returns a mapping ``email -> {"names": set, "sources": [entry-id, ...]}``.
    """
    contacts: dict[str, dict] = {}
    for entry in activities:
        eid = entry.get("id", "?")
        desc = entry.get("description", "") or ""
        for match in _WRAPPED_EMAIL_RE.finditer(desc):
            email = match.group(1).strip().rstrip(".,;:")
            name = _walk_back_for_name(desc, match.start())
            # A real name has at least two words; reject single-token noise.
            if len(name.split()) < 2:
                continue
            record = contacts.setdefault(email, {"names": set(), "sources": []})
            record["names"].add(name)
            if eid not in record["sources"]:
                record["sources"].append(eid)
    return contacts


def canonical_name(names: set[str]) -> str:
    """Pick the most descriptive name from a set of observed variants."""
    return sorted(names, key=lambda n: (-len(n), not n.startswith("Dr")))[0]
