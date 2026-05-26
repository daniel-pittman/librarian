# Contributing to librarian

Thanks for your interest in improving `librarian`. This is a small, focused,
local-first tool — contributions that keep it that way are very welcome.

## Development setup

```bash
git clone <repository-url>
cd librarian
./scripts/setup-dev.sh        # creates .venv, installs deps, installs hooks
source .venv/bin/activate
```

See `CLAUDE.md` for the architecture overview and conventions.

## Before you open a pull request

Run the full local check — CI runs exactly these:

```bash
ruff check .
ruff format --check .
pytest
```

All three must pass. The pre-commit hook runs `ruff` automatically on commit;
install it with `pre-commit install` if `setup-dev.sh` did not.

- Add tests for any new behavior. The suite runs against the synthetic fixture
  corpus in `tests/fixtures/` — extend the fixtures rather than relying on a
  real database.
- Keep the comment density high — every command and helper carries a docstring.
- **No personal data.** This is a public, open-source repository: examples,
  fixtures and docs use only fictional, generic content.

## Claude-driven review workflows

This repository runs three Claude-driven workflows on contributor activity:

- **Code review** — every PR gets an automatic review comment from Claude.
- **Security review** — PRs targeting `main` or `develop` also get a deeper,
  security-focused review.
- **`@claude` bot** — collaborators can mention `@claude` in an issue or PR
  comment to ask questions, request changes, or have it summarize. Only
  authors with collaborator access can drive it.

First-time outside contributors: your first workflow run may need a
maintainer to approve it before any review runs. After that, runs are
automatic on subsequent pushes.

## Reporting bugs

Open a GitHub issue with steps to reproduce. For **security** issues, do not
open a public issue — follow `SECURITY.md` instead.

## Branching

- `main` — released, stable.
- `develop` — integration branch for upcoming work.

Open pull requests against `develop` unless a maintainer directs otherwise.
