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

This repository has three optional Claude-driven workflows. None of them runs
automatically on every PR — each one is gated so that only maintainers can
trigger it. If you would like one applied to your PR, ask a maintainer.

- **`claude-review` label** → runs `claude-code-review.yml`, a general code
  review that posts a single review comment on the PR. Backed by an OAuth
  subscription (no per-run API cost).
- **`claude-security-review` label** → runs `claude-code-security-review.yml`,
  a deeper security-focused pass. Backed by a metered API key, so it is used
  more sparingly than the general review.
- **`@claude` in a comment, issue, or PR review** → invokes the interactive
  bot in `claude.yml`. The bot can read the repo, comment back, and (when
  asked) commit code changes. It only responds to authors with at least
  COLLABORATOR access — random outside users cannot drive it.

Outside contributors cannot apply labels and do not have the access level
required by the `@claude` bot, so none of these workflows can be triggered
from untrusted fork code. See `SECURITY.md` for the full security rationale.

## Reporting bugs

Open a GitHub issue with steps to reproduce. For **security** issues, do not
open a public issue — follow `SECURITY.md` instead.

## Branching

- `main` — released, stable.
- `develop` — integration branch for upcoming work.

Open pull requests against `develop` unless a maintainer directs otherwise.
