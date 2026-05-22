"""File-inventory CLI tests, run against the synthetic fixture corpus."""

from __future__ import annotations

import json


def _make_artifact(sandbox, name: str, content: str = "synthetic artifact content") -> str:
    """Create an artifact file inside the sandbox and return its relative path."""
    path = sandbox.artifacts / name
    path.write_text(content)
    return f"artifacts/{name}"


# ---------------------------------------------------------------------------
# file-add / file-get
# ---------------------------------------------------------------------------


def test_file_add_and_get(sandbox):
    """file-add registers a file and file-get shows it."""
    rel = _make_artifact(sandbox, "new-poster.txt")
    out, _, rc = sandbox.run(
        "file-add", rel, "--category", "Scholarship", "--title", "A New Poster"
    )
    assert rc == 0
    assert "Registered file" in out
    out, _, rc = sandbox.run("file-get", "new-poster")
    assert rc == 0
    assert "A New Poster" in out
    assert "on disk: YES" in out


def test_file_add_derives_slug_id(sandbox):
    """file-add derives a slug id from the filename when --id is omitted."""
    rel = _make_artifact(sandbox, "Quarterly Report 2026.txt")
    out, _, rc = sandbox.run("file-add", rel, "--category", "Reports", "--title", "Quarterly")
    assert rc == 0
    ids = {r["id"] for r in sandbox.load_files()}
    assert "quarterly-report-2026" in ids


def test_file_add_explicit_id(sandbox):
    """file-add honours an explicit --id."""
    rel = _make_artifact(sandbox, "explicit.txt")
    out, _, rc = sandbox.run(
        "file-add", rel, "--category", "X", "--title", "T", "--id", "my-custom-id"
    )
    assert rc == 0
    assert any(r["id"] == "my-custom-id" for r in sandbox.load_files())


def test_file_add_rejects_missing_file(sandbox):
    """file-add refuses a path that does not exist on disk."""
    out, _, rc = sandbox.run("file-add", "artifacts/ghost.txt", "--category", "X", "--title", "T")
    assert rc == 1
    assert "does not exist" in out


def test_file_add_rejects_duplicate_path(sandbox):
    """file-add refuses a path that is already registered."""
    out, _, rc = sandbox.run(
        "file-add", "artifacts/syllabus-infosec-101.txt", "--category", "X", "--title", "T"
    )
    assert rc == 1
    assert "already registered" in out


def test_file_add_records_sha256(sandbox):
    """file-add stores a sha256 digest for the registered file."""
    rel = _make_artifact(sandbox, "hashed.txt", "specific content for hashing")
    sandbox.run("file-add", rel, "--category", "X", "--title", "T")
    record = next(r for r in sandbox.load_files() if r["id"] == "hashed")
    assert len(record["sha256"]) == 64


def test_file_add_warns_on_identical_content(sandbox):
    """file-add warns (but does not block) when identical content is registered."""
    content = "the very same bytes"
    rel1 = _make_artifact(sandbox, "first-copy.txt", content)
    sandbox.run("file-add", rel1, "--category", "X", "--title", "First")
    rel2 = _make_artifact(sandbox, "second-copy.txt", content)
    out, _, rc = sandbox.run("file-add", rel2, "--category", "X", "--title", "Second")
    assert rc == 0  # warn, not block
    assert "identical content" in out


def test_file_add_warns_on_similar_title(sandbox):
    """file-add warns on a fuzzily-similar existing title."""
    rel = _make_artifact(sandbox, "similar-titled.txt")
    out, _, rc = sandbox.run(
        "file-add",
        rel,
        "--category",
        "Scholarship",
        "--title",
        "Security Curriculum Review Report",
    )
    assert rc == 0
    assert "similar file" in out.lower()


# ---------------------------------------------------------------------------
# file-list / file-search
# ---------------------------------------------------------------------------


def test_file_list(sandbox):
    """file-list shows every registered file."""
    out, _, rc = sandbox.run("file-list")
    assert rc == 0
    assert "syllabus-infosec-101" in out
    assert "Files: 3" in out


def test_file_list_by_category(sandbox):
    """file-list --category filters by category."""
    out, _, rc = sandbox.run("file-list", "--category", "Teaching")
    assert rc == 0
    assert "syllabus-infosec-101" in out
    assert "curriculum-report-2024" not in out


def test_file_list_orphans(sandbox):
    """file-list --orphans reports the unreferenced standalone file."""
    out, _, rc = sandbox.run("file-list", "--orphans")
    assert rc == 0
    assert "standalone-profile" in out


