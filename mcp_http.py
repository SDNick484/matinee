#!/usr/bin/env python3
"""Matinee MCP over Streamable HTTP, behind Cloudflare Access.

Run as the `matinee-mcp` service. The tools are the same as mcp_server.py's, built by
build_server(); mcp_remote.py (a verbatim copy of mcp-server-shared's remote.py) adds the Access
JWT check, the Host check and /healthz. Read-only unless MATINEE_MCP_READONLY is 0/false/no/off.
"""
import argparse
import urllib.request
from typing import Mapping, Optional

import anyio.to_thread

import mcp_remote

_WRITES_ON = {"0", "false", "no", "off"}


def readonly(env: Mapping[str, str]) -> bool:
    """True unless MATINEE_MCP_READONLY is explicitly 0, false, no or off (anything else stays read-only)."""
    return (env.get("MATINEE_MCP_READONLY") or "").strip().lower() not in _WRITES_ON


def matinee_url(env: Mapping[str, str]) -> str:
    """Where the Flask app listens: MATINEE_URL, else loopback on its PORT."""
    return ((env.get("MATINEE_URL") or "").strip()
            or f"http://127.0.0.1:{(env.get('PORT') or '8771').strip()}").rstrip("/")


def matinee_healthy(base_url: str) -> bool:
    """Blocking GET {base_url}/healthz; True only on HTTP 200 within 2 s."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/healthz", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _http_config(env: Mapping[str, str]) -> "mcp_remote.HttpConfig":
    env = dict(env)
    if not (env.get("CF_ACCESS_TEAM_DOMAIN") or "").strip():
        env["CF_ACCESS_TEAM_DOMAIN"] = env.get("CF_TEAM_DOMAIN", "")
    parser = argparse.ArgumentParser()
    mcp_remote.add_http_arguments(parser, default_port=8719, default_path="/matinee/mcp")
    return mcp_remote.http_config(parser.parse_args([]), env)


def build_http_app(env: Mapping[str, str], verifier: Optional["mcp_remote.AccessVerifier"] = None):
    """The ASGI app for the given settings. `verifier` is injected by tests."""
    import mcp_server  # imported late: it reads MATINEE_* from os.environ at import

    cfg = _http_config(env)
    server = mcp_server.build_server(writes=not readonly(env))
    if server is None:
        raise mcp_remote.ConfigError("The MCP SDK is not installed: pip install -r requirements-mcp.txt")
    base = matinee_url(env)

    async def health() -> bool:
        return await anyio.to_thread.run_sync(matinee_healthy, base)

    return mcp_remote.build_app(server, cfg, verifier, health_check=health)
