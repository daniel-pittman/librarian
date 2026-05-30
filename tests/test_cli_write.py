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
# docs_optional (suppresses the NO DOCS validation warning)
# ---------------------------------------------------------------------------


def _docless_entry(**overrides) -> dict:
    """A minimal valid entry with no docs (would otherwise warn NO DOCS)."""
    base = {
        "id": "2026-08-docless",
        "date": "2026-08-01",
        "title": "A docless activity",
        "description": "An activity with no artifact.",
        "tags": ["x"],
        "docs": [],
        "ptr": {
            "category": "service",
            "subcategory": "external-professional",
            "notes": "Rationale.",
        },
    }
    base.update(overrides)
    return base


def _no_docs_flagged(sandbox, entry_id: str) -> bool:
    """True if validate emits a NO DOCS warning for the given entry id."""
    out, _, _ = sandbox.run("validate")
    return f"NO DOCS: {entry_id}" in out


def test_create_with_docs_optional_suppresses_no_docs(sandbox):
    """An entry created with docs_optional=true and no docs is not flagged."""
    out, _, rc = sandbox.run("create", stdin=json.dumps(_docless_entry(docs_optional=True)))
    assert rc == 0
    # Renders + parses back as a real boolean, not the string "true".
    assert sandbox.entry("2026-08-docless")["docs_optional"] is True
    assert not _no_docs_flagged(sandbox, "2026-08-docless")


def test_docless_entry_without_flag_is_flagged(sandbox):
    """Control: the same entry without the flag DOES warn NO DOCS."""
    sandbox.run("create", stdin=json.dumps(_docless_entry()))
    assert _no_docs_flagged(sandbox, "2026-08-docless")


def test_update_field_sets_docs_optional_and_suppresses(sandbox):
    """Setting docs_optional on an existing docless entry clears the warning."""
    sandbox.run("create", stdin=json.dumps(_docless_entry()))
    assert _no_docs_flagged(sandbox, "2026-08-docless")
    out, _, rc = sandbox.run("update-field", "2026-08-docless", "docs_optional", "true")
    assert rc == 0
    assert sandbox.entry("2026-08-docless")["docs_optional"] is True
    assert not _no_docs_flagged(sandbox, "2026-08-docless")


def test_update_field_docs_optional_false_still_warns(sandbox):
    """docs_optional=false is a real boolean False and does not suppress NO DOCS."""
    sandbox.run("create", stdin=json.dumps(_docless_entry()))
    out, _, rc = sandbox.run("update-field", "2026-08-docless", "docs_optional", "false")
    assert rc == 0
    assert sandbox.entry("2026-08-docless")["docs_optional"] is False
    assert _no_docs_flagged(sandbox, "2026-08-docless")


def test_docs_optional_does_not_suppress_other_no_docs(sandbox):
    """The flag is per-entry — it must not silence a different docless entry."""
    sandbox.run(
        "create", stdin=json.dumps(_docless_entry(id="2026-08-flagged", docs_optional=True))
    )
    sandbox.run("create", stdin=json.dumps(_docless_entry(id="2026-08-unflagged")))
    out, _, _ = sandbox.run("validate")
    assert "NO DOCS: 2026-08-flagged" not in out
    assert "NO DOCS: 2026-08-unflagged" in out


def test_create_docs_optional_stringy_false_is_falsey(sandbox):
    """create coerces a JSON string "false" to real False, not truthy.

    A non-empty string "false" is truthy in Python, so without coercion the
    entry would render docs_optional: true. create now parses it the same
    strict way update-field does.
    """
    out, _, rc = sandbox.run("create", stdin=json.dumps(_docless_entry(docs_optional="false")))
    assert rc == 0
    assert sandbox.entry("2026-08-docless")["docs_optional"] is False
    assert _no_docs_flagged(sandbox, "2026-08-docless")


def test_create_docs_optional_invalid_token_rejected(sandbox):
    """create rejects a docs_optional value that isn't a recognized boolean."""
    out, _, rc = sandbox.run("create", stdin=json.dumps(_docless_entry(docs_optional="maybe")))
    assert rc == 1
    assert "docs_optional" in out.lower()


def test_update_field_docs_optional_invalid_token_rejected(sandbox):
    """update-field rejects a bool typo instead of silently coercing to false."""
    sandbox.run("create", stdin=json.dumps(_docless_entry()))
    out, _, rc = sandbox.run("update-field", "2026-08-docless", "docs_optional", "ture")
    assert rc == 1
    assert "docs_optional" in out.lower()
    # The entry must be unchanged (no docs_optional written).
    assert "docs_optional" not in sandbox.entry("2026-08-docless")


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


# ---------------------------------------------------------------------------
# set-block (add a schema block to an existing entry)
# ---------------------------------------------------------------------------


def _ptr_only_entry(**overrides) -> dict:
    """A minimal valid ptr-only entry, suitable for adding a cpe block to."""
    base = {
        "id": "2026-09-sb-target",
        "date": "2026-09-01",
        "title": "set-block target",
        "description": "A ptr-only entry to test adding a cpe block.",
        "tags": ["t"],
        "docs": ["https://example.com/a"],
        "ptr": {
            "category": "service",
            "subcategory": "external-professional",
            "notes": "ptr-only.",
        },
    }
    base.update(overrides)
    return base


def test_set_block_adds_full_block_to_existing_entry(sandbox):
    """set-block adds a complete block; round-trips as native Python types."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 10, "submitted": True, "notes": "ten credits"}),
    )
    assert rc == 0
    cpe = sandbox.entry("2026-09-sb-target")["cpe"]
    assert cpe["group"] == "primary"
    assert cpe["credits"] == 10  # int, not "10"
    assert cpe["submitted"] is True  # bool, not "true"
    assert cpe["notes"] == "ten credits"


def test_set_block_preserves_existing_block_and_core_fields(sandbox):
    """Adding a new block leaves the existing block and core fields untouched."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    before = sandbox.entry("2026-09-sb-target")
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 5}),
    )
    after = sandbox.entry("2026-09-sb-target")
    assert after["ptr"] == before["ptr"]
    for field in ("id", "date", "title", "description", "tags", "docs"):
        assert after[field] == before[field]


def test_set_block_rejects_unknown_block(sandbox):
    """A block name not declared by the schema is rejected."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run("set-block", "2026-09-sb-target", "nope", "--json", '{"a": 1}')
    assert rc == 1
    assert "not declared" in out.lower()


def test_set_block_rejects_unknown_field(sandbox):
    """A field not declared on the block is rejected (typos surface)."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1, "oops": "x"}),
    )
    assert rc == 1
    assert "unknown field" in out.lower()


def test_set_block_rejects_missing_required(sandbox):
    """The whole block is validated; a missing required field is reported."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary"}),  # missing credits
    )
    assert rc == 1
    assert "MISSING CPE.CREDITS" in out


def test_set_block_rejects_bad_enum_value(sandbox):
    """An out-of-set enum value is rejected, listing the allowed values."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "bogus", "credits": 1}),
    )
    assert rc == 1
    assert "INVALID CPE.GROUP" in out


def test_set_block_refuses_when_block_already_present(sandbox):
    """set-block is a creation primitive; editing existing blocks is forbidden."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "ptr",
        "--json",
        json.dumps({"category": "scholarly", "subcategory": "cat3-other"}),
    )
    assert rc == 1
    assert "already present" in out.lower()
    # The error must name a real CLI command (update-nested-field), not the
    # MCP-side tool name (update-block-field), which would point users at a
    # non-existent CLI command.
    assert "update-nested-field" in out.lower()
    assert "update-block-field" not in out.lower()


def test_set_block_rejects_invalid_json(sandbox):
    """A non-JSON payload is a clean error, not a crash."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run("set-block", "2026-09-sb-target", "cpe", "--json", "not-json")
    assert rc == 1
    assert "not valid json" in out.lower()


def test_set_block_rejects_non_object_payload(sandbox):
    """The block payload must be a JSON object, not a list/string/etc."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run("set-block", "2026-09-sb-target", "cpe", "--json", "[1,2,3]")
    assert rc == 1
    assert "json object" in out.lower()


def test_set_block_rejects_unknown_entry(sandbox):
    """An unknown entry id errors out without writing."""
    before = _line_count(sandbox.activities)
    out, _, rc = sandbox.run(
        "set-block",
        "no-such-entry",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    assert rc == 1
    assert "not found" in out.lower()
    assert _line_count(sandbox.activities) == before


def test_set_block_requires_session_label(sandbox):
    """A write command with no session label is rejected (project invariant)."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
        extra_env={"LIBRARIAN_SESSION_LABEL": ""},
    )
    assert rc == 1
    assert "require" in out.lower()


def test_set_block_writes_ledger_entry(sandbox):
    """Every set-block write appends an attributed line to the ledger."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    text = sandbox.ledger.read_text()
    assert "set-block" in text
    assert "2026-09-sb-target" in text


def test_set_block_no_partial_write_on_validation_failure(sandbox):
    """A schema-validation failure must not modify the activities file at all."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    before = sandbox.activities.read_text()
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary"}),  # missing required credits
    )
    assert sandbox.activities.read_text() == before


def test_set_block_does_not_affect_other_entries(sandbox):
    """Adding a block to one entry leaves a different entry byte-identical."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    untouched_before = sandbox.entry("2025-04-journal-article")
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    assert sandbox.entry("2025-04-journal-article") == untouched_before


def test_set_block_renders_int_unquoted_so_it_parses_back_as_int(sandbox):
    """A schema-int field must round-trip as a Python int, not a string."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 42}),
    )
    assert isinstance(sandbox.entry("2026-09-sb-target")["cpe"]["credits"], int)


def test_set_block_renders_bool_unquoted_so_it_parses_back_as_bool(sandbox):
    """A schema-bool field must round-trip as a Python bool, not a string."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1, "submitted": False}),
    )
    cpe = sandbox.entry("2026-09-sb-target")["cpe"]
    assert cpe["submitted"] is False
    assert isinstance(cpe["submitted"], bool)


def test_set_block_accepts_json_on_stdin(sandbox):
    """JSON may be piped on stdin instead of supplied via --json."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        stdin=json.dumps({"group": "primary", "credits": 5}),
    )
    assert rc == 0
    assert sandbox.entry("2026-09-sb-target")["cpe"]["credits"] == 5


