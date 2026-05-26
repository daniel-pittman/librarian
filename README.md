<p align="center">
  <img src="docs/img/librarian-logo.png" alt="The Librarian — a friendly young cartoon character with round spectacles and a navy cardigan, holding a small stack of manila folders" width="240">
</p>

# The Librarian

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/daniel-pittman/librarian/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/daniel-pittman/librarian/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![GitHub release](https://img.shields.io/github/v/release/daniel-pittman/librarian)](https://github.com/daniel-pittman/librarian/releases)

Meet **the Librarian**. He's a lightweight, **local-first, plain-text** activity
tracker — keep a running, structured record of what you actually do, so
reporting season is a query instead of a memory test.

---

## Ever had one of these moments?

- You sit down for your **annual performance review** and cannot remember half
  of what you accomplished this year. The big wins from January are a blur.
- You finally start your multi-year **post-tenure review** and realize you
  never wrote down the talks, the committees, the student you mentored to a
  publication.
- Your certification renewal is due and you need to **report continuing-education
  credits** — but the courses and conferences are scattered across an inbox,
  a calendar, and your memory.
- You are a **student** applying to grad school and have to reconstruct three
  years of projects, research, and activities from old folders.
- A great **job posting** appears and you want a resume tailored to *it* — not
  the generic one — but assembling the relevant experience from scratch is
  daunting.

Every one of those is the same problem: the work happened, but it was never
recorded in a form you can search, filter, and report from. `librarian` fixes
that. You log activities as they happen — a couple of minutes each — into a
plain-text file you own. When a reporting moment arrives, the record is already
there.

## What it is

`librarian` is a CRUD + search tool over a YAML database of **activity
entries**. Each entry has a few fixed core fields (id, date, title,
description, tags, supporting docs) plus optional structured **blocks** defined
by a **pluggable schema** you choose. It ships as:

- a **command-line tool** (`librarian`),
- an **MCP server** so AI assistants can read and update the database for you,
- and four **ready-made schemas** for common reporting needs.

## Why local-first, plain-text?

- **You own your data.** It is a YAML file on your disk. No account, no
  subscription, no cloud, no vendor.
- **No lock-in.** Plain text means you can read, grep, edit, and back it up
  with any tool. If you stop using `librarian` tomorrow, your record is still a
  perfectly readable file.
- **Git-friendly.** Commit the file to a private repo and you get a full,
  diff-able history of your career record for free.
- **Extensible.** The schema is data, not code — adapt it to your situation
  without touching the program.
- **AI-agent-friendly.** The bundled MCP server lets an assistant log activities,
  answer "what did I do in Q2?", and assemble tailored reports — using *your*
  real record, not guesses.

## Features

- **Pluggable schema** — structured "blocks" (review classification, credit
  tracking, ...) are declared in a `schema.yaml`, not hardcoded.
- **Four bundled schemas** — performance review, post-tenure review,
  certification credits, student portfolio.
- **Full-text and structured search** — `search`, `filter`, `list`, `project`,
  `similar`, `stats`.
- **Project aggregation** — `project <name>` collects every entry tagged with
  (or mentioning) a project name; `--strict` limits to exact tag match,
  `--broad` includes keyword hits anywhere in the entry.
- **Slice export** — `export` writes a filtered subset of entries to CSV or
  JSON, with date and tag filters, for downstream reporting or import into
  other tools.
- **Format-preserving writes** — edits are surgical, line-level splices; your
  hand-formatting and paragraph breaks survive every write.
- **Schema validation** — `validate` flags bad enum values, missing required
  fields, duplicate ids, dangling cross-references, and inventory problems.
- **Change ledger** — every write is appended to an audit log; poll "what
  changed since I last looked?" with `changes`.
- **File inventory** — track supporting artifacts (PDFs, posters, certificates)
  in a normalized registry, with sha256 de-duplication.
- **Contact rolodex** — auto-built index of the people you collaborate with,
  derived from `Name (email)` mentions in your descriptions and queryable by
  name or email fragment.
- **Safe cross-references** — `rename-id` repoints every backticked **and**
  plain-text reference to an entry across the corpus, token-bounded so longer
  ids aren't matched as substrings.
- **Tag normalization** — `tag-audit` flags case and separator variants of the
  same tag (e.g. `Build-A-Bot` vs `build-a-bot`) so they don't fragment your
  index over time.
- **Concurrency-safe** — `fcntl` advisory locking guards every write.
- **MCP server** — drive the whole tool from an AI assistant.
- **Duplicate detection** — fuzzy similarity warns you before you create a
  near-duplicate entry.
- **Bundled agent template** — a generic AI-agent definition in `agents/`.

## Install

Requires **Python 3.10+**.

```bash
git clone <repository-url>
cd librarian
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This puts a `librarian` command on your `PATH`. (For development — tests and
lint — use `pip install -e ".[dev]"` or run `./scripts/setup-dev.sh`.)

## Quickstart

```bash
# 1. Choose a schema. The tool runs schema-less by default; opt into one by
#    copying it into your data home (created on first use):
mkdir -p ~/.config/librarian
cp schemas/performance-review.yaml ~/.config/librarian/schema.yaml

# 2. Inspect the active schema:
librarian schema

# 3. Log an activity (writes need a --label for the audit ledger):
librarian create --label cli:setup --json '{
  "id": "2026-03-launch",
  "date": "2026-03-15",
  "title": "Led the v2 launch",
  "description": "Shipped the v2 release; coordinated three teams.",
  "tags": ["delivery", "leadership"],
  "docs": ["https://example.com/v2-notes"],
  "review": {"kind": "accomplishment", "competency": "leadership",
             "scope": "organization", "review_period": "2026-H1",
             "notes": "Cross-team delivery under deadline."}
}'

