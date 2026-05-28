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


def test_add_docs_preserves_same_indent_list_style(sandbox):
    """Items written at the same indent as their parent key must stay valid.

    YAML accepts list items at the parent key's indent OR two deeper. The
    fixtures use the nested style, so this test first rewrites one entry's
    ``docs:`` block to use the flush style (items at the same column as
    ``docs:``), then runs ``add-docs`` and re-parses to confirm the file
    is still valid YAML and the new doc landed in the right place.
    """
    text = sandbox.activities.read_text()
    # Flatten the existing 6-space-indented items under this entry's docs:
    # to 4-space indent so they sit at the same column as `docs:` itself.
    needle = (
        "    docs:\n"
        "      - 'https://example.edu/courses/infosec-101'\n"
        "      - 'file:syllabus-infosec-101'\n"
    )
    flush = (
        "    docs:\n"
        "    - 'https://example.edu/courses/infosec-101'\n"
        '    - "file:syllabus-infosec-101"\n'
    )
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, flush))

    out, _, rc = sandbox.run("add-docs", "2024-03-intro-security-course", "file:new-doc-ref")
    assert rc == 0
    # Must re-parse cleanly — pre-fix this raised a ParserError.
    docs = sandbox.entry("2024-03-intro-security-course")["docs"]
    assert "file:new-doc-ref" in docs
    assert "file:syllabus-infosec-101" in docs


def test_add_tags_preserves_same_indent_list_style(sandbox):
    """add-tags must also match the existing item indent (parallel to add-docs)."""
    text = sandbox.activities.read_text()
    needle = "    tags:\n      - teaching\n      - cpe-primary\n      - course\n"
    flush = "    tags:\n    - teaching\n    - cpe-primary\n    - course\n"
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, flush))

    out, _, rc = sandbox.run("add-tags", "2024-03-intro-security-course", "flush-style")
    assert rc == 0
    tags = sandbox.entry("2024-03-intro-security-course")["tags"]
    assert "flush-style" in tags
    assert {"teaching", "cpe-primary", "course"} <= set(tags)


def test_add_docs_skips_blank_lines_before_items(sandbox):
    """A blank line between `docs:` and the first item must not truncate the scan.

    Pre-fix: the item-collection loop broke on the blank line, treated the
    list as empty, and inserted the new item between the parent key and the
    existing items — silently changing the list order and breaking duplicate
    detection.
    """
    text = sandbox.activities.read_text()
    needle = (
        "    docs:\n"
        "      - 'https://example.edu/courses/infosec-101'\n"
        "      - 'file:syllabus-infosec-101'\n"
    )
    with_blank = (
        "    docs:\n"
        "\n"
        "      - 'https://example.edu/courses/infosec-101'\n"
        "      - 'file:syllabus-infosec-101'\n"
    )
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_blank))

    out, _, rc = sandbox.run("add-docs", "2024-03-intro-security-course", "file:appended")
    assert rc == 0
    docs = sandbox.entry("2024-03-intro-security-course")["docs"]
    assert docs[-1] == "file:appended"
    assert docs[0] == "https://example.edu/courses/infosec-101"
    assert docs[1] == "file:syllabus-infosec-101"

    # Duplicate detection must work across the blank-line gap.
    out, _, rc = sandbox.run(
        "add-docs", "2024-03-intro-security-course", "file:syllabus-infosec-101"
    )
    assert rc == 0
    assert "No new docs" in out


def test_add_tags_skips_blank_lines_before_items(sandbox):
    """add-tags must also tolerate a blank line between `tags:` and the first item."""
    text = sandbox.activities.read_text()
    needle = "    tags:\n      - teaching\n      - cpe-primary\n      - course\n"
    with_blank = "    tags:\n\n      - teaching\n      - cpe-primary\n      - course\n"
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_blank))

    sandbox.run("add-tags", "2024-03-intro-security-course", "appended-tag")
    tags = sandbox.entry("2024-03-intro-security-course")["tags"]
    assert tags[-1] == "appended-tag"
    assert tags[:3] == ["teaching", "cpe-primary", "course"]

    out, _, rc = sandbox.run("add-tags", "2024-03-intro-security-course", "teaching")
    assert rc == 0
    assert "already present" in out.lower()