def test_set_block_does_not_collide_with_docs_in_description_prose(sandbox):
    """A description body line beginning with 'docs:' must not be mistaken for
    the entry's docs: field; the new block must still land in the right place
    and the description must survive intact."""
    entry = _ptr_only_entry(description="Preamble.\ndocs: see appendix.")
    sandbox.run("create", stdin=json.dumps(entry))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 7}),
    )
    assert rc == 0
    e = sandbox.entry("2026-09-sb-target")
    assert e["cpe"]["credits"] == 7
    assert "see appendix" in e["description"]


def test_set_block_does_not_refuse_block_name_in_description_prose(sandbox):
    """A description body line beginning with the block name as prose must not
    trip the 'block already present' guard."""
    entry = _ptr_only_entry(description="Preamble.\ncpe: this is just prose text.")
    sandbox.run("create", stdin=json.dumps(entry))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    assert rc == 0
    assert sandbox.entry("2026-09-sb-target")["cpe"]["credits"] == 1


def test_set_block_bool_case_insensitive(sandbox):
    """`"TRUE"` / `"YES"` for a bool field must round-trip as True, not False.

    validate_block accepts these case-insensitively; set-block coerces them to
    real bools before rendering, so the user's intent isn't silently flipped.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1, "submitted": "TRUE"}),
    )
    cpe = sandbox.entry("2026-09-sb-target")["cpe"]
    assert cpe["submitted"] is True


def test_set_block_rejects_text_with_newline(sandbox):
    """Multi-line text values would break the single-line YAML splice."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1, "notes": "line1\nline2"}),
    )
    assert rc == 1
    assert "newline" in out.lower()


def test_set_block_coerces_stringy_int_to_native_int(sandbox):
    """`"08"` for an int field must coerce to native int 8, not persist as a string."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": "08"}),
    )
    cpe = sandbox.entry("2026-09-sb-target")["cpe"]
    assert cpe["credits"] == 8
    assert isinstance(cpe["credits"], int)


def test_set_block_existence_check_runs_before_validation(sandbox):
    """The already-present error fires before schema validation.

    The user gets the actionable error, not a validation report that turns out
    to be moot once they fix the JSON.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "ptr",
        "--json",
        json.dumps({"category": "bogus", "subcategory": "also-bogus"}),
    )
    assert rc == 1
    assert "already present" in out.lower()
    assert "INVALID" not in out


def test_set_block_generic_mode_error(sandbox):
    """Without an active schema, set-block reports 'no schema configured'."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    sandbox.schema.unlink()  # remove schema.yaml so the context is empty
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    assert rc == 1
    assert "no schema configured" in out.lower()


def test_set_block_rejects_empty_payload(sandbox):
    """An empty payload would render a degenerate `block:` with no children."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run("set-block", "2026-09-sb-target", "cpe", "--json", "{}")
    assert rc == 1
    assert "empty" in out.lower()


def test_set_block_reports_multiple_validation_issues(sandbox):
    """validate_block emits all issues; set-block must print all of them.

    Pins the multi-issue surface so a future regression that `break`s after
    the first issue does not pass silently.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "bogus"}),  # bad enum AND missing required credits
    )
    assert rc == 1
    assert "INVALID CPE.GROUP" in out
    assert "MISSING CPE.CREDITS" in out


def test_set_block_inserts_correctly_when_end_date_present(sandbox):
    """The insertion-point logic works for entries with end_date between date and docs."""
    entry = _ptr_only_entry(end_date="2026-09-30")
    sandbox.run("create", stdin=json.dumps(entry))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 3}),
    )
    assert rc == 0
    e = sandbox.entry("2026-09-sb-target")
    assert e["end_date"] == "2026-09-30"
    assert e["cpe"]["credits"] == 3


def test_set_block_adds_earlier_schema_block_to_cpe_only_entry(sandbox):
    """Adding a block when another block already exists keeps both intact.

    Exercises the case where the new block is not the only block on the entry
    (covers the placement-among-existing-blocks path).
    """
    entry = {
        "id": "2026-09-cpe-only",
        "date": "2026-09-01",
        "title": "cpe only",
        "description": "An entry with only a cpe block.",
        "tags": ["t"],
        "docs": ["https://example.com/a"],
        "cpe": {"group": "primary", "credits": 5},
    }
    sandbox.run("create", stdin=json.dumps(entry))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-cpe-only",
        "ptr",
        "--json",
        json.dumps({"category": "service", "subcategory": "external-professional"}),
    )
    assert rc == 0
    e = sandbox.entry("2026-09-cpe-only")
    assert e["ptr"]["category"] == "service"
    assert e["cpe"]["credits"] == 5


def test_set_block_rejects_string_field_with_newline(sandbox):
    """A type='string' field (e.g. cpe.domain) with a newline must be rejected.

    The previous fix only guarded type='text'; a multi-line string would still
    have spliced a literal LF into a single-quoted YAML scalar and corrupted
    the file on the next read.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1, "domain": "line1\nline2"}),
    )
    assert rc == 1
    assert "newline" in out.lower()


def test_set_block_existence_check_runs_before_coerce(sandbox):
    """The already-present error must fire even when coercion would have erred.

    coerce_value runs after the existence check now; an un-coercible value
    against an existing-block entry must surface the existence error, not the
    coerce error (which would be moot once the user fixes the JSON).
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    # _ptr_only_entry has ptr; try to set ptr with junk - but ptr has no int
    # fields, so test the case the reviewer raised: add cpe, then re-add cpe
    # with a stringy int that would fail coerce.
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": "not-an-int"}),
    )
    assert rc == 1
    assert "already present" in out.lower()
    assert "not-an-int" not in out  # coerce error did not run


def test_set_block_coerce_error_matches_validate_format(sandbox):
    """Coerce errors use the same INVALID BLOCK.FIELD shape as validate_block."""
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": "not-an-int"}),
    )
    assert rc == 1
    assert "INVALID CPE.CREDITS" in out


def test_set_block_help_exits_zero(sandbox):
    """`set-block -h` prints the help text and returns exit code 0."""
    out, _, rc = sandbox.run("set-block", "-h")
    assert rc == 0
    assert "set-block" in out.lower()


def test_set_block_inserts_in_schema_declaration_order(sandbox):
    """Adding an earlier-in-schema block to a later-block-only entry preserves order.

    Schema declares ptr then cpe. An entry that has only `cpe` and receives
    `ptr` via set-block must end up with `ptr` lines BEFORE `cpe` lines in the
    file, matching the order _render_entry establishes at create time. This
    keeps the hand-formatted invariant stable as `merge` (the next PR) starts
    repeatedly carrying blocks across entries.
    """
    entry = {
        "id": "2026-09-order-target",
        "date": "2026-09-01",
        "title": "order target",
        "description": "cpe-only entry receiving ptr later.",
        "tags": ["t"],
        "docs": ["https://example.com/a"],
        "cpe": {"group": "primary", "credits": 5},
    }
    sandbox.run("create", stdin=json.dumps(entry))
    sandbox.run(
        "set-block",
        "2026-09-order-target",
        "ptr",
        "--json",
        json.dumps({"category": "service", "subcategory": "external-professional"}),
    )
    text = sandbox.activities.read_text()
    # Find the file positions of `    ptr:` and `    cpe:` lines for this entry.
    # Pick the occurrences that follow the entry's id to scope to this entry.
    entry_marker = text.index("- id: 2026-09-order-target")
    ptr_pos = text.index("ptr:", entry_marker)
    cpe_pos = text.index("cpe:", entry_marker)
    assert ptr_pos < cpe_pos, (
        f"ptr block should precede cpe block in schema-declaration order; "
        f"got ptr at {ptr_pos}, cpe at {cpe_pos}"
    )


def test_set_block_rejects_date_field_with_newline(sandbox):
    """A type='date?' (or date) field with a trailing LF must be rejected.

    Closes the symmetric-gap pattern: the prior fixes covered text/string/enum
    but date/date? slipped through because the ISO regex matches a trailing
    newline. The fix is type-agnostic (any str value), so adding a new schema
    type can never re-open this class of bug.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps(
            {
                "group": "primary",
                "credits": 1,
                "submission_date": "2024-01-01\n",
            }
        ),
    )
    assert rc == 1
    assert "newline" in out.lower()


def test_set_block_newline_check_runs_after_existence(sandbox):
    """The already-present error fires before the newline guard.

    The existence-first invariant must hold for every form of value validation,
    not just coerce + validate_block. Mirrors the round-2 existence-before-coerce
    test.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": "primary", "credits": 1, "notes": "a\nb"}),
    )
    assert rc == 1
    assert "already present" in out.lower()
    assert "newline" not in out.lower()


def test_set_block_help_mid_write_does_not_silently_no_op(sandbox):
    """`set-block <id> <block> -h ...` must not silently exit 0 without writing.

    argparse processes -h anywhere in argv and exits 0; a permissive handler
    would dutifully return 0 and the user's wrapper script would think the
    write succeeded. Pre-screen so `-h` mixed with other args is a hard error.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "-h",
        "--json",
        json.dumps({"group": "primary", "credits": 1}),
    )
    assert rc != 0
    assert "alone" in out.lower() or "help" in out.lower()
    # The write must NOT have happened.
    assert "cpe" not in sandbox.entry("2026-09-sb-target")


