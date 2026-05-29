"""Unit tests for the pluggable schema engine (librarian.schema)."""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian.schema import (
    EMPTY_SCHEMA,
    SchemaError,
    coerce_value,
    load_schema,
    parse_schema,
    validate_block,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------


def test_load_sample_schema():
    """The sample schema parses into two blocks with the expected fields."""
    schema = load_schema(FIXTURES / "sample_schema.yaml")
    assert not schema.is_empty
    assert {b.name for b in schema.blocks} == {"ptr", "cpe"}
    ptr = schema.block("ptr")
    assert [f.name for f in ptr.fields] == ["category", "subcategory", "notes"]
    assert ptr.field("subcategory").is_dependent_enum


def test_missing_schema_is_empty():
    """A missing schema file yields the empty (generic-mode) schema."""
    schema = load_schema(FIXTURES / "does-not-exist.yaml")
    assert schema is EMPTY_SCHEMA
    assert schema.is_empty


def test_parse_rejects_unknown_field_type():
    """An unknown field type is a SchemaError."""
    with pytest.raises(SchemaError, match="unknown type"):
        parse_schema({"blocks": {"b": {"fields": [{"name": "x", "type": "blob"}]}}})


def test_parse_rejects_enum_without_values():
    """An enum field with no values is a SchemaError."""
    with pytest.raises(SchemaError, match="enum needs 'values'"):
        parse_schema({"blocks": {"b": {"fields": [{"name": "x", "type": "enum"}]}}})


def test_parse_rejects_dangling_depends_on():
    """A dependent enum whose parent field does not exist is a SchemaError."""
    bad = {
        "blocks": {
            "b": {
                "fields": [
                    {
                        "name": "child",
                        "type": "enum",
                        "depends_on": "ghost",
                        "values": {"a": ["x"]},
                    }
                ]
            }
        }
    }
    with pytest.raises(SchemaError, match="no sibling field"):
        parse_schema(bad)


def test_parse_rejects_depends_on_non_mapping_values():
    """A dependent enum needs a mapping of parent-value -> list."""
    bad = {
        "blocks": {
            "b": {
                "fields": [
                    {"name": "p", "type": "enum", "values": ["a"]},
                    {"name": "c", "type": "enum", "depends_on": "p", "values": ["x"]},
                ]
            }
        }
    }
    with pytest.raises(SchemaError, match="must be a mapping"):
        parse_schema(bad)


# ---------------------------------------------------------------------------
# Block validation
# ---------------------------------------------------------------------------


@pytest.fixture
def schema():
    """The sample test schema, parsed."""
    return load_schema(FIXTURES / "sample_schema.yaml")


def test_validate_block_accepts_good_data(schema):
    """A well-formed ptr block produces no issues."""
    issues = validate_block(
        schema.block("ptr"),
        {"category": "teaching", "subcategory": "advising", "notes": "ok"},
    )
    assert issues == []


def test_validate_block_flags_bad_enum(schema):
    """An out-of-set enum value is flagged."""
    issues = validate_block(schema.block("ptr"), {"category": "nonsense"})
    assert any("INVALID PTR.CATEGORY" in i for i in issues)


def test_validate_block_flags_bad_dependent_enum(schema):
    """A subcategory invalid for its category is flagged with the parent name."""
    issues = validate_block(
        schema.block("ptr"),
        {"category": "teaching", "subcategory": "cat1-peer-reviewed"},
    )
    assert any("INVALID PTR.SUBCATEGORY" in i and "category" in i for i in issues)


def test_validate_block_flags_missing_required(schema):
    """A missing required field is flagged."""
    issues = validate_block(schema.block("cpe"), {"credits": 5})
    assert any("MISSING CPE.GROUP" in i for i in issues)


def test_validate_block_flags_non_int(schema):
    """A non-integer value in an int field is flagged."""
    issues = validate_block(schema.block("cpe"), {"group": "primary", "credits": "lots"})
    assert any("INVALID CPE.CREDITS" in i for i in issues)


def test_validate_block_accepts_int_as_string(schema):
    """An integer-shaped string passes int validation."""
    issues = validate_block(schema.block("cpe"), {"group": "primary", "credits": "12"})
    assert issues == []


def test_validate_block_nullable_date_accepts_null(schema):
    """A null value in a date? field is accepted."""
    issues = validate_block(
        schema.block("cpe"),
        {"group": "general", "credits": 1, "submission_date": None},
    )
    assert issues == []


def test_validate_block_flags_required_enum_null(schema):
    """A required enum field set to null is rejected (was silently accepted)."""
    issues = validate_block(schema.block("cpe"), {"group": None, "credits": 1})
    assert any("INVALID CPE.GROUP" in i and "null" in i.lower() for i in issues)


def test_validate_block_flags_required_int_null(schema):
    """A required int field set to null is rejected."""
    issues = validate_block(schema.block("cpe"), {"group": "general", "credits": None})
    assert any("INVALID CPE.CREDITS" in i and "null" in i.lower() for i in issues)


def test_validate_block_accepts_optional_field_null(schema):
    """A non-required field set to null is still treated as 'not set'."""
    # cpe.domain is type=string and not required; null must remain accepted.
    issues = validate_block(
        schema.block("cpe"),
        {"group": "general", "credits": 1, "domain": None},
    )
    assert issues == []


def test_validate_block_flags_bad_date(schema):
    """A non-ISO date string is flagged."""
    issues = validate_block(
        schema.block("cpe"),
        {"group": "general", "credits": 1, "submission_date": "April 2026"},
    )
    assert any("INVALID CPE.SUBMISSION_DATE" in i for i in issues)


def test_validate_block_flags_bad_bool(schema):
    """A non-boolean value in a bool field is flagged."""
    issues = validate_block(
        schema.block("cpe"),
        {"group": "general", "credits": 1, "submitted": "maybe"},
    )
    assert any("INVALID CPE.SUBMITTED" in i for i in issues)


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def test_coerce_int(schema):
    """An int field coerces a numeric string to an int."""
    assert coerce_value(schema.block("cpe").field("credits"), "20") == 20


def test_coerce_bool(schema):
    """A bool field coerces common true/false spellings."""
    field = schema.block("cpe").field("submitted")
    assert coerce_value(field, "true") is True
    assert coerce_value(field, "no") is False


def test_coerce_nullable_date_accepts_null(schema):
    """A date? field coerces the literal 'null' to None."""
    assert coerce_value(schema.block("cpe").field("submission_date"), "null") is None


def test_coerce_rejects_bad_date(schema):
    """A date field rejects a non-ISO string."""
    with pytest.raises(ValueError, match="ISO date"):
        coerce_value(schema.block("cpe").field("submission_date"), "soon")


# ---------------------------------------------------------------------------
# Bundled schemas all parse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["ptr.yaml", "cpe.yaml", "performance-review.yaml", "student-portfolio.yaml"]
)
def test_bundled_schema_parses(name):
    """Every bundled schema in schemas/ parses without error."""
    schema = load_schema(Path(__file__).parent.parent / "schemas" / name)
    assert not schema.is_empty
    assert schema.name
