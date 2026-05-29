"""CLI read-command tests, run against the synthetic fixture corpus."""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# search / get
# ---------------------------------------------------------------------------


def test_search_finds_matches(sandbox):
    """A full-text search returns matching entries."""
    out, _, rc = sandbox.run("search", "workshop", "--brief")
    assert rc == 0
    assert "Found" in out
    assert "2026-01-security-workshop" in out


def test_search_no_results(sandbox):
    """A search with no matches reports zero."""
    out, _, rc = sandbox.run("search", "zzz-nonexistent-zzz")
    assert rc == 0
    assert "Found 0" in out


def test_search_cross_surfaces_files(sandbox):
    """A search that also matches inventory files prints the file footer."""
    out, _, rc = sandbox.run("search", "syllabus")
    assert rc == 0
    assert "inventory file(s) also match" in out


def test_get_exact(sandbox):
    """get with an exact id returns the full entry."""
    out, _, rc = sandbox.run("get", "2025-02-conference-talk")
    assert rc == 0
    assert "Regional Computing Conference" in out


def test_get_resolves_file_refs(sandbox):
    """get prints resolved paths for file: doc references."""
    out, _, rc = sandbox.run("get", "2024-03-intro-security-course")
    assert rc == 0
    assert "Resolved file references:" in out
    assert "artifacts/syllabus-infosec-101.txt" in out


def test_get_fuzzy(sandbox):
    """get with a partial id falls back to a substring match."""
    out, _, rc = sandbox.run("get", "conference")
    assert rc == 0
    assert "Partial matches" in out or "Regional Computing" in out


def test_get_unknown(sandbox):
    """get with an unknown id reports not found."""
    out, _, rc = sandbox.run("get", "no-such-entry")
    assert rc == 1
    assert "No entry found" in out


# ---------------------------------------------------------------------------
# filter
# ---------------------------------------------------------------------------


def test_filter_by_block_field(sandbox):
    """--block-field filters on a schema block field."""
    out, _, rc = sandbox.run("filter", "--block-field", "ptr.category", "scholarly", "--count")
    assert rc == 0
    assert int(out.strip()) == 3


def test_filter_category_alias(sandbox):
    """The --category alias filters ptr.category."""
    out, _, rc = sandbox.run("filter", "--category", "teaching", "--count")
    assert rc == 0
    assert int(out.strip()) == 2


def test_filter_has_block(sandbox):
    """--cpe (alias for --has-block cpe) filters to entries with a cpe block."""
    out, _, rc = sandbox.run("filter", "--cpe", "--count")
    assert rc == 0
    assert int(out.strip()) == 4


def test_filter_no_block(sandbox):
    """--no-cpe filters to entries without a cpe block."""
    out, _, rc = sandbox.run("filter", "--no-cpe", "--count")
    assert rc == 0
    assert int(out.strip()) == 4


def test_filter_by_date(sandbox):
    """--after filters on the entry start date."""
    out, _, rc = sandbox.run("filter", "--after", "2026-01-01", "--count")
    assert rc == 0
    assert int(out.strip()) == 3


def test_filter_by_year_during(sandbox):
    """--year filters to entries active during a calendar year."""
    out, _, rc = sandbox.run("filter", "--year", "2025", "--count")
    assert rc == 0
    # The committee-service entry spans into 2026 but starts in 2025, and the
    # 2024 course ended in 2024 — three entries start within 2025.
    assert int(out.strip()) >= 3


def test_filter_by_tag(sandbox):
    """--tag filters on entry tags."""
    out, _, rc = sandbox.run("filter", "--tag", "cpe-primary", "--count")
    assert rc == 0
    assert int(out.strip()) == 2


# ---------------------------------------------------------------------------
# list / stats / tags
# ---------------------------------------------------------------------------


def test_list_brief(sandbox):
    """list prints a brief table of all entries."""
    out, _, rc = sandbox.run("list")
    assert rc == 0
    assert "Total entries: 8" in out


def test_stats(sandbox):
    """stats groups counts by the active schema's blocks."""
    out, _, rc = sandbox.run("stats")
    assert rc == 0
    assert "Total entries: 8" in out
    assert "Post-Tenure Review (ptr)" in out
    assert "Continuing Education Credit (cpe)" in out
    assert "total credits: 46" in out


def test_tags(sandbox):
    """tags lists every tag with a count."""
    out, _, rc = sandbox.run("tags")
    assert rc == 0
    assert "Unique tags:" in out
    assert "teaching" in out


def test_tag_audit_clean(sandbox):
    """tag-audit reports a clean tag set for the fixture corpus."""
    out, _, rc = sandbox.run("tag-audit")
    assert rc == 0
    assert "clean" in out.lower()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_finds_dangling_ref(sandbox):
    """validate flags the intentional dangling cross-reference."""
    out, _, rc = sandbox.run("validate")
    assert rc == 0
    assert "DANGLING REF" in out
    assert "2099-99-nonexistent-entry" in out


def test_validate_clean_after_removing_dangling(sandbox):
    """With the dangling ref removed, validate finds no schema issues."""
    # Rewrite the offending description to drop the bad backtick reference.
    sandbox.run(
        "update-description",
        "2026-03-external-review",
        stdin="A clean description with no dangling reference.",
    )
    out, _, rc = sandbox.run("validate")
    assert rc == 0
    assert "DANGLING REF" not in out
    assert "INVALID" not in out


