# Design Notes

This document records the design decisions made in turning a single-purpose
activity tracker into a generic, schema-pluggable open-source tool — what
changed versus the original, why, and what to flag for review.

## 1. The schema engine

### The problem

The original tool hardcoded two structured "blocks" on every entry: `ptr`
(post-tenure review) and `cpe` (continuing-education credits). The validator's
enums, the nested-field updater's whitelist, `stats`, `project`, and
`filter --category/--cpe` all baked those two blocks in. Adding a third use
case meant editing five places in a 3,000-line file.

### The design

The schema is now **data**, loaded from a `schema.yaml` (`librarian/schema.py`).
The model is three dataclasses:

- `Schema` — a named collection of blocks.
- `BlockDef` — one optional block (`ptr`, `cpe`, `review`, ...).
- `FieldDef` — one field, with a `type` and (for enums) a value set.

Supported field types: `enum`, `text`, `string`, `int`, `bool`, `date`, and
`date?` (nullable date). The engine exposes three operations:

- `load_schema(path)` / `parse_schema(data)` — parse, raising `SchemaError`
  on a structurally broken schema.
- `validate_block(block, data)` — return human-readable issue strings,
  formatted to mirror the original's `INVALID PTR.SUBCATEGORY: ...` style.
- `coerce_value(field, raw)` — coerce a CLI string argument to the field's
  native type, so the YAML serialiser knows whether to render bare or quoted.

**Dependent enums.** The original's category-dependent subcategory (a
subcategory valid under `teaching` is not valid under `service`) generalises to
a `depends_on` attribute on a `FieldDef`: the field's `values` becomes a
mapping from each parent value to that parent's allowed child values. The
validator resolves the parent value at check time. This is the one genuinely
non-trivial schema feature and it is fully covered by tests.

### Tradeoffs

- **Generic mode.** With no `schema.yaml`, the tool runs schema-less: full
  CRUD/search/file-inventory still works, blocks are simply not validated. This
  keeps the tool useful before a schema is chosen and makes "no schema" a
  first-class state rather than an error.
- **Single active schema.** The tool loads exactly one schema at a time. To
  track both review and credits at once, a user merges both blocks into one
  `schema.yaml` (the bundled `ptr.yaml` and `cpe.yaml` can be concatenated
  under a single `blocks:` mapping; the test fixture `sample_schema.yaml` does
  exactly this). A multi-schema registry was considered and rejected as
  over-engineering for the target audience.
- **Unknown blocks are preserved, not rejected.** If an entry carries a block
  the active schema does not declare, reads still work and `create` still
  renders it (via a generic scalar renderer). Only validation is skipped for
  it. This avoids data loss when switching schemas.

## 2. CLI behavior changes vs. the original

The brief permitted generalizing flags; here is the full list of what changed
and why.

| Area | Original | Now | Why |
|---|---|---|---|
| `filter --category` | hardcoded `ptr.category` | still works as an **alias**; the general form is `--block-field BLOCK.FIELD VALUE` | schema-agnostic filtering |
| `filter --cpe / --no-cpe` | hardcoded `cpe` block | still work as **aliases**; general form is `--has-block / --no-block` | schema-agnostic |
| `update-nested-field` | whitelist of `ptr.*`/`cpe.*` paths | validates against the active schema instead | the whitelist *was* the hardcoding |
| `update-notes --section ptr\|cpe` | two hardcoded sections | `--block B --field F`; `--section` kept as a back-compat alias; block defaults to the schema's first block | generalised to any block/text field |
| `stats` | hardcoded PTR/CPE/C3-lab/tenure counters | iterates the schema's blocks, counts every enum field, sums every int field | schema-driven |
| `project` | hardcoded per-project tag/keyword tables | generic: tag-match + keyword-match on the project name | the project tables were personal data |
| `export` CSV columns | fixed `ptr_*`/`cpe_*` columns | core columns + one column per schema enum field | schema-driven |
| `validate` | hardcoded `_PTR_SUBCATEGORIES` | runs `validate_block` for every schema block on every entry | schema-driven |
| dangling-ref scan | scanned `description`, `ptr.notes`, `cpe.notes` | scans `description` + every text/string field the schema declares | schema-driven |
| new `schema` command | — | added; describes the active schema (`--json` too) | discoverability |
| new `rollup` command | — | added; `rollup BLOCK [--sum FIELD] [--group-by FIELD] [--json]` aggregates a block over a filtered set (count + summed int field + per-group breakdown) | schema-agnostic aggregation primitive (the totals view a funding portfolio needs) |
| MCP `update_ptr_field` / `update_cpe_field` | two block-specific tools | one `librarian_update_block_field(block, field, ...)` | schema-agnostic |
| ID-shape heuristic (dangling refs) | matched corpus-specific prefixes (`c3lab-`, `ongoing-`, `rejected-`, year) | matches any valid slug that contains a digit | the prefix list was personal data |
| contact rolodex | mentioned a specific canonical roster entry | generic `Name (email)` extraction, no special entry | personal data removed |

