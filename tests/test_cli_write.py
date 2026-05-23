"""CLI write-command tests, run against copies of the synthetic fixtures.

Every test works on an isolated sandbox copy, so the committed fixtures are
never mutated and tests never interfere with each other.
"""

from __future__ import annotations

import json


def _line_count(path) -> int:
    """Return the number of lines in a file."""
    return len(path.read_text().splitlines())


# ---------------------------------------------------------------------------
# add-tags / remove-tags
# ---------------------------------------------------------------------------


def test_add_tags_basic(sandbox):
    """add-tags appends a new tag and preserves entry count."""
    before = len(sandbox.load_activities())
    out, _, rc = sandbox.run("add-tags", "2024-06-security-curriculum-report", "fresh-tag")
    assert rc == 0
    assert "Added" in out
    assert len(sandbox.load_activities()) == before
    assert "fresh-tag" in sandbox.entry("2024-06-security-curriculum-report")["tags"]


def test_add_tags_duplicate_is_noop(sandbox):
    """Adding an existing tag is a no-op and does not change the file."""
    before = _line_count(sandbox.activities)
    out, _, rc = sandbox.run("add-tags", "2024-06-security-curriculum-report", "report")
    assert rc == 0
    assert "already present" in out.lower()
    assert _line_count(sandbox.activities) == before


def test_add_tags_multiple(sandbox):
    """add-tags can add several tags at once."""
    sandbox.run("add-tags", "2025-02-conference-talk", "ta", "tb", "tc")
    tags = sandbox.entry("2025-02-conference-talk")["tags"]
    assert {"ta", "tb", "tc"} <= set(tags)


def test_add_tags_preserves_other_entries(sandbox):
    """Editing one entry leaves a different entry untouched."""
    before = sandbox.entry("2025-04-journal-article")
    sandbox.run("add-tags", "2024-06-security-curriculum-report", "isolated")
    assert sandbox.entry("2025-04-journal-article") == before


def test_add_tags_unknown_entry(sandbox):
    """add-tags on an unknown entry fails without changing the file."""
    before = _line_count(sandbox.activities)
    out, _, rc = sandbox.run("add-tags", "no-such-entry", "x")
    assert rc == 1
    assert "not found" in out.lower()
    assert _line_count(sandbox.activities) == before


def test_add_tags_requires_label(sandbox):
    """A write command with no session label is rejected."""
    out, _, rc = sandbox.run(
        "add-tags", "2025-02-conference-talk", "x", extra_env={"LIBRARIAN_SESSION_LABEL": ""}
    )
    assert rc == 1
    assert "require" in out.lower()


def test_remove_tags(sandbox):
    """remove-tags drops a tag while keeping the others."""
    sandbox.run("add-tags", "2025-02-conference-talk", "removable")
    out, _, rc = sandbox.run("remove-tags", "2025-02-conference-talk", "removable")
    assert rc == 0
    assert "removable" not in sandbox.entry("2025-02-conference-talk")["tags"]
    assert "conference" in sandbox.entry("2025-02-conference-talk")["tags"]


def test_remove_tags_missing(sandbox):
    """Removing an absent tag is a safe no-op."""
    out, _, rc = sandbox.run("remove-tags", "2025-02-conference-talk", "never-there")
    assert rc == 0
    assert "not found" in out.lower()


# ---------------------------------------------------------------------------
# update-field
# ---------------------------------------------------------------------------


def test_update_field_date(sandbox):
    """update-field changes the date field."""
    out, _, rc = sandbox.run("update-field", "2025-02-conference-talk", "date", "2025-02-21")
    assert rc == 0
    assert sandbox.entry("2025-02-conference-talk")["date"] == "2025-02-21"


def test_update_field_title(sandbox):
    """update-field changes the title field."""
    sandbox.run("update-field", "2025-02-conference-talk", "title", "New Talk Title")
    assert sandbox.entry("2025-02-conference-talk")["title"] == "New Talk Title"


def test_update_field_adds_end_date(sandbox):
    """update-field can add an end_date that did not exist."""
    sandbox.run("update-field", "2025-02-conference-talk", "end_date", "2025-02-22")
    assert sandbox.entry("2025-02-conference-talk")["end_date"] == "2025-02-22"


def test_update_field_rejects_unsafe_field(sandbox):
    """update-field refuses unsupported fields."""
    out, _, rc = sandbox.run("update-field", "2025-02-conference-talk", "description", "x")
    assert rc == 1
    assert "not supported" in out


def test_update_field_quotes_value_with_apostrophe(sandbox):
    """A title with an apostrophe round-trips correctly."""
    sandbox.run("update-field", "2025-02-conference-talk", "title", "Jordan's Big Talk")
    assert sandbox.entry("2025-02-conference-talk")["title"] == "Jordan's Big Talk"


