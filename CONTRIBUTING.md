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

## The `claude-review` label

This repository has an optional automated Claude code review. It is **not**
run on every PR. A maintainer requests it by applying the **`claude-review`**
label to a pull request; the review workflow then runs once and posts a review
comment. Outside contributors cannot apply labels, so the review never runs on
untrusted PRs automatically — this is a deliberate security measure (see
`SECURITY.md`). If you would like a Claude review of your PR, ask a maintainer
to apply the label.

## Reporting bugs

Open a GitHub issue with steps to reproduce. For **security** issues, do not
open a public issue — follow `SECURITY.md` instead.

## Branching

- `main` — released, stable.
- `develop` — integration branch for upcoming work.

Open pull requests against `develop` unless a maintainer directs otherwise.