Read commands keep their original names and core ergonomics. The dispatch table
and exit-code contract (`0` success, `1` error) are unchanged.

## 3. Data home

The default data home moved from script-relative / personal absolute paths to
an XDG-style config dir: `$XDG_CONFIG_HOME/librarian/` or
`~/.config/librarian/`. It holds `activities.yaml`, `files.yaml`, `schema.yaml`,
`changes.log`, and an `artifacts/` folder. The directory is created on first
write. All five original environment overrides (`LIBRARIAN_YAML_PATH`,
`LIBRARIAN_FILES_PATH`, `LIBRARIAN_LEDGER_PATH`, `LIBRARIAN_ROOT`,
`LIBRARIAN_MEMORY_DIR`) are preserved, plus a new `LIBRARIAN_HOME` that moves
the whole home in one variable. Per-resource overrides win over `LIBRARIAN_HOME`,
which wins over the XDG default. `paths.py` performs **no filesystem I/O** —
it only computes paths — which makes the resolution logic trivially unit-tested
(`tests/test_paths.py`).

## 4. Default-schema behavior

**Decision: ship `ptr.yaml` as the bundled default, but do not auto-install it.**

The tool runs **schema-less by default**. On first use it creates the data home
and an empty `activities.yaml`; no `schema.yaml` is written. The user opts into
a schema by copying one of the five files from `schemas/` into the data home as
`schema.yaml` (or pointing `LIBRARIAN_SCHEMA_PATH` at one).

Rationale: silently copying a *post-tenure-review* schema into a new user's data
home would be a confusing default for the majority of users (employees,
students, certified professionals). Schema-less mode is fully functional, so
"no schema yet" is a safe, honest starting state. The README's quickstart walks
through choosing a schema as an explicit step.

## 5. The MCP server

The server still wraps the CLI as a subprocess (keeping the two decoupled). Two
changes:

- **In-repo venv.** The self-bootstrap targets `<repo>/.venv` instead of
  `/tmp/docenv`, and the builder-Python discovery is generic — it tries the
  launching interpreter, then `python3`/`python3.1x` on `PATH`, then common
  install locations, all version-gated. No user pyenv path is hardcoded.
- **Memory directory is opt-in.** The MCP memory resources now require
  `LIBRARIAN_MEMORY_DIR` to be set; there is **no personal default**. When the
  variable is unset, the memory resources return a "disabled" message.

## 6. Tests

The original suites copied a real private `activities.yaml` and asserted on
real entry ids. The new suite (134 tests, pytest) runs entirely against a
committed **synthetic** fixture corpus (`tests/fixtures/sample_activities.yaml`
— eight fictional entries exercising both block types, tags, docs, `file:`
refs, a cross-reference, and a deliberate dangling reference). Every test runs
in an isolated `tmp_path` sandbox via the `sandbox` fixture, so the committed
fixtures are never mutated. Coverage spans read commands, write commands, the
file inventory, schema parsing/validation, value coercion, and path resolution.

## Flag for review

- **`update-notes` default block.** When the schema declares multiple blocks,
  `update-notes` with no `--block` targets the *first* block in schema order.
  That is predictable but implicit; a reviewer may prefer requiring `--block`
  explicitly whenever more than one block exists.
- **`project` keyword matching** dumps each entry to YAML and does a substring
  search. It is simple and dependency-free but O(entries × dump cost); fine for
  thousands of entries, worth revisiting if a corpus grows very large.
- **Single active schema** (see §1 tradeoffs) — confirm this matches the
  intended product scope before 1.0.