# 4. Search, filter, and summarize:
librarian search launch
librarian filter --block-field review.competency leadership
librarian stats
librarian validate
```

Your data lives in `$XDG_CONFIG_HOME/librarian/` (or `~/.config/librarian/`):
`activities.yaml`, `files.yaml`, `schema.yaml`, `changes.log`, and an
`artifacts/` folder. Every location is overridable — see
[Data location](#data-location).

## The schema system

An entry always has the **core fields** `id`, `date`, `title`, `description`,
`tags`, `docs` (and an optional `end_date`). On top of that, a `schema.yaml`
declares optional structured **blocks** — each with named fields, types, and
enum value sets. The validator, `stats`, `filter`, and the field-update command
all read the active schema; nothing about any particular block is hardcoded.

Field types: `enum`, `text`, `string`, `int`, `bool`, `date`, `date?`
(nullable date). Enums can be **dependent** — the legal values of one field can
depend on the value of a sibling field.

With no `schema.yaml`, `librarian` runs in **generic mode**: full CRUD, search,
and file-inventory still work; blocks are just not validated.

### The four bundled schemas

Each maps to a concrete reporting moment. Pick the one that fits, or adapt one.

| Schema | For | The reporting moment it serves |
|---|---|---|
| `performance-review.yaml` | any employee | the **annual performance review** or promotion packet — the "brag document" |
| `ptr.yaml` | academic faculty | **post-tenure review** — teaching, scholarly, and service classification |
| `cpe.yaml` | certified professionals | **continuing-education credit reporting** — CISSP CPEs, PMP PDUs, nursing CEUs, etc. |
| `student-portfolio.yaml` | students | **grad-school / scholarship / job applications** — coursework, projects, research, awards |

To track more than one at once, merge the blocks from several schema files into
one `schema.yaml` under a single `blocks:` mapping.

## Use case: a tailored resume as a job aid

Here is the payoff of keeping a structured record. Because `librarian` holds a
tagged, searchable database of your **real work**, you — or an AI assistant via
the MCP server — can rapidly assemble a resume **tailored to a specific job
posting**: pull only the entries relevant to that role, and frame each example
toward what the solicitation actually asks for.

A targeted resume drawn from your real record beats a one-size-fits-all resume
padded with experience that does not apply to the role. Instead of "here is
everything I have ever done", you get "here is precisely the experience this
job calls for, with concrete examples and dates" — assembled in minutes,
because the underlying record already exists and is queryable.

```bash
# Find the experience relevant to a posting's keywords:
librarian search "incident response"
librarian filter --tag leadership --after 2024-01-01
librarian export --format json --tag cloud-security
```

Hand that to an assistant connected over MCP and ask it to draft a resume
section for the specific role — it works from evidence, not from a blank page.

## The file inventory

Supporting artifacts (PDFs, posters, slide decks, certificates) are tracked in
a separate normalized registry, `files.yaml`. Register a file with `file-add`;
it gets an id, a category, a sha256 digest, and an `added` date. Entries then
reference a file by putting `file:<id>` in their `docs` list — not a raw path —
so moving or renaming the file is a single `file-move` edit and no entry
reference changes. `file-add` warns (without blocking) on exact-content
duplicates and fuzzily-similar titles. A file need not belong to any entry: a
standalone, categorized artifact is a valid record on its own.

```bash
librarian file-add ~/docs/cert.pdf --category Certifications \
  --title "CISSP Certificate" --label cli:setup
