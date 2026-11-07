#!/usr/bin/env python3
"""Matinee MCP over Streamable HTTP, behind Cloudflare Access.

Run as the `matinee-mcp` service. The tools are the same as mcp_server.py's, built by
build_server(); mcp_remote.py (a verbatim copy of mcp-server-shared's remote.py) adds the Access
JWT check, the Host check and /healthz. Read-only unless MATINEE_MCP_READONLY is 0/false/no/off.
"""
import argparse
import logging
import os
import sys
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


def _health_check(base: str):
    async def health() -> bool:
        return await anyio.to_thread.run_sync(matinee_healthy, base)

    return health


def build_http_app(env: Mapping[str, str], verifier: Optional["mcp_remote.AccessVerifier"] = None):
    """The ASGI app for the given settings. `verifier` is injected by tests."""
    import mcp_server  # imported late: it reads MATINEE_* from os.environ at import

    cfg = _http_config(env)
    server = mcp_server.build_server(writes=not readonly(env))
    if server is None:
        raise mcp_remote.ConfigError("The MCP SDK is not installed: pip install -r requirements-mcp.txt")
    return mcp_remote.build_app(server, cfg, verifier, health_check=_health_check(matinee_url(env)))


def main(argv: Optional[list] = None, env: Optional[Mapping[str, str]] = None) -> int:
    """Run the service. Returns 2 with a one-line stderr message when the settings are unsafe."""
    env = dict(os.environ if env is None else env)
    env["MATINEE_DB_PATH"] = ""  # REST only: this service never opens the database
    api_key = (env.get("MATINEE_API_KEY") or "").strip()
    if not api_key:
        print("matinee-mcp: MATINEE_API_KEY is empty; the service reads Matinee through its admin API.",
              file=sys.stderr)
        return 2
    base = matinee_url(env)
    os.environ["MATINEE_DB_PATH"] = ""
    os.environ["MATINEE_URL"] = base
    try:
        cfg = _http_config(env)
        cfg.check()
    except mcp_remote.ConfigError as exc:
        print(f"matinee-mcp: {exc}", file=sys.stderr)
        return 2
    import mcp_server  # after the environment is set: it reads MATINEE_* at import

    mcp_server._default_client = mcp_server.MatineeClient(api_url=base, api_key=api_key, db_path="")
    server = mcp_server.build_server(writes=not readonly(env))
    if server is None:
        print("matinee-mcp: the MCP SDK is not installed: pip install -r requirements-mcp.txt", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("matinee.mcp").info(
        "matinee-mcp starting: %s on %s:%s (%s)", cfg.path, cfg.bind, cfg.port,
        "read-only" if readonly(env) else "writes enabled")
    mcp_remote.serve_http(server, cfg, health_check=_health_check(base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