def test_add_docs_skips_comment_line_before_items(sandbox):
    """A YAML comment between `docs:` and the first item is also tolerated."""
    text = sandbox.activities.read_text()
    needle = (
        "    docs:\n"
        "      - 'https://example.edu/courses/infosec-101'\n"
        "      - 'file:syllabus-infosec-101'\n"
    )
    with_comment = (
        "    docs:\n"
        "      # canonical course materials\n"
        "      - 'https://example.edu/courses/infosec-101'\n"
        "      - 'file:syllabus-infosec-101'\n"
    )
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_comment))

    sandbox.run("add-docs", "2024-03-intro-security-course", "file:appended")
    docs = sandbox.entry("2024-03-intro-security-course")["docs"]
    assert docs[-1] == "file:appended"
    assert docs[0] == "https://example.edu/courses/infosec-101"


def test_remove_docs(sandbox):
    """remove-docs deletes a doc reference."""
    sandbox.run("add-docs", "2025-09-committee-service", "https://remove.example.com")
    out, _, rc = sandbox.run(
        "remove-docs", "2025-09-committee-service", "https://remove.example.com"
    )
    assert rc == 0
    assert "https://remove.example.com" not in sandbox.entry("2025-09-committee-service")["docs"]


def test_remove_tags_skips_blank_lines_before_items(sandbox):
    """remove-tags must tolerate a blank line between `tags:` and the first item.

    Pre-fix, the scan-loop broke on the blank line and returned ``Tags ... not
    found`` even when the tag was present further down. Parallel to the
    add-tags fix in the same PR.
    """
    text = sandbox.activities.read_text()
    needle = "    tags:\n      - teaching\n      - cpe-primary\n      - course\n"
    with_blank = "    tags:\n\n      - teaching\n      - cpe-primary\n      - course\n"
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_blank))

    out, _, rc = sandbox.run("remove-tags", "2024-03-intro-security-course", "teaching")
    assert rc == 0
    assert "teaching" not in sandbox.entry("2024-03-intro-security-course")["tags"]
    assert "cpe-primary" in sandbox.entry("2024-03-intro-security-course")["tags"]


def test_add_docs_skips_comment_between_items(sandbox):
    """A YAML comment between two items must not stop item collection short.

    Pins the inter-item gap behavior (the existing tests cover only the
    parent-key-to-first-item gap). Duplicate detection must still see items
    on both sides of the comment.
    """
    text = sandbox.activities.read_text()
    needle = (
        "    docs:\n"
        "      - 'https://example.edu/courses/infosec-101'\n"
        "      - 'file:syllabus-infosec-101'\n"
    )
    with_inter_comment = (
        "    docs:\n"
        "      - 'https://example.edu/courses/infosec-101'\n"
        "      # syllabus PDF\n"
        "      - 'file:syllabus-infosec-101'\n"
    )
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_inter_comment))

    # Re-adding either flanking item must be a no-op — the scan has to see both.
    out, _, rc = sandbox.run(
        "add-docs",
        "2024-03-intro-security-course",
        "https://example.edu/courses/infosec-101",
    )
    assert rc == 0
    assert "No new docs" in out

    out, _, rc = sandbox.run(
        "add-docs", "2024-03-intro-security-course", "file:syllabus-infosec-101"
    )
    assert rc == 0
    assert "No new docs" in out


def test_add_tags_skips_comment_line_before_items(sandbox):
    """add-tags must tolerate a `#` comment between `tags:` and the first item.

    Parity with the add-docs comment coverage. Pre-refactor, three separate
    loops meant each caller had its own implicit coverage; the helper now
    lives in one place, so each caller must pin its own regression test.
    """
    text = sandbox.activities.read_text()
    needle = "    tags:\n      - teaching\n      - cpe-primary\n      - course\n"
    with_comment = (
        "    tags:\n"
        "      # primary teaching tags\n"
        "      - teaching\n"
        "      - cpe-primary\n"
        "      - course\n"
    )
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_comment))

    sandbox.run("add-tags", "2024-03-intro-security-course", "appended-tag")
    tags = sandbox.entry("2024-03-intro-security-course")["tags"]
    assert tags[-1] == "appended-tag"
    assert tags[:3] == ["teaching", "cpe-primary", "course"]

    out, _, rc = sandbox.run("add-tags", "2024-03-intro-security-course", "teaching")
    assert rc == 0
    assert "already present" in out.lower()


