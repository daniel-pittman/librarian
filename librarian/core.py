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
# (entry ids in practice always carry a date component or a numeric tail) AND
# contains at least one hyphen (entry ids are multi-token slugs; this excludes
# hex-only tokens like backticked git commit SHAs that would otherwise look
# id-shaped). Backticked tags such as `peer-reviewed`, code terms like
# `connect-src`, and SHAs like `ce153c8` are not mistaken for entry references.
_BACKTICKED_RE = re.compile(r"`([a-z0-9][a-z0-9-]+[a-z0-9])`")
_HAS_DIGIT_RE = re.compile(r"\d")

# Phrases that mark a following backticked id as deliberately historical, not a
# live cross-reference. The scanner skips such mentions even when the id has
# been renamed away or consolidated under a merge, so prose like "Originally
# tracked under `2024-foo`" or "Consolidates `2023-bar`" doesn't surface as a
# DANGLING REF after the fact. Matched within a ~200-char window preceding the
# backtick to keep historical context tied to its own sentence.
_HISTORICAL_PHRASES_RE = re.compile(
    r"\b(?:"
    # Each historical word must be followed by a context verb to count, so a
    # bare "previously discussed" or "formerly common practice" cannot
    # blanket-suppress an unrelated dangling ref in the same window.
    r"originally\s+(?:tracked|known|named|filed|recorded|logged|stored)|"
    r"previously\s+(?:tracked|known\s+as|named|filed\s+under|recorded\s+as)|"
    r"formerly\s+(?:tracked|known\s+as|named|filed\s+under)|"
    r"consolidat\w*\s+(?:from|under|into)|"
    r"merged\s+(?:from|into)|"
    r"renamed\s+(?:from|to)|"
    r"superseded\s+by"
    # The bare alternatives "old id", "former(ly) id" and "was named/known as"
    # used to live here. They were too generic — "old id-pattern matched" or
    # "the function was named" would silently suppress a real dangling ref in
    # the same window. Authors with a history note can still get coverage via
    # the explicit forms above ("previously named", "formerly tracked", etc.).
    r")\b",
    re.IGNORECASE,
)
_HISTORICAL_WINDOW = 200  # chars before the backtick we'll scan for a phrase