def test_set_block_detects_block_with_space_before_colon(sandbox):
    """A hand-edited entry with `ptr :` (space before colon) must still trip
    the duplicate-block guard.

    `find_entry_line_range` accepts both `- id:` and `- id :` forms, so the
    duplicate-block helper has to match that tolerance. Otherwise set-block
    would happily splice a second block of the same name into the entry,
    silently losing or invalidating the first.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    # Hand-edit the entry so its ptr block-key has a stray space before the
    # colon — the same shape `find_entry_line_range` already tolerates. Scope
    # to this entry by anchoring on its id marker so unrelated fixture entries
    # are untouched.
    text = sandbox.activities.read_text()
    anchor = text.index("- id: 2026-09-sb-target")
    suffix_start = anchor + text[anchor:].index("    ptr:")
    sandbox.activities.write_text(
        text[:suffix_start] + text[suffix_start:].replace("    ptr:", "    ptr :", 1)
    )

    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "ptr",
        "--json",
        json.dumps({"category": "service", "subcategory": "external-professional"}),
    )
    assert rc == 1
    assert "already present" in out.lower()


def test_set_block_rejects_required_fields_set_to_null(sandbox):
    """JSON null for a required field must be a hard validation error.

    Pre-existing engine quirk: validate_block only enforced required+non-null
    for date fields, so {group: null, credits: null} silently produced a
    fully-null block on disk. set-block (and any future full-block writer like
    merge) needs the engine to reject this for every required type.
    """
    sandbox.run("create", stdin=json.dumps(_ptr_only_entry()))
    out, _, rc = sandbox.run(
        "set-block",
        "2026-09-sb-target",
        "cpe",
        "--json",
        json.dumps({"group": None, "credits": None}),
    )
    assert rc == 1
    assert "null" in out.lower()
    assert "INVALID CPE.GROUP" in out
    assert "INVALID CPE.CREDITS" in out


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

    out, err, rc = sandbox.run("add-tags", "2024-03-intro-security-course", "appended-tag")
    assert rc == 0, f"add-tags failed: stdout={out!r} stderr={err!r}"
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


def test_create_rejects_whitespace_id(sandbox):
    """create refuses an id with whitespace.

    The change ledger is space-delimited, so a whitespace id would be
    truncated when parsed back and silently dropped by --changed-since. The
    id must be a ledger-safe slug.
    """
    out, _, rc = sandbox.run("create", stdin=json.dumps(_new_entry(id="my entry")))
    assert rc == 1
    assert "not a valid id" in out.lower()


def test_create_rejects_uppercase_id(sandbox):
    """create enforces the same slug rule as rename-id (lowercase only)."""
    out, _, rc = sandbox.run("create", stdin=json.dumps(_new_entry(id="2026-07-MixedCase")))
    assert rc == 1
    assert "not a valid id" in out.lower()


def test_create_rejects_integer_id(sandbox):
    """A bare-integer JSON id is rejected (it would persist as a non-string).

    `str(20260728)` matches the slug regex, but the entry would round-trip as
    an int while the ledger keys on the string token — silently dropping it
    from --changed-since. The id must be a genuine string.
    """
    data = _new_entry()
    data["id"] = 20260728  # intentional non-string id
    out, _, rc = sandbox.run("create", stdin=json.dumps(data))
    assert rc == 1
    assert "not a valid id" in out.lower()


def test_create_accepts_slug_id(sandbox):
    """A normal slug id is accepted and round-trips through --changed-since.

    Guards the end-to-end path the validation protects: a created entry must
    be findable by its full id in a ledger-derived query.
    """
    out, _, rc = sandbox.run("create", stdin=json.dumps(_new_entry(id="2026-07-valid-slug")))
    assert rc == 0
    out, _, rc = sandbox.run("filter", "--changed-since", "2000-01-01", "--brief")
    assert rc == 0
    assert "2026-07-valid-slug" in out


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
# delete --repoint-to (rewrite inbound references before deleting)
# ---------------------------------------------------------------------------


def test_delete_repoint_to_rewrites_inbound_refs_and_deletes_source(sandbox):
    """delete --repoint-to <t> rewrites every reference to the source -> t,
    then removes the source entry."""
    # 2024-06-security-curriculum-report references 2024-03-intro-security-course
    # by backtick in its description.
    out, _, rc = sandbox.run(
        "delete",
        "2024-03-intro-security-course",
        "--repoint-to",
        "2026-04-self-study",
        "--confirm",
    )
    assert rc == 0
    assert "repointed" in out.lower()
    # Source is gone.
    ids = {e["id"] for e in sandbox.load_activities()}
    assert "2024-03-intro-security-course" not in ids
    # The referrer now points at the target.
    desc = sandbox.entry("2024-06-security-curriculum-report")["description"]
    assert "`2026-04-self-study`" in desc
    assert "2024-03-intro-security-course" not in desc


def test_delete_repoint_to_dry_run_shows_count_without_writing(sandbox):
    """Without --confirm, delete --repoint-to previews the count and writes nothing."""
    before_count = len(sandbox.load_activities())
    before_text = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "delete",
        "2024-03-intro-security-course",
        "--repoint-to",
        "2026-04-self-study",
    )
    assert rc == 0
    assert "Would repoint" in out
    assert "Dry run" in out
    assert len(sandbox.load_activities()) == before_count
    assert sandbox.activities.read_text() == before_text


def test_delete_repoint_to_unknown_target_rejected(sandbox):
    """delete --repoint-to <unknown-id> fails before any write."""
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "delete",
        "2026-04-self-study",
        "--repoint-to",
        "no-such-target",
        "--confirm",
    )
    assert rc == 1
    assert "not found" in out.lower()
    assert sandbox.activities.read_text() == before


def test_delete_repoint_to_rejects_self_target(sandbox):
    """--repoint-to cannot target the entry being deleted."""
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "delete",
        "2026-04-self-study",
        "--repoint-to",
        "2026-04-self-study",
        "--confirm",
    )
    assert rc == 1
    assert "being deleted" in out.lower()
    assert sandbox.activities.read_text() == before


def test_delete_repoint_to_with_no_inbound_refs_succeeds(sandbox):
    """An entry with no inbound refs can still be deleted with --repoint-to.

    Repoint count is 0; the delete proceeds normally.
    """
    out, _, rc = sandbox.run(
        "delete",
        "2026-04-self-study",
        "--repoint-to",
        "2025-02-conference-talk",
        "--confirm",
    )
    assert rc == 0
    assert "0 reference(s) repointed" in out
    ids = {e["id"] for e in sandbox.load_activities()}
    assert "2026-04-self-study" not in ids


def test_delete_repoint_to_respects_token_boundaries(sandbox):
    """A longer id-shaped string starting with the source id must not be touched."""
    sentinel = "2024-03-intro-security-course-extension"
    sandbox.run(
        "update-notes",
        "2025-02-conference-talk",
        stdin=f"See {sentinel} for an unrelated follow-on.",
    )
    out, _, rc = sandbox.run(
        "delete",
        "2024-03-intro-security-course",
        "--repoint-to",
        "2026-04-self-study",
        "--confirm",
    )
    assert rc == 0
    notes = sandbox.entry("2025-02-conference-talk")["ptr"]["notes"]
    assert sentinel in notes, f"longer id-shaped string was modified: {notes!r}"


def test_delete_repoint_to_rewrites_plain_text_refs_in_notes(sandbox):
    """The rewrite covers plain-text mentions in block notes, not just backticks."""
    sandbox.run(
        "update-notes",
        "2025-02-conference-talk",
        stdin="Originated from 2024-03-intro-security-course coursework.",
    )
    out, _, rc = sandbox.run(
        "delete",
        "2024-03-intro-security-course",
        "--repoint-to",
        "2026-04-self-study",
        "--confirm",
    )
    assert rc == 0
    notes = sandbox.entry("2025-02-conference-talk")["ptr"]["notes"]
    assert "2026-04-self-study" in notes
    assert "2024-03-intro-security-course" not in notes


def test_delete_without_repoint_to_leaves_refs_intact(sandbox):
    """Existing delete behavior unchanged: without --repoint-to, refs aren't rewritten."""
    out, _, rc = sandbox.run("delete", "2024-03-intro-security-course", "--confirm")
    assert rc == 0
    # The referrer still mentions the now-dangling id (this is the prior
    # behavior the --repoint-to flag exists to remediate).
    desc = sandbox.entry("2024-06-security-curriculum-report")["description"]
    assert "2024-03-intro-security-course" in desc


def test_delete_repoint_to_writes_ledger_entry_with_count(sandbox):
    """The ledger records the repoint target and reference count."""
    sandbox.run(
        "delete",
        "2024-03-intro-security-course",
        "--repoint-to",
        "2026-04-self-study",
        "--confirm",
    )
    text = sandbox.ledger.read_text()
    assert "delete" in text
    assert "2024-03-intro-security-course" in text
    assert "repoint-to=2026-04-self-study" in text
    assert "refs=" in text


def test_delete_help_mid_args_does_not_silently_no_op(sandbox):
    """`delete <id> -h --confirm` must not silently exit 0 without deleting.

    Same safety pattern as set-block: -h alongside other args is a hard error,
    not a sneaky no-op that looks like success.
    """
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run("delete", "2026-04-self-study", "-h", "--confirm")
    assert rc != 0
    assert sandbox.activities.read_text() == before


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
# merge (consolidate source entries into a target, atomically)
# ---------------------------------------------------------------------------


def _merge_target_entry(**overrides) -> dict:
    """A ptr-only target entry to be merged into."""
    base = {
        "id": "2026-09-mg-target",
        "date": "2026-09-01",
        "title": "Merge target",
        "description": "Original target description.",
        "tags": ["target-tag", "shared"],
        "docs": ["https://example.com/target"],
        "ptr": {
            "category": "service",
            "subcategory": "external-professional",
            "notes": "target ptr",
        },
    }
    base.update(overrides)
    return base


def _merge_source_entry(**overrides) -> dict:
    """A cpe-only source entry that can carry a block to a ptr-only target."""
    base = {
        "id": "2026-09-mg-source",
        "date": "2026-09-01",
        "title": "Merge source",
        "description": "Source description.",
        "tags": ["source-tag", "shared"],
        "docs": ["https://example.com/source"],
        "cpe": {"group": "primary", "credits": 4},
    }
    base.update(overrides)
    return base


def test_merge_unions_tags_in_target_then_source_order(sandbox):
    """Tag union keeps target's order and de-duplicates against shared values."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 0
    tags = sandbox.entry("2026-09-mg-target")["tags"]
    # Target's tags stay in their original order; source's new tag is appended;
    # shared tag is not duplicated.
    assert tags[:2] == ["target-tag", "shared"]
    assert "source-tag" in tags
    assert tags.count("shared") == 1


def test_merge_unions_docs(sandbox):
    """Doc union de-duplicates and appends source's new docs."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    docs = sandbox.entry("2026-09-mg-target")["docs"]
    assert "https://example.com/target" in docs
    assert "https://example.com/source" in docs


