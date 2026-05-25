"""Unit tests for ``librarian.core`` helpers."""

from __future__ import annotations

from librarian.core import scan_dangling_refs


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