librarian add-docs 2026-03-launch file:cert --label cli:setup
librarian file-list --orphans     # inventory coverage report
```

## The contact rolodex

A side-effect of writing descriptions is a queryable rolodex of the people you
collaborate with. Every time a description mentions someone as
`Name (email@domain)`, the librarian indexes the pair and remembers which
entry mentioned them. Ask `contact <query>` to look someone up by name or
email fragment; the result lists every entry where they appear, so you can
pivot from "who is Jane?" to "everything I've worked on with Jane" without
running two searches. No manual rolodex curation — it's derived purely from
the descriptions you already write.

```bash
librarian contact garcia
#   Dr. Maria Garcia
#     mgarcia@example.edu
#     Sources: 2024-grant-application, 2025-conference-poster,
#              2025-coauthored-paper
```

## The MCP server

`librarian` ships an [MCP](https://modelcontextprotocol.io) server so an AI
assistant can operate the database directly — log activities you describe in
chat, answer questions about your record, and assemble reports. Run it with:

```bash
python3 -m librarian.mcp_server
```

The server self-bootstraps an in-repo `.venv` on first run and exposes the
read tools (no label needed) and write tools (a `session_label` is required for
audit attribution). Point your MCP-capable client at that command to register
it. If you set `LIBRARIAN_MEMORY_DIR`, the directory's Markdown files are also
exposed as MCP resources (opt-in; there is no default).

### Registering with Claude Code (`~/.claude.json`)

The canonical way to launch the server is as a Python module
(`python -m librarian.mcp_server`); launching it by file path also works (a
`__package__` shim in the script handles it), but the module form is preferred.
For Claude Code, add this entry under `mcpServers.librarian` in
`~/.claude.json`, substituting your own paths:

```json
{
  "type": "stdio",
  "command": "/absolute/path/to/librarian/.venv/bin/python",
  "args": ["-m", "librarian.mcp_server"],
  "env": {
    "LIBRARIAN_YAML_PATH":   "/absolute/path/to/activities.yaml",
    "LIBRARIAN_FILES_PATH":  "/absolute/path/to/files.yaml",
    "LIBRARIAN_LEDGER_PATH": "/absolute/path/to/changes.log",
    "LIBRARIAN_ROOT":        "/absolute/path/to/data-root",
    "LIBRARIAN_SCHEMA_PATH": "/absolute/path/to/schema.yaml"
  }
}
```

Every `LIBRARIAN_*` env var is optional; omit it to fall back to the XDG
default (`~/.config/librarian/...`). The env block is the right place to point
the OSS server at an existing data home — e.g. a YAML you already track in a
private directory — without copying the file. `LIBRARIAN_ROOT` is the base
against which inventory file paths (the `path:` field on each `files.yaml`
record) are resolved.

Restart Claude Code after editing `~/.claude.json` so the MCP server is
re-spawned with the new config.

## The bundled agent template

`agents/librarian.md` is a **generic, reusable AI-agent definition template** —
drop it into your assistant's agent configuration to get sensible behavior on
top of the raw tools: search-before-create, schema-aware classification,
consistent tagging, audit-labeled writes, and accurate attribution.

For Claude Code, copy it to `~/.claude/agents/librarian.md` (user-scope, so it
is available to every Claude Code session on your machine):

```sh
cp agents/librarian.md ~/.claude/agents/librarian.md
```

> ⚠️ **Customize before relying on it.** The template ships intentionally
> generic — it has no project-specific judgment until you add it. After
> copying it, edit your local copy to fill in:
>
> - **Your active schema** — which schema you selected (e.g. `ptr`, `cpe`,
>   `performance-review`, `student-portfolio`) and what its blocks mean for
>   your reporting context.
> - **Your tagging conventions** — the projects, people, and topics you tag
>   for, and their canonical forms (lowercase-hyphenated, TitleCase-Hyphenated,
>   etc.).
> - **Project-specific notes** — anything an agent should know about your
>   record-keeping situation (preferred labels, what is in vs. out of scope
>   for you, recurring collaborators).
>
> Generic-as-shipped, the agent is a competent operator of the tool but not a
> curator of *your* record. Customization takes around 15 minutes and is the
> difference between an agent that logs entries blindly and one that behaves
> like a thoughtful collaborator.

## Data location

The default data home is `$XDG_CONFIG_HOME/librarian/`, falling back to
`~/.config/librarian/`. Every location is overridable by an environment
variable:

| Variable | Overrides |
|---|---|
| `LIBRARIAN_HOME` | the whole data home directory |
| `LIBRARIAN_YAML_PATH` | the `activities.yaml` path |
| `LIBRARIAN_FILES_PATH` | the `files.yaml` inventory path |
| `LIBRARIAN_LEDGER_PATH` | the change-ledger path |
| `LIBRARIAN_SCHEMA_PATH` | the `schema.yaml` path |
| `LIBRARIAN_ROOT` | the root that inventory file paths resolve against |
| `LIBRARIAN_MEMORY_DIR` | the optional MCP memory-resource directory |

Per-resource variables win over `LIBRARIAN_HOME`, which wins over the XDG
default.

## Command reference

**Read:** `search`, `get`, `filter`, `list`, `stats`, `tags`, `tag-audit`,
`validate`, `export`, `project`, `similar`, `contact`, `changes`, `schema`

**Write:** `create`, `update-field`, `update-description`, `update-notes`,
`update-nested-field`, `add-tags`, `remove-tags`, `add-docs`, `remove-docs`,
`delete`, `rename-id`

**File inventory:** `file-add`, `file-list`, `file-get`, `file-move`,
`file-update`, `file-rehash`, `file-search`

Run `librarian <command> --help` for command-specific options.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the local
checks (`ruff check`, `ruff format --check`, `pytest`), and the
Claude-driven review workflows that run on every pull request. Security
issues: see [SECURITY.md](SECURITY.md) — please do not open a public issue
for them.

## License

[MIT](LICENSE) © librarian contributors