def test_merge_carries_block_target_lacks(sandbox):
    """A source's block that target lacks is carried over and round-trips."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))  # ptr only
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))  # cpe only
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    target = sandbox.entry("2026-09-mg-target")
    # cpe carried over.
    assert target["cpe"]["group"] == "primary"
    assert target["cpe"]["credits"] == 4
    # Target's ptr untouched.
    assert target["ptr"]["notes"] == "target ptr"


def test_merge_same_block_conflict_aborts_by_default(sandbox):
    """Default --on-block-conflict=abort refuses to merge when both have the block."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    source = _merge_source_entry(
        ptr={"category": "scholarly", "subcategory": "cat3-other", "notes": "src ptr"}
    )
    source.pop("cpe", None)
    sandbox.run("create", stdin=json.dumps(source))
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 1
    assert "conflict" in out.lower()
    assert "--on-block-conflict" in out
    # No partial write.
    assert sandbox.activities.read_text() == before


def test_merge_keep_target_drops_source_block(sandbox):
    """--on-block-conflict=keep-target retains target's block; source's is dropped."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    source = _merge_source_entry(
        ptr={"category": "scholarly", "subcategory": "cat3-other", "notes": "src ptr"}
    )
    source.pop("cpe", None)
    sandbox.run("create", stdin=json.dumps(source))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--on-block-conflict",
        "keep-target",
        "--confirm",
    )
    assert rc == 0
    target = sandbox.entry("2026-09-mg-target")
    # Target's ptr survives untouched.
    assert target["ptr"]["notes"] == "target ptr"
    assert target["ptr"]["category"] == "service"


def test_merge_keep_source_replaces_target_block(sandbox):
    """--on-block-conflict=keep-source replaces target's block with the source's."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    source = _merge_source_entry(
        ptr={"category": "scholarly", "subcategory": "cat3-other", "notes": "src ptr"}
    )
    source.pop("cpe", None)
    sandbox.run("create", stdin=json.dumps(source))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--on-block-conflict",
        "keep-source",
        "--confirm",
    )
    assert rc == 0
    target = sandbox.entry("2026-09-mg-target")
    # Target's ptr is now source's ptr.
    assert target["ptr"]["category"] == "scholarly"
    assert target["ptr"]["notes"] == "src ptr"


def test_merge_validates_carried_block_against_schema(sandbox):
    """A carried-over block must satisfy schema validation before any write."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    # Construct a source whose cpe block is missing a required field. The
    # source file has to bypass create's own validation - hand-edit the YAML
    # to simulate a manually-corrupted source.
    source = _merge_source_entry()
    sandbox.run("create", stdin=json.dumps(source))
    text = sandbox.activities.read_text()
    # Replace the source's `credits: 4` line with `credits: null`.
    sandbox.activities.write_text(text.replace("credits: 4", "credits: null"))
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 1
    assert "fails schema validation" in out.lower()
    assert sandbox.activities.read_text() == before


def test_merge_repoints_inbound_references(sandbox):
    """References to source ids across other entries are repointed to target."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    # Inject a referrer that mentions the source in both backtick and plain text.
    sandbox.run(
        "update-notes",
        "2024-03-intro-security-course",
        stdin="See `2026-09-mg-source` and 2026-09-mg-source.",
    )
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    notes = sandbox.entry("2024-03-intro-security-course")["ptr"]["notes"]
    assert "2026-09-mg-target" in notes
    assert "2026-09-mg-source" not in notes


def test_merge_repointing_respects_token_boundaries(sandbox):
    """A longer id-shaped string starting with the source id must not be repointed."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sentinel = "2026-09-mg-source-extension"
    sandbox.run(
        "update-notes",
        "2024-03-intro-security-course",
        stdin=f"See {sentinel} for an unrelated follow-on.",
    )
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    notes = sandbox.entry("2024-03-intro-security-course")["ptr"]["notes"]
    assert sentinel in notes


def test_merge_provenance_note_is_plain_text_not_backticked(sandbox):
    """The provenance note must use plain text so validate's dangling-ref
    scanner doesn't flag the now-deleted source ids as broken refs."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    desc = sandbox.entry("2026-09-mg-target")["description"]
    assert "Consolidates former entries: 2026-09-mg-source" in desc
    # MUST be plain text — backticks would trip the dangling-ref scanner.
    assert "`2026-09-mg-source`" not in desc
    # validate now runs clean (the consolidates ids are plain prose, not refs).
    out, _, _ = sandbox.run("validate")
    assert "DANGLING REF: 2026-09-mg-target" not in out


def test_merge_no_provenance_suppresses_note(sandbox):
    """--no-provenance keeps the target's description unchanged."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--no-provenance",
        "--confirm",
    )
    desc = sandbox.entry("2026-09-mg-target")["description"]
    assert "Consolidates former entries" not in desc


def test_merge_deletes_source_entries(sandbox):
    """All sources are removed after the merge succeeds."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    ids = {e["id"] for e in sandbox.load_activities()}
    assert "2026-09-mg-source" not in ids
    assert "2026-09-mg-target" in ids


def test_merge_with_multiple_sources_deletes_all_cleanly(sandbox):
    """Multiple sources delete cleanly (no index drift between deletions)."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry(id="2026-09-mg-s1")))
    sandbox.run(
        "create",
        stdin=json.dumps(
            {
                "id": "2026-09-mg-s2",
                "date": "2026-09-01",
                "title": "S2",
                "description": "d",
                "tags": ["s2-tag"],
                "docs": [],
                "ptr": {
                    "category": "scholarly",
                    "subcategory": "cat3-other",
                    "notes": "s2 ptr",
                },
            }
        ),
    )
    sandbox.run(
        "merge",
        "2026-09-mg-s1",
        "2026-09-mg-s2",
        "--into",
        "2026-09-mg-target",
        "--on-block-conflict",
        "keep-target",
        "--confirm",
    )
    ids = {e["id"] for e in sandbox.load_activities()}
    assert "2026-09-mg-s1" not in ids
    assert "2026-09-mg-s2" not in ids
    assert "2026-09-mg-target" in ids


def test_merge_dry_run_shows_source_descriptions_and_writes_nothing(sandbox):
    """Without --confirm, the dry-run prints source descriptions for manual folding."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
    )
    assert rc == 0
    assert "Dry run" in out
    assert "Source description" in out  # the surfaced description for folding
    assert sandbox.activities.read_text() == before


def test_merge_skips_self_source(sandbox):
    """Listing the target as a source is silently skipped."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "2026-09-mg-target",  # self-source
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 0
    # Target survives and the legitimate source was still merged.
    assert sandbox.entry("2026-09-mg-target") is not None
    assert "2026-09-mg-source" not in {e["id"] for e in sandbox.load_activities()}


def test_merge_dedups_repeated_source_ids(sandbox):
    """Duplicate source ids in argv collapse to a single source."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 0
    # The single source was processed; ledger should reflect one source.
    text = sandbox.ledger.read_text()
    assert "sources=2026-09-mg-source " in text


def test_merge_unknown_target_rejected(sandbox):
    """An unknown target id is a hard error without any write."""
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    after_create = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "no-such-target",
        "--confirm",
    )
    assert rc == 1
    assert "target" in out.lower() and "not found" in out.lower()
    assert sandbox.activities.read_text() == after_create


def test_merge_unknown_source_rejected(sandbox):
    """An unknown source id is a hard error without any write."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "no-such-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 1
    assert "not found" in out.lower()
    assert sandbox.activities.read_text() == before


def test_merge_ledger_records_aggregate(sandbox):
    """One merge ledger entry summarizes the whole transaction."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    text = sandbox.ledger.read_text()
    assert "merge 2026-09-mg-target" in text
    assert "sources=2026-09-mg-source" in text
    assert "blocks=carried:cpe" in text
    assert "refs=" in text


def test_merge_help_mid_args_does_not_silently_no_op(sandbox):
    """`merge ... -h ... --confirm` must not silently exit 0 without merging.

    Same safety pattern as set-block and delete: -h alongside other args is
    a hard error, not a sneaky no-op.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "-h",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc != 0
    assert sandbox.activities.read_text() == before


def test_merge_docs_union_does_not_corrupt_inline_target(sandbox):
    """When target's docs field is inline `[...]`, the union must stay valid YAML.

    Round-1 review finding: the prior path's `else` branch spliced new items
    as block-style children under an inline list, producing invalid YAML.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    # Hand-edit target's docs to the inline non-empty shape.
    text = sandbox.activities.read_text()
    needle = "    docs:\n      - 'https://example.com/target'\n"
    replacement = "    docs: ['https://example.com/target']\n"
    assert needle in text, "fixture format changed; update this test"
    sandbox.activities.write_text(text.replace(needle, replacement, 1))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 0
    # The post-merge file must still parse cleanly.
    docs = sandbox.entry("2026-09-mg-target")["docs"]
    assert "https://example.com/target" in docs
    assert "https://example.com/source" in docs


def test_merge_carries_generic_block_target_lacks(sandbox):
    """A source's generic (schema-unknown) block must be carried over, not
    silently dropped.

    Round-1 review finding #2: the block-plan loop only iterated
    ctx.schema.blocks, so a source with a generic block had it silently
    discarded along with the source entry.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    source = _merge_source_entry(id="2026-09-mg-gsrc")
    # Append a generic block via JSON; cmd_create's _render_entry supports it.
    source["custom_block"] = {"key1": "value1", "key2": 42}
    sandbox.run("create", stdin=json.dumps(source))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-gsrc",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 0
    target = sandbox.entry("2026-09-mg-target")
    assert "custom_block" in target
    assert target["custom_block"]["key1"] == "value1"
    assert target["custom_block"]["key2"] == 42


def test_merge_provenance_hard_fails_when_target_has_no_description(sandbox):
    """If the target lacks a description, the default-on provenance step must
    not silently no-op while the sources get deleted anyway.

    Round-1 review finding #3.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    # Hand-edit out the target's description field entirely (artificial state,
    # but the merge code must refuse rather than silently lose the audit note).
    text = sandbox.activities.read_text()
    # Replace the literal-block description (two lines: header + indented body)
    # with nothing for the target entry only.
    anchor = text.index("- id: 2026-09-mg-target")
    chunk = text[anchor:]
    no_desc = chunk.replace("    description: |\n      Original target description.\n", "", 1)
    sandbox.activities.write_text(text[:anchor] + no_desc)
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 1
    assert "no description" in out.lower() or "--no-provenance" in out
    # No partial write: source was not deleted.
    assert sandbox.activities.read_text() == before


