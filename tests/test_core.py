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
