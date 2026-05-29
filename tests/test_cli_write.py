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
