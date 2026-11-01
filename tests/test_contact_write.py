"""Tests for the contact and description editors: ``edit-description`` and
``contact-update``.

Entries are appended to the sandbox copy of the synthetic corpus, so each test
controls exactly how a description is stored (literal block, quoted scalar).
All names and emails are fictional.
"""

from __future__ import annotations

import textwrap

import pytest

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
    final = len(_desc(sandbox, "2025-02-conference-talk"))
    line = _ledger(sandbox)[-1]
    assert "edit-description" in line
    assert "2025-02-conference-talk" in line
    assert f"old_chars=9,new_chars=17,chars={final}" in line


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


OLD = "Alexandra Rivera"


def _cu(sandbox, *extra, old=OLD, new="Alex Rivera", **kw):
    """Run contact-update for ALEX with --old-name/--name."""
    return sandbox.run("contact-update", ALEX, "--old-name", old, "--name", new, *extra, **kw)


def test_contact_update_dry_run_is_default(sandbox):
    """Without --confirm nothing is written, and the preview shows each mention."""
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = _cu(sandbox, extra_env={"LIBRARIAN_SESSION_LABEL": ""})
    assert rc == 0, out
    assert "2026-02-alex-one:" in out
    assert "Dr. [Alexandra Rivera] (arivera@example.edu)" in out
    assert "Dr. [Alex Rivera] (arivera@example.edu)" in out
    assert "by [Alex Rivera] <arivera@example.edu>" in out
    assert "3 entries changed, 3 mentions changed, 0 unchanged, 0 skipped" in out
    assert "Dry run" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_contact_update_dry_run_wins_over_confirm(sandbox):
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = _cu(sandbox, "--confirm", "--dry-run")
    assert rc == 0
    assert "Dry run" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_contact_update_requires_old_name_and_lists_variants(sandbox):
    """Without --old-name the command lists the names the rolodex derives."""
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run("contact-update", ALEX, "--name", "Alex Rivera", "--confirm")
    assert rc == 1, out
    assert "--old-name" in out
    assert "'Dr. Alexandra Rivera'" in out
    assert "'Alexandra Rivera'" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_rejects_single_word_old_name(sandbox):
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = _cu(sandbox, "--confirm", old="Rivera")
    assert rc == 1
    assert "--old-name" in out and "two or more words" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_confirm_rewrites_mentions_across_entries(sandbox):
    """--confirm changes only the old name's text, with one ledger line per entry."""
    _seed_alex(sandbox)
    ledger_before = _ledger(sandbox)
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    # The title before the old name is not part of it and stays.
    assert "Advised the capstone team. Dr. Alex Rivera (arivera@example.edu) signed off." in _desc(
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
    assert all(f"email={ALEX},mentions=1" in line for line in new_lines)
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


def test_contact_update_keeps_lead_in_words(sandbox):
    """A verb, role, title or the previous sentence before the name survives."""
    cases = {
        "2026-02-met": "Met Alexandra Rivera (arivera@example.edu) at the expo.",
        "2026-02-pi": "PI Alexandra Rivera (arivera@example.edu) leads the grant.",
        "2026-02-trip": "Trip to Denver. Alexandra Rivera (arivera@example.edu) presented.",
        "2026-02-prev": "Met with Pat Lee. Alexandra Rivera (arivera@example.edu) also joined.",
        "2026-02-dr": "Spoke with Dr. Alexandra Rivera (arivera@example.edu) about labs.",
        "2026-02-phd": "Panel chaired by Alexandra Rivera, PhD (arivera@example.edu) this year.",
    }
    for entry_id, body in cases.items():
        _append(sandbox, _literal_entry(entry_id, body))
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    for entry_id, body in cases.items():
        expected = body if entry_id == "2026-02-phd" else body.replace(OLD, "Alex Rivera")
        assert _desc(sandbox, entry_id) == expected + "\n"
    assert "Skipped" in out and "2026-02-phd" in out
    assert "5 entries changed, 5 mentions changed, 0 unchanged, 1 skipped" in out


def test_contact_update_confirm_requires_label(sandbox):
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = _cu(sandbox, "--confirm", extra_env={"LIBRARIAN_SESSION_LABEL": ""})
    assert rc == 1
    assert "require --label" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_skips_email_without_a_name(sandbox):
    """A mention with no name before it is reported and left untouched."""
    _seed_alex(sandbox)
    _append(
        sandbox,
        _literal_entry("2026-02-alex-bare", "Contact the office (arivera@example.edu) for access."),
    )
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    assert "Skipped (does not end with the old or new name)" in out
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
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0
    assert "unchanged" in out
    assert "Nothing to change." in out
    assert "0 entries changed, 0 mentions changed, 1 unchanged, 0 skipped" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_contact_update_old_equals_new_is_a_no_op(sandbox):
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = _cu(sandbox, "--confirm", new=OLD)
    assert rc == 0, out
    assert "Nothing to change." in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_rerun_is_a_no_op(sandbox):
    """Running the same rename twice changes nothing the second time."""
    _seed_alex(sandbox)
    _, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0
    after_first = sandbox.activities.read_text()
    ledger_first = _ledger(sandbox)
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    assert "Nothing to change." in out
    assert "0 entries changed, 0 mentions changed, 3 unchanged, 0 skipped" in out
    assert sandbox.activities.read_text() == after_first
    assert _ledger(sandbox) == ledger_first


@pytest.mark.parametrize(
    "new", ["bell hooks", "Maria Elena de la Cruz", "Alex Rivera Jr.", "Alex Dean"]
)
def test_contact_update_refuses_a_name_the_rolodex_cannot_read_back(sandbox, new):
    """Lowercase, five words, a trailing period, a role word: refused whole."""
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = _cu(sandbox, "--confirm", new=new)
    assert rc == 1, out
    assert "cannot read" in out
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
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    assert _desc(sandbox, "2026-02-twice").strip() == (
        "Alex Rivera (arivera@example.edu) opened; later Alex Rivera <arivera@example.edu> closed."
    )
    new_lines = _ledger(sandbox)[len(ledger_before) :]
    assert len(new_lines) == 1
    assert f"email={ALEX},mentions=2" in new_lines[0]
    out, _, rc = sandbox.run("changes", "--op", "contact-update")
    assert f"email={ALEX},mentions=2" in out


def test_contact_update_unknown_email_returns_1(sandbox):
    activities_before = sandbox.activities.read_text()
    out, _, rc = sandbox.run(
        "contact-update", "nobody@example.org", "--old-name", "Old Body", "--name", "No Body"
    )
    assert rc == 1
    assert "no 'Name (nobody@example.org' mention" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_rejects_single_word_name(sandbox):
    """A one-word name would vanish from the rolodex, so it is refused."""
    _seed_alex(sandbox)
    activities_before = sandbox.activities.read_text()
    out, _, rc = _cu(sandbox, "--confirm", new="Alex")
    assert rc == 1
    assert "two or more words" in out
    assert sandbox.activities.read_text() == activities_before


def test_contact_update_handles_quoted_scalar_description(sandbox):
    """A quoted-scalar description is read and rewritten exactly, and stays valid."""
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
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    assert _desc(sandbox, "2026-02-quoted") == (
        "Reviewed by Alex Rivera (arivera@example.edu): approved."
    )
    _, _, rc = sandbox.run("validate")
    assert rc == 0


def test_contact_update_refuses_duplicate_target_id(sandbox):
    """A changed entry whose id appears twice refuses the whole command."""
    _seed_alex(sandbox)
    _append(sandbox, _literal_entry("2026-02-alex-one", "Same id, no mention."))
    activities_before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 1, out
    assert "2026-02-alex-one" in out and "duplicate" in out
    assert sandbox.activities.read_text() == activities_before
    assert _ledger(sandbox) == ledger_before


def test_contact_update_keeps_escaped_separator_and_whitespace(sandbox):
    """An existing double-quoted description holding U+2028 and trailing
    whitespace is rewritten so that only the name changes."""
    _append_raw(
        sandbox,
        "\n  - id: 2026-02-escaped\n"
        "    date: '2026-02-12'\n"
        "    title: Escaped separator\n"
        '    description: "Met Alexandra Rivera (arivera@example.edu) on site.\\u2028'
        'Second clause.  \\n\\n"\n'
        "    tags:\n"
        "      - sample\n",
    )
    entry_before = sandbox.entry("2026-02-escaped")
    assert "\u2028" in entry_before["description"]
    others_before = _others(sandbox, "2026-02-escaped")
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    _assert_only_description_changed(
        sandbox,
        "2026-02-escaped",
        others_before,
        entry_before,
        entry_before["description"].replace(OLD, "Alex Rivera"),
    )
    _, _, rc = sandbox.run("validate")
    assert rc == 0


def test_contact_update_multiline_quoted_continuation_at_field_indent(sandbox):
    _append_raw(
        sandbox,
        "\n  - id: 2026-02-quoted-ml\n"
        "    date: '2026-02-13'\n"
        "    title: Multi-line quoted\n"
        '    description: "Reviewed by Alexandra Rivera (arivera@example.edu)\n'
        '    and approved in May."\n'
        "    tags:\n"
        "      - sample\n",
    )
    entry_before = sandbox.entry("2026-02-quoted-ml")
    others_before = _others(sandbox, "2026-02-quoted-ml")
    out, _, rc = _cu(sandbox, "--confirm")
    assert rc == 0, out
    _assert_only_description_changed(
        sandbox,
        "2026-02-quoted-ml",
        others_before,
        entry_before,
        "Reviewed by Alex Rivera (arivera@example.edu) and approved in May.",
    )


# ---------------------------------------------------------------------------
# Write safety shared by the description writers
# ---------------------------------------------------------------------------


def _append_raw(sandbox, text: str) -> None:
    """Append exact YAML text to the corpus (no re-indenting)."""
    with open(sandbox.activities, "a", encoding="utf-8") as fh:
        fh.write(text)


def _others(sandbox, entry_id: str) -> list[dict]:
    """Every parsed entry except ``entry_id``, in file order."""
    return [e for e in sandbox.load_activities() if e.get("id") != entry_id]


def _without_description(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "description"}


def _assert_only_description_changed(sandbox, entry_id, others_before, entry_before, expected):
    """The file parses, the target's description is exactly ``expected``, and
    nothing else (other entries, the target's other fields) moved."""
    assert _others(sandbox, entry_id) == others_before
    after = sandbox.entry(entry_id)
    assert after["description"] == expected
    assert _without_description(after) == _without_description(entry_before)


def test_edit_description_counts_overlapping_matches(sandbox):
    """Overlapping occurrences count separately, so the edit is ambiguous."""
    _append(sandbox, _literal_entry("2026-03-fruit", "A banana split."))
    _append(sandbox, _literal_entry("2026-03-part", "Completed Part III of the course."))
    before = sandbox.activities.read_text()
    out, _, rc = sandbox.run("edit-description", "2026-03-fruit", "--old", "ana", "--new", "XYZ")
    assert rc == 1, out
    assert "found 2" in out
    out, _, rc = sandbox.run("edit-description", "2026-03-part", "--old", "II", "--new", "IV")
    assert rc == 1, out
    assert "found 2" in out
    assert sandbox.activities.read_text() == before


def test_edit_description_refuses_duplicate_id(sandbox):
    """A target id that appears twice is refused rather than editing the wrong entry."""
    _append(sandbox, _literal_entry("2026-03-dup", "First entry body unique-alpha."))
    _append(sandbox, _literal_entry("2026-03-dup", "Second entry body."))
    before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run(
        "edit-description", "2026-03-dup", "--old", "unique-alpha", "--new", "unique-beta"
    )
    assert rc == 1, out
    assert "2026-03-dup" in out and "2 times" in out
    assert sandbox.activities.read_text() == before
    assert _ledger(sandbox) == ledger_before


def test_edit_description_ledger_details_survive_changes(sandbox):
    """The ledger details carry no spaces, so `changes` shows all of them."""
    sandbox.run(
        "edit-description",
        "2025-02-conference-talk",
        "--old",
        "30-minute",
        "--new",
        "45-minute talk of",
    )
    final = len(_desc(sandbox, "2025-02-conference-talk"))
    out, _, rc = sandbox.run("changes", "--op", "edit-description")
    assert rc == 0
    assert f"old_chars=9,new_chars=17,chars={final}" in out


@pytest.mark.parametrize(
    "new",
    ["a b", "a b", "a\u0085b", "a\rb", "a\x07b"],
    ids=["U+2028", "U+2029", "U+0085", "bare-CR", "control-char"],
)
def test_edit_description_line_break_and_control_chars_round_trip(sandbox, new):
    """Characters a literal block cannot hold never leave an unparseable file:
    the value is written so that it reads back exactly."""
    entry_before = sandbox.entry("2025-02-conference-talk")
    others_before = _others(sandbox, "2025-02-conference-talk")
    out, _, rc = sandbox.run(
        "edit-description", "2025-02-conference-talk", "--old", "30-minute", "--new", new
    )
    assert rc == 0, out
    expected = entry_before["description"].replace("30-minute", new)
    _assert_only_description_changed(
        sandbox, "2025-02-conference-talk", others_before, entry_before, expected
    )
    _, _, rc = sandbox.run("validate")
    assert rc == 0


def test_update_description_refuses_a_write_that_would_not_parse(sandbox):
    """update-description keeps its literal-block output; when that output
    cannot hold the text, the parse check refuses and nothing is written."""
    before = sandbox.activities.read_text()
    ledger_before = _ledger(sandbox)
    out, _, rc = sandbox.run(
        "update-description", "2025-02-conference-talk", stdin="first second\n"
    )
    assert rc == 1, out
    assert "2025-02-conference-talk" in out
    assert "Nothing was written" in out
    assert sandbox.activities.read_text() == before
    assert _ledger(sandbox) == ledger_before


def test_verify_staged_write_rejects_a_broken_splice():
    """The guard refuses text that does not parse, a changed neighbour, or a
    description that reads back differently from the intended value."""
    from librarian.cli import _verify_staged_write

    before = [
        "activities:\n",
        "  - id: a-one\n",
        "    description: |\n",
        "      Old text.\n",
        "  - id: a-two\n",
        "    description: Neighbour.\n",
    ]
    good = list(before)
    good[3] = "      New text.\n"
    assert _verify_staged_write(before, good, {"a-one": "New text.\n"}) is None

    unparseable = list(before)
    unparseable[3] = "      New\n"
    unparseable.insert(4, "text: [\n")
    assert "a-one" in _verify_staged_write(before, unparseable, {"a-one": "New\n"})

    wrong_value = list(good)
    assert "a-one" in _verify_staged_write(before, wrong_value, {"a-one": "Other text.\n"})

    neighbour = list(good)
    neighbour[5] = "    description: Changed.\n"
    assert "a-two" in _verify_staged_write(before, neighbour, {"a-one": "New text.\n"})

    dropped = good[:4]
    assert _verify_staged_write(before, dropped, {"a-one": "New text.\n"}) is not None


def test_edit_description_writes_the_real_description_not_a_block_line(sandbox):
    """A block line that starts with ``description:`` is not the description."""
    _append_raw(
        sandbox,
        "\n  - id: 2026-03-blockfirst\n"
        "    date: '2026-03-02'\n"
        "    title: Block first\n"
        "    ptr:\n"
        "      category: teaching\n"
        "      subcategory: advising\n"
        "      notes: |\n"
        "        description: this line is inside ptr.notes\n"
        "    description: Met with Sam Okafor later.\n"
        "    tags:\n"
        "      - sample\n",
    )
    entry_before = sandbox.entry("2026-03-blockfirst")
    others_before = _others(sandbox, "2026-03-blockfirst")
    out, _, rc = sandbox.run(
        "edit-description", "2026-03-blockfirst", "--old", "later", "--new", "afterwards"
    )
    assert rc == 0, out
    _assert_only_description_changed(
        sandbox,
        "2026-03-blockfirst",
        others_before,
        entry_before,
        "Met with Sam Okafor afterwards.",
    )


@pytest.mark.parametrize(
    "stored, value",
    [
        (
            '    description: "Met Alex Rivera on site\n    and approved in May."\n',
            "Met Alex Rivera on site and approved in May.",
        ),
        (
            "    description: 'Met Alex Rivera on site\n    and approved in May.'\n",
            "Met Alex Rivera on site and approved in May.",
        ),
    ],
    ids=["double-quoted", "single-quoted"],
)
def test_edit_description_multiline_quoted_continuation_at_field_indent(sandbox, stored, value):
    """A quoted description whose continuation line sits at the field's own
    indent is replaced whole, so no stray tail is left behind."""
    _append_raw(
        sandbox,
        "\n  - id: 2026-03-quoted-ml\n"
        "    date: '2026-03-03'\n"
        "    title: Multi-line quoted\n" + stored + "    tags:\n      - sample\n",
    )
    entry_before = sandbox.entry("2026-03-quoted-ml")
    assert entry_before["description"] == value
    others_before = _others(sandbox, "2026-03-quoted-ml")
    out, _, rc = sandbox.run(
        "edit-description", "2026-03-quoted-ml", "--old", "in May", "--new", "in June"
    )
    assert rc == 0, out
    _assert_only_description_changed(
        sandbox,
        "2026-03-quoted-ml",
        others_before,
        entry_before,
        value.replace("in May", "in June"),
    )


def _last_field_entry() -> str:
    return (
        "\n  - id: 2026-03-lastfield\n"
        "    date: '2026-03-04'\n"
        "    title: Description is the last field\n"
        "    tags:\n"
        "      - sample\n"
        "    description: |\n"
        "      Description at the end.\n"
        "\n"
        "  # A comment that belongs before the next entry.\n"
        "  - id: 2026-03-after\n"
        "    date: '2026-03-05'\n"
        "    title: After\n"
        "    description: Untouched neighbour.\n"
    )


@pytest.mark.parametrize("command", ["edit-description", "update-description"])
def test_description_as_last_field_keeps_blank_separator(sandbox, command):
    """The blank line between the entry and the next one survives the splice."""
    _append_raw(sandbox, _last_field_entry())
    if command == "edit-description":
        out, _, rc = sandbox.run(
            command, "2026-03-lastfield", "--old", "at the end", "--new", "at the very end"
        )
    else:
        out, _, rc = sandbox.run(command, "2026-03-lastfield", stdin="Description at the very end.")
    assert rc == 0, out
    text = sandbox.activities.read_text()
    assert (
        "      Description at the very end.\n"
        "\n"
        "  # A comment that belongs before the next entry.\n"
        "  - id: 2026-03-after\n"
    ) in text
    assert sandbox.entry("2026-03-after")["description"] == "Untouched neighbour."


def test_edit_description_keeps_whitespace_outside_the_edit(sandbox):
    """Trailing spaces, trailing blank lines and a leading-space first line
    outside the edited passage are kept exactly."""
    _append_raw(
        sandbox,
        "\n  - id: 2026-03-ws\n"
        "    date: '2026-03-06'\n"
        "    title: Whitespace\n"
        '    description: "  Indented first.  \\n\\nNext line with typo.\\n\\n"\n'
        "    tags:\n"
        "      - sample\n",
    )
    entry_before = sandbox.entry("2026-03-ws")
    others_before = _others(sandbox, "2026-03-ws")
    out, _, rc = sandbox.run("edit-description", "2026-03-ws", "--old", "typo", "--new", "fix")
    assert rc == 0, out
    _assert_only_description_changed(
        sandbox,
        "2026-03-ws",
        others_before,
        entry_before,
        "  Indented first.  \n\nNext line with fix.\n\n",
    )


def test_edit_description_keeps_new_leading_space_and_missing_final_newline(sandbox):
    """A replacement that starts the description with spaces is written as given,
    and a description with no trailing newline does not gain one."""
    _append(
        sandbox,
        """
          - id: 2026-03-plain
            date: '2026-03-07'
            title: Plain
            description: First words here.
            tags:
              - sample
        """,
    )
    entry_before = sandbox.entry("2026-03-plain")
    others_before = _others(sandbox, "2026-03-plain")
    out, _, rc = sandbox.run(
        "edit-description", "2026-03-plain", "--old", "First", "--new", "  First"
    )
    assert rc == 0, out
    _assert_only_description_changed(
        sandbox, "2026-03-plain", others_before, entry_before, "  First words here."
    )
