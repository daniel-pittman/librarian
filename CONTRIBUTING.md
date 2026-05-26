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

This repository has three Claude-driven workflows. The first two run
automatically on every PR; the third is interactive.

- **`claude-code-review.yml`** — a general code review that posts a single
  comment on every PR (opened / synchronized / ready-for-review / reopened).
  Backed by an OAuth subscription (no per-run API cost).
- **`claude-security-review.yml`** — a deeper security-focused pass. Runs on
  every PR whose base is `main` or `develop`, and can also be dispatched
  manually by a maintainer. Backed by a metered API key.
- **`@claude` in a comment, issue, or PR review** → invokes the interactive
  bot in `claude.yml`. The bot can read the repo, comment back, and (when
  asked) commit code changes. It only responds to authors with at least
  COLLABORATOR access — random outside users cannot drive it.

Drive-by abuse from untrusted fork PRs is bounded at the repository level:
**Settings → Actions → Fork pull-request workflows from outside collaborators**
is set to "Require approval for all outside collaborators", so a first-time
contributor's first workflow run must be approved by a maintainer before any
Action executes. See `SECURITY.md` for the full security rationale.

## Reporting bugs

Open a GitHub issue with steps to reproduce. For **security** issues, do not
open a public issue — follow `SECURITY.md` instead.

## Branching

- `main` — released, stable.
- `develop` — integration branch for upcoming work.

Open pull requests against `develop` unless a maintainer directs otherwise.