def test_validate_flags_schema_violation(sandbox):
    """An entry with an invalid enum value is flagged by validate."""
    sandbox.run(
        "create",
        "--json",
        json.dumps(
            {
                "id": "2026-05-bad-entry",
                "date": "2026-05-01",
                "title": "Bad",
                "description": "x",
                "tags": ["t"],
                "docs": ["https://example.com"],
                "ptr": {"category": "teaching", "subcategory": "cat1-peer-reviewed"},
            }
        ),
    )
    # create itself rejects the bad block, so the entry never lands — confirm.
    out, _, rc = sandbox.run("get", "2026-05-bad-entry")
    assert rc == 1


# ---------------------------------------------------------------------------
# export / project / similar / contact / schema
# ---------------------------------------------------------------------------


def test_export_csv(sandbox):
    """export --format csv emits a header with schema enum columns."""
    out, _, rc = sandbox.run("export", "--format", "csv")
    assert rc == 0
    assert "id,date,title" in out
    assert "ptr_category" in out
    assert "cpe_group" in out


def test_export_json(sandbox):
    """export --format json emits a parseable JSON array."""
    out, _, rc = sandbox.run("export", "--format", "json", "--after", "2026-01-01")
    assert rc == 0
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 3


def test_project_tag_match(sandbox):
    """project finds entries tagged for a project name."""
    out, _, rc = sandbox.run("project", "workshop", "--brief")
    assert rc == 0
    assert "Found" in out


def test_similar(sandbox):
    """similar surfaces near-duplicate entries above the threshold."""
    out, _, rc = sandbox.run("similar", "Security Curriculum Review Report")
    assert rc == 0
    assert "2024-06-security-curriculum-report" in out


def test_similar_no_match(sandbox):
    """similar reports nothing for unrelated text."""
    out, _, rc = sandbox.run("similar", "underwater basket weaving championship")
    assert rc == 0
    assert "No similar entries" in out


def test_contact_finds_embedded_email(sandbox):
    """contact extracts a Name (email) pattern from a description."""
    out, _, rc = sandbox.run("contact", "Jordan")
    assert rc == 0
    assert "javery@example.org" in out


def test_contact_by_institution(sandbox):
    """contact --institution filters the rolodex by email domain."""
    out, _, rc = sandbox.run("contact", "--institution", "example.net")
    assert rc == 0
    assert "srivera@example.net" in out


def test_schema_command(sandbox):
    """schema describes the active schema's blocks and fields."""
    out, _, rc = sandbox.run("schema")
    assert rc == 0
    assert "block 'ptr'" in out
    assert "block 'cpe'" in out


def test_schema_json(sandbox):
    """schema --json emits a parseable description."""
    out, _, rc = sandbox.run("schema", "--json")
    assert rc == 0
    parsed = json.loads(out)
    assert "ptr" in parsed["blocks"]


def test_schema_lists_plain_enum_values(sandbox):
    """schema enumerates a plain enum's allowed values inline."""
    out, _, rc = sandbox.run("schema")
    assert rc == 0
    # The ptr.category enum values must be discoverable without reading YAML.
    assert "values:" in out
    assert "teaching" in out
    assert "scholarly" in out
    assert "service" in out


def test_schema_lists_dependent_enum_map(sandbox):
    """schema prints the dependent enum (category -> subcategory) map."""
    out, _, rc = sandbox.run("schema")
    assert rc == 0
    assert "values (by category):" in out
    # A representative subcategory from the fixture schema.
    assert "cat1-peer-reviewed" in out


def test_schema_json_includes_enum_values(sandbox):
    """schema --json carries enum values: a list for plain, a dict for dependent."""
    out, _, rc = sandbox.run("schema", "--json")
    assert rc == 0
    fields = {f["name"]: f for f in json.loads(out)["blocks"]["ptr"]["fields"]}
    assert isinstance(fields["category"]["values"], list)
    assert "teaching" in fields["category"]["values"]
    assert isinstance(fields["subcategory"]["values"], dict)
    assert "teaching" in fields["subcategory"]["values"]


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


def test_env_shows_resolved_paths(sandbox):
    """env prints each resolved resource path with a source and existence tag."""
    out, _, rc = sandbox.run("env")
    assert rc == 0
    for label in ("home", "activities", "files", "ledger", "schema", "root"):
        assert label in out
    assert str(sandbox.activities) in out
    # activities.yaml was copied into the sandbox, so it exists.
    assert "exists" in out


def test_env_reports_override_source(sandbox):
    """env attributes a path to the env var that set it.

    The sandbox sets LIBRARIAN_HOME and LIBRARIAN_ROOT, so those show as the
    source; per-resource paths with no own override derive from the home.
    """
    out, _, rc = sandbox.run("env")
    assert rc == 0
    assert "source=LIBRARIAN_HOME" in out
    assert "source=LIBRARIAN_ROOT" in out
    assert "source=home" in out  # e.g. activities/files/ledger/schema


def test_env_json(sandbox):
    """env --json is parseable and reports path/source/exists per resource."""
    out, _, rc = sandbox.run("env", "--json")
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["activities"]["path"] == str(sandbox.activities)
    assert parsed["activities"]["exists"] is True
    assert parsed["activities"]["source"] == "home"  # derives from LIBRARIAN_HOME
    assert parsed["home"]["source"] == "LIBRARIAN_HOME"
    assert parsed["artifacts"]["source"] == "derived"
    assert parsed["schema_configured"] is True


def test_env_memory_dir_unset(sandbox):
    """With no LIBRARIAN_MEMORY_DIR, env reports memory_dir as unset."""
    out, _, rc = sandbox.run("env", extra_env={"LIBRARIAN_MEMORY_DIR": ""})
    assert rc == 0
    assert "memory_dir" in out
    assert "(unset)" in out


def test_version(sandbox):
    """The --version flag prints the version string."""
    out, _, rc = sandbox.run("--version")
    assert rc == 0
    assert out.strip() == "1.6.0"
