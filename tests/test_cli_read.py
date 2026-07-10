"""CLI read-command tests, run against the synthetic fixture corpus."""

from __future__ import annotations

import json
import os
from pathlib import Path

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


# A minimal schema + corpus exercising the bool-as-int edge in the stats int
# summation. Fictional data only.
_BOOL_INT_SCHEMA = """\
name: Bool int test schema
description: Stats int-coercion fixture.
blocks:
  grant:
    label: Grant / Funding
    fields:
      - name: amount
        type: int
"""

_BOOL_INT_ACTIVITIES = """\
activities:
  - id: 2025-01-real-amount
    date: '2025-01-15'
    title: Real amount
    description: A real int amount.
    tags: []
    docs: []
    grant:
      amount: 5
  - id: 2025-02-bool-amount
    date: '2025-02-15'
    title: Bool amount
    description: A bool-valued int field.
    tags: []
    docs: []
    grant:
      amount: true
"""


def test_stats_int_total_excludes_bool(sandbox):
    """A bool-valued int field contributes 0 to a stats total, not 1.

    cmd_stats now sums int block fields through core._intish, which excludes
    ``bool`` — so ``amount: true`` adds 0, leaving the total at the single real
    ``amount: 5``. (The old inline ``isinstance(raw, int)`` path counted the
    bool as 1, giving 6.)
    """
    sandbox.schema.write_text(_BOOL_INT_SCHEMA)
    sandbox.activities.write_text(_BOOL_INT_ACTIVITIES)
    out, _, rc = sandbox.run("stats")
    assert rc == 0
    assert "total amount: 5" in out


# ---------------------------------------------------------------------------
# rollup
# ---------------------------------------------------------------------------

# A small grant schema + corpus written into the sandbox for the rollup tests.
# Fictional data only: Dr. Jane Roe at Acme University, National Science Org.
_GRANT_SCHEMA = """\
name: Grant test schema
description: Grant rollup fixture.
blocks:
  grant:
    label: Grant / Funding
    fields:
      - name: amount
        type: int
      - name: role
        type: enum
        values: [pi, co-pi, co-i, senior-personnel, consultant, collaborator]
      - name: status
        type: enum
        required: true
        values: [awarded, pending, not-funded]
      - name: sponsor
        type: string
"""

_GRANT_ACTIVITIES = """\
activities:
  - id: 2025-01-grant-pi
    date: '2025-01-15'
    title: Funded study
    description: A funded study.
    tags: [grant, research]
    docs: []
    grant:
      amount: 500000
      role: pi
      status: awarded
      sponsor: National Science Org
  - id: 2025-02-grant-copi
    date: '2025-02-20'
    title: Pending proposal
    description: A pending proposal.
    tags: [grant]
    docs: []
    grant:
      amount: "250000"
      role: co-pi
      status: pending
      sponsor: Acme University
  - id: 2025-03-grant-seed
    date: '2025-03-10'
    title: Internal seed award
    description: Seed funding.
    tags: [grant, research]
    docs: []
    grant:
      amount: 10000
      role: pi
      status: awarded
      sponsor: Acme University
  - id: 2025-04-plain
    date: '2025-04-01'
    title: Not a grant
    description: A non-grant entry.
    tags: []
    docs: []
"""


def _grant_sandbox(sandbox):
    """Replace the sandbox schema + activities with the grant corpus."""
    sandbox.schema.write_text(_GRANT_SCHEMA)
    sandbox.activities.write_text(_GRANT_ACTIVITIES)
    return sandbox


def test_rollup_sum(sandbox):
    """rollup --sum totals an int block field across grant entries."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "grant", "--sum", "amount")
    assert rc == 0
    assert "count: 3" in out
    assert "sum(amount): 760,000" in out


def test_rollup_group_by(sandbox):
    """rollup --group-by breaks the rollup down with per-group sums."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "grant", "--sum", "amount", "--group-by", "status")
    assert rc == 0
    assert "by status:" in out
    assert "awarded" in out
    assert "pending" in out


def test_rollup_json(sandbox):
    """rollup --json emits the machine-readable rollup_entries dict."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "grant", "--sum", "amount", "--group-by", "status", "--json")
    assert rc == 0
    data = json.loads(out)
    assert data["block"] == "grant"
    assert data["count"] == 3
    assert data["sum"] == 760000
    assert data["groups"]["awarded"] == {"count": 2, "sum": 510000}
    assert data["groups"]["pending"] == {"count": 1, "sum": 250000}


def test_rollup_filtered_by_tag_and_block_field(sandbox):
    """rollup honours --tag and --block-field scoping before aggregating."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run(
        "rollup", "grant", "--sum", "amount", "--block-field", "grant.status", "awarded", "--json"
    )
    assert rc == 0
    data = json.loads(out)
    # Only the two awarded grants (510000) survive the block-field filter.
    assert data["count"] == 2
    assert data["sum"] == 510000

    out, _, rc = sandbox.run("rollup", "grant", "--tag", "research", "--json")
    assert rc == 0
    assert json.loads(out)["count"] == 2


def test_rollup_non_int_sum_field_errors(sandbox):
    """rollup --sum on a non-int schema field fails with a friendly error."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "grant", "--sum", "sponsor")
    assert rc == 1
    assert "not int" in out


def test_rollup_unknown_block_warns_but_runs(sandbox):
    """rollup on a block absent from the schema warns but still runs."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "nosuchblock")
    assert rc == 0
    assert "WARNING" in out
    assert "count: 0" in out