# ---------------------------------------------------------------------------
# update-description / update-notes
# ---------------------------------------------------------------------------


def test_update_description(sandbox):
    """update-description replaces the description from stdin."""
    out, _, rc = sandbox.run(
        "update-description", "2025-09-committee-service", stdin="A brand new description."
    )
    assert rc == 0
    assert sandbox.entry("2025-09-committee-service")["description"].strip() == (
        "A brand new description."
    )


def test_update_description_multiline_preserved(sandbox):
    """A multi-paragraph description keeps its blank-line break."""
    text = "First paragraph.\n\nSecond paragraph."
    sandbox.run("update-description", "2025-09-committee-service", stdin=text)
    desc = sandbox.entry("2025-09-committee-service")["description"]
    assert "First paragraph." in desc
    assert "Second paragraph." in desc


def test_update_description_preserves_entry_count(sandbox):
    """update-description does not add or drop entries."""
    before = len(sandbox.load_activities())
    sandbox.run("update-description", "2025-09-committee-service", stdin="short")
    assert len(sandbox.load_activities()) == before


def test_update_notes_default_block(sandbox):
    """update-notes targets the schema's first block (ptr) by default."""
    out, _, rc = sandbox.run(
        "update-notes", "2025-02-conference-talk", stdin="Revised ptr rationale."
    )
    assert rc == 0
    assert sandbox.entry("2025-02-conference-talk")["ptr"]["notes"] == ("Revised ptr rationale.")


def test_update_notes_explicit_block(sandbox):
    """update-notes --block targets a named block's notes field."""
    out, _, rc = sandbox.run(
        "update-notes",
        "2025-02-conference-talk",
        "--block",
        "cpe",
        stdin="Revised cpe rationale.",
    )
    assert rc == 0
    assert sandbox.entry("2025-02-conference-talk")["cpe"]["notes"] == ("Revised cpe rationale.")
    # The ptr notes must be unchanged.
    assert "peer-reviewed conference" in sandbox.entry("2025-02-conference-talk")["ptr"]["notes"]


def test_update_notes_with_embedded_quotes(sandbox):
    """Notes containing both quote kinds round-trip correctly."""
    text = 'He said "hello" and it\'s fine.'
    sandbox.run("update-notes", "2025-02-conference-talk", stdin=text)
    assert sandbox.entry("2025-02-conference-talk")["ptr"]["notes"] == text


# ---------------------------------------------------------------------------
# update-nested-field (schema-validated)
# ---------------------------------------------------------------------------


def test_update_nested_field_replaces_enum(sandbox):
    """update-nested-field replaces a valid enum value."""
    out, _, rc = sandbox.run(
        "update-nested-field", "2025-02-conference-talk", "ptr.subcategory", "cat3-other"
    )
    assert rc == 0
    assert sandbox.entry("2025-02-conference-talk")["ptr"]["subcategory"] == "cat3-other"


def test_update_nested_field_replaces_int(sandbox):
    """update-nested-field replaces an int field, rendered bare."""
    sandbox.run("update-nested-field", "2025-02-conference-talk", "cpe.credits", "15")
    assert sandbox.entry("2025-02-conference-talk")["cpe"]["credits"] == 15


def test_update_nested_field_replaces_bool(sandbox):
    """update-nested-field replaces a bool field, rendered bare."""
    sandbox.run("update-nested-field", "2025-02-conference-talk", "cpe.submitted", "true")
    assert sandbox.entry("2025-02-conference-talk")["cpe"]["submitted"] is True


def test_update_nested_field_rejects_bad_enum(sandbox):
    """update-nested-field rejects an out-of-set enum value."""
    out, _, rc = sandbox.run(
        "update-nested-field", "2025-02-conference-talk", "ptr.category", "nonsense"
    )
    assert rc == 1
    assert "INVALID" in out


def test_update_nested_field_rejects_bad_dependent_enum(sandbox):
    """update-nested-field rejects a subcategory invalid for the category."""
    out, _, rc = sandbox.run(
        "update-nested-field", "2025-02-conference-talk", "ptr.subcategory", "advising"
    )
    assert rc == 1
    assert "INVALID" in out


def test_update_nested_field_rejects_unknown_path(sandbox):
    """update-nested-field rejects a block/field the schema does not declare."""
    out, _, rc = sandbox.run("update-nested-field", "2025-02-conference-talk", "ptr.bogus", "x")
    assert rc == 1
    assert "not declared" in out


def test_update_nested_field_rejects_missing_block(sandbox):
    """update-nested-field rejects updating a block the entry lacks."""
    out, _, rc = sandbox.run(
        "update-nested-field", "2026-04-self-study", "ptr.category", "teaching"
    )
    assert rc == 1
    assert "not present" in out


