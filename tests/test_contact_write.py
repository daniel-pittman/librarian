"""Tests for the contact and description editors: ``edit-description`` and
``contact-update``.

Entries are appended to the sandbox copy of the synthetic corpus, so each test
controls exactly how a description is stored (literal block, quoted scalar).
All names and emails are fictional.
"""

from __future__ import annotations

import textwrap

ALEX = "arivera@example.edu"


def _append(sandbox, yaml_text: str) -> None:
    """Append entry YAML to the corpus, indented to sit under ``activities:``.

    ``yaml_text`` is written with its first ``- id:`` line at any indent; the
    block is dedented and re-indented to the corpus's two-space list indent.
    """
    block = textwrap.indent(textwrap.dedent(yaml_text).strip("\n"), "  ")
    with open(sandbox.activities, "a", encoding="utf-8") as fh:
        fh.write("\n" + block + "\n")


def _literal_entry(entry_id: str, body: str) -> str:
    """A minimal entry whose description is stored as a literal block."""
    indented = textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
    return (
        f"- id: {entry_id}\n"
        "  date: '2026-01-10'\n"
        f"  title: Title of {entry_id}\n"
        "  description: |\n"
        f"{indented}\n"
        "  tags:\n"
        "    - sample\n"
    )


def _ledger(sandbox) -> list[str]:
    """Current ledger lines (empty when nothing has been written)."""
    if not sandbox.ledger.exists():
        return []
    return sandbox.ledger.read_text().splitlines()


def _desc(sandbox, entry_id: str) -> str:
    return sandbox.entry(entry_id)["description"]


# ---------------------------------------------------------------------------
# edit-description
# ---------------------------------------------------------------------------


def test_edit_description_replaces_single_occurrence(sandbox):
    """A passage that occurs once is replaced and the rest is untouched."""
    before = _desc(sandbox, "2025-02-conference-talk")
    out, _, rc = sandbox.run(
        "edit-description",
        "2025-02-conference-talk",
        "--old",
        "30-minute conference talk",
        "--new",
        "45-minute keynote",
    )
    assert rc == 0, out
    after = _desc(sandbox, "2025-02-conference-talk")
    assert after.strip() == before.strip().replace("30-minute conference talk", "45-minute keynote")
    assert "189 chars" in out or "chars)" in out


def test_edit_description_ledger_records_replacement_size(sandbox):
    """The ledger line carries old/new/final sizes, not only the final length."""
    sandbox.run(
        "edit-description",
        "2025-02-conference-talk",
        "--old",
        "30-minute",
        "--new",
        "45-minute talk of",
    )
    final = len(_desc(sandbox, "2025-02-conference-talk").strip())
    line = _ledger(sandbox)[-1]
    assert "edit-description" in line
    assert "2025-02-conference-talk" in line
    assert f"old_chars=9 new_chars=17 chars={final}" in line


def test_edit_description_zero_matches_refused(sandbox):
    """No match: error names the count, nothing written, ledger unchanged."""
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run(
        "edit-description", "2025-02-conference-talk", "--old", "no such text", "--new", "x"
    )
    assert rc == 1
    assert "found 0" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_edit_description_multiple_matches_refused(sandbox):
    """Two matches: error names the count and nothing is written."""
    _append(sandbox, _literal_entry("2026-01-dup", "alpha beta alpha gamma"))
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run("edit-description", "2026-01-dup", "--old", "alpha", "--new", "omega")
    assert rc == 1
    assert "found 2" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_edit_description_empty_old_refused(sandbox):
    """An empty --old is an error rather than matching everywhere."""
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "edit-description", "2025-02-conference-talk", "--old", "", "--new", "x"
    )
    assert rc == 1
    assert "must not be empty" in out
    assert sandbox.activities.read_text() == activities_before


def test_edit_description_unknown_entry(sandbox):
    out, _, rc = sandbox.run("edit-description", "no-such-entry", "--old", "a", "--new", "b")
    assert rc == 1
    assert "not found" in out


def test_edit_description_dry_run_writes_nothing_and_needs_no_label(sandbox):
    """--dry-run prints the passage in context and works without a label."""
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run(
        "edit-description",
        "2025-02-conference-talk",
        "--old",
        "30-minute",
        "--new",
        "45-minute",
        "--dry-run",
        extra_env={"LIBRARIAN_SESSION_LABEL": ""},
    )
    assert rc == 0, out
    assert "[30-minute]" in out
    assert "[45-minute]" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_edit_description_write_requires_label(sandbox):
    """A real write without a label is refused and writes nothing."""
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "edit-description",
        "2025-02-conference-talk",
        "--old",
        "30-minute",
        "--new",
        "45-minute",
        extra_env={"LIBRARIAN_SESSION_LABEL": ""},
    )
    assert rc == 1
    assert "require --label" in out
    assert sandbox.activities.read_text() == activities_before