def test_rollup_honours_date_filter(sandbox):
    """rollup scopes the set with --after / --before before aggregating."""
    sandbox = _grant_sandbox(sandbox)
    # The corpus has grant entries dated 2025-01-15, 2025-02-20, 2025-03-10.
    # --after 2025-01-31 drops the January grant; --before 2025-03-01 drops
    # the March grant, leaving only the pending February proposal (250000).
    out, _, rc = sandbox.run(
        "rollup",
        "grant",
        "--sum",
        "amount",
        "--after",
        "2025-01-31",
        "--before",
        "2025-03-01",
        "--json",
    )
    assert rc == 0
    data = json.loads(out)
    assert data["count"] == 1
    assert data["sum"] == 250000


def test_rollup_malformed_block_field_errors(sandbox):
    """rollup --block-field without a '.' prints the BLOCK.FIELD error and fails."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "grant", "--block-field", "status", "awarded")
    assert rc != 0
    assert "BLOCK.FIELD" in out


def test_rollup_unknown_group_by_field_warns(sandbox):
    """rollup --group-by on a field absent from the block warns but still runs."""
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "grant", "--group-by", "nosuchfield")
    assert rc == 0
    assert "WARNING" in out
    assert "(unset)" in out


def test_rollup_unknown_sum_field_warns(sandbox):
    """rollup --sum on a field absent from a known block warns but still runs.

    A typo like ``--sum amountt`` is not a non-int field (so it isn't a hard
    error) but silently totals to 0; the warning surfaces that instead.
    """
    sandbox = _grant_sandbox(sandbox)
    out, _, rc = sandbox.run("rollup", "grant", "--sum", "amountt")
    assert rc == 0
    assert "WARNING" in out
    assert "sum(amountt): 0" in out


def test_tags(sandbox):
    """tags lists every tag with a count."""
    out, _, rc = sandbox.run("tags")
    assert rc == 0
    assert "Unique tags:" in out
    assert "teaching" in out


def test_tags_survives_numeric_tag_in_yaml(sandbox):
    """A bare-numeric tag (unquoted integer in the on-disk YAML) must not
    crash ``librarian tags``. YAML parses ``2026`` as an ``int`` and the
    ``{tag:45s}`` format used to raise ``ValueError: Unknown format code
    's' for object of type 'int'``. Issue #37 (and #39, its duplicate).

    v1.8.2 fix: ``load_activities`` coerces every tag to ``str`` at the
    single load boundary, and ``cmd_tags`` is defensively ``str(tag)``.
    """
    # Inject a numeric tag directly into the YAML — the CLI would normally
    # quote strings on write, so this simulates a hand-edit.
    text = sandbox.activities.read_text()
    marker = "- teaching"
    assert marker in text, "fixture layout drift"
    # Insert a bare-integer tag as a peer of the first ``- teaching`` line.
    text = text.replace(marker, marker + "\n      - 2026", 1)
    sandbox.activities.write_text(text)
    out, err, rc = sandbox.run("tags")
    assert rc == 0, f"tags crashed on numeric tag: {out} / {err}"
    assert "Unique tags:" in out
    # The numeric tag surfaces in the listing as its string form.
    assert "2026" in out


def test_tag_audit_survives_numeric_tag_in_yaml(sandbox):
    """Same coercion invariant for ``tag-audit`` (uses ``tag_kernel``,
    which calls ``.casefold()`` on each tag — fails on int without the
    load-time coercion)."""
    text = sandbox.activities.read_text()
    text = text.replace("- teaching", "- teaching\n      - 2026", 1)
    sandbox.activities.write_text(text)
    out, _, rc = sandbox.run("tag-audit")
    assert rc == 0, f"tag-audit crashed on numeric tag: {out}"


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
    """The --version flag prints the version string from the VERSION file."""
    from pathlib import Path

    expected = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    out, _, rc = sandbox.run("--version")
    assert rc == 0
    assert out.strip() == expected


def test_empty_home_hint_fires_on_bare_default(tmp_path):
    """A user who runs the bare CLI without any LIBRARIAN_* env var set,
    against an empty XDG default home, gets a helpful hint pointing at
    the resolved path — not silent zero-result output that reads as "the
    entry doesn't exist".

    v1.8.2 operational fix from the CLI-vs-MCP paths note: fresh sessions
    that fall back to the CLI won't guess an entry is missing when it
    just isn't in the default home.
    """
    import subprocess
    import sys

    # Point XDG_CONFIG_HOME at a fresh empty directory so the default home
    # resolves to something guaranteed empty, and wipe every LIBRARIAN_*
    # override so the "any override set" gate is false.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "XDG_CONFIG_HOME": str(tmp_path),
    }
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "librarian.cli", "list"],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo_root,
    )
    assert result.returncode == 0
    # The count-header still prints normally.
    assert "Total entries: 0" in result.stdout
    # The hint goes to stderr so it doesn't corrupt piped/scripted stdout.
    assert "LIBRARIAN_YAML_PATH" in result.stderr or "LIBRARIAN_HOME" in result.stderr
    assert "librarian env" in result.stderr


def test_empty_home_hint_silent_when_override_set(sandbox):
    """When LIBRARIAN_HOME is set (as the sandbox fixture does), an empty
    result set must NOT trigger the hint — the user configured a path,
    they own the emptiness."""
    # A search that matches nothing yields zero results but shouldn't hint.
    _, err, rc = sandbox.run("search", "zzzzz-no-such-token-anywhere")
    assert rc == 0
    assert "LIBRARIAN_YAML_PATH" not in err
    assert "librarian env" not in err