def test_add_tags_skips_comment_between_items(sandbox):
    """Inter-item `#` comments must not truncate the add-tags scan."""
    text = sandbox.activities.read_text()
    needle = "    tags:\n      - teaching\n      - cpe-primary\n      - course\n"
    with_inter_comment = (
        "    tags:\n"
        "      - teaching\n"
        "      # promotion-and-tenure category\n"
        "      - cpe-primary\n"
        "      - course\n"
    )
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_inter_comment))

    out, _, rc = sandbox.run("add-tags", "2024-03-intro-security-course", "cpe-primary")
    assert rc == 0
    assert "already present" in out.lower()

    out, _, rc = sandbox.run("add-tags", "2024-03-intro-security-course", "course")
    assert rc == 0
    assert "already present" in out.lower()


def test_remove_tags_skips_comment_between_items(sandbox):
    """remove-tags must find items on both sides of an inter-item `#` comment."""
    text = sandbox.activities.read_text()
    needle = "    tags:\n      - teaching\n      - cpe-primary\n      - course\n"
    with_inter_comment = (
        "    tags:\n"
        "      - teaching\n"
        "      # promotion-and-tenure category\n"
        "      - cpe-primary\n"
        "      - course\n"
    )
    assert needle in text, "fixture layout changed; update this test"
    sandbox.activities.write_text(text.replace(needle, with_inter_comment))

    out, _, rc = sandbox.run("remove-tags", "2024-03-intro-security-course", "course")
    assert rc == 0
    tags = sandbox.entry("2024-03-intro-security-course")["tags"]
    assert "course" not in tags
    assert "teaching" in tags
    assert "cpe-primary" in tags


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


def test_changes_since_naive_date(sandbox):
    """changes --since with a bare date must not crash (regression).

    Ledger timestamps are tz-aware (`...Z`); a bare `--since 2020-01-01`
    parses naive, and comparing naive vs aware raised TypeError, making every
    --since query crash with empty output. The fix normalizes naive values to
    UTC.
    """
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    out, _, rc = sandbox.run("changes", "--since", "2020-01-01")
    assert rc == 0
    assert "add-tags" in out
    assert "2026-04-self-study" in out


def test_changes_since_json_naive_date(sandbox):
    """The MCP path (changes --format json --since <naive>) must not crash."""
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    out, _, rc = sandbox.run("changes", "--format", "json", "--since", "2020-01-01")
    assert rc == 0
    parsed = json.loads(out)
    assert any(e["op"] == "add-tags" for e in parsed)


def test_changes_since_tz_aware(sandbox):
    """An explicit tz-aware --since still works."""
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    out, _, rc = sandbox.run("changes", "--since", "2020-01-01T00:00:00Z")
    assert rc == 0
    assert "add-tags" in out


def test_changes_since_future_excludes(sandbox):
    """A future --since matches nothing but exits cleanly."""
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    out, _, rc = sandbox.run("changes", "--since", "2099-01-01")
    assert rc == 0
    assert "2026-04-self-study" not in out


def test_changes_since_combined_with_op(sandbox):
    """--since composes with --op."""
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    sandbox.run("delete", "2026-01-security-workshop", "--confirm")
    out, _, rc = sandbox.run("changes", "--since", "2020-01-01", "--op", "delete")
    assert rc == 0
    assert "2026-01-security-workshop" in out
    assert "add-tags" not in out


def test_changes_since_invalid(sandbox):
    """An unparseable --since is a clean error, not a crash."""
    sandbox.run("add-tags", "2026-04-self-study", "t1")
    out, _, rc = sandbox.run("changes", "--since", "not-a-date")
    assert rc == 1
    assert "cannot parse" in out.lower()


