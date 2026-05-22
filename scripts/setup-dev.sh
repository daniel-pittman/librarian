#!/usr/bin/env bash
# Set up the in-repo development environment for librarian.
#
# Creates an in-repo `.venv`, installs the project plus dev dependencies into
# it, and (optionally) installs the pre-commit hook. Re-runnable.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV=".venv"

if [[ ! -d "$VENV" ]]; then
    echo "Creating virtual environment at $VENV ..."
    python3 -m venv "$VENV"
fi

echo "Installing librarian (editable) + dev dependencies ..."
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e ".[dev]"

if "$VENV/bin/python" -c "import pre_commit" 2>/dev/null; then
    echo "Installing the pre-commit git hook ..."
    "$VENV/bin/pre-commit" install || true
fi

echo
echo "Done. Activate the environment with:"
echo "    source $VENV/bin/activate"
echo "Then run tests with: pytest    and lint with: ruff check ."
