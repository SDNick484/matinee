"""Tests for the remote (Streamable HTTP) MCP service: mcp_http.py and the vendored mcp_remote.py."""
import hashlib
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver")

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_SHA256 = "cb14c336003cf48afe1aee5bead964adf2c8ca3c23627eb166d07ef3a4dc6c84"
UPSTREAM_COMMIT = "3ccb5f1e1139abb708c113f6663e64eb953cb702"


def test_mcp_remote_copy_is_unmodified():
    lines = (ROOT / "mcp_remote.py").read_text(encoding="utf-8").split("\n")
    assert UPSTREAM_COMMIT in lines[0]
    body = [l for l in lines]
    while body and body[0].startswith("#"):
        body.pop(0)
    assert hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest() == UPSTREAM_SHA256
