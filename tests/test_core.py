"""Unit tests for ``librarian.core`` helpers."""

from __future__ import annotations

from librarian.core import extract_contacts, scan_dangling_refs


def _entry(eid: str, **fields) -> dict:
    """Build a minimal entry dict for the scanner."""
    return {"id": eid, **fields}


_DESC = [("description", ["description"])]


def test_scan_dangling_refs_flags_real_dangling():
    """A backticked id-shaped token that does not resolve must be flagged."""
    activities = [
        _entry("2024-01-real", description="See `2099-99-nonexistent` below."),
    ]
    findings = scan_dangling_refs(activities, ids={"2024-01-real"}, text_fields=_DESC)
    assert findings == [("2024-01-real", "2099-99-nonexistent", "description")]


def test_scan_dangling_refs_skips_self_and_resolved():
    """Self-refs and refs that resolve must not be flagged."""
    activities = [
        _entry(
            "2024-01-real",
            description="Self-ref `2024-01-real` and resolved `2024-02-other`.",
        ),
        _entry("2024-02-other", description=""),
    ]
    ids = {"2024-01-real", "2024-02-other"}
    assert scan_dangling_refs(activities, ids=ids, text_fields=_DESC) == []


def test_scan_dangling_refs_excludes_tags():
    """A backticked token that matches a tag-in-use is NOT a dangling entry
    reference and must be suppressed. Without exclusion the slug-plus-digit
    heuristic would flag tags like `c3-lab-output` as false positives."""
    activities = [
        _entry(
            "2024-01-tagged",
            tags=["c3-lab-output", "sustainability-hub"],
            description="(empty)",
        ),
        _entry(
            "2024-02-mentions",
            description="The `c3-lab-output` tag covers this work.",
        ),
    ]
    ids = {"2024-01-tagged", "2024-02-mentions"}

    # Without exclude: false positive on the tag.
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC)
    assert ("2024-02-mentions", "c3-lab-output", "description") in findings

    # With exclude: the tag is suppressed.
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC, exclude={"c3-lab-output"})
    assert findings == []


def test_scan_dangling_refs_excludes_file_inventory_ids():
    """A backticked token that matches a file-inventory id is NOT a dangling
    entry reference -- mentioning a file id in prose (e.g. an "inventory file
    `watermark-portfolio-export-2026`" callout) is legitimate."""
    activities = [
        _entry(
            "2024-01-watermark-audit",
            description="Audited the inventory file `watermark-portfolio-export-2026`.",
        ),
    ]
    ids = {"2024-01-watermark-audit"}
    inventory = {"watermark-portfolio-export-2026"}

    # Without exclude: false positive.
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC)
    assert findings, "expected a (false) finding without exclusion"

    # With exclude: file-inventory id is suppressed.
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC, exclude=inventory)
    assert findings == []


def test_scan_dangling_refs_still_catches_real_dangling_when_excluding():
    """Excluding tags/file-ids must not mask a real dangling entry reference."""
    activities = [
        _entry(
            "2024-01-mixed",
            tags=["c3-lab-output"],
            description=(
                "References include a tag `c3-lab-output`, a real entry "
                "`2024-02-resolved`, and a genuinely broken `2099-99-missing`."
            ),
        ),
        _entry("2024-02-resolved", description=""),
    ]
    ids = {"2024-01-mixed", "2024-02-resolved"}
    findings = scan_dangling_refs(
        activities,
        ids=ids,
        text_fields=_DESC,
        exclude={"c3-lab-output"},
    )
    # The tag and the resolved entry are not flagged; the genuinely broken
    # reference IS still flagged. That is the key safety property.
    assert findings == [("2024-01-mixed", "2099-99-missing", "description")]


