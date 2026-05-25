"""Tests for the MCP server launch path.

These do NOT spin up a full MCP session; they exercise the script-mode entry
point and verify it does not crash before the FastMCP runtime is ready to
accept stdin. The case that motivates this file is the relative-import pitfall:
a user who launches the server with ``python path/to/mcp_server.py`` (because
the file is right there) instead of ``python -m librarian.mcp_server`` must
not see an ``ImportError`` before the handshake can run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVER_SCRIPT = _REPO_ROOT / "librarian" / "mcp_server.py"


def _mcp_initialize_request() -> bytes:
    """A minimal MCP ``initialize`` JSON-RPC request (one line, newline-terminated)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "librarian-tests", "version": "0"},
        },
    }
    return (json.dumps(payload) + "\n").encode()


def test_mcp_server_runs_as_script_file(tmp_path):
    """Launching the server by file path must not crash with a relative-import
    error. The fix is a small ``__package__`` shim in the ``__main__`` block;
    this test pins that behavior so the pitfall does not regress."""
    env = {
        **os.environ,
        # Sandbox the data home so the launch test does not touch the user's
        # real ~/.config/librarian/ directory.
        "LIBRARIAN_HOME": str(tmp_path),
        # The memory-resource feature is opt-in; explicitly disable it for the
        # test so the server does not try to read an external directory.
        "LIBRARIAN_MEMORY_DIR": "",
    }
    proc = subprocess.run(
        [sys.executable, str(_SERVER_SCRIPT)],
        input=_mcp_initialize_request(),
        capture_output=True,
        timeout=15,
        env=env,
    )
    # The relative-import bug surfaces on stderr as a Python traceback ending
    # in ImportError; a healthy server keeps stderr quiet (or only chatty
    # about the optional venv bootstrap).
    assert b"ImportError" not in proc.stderr, (
        f"mcp_server.py crashed at script-mode launch:\n{proc.stderr.decode(errors='replace')}"
    )
    # A healthy MCP server responds to ``initialize`` with a JSON-RPC result
    # that names the protocol version. Anything else means the server never
    # reached the handshake.
    assert b'"protocolVersion"' in proc.stdout, (
        "server did not respond to initialize:\n"
        f"stdout={proc.stdout.decode(errors='replace')!r}\n"
        f"stderr={proc.stderr.decode(errors='replace')!r}"
    )