def test_file_search(sandbox):
    """file-search does a full-text search over the inventory."""
    out, _, rc = sandbox.run("file-search", "curriculum")
    assert rc == 0
    assert "curriculum-report-2024" in out


# ---------------------------------------------------------------------------
# file-move / file-update / file-rehash
# ---------------------------------------------------------------------------


def test_file_move(sandbox):
    """file-move relocates the file on disk and updates the inventory path."""
    out, _, rc = sandbox.run("file-move", "standalone-profile", "artifacts/moved-profile.txt")
    assert rc == 0
    record = next(r for r in sandbox.load_files() if r["id"] == "standalone-profile")
    assert record["path"] == "artifacts/moved-profile.txt"
    assert (sandbox.home / "artifacts" / "moved-profile.txt").exists()
    assert not (sandbox.home / "artifacts" / "standalone-profile.txt").exists()


def test_file_move_rejects_existing_dest(sandbox):
    """file-move refuses to overwrite an existing destination."""
    out, _, rc = sandbox.run(
        "file-move", "standalone-profile", "artifacts/syllabus-infosec-101.txt"
    )
    assert rc == 1
    assert "already exists" in out


def test_file_update(sandbox):
    """file-update changes a file's category and title."""
    out, _, rc = sandbox.run(
        "file-update", "standalone-profile", "--category", "Updated", "--title", "New Title"
    )
    assert rc == 0
    record = next(r for r in sandbox.load_files() if r["id"] == "standalone-profile")
    assert record["category"] == "Updated"
    assert record["title"] == "New Title"


def test_file_rehash_single(sandbox):
    """file-rehash recomputes the digest after content changes on disk."""
    record = next(r for r in sandbox.load_files() if r["id"] == "standalone-profile")
    old_hash = record["sha256"]
    (sandbox.home / "artifacts" / "standalone-profile.txt").write_text("changed bytes")
    out, _, rc = sandbox.run("file-rehash", "standalone-profile")
    assert rc == 0
    new_hash = next(r for r in sandbox.load_files() if r["id"] == "standalone-profile")["sha256"]
    assert new_hash != old_hash


def test_file_rehash_all(sandbox):
    """file-rehash --all rehashes the whole inventory."""
    out, _, rc = sandbox.run("file-rehash", "--all")
    assert rc == 0
    assert "Rehashed 3" in out


# ---------------------------------------------------------------------------
# file references from entries + validation
# ---------------------------------------------------------------------------


def test_file_get_shows_references(sandbox):
    """file-get lists the entries that cite a file."""
    out, _, rc = sandbox.run("file-get", "syllabus-infosec-101")
    assert rc == 0
    assert "2024-03-intro-security-course" in out


def test_validate_clean_file_inventory(sandbox):
    """validate finds no inventory issues for the consistent fixture corpus."""
    out, _, rc = sandbox.run("validate")
    assert rc == 0
    assert "MISSING FILE" not in out
    assert "DANGLING FILE REF" not in out


def test_validate_flags_dangling_file_ref(sandbox):
    """validate flags an entry citing a file id not in the inventory."""
    sandbox.run("add-docs", "2026-04-self-study", "file:ghost-file-id")
    out, _, rc = sandbox.run("validate")
    assert rc == 0
    assert "DANGLING FILE REF" in out
    assert "ghost-file-id" in out


def test_validate_flags_missing_file(sandbox):
    """validate flags a registered file whose path is gone from disk."""
    (sandbox.home / "artifacts" / "standalone-profile.txt").unlink()
    out, _, rc = sandbox.run("validate")
    assert rc == 0
    assert "MISSING FILE" in out


def test_attach_file_to_entry_round_trip(sandbox):
    """Registering a file and citing it as file:<id> resolves on get."""
    rel = _make_artifact(sandbox, "attachable.txt")
    sandbox.run("file-add", rel, "--category", "X", "--title", "Attachable", "--id", "att")
    sandbox.run("add-docs", "2026-04-self-study", "file:att")
    out, _, rc = sandbox.run("get", "2026-04-self-study")
    assert rc == 0
    assert "file:att  -> artifacts/attachable.txt" in out


def test_file_list_json(sandbox):
    """file-list --format json emits a parseable array."""
    out, _, rc = sandbox.run("file-list", "--format", "json")
    assert rc == 0
    assert len(json.loads(out)) == 3