def test_scan_dangling_refs_skips_hex_tokens_without_hyphens():
    """Hex-only tokens like git commit SHAs (`ce153c8`, `df4c80f`) pass the
    slug+digit filter but are not entry references. Requiring a hyphen
    excludes them: real entry ids are multi-token slugs and always have at
    least one hyphen."""
    activities = [
        _entry(
            "2024-01-refactor",
            description=("Behavior change landed in commits `ce153c8` and `df4c80f`."),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2024-01-refactor"}, text_fields=_DESC)
    assert findings == [], f"hex tokens were incorrectly flagged: {findings}"


def test_scan_dangling_refs_skips_historical_originally_tracked():
    """A backticked former-id preceded by 'Originally tracked under' is
    historical context, not a dangling reference. Spec follow-up: soften the
    scanner so consolidated/renamed history doesn't surface as broken links."""
    activities = [
        _entry(
            "2026-09-merged",
            description=(
                "Active record. Originally tracked under `2024-old-id` before consolidation."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-merged"}, text_fields=_DESC)
    assert findings == [], f"historical mention was wrongly flagged: {findings}"


def test_scan_dangling_refs_skips_historical_previously_known_as():
    """'Previously known as `id`' is historical context."""
    activities = [
        _entry(
            "2026-09-renamed",
            description="Previously known as `2024-legacy-id`.",
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-renamed"}, text_fields=_DESC)
    assert findings == []


def test_scan_dangling_refs_skips_historical_consolidated_from():
    """'Consolidated from `id`' is historical context (the merge use case)."""
    activities = [
        _entry(
            "2026-09-merged",
            description="Consolidated from `2024-source-a` and `2024-source-b`.",
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-merged"}, text_fields=_DESC)
    assert findings == []


def test_scan_dangling_refs_still_catches_plain_dangling_after_softening():
    """A backticked id with NO historical-context phrase nearby is still
    flagged. The softening must not blanket-suppress every dangling ref."""
    activities = [
        _entry(
            "2026-09-real",
            description="Related to `2024-bogus-ref` for context.",
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-real"}, text_fields=_DESC)
    assert findings, "real dangling ref should still be flagged"
    assert findings[0][1] == "2024-bogus-ref"


def test_scan_dangling_refs_does_not_blanket_skip_on_bare_originally_previously_formerly():
    """A bare 'previously'/'originally'/'formerly' must NOT suppress an
    unrelated dangling ref. Round-2 review finding #2: the regex previously
    allowed those words alone, which blanket-skipped genuine dangling refs.

    Now each historical word must be followed by a context verb
    (tracked / known as / named / filed under) to count.
    """
    activities = [
        _entry(
            "2026-09-discuss",
            description="We previously discussed approach X. See `2024-broken` for details.",
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-discuss"}, text_fields=_DESC)
    assert findings, "bare 'previously' must not hide a dangling ref"
    assert findings[0][1] == "2024-broken"


def test_scan_dangling_refs_historical_window_does_not_cross_earlier_backtick():
    """A historical phrase attached to an EARLIER backticked id must not
    blanket-suppress a LATER, unrelated dangling id in the same sentence.

    Round-2 review finding #6. Scoping the search to text after the nearest
    earlier closing backtick keeps the phrase tied to its own ref.
    """
    activities = [
        _entry(
            "2026-09-merged",
            description=(
                "Originally tracked under `2024-old-id`, but see also `2024-bogus` for context."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-merged"}, text_fields=_DESC)
    # 2024-old-id is historical (covered by the phrase) → suppressed.
    # 2024-bogus is a separate ref; the phrase doesn't apply to it.
    assert any(ref == "2024-bogus" for _, ref, _ in findings), (
        f"historical phrase wrongly suppressed an unrelated dangling ref: {findings}"
    )
    assert not any(ref == "2024-old-id" for _, ref, _ in findings)


def test_scan_dangling_refs_historical_phrase_window_is_local():
    """A historical phrase too far away (>200 chars before the backtick)
    must NOT suppress a dangling ref — otherwise stray context anywhere in
    a long description would silently hide real broken links."""
    far_away_filler = "noise " * 100  # ~600 chars
    activities = [
        _entry(
            "2026-09-real",
            description=(
                "Originally tracked elsewhere. "
                + far_away_filler
                + "Related to `2024-bogus-ref` for context."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-real"}, text_fields=_DESC)
    assert findings, "a far-away historical phrase must not hide a dangling ref"


def test_scan_dangling_refs_does_not_suppress_on_bare_originally_stored_or_known():
    """The ``originally\\s+(...)`` branch used to allow bare completers
    (``originally stored``, ``originally known``, ``originally filed``)
    while the matching ``previously`` / ``formerly`` branches required
    ``as`` / ``under``. The asymmetry re-opened exactly the blanket-
    suppression hole round-2 closed for the other branches.

    Round-5 review finding #2: tighten the ``originally`` branch to the
    same discipline — each form must be followed by its completer word
    (``as``, ``under``).
    """
    activities = [
        _entry(
            "2026-09-stored",
            description=(
                "The dataset was originally stored as CSV. "
                "See `2024-broken-ref` for migration notes."
            ),
        ),
        _entry(
            "2026-09-known",
            description=(
                "The helper was originally known internally as a fragile area. "
                "Compare `2024-also-broken`."
            ),
        ),
        _entry(
            "2026-09-filed",
            description=(
                "The volume was originally filed by mistake; "
                "replaced by the entry at `2024-third-broken`."
            ),
        ),
    ]
    ids = {"2026-09-stored", "2026-09-known", "2026-09-filed"}
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-broken-ref" in refs, (
        f"bare 'originally stored' (no 'as') must not hide a dangling ref: {findings}"
    )
    assert "2024-also-broken" in refs, (
        f"bare 'originally known' (no 'as') must not hide a dangling ref: {findings}"
    )
    assert "2024-third-broken" in refs, (
        f"bare 'originally filed' (no 'under') must not hide a dangling ref: {findings}"
    )


def test_scan_dangling_refs_historical_phrase_survives_line_wrap():
    """A historical phrase that wraps across a single newline (a common
    YAML literal-block authoring pattern) must still be recognized — the
    bare ``\\n`` is NOT a sentence boundary, only a paragraph break
    (``\\n\\s*\\n``) or sentence-terminator-plus-whitespace is.

    Round-6 review finding #1: an earlier fix treated every ``\\n`` as a
    clause boundary, false-positively flagging the backtick below as
    dangling because the trim discarded "Originally tracked" on the
    prior line.
    """
    activities = [
        _entry(
            "2026-09-wrapped",
            description="Originally tracked\nunder `2024-legacy-id`.",
        ),
        _entry(
            "2026-09-wrapped-2",
            description=(
                "Active record.\n"
                "Originally tracked under `2024-legacy-id-2` before\n"
                "the consolidation."
            ),
        ),
    ]
    ids = {"2026-09-wrapped", "2026-09-wrapped-2"}
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-legacy-id" not in refs, (
        f"line-wrapped historical phrase must still be recognized: {findings}"
    )
    assert "2024-legacy-id-2" not in refs, (
        f"line-wrapped historical phrase must still be recognized: {findings}"
    )


def test_scan_dangling_refs_sentence_break_skips_decimals_and_abbreviations():
    """Decimals (``4.2``), version numbers (``v1.0``), and short abbreviations
    (``e.g.``, ``Dr.``) must NOT count as sentence boundaries — the lookbehind
    of 3+ alphabetic characters rules them out. Otherwise a historical phrase
    preceding such a token gets discarded and the backtick after it is wrongly
    flagged as dangling.

    Round-7 review finding #1 (regression from round 6's sentence-break
    tightening).
    """
    activities = [
        _entry(
            "2026-09-spec",
            description="Originally tracked under section 4.2 of `2024-old-spec`.",
        ),
        _entry(
            "2026-09-version",
            description="Originally tracked under v1.0 of the `2024-foo-bar` schema.",
        ),
        _entry(
            "2026-09-eg",
            description="Originally tracked e.g. under the legacy `2024-baz` index.",
        ),
    ]
    ids = {"2026-09-spec", "2026-09-version", "2026-09-eg"}
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-old-spec" not in refs, (
        f"decimal '4.2' must not act as a sentence boundary: {findings}"
    )
    assert "2024-foo-bar" not in refs, (
        f"version 'v1.0' must not act as a sentence boundary: {findings}"
    )
    assert "2024-baz" not in refs, (
        f"abbreviation 'e.g.' must not act as a sentence boundary: {findings}"
    )


def test_scan_dangling_refs_does_not_suppress_on_bare_named():
    """The bare ``named`` alternative (``originally named``, ``previously
    named``, ``formerly named`` without an ``as`` completer) must NOT
    suppress unrelated dangling refs. Round-7 review finding #2: the
    ``named`` form was overlooked when round-5 tightened the other
    completers.
    """
    activities = [
        _entry(
            "2026-09-haste",
            description=(
                "The helper was originally named in haste during the spike. "
                "See `2024-broken-ref` for the rewrite."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-haste"}, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-broken-ref" in refs, (
        f"bare 'originally named' (no 'as') must not hide a dangling ref: {findings}"
    )


def test_scan_dangling_refs_still_skips_named_as_with_completer():
    """The tightened ``named as`` form must still be recognized as
    historical context."""
    activities = [
        _entry(
            "2026-09-renamed-as",
            description="Originally named as `2024-old-name` in the proposal.",
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-renamed-as"}, text_fields=_DESC)
    assert findings == [], f"explicit 'named as' form was wrongly flagged: {findings}"


def test_scan_dangling_refs_consolidat_noun_does_not_match():
    """``consolidation into a single index`` is generic prose, not historical
    provenance. The over-broad ``consolidat\\w*`` quantifier was tightened
    to past-tense finite forms (``consolidate[ds]?``) so noun usage no
    longer blanket-suppresses unrelated dangling refs.

    v1.7.1 follow-up (round-5 review #4).
    """
    activities = [
        _entry(
            "2026-09-noun",
            description=(
                "The schema consolidation into a single index improved query speed. "
                "See `2024-broken-ref` for the implementation."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-noun"}, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-broken-ref" in refs, (
        f"'consolidation into' (noun) must not act as historical context: {findings}"
    )


def test_scan_dangling_refs_merged_into_main_does_not_match():
    """``merged into main`` is generic dev/git prose, not historical
    provenance. The ``merged\\s+(?:from|into)`` alternation was tightened
    to just ``merged\\s+from`` (the merge-history use case).

    v1.7.1 follow-up (round-5 review #4).
    """
    activities = [
        _entry(
            "2026-09-into-main",
            description=(
                "This branch was merged into main last week. "
                "Compare with `2024-broken-ref` for the alternative approach."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-into-main"}, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-broken-ref" in refs, (
        f"'merged into main' must not act as historical context: {findings}"
    )


def test_scan_dangling_refs_consolidated_from_still_recognized():
    """The legitimate historical form ``Consolidated from`` is still
    recognized after the tightening — only the noun ``consolidation`` and
    the ``merged into`` alternatives were dropped."""
    activities = [
        _entry(
            "2026-09-merged-finite",
            description="Consolidated from `2024-source-a` and `2024-source-b`.",
        ),
        _entry(
            "2026-09-merged-from",
            description="Merged from `2024-old-x` before the cleanup.",
        ),
    ]
    ids = {"2026-09-merged-finite", "2026-09-merged-from"}
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC)
    assert findings == [], f"historical past-tense forms wrongly flagged: {findings}"


def test_scan_dangling_refs_multiline_continuation_list_handled():
    """A historical continuation list that wraps across a newline must
    still recognize the ``and`` / ``or`` continuation token, even though
    the leading whitespace before ``and`` is now a newline. The strip set
    was extended to include ``\\r\\n``.

    v1.7.1 follow-up (round-5 review #6).
    """
    activities = [
        _entry(
            "2026-09-wrap-list",
            description=("Originally tracked under `2024-old-a`,\n      and `2024-old-b`."),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-wrap-list"}, text_fields=_DESC)
    assert findings == [], f"line-wrapped 'and' continuation must keep historical scope: {findings}"


def test_scan_dangling_refs_stray_backtick_does_not_misroute_scope():
    """An unmatched stray backtick in the window (e.g. an unclosed inline
    code mid-edit, or a literal placeholder symbol) must not misroute the
    clause boundary. The look-back now scopes by matched backtick PAIRS,
    not raw rfind, so a lone backtick is ignored.

    v1.7.1 follow-up (round-5 #5 / round-7 #5).
    """
    activities = [
        _entry(
            "2026-09-stray",
            description=(
                "Originally tracked under ` (this is a placeholder symbol) "
                "the system at `2024-old-id`."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-stray"}, text_fields=_DESC)
    assert findings == [], f"stray backtick misrouted clause scope: {findings}"


def test_scan_dangling_refs_paragraph_break_still_scopes_phrase():
    """Round-5's sentence-scope intent (a phrase in a PRIOR sentence does
    not apply to a backtick in the next) still holds for the paragraph-
    break variant. ``\\n\\s*\\n`` between an unrelated historical phrase
    and a later backtick must scope the phrase out."""
    activities = [
        _entry(
            "2026-09-paragraph",
            description=(
                "The dataset was originally stored as CSV.\n\n"
                "See `2024-broken-ref` for migration notes."
            ),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-paragraph"}, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-broken-ref" in refs, "phrase in a prior paragraph must not suppress dangling ref"


def test_scan_dangling_refs_still_skips_originally_stored_as():
    """The tightened ``originally`` branch must still recognize the explicit
    completer forms (``originally stored as``, ``originally known as``,
    ``originally filed under``) as historical context."""
    activities = [
        _entry(
            "2026-09-archived",
            description=("Originally stored as `2024-archive-id` before the migration."),
        ),
    ]
    findings = scan_dangling_refs(activities, ids={"2026-09-archived"}, text_fields=_DESC)
    assert findings == [], f"explicit completer form was wrongly flagged: {findings}"


def test_scan_dangling_refs_does_not_suppress_on_bare_was_named_or_old_id():
    """The bare alternatives ``was named``, ``old id`` and ``former(ly) id``
    used to live in the historical-phrase regex. They were too generic —
    "the function was named X" or "the old id-pattern" in narrative prose
    would silently suppress a real dangling ref in the same window.

    Round-4 review finding: drop those bare alternatives. Authors with
    history notes still get coverage through the explicit forms
    (``previously named``, ``formerly tracked``, etc.).
    """
    activities = [
        _entry(
            "2026-09-narrative",
            description=(
                "The helper was named differently in early drafts. "
                "See `2024-broken-ref` for the latest review."
            ),
        ),
        _entry(
            "2026-09-narrative-2",
            description=(
                "We changed the old id-resolution path last quarter. "
                "Tracking under `2024-also-broken` now."
            ),
        ),
    ]
    ids = {"2026-09-narrative", "2026-09-narrative-2"}
    findings = scan_dangling_refs(activities, ids=ids, text_fields=_DESC)
    refs = {ref for _, ref, _ in findings}
    assert "2024-broken-ref" in refs, f"bare 'was named' must not hide a dangling ref: {findings}"
    assert "2024-also-broken" in refs, f"bare 'old id' must not hide a dangling ref: {findings}"


# ---------------------------------------------------------------------------
# extract_contacts — rolodex name-walk-back boundary behavior
# ---------------------------------------------------------------------------


def test_extract_contacts_stops_walk_back_at_semicolon_boundary():
    """A trailing ``;`` on a token preceded by real-name material (tokens
    with lowercase letters) marks the boundary BETWEEN clauses. The
    walk-back must stop at the `;` without consuming the institutional
    abbreviation in front of it. Otherwise ``ZX`` in
    ``... ZX; Bob Jones ...`` leaks into the next person's name as
    ``ZX Bob Jones``.

    Fixture uses back-to-back `;`-separated clauses without intervening
    parenthesised emails between the boundary and the next name, so the
    `;` is the only walk-back stop signal (no `(...)` barrier confounds
    the test)."""
    activities = [
        _entry(
            "2026-05-multi-author-paper",
            description=(
                "Co-author with Alice Garcia ZX; Bob Jones ZX; "
                "Carol Lee (carol@example.com) at the workshop"
            ),
        ),
    ]
    contacts = extract_contacts(activities)
    assert "carol@example.com" in contacts
    # The two preceding clauses' content must NOT leak into Carol's name.
    assert contacts["carol@example.com"]["names"] == {"Carol Lee"}
    # Explicit negatives at the assertion site (the bugs we're guarding
    # against would each produce one of these).
    assert "ZX Carol Lee" not in contacts["carol@example.com"]["names"]
    assert "Jones Carol Lee" not in contacts["carol@example.com"]["names"]


def test_extract_contacts_consumes_name_when_semicolon_is_on_first_token():
    """When a trailing ``;`` appears on the very first walk-back token
    AND the cleaned remainder contains a lowercase letter (i.e. is
    plausibly a real name word, not an acronym), the `;` is terminating
    the CURRENT author's clause. The name word itself is in this token
    (e.g. ``Bob Smith;``) and the walk-back must strip-and-consume the
    cleaned token, then continue back to pick up preceding name words.

    This is the inverse of the boundary case above and the silent
    regression a naive break-before-consume implementation would
    introduce."""
    activities = [
        _entry(
            "2026-05-trailing-semicolon",
            description="we met with Bob Smith; (bob@example.com) at the workshop.",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "bob@example.com" in contacts, (
        "trailing `;` on the name's last word must not drop the contact"
    )
    assert contacts["bob@example.com"]["names"] == {"Bob Smith"}


def test_extract_contacts_skips_trailing_acronym_to_find_name_behind_it():
    """Symmetric case to the leading-affiliation form. When the institutional
    abbreviation appears AFTER the name in source order, the walk-back
    encounters the acronym token first. The cleaned remainder has no
    lowercase letter, so it's NOT consumed as a name — but the walk-back
    must SKIP it (not break) and continue back to find the real name
    behind the affiliation.

    Without this skip, ``Bob Smith ZX; (email)`` was producing the same
    ``ZX``-prefix leak the PR is supposed to fix, just with the
    affiliation in trailing rather than leading position."""
    activities = [
        _entry(
            "2026-05-trailing-affiliation",
            description="we met with Bob Smith ZX; (bob@example.com) yesterday.",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "bob@example.com" in contacts
    assert contacts["bob@example.com"]["names"] == {"Bob Smith"}
    # The institutional abbreviation must NOT have leaked.
    assert "Bob Smith ZX" not in contacts["bob@example.com"]["names"]
    assert "ZX" not in " ".join(contacts["bob@example.com"]["names"])


def test_extract_contacts_combined_semicolon_boundary_and_period_invariant():
    """Combines both invariants in one fixture so the test actually
    exercises the new `;`-boundary branch alongside the "trailing `.` is
    NOT a boundary" guarantee. The walk-back hits ``Lab;`` (a real-name-
    shaped clause-end with lowercase, consumed), then ``A.`` (middle
    initial, not a boundary), then more name words. One assertion guards
    both axes."""
    activities = [
        _entry(
            "2026-05-combined-invariants",
            description="Smith Lab; Maria A. Smith (maria@example.edu)",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "maria@example.edu" in contacts
    # The full three-token name including the middle initial survives,
    # AND nothing from before the `;` (e.g. "Lab", "Smith") leaks in.
    assert contacts["maria@example.edu"]["names"] == {"Maria A. Smith"}


def test_extract_contacts_does_not_drop_contact_when_acronym_between_name_and_email():
    """Regression for the silent-contact-drop bug: when an institutional
    token sits BETWEEN the name and the email (``Name; Acronym (email)``),
    naive walk-back collects the acronym first, then breaks on the `;`
    boundary because name_parts is non-empty — leaving a single-token
    ``["ACM"]`` which fails the 2-word floor and silently drops the
    contact from the rolodex.

    The fix recognises that ``["ACM"]`` has no real-name material (no
    token with a lowercase letter) and treats the `;` not as a clause
    boundary to respect but as a marker that the real name is past it.
    The walk-back discards the suspect tokens, consumes the boundary
    token's cleaned form (``"Garcia"``), and continues back to pick up
    ``"Alice"``.

    The property under test: **a `;` boundary must never cause a contact
    to silently disappear**."""
    activities = [
        _entry(
            "2026-05-acronym-between-name-and-email",
            description="Alice Garcia; ACM (alice@example.com) reviewed it",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "alice@example.com" in contacts, (
        "boundary with suspect intervening tokens must not drop the contact"
    )
    assert contacts["alice@example.com"]["names"] == {"Alice Garcia"}


def test_extract_contacts_strips_multi_token_trailing_affiliation():
    """Regression for the multi-token-trailing-affiliation bug: when
    multiple acronym-shaped tokens trail the name before the `;`
    boundary, the single-token boundary skip isn't enough — subsequent
    acronym tokens re-enter the normal name-collection path and get
    appended verbatim.

    The post-loop cleanup strips leading-position (source-rightmost)
    all-caps tokens of length ≥2 from the collected ``name_parts``,
    so ``Bob Smith ZX YZ; (email)`` resolves to ``"Bob Smith"`` rather
    than ``"Bob Smith ZX"``. Single-letter tokens are preserved so
    initials in unusual positions don't get accidentally stripped."""
    activities = [
        _entry(
            "2026-05-multi-token-trailing-acronym",
            description="we met Bob Smith ZX YZ; (bob@example.com) yesterday",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "bob@example.com" in contacts
    assert contacts["bob@example.com"]["names"] == {"Bob Smith"}
    # Neither all-caps token may leak.
    assert "ZX" not in " ".join(contacts["bob@example.com"]["names"])
    assert "YZ" not in " ".join(contacts["bob@example.com"]["names"])


def test_extract_contacts_does_not_misattribute_when_role_word_terminates_clause():
    """Regression for the role-stop-word boundary leak. When a `;` token
    has a cleaned form that's a role word (``Director``, ``Manager``,
    ``Lead``) — lowercase letters present but rejected by
    ``_is_name_token`` because it's in ``_ROLE_STOP_WORDS`` — the
    boundary must be respected, not skip-continued. Otherwise the
    walk-back sweeps the prior clause's name tokens and wrongly
    attributes them to this email.

    The property under test: **a `;`-terminated role word is a real
    clause boundary; the walk-back must not cross it and misattribute
    the prior clause's name**."""
    activities = [
        _entry(
            "2026-05-role-stop-word-boundary",
            description="Carol Lee Director; (bob@example.com) emailed us",
        ),
    ]
    contacts = extract_contacts(activities)
    # Either bob@example.com has no entry (correct: we have no name for
    # bob, the prior clause described Carol Lee), or it's present but
    # NOT named "Carol Lee" (which would be misattribution).
    if "bob@example.com" in contacts:
        assert "Carol Lee" not in contacts["bob@example.com"]["names"], (
            "Carol Lee is the PRIOR clause's name, must not be misattributed"
        )
        assert "Carol Lee Director" not in contacts["bob@example.com"]["names"]


def test_extract_contacts_bare_semicolon_token_is_hard_boundary():
    """Regression for the bare-`;` cross-clause leak. A `;` surrounded by
    whitespace (``"... word ; word ..."``) tokenizes as a standalone
    ``;`` whose cleaned form is empty. The walk-back must treat empty-
    cleaned `;` tokens as a hard boundary, otherwise it falls through
    and silently sweeps the preceding clause's tokens.

    Real-world prevalence: typographical convention puts a space before
    `;` in many style guides; LaTeX bibliographies and CMS authoring
    tools also produce this whitespace pattern."""
    activities = [
        _entry(
            "2026-05-bare-semicolon-token",
            description="Alice Garcia Bob Smith ; (bob@example.com) at the workshop",
        ),
    ]
    contacts = extract_contacts(activities)
    # The bare `;` must stop the walk-back. Either bob@example.com has
    # no entry, or it does NOT carry the prior clause's content
    # ("Alice Garcia Bob Smith").
    if "bob@example.com" in contacts:
        assert "Alice Garcia Bob Smith" not in contacts["bob@example.com"]["names"]
        assert "Alice Garcia" not in contacts["bob@example.com"]["names"]


def test_extract_contacts_preserves_roman_numeral_name_suffix():
    """Regression for the Roman-numeral-suffix-stripping bug introduced
    by the post-loop all-caps strip. Generational suffixes like ``III``,
    ``IV`` look acronym-shaped (all-alpha + all-upper + length >= 2)
    but legitimately belong on a name. The ``_NAME_SUFFIXES`` allowlist
    excludes them from the strip."""
    activities = [
        _entry(
            "2026-05-roman-numeral-suffix",
            description="Bob Smith III (bob@example.com) reviewed the draft",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "bob@example.com" in contacts
    assert contacts["bob@example.com"]["names"] == {"Bob Smith III"}


def test_extract_contacts_preserves_suffix_carrying_trailing_semicolon():
    """Regression for the `;`-handler / `_NAME_SUFFIXES` interaction. When
    a legitimate name suffix (Roman numeral, generational, or all-caps
    degree credential) carries the trailing `;`, the boundary handler's
    all-caps branch must consult ``_NAME_SUFFIXES`` and consume the
    cleaned token rather than treating it as an institutional acronym
    and skip-continuing.

    Without the fix, the post-loop allowlist never sees the suffix because
    Case E discards it via ``name_parts.clear() + continue`` before the
    post-loop runs."""
    activities = [
        _entry(
            "2026-05-suffix-with-semicolon",
            description=("Bob Smith III; (bob@example.com), Carol Lee MD; (carol@example.com)"),
        ),
    ]
    contacts = extract_contacts(activities)
    assert contacts["bob@example.com"]["names"] == {"Bob Smith III"}
    assert contacts["carol@example.com"]["names"] == {"Carol Lee MD"}


def test_extract_contacts_preserves_suffix_with_trailing_affiliation_and_semicolon():
    """Sibling of the suffix-with-`;` case: when source order is
    ``Name Acronym Suffix;``, the suffix still hits the boundary branch
    first and must be preserved. The acronym between the name and the
    suffix should still be stripped by the post-loop.

    For ``Bob Smith ZX III; (email)``: walk-back hits ``III;`` first
    (consumed via the suffix branch), then ``ZX`` (appended as a name
    token under the normal path), then ``Smith``, ``Bob``. Post-loop
    strips ``ZX`` (leading-position all-caps, not in ``_NAME_SUFFIXES``)
    but leaves ``III`` because it IS in the allowlist."""
    activities = [
        _entry(
            "2026-05-suffix-with-trailing-acronym",
            description="we met Bob Smith ZX III; (bob@example.com) yesterday",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "bob@example.com" in contacts
    assert contacts["bob@example.com"]["names"] == {"Bob Smith III"}
    assert "ZX" not in " ".join(contacts["bob@example.com"]["names"])


def test_extract_contacts_case_d_clear_preserves_collected_name_suffixes():
    """When Case D (the boundary-with-real-name-cleaned branch) fires, its
    discard step must preserve any ``_NAME_SUFFIXES`` tokens already in
    ``name_parts`` — those aren't suspect acronyms; they're legitimate
    suffixes that happen to have been collected before the boundary.

    Fixture: ``Foo Smith; III (email)`` (unusual placement of III on the
    near-email side of the `;`, but the property still holds: III is a
    valid suffix and shouldn't be silently lost when Case D clears)."""
    activities = [
        _entry(
            "2026-05-case-d-preserves-suffix",
            description="Foo Smith; III (bob@example.com) at the meeting",
        ),
    ]
    contacts = extract_contacts(activities)
    if "bob@example.com" in contacts:
        names = contacts["bob@example.com"]["names"]
        # Whatever name resolves for bob, it must include "III" (which
        # was already collected before the `;` fired Case D's clear).
        assert any("III" in n for n in names), (
            f"III suffix should be preserved through Case D's clear; got {names}"
        )