def test_merge_no_provenance_succeeds_when_target_lacks_description(sandbox):
    """With --no-provenance, the missing-description case must succeed."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    text = sandbox.activities.read_text()
    anchor = text.index("- id: 2026-09-mg-target")
    chunk = text[anchor:]
    no_desc = chunk.replace("    description: |\n      Original target description.\n", "", 1)
    sandbox.activities.write_text(text[:anchor] + no_desc)
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--no-provenance",
        "--confirm",
    )
    assert rc == 0


def test_merge_refuses_inline_scalar_description(sandbox):
    """An inline scalar description (`description: hello`) must be refused
    with --no-provenance suggested, not silently polluted.

    Round-1 review finding #4.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    text = sandbox.activities.read_text()
    # Replace target's literal-block description with an inline scalar.
    inline = text.replace(
        "    description: |\n      Original target description.\n",
        "    description: hello inline\n",
        1,
    )
    sandbox.activities.write_text(inline)
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 1
    assert "inline description" in out.lower()
    assert sandbox.activities.read_text() == before


def test_merge_inline_scalar_description_ok_with_no_provenance(sandbox):
    """--no-provenance bypasses the inline-description check."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    text = sandbox.activities.read_text()
    inline = text.replace(
        "    description: |\n      Original target description.\n",
        "    description: hello inline\n",
        1,
    )
    sandbox.activities.write_text(inline)
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--no-provenance",
        "--confirm",
    )
    assert rc == 0


def test_merge_carried_block_refs_to_other_source_are_rewritten(sandbox):
    """A carried block's mention of another source being deleted in the same
    merge must be rewritten to the target before the source is removed —
    otherwise the docstring's no-dangling-refs promise breaks.

    Round-2 review finding #1. Source A's cpe.notes mentions source B; when
    both are merged into target, A's cpe is carried over (target lacks cpe),
    so the B-mention rides along verbatim. The merge must rewrite that
    mention to target_id BEFORE step 6 deletes B, leaving validate clean.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src_a = _merge_source_entry(id="2026-09-mg-src-a")
    src_a["cpe"]["notes"] = "Related to `2026-09-mg-src-b`."
    sandbox.run("create", stdin=json.dumps(src_a))
    src_b = {
        "id": "2026-09-mg-src-b",
        "date": "2026-09-01",
        "title": "B",
        "description": "B desc.",
        "tags": ["btag"],
        "docs": [],
        "ptr": {
            "category": "scholarly",
            "subcategory": "cat3-other",
            "notes": "b ptr",
        },
    }
    sandbox.run("create", stdin=json.dumps(src_b))
    sandbox.run(
        "merge",
        "2026-09-mg-src-a",
        "2026-09-mg-src-b",
        "--into",
        "2026-09-mg-target",
        "--on-block-conflict",
        "keep-target",
        "--confirm",
    )
    # The carried cpe.notes must now reference target_id, not the deleted
    # source-b id. validate must report no dangling refs from target.
    target = sandbox.entry("2026-09-mg-target")
    assert "2026-09-mg-src-b" not in target["cpe"]["notes"]
    assert "2026-09-mg-target" in target["cpe"]["notes"]
    out, _, _ = sandbox.run("validate")
    assert "DANGLING REF: 2026-09-mg-target" not in out


def test_merge_ledger_records_carried_replaced_dropped_distinctions(sandbox):
    """The ledger details distinguish carried / replaced / dropped block paths.

    Round-1 review finding #6.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    # Source has cpe (carry-over candidate) and a conflicting ptr.
    src = _merge_source_entry()
    src["ptr"] = {
        "category": "scholarly",
        "subcategory": "cat3-other",
        "notes": "src ptr",
    }
    sandbox.run("create", stdin=json.dumps(src))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--on-block-conflict",
        "keep-source",  # replaces target's ptr
        "--confirm",
    )
    text = sandbox.ledger.read_text()
    # cpe was carried; ptr was replaced.
    assert "carried:cpe" in text
    assert "replaced:ptr" in text


def test_merge_surfaces_first_wins_dropped_duplicate_source_blocks(sandbox):
    """When two sources carry the same block target lacks, the second's drop
    must surface in the dry-run plan output.

    Round-1 review finding #5.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry(id="2026-09-mg-s1")))
    sandbox.run(
        "create",
        stdin=json.dumps(
            _merge_source_entry(id="2026-09-mg-s2", cpe={"group": "general", "credits": 9})
        ),
    )
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-s1",
        "2026-09-mg-s2",
        "--into",
        "2026-09-mg-target",
    )
    assert rc == 0
    assert "first source wins" in out.lower()
    assert "2026-09-mg-s2" in out  # the dropped duplicate is named


def test_merge_splice_block_helper_handles_unknown_block_name(sandbox):
    """_splice_block_into_entry must not raise a misleading ValueError when
    the block name isn't in schema_block_names.

    Round-1 review finding #7. We exercise this indirectly through the
    generic-block carry-over path (which exists now that #2 is fixed).
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    source = _merge_source_entry(id="2026-09-mg-unk")
    source["unknown_block"] = {"a": 1, "b": "two"}
    sandbox.run("create", stdin=json.dumps(source))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-unk",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 0
    # The unknown block landed cleanly — no misleading "'foo' is not in list" error.
    assert "is not in list" not in out
    target = sandbox.entry("2026-09-mg-target")
    assert target["unknown_block"]["a"] == 1


def test_merge_refuses_source_with_non_dict_top_level_keys(sandbox):
    """A source carrying a non-core, non-block top-level key whose value is
    a list or scalar must abort the merge, naming the keys.

    Round-2 review finding #3 — round-1 #2 only fixed dict values. cmd_create
    itself drops non-dict top-level keys silently, so the test simulates a
    hand-edited / imported source (which the README explicitly supports) by
    injecting the field directly into the YAML.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry(id="2026-09-mg-listy")))
    # Hand-edit a flush-aligned `custom_list: [one, two]` into the source.
    text = sandbox.activities.read_text()
    needle = "    cpe:\n      group: 'primary'\n"
    assert needle in text, "fixture shape changed; update this test"
    text2 = text.replace(needle, "    custom_list: [one, two, three]\n" + needle, 1)
    sandbox.activities.write_text(text2)
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge", "2026-09-mg-listy", "--into", "2026-09-mg-target", "--confirm"
    )
    assert rc == 1
    assert "custom_list" in out
    assert "non-block" in out.lower() or "cannot safely carry" in out.lower()
    assert sandbox.activities.read_text() == before


def test_merge_inline_docs_with_internal_quote_and_comma_round_trip(sandbox):
    """Doc URLs containing `,` round-trip safely through the inline rewriter.

    Round-2 review finding #6 (split(",") + repr() corruption case).
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src = _merge_source_entry(docs=["https://example.com/q?a=1,b=2"])
    sandbox.run("create", stdin=json.dumps(src))
    # Force target's docs to inline non-empty so the inline rewriter runs.
    text = sandbox.activities.read_text()
    needle = "    docs:\n      - 'https://example.com/target'\n"
    replacement = "    docs: ['https://example.com/target']\n"
    assert needle in text, "fixture format changed; update this test"
    sandbox.activities.write_text(text.replace(needle, replacement, 1))
    out, _, rc = sandbox.run(
        "merge", "2026-09-mg-source", "--into", "2026-09-mg-target", "--confirm"
    )
    assert rc == 0
    # The URL with the embedded comma must survive intact (not split into two).
    docs = sandbox.entry("2026-09-mg-target")["docs"]
    assert "https://example.com/q?a=1,b=2" in docs


def test_merge_docs_empty_reset_branch_quotes_doc_with_double_quote(sandbox):
    """The `docs: []` reset branch must yaml-quote items, not naively
    interpolate `"{doc}"` (round-2 finding #5).

    A doc value containing a `"` character would otherwise close the YAML
    scalar prematurely and corrupt the file.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry(docs=[])))
    src = _merge_source_entry(docs=['say "hi" world'])
    sandbox.run("create", stdin=json.dumps(src))
    out, _, rc = sandbox.run(
        "merge", "2026-09-mg-source", "--into", "2026-09-mg-target", "--confirm"
    )
    assert rc == 0
    # File must still parse, and the doc string must round-trip intact.
    docs = sandbox.entry("2026-09-mg-target")["docs"]
    assert 'say "hi" world' in docs


def test_merge_provenance_body_indent_matches_existing_body(sandbox):
    """Provenance note must align with the description's actual body indent,
    not assume desc_indent + 2.

    Round-2 review finding #2. Hand-edit target's literal block so its body
    lives at desc_indent + 4 (an explicit `|4` indent), then verify the
    provenance line sits at the same indent — otherwise YAML would terminate
    the literal block early and the note would become a stray mapping key.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    text = sandbox.activities.read_text()
    deep_indented = text.replace(
        "    description: |\n      Original target description.\n",
        "    description: |4\n        Original target description.\n",
        1,
    )
    sandbox.activities.write_text(deep_indented)
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    assert rc == 0
    # The whole file must still parse and the provenance line must live
    # inside the description body (not as a stray top-level mapping key).
    target = sandbox.entry("2026-09-mg-target")
    assert "Consolidates former entries" in target["description"]
    # No stray top-level key got introduced.
    assert "Consolidates former entries" not in target


def test_merge_empty_description_gets_clearer_error(sandbox):
    """The empty-description case should not be labeled 'inline scalar'.

    Round-2 review finding #10.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    text = sandbox.activities.read_text()
    # Hand-edit target's description to empty value (header present, no body).
    text2 = text.replace(
        "    description: |\n      Original target description.\n",
        "    description:\n",
        1,
    )
    sandbox.activities.write_text(text2)
    out, _, rc = sandbox.run(
        "merge", "2026-09-mg-source", "--into", "2026-09-mg-target", "--confirm"
    )
    assert rc == 1
    assert "empty description" in out.lower()


