# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.** A public
issue discloses the problem before a fix is available.

Instead, use **GitHub Private Vulnerability Reporting**:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, the impact, and steps to reproduce.

The maintainers will acknowledge the report, work on a fix, and coordinate a
disclosure timeline with you. `librarian` is a local-first tool with no network
service, so the realistic threat surface is small (e.g. path handling, YAML
parsing), but all reports are welcome.

## Supported versions

Security fixes are applied to the latest release on the default branch and
shipped as patch releases on the 1.x line.

---

## Repository setup checklist (for maintainers)

The CI and review workflows in this repository are designed to be safe on a
**public** repository, but several protections cannot be committed as files —
they are GitHub repository settings. After creating the upstream repository,
a maintainer must apply all of the following:

### 1. Restrict fork pull-request workflows

**Settings → Actions → General → Fork pull request workflows from outside
collaborators** → set to **"Require approval for all outside collaborators"**.

This means a maintainer must approve each workflow run requested by an outside
contributor's PR, preventing drive-by Actions execution.

### 2. Branch protection on `main` and `develop`

For **both** the `main` and `develop` branches (Settings → Branches → Add
branch protection rule):

- Require a pull request before merging.
- Require **1 approving review**.
- **Dismiss stale pull-request approvals when new commits are pushed.**
- Require status checks to pass before merging — select the **`CI`** checks.
- Require **conversation resolution** before merging.
- Do **not** allow force pushes.
- Do **not** allow deletions.
- (Recommended) Require review from Code Owners, so `.github/` changes are
  gated by `CODEOWNERS`.

### 3. Verify the CODEOWNERS owner is a direct collaborator

The owner named in `.github/CODEOWNERS` (the placeholder `@maintainer`) must be
replaced with a real GitHub handle, and that account must be a **direct
collaborator** on the repository — or a member of a team with direct repository
access. Permissions inherited only through an organization role do **not**
satisfy CODEOWNERS enforcement: GitHub silently treats the owner as invalid and
the review requirement does not block. Confirm the owner appears under
**Settings → Collaborators and teams**.

### 4. Enable secret scanning and push protection

**Settings → Code security and analysis**:

- Enable **Secret scanning**.
- Enable **Push protection** (blocks commits that contain detected secrets).

### 5. Configure the Claude review secrets

The repository ships three optional Claude-driven workflows. Each one is
**inert** until its secret is provisioned, so you can enable them
independently as you decide what's worth running.

#### `CLAUDE_CODE_OAUTH_TOKEN` — used by `claude-code-review.yml` and `claude.yml`

These two workflows draw against a Claude **subscription** (not metered API
billing). Provision the token by running

```
claude /install-github-app
```

from a local Claude Code session in this repository. The command installs the
official Claude Code GitHub App on the repo and writes the OAuth token to
**Settings → Secrets and variables → Actions** as `CLAUDE_CODE_OAUTH_TOKEN`.

- `claude-code-review.yml` runs automatically on every pull request. The
  outside-collaborator approval gate (step 1 above) is what bounds drive-by
  subscription-quota burn from random fork PRs: a first-time outside
  contributor's first workflow run must be approved by a maintainer before
  any Action executes.
- `claude.yml` (the interactive `@claude` bot) runs only when the commenter /
  issue author has at least **COLLABORATOR** access on the repository. Random
  outside users cannot trigger it even by including `@claude` in their text.

#### Security scanning: Semgrep (OSS, no secret)

A free, token-free Semgrep OSS scan runs first on every pull request and posts
its findings as a single sticky comment, which the `claude-code-review.yml`
reviewer folds into its review. It uses the `p/python`, `p/bash`, `p/secrets`,
and `p/ci` rule packs and needs no API key. This replaced the metered Claude
security-review job, so no `ANTHROPIC_API_KEY` secret is required.