def test_update_nested_field_rejects_non_int(sandbox):
    """update-nested-field rejects a non-integer for an int field."""
    out, _, rc = sandbox.run(
        "update-nested-field", "2025-02-conference-talk", "cpe.credits", "lots"
    )
    assert rc == 1
    assert "not an integer" in out or "ERROR" in out


def test_update_nested_field_nullable_date(sandbox):
    """update-nested-field accepts 'null' for a date? field."""
    out, _, rc = sandbox.run(
        "update-nested-field", "2024-03-intro-security-course", "cpe.submission_date", "null"
    )
    assert rc == 0
    assert sandbox.entry("2024-03-intro-security-course")["cpe"]["submission_date"] is None


# ---------------------------------------------------------------------------
# add-docs / remove-docs
# ---------------------------------------------------------------------------


def test_add_docs(sandbox):
    """add-docs appends a doc reference."""
    out, _, rc = sandbox.run("add-docs", "2025-09-committee-service", "https://example.com/new-doc")
    assert rc == 0
    assert "https://example.com/new-doc" in sandbox.entry("2025-09-committee-service")["docs"]


def test_add_docs_no_duplicates(sandbox):
    """Adding an existing doc is a no-op."""
    sandbox.run("add-docs", "2025-09-committee-service", "https://uniq.example.com")
    out, _, rc = sandbox.run("add-docs", "2025-09-committee-service", "https://uniq.example.com")
    assert rc == 0
    assert "No new docs" in out


def test_remove_docs(sandbox):
    """remove-docs deletes a doc reference."""
    sandbox.run("add-docs", "2025-09-committee-service", "https://remove.example.com")
    out, _, rc = sandbox.run(
        "remove-docs", "2025-09-committee-service", "https://remove.example.com"
    )
    assert rc == 0
    assert "https://remove.example.com" not in sandbox.entry("2025-09-committee-service")["docs"]


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def _new_entry(**overrides) -> dict:
    """Build a minimal valid new-entry mapping for create tests."""
    base = {
        "id": "2026-07-new-activity",
        "date": "2026-07-01",
        "title": "A New Activity",
        "description": "Description of the new activity.",
        "tags": ["new", "test"],
        "docs": ["https://example.com/new"],
        "ptr": {
            "category": "service",
            "subcategory": "external-professional",
            "notes": "Service rationale.",
        },
    }
    base.update(overrides)
    return base


def test_create_basic(sandbox):
    """create adds a new entry that round-trips through the parser."""
    before = len(sandbox.load_activities())
    out, _, rc = sandbox.run("create", stdin=json.dumps(_new_entry()))
    assert rc == 0
    assert "Created entry" in out
    assert len(sandbox.load_activities()) == before + 1
    entry = sandbox.entry("2026-07-new-activity")
    assert entry["title"] == "A New Activity"
    assert entry["ptr"]["category"] == "service"


def test_create_with_cpe_block(sandbox):
    """create accepts and writes a cpe block."""
    data = _new_entry(
        id="2026-07-with-cpe",
        cpe={
            "group": "primary",
            "credits": 5,
            "domain": "X",
            "submitted": False,
            "submission_date": None,
            "notes": "n",
        },
    )
    out, _, rc = sandbox.run("create", stdin=json.dumps(data))
    assert rc == 0
    assert sandbox.entry("2026-07-with-cpe")["cpe"]["credits"] == 5


def test_create_rejects_duplicate_id(sandbox):
    """create refuses an id that already exists."""
    out, _, rc = sandbox.run("create", stdin=json.dumps(_new_entry(id="2025-02-conference-talk")))
    assert rc == 1
    assert "already exists" in out


def test_create_rejects_missing_required(sandbox):
    """create refuses an entry missing a required core field."""
    data = _new_entry()
    del data["title"]
    out, _, rc = sandbox.run("create", stdin=json.dumps(data))
    assert rc == 1
    assert "missing required" in out.lower()


def test_create_rejects_bad_schema_block(sandbox):
    """create validates schema blocks and refuses an invalid one."""
    data = _new_entry(
        id="2026-07-bad-block",
        ptr={"category": "teaching", "subcategory": "cat1-peer-reviewed"},
    )
    out, _, rc = sandbox.run("create", stdin=json.dumps(data))
    assert rc == 1
    assert "schema validation failed" in out


def test_create_dry_run(sandbox):
    """create --dry-run previews without writing."""
    before = len(sandbox.load_activities())
    out, _, rc = sandbox.run("create", "--dry-run", stdin=json.dumps(_new_entry()))
    assert rc == 0
    assert "Dry run" in out
    assert len(sandbox.load_activities()) == before


