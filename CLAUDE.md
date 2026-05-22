# Developer Guide — working ON the librarian codebase

This file orients a developer (or an AI coding agent) working on `librarian`
itself. For *using* the tool, see `README.md`.

## What this project is

`librarian` is a local-first, plain-text activity tracker: a CRUD + search tool
over a YAML database of "activity entries". Each entry has fixed core fields
plus optional structured "blocks" defined by a **pluggable schema**.

## Architecture

```
librarian/
  __init__.py      package version (reads the VERSION file)
  paths.py         data-home + env-override path resolution (no I/O)
  schema.py        the pluggable schema engine (parse + validate + coerce)
  storage.py       low-level YAML I/O: line-level editing, fcntl locks, ledger
  files.py         the artifact-file inventory (files.yaml)
  core.py          schema-agnostic analysis: filtering, similarity, scanners
  cli.py           argparse command dispatch — the `librarian` entry point
  mcp_server.py    FastMCP server wrapping the CLI; self-bootstraps .venv
schemas/           four bundled example schemas
agents/            a generic AI-agent definition template
tests/             pytest suite + synthetic fixture corpus
```

Dependency direction: `cli` → (`core`, `schema`, `storage`, `files`, `paths`).
The lower layers never import `cli`. `mcp_server` shells out to the CLI rather
than importing it, so the two stay decoupled.

### Key design rule: line-level YAML editing

`activities.yaml` is hand-formatted and human-edited. Writes must be *surgical*
line-level splices (see `storage.find_entry_line_range` and the `cmd_*`
write functions) — **never** a `yaml.dump` of the whole file, which would
reflow formatting and collapse literal-block scalars. `files.yaml` holds only
scalars, so it *is* safe to load/mutate/dump in full (`files.save_files`).

### The schema engine

The schema is *data*, loaded from `schema.yaml` by `schema.load_schema`. Blocks,
fields, types and enums are all declarative. Nothing about any particular
block (`ptr`, `cpe`, ...) is hardcoded — `validate`, `stats`, `filter` and
`update-nested-field` all consult the loaded `Schema`. With no schema file the
tool runs in generic mode and blocks are simply not validated.

## Running tests and lint

The repo uses an **in-repo `.venv`**. Bootstrap it once:

```bash
./scripts/setup-dev.sh
```

Then:

```bash
.venv/bin/pytest              # run the test suite
.venv/bin/ruff check .        # lint
.venv/bin/ruff format .       # auto-format
.venv/bin/ruff format --check .   # CI-style format check
```

Every test runs against an isolated copy of the synthetic fixture corpus in
`tests/fixtures/` — see the `sandbox` fixture in `tests/conftest.py`. Tests
never touch a real data home.

## Conventions

- **Python ≥ 3.10**, formatted and linted with `ruff` (config in
  `pyproject.toml`). CI runs lint + format-check + tests on every push/PR.
- **Comment density matters.** Every command function, every helper, and the
  schema engine especially carry docstrings and explanatory comments.
- **No personal data.** This is an open-source repo. Fixtures, examples and
  docs use only fictional, generic content.
- **Commits** should be focused and logically grouped. The pre-commit hook
  enforces `ruff`.
- When adding a CLI command: write a `cmd_*` function in `cli.py`, register it
  in the `COMMANDS` dict, and add tests under `tests/`.
- When extending the schema engine: update `schema.py`, keep `FIELD_TYPES`
  authoritative, and add `tests/test_schema.py` coverage.

## Data locations

The default data home is `$XDG_CONFIG_HOME/librarian/` (or
`~/.config/librarian/`). Every path is overridable via `LIBRARIAN_HOME` or the
per-resource `LIBRARIAN_*` variables — see `paths.py`. Tests rely on these
overrides for isolation.