def scan_dangling_refs(
    activities: list[dict],
    ids: set[str],
    text_fields: list[tuple[str, list[str]]],
    exclude: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Find backticked, id-shaped cross-references that do not resolve.

    Args:
        activities: The entry list.
        ids: The set of all live entry ids.
        text_fields: A list of ``(label, path)`` pairs naming the text fields
            to scan. ``path`` is a list of keys: ``["description"]`` for a
            top-level field, ``["ptr", "notes"]`` for a nested one.
        exclude: A set of names that may look entry-id-shaped but are NOT
            entry references (typically: tags in use anywhere in the corpus,
            and file-inventory ids). The scanner's id-shape heuristic
            (slug + digit) over-matches on these and would otherwise produce
            false positives like a backticked tag name. Suppressing them
            here keeps real dangling refs from getting buried in noise.

    Returns:
        ``(source_id, dangling_target, field_label)`` tuples. Self-references,
        references that resolve, and excluded names are not reported.
    """
    exclude = exclude or set()
    findings: list[tuple[str, str, str]] = []
    for entry in activities:
        eid = entry.get("id") or "?"
        for label, path in text_fields:
            text = _dig(entry, path)
            if not text:
                continue
            for match in _BACKTICKED_RE.finditer(text):
                ref = match.group(1)
                # Must look like an id (multi-token slug with a digit) and not
                # be a self-ref. Requiring a hyphen excludes hex-only tokens
                # like git commit SHAs that would otherwise pass the slug+digit
                # filter and produce false positives.
                if "-" not in ref or not _HAS_DIGIT_RE.search(ref) or ref == eid:
                    continue
                # Skip names known to NOT be entry ids (tags, file ids).
                if ref in exclude:
                    continue
                if ref in ids:
                    continue
                # Treat as historical (not dangling) when a phrase like
                # "Originally tracked under", "Previously known as",
                # "Consolidated from" appears in the prose preceding the
                # backtick. Scope: when an earlier closing backtick sits in
                # the window, keep the phrase visible only if the text
                # between the two backticks is a list-continuation token
                # (` and `, `, `, ` or `). Anything else ("`a`, but see
                # also `b`", "`a`. See also `b`") opens a new clause and the
                # phrase no longer applies.
                preceding = text[max(0, match.start() - _HISTORICAL_WINDOW) : match.start()]
                last_close_backtick = preceding.rfind("`")
                if last_close_backtick != -1:
                    between = preceding[last_close_backtick + 1 :].strip(" \t,")
                    if between and between.lower() not in ("and", "or", "&"):
                        preceding = preceding[last_close_backtick + 1 :]
                if _HISTORICAL_PHRASES_RE.search(preceding):
                    continue
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
# Generational / ordinal name suffixes that look acronym-shaped (all-caps,
# length >= 2) but legitimately belong on the end of a name. Excluded from
# the post-loop all-caps strip so ``Bob Smith III`` keeps the ``III``.
# Note: ``Jr``/``Jr.``/``Sr``/``Sr.`` happen to survive the post-loop strip
# on their own (the lowercase ``r`` breaks the all-upper check), but they
# belong in this set for consistency and robustness against future
# refactoring of the strip condition.
_NAME_SUFFIXES = frozenset(
    {
        "II",
        "III",
        "IV",
        "V",
        "VI",
        "VII",
        "VIII",
        "IX",
        "X",
        "XI",
        "XII",
        "Jr",
        "Jr.",
        "Sr",
        "Sr.",
        # All-caps variants of Jr/Sr — uncommon stylistically but seen
        # in legal-style author lists.
        "JR",
        "JR.",
        "SR",
        "SR.",
        # All-caps degree / credential suffixes. The lowercase-letter
        # forms (PhD, MSc, BSc) survive the all-upper check on their
        # own; these all-caps forms need the allowlist to preserve them.
        "MD",
        "MA",
        "BA",
        "MS",
        "BS",
        "JD",
        "MBA",
        "DDS",
        "DVM",
    }
)
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
    """Extract the capitalised name sequence ending just before an email.

    Driven by a single property: a real-name token is one that contains at
    least one lowercase letter. Acronyms (``DU``, ``ZX``, ``ACM``) lack
    lowercase letters by construction; honorifics (``Dr.``, ``Mr.``) are
    handled by ``_NAME_TITLES``; generational / degree suffixes that look
    acronym-shaped (``III``, ``MD``, ``MBA``) are preserved via the explicit
    ``_NAME_SUFFIXES`` allowlist. The `;`-boundary handling and the post-loop
    cleanup both key off these checks, which lets the same logic cover six
    arrangements:

      A. ``Bob Smith (email)`` — no boundary. Standard walk-back.
      B. ``Bob Smith; (email)`` — boundary IS the name's last word.
      C. ``ZX; Bob Smith (email)`` — boundary BEFORE real-name material.
      D. ``Alice Garcia; ACM (email)`` — institutional between name and email.
      E. ``Bob Smith ZX; (email)`` — single trailing acronym.
      F. ``Bob Smith ZX YZ; (email)`` — multi-token trailing acronyms.

    Walking back, we keep collecting plausibly-name tokens until we hit a
    `;` boundary. When the boundary fires, the decision is binary on whether
    we've collected any real-name material (any token with a lowercase
    letter):

      * Have real-name material → respect the boundary, stop here (Case B/C).
      * No real-name material yet → the name is past the boundary; discard
        whatever suspect tokens we collected, optionally consume the
        boundary token's cleaned form if it has lowercase, continue walking
        back (Case D, also handles the boundary-on-first-token sub-case).
      * Boundary token itself is suspect (all-caps) → skip it without
        consuming and keep walking (Case E).

    After the loop, strip leading-position (source-rightmost) all-caps
    tokens of length ≥2 from ``name_parts``. This handles multi-token
    trailing affiliations that slipped past the boundary skip because they
    didn't carry the `;` themselves (Case F). The 2-char-minimum preserves
    single-letter initials.

    Trailing `.` is intentionally NOT treated as a boundary: it legitimately
    ends honorifics in ``_NAME_TITLES`` (``Dr.``, ``Prof.``) and middle
    initials like ``A.`` in ``Maria A. Smith``.

    Known limitations:

      * Comma-separated author lists (``Alice Smith, Bob Jones``) — `,` is
        still silently stripped by ``_is_name_token`` and the append. The
        last author's name can absorb earlier authors. Fixing requires
        disambiguating ``Smith, Jr.`` (intra-name) from ``Smith, Bob``
        (cross-clause), which needs look-ahead and is deferred.
      * Trailing punctuation after the `;` (``ZX;)``), no space after the
        `;` (``ZX;Alice``), and Unicode look-alike semicolons (``；``,
        ``;``) all bypass this ASCII-`;`-at-end check.
      * 80-char snippet window. If a verbose affiliation pushes the prior
        author's `;` outside the window, the boundary never enters the
        token list and the leak resurfaces.
      * **False-positive class: capitalised noise without an acronym
        marker.** A description like ``Reviewed Annual Filings ZX; (email)``
        produces ``Reviewed Annual Filings`` as a "name" — the skip-continue
        branch consumes the visible ``ZX`` marker that a human auditor
        might otherwise use to spot the corruption. Pre-aabb211 the same
        input produced ``Reviewed Annual Filings ZX`` (still wrong, but
        self-flagging). The walk-back has no semantic name detector, so
        this class of false positive can't be fully closed without a more
        substantial redesign.
      * **All-caps last names** (``SMITH``, ``CHEN``) are misclassified as
        institutional acronyms by the lowercase-letter check and stripped
        by the post-loop cleanup. Author lists that use ``SMITH, J.`` style
        all-caps last names will lose those contacts. Real-world impact is
        narrow because most description prose uses Title Case for names.
      * **Internal-`;` tokens** like ``"III;some"`` (a `;` mid-token without
        whitespace around it) bypass the ``endswith(";")`` boundary gate.
        The token then enters ``name_parts`` literally, producing a display
        name containing an embedded `;` (e.g. ``"Bob III;some Jones"``).
        Realistic only when descriptions concatenate text via search-and-
        replace that drops a space; the `;`-handler operates on whitespace-
        delimited tokens.

    The result is capped at 4 tokens (``name_parts[:4]``) — anything longer
    almost always swept in a role title at the source-leftmost position
    (the first-name end of the name in source order, since ``name_parts`` is
    built in reverse-source order during the walk-back).
    """
    snippet = text[max(0, email_start - 80) : email_start]
    tokens = snippet.split()
    name_parts: list[str] = []

    def has_lowercase(s: str) -> bool:
        return any(ch.islower() for ch in s)

    for token in reversed(tokens):
        if token.endswith(";"):
            cleaned = token.rstrip(";").strip(",;:")
            # A bare `;` token (whitespace on both sides → cleaned is
            # empty) is always a hard clause boundary regardless of
            # whether name_parts is empty. Without this, the walk-back
            # would silently sweep across the boundary.
            if not cleaned:
                break
            # Have we collected any real-name material (a token with at
            # least one lowercase letter) on this side of the boundary?
            if any(has_lowercase(p) for p in name_parts):
                # Yes — respect the boundary. Stop without consuming.
                break
            # No real-name material yet. Four sub-cases for the boundary
            # token's `cleaned` form:
            if has_lowercase(cleaned):
                if _is_name_token(cleaned, is_last_name=not name_parts):
                    # Real name word terminating its own clause
                    # (e.g. "Smith;"). Discard suspect tokens (but
                    # preserve any legitimate name suffixes already
                    # collected — e.g. ``III`` in ``Foo Smith; III``),
                    # consume the cleaned remainder, continue walking
                    # back for preceding name words.
                    name_parts[:] = [p for p in name_parts if p in _NAME_SUFFIXES]
                    name_parts.append(cleaned)
                    continue
                # Lowercase but NOT a name token — it's a role stop-word
                # (Director, Manager, Lead) or a particle. The `;` is a
                # real boundary; respect it rather than skip-continue,
                # which would silently sweep the prior clause's name
                # tokens and misattribute them to this email.
                break
            # cleaned is all-caps. Two sub-cases:
            if cleaned in _NAME_SUFFIXES:
                # Legitimate name suffix terminating its clause
                # (e.g. ``Bob Smith III;`` or ``Bob Smith MD;``).
                # Preserve it like Case D's name-word path: discard
                # suspects, consume, continue walking back.
                name_parts[:] = [p for p in name_parts if p in _NAME_SUFFIXES]
                name_parts.append(cleaned)
                continue
            # Institutional acronym in trailing position. Skip without
            # consuming and continue walking back to find the real
            # name behind it.
            name_parts.clear()
            continue
        is_last_name = not name_parts
        if not _is_name_token(token, is_last_name=is_last_name):
            break
        name_parts.append(token.strip(",;:"))

    # Post-loop: strip leading-position (source-rightmost) all-caps tokens
    # of length ≥2 — these are multi-token trailing affiliations that the
    # boundary skip didn't catch (because they didn't carry the `;`
    # themselves). Length-1 tokens preserved so single-letter initials
    # without trailing punctuation don't get stripped.
    #
    # Two-pass design so a legitimate name suffix at position 0 doesn't
    # block the acronym strip behind it:
    #   1. Collect a run of leading-position ``_NAME_SUFFIXES`` tokens
    #      (the source-rightmost suffixes — e.g. ``III``).
    #   2. Strip leading-position all-caps non-suffix tokens (the
    #      affiliations — e.g. ``ZX``).
    #   3. Re-attach the suffix run at the front so the suffix stays at
    #      its source-rightmost position relative to the name.
    #
    # The ``len ≥ 2`` guard is load-bearing for the empty-string edge
    # case: ``all(ch.isalpha() and ch.isupper() for ch in "")`` is
    # vacuously True, so without the guard a phantom empty token would
    # loop forever.
    trailing_suffixes: list[str] = []
    while name_parts and name_parts[0] in _NAME_SUFFIXES:
        trailing_suffixes.append(name_parts.pop(0))
    while (
        name_parts
        and len(name_parts[0]) >= 2
        and name_parts[0] not in _NAME_SUFFIXES
        and all(ch.isalpha() and ch.isupper() for ch in name_parts[0])
    ):
        name_parts.pop(0)
    name_parts[:0] = trailing_suffixes

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
