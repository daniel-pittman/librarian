"""The pluggable schema engine.

The librarian stores *activity entries*. Every entry always has a fixed set of
**core fields** — ``id``, ``date``, ``title``, ``description``, ``tags``,
``docs`` and an optional ``end_date``. On top of that, an entry may carry one
or more optional structured **blocks** (e.g. a ``ptr`` block for post-tenure
review, a ``cpe`` block for continuing-education credits).

Historically those blocks were hardcoded. This module makes them *data*: a
``schema.yaml`` file declares the blocks, their fields, the field types, and
the enum value sets. The validator, the nested-field updater, ``stats`` and
``filter`` all consult the loaded :class:`Schema` instead of baking in any
particular block.

When no schema is configured the tool still does fully generic CRUD, search
and file-inventory work — blocks simply are not validated.

Schema file shape
-----------------
::

    name: Academic Post-Tenure Review
    description: Track teaching, scholarly and service work.
    blocks:
      ptr:
        label: Post-Tenure Review
        fields:
          - name: category
            type: enum
            values: [teaching, scholarly, service]
          - name: subcategory
            type: enum
            depends_on: category          # category-dependent enum
            values:
              teaching: [instructional-delivery, advising, ...]
              scholarly: [cat1-peer-reviewed, ...]
              service: [institutional-university, ...]
          - name: notes
            type: text

Supported field ``type`` values: ``enum``, ``text``, ``string``, ``int``,
``bool``, ``date`` and ``date?`` (a nullable date).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

import yaml

# The complete set of field types the engine understands. ``date?`` is a date
# that additionally accepts ``null`` / ``None`` (e.g. a "submitted on" date
# that is empty until the thing is actually submitted).
FIELD_TYPES = frozenset({"enum", "text", "string", "int", "bool", "date", "date?"})

# A plain ISO calendar date. Used to validate ``date`` / ``date?`` fields.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Accepted spellings for boolean field values when supplied as strings (CLI
# arguments always arrive as strings).
_TRUE_STRINGS = frozenset({"true", "yes", "1"})
_FALSE_STRINGS = frozenset({"false", "no", "0"})


class SchemaError(Exception):
    """Raised when a ``schema.yaml`` file is structurally invalid.

    This is distinct from a *data* validation failure (an entry that violates
    the schema): a SchemaError means the schema definition itself is broken.
    """


@dataclass(frozen=True)
class FieldDef:
    """One field within a schema block.

    Attributes:
        name: The field key as it appears in the YAML entry.
        type: One of :data:`FIELD_TYPES`.
        values: For a plain ``enum``, the list of allowed values. For a
            dependent enum (``depends_on`` set), a mapping from each parent
            value to that parent's allowed child values.
        depends_on: For a dependent enum, the sibling field whose value
            selects which value set applies. ``None`` for every other field.
        required: Whether the field must be present when its block is present.
    """

    name: str
    type: str
    values: object = None
    depends_on: str | None = None
    required: bool = False

    @property
    def is_dependent_enum(self) -> bool:
        """True when this field is an enum whose value set depends on a sibling."""
        return self.type == "enum" and self.depends_on is not None


@dataclass(frozen=True)
class BlockDef:
    """One optional structured block (e.g. ``ptr`` or ``cpe``)."""

    name: str
    label: str
    fields: tuple[FieldDef, ...]

    def field(self, name: str) -> FieldDef | None:
        """Return the named :class:`FieldDef`, or ``None`` if absent."""
        return next((f for f in self.fields if f.name == name), None)


@dataclass(frozen=True)
class Schema:
    """A loaded schema: a named collection of block definitions.

    A :class:`Schema` with no blocks is perfectly valid — it simply means the
    tool runs in generic, block-unaware mode.
    """

    name: str = ""
    description: str = ""
    blocks: tuple[BlockDef, ...] = dc_field(default_factory=tuple)

    def block(self, name: str) -> BlockDef | None:
        """Return the named :class:`BlockDef`, or ``None`` if absent."""
        return next((b for b in self.blocks if b.name == name), None)

    @property
    def is_empty(self) -> bool:
        """True when the schema declares no blocks (generic mode)."""
        return not self.blocks


# A shared empty schema for the schema-less / generic case.
EMPTY_SCHEMA = Schema()


def _parse_field(raw: dict, block_name: str) -> FieldDef:
    """Build a :class:`FieldDef` from one raw field mapping.

    Raises:
        SchemaError: if the field mapping is malformed (missing name, unknown
            type, enum without values, dependent enum without a mapping, ...).
    """
    if not isinstance(raw, dict):
        raise SchemaError(f"block '{block_name}': each field must be a mapping")

    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise SchemaError(f"block '{block_name}': a field is missing its 'name'")

    ftype = raw.get("type", "string")
    if ftype not in FIELD_TYPES:
        raise SchemaError(
            f"block '{block_name}', field '{name}': unknown type '{ftype}' "
            f"(allowed: {sorted(FIELD_TYPES)})"
        )

    depends_on = raw.get("depends_on")
    values = raw.get("values")
    required = bool(raw.get("required", False))

    if ftype == "enum":
        if values is None:
            raise SchemaError(f"block '{block_name}', field '{name}': enum needs 'values'")
        if depends_on is not None:
            # Dependent enum: values must be a mapping parent-value -> list.
            if not isinstance(values, dict):
                raise SchemaError(
                    f"block '{block_name}', field '{name}': a dependent enum's "
                    f"'values' must be a mapping of parent value -> list"
                )
            values = {k: list(v) for k, v in values.items()}
        else:
            if not isinstance(values, (list, tuple)):
                raise SchemaError(
                    f"block '{block_name}', field '{name}': enum 'values' must be a list"
                )
            values = list(values)
    elif depends_on is not None:
        raise SchemaError(
            f"block '{block_name}', field '{name}': 'depends_on' is only valid on enum fields"
        )

    return FieldDef(
        name=name,
        type=ftype,
        values=values,
        depends_on=depends_on,
        required=required,
    )


def _parse_block(name: str, raw: dict) -> BlockDef:
    """Build a :class:`BlockDef` from one raw block mapping."""
    if not isinstance(raw, dict):
        raise SchemaError(f"block '{name}': definition must be a mapping")
    label = raw.get("label", name)
    raw_fields = raw.get("fields", []) or []
    if not isinstance(raw_fields, list):
        raise SchemaError(f"block '{name}': 'fields' must be a list")
    fields = tuple(_parse_field(rf, name) for rf in raw_fields)

    # A dependent enum's `depends_on` must name a real sibling field.
    field_names = {f.name for f in fields}
    for f in fields:
        if f.depends_on and f.depends_on not in field_names:
            raise SchemaError(
                f"block '{name}', field '{f.name}': depends_on='{f.depends_on}'"
                f" but no sibling field by that name"
            )
    return BlockDef(name=name, label=label, fields=fields)


def parse_schema(data: dict) -> Schema:
    """Build a :class:`Schema` from an already-parsed YAML mapping.

    Raises:
        SchemaError: if the mapping is structurally invalid.
    """
    if data is None:
        return EMPTY_SCHEMA
    if not isinstance(data, dict):
        raise SchemaError("schema root must be a mapping")
    raw_blocks = data.get("blocks", {}) or {}
    if not isinstance(raw_blocks, dict):
        raise SchemaError("'blocks' must be a mapping of block name -> definition")
    blocks = tuple(_parse_block(name, raw) for name, raw in raw_blocks.items())
    return Schema(
        name=data.get("name", ""),
        description=data.get("description", ""),
        blocks=blocks,
    )


def load_schema(path: Path) -> Schema:
    """Load and parse a schema file.

    A missing file is not an error — it yields :data:`EMPTY_SCHEMA` so the
    tool runs in generic mode. A present-but-broken file raises
    :class:`SchemaError`.
    """
    if not path.exists():
        return EMPTY_SCHEMA
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return parse_schema(data)


def _allowed_enum_values(field: FieldDef, block_data: dict) -> list | None:
    """Return the active value set for an enum field given its block's data.

    For a plain enum this is just ``field.values``. For a dependent enum it is
    the list keyed by the current value of the parent field, or ``None`` when
    the parent value is itself invalid (the caller reports that separately).
    """
    if not field.is_dependent_enum:
        return list(field.values or [])
    parent_value = block_data.get(field.depends_on)
    mapping = field.values or {}
    return mapping.get(parent_value)


def validate_block(block: BlockDef, block_data: dict) -> list[str]:
    """Validate one entry's block data against its :class:`BlockDef`.

    Returns a list of human-readable issue strings. The strings are formatted
    to mirror the original tool's ``validate`` output, e.g.::

        INVALID PTR.CATEGORY: value 'x' (allowed: [...])
        INVALID PTR.SUBCATEGORY: value 'y' not valid for category 'teaching'

    An empty list means the block is valid.
    """
    issues: list[str] = []
    if not isinstance(block_data, dict):
        return [f"INVALID {block.name.upper()}: block is not a mapping"]

    for field in block.fields:
        present = field.name in block_data
        value = block_data.get(field.name)

        if not present:
            if field.required:
                issues.append(
                    f"MISSING {block.name.upper()}.{field.name.upper()}: required field is absent"
                )
            continue

        # An explicitly-null OPTIONAL field is treated as "not set". For a
        # REQUIRED field, ``null`` is a hard error regardless of type — the
        # only exception is ``date?``, the nullable-date type, which exists
        # specifically to permit null. Prior to broadening this, only the
        # date branch enforced required+null; set-block (and any future
        # full-block writer like merge) could otherwise land a block on disk
        # with every required field explicitly null.
        if value is None:
            if field.required and field.type != "date?":
                issues.append(
                    f"INVALID {block.name.upper()}.{field.name.upper()}: required field is null"
                )
            continue

        issue = _validate_value(block, field, value, block_data)
        if issue:
            issues.append(issue)
    return issues


def _validate_value(
    block: BlockDef, field: FieldDef, value: object, block_data: dict
) -> str | None:
    """Validate a single non-null field value. Returns an issue string or None."""
    label = f"{block.name.upper()}.{field.name.upper()}"

    if field.type == "enum":
        allowed = _allowed_enum_values(field, block_data)
        if allowed is None:
            # The parent (depends_on) field is itself invalid — that parent
            # field's own validation will report it; don't double-report.
            return None
        if value not in allowed:
            if field.is_dependent_enum:
                parent_value = block_data.get(field.depends_on)
                return (
                    f"INVALID {label}: value '{value}' not valid for "
                    f"{field.depends_on} '{parent_value}' "
                    f"(allowed: {sorted(allowed)})"
                )
            return f"INVALID {label}: value '{value}' (allowed: {sorted(allowed)})"
        return None

    if field.type == "int":
        if isinstance(value, bool) or not _is_intish(value):
            return f"INVALID {label}: value '{value}' is not an integer"
        return None

    if field.type == "bool":
        if isinstance(value, bool):
            return None
        if isinstance(value, str) and value.lower() in (_TRUE_STRINGS | _FALSE_STRINGS):
            return None
        return f"INVALID {label}: value '{value}' is not a boolean"

    if field.type in ("date", "date?"):
        if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
            return f"INVALID {label}: value '{value}' is not an ISO date (YYYY-MM-DD)"
        return None

    # text / string accept any scalar; reject containers.
    if isinstance(value, (dict, list)):
        return f"INVALID {label}: value must be a scalar string, got {type(value).__name__}"
    return None


def _is_intish(value: object) -> bool:
    """True when `value` is an int or a string that parses cleanly as one."""
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        try:
            int(value.strip())
            return True
        except ValueError:
            return False
    return False


def coerce_value(field: FieldDef, raw: str):
    """Coerce a CLI string argument to the field's native type.

    CLI arguments always arrive as strings; the YAML serialiser needs to know
    whether to render a value bare (int/bool/null) or quoted (string/date).

    Raises:
        ValueError: if the string cannot be coerced to the field's type.
    """
    if raw == "null" and field.type == "date?":
        return None
    if field.type == "int":
        return int(raw.strip())
    if field.type == "bool":
        low = raw.lower()
        if low in _TRUE_STRINGS:
            return True
        if low in _FALSE_STRINGS:
            return False
        raise ValueError(f"'{raw}' is not a boolean")
    if field.type in ("date", "date?"):
        if not _ISO_DATE_RE.match(raw):
            raise ValueError(f"'{raw}' is not an ISO date (YYYY-MM-DD)")
        return raw
    return raw