def test_edit_description_quoted_scalar_becomes_literal_block(sandbox):
    """A quoted-scalar description is read correctly and rewritten as a literal block."""
    _append(
        sandbox,
        """
          - id: 2026-01-quoted
            date: '2026-01-11'
            title: Quoted description
            description: "Met Pat Lee; wrote the plan, then \\"shipped\\" it."
            tags:
              - sample
        """,
    )
    out, _, rc = sandbox.run(
        "edit-description", "2026-01-quoted", "--old", "wrote the plan", "--new", "drafted the plan"
    )
    assert rc == 0, out
    assert (
        _desc(sandbox, "2026-01-quoted").strip()
        == 'Met Pat Lee; drafted the plan, then "shipped" it.'
    )
    assert "description: |" in sandbox.activities.read_text().split("2026-01-quoted", 1)[1]
    _, _, rc = sandbox.run("validate")
    assert rc == 0


def test_edit_description_literal_block_keeps_paragraphs_and_siblings(sandbox):
    """Paragraph breaks survive and neighbouring entries are byte-identical."""
    _append(sandbox, _literal_entry("2026-01-para", "First paragraph.\n\nSecond paragraph here."))
    others_before = {e["id"]: e for e in sandbox.load_activities() if e["id"] != "2026-01-para"}
    _, _, rc = sandbox.run("edit-description", "2026-01-para", "--old", "Second", "--new", "Final")
    assert rc == 0
    assert _desc(sandbox, "2026-01-para").strip() == "First paragraph.\n\nFinal paragraph here."
    for e in sandbox.load_activities():
        if e["id"] != "2026-01-para":
            assert e == others_before[e["id"]]


def test_update_description_still_replaces_whole_description(sandbox):
    """update-description keeps its behavior after sharing the splice helper."""
    out, _, rc = sandbox.run(
        "update-description", "2025-02-conference-talk", stdin="Entirely new text.\n\nTwo paras.\n"
    )
    assert rc == 0
    assert "(" in out and "chars)" in out
    assert _desc(sandbox, "2025-02-conference-talk") == "Entirely new text.\n\nTwo paras.\n"
    assert "update-description" in _ledger(sandbox)[-1]
    out, _, rc = sandbox.run("update-description", "no-such-entry", stdin="x")
    assert rc == 1
    assert "ERROR: entry 'no-such-entry' not found" in out


# ---------------------------------------------------------------------------
# contact-update
# ---------------------------------------------------------------------------


def _seed_alex(sandbox) -> None:
    """Three entries mentioning one email, plus bystander text that must not change."""
    _append(
        sandbox,
        _literal_entry(
            "2026-02-alex-one",
            """
            Advised the capstone team. Dr. Alexandra Rivera (arivera@example.edu) signed off.
            """,
        )
        + "\n"
        + _literal_entry(
            "2026-02-alex-two",
            """
            Author list: Alexandra Rivera, Pat Lee.
            Logistics by Alexandra Rivera (ARIVERA@example.edu) and a second note.
            """,
        )
        + "\n"
        + _literal_entry(
            "2026-02-alex-three",
            """
            Outcome reviewed by Alexandra Rivera <arivera@example.edu> in May.
            """,
        ),
    )


def test_contact_update_dry_run_is_default(sandbox):
    """Without --confirm nothing is written, and the preview lists each mention."""
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run(
        "contact-update",
        ALEX,
        "--name",
        "Alex Rivera",
        extra_env={"LIBRARIAN_SESSION_LABEL": ""},
    )
    assert rc == 0, out
    assert "2026-02-alex-one: Dr. Alexandra Rivera -> Alex Rivera" in out
    assert "2026-02-alex-two: Alexandra Rivera -> Alex Rivera" in out
    assert "2026-02-alex-three: Alexandra Rivera -> Alex Rivera" in out
    assert "3 entries changed, 3 mentions changed, 0 unchanged, 0 skipped" in out
    assert "Dry run" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_contact_update_dry_run_wins_over_confirm(sandbox):
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "contact-update", ALEX, "--name", "Alex Rivera", "--confirm", "--dry-run"
    )
    assert rc == 0
    assert "Dry run" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_confirm_rewrites_mentions_across_entries(sandbox):
    """--confirm changes only the name spans, with one ledger line per entry."""
    _seed_alex(sandbox)
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run("contact-update", ALEX, "--name", "Alex Rivera", "--confirm")
    assert rc == 0, out
    # A title prefix is part of the derived name and is replaced with it.
    assert "Advised the capstone team. Alex Rivera (arivera@example.edu) signed off." in _desc(
        sandbox, "2026-02-alex-one"
    )
    two = _desc(sandbox, "2026-02-alex-two")
    # The byline mention of the same name without the email is left alone.
    assert "Author list: Alexandra Rivera, Pat Lee." in two
    assert "Logistics by Alex Rivera (ARIVERA@example.edu) and a second note." in two
    assert "by Alex Rivera <arivera@example.edu> in May." in _desc(sandbox, "2026-02-alex-three")

    new_lines = _ledger(sandbox)[len(ledger_before) :]
    assert len(new_lines) == 3
    assert all("contact-update" in line for line in new_lines)
    assert all(f"email={ALEX} mentions=1" in line for line in new_lines)
    assert {
        e for line in new_lines for e in ("alex-one", "alex-two", "alex-three") if e in line
    } == {
        "alex-one",
        "alex-two",
        "alex-three",
    }

    out, _, rc = sandbox.run("contact", ALEX)
    assert rc == 0
    assert "Alex Rivera" in out
    assert "Alexandra" not in out
    _, _, rc = sandbox.run("validate")
    assert rc == 0