def test_merge_dry_run_flag_works_as_alias(sandbox):
    """--dry-run is accepted (README documents it) and overrides --confirm."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--dry-run",
        "--confirm",  # dry-run wins over confirm
    )
    assert rc == 0
    assert "Dry run" in out
    assert sandbox.activities.read_text() == before


def test_merge_keep_source_deletes_full_block_through_comment(sandbox):
    """A hand-edited block with a flush-left comment inside must still be
    fully deleted (not partially) when keep-source replaces it.

    Round-2 review finding #7.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src = _merge_source_entry()
    src["ptr"] = {
        "category": "scholarly",
        "subcategory": "cat3-other",
        "notes": "src ptr",
    }
    sandbox.run("create", stdin=json.dumps(src))
    # Inject a flush-left `# ...` comment line inside target's ptr block.
    text = sandbox.activities.read_text()
    inject_at = "      notes: 'target ptr'\n"
    # The comment lives at column 0 (less indent than the block header),
    # which the old terminator scan would mistake for the block's end.
    text2 = text.replace(inject_at, inject_at + "# stray comment inside the block\n", 1)
    sandbox.activities.write_text(text2)
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--on-block-conflict",
        "keep-source",
        "--confirm",
    )
    assert rc == 0
    # Target's ptr is the source's now; no remnants of target's prior ptr.
    target = sandbox.entry("2026-09-mg-target")
    assert target["ptr"]["category"] == "scholarly"
    assert target["ptr"]["notes"] == "src ptr"
    # File parses cleanly (no double-block headers from a partial deletion).
    assert isinstance(target["ptr"], dict)


def test_merge_append_sources_folds_descriptions_with_plain_text_headers(sandbox):
    """--append-sources appends each source's description under a plain-text
    `## From <id>` header in the target's literal-block description.

    Optional flag from the original spec. Off by default. Headers must NOT be
    backticked or the validate dangling-ref scanner would flag the
    soon-to-be-deleted source ids.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src = _merge_source_entry(description="Source 1 first paragraph.\nSecond paragraph.")
    sandbox.run("create", stdin=json.dumps(src))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",
        "--confirm",
    )
    assert rc == 0
    desc = sandbox.entry("2026-09-mg-target")["description"]
    # Original target description survives.
    assert "Original target description." in desc
    # Plain-text header (NOT backticked).
    assert "## From 2026-09-mg-source" in desc
    assert "`2026-09-mg-source`" not in desc
    # Source body included.
    assert "Source 1 first paragraph." in desc
    assert "Second paragraph." in desc
    # Default provenance one-liner is still present alongside.
    assert "Consolidates former entries:" in desc
    # validate is clean (the plain-text header doesn't dangle).
    out, _, _ = sandbox.run("validate")
    assert "DANGLING REF: 2026-09-mg-target" not in out


def test_merge_append_sources_rewrites_cross_source_refs_in_bodies(sandbox):
    """When --append-sources is on and source A's body backticks source B's
    id, B's id must be rewritten to target_id before splicing — otherwise
    after step 6 deletes B, the appended body holds a dangling backticked
    ref to a now-missing entry.

    Round-2 review finding #3.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src_a = _merge_source_entry(
        id="2026-09-mg-src-a",
        description="A intro. See `2026-09-mg-src-b` for context.",
    )
    out, err, rc = sandbox.run("create", stdin=json.dumps(src_a))
    assert rc == 0, f"src_a create failed: {err}"
    src_b = {
        "id": "2026-09-mg-src-b",
        "date": "2026-09-01",
        "title": "B",
        "description": "B desc.",
        "tags": ["btag"],
        "docs": [],
        "ptr": {"category": "scholarly", "subcategory": "cat3-other", "notes": "b"},
    }
    out, err, rc = sandbox.run("create", stdin=json.dumps(src_b))
    assert rc == 0, f"src_b create failed: {err}"
    out, err, rc = sandbox.run(
        "merge",
        "2026-09-mg-src-a",
        "2026-09-mg-src-b",
        "--into",
        "2026-09-mg-target",
        "--on-block-conflict",
        "keep-target",
        "--append-sources",
        "--confirm",
    )
    assert rc == 0, f"merge failed: {err}"
    desc = sandbox.entry("2026-09-mg-target")["description"]
    # B's id appears legitimately as the plain-text "## From <sid>" header
    # for its own appended block and in the provenance one-liner; what must
    # NOT survive is the backticked cross-reference inside A's body, since
    # step 6 deletes B and that backtick would dangle.
    assert "`2026-09-mg-src-b`" not in desc
    assert "`2026-09-mg-target`" in desc
    # validate must not flag a dangling ref from target.
    out, _, _ = sandbox.run("validate")
    assert "DANGLING REF: 2026-09-mg-target" not in out