# ---------------------------------------------------------------------------
# --changed-since / --changed-until (ledger-derived) on filter / list / search
# ---------------------------------------------------------------------------


def test_filter_changed_since_excludes_unledgered(sandbox):
    """A fresh sandbox has no ledger, so --changed-since matches nothing.

    The fixture corpus has entries but no ledger lines (mirrors the user's
    bulk-imported pre-ledger corpus). Entries with no recorded change must be
    excluded whenever a --changed-since bound is set.
    """
    out, _, rc = sandbox.run("filter", "--changed-since", "2000-01-01", "--count")
    assert rc == 0
    assert out.strip() == "0"


def test_filter_changed_since_includes_after_write(sandbox):
    """Once an entry is written through the tool, it has a ledger record."""
    sandbox.run("add-tags", "2026-04-self-study", "marker")
    out, _, rc = sandbox.run("filter", "--changed-since", "2000-01-01", "--brief")
    assert rc == 0
    assert "2026-04-self-study" in out


def test_filter_changed_since_future_excludes(sandbox):
    """A future cutoff excludes a just-written entry."""
    sandbox.run("add-tags", "2026-04-self-study", "marker")
    out, _, rc = sandbox.run("filter", "--changed-since", "2099-01-01", "--count")
    assert rc == 0
    assert out.strip() == "0"


def test_filter_changed_until_includes_and_excludes(sandbox):
    """--changed-until keeps entries last touched at/before the bound."""
    sandbox.run("add-tags", "2026-04-self-study", "marker")
    out, _, rc = sandbox.run("filter", "--changed-until", "2099-01-01", "--brief")
    assert rc == 0
    assert "2026-04-self-study" in out
    out, _, rc = sandbox.run("filter", "--changed-until", "2000-01-01", "--count")
    assert rc == 0
    assert out.strip() == "0"


def test_filter_changed_since_with_tag_since_last_pull(sandbox):
    """The headline use case: tag + changed-since to find what to export.

    Only the entry that (a) carries the tag AND (b) was changed through the
    tool after the cutoff should appear.
    """
    sandbox.run("add-tags", "2026-04-self-study", "synced-marker")
    out, _, rc = sandbox.run(
        "filter", "--tag", "synced-marker", "--changed-since", "2000-01-01", "--brief"
    )
    assert rc == 0
    assert "2026-04-self-study" in out
    # A different entry with the same recency but without the tag is excluded.
    sandbox.run("add-tags", "2025-02-conference-talk", "other-marker")
    out, _, rc = sandbox.run(
        "filter", "--tag", "synced-marker", "--changed-since", "2000-01-01", "--count"
    )
    assert rc == 0
    assert out.strip() == "1"


def test_filter_changed_since_invalid(sandbox):
    """An unparseable --changed-since is a clean error."""
    out, _, rc = sandbox.run("filter", "--changed-since", "nope")
    assert rc == 1
    assert "cannot parse" in out.lower()


def test_list_changed_since(sandbox):
    """list honors --changed-since."""
    sandbox.run("add-tags", "2026-04-self-study", "marker")
    out, _, rc = sandbox.run("list", "--changed-since", "2000-01-01")
    assert rc == 0
    assert "2026-04-self-study" in out
    out, _, rc = sandbox.run("list", "--changed-since", "2099-01-01")
    assert rc == 0
    assert "Total entries: 0" in out


def test_search_changed_since(sandbox):
    """search honors --changed-since, intersecting with the text query."""
    sandbox.run("add-tags", "2026-04-self-study", "marker")
    # Match-all-ish query; restrict to recently-changed.
    out, _, rc = sandbox.run("search", "self-study", "--changed-since", "2000-01-01")
    assert rc == 0
    assert "2026-04-self-study" in out
    out, _, rc = sandbox.run("search", "self-study", "--changed-since", "2099-01-01")
    assert rc == 0
    assert "Found 0 entries" in out


def test_search_changed_since_invalid(sandbox):
    """search surfaces a bad --changed-since as an error."""
    out, _, rc = sandbox.run("search", "anything", "--changed-since", "nope")
    assert rc == 1
    assert "cannot parse" in out.lower()