def test_contact_update_confirm_requires_label(sandbox):
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "contact-update",
        ALEX,
        "--name",
        "Alex Rivera",
        "--confirm",
        extra_env={"LIBRARIAN_SESSION_LABEL": ""},
    )
    assert rc == 1
    assert "require --label" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_skips_email_without_a_name(sandbox):
    """A mention with no two-word name before it is reported and left untouched."""
    _seed_alex(sandbox)
    _append(
        sandbox,
        _literal_entry("2026-02-alex-bare", "Contact the office (arivera@example.edu) for access."),
    )
    out, _, rc = sandbox.run("contact-update", ALEX, "--name", "Alex Rivera", "--confirm")
    assert rc == 0, out
    assert "Skipped (could not locate name safely)" in out
    assert "2026-02-alex-bare" in out
    assert "3 entries changed, 3 mentions changed, 0 unchanged, 1 skipped" in out
    assert _desc(sandbox, "2026-02-alex-bare").strip() == (
        "Contact the office (arivera@example.edu) for access."
    )


def test_contact_update_noop_when_name_already_matches(sandbox):
    """If every mention already has the new name, say so and write nothing."""
    _append(
        sandbox,
        _literal_entry("2026-02-same", "Talked with Alex Rivera (arivera@example.edu) today."),
    )
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run("contact-update", ALEX, "--name", "Alex Rivera", "--confirm")
    assert rc == 0
    assert "(unchanged)" in out
    assert "Nothing to change." in out
    assert "0 entries changed, 0 mentions changed, 1 unchanged, 0 skipped" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_contact_update_counts_multiple_mentions_in_one_entry(sandbox):
    """Two mentions in one entry produce one ledger line with mentions=2."""
    _append(
        sandbox,
        _literal_entry(
            "2026-02-twice",
            "Alexandra Rivera (arivera@example.edu) opened; later Alexandra Rivera "
            "<arivera@example.edu> closed.",
        ),
    )
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run("contact-update", ALEX, "--name", "Alex Rivera", "--confirm")
    assert rc == 0, out
    assert _desc(sandbox, "2026-02-twice").strip() == (
        "Alex Rivera (arivera@example.edu) opened; later Alex Rivera <arivera@example.edu> closed."
    )
    new_lines = _ledger(sandbox)[len(ledger_before) :]
    assert len(new_lines) == 1
    assert f"email={ALEX} mentions=2" in new_lines[0]


def test_contact_update_unknown_email_returns_1(sandbox):
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run("contact-update", "nobody@example.org", "--name", "No Body")
    assert rc == 1
    assert "no 'Name (nobody@example.org' mention" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_rejects_single_word_name(sandbox):
    """A one-word name would vanish from the rolodex, so it is refused."""
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run("contact-update", ALEX, "--name", "Alex", "--confirm")
    assert rc == 1
    assert "two or more words" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_handles_quoted_scalar_description(sandbox):
    """A quoted-scalar description is read, rewritten as a literal block, and stays valid."""
    _append(
        sandbox,
        """
          - id: 2026-02-quoted
            date: '2026-02-11'
            title: Quoted description
            description: "Reviewed by Alexandra Rivera (arivera@example.edu): approved."
            tags:
              - sample
        """,
    )
    out, _, rc = sandbox.run("contact-update", ALEX, "--name", "Alex Rivera", "--confirm")
    assert rc == 0, out
    assert (
        _desc(sandbox, "2026-02-quoted").strip()
        == "Reviewed by Alex Rivera (arivera@example.edu): approved."
    )
    _, _, rc = sandbox.run("validate")
    assert rc == 0
