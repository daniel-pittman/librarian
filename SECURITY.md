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

This project is pre-1.0. Security fixes are applied to the latest release on
the default branch.

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

### 5. Configure the Claude review API key

The `claude-code-review.yml` workflow reads `${{ secrets.ANTHROPIC_API_KEY }}`.
Add it under **Settings → Secrets and variables → Actions → New repository
secret**. The workflow only runs when a maintainer applies the `claude-review`
label to a PR (see CONTRIBUTING.md), so the key is never exposed to untrusted
fork code.
