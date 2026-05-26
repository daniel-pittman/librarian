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
    """A trailing ``;`` on a token *after* name parts have been collected
    marks the end of the previous clause. The walk-back must stop there
    without consuming the token. Otherwise an institutional abbreviation
    like ``ZX`` in ``... ZX; Bob Jones (bob@example.com)`` gets pulled
    into the next person's name as ``ZX Bob Jones`` -- a real bug class
    that surfaces when an entry description lists collaborators in a
    semicolon-separated affiliation list.

    The fixture deliberately avoids em-dashes and other punctuation
    between the author clauses so the test exercises only the `;`
    boundary, not adjacent separators."""
    activities = [
        _entry(
            "2026-05-multi-author-paper",
            description=(
                "Co-author with Alice Garcia (alice@example.com) ZX; "
                "Bob Jones (bob@example.com) ZX; "
                "Carol Lee (carol@example.com) YW"
            ),
        ),
    ]
    contacts = extract_contacts(activities)

    # All three emails resolve.
    assert set(contacts) == {
        "alice@example.com",
        "bob@example.com",
        "carol@example.com",
    }

    # The key assertions: the institutional abbreviation preceding each
    # name (after a `;`) must NOT leak into the extracted display name.
    assert contacts["bob@example.com"]["names"] == {"Bob Jones"}
    assert contacts["carol@example.com"]["names"] == {"Carol Lee"}

    # Explicit negative assertion to make the regression intent visible
    # at the assertion site (the bug we're guarding against produced
    # exactly this name).
    assert "ZX Bob Jones" not in contacts["bob@example.com"]["names"]
    assert "ZX Carol Lee" not in contacts["carol@example.com"]["names"]

    # Garcia is the first author — no semicolon precedes her, so she's
    # clean either way. Anchors the happy-path baseline.
    assert contacts["alice@example.com"]["names"] == {"Alice Garcia"}


def test_extract_contacts_consumes_name_when_semicolon_is_on_first_token():
    """When a trailing ``;`` appears on the very first walk-back token
    (no name parts collected yet), the `;` is terminating the CURRENT
    author's clause, not bounding a previous one. The name word itself
    is in this token (e.g. ``Bob Smith;``) and the walk-back must
    strip-and-consume the cleaned token, then continue back to pick up
    preceding name words.

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


def test_extract_contacts_preserves_initial_in_name_after_semicolon_fix():
    """The semicolon-boundary fix must NOT also block walk-back at a
    trailing ``.``: middle initials (``Maria A. Smith``) and titles
    (``Dr.``, ``Prof.``) legitimately end with a period. Anchors the
    "trailing `.` is not a clause boundary" invariant alongside the
    "trailing `;` IS a clause boundary" one tested above."""
    activities = [
        _entry(
            "2026-05-initial-test",
            description="Lead author Maria A. Smith (maria@example.edu) reports...",
        ),
    ]
    contacts = extract_contacts(activities)
    assert "maria@example.edu" in contacts
    # The full three-token name including the middle initial survives.
    assert "Maria A. Smith" in contacts["maria@example.edu"]["names"]