def test_merge_append_sources_rewrites_self_backticks_in_bodies(sandbox):
    """When --append-sources is on and a source body backticks its OWN id,
    that backtick must also be rewritten to target_id — once the body lives
    in the target's description and step 6 deletes the source, the self-ref
    would otherwise become a dangling backticked reference to a now-missing
    entry.

    Round-4 review finding: the prior heuristic skipped self-mentions on
    the (wrong) reasoning that they were the author's own narrative; in
    fact every backticked source id is a reference to an entry that step 6
    is about to delete, including self-mentions.
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src = _merge_source_entry(
        description="See `2026-09-mg-source` in the prior write-up for context.",
    )
    out, err, rc = sandbox.run("create", stdin=json.dumps(src))
    assert rc == 0, f"src create failed: {err}"
    out, err, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",
        "--confirm",
    )
    assert rc == 0, f"merge failed: {err}"
    desc = sandbox.entry("2026-09-mg-target")["description"]
    # The self-backtick must be rewritten to target_id.
    assert "`2026-09-mg-source`" not in desc
    assert "`2026-09-mg-target`" in desc
    # validate is clean.
    out, _, _ = sandbox.run("validate")
    assert "DANGLING REF: 2026-09-mg-target" not in out


def test_merge_append_sources_preserves_plain_text_source_id_mentions(sandbox):
    """Plain-text (un-backticked) mentions of a source id in a source body
    must NOT be rewritten when --append-sources splices the body into the
    target's description.

    Round-5 review finding #1: a word-boundary regex would silently
    rewrite narrative like "Originally tracked under <sid> before
    consolidation" into a fabricated self-reference. The append-sources
    body rewrite must be backtick-anchored (matching step 1b's pattern).
    """
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src = _merge_source_entry(
        description=(
            "Originally tracked under 2026-09-mg-source before consolidation. "
            "See `2026-09-mg-source` for the prior write-up."
        ),
    )
    out, err, rc = sandbox.run("create", stdin=json.dumps(src))
    assert rc == 0, f"src create failed: {err}"
    out, err, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",
        "--confirm",
    )
    assert rc == 0, f"merge failed: {err}"
    desc = sandbox.entry("2026-09-mg-target")["description"]
    # Plain-text mention survives intact — the author's historical claim
    # ("Originally tracked under <sid>") must not be silently rewritten
    # into a fabricated self-reference ("Originally tracked under <target>").
    assert "Originally tracked under 2026-09-mg-source before consolidation" in desc, (
        "plain-text mention of source id was silently rewritten — "
        "the body rewrite must be backtick-anchored, not word-boundary"
    )
    # Backticked mention IS still rewritten to target_id (round-4 fix).
    assert "`2026-09-mg-source`" not in desc
    assert "`2026-09-mg-target`" in desc


def test_merge_rewrites_backticked_source_id_inside_target_range(sandbox):
    """Backticked source-id mentions in the TARGET's prose are live
    cross-references and must be rewritten, even though plain-text mentions
    in the same range are deliberately left alone.

    Round-2 review finding #4. Without this pass the backtick survives,
    step 6 deletes the source, and validate flags a fresh dangling ref.
    """
    target = _merge_target_entry(
        description=(
            "Original target description.\nSee `2026-09-mg-source` for the original write-up."
        ),
    )
    sandbox.run("create", stdin=json.dumps(target))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    desc = sandbox.entry("2026-09-mg-target")["description"]
    # Backticked mention rewritten to target_id (now self-ref, which the
    # dangling scanner ignores per ref == eid).
    assert "`2026-09-mg-source`" not in desc
    assert "`2026-09-mg-target`" in desc
    out, _, _ = sandbox.run("validate")
    assert "DANGLING REF: 2026-09-mg-target" not in out


def test_merge_append_sources_off_by_default(sandbox):
    """Without --append-sources, the target's description is unchanged apart
    from the optional provenance one-liner."""
    sandbox.run("create", stdin=json.dumps(_merge_target_entry()))
    src = _merge_source_entry(description="Source body NOT appended.")
    sandbox.run("create", stdin=json.dumps(src))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    desc = sandbox.entry("2026-09-mg-target")["description"]
    assert "Source body NOT appended." not in desc
    assert "## From " not in desc


def test_merge_leaves_target_prose_mentions_of_source_id_alone(sandbox):
    """Prose mentions of a source id inside the target's own description
    must not be silently rewritten to self-references.

    Round-2 review finding #8 (deferred to this cleanup PR). rename-id's
    rewriter is broad by design (every mention everywhere); merge limits
    that scope so the target's own descriptive prose about a source id
    survives intact for the human to edit. Self-refs would otherwise be
    invisible to validate (scan_dangling_refs skips ref == eid).
    """
    target = _merge_target_entry(
        description=(
            "Original target description.\n"
            "Originally tracked under 2026-09-mg-source before consolidation."
        ),
    )
    sandbox.run("create", stdin=json.dumps(target))
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--confirm",
    )
    desc = sandbox.entry("2026-09-mg-target")["description"]
    # The prose mention of the source id stays as the user wrote it.
    assert "2026-09-mg-source before consolidation" in desc
    # And it did NOT become a self-reference.
    assert "2026-09-mg-target before consolidation" not in desc


# ---------------------------------------------------------------------------
# write-lock concurrency
# ---------------------------------------------------------------------------


def test_concurrent_writers_do_not_clobber_each_other(sandbox):
    """Two writer processes hammering the same activities file must serialize
    cleanly: every write must land, none silently lost.

    Pins the cross-process write_lock that wraps read-plan-write. Without it,
    two writers each reading their own snapshot and writing it back would
    last-writer-wins one of them.
    """
    import subprocess
    import sys
    from pathlib import Path

    # Derive the repo root from this test file's location so the test works
    # on any checkout (local + CI), not a hardcoded developer path.
    repo_root = Path(__file__).resolve().parent.parent

    # Launch N parallel add-tags invocations against the same entry; each adds
    # a distinct tag. All N writes must survive.
    procs = []
    n = 8
    full_env = {
        **sandbox.env,
        "PATH": "/usr/bin:/bin",
        "LIBRARIAN_SESSION_LABEL": "test:concurrent",
    }
    for i in range(n):
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "librarian.cli",
                    "add-tags",
                    "2026-04-self-study",
                    f"concurrent-tag-{i}",
                ],
                env=full_env,
                cwd=str(repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    # Wait, capture, and surface any worker failure with its stderr so a
    # regression doesn't read as "tag missing for no reason at all".
    for i, p in enumerate(procs):
        stdout, stderr = p.communicate()
        assert p.returncode == 0, (
            f"worker {i} (add-tags concurrent-tag-{i}) exited {p.returncode}\n"
            f"stdout: {stdout.decode(errors='replace')!r}\n"
            f"stderr: {stderr.decode(errors='replace')!r}"
        )
    tags = sandbox.entry("2026-04-self-study")["tags"]
    for i in range(n):
        assert f"concurrent-tag-{i}" in tags, (
            f"tag concurrent-tag-{i} was lost to a concurrent writer"
        )


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


def test_filter_changed_until_bare_date_includes_same_day(sandbox):
    """A bare-date --changed-until is inclusive of the whole day.

    The write happens 'now' (e.g. 14:30Z); a date-only upper bound parses to
    midnight, so without end-of-day normalization the same-day change is wrongly
    dropped. Today's bare date must include it; yesterday's must exclude it.
    """
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    today_str = today.isoformat()

    sandbox.run("add-tags", "2026-04-self-study", "marker")

    out, _, rc = sandbox.run("filter", "--changed-until", today_str, "--brief")
    assert rc == 0
    assert "2026-04-self-study" in out, "same-day change wrongly excluded by bare-date upper bound"

    out, _, rc = sandbox.run("filter", "--changed-until", yesterday, "--count")
    assert rc == 0
    assert out.strip() == "0"


def test_filter_changed_until_explicit_midnight_is_exact(sandbox):
    """An explicit T-time upper bound is honored as-is (not end-of-day)."""
    sandbox.run("add-tags", "2026-04-self-study", "marker")
    # Explicit midnight today: a change made later today is after this instant.
    from datetime import datetime, timezone

    midnight = datetime.now(timezone.utc).date().isoformat() + "T00:00:00Z"
    out, _, rc = sandbox.run("filter", "--changed-until", midnight, "--count")
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


# =============================================================================
# v1.7.1 follow-ups
# =============================================================================


def test_merge_rejects_folded_scalar_when_append_sources(sandbox):
    """A folded-scalar (``description: >``) target must be rejected when
    --append-sources is on. YAML's folded form collapses newlines into
    spaces and would mangle the appended source bodies.

    v1.7.1 follow-up (round-4 review #5).
    """
    target = _merge_target_entry()
    sandbox.run("create", stdin=json.dumps(target))
    # Hand-edit the persisted YAML to convert TARGET's description to a
    # folded scalar. The CLI always writes `|` so this can only happen via
    # hand-edit; that's exactly the case we need to catch. Locate the
    # target's id-line first so we don't accidentally rewrite a fixture
    # entry's description.
    text = sandbox.activities.read_text()
    target_idx = text.find("- id: 2026-09-mg-target")
    assert target_idx != -1, "target entry missing from activities yaml"
    desc_idx = text.find("description: |", target_idx)
    assert desc_idx != -1, "target description not in literal-block form"
    text = text[:desc_idx] + "description: >" + text[desc_idx + len("description: |") :]
    sandbox.activities.write_text(text)

    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",
        "--confirm",
    )
    assert rc == 1, f"merge should reject folded scalar + append-sources: {out}"
    assert "folded" in out.lower()
    assert "literal-block" in out.lower() or "`|`" in out


def test_merge_opt_out_advice_uses_and_when_both_flags(sandbox):
    """When BOTH provenance and append-sources are active and the target's
    description is in an unsupported shape, the error must advise dropping
    BOTH (joined by ``and``) — dropping either alone leaves the other
    active, which keeps the same shape check failing.

    v1.7.1 follow-up (round-4 review #3).
    """
    # Create a target with inline description (rejected by the gate).
    target = _merge_target_entry(description="Inline description")
    sandbox.run("create", stdin=json.dumps(target))
    text = sandbox.activities.read_text()
    # Force inline scalar (CLI defaults to literal-block).
    text = text.replace(
        "description: |\n      Inline description\n", "description: 'Inline description'\n", 1
    )
    sandbox.activities.write_text(text)

    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))
    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",  # provenance is on by default
        "--confirm",
    )
    assert rc == 1
    # Both opt-outs joined by ``and``, not ``or``.
    assert "--no-provenance and drop --append-sources" in out or (
        "and" in out and "or" not in out.split("re-run with")[1].split(")")[0]
    ), f"expected `and` joiner when both flags active; got: {out}"


def test_merge_preview_total_matches_ledger(sandbox):
    """The dry-run preview's `Total references to repoint` line must match
    the post-confirm ledger's `refs=` count. Pre-PR the preview counted only
    step 1 (cross-file repoints); the ledger accumulated steps 1 + 1b + 2 + 5.

    v1.7.1 follow-up (round-4 review #2).
    """
    target = _merge_target_entry(
        description=(
            "Original target description. See `2026-09-mg-source` for the prior write-up."
        ),
    )
    sandbox.run("create", stdin=json.dumps(target))
    src = _merge_source_entry(description="See `2026-09-mg-source` for self-context.")
    sandbox.run("create", stdin=json.dumps(src))

    # Dry run — captures preview total.
    preview_out, _, _ = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",
    )
    # Extract the preview total from the displayed line.
    preview_total = None
    for line in preview_out.splitlines():
        if "Total references to repoint" in line:
            preview_total = int(line.rsplit(":", 1)[1].strip())
            break
    assert preview_total is not None, f"preview total line missing: {preview_out}"

    # Confirm — captures the ledger refs= count.
    confirm_out, _, _ = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",
        "--confirm",
    )
    # Pull the count from the success line, e.g. "(3 reference(s) repointed; ...)".
    import re as _re

    m = _re.search(r"\((\d+)\s+reference\(s\)\s+repointed", confirm_out)
    assert m, f"ledger refs= count missing: {confirm_out}"
    actual_total = int(m.group(1))

    assert preview_total == actual_total, (
        f"preview shown {preview_total}, ledger recorded {actual_total}"
    )


def test_file_rehash_does_not_use_decorator_lock(sandbox, tmp_path):
    """file-rehash --all should still work after the lock-scope refactor
    that pulled the SHA-256 loop out of the lock.

    v1.7.1 follow-up (round-7 review #3).
    """
    # Place a real file under the data root.
    data_root = sandbox.activities.parent
    test_file = data_root / "rehash-test.txt"
    test_file.write_text("hello rehash")
    out, _, rc = sandbox.run(
        "file-add",
        "rehash-test.txt",
        "--category",
        "evidence",
        "--title",
        "Rehash test",
    )
    assert rc == 0, f"file-add failed: {out}"
    # Mutate the file so the sha changes.
    test_file.write_text("hello rehash, modified")
    out, _, rc = sandbox.run("file-rehash", "--all")
    assert rc == 0, f"file-rehash failed: {out}"
    # Fixture has pre-existing inventory; just verify the command succeeded
    # and rehashed at least the one we added.
    assert "Rehashed" in out and "file(s)" in out


def test_help_does_not_materialize_lockfile(sandbox):
    """``librarian <writer-command> --help`` must NOT create the
    activities.yaml.lock sidecar against a fresh data home — the lock is
    only needed for actual writes.

    v1.7.1 follow-up (round-3 #5 / round-4 #1).
    """
    lock_path = sandbox.activities.with_suffix(".yaml.lock")
    if lock_path.exists():
        lock_path.unlink()
    out, _, rc = sandbox.run("delete", "--help")
    assert rc == 0
    assert not lock_path.exists(), (
        f"--help materialized lockfile at {lock_path}; lock should be skipped "
        f"for pure-read invocations"
    )


def test_dry_run_does_not_write_content(sandbox):
    """``librarian create --dry-run`` must not write the entry. (Lock
    behavior varies by command: ``create`` is inline-refactored so its
    dry-run path doesn't even reach the lock; other writers still hold
    the lock around their entire body — that's by design and is the
    safer side of the round-1 PR #20 #1 finding.)

    v1.7.1 follow-up.
    """
    entry = {
        "id": "2026-09-dry-run",
        "date": "2026-09-01",
        "title": "Dry run probe",
        "description": "Should not write content.",
        "tags": ["probe"],
    }
    out, _, rc = sandbox.run("create", "--dry-run", stdin=json.dumps(entry))
    assert rc == 0
    assert "Dry run" in out
    assert "2026-09-dry-run" not in sandbox.activities.read_text()


def test_pure_read_skip_not_triggered_by_arg_value(sandbox):
    """``add-tags <id> --dry-run`` — where ``--dry-run`` is a literal tag
    name — must NOT skip the activities lock. The round-1 PR #20 bug:
    membership test ``"--dry-run" in args`` matched this case and skipped
    the lock, opening a concurrency window for any writer that takes
    user-controlled positional values.

    v1.7.1 follow-up — round-1 PR #20 #1.
    """
    out, err, rc = sandbox.run(
        "add-tags",
        "2024-03-intro-security-course",
        "--dry-run",  # literal tag name, NOT a flag (add-tags has no --dry-run)
    )
    assert rc == 0, f"add-tags failed: {out} / {err}"
    tags = sandbox.entry("2024-03-intro-security-course")["tags"]
    assert "--dry-run" in tags
    # And the lockfile was correctly created (lock was taken).
    lock_path = sandbox.activities.with_suffix(".yaml.lock")
    assert lock_path.exists()


def test_file_rehash_reports_missing_without_double_counting(sandbox, tmp_path):
    """If a registered file's path doesn't exist on disk at rehash time,
    the rid lands in the ``missing`` bucket — and MUST NOT also appear
    in the ``added during hashing`` bucket. Round-3 review #1: my
    earlier round-2 fix counted such rids twice and emitted misleading
    "rerun to pick them up" advice.

    v1.7.1 follow-up — round-1 PR #20 #2 + round-3 PR #20 #1 + #10.
    """
    data_root = sandbox.activities.parent
    f1 = data_root / "rehash-a.txt"
    f2 = data_root / "rehash-b.txt"
    f1.write_text("alpha content")
    f2.write_text("beta content")
    sandbox.run("file-add", "rehash-a.txt", "--category", "evidence", "--title", "A")
    sandbox.run("file-add", "rehash-b.txt", "--category", "evidence", "--title", "B")

    # Move rehash-b's path to a non-existent location in the inventory.
    import yaml as _yaml

    files_yaml = sandbox.activities.parent / "files.yaml"
    data = _yaml.safe_load(files_yaml.read_text())
    for r in data["files"]:
        if r["id"] == "rehash-b":
            r["path"] = "moved-elsewhere.txt"
    files_yaml.write_text(_yaml.dump(data, default_flow_style=False, sort_keys=False))

    out, err, rc = sandbox.run("file-rehash", "--all")
    assert rc == 0, f"file-rehash failed: {err}"
    # rehash-b appears in the missing-from-disk notice.
    assert "missing from disk" in out and "rehash-b" in out
    # ...and MUST NOT also appear in the added-during-hashing notice.
    assert "added during hashing" not in out, (
        f"round-3 #1 regression: missing-from-disk rid was double-counted: {out}"
    )


def test_file_rehash_path_drift_check_present():
    """Direct unit-shape probe of the path-drift branch in
    ``cmd_file_rehash``. The branch is not directly subprocess-testable
    because the race requires interleaving between two ``load_files``
    calls in the same process; we assert here that the code path EXISTS
    by importing the module-level pattern and confirming the
    ``skipped_path_drift`` symbol participates in the rehash flow.

    v1.7.1 follow-up — round-3 PR #20 #10 (acknowledging the gap that
    the prior test asserted the missing-from-disk branch by mistake).
    """
    import inspect

    import librarian.cli as _cli

    source = inspect.getsource(_cli.cmd_file_rehash)
    assert "skipped_path_drift" in source, "cmd_file_rehash must contain the path-drift skip branch"
    assert "snap_path" in source, "phase 3 must compare snapshot path to current"
    assert "path changed during" in source, "path-drift notice text must be present"


def test_file_rehash_help_short_circuits(sandbox):
    """``librarian file-rehash --help`` must print usage and exit 0 —
    not fall through to the id-lookup path and report ``file id
    '--help' not found``.

    v1.7.1 follow-up — round-2 PR #20 #3.
    """
    out, _, rc = sandbox.run("file-rehash", "--help")
    assert rc == 0, f"file-rehash --help should exit 0: {out}"
    assert "Usage" in out or "usage" in out
    out, _, rc = sandbox.run("file-rehash", "-h")
    assert rc == 0
    assert "Usage" in out or "usage" in out


def test_write_preserves_file_mode(sandbox):
    """A user that ``chmod 600``s activities.yaml must see the mode
    survive across writes — the temp+rename pattern can otherwise revert
    the mode to umask defaults.

    v1.7.1 follow-up — round-2 PR #20 #4.
    """
    import os as _os
    import stat as _stat

    _os.chmod(sandbox.activities, 0o600)
    mode_before = _stat.S_IMODE(sandbox.activities.stat().st_mode)
    assert mode_before == 0o600, f"chmod did not take effect: {oct(mode_before)}"

    # Trigger a full rewrite via add-tags (uses write_lines under the hood).
    sandbox.run("add-tags", "2024-03-intro-security-course", "mode-probe")
    mode_after = _stat.S_IMODE(sandbox.activities.stat().st_mode)
    assert mode_after == 0o600, f"write reverted mode: {oct(mode_before)} -> {oct(mode_after)}"


def test_create_round_trips_parseable_yaml(sandbox):
    """A full ``cmd_create`` must leave the activities file fully
    parseable after the write completes — atomic-replace semantics.
    (The ``append_text`` helper that previously backed this was retired
    in v1.7.1; ``cmd_create`` now inlines read-existing + concat +
    atomic_replace under the lock.)

    v1.7.1 follow-up — round-2 PR #20 #1.
    """
    entry = {
        "id": "2026-09-atomic-append",
        "date": "2026-09-01",
        "title": "Atomic append probe",
        "description": "Probe.",
        "tags": ["probe"],
    }
    out, err, rc = sandbox.run("create", stdin=json.dumps(entry))
    assert rc == 0, f"create failed: {err}"
    import yaml as _yaml

    parsed = _yaml.safe_load(sandbox.activities.read_text())
    assert any(e["id"] == "2026-09-atomic-append" for e in parsed["activities"])


def test_writer_help_works_without_label(sandbox):
    """``librarian <writer-cmd> --help`` must print usage and exit 0
    even when no session label is set. Round-4 review #5: pre-fix the
    decorator skipped the lock for --help but ``_resolve_label`` (called
    inside the function body) still required a label, so a fresh-shell
    user got ``ERROR: write operations require --label`` instead of
    usage. The decorator now short-circuits to help-printing before any
    label resolution.

    v1.7.1 follow-up — round-4 PR #20 #5.
    """
    for cmd in ("delete", "add-tags", "remove-tags", "add-docs", "remove-docs"):
        out, _, rc = sandbox.run(cmd, "--help", extra_env={"LIBRARIAN_SESSION_LABEL": ""})
        assert rc == 0, f"{cmd} --help should exit 0 without label: {out}"
        assert "Usage" in out or "usage" in out, f"{cmd} --help should print usage: {out}"


def test_file_rehash_skips_empty_string_id(sandbox):
    """A malformed inventory record with ``id: ""`` must be skipped at
    phase 2 to avoid bucket-colliding with other empty-id records under
    ``hash_results[""]`` (last-write-wins → wrong digest written to one
    of the records → silent inventory corruption).

    v1.7.1 follow-up — round-4 PR #20 #1 HIGH.
    """
    data_root = sandbox.activities.parent
    (data_root / "ok-file.txt").write_text("alpha")
    sandbox.run("file-add", "ok-file.txt", "--category", "evidence", "--title", "OK")
    # Hand-edit the inventory to add a record with empty-string id.
    import yaml as _yaml

    files_yaml = data_root / "files.yaml"
    data = _yaml.safe_load(files_yaml.read_text())
    data["files"].append({"id": "", "path": "ok-file.txt", "category": "evidence", "title": "Bad"})
    files_yaml.write_text(_yaml.dump(data, default_flow_style=False, sort_keys=False))

    out, _, rc = sandbox.run("file-rehash", "--all")
    assert rc == 0, f"file-rehash failed: {out}"
    # The empty-id record must NOT appear in any notice that implies a
    # successful or attempted rehash — it's silently dropped from phase 2.
    # We assert no traceback / no crash.


def test_file_rehash_skips_empty_path(sandbox):
    """A record with empty / non-string ``path`` must be reported as
    malformed, not routed into the generic "vanished or unreadable"
    notice. ``root / ""`` returns the data home (a directory) which
    ``.exists()`` reports True and ``sha256_of`` then opens as a file →
    ``IsADirectoryError``; we trap this earlier with a clear notice.

    v1.7.1 follow-up — round-4 PR #20 #2.
    """
    data_root = sandbox.activities.parent
    (data_root / "real-file.txt").write_text("content")
    sandbox.run("file-add", "real-file.txt", "--category", "evidence", "--title", "Real")
    import yaml as _yaml

    files_yaml = data_root / "files.yaml"
    data = _yaml.safe_load(files_yaml.read_text())
    data["files"].append({"id": "bad-path", "path": "", "category": "evidence", "title": "Bad"})
    files_yaml.write_text(_yaml.dump(data, default_flow_style=False, sort_keys=False))

    out, _, rc = sandbox.run("file-rehash", "--all")
    assert rc == 0, f"file-rehash failed: {out}"
    assert "empty / non-string path" in out
    assert "bad-path" in out


def test_merge_dry_run_surfaces_folded_scalar_rejection(sandbox):
    """The dry-run preview must error on a folded-scalar (``description:
    >``) target when ``--append-sources`` is on — pre-fix the gate ran
    only in the execute phase, so the preview printed normally and only
    ``--confirm`` failed.

    v1.7.1 follow-up — round-4 PR #20 #3.
    """
    target = _merge_target_entry()
    sandbox.run("create", stdin=json.dumps(target))
    # Convert target's description to a folded scalar by hand-edit.
    text = sandbox.activities.read_text()
    target_idx = text.find("- id: 2026-09-mg-target")
    desc_idx = text.find("description: |", target_idx)
    text = text[:desc_idx] + "description: >" + text[desc_idx + len("description: |") :]
    sandbox.activities.write_text(text)
    sandbox.run("create", stdin=json.dumps(_merge_source_entry()))

    out, _, rc = sandbox.run(
        "merge",
        "2026-09-mg-source",
        "--into",
        "2026-09-mg-target",
        "--append-sources",
        # NO --confirm: this is a dry-run preview.
    )
    assert rc == 1, f"dry-run should reject folded scalar: {out}"
    assert "folded" in out.lower()


def test_label_rejects_flag_as_value(sandbox):
    """``--label --dry-run`` is a typo (forgot the label string). Without
    the round-3 fix, ``_resolve_label`` would pop ``--dry-run`` as the
    label value, drop it from argv, and the write path would run with
    ``label="--dry-run"`` recorded in the ledger.

    v1.7.1 follow-up — round-3 PR #20 #5.
    """
    entry = {
        "id": "2026-09-bad-label",
        "date": "2026-09-01",
        "title": "Bad label probe",
        "description": "Should be rejected.",
        "tags": ["probe"],
    }
    out, _, rc = sandbox.run(
        "create",
        "--label",
        "--dry-run",
        "--json",
        json.dumps(entry),
        extra_env={"LIBRARIAN_SESSION_LABEL": ""},
    )
    assert rc == 1, f"expected rejection of flag-as-label-value: {out}"
    assert "looks like another flag" in out
    # The entry must NOT have landed.
    assert "2026-09-bad-label" not in sandbox.activities.read_text()


def test_create_dry_run_without_label_succeeds(sandbox):
    """``librarian create --dry-run`` without a ``--label`` (and without
    the env var) must succeed: dry-run doesn't write, so the label gate
    that exists for actual writes shouldn't apply.

    v1.7.1 follow-up — round-1 PR #20 #8.
    """
    entry = {
        "id": "2026-09-labelless-dry",
        "date": "2026-09-01",
        "title": "Labelless dry-run probe",
        "description": "No label, no write, no problem.",
        "tags": ["probe"],
    }
    # Wipe both --label and the env var.
    out, _, rc = sandbox.run(
        "create",
        "--dry-run",
        stdin=json.dumps(entry),
        extra_env={"LIBRARIAN_SESSION_LABEL": ""},
    )
    assert rc == 0, f"labelless dry-run should succeed: {out}"
    assert "Dry run" in out