def test_create_preserves_existing_entries(sandbox):
    """Creating a new entry leaves existing entries intact."""
    before = sandbox.entry("2024-03-intro-security-course")
    sandbox.run("create", stdin=json.dumps(_new_entry()))
    assert sandbox.entry("2024-03-intro-security-course") == before


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_dry_run(sandbox):
    """delete without --confirm is a dry run."""
    before = len(sandbox.load_activities())
    out, _, rc = sandbox.run("delete", "2026-04-self-study")
    assert rc == 0
    assert "Dry run" in out
    assert len(sandbox.load_activities()) == before


def test_delete_confirmed(sandbox):
    """delete --confirm removes the entry."""
    before = len(sandbox.load_activities())
    out, _, rc = sandbox.run("delete", "2026-04-self-study", "--confirm")
    assert rc == 0
    assert len(sandbox.load_activities()) == before - 1
    ids = {e["id"] for e in sandbox.load_activities()}
    assert "2026-04-self-study" not in ids


def test_delete_unknown(sandbox):
    """delete on an unknown entry fails cleanly."""
    out, _, rc = sandbox.run("delete", "no-such-entry", "--confirm")
    assert rc == 1
    assert "not found" in out.lower()


# ---------------------------------------------------------------------------
# rename-id
# ---------------------------------------------------------------------------


def test_rename_id_basic(sandbox):
    """rename-id changes an entry's id."""
    out, _, rc = sandbox.run("rename-id", "2026-04-self-study", "2026-04-cloud-self-study")
    assert rc == 0
    ids = {e["id"] for e in sandbox.load_activities()}
    assert "2026-04-cloud-self-study" in ids
    assert "2026-04-self-study" not in ids


def test_rename_id_repoints_backtick_refs(sandbox):
    """rename-id repoints backticked cross-references in other entries."""
    # 2024-06 references 2024-03 by backtick; rename the target.
    out, _, rc = sandbox.run("rename-id", "2024-03-intro-security-course", "2024-03-renamed-course")
    assert rc == 0
    assert "repointed" in out
    desc = sandbox.entry("2024-06-security-curriculum-report")["description"]
    assert "`2024-03-renamed-course`" in desc
    assert "`2024-03-intro-security-course`" not in desc


def test_rename_id_repoints_plain_text_refs(sandbox):
    """rename-id repoints plain-text cross-references, not just backticked ones."""
    # Inject a plain-text mention of the target id into another entry's ptr.notes.
    sandbox.run(
        "update-notes",
        "2025-02-conference-talk",
        stdin="See 2024-03-intro-security-course for the underlying coursework.",
    )
    out, _, rc = sandbox.run("rename-id", "2024-03-intro-security-course", "2024-03-renamed-course")
    assert rc == 0
    notes = sandbox.entry("2025-02-conference-talk")["ptr"]["notes"]
    assert "2024-03-renamed-course" in notes
    assert "2024-03-intro-security-course" not in notes


def test_rename_id_respects_token_boundaries(sandbox):
    """A rename must not touch a longer id-shaped string that begins with the
    old id. This is the safety property that makes plain-text repointing OK."""
    sentinel = "2024-03-intro-security-course-extension"
    sandbox.run(
        "update-notes",
        "2025-02-conference-talk",
        stdin=f"See {sentinel} for an unrelated follow-on.",
    )
    out, _, rc = sandbox.run("rename-id", "2024-03-intro-security-course", "2024-03-renamed-course")
    assert rc == 0
    notes = sandbox.entry("2025-02-conference-talk")["ptr"]["notes"]
    # The longer id-shaped sentinel must be left untouched.
    assert sentinel in notes, f"longer id-shaped string was modified: {notes!r}"


def test_rename_id_rejects_existing(sandbox):
    """rename-id refuses a target id that already exists."""
    out, _, rc = sandbox.run("rename-id", "2026-04-self-study", "2025-02-conference-talk")
    assert rc == 1
    assert "already exists" in out


def test_rename_id_rejects_invalid_slug(sandbox):
    """rename-id refuses an id that is not a valid slug."""
    out, _, rc = sandbox.run("rename-id", "2026-04-self-study", "Bad ID!")
    assert rc == 1
    assert "not a valid id" in out


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------


def test_ledger_records_writes(sandbox):
    """Every write appends an attributed line to the change ledger."""
    sandbox.run("add-tags", "2026-04-self-study", "ledger-test")
    assert sandbox.ledger.exists()
    text = sandbox.ledger.read_text()
    assert "add-tags" in text
    assert "label=test:suite" in text


def test_changes_command(sandbox):
    """changes reports ledger entries; --op filters by operation."""
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    sandbox.run("delete", "2026-01-security-workshop", "--confirm")
    out, _, rc = sandbox.run("changes", "--op", "delete")
    assert rc == 0
    assert "2026-01-security-workshop" in out
    assert "add-tags" not in out


def test_changes_json(sandbox):
    """changes --format json bundles current entry state."""
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    out, _, rc = sandbox.run("changes", "--format", "json")
    assert rc == 0
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed[-1]["op"] == "add-tags"
