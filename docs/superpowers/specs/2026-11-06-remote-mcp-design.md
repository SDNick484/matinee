# Design Spec: Remote (HTTP) MCP Access — Matinee from the Claude App, Anywhere

**Date:** 2026-11-06
**Branch:** `feat/remote-mcp`
**Schema:** unchanged
**Status:** IMPLEMENTED — PR #13 (merged 969551d, 2026-11-06). PR #15 (`fix/cf-team-domain`) removed the installer's `CF_ACCESS_TEAM_DOMAIN` key; matinee-mcp now derives it from `CF_TEAM_DOMAIN`.
**Post-merge deltas** (see `.superpowers/review/2026-11-08-spec-reconciliation.md`): §5 `CF_ACCESS_TEAM_DOMAIN` row (PR #15); §14 row 2 (ratings and watch notes are public at `/api`, not editor-only); §3 "24 tests" was 25; the host-specific address and hostname in §5/§8 are now placeholders in the shipped files (`192.168.1.100`, `mcp.example.com`); a read-only service no longer advertises `log_watch`/`rate_movie` or the `log_movie_night` prompt.
**Verified against:** `matinee` `main` @ `572d53d` at design time, `mcp-server-shared` `main` @ `3ccb5f1`, `mcp` 2.3.0

---

## 1. Intent

Nick wants to ask the Claude app on his Pixel 9, from anywhere and with Wi-Fi off, *"Where is Paddington 2?"* and get *"Blu-ray at Family Room › Media Cabinet › Shelf 2"*. Today that works only from a desktop that launches `mcp_server.py` over stdio. The MCP server must instead be an always-on service on the Matinee host (CT 210), reachable through the existing Cloudflare Tunnel exactly like his harmony, sofabaton and webos servers.

Claude's connectors are called from Anthropic's cloud, so Tailscale-only is not an option. The endpoint is public and must be locked down properly.

### Goals

- New OpenRC service `matinee-mcp` serving MCP over Streamable HTTP at `https://mcp.nickandrade.com/matinee/mcp`.
- Authentication identical to the sibling servers: Cloudflare Access Managed OAuth at the edge, plus the Access JWT verified at the origin on every request.
- Writes are **off by default** for the remote service (`MATINEE_MCP_READONLY=1`).
- `mcp_server.py` keeps working unchanged over stdio (Claude Desktop, Antigravity, Claude Code).
- No change to the Flask app, the schema, or `$DATA_DIR`.
- Ops parity: `.env.example`, installer, `/etc/conf.d/matinee`, health check, docs, tests, CI.

### Non-goals

- Our own OAuth server, API-key-in-header auth, or any auth the origin invents (§2, decision 1).
- Per-person identity (Lauren's ratings vs Nick's) — needs the backlog's per-person feature.
- Syncing shared code through `mcp-server-shared` (§2, decision 4).
- Exposing the SQLite file to the MCP service (it runs REST-only).
- A Docker deployment.

## 2. Decisions from brainstorming

| # | Question | Decision | Why |
|---|----------|----------|-----|
| 1 | Auth | **Cloudflare Access Managed OAuth**, as harmony / sofabaton / webos. Reuse the existing Access app that covers all of `mcp.nickandrade.com` (path empty) and its AUD tag. | Claude connectors support OAuth (DCR/CIMD) but static request-header credentials are beta for a limited set of organizations. Access *is* the OAuth server, so the app stores no secret and runs no OAuth code. |
| 2 | Remote write tools | **Read-only by default.** `MATINEE_MCP_READONLY=1` removes `rate_movie`, `log_watch`, `add_movie` from the remote server. Set `0` to enable. HTTP service only; stdio is unaffected. | A fresh install can never expose writes by accident. Writes are one config line and a restart away. |
| 3 | Process model | **Separate OpenRC service `matinee-mcp`**, own user, own port **8719**, own venv. | The HTTP transport is ASGI (uvicorn) and needs `mcp>=2.3`; the Flask app is WSGI under gunicorn and stays pure `apk`. Failure isolation. Same shape as the siblings. |
| 4 | Where `remote.py` comes from | **One-off hand copy** of `mcp-server-shared`'s `remote.py` into `mcp_remote.py`, with a header naming the source commit and hash. **No** `shared.lock`, no `sync_shared.py`, no drift job. | Nick does not want to widen `mcp-server-shared`. A unit test guards the copy against accidental edits. The cost (no automatic pickup of upstream fixes) is accepted, §16. |
| 5 | Tool errors | Make `MatineeClient` errors surface to the model as real messages (§6.3). | Verified in the prototype: under `mcp` 2.x an uncaught `RuntimeError` reaches the model only as `Error executing tool where_is`. "Matinee is down" and "bad rating" are indistinguishable. |
| 6 | Port | **8719** | 8716 is taken (`mcp-server-misterfpga`); 8717 Batocera; 8718 Pixelcade. See §3. |

## 3. Current state and drift (verified)

### Corrections to the task statement

| Task said | Code / repos say |
|-----------|------------------|
| Propose 8716 | **8716 is MiSTer FPGA's** (`deploy/alpine/env.example`, docker-compose). Sibling ports: Onkyo 8711, Shield 8712, Harmony 8713, Sofabaton 8714, webOS 8715, MiSTer 8716, Batocera 8717, Pixelcade 8718. Steam-backlog is 8486. **8719 is the next free one in the series.** I cannot run `ss -tlnp` on CT 210 from here: check `ss -tlnp \| grep :8719` before the first start (§17). |
| MCP binds to 127.0.0.1 | The tunnel's cloudflared **runs as a Docker container inside CT 210** (webos `HARDWARE_VALIDATION.md`, 2026-10-14). A route to `localhost` is the container and gives HTTP 502. The siblings route to `http://192.168.1.174:871x` and set `MCP_HTTP_BIND=192.168.1.174`. The MCP service therefore binds CT 210's LAN address. The hop **to Matinee** stays loopback: `MATINEE_URL=http://127.0.0.1:8771`. |
| An unauthenticated request must get 401/404 | Through the tunnel, Access answers 401 + `WWW-Authenticate` (documented for the siblings; to be confirmed for `/matinee/` at deploy). **At the origin, the shared `remote.py` answers 403** `{"error": "forbidden"}`, deliberately: Cloudflare warns that an origin doing its own OAuth conflicts with Managed OAuth. Either way the request never reaches a tool (§7, verified). |

### Drift found

1. **Port 8769 is the code default everywhere; production is 8771.** `app.py:1916`, `installer/setup-head.sh` (lines 7, 15, 30, 113), `.env.example` (lines 5–6: "8769 = next free after Roasted's 8768"), `README.md` (lines 73, 92), `docs/mcp-setup.md` (lines 64, 80, 100), and `mcp_server.py:43` (`MATINEE_URL` default). 8769 is the lunch app's port. Production keeps 8771 only because the installer preserves an existing `PORT=` in `/etc/conf.d/matinee`. A fresh install on this host would collide. **Out of scope to re-default the app** (§16), but the new docs use 8771 and the `mcp-setup.md` examples are corrected because we edit that file anyway.
2. **Nothing runs the MCP server on CT 210 today.** `build.sh` ships `mcp_server.py`, but the installer never installs `mcp`, and `main()` only calls `mcp.run()` (stdio).
3. **SDK range mismatch.** `requirements-dev.txt` says `mcp>=1.0.0`; `mcp_server.py` imports `mcp.server.mcpserver` first, then falls back to `mcp.server.fastmcp`. The shared `remote.py` needs `mcp.server.mcpserver` and `streamable_http_app(streamable_http_path=…)`, so `mcp>=2.3,<3`. `mcp` 2.3.0 declares `Requires-Python >=3.10` (the siblings pin 3.11; I only ran 3.13, so 3.10 is **unverified**; HTTP tests use `importorskip`, §11).
4. **`where_is` differs between modes.** Direct-SQLite mode prefers an exact title match, then the shortest title. REST mode (the only mode the remote service uses) takes `search_collection(query=title, limit=5)[0]`, and `list_titles()` matches `search_text LIKE %word%` for each word, sorted by `sort_title`. So `where_is("Up")` can resolve to whichever owned title containing "up" sorts first (for example *Super 8*, if owned) instead of *Up* (2009). This directly affects the phone use case. Fixed in §6.4.
5. **Unannotated write tools.** Only `get_recommendations` carries `readOnlyHint`. Left alone (§16).
6. **Project docs.** `backlog.md` says `docs/vision.md` is missing; it exists.

### Not verifiable from here

- The sandbox's network proxy refuses `mcp.nickandrade.com` (`CONNECT … 403`), so I could not probe the live Access behavior for `/matinee/`. The 401 claim above rests on the webos README.
- Whether the **Android** app can *add* a custom connector. Anthropic's docs describe adding one under **Customize > Connectors** (claude.ai / Desktop) and that it is usable across surfaces; the webos checklist row "Through Access from the mobile app" is still blank. The plan's last task settles this and the docs are then corrected to what worked.
- Whether `rc-service matinee restart` also restarts `matinee-mcp` (`need matinee`). Checked on CT 210 in the last plan task; the service is resilient either way because each tool call opens a fresh loopback request.

## 4. Architecture

```
Pixel 9 Claude app ──HTTPS──▶ Cloudflare edge ──▶ Access (Managed OAuth, app = all of mcp.nickandrade.com)
                                                     │ sign-in happens here; request continues only with a valid session
                                                     ▼
                        Tunnel route  mcp.nickandrade.com  ^/matinee/  ──▶  http://192.168.1.174:8719
                                                     │  (path is NOT stripped; Host header = mcp.nickandrade.com;
                                                     │   Cf-Access-Jwt-Assertion added by Access)
                                                     ▼
 CT 210 ─ matinee-mcp  (uvicorn, user matinee-mcp, 192.168.1.174:8719, path /matinee/mcp)
            1. Host/Origin check          (421 if Host is not mcp.nickandrade.com / loopback / bind address)
            2. Access JWT check           (403 unless RS256 sig, AUD, issuer, expiry, email all pass)
            3. MCP session + tool         (writes absent unless MATINEE_MCP_READONLY=0)
            4. MatineeClient (urllib) ──▶ http://127.0.0.1:8771/admin/api/…  with  Authorization: Bearer $MATINEE_API_KEY
                                           ▲
                                           └─ matinee (gunicorn/Flask, unchanged)
```

### Auth flow, step by step

1. First use: the Claude app calls the URL, Access answers 401 with the OAuth discovery pointers, Claude runs the Managed OAuth dance with Access (your usual Access login, one-time PIN or IdP), and stores the resulting session.
2. Every later request reaches the tunnel carrying `Cf-Access-Jwt-Assertion`. `mcp_remote.AccessVerifier` fetches Cloudflare's published keys from `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` (cached, refetched on unknown `kid`) and checks RS256 signature, `aud` ∈ `CF_ACCESS_AUD`, `iss`, `exp`/`iat`, and `email` ∈ `MCP_ALLOWED_EMAILS`.
3. Anything that fails gets a plain 403 `{"error": "forbidden"}` and is logged as `Denied POST /matinee/mcp from <ip>: <reason>`. The reason never contains the token (verified: §11, secrets test).
4. `MATINEE_API_KEY` never leaves CT 210. It is not in any URL, any response, or the phone.

## 5. Configuration

All new settings live in `/etc/conf.d/matinee` (the installer *adds* missing keys, never overwrites), documented in `.env.example`.

| Variable | Default written by installer | Read by | Meaning |
|----------|------------------------------|---------|---------|
| `MATINEE_MCP_READONLY` | `1` | `matinee-mcp` | `1` (anything except `0`/`false`/`no`/`off`) = no write tools. Fail-safe: unset, empty or garbage ⇒ read-only. |
| `MCP_HTTP_PORT` | `8719` | `matinee-mcp` | Port. |
| `MCP_HTTP_BIND` | CT 210's detected LAN address | `matinee-mcp` | Where to listen. Must be the address the tunnel route points at (cloudflared is a container). A non-loopback bind **refuses to start without the Access settings below**. |
| `MCP_HTTP_PATH` | `/matinee/mcp` | `matinee-mcp` | The endpoint path. Must start with the tunnel prefix; the tunnel does not strip it. |
| `MCP_PUBLIC_HOSTS` | *(empty — required)* | `matinee-mcp` | `mcp.nickandrade.com`. Without it every request through the tunnel gets HTTP 421. |
| `CF_ACCESS_TEAM_DOMAIN` | *(not written since PR #15; falls back to `CF_TEAM_DOMAIN`)* | `matinee-mcp` | Same team domain as the Flask app's Access. Only set it if the MCP Access app is in a different team. |
| `CF_ACCESS_AUD` | *(empty — required)* | `matinee-mcp` | AUD tag of the **`mcp.nickandrade.com` Access app**. Not `CF_AUD`: that is the Flask `/admin` app's tag. |
| `MCP_ALLOWED_EMAILS` | *(empty)* | `matinee-mcp` | Second gate behind the Access policy. Set it to Nick's Access email. |
| `MATINEE_URL` | *(derived)* `http://127.0.0.1:$PORT` | `matinee-mcp` | Loopback URL of the Flask app. |
| `MATINEE_API_KEY` | *(existing)* | `matinee` and `matinee-mcp` | Required for the remote service (it refuses to start without it). |

`MATINEE_DB_PATH` is **ignored** by the service: it is forced empty so the process never needs the database file.

`.env.example` additions:

```sh
# 1 = the remote MCP service (matinee-mcp) exposes only read tools; 0 = also rate_movie, log_watch, add_movie
MATINEE_MCP_READONLY=1
# Remote MCP service (matinee-mcp), Streamable HTTP behind Cloudflare Access. Port after Pixelcade's 8718.
MCP_HTTP_PORT=8719
# Address matinee-mcp listens on: CT 210's LAN address, because cloudflared runs in a container (127.0.0.1 would 502)
MCP_HTTP_BIND=192.168.1.174
# Endpoint path; the tunnel route ^/matinee/ does not strip it
MCP_HTTP_PATH=/matinee/mcp
# Public hostname the tunnel forwards (required, otherwise HTTP 421)
MCP_PUBLIC_HOSTS=mcp.nickandrade.com
# Access application that covers mcp.nickandrade.com (team domain defaults to CF_TEAM_DOMAIN; AUD is NOT CF_AUD)
CF_ACCESS_TEAM_DOMAIN=
CF_ACCESS_AUD=
# Comma-separated Access emails allowed to use the MCP endpoint
MCP_ALLOWED_EMAILS=
```

## 6. Code layout

```
mcp_server.py        stdio entry, unchanged behavior; gains build_server(), WRITE_TOOLS, MatineeError (§6.2–6.4)
mcp_http.py          NEW  HTTP entry: env → config → read-only → remote.serve_http   (~90 lines)
mcp_remote.py        NEW  verbatim copy of mcp-server-shared remote.py (462 lines) + 3-line header
requirements-mcp.txt NEW  runtime pins for the matinee-mcp venv
```

### 6.1 `mcp_remote.py`

The header is three comment lines; the rest is byte-identical to upstream:

```python
# Verbatim copy of SDNick484/mcp-server-shared src/mcp_shared/remote.py @ 3ccb5f1e1139abb708c113f6663e64eb953cb702
# upstream-sha256: cb14c336003cf48afe1aee5bead964adf2c8ca3c23627eb166d07ef3a4dc6c84
# Hand copy, no sync tooling. To update: re-copy, change these two lines, run pytest.
"""Serve an MCP server over Streamable HTTP, behind Cloudflare Access.
```

`remote.py` is identical in harmony, webos, sofabaton and batocera today (same SHA), so Matinee runs the exact code that was hardware-verified through the Claude app on 2026-10-14. A test strips the header and fails if the hash differs (§11).

### 6.2 `build_server()` (pure refactor, first commit)

The tool definitions currently run at import into one global `mcp`. `remove_tool()` is permanent for a process, so a global singleton can't be tested both ways. Wrap the existing `if mcp:` block:

```python
WRITE_TOOLS = ("rate_movie", "log_watch", "add_movie")

def build_server(*, writes: bool = True):
    server = _make_server()
    if not server:
        return None
    @server.tool()
    def search_collection(...): ...      # bodies unchanged; only `mcp.` → `server.` and one indent level
    ...
    if writes:
        @server.tool()
        def rate_movie(...): ...
        # log_watch, add_movie
    return server

mcp = build_server()                      # unchanged for stdio, tests, Claude Desktop
```

`git diff -w` shows only `mcp.`→`server.` renames. The existing `tests/test_mcp_server.py` tests (24 today) are the safety net and must pass untouched in that commit.

### 6.3 Errors the model can read

```python
try:
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    try:
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:
        ToolError = Exception

class MatineeError(RuntimeError, ToolError):
    """Raised for every Matinee failure; ToolError text reaches the model, RuntimeError keeps old callers and tests working."""
```

`_api_request` raises `MatineeError` with messages that contain no URL, no key and no HTML:

| Cause | Message the model sees (after the SDK prefix `Error executing tool where_is: `) |
|-------|------------|
| connection refused / timeout | `Matinee is unreachable right now. Try again in a minute.` |
| HTTP 401/403 | `Matinee rejected the service's API key (HTTP 401). Check MATINEE_API_KEY on CT 210.` |
| HTTP 404 on a title | `Movie with id 99999 not found.` (unchanged) |
| other HTTP error | `Matinee API error (500): <first 200 chars of the JSON error, or no body if it is HTML>` |

The detail (URL, exception text) goes to the server log only. Stdio users get the same clearer messages; nothing that matched `RuntimeError` breaks.

### 6.4 `where_is` REST parity

Search with `limit=50`, then pick the exact case-insensitive title match, otherwise the shortest title, mirroring the SQLite branch. Test with *Up* vs *Super 8* style fixtures.

### 6.5 `mcp_http.py`

```python
def main(argv=None, env=None) -> int:
    env = dict(os.environ if env is None else env)
    env["MATINEE_DB_PATH"] = ""                              # REST only
    env.setdefault("CF_ACCESS_TEAM_DOMAIN", env.get("CF_TEAM_DOMAIN", ""))
    if not env.get("MATINEE_API_KEY", "").strip():
        return fail("MATINEE_API_KEY is empty: matinee-mcp reads Matinee through its admin API.")
    os.environ.update(MATINEE_DB_PATH="", MATINEE_URL=env.get("MATINEE_URL") or f"http://127.0.0.1:{env.get('PORT', '8771')}")
    import mcp_server, mcp_remote as remote                  # after env is set: mcp_server reads it at import
    server = mcp_server.build_server(writes=not readonly(env))
    parser = argparse.ArgumentParser(); remote.add_http_arguments(parser, default_port=8719, default_path="/matinee/mcp")
    cfg = remote.http_config(parser.parse_args(argv), env)   # remote.check() refuses unsafe combos
    remote.serve_http(server, cfg, health_check=matinee_healthy)
```

`matinee_healthy()` runs `GET $MATINEE_URL/healthz` (timeout 2 s, in a worker thread) and returns a bool, so `/healthz` is 200 only while Matinee answers. `/healthz` is answered before auth and returns only `{"status": …}`; the tunnel route does not publish it.

## 7. Transport, auth flow and exact requests/responses

Captured on 2026-11-06 from a prototype run: the real `remote.py` (SHA above), Matinee's real `mcp_server.py`, `mcp` 2.3.0, uvicorn, a fake Matinee on loopback requiring `Authorization: Bearer <key>`, and test-signed Access JWTs. Shapes and status codes are exact. **The data (title ids, the shelf path) is illustrative.** Public URL `https://mcp.nickandrade.com` stands in for the loopback test URL; the Host header was `mcp.nickandrade.com`.

### 7.1 Allowed: the Claude app, after Access sign-in

Headers on every call: `Content-Type: application/json`, `Accept: application/json, text/event-stream`, plus `Cf-Access-Jwt-Assertion` (added by Access, not by the client) and `Mcp-Session-Id` after `initialize`.

**`initialize`** — `POST https://mcp.nickandrade.com/matinee/mcp`

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"claude-android","version":"1.0"}}}
```
```
HTTP 200   content-type: text/event-stream   mcp-session-id: 6cedd802ad0149af8f0128d500c55713
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"prompts":{"listChanged":false},"resources":{"listChanged":false,"subscribe":false},"tools":{"listChanged":false}},"instructions":"Matinee is the family's own collection of physical discs and digital copies.\n- Recommend only what get_recommendations … (SERVER_INSTRUCTIONS, unchanged)","protocolVersion":"2025-06-18","serverInfo":{"name":"matinee","version":""}}}
```

**`notifications/initialized`** → `HTTP 202`, empty body.

**`tools/list`**, default (`MATINEE_MCP_READONLY=1`) returns exactly:
`search_collection, get_movie, where_is, get_recommendations, get_collection_stats, search_tmdb`.
With `MATINEE_MCP_READONLY=0` it additionally lists `rate_movie, log_watch, add_movie`.

**`tools/call` `where_is` "Paddington 2"**

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"where_is","arguments":{"title":"Paddington 2"}}}
```
```
HTTP 200   content-type: text/event-stream
event: message
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"text":"{\n  \"found\": true,\n  \"title\": \"Paddington 2\",\n  \"year\": 2017,\n  \"copies\": [\n    {\n      \"id\": 351,\n      \"kind\": \"physical\",\n      \"format\": \"Blu-ray\",\n      \"service\": null,\n      \"location_id\": 9,\n      \"location_path\": \"Family Room › Media Cabinet › Shelf 2\"\n    }\n  ],\n  \"summary\": \"Paddington 2 (2017): Blu-ray at Family Room › Media Cabinet › Shelf 2\"\n}","type":"text"}],"isError":false,"structuredContent":{"result":{"found":true,"title":"Paddington 2","year":2017,"copies":[{"id":351,"kind":"physical","format":"Blu-ray","service":null,"location_id":9,"location_path":"Family Room › Media Cabinet › Shelf 2"}],"summary":"Paddington 2 (2017): Blu-ray at Family Room › Media Cabinet › Shelf 2"}}}}
```

### 7.2 Rejected: nothing reaches a tool

The same `tools/call` body, varying only what is noted. After R1–R9 the fake Matinee had recorded **zero** requests, and across the whole run (including the accepted calls) the captured output contained neither the API key, nor any `eyJ…` token, nor `Bearer`.

| # | Request | Response (exact) |
|---|---------|------------------|
| R1 | No `Cf-Access-Jwt-Assertion` | `403` `{"error": "forbidden"}` — log: `Denied POST /matinee/mcp from <ip>: no Cloudflare Access assertion (request didn't come through Access)` |
| R2 | Assertion `not-a-jwt` | `403` `{"error": "forbidden"}` — `invalid Access assertion: Not enough segments` |
| R3 | Valid signature, wrong `aud` | `403` `{"error": "forbidden"}` — `Audience doesn't match` |
| R4 | Expired | `403` `{"error": "forbidden"}` — `Signature has expired` |
| R5 | Signed by a different key | `403` `{"error": "forbidden"}` — `Signature verification failed` |
| R6 | Valid, email not in `MCP_ALLOWED_EMAILS` | `403` `{"error": "forbidden"}` — `other@example.com is not in MCP_ALLOWED_EMAILS` |
| R7 | Valid, `Host: evil.example.com` | `421` body `Invalid Host header` |
| R8 | Valid, `POST /mcp` (outside the prefix) | `404` body `Not Found` |
| R9 | Valid, `GET /matinee/healthz` | `404` body `Not Found` (health is only `/healthz`, unrouted by the tunnel) |
| H1 | `GET /healthz`, no auth, origin only | `200` `{"status": "ok"}` |
| H2 | same, Matinee down | `503` `{"status": "degraded", "error": "health check returned False"}` |

**Through the tunnel** the first line of defense is Access, so the smoke-test command from any machine is:

```sh
curl -si -X POST https://mcp.nickandrade.com/matinee/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -5
# expected: HTTP 401 plus a www-authenticate header, from Access (as for /webos/mcp). Anything 200 is a failure.
```

### 7.3 Read-only and failure behavior

| Case | Response |
|------|----------|
| `tools/call rate_movie` while read-only | `200` `{"jsonrpc":"2.0","id":6,"result":{"content":[{"text":"Unknown tool: rate_movie","type":"text"}],"isError":true}}` — and it is absent from `tools/list` |
| `where_is` with Matinee down, today's code | `…"content":[{"text":"Error executing tool where_is","type":"text"}],"isError":true` (useless) |
| same, with §6.3 | `…"content":[{"text":"Error executing tool where_is: Matinee is unreachable right now. Try again in a minute.","type":"text"}],"isError":true` (the prototype measured this exact string using a `ToolError`) |

Sessions are stateful (`Mcp-Session-Id`). A `matinee-mcp` restart drops them; per the MCP spec the client re-initializes. Not exercised here (§17).

## 8. Tunnel and Cloudflare (Nick adds these in the dashboard)

| Where | Setting |
|-------|---------|
| Tunnel: "Dinner Log, Steam, & Wishlist" → Published application routes → Add | **Hostname** `mcp.nickandrade.com` · **Path** `^/matinee/` (regex) · **Service** `http://192.168.1.174:8719` · HTTP Host Header override: **unset** |
| Access app for `mcp.nickandrade.com` | **No change** if it already has path empty and Managed OAuth on (decision 1). Verify: no other Access app, and no Bypass policy, covers a path under `/matinee`. Copy its **AUD tag** into `CF_ACCESS_AUD`. |
| Claude: Customize > Connectors > Add custom connector | URL `https://mcp.nickandrade.com/matinee/mcp`. Authentication: **Sign in now**. OAuth client: the default (Claude's published identity) or **Register automatically**; the webos connector already worked through this Access app. |
| Claude Code (optional) | `claude mcp add --transport http matinee https://mcp.nickandrade.com/matinee/mcp` |

The Access app already allows localhost clients and `https://claude.ai/*` (webos README step 1).

## 9. OpenRC service and installer

### 9.1 `/etc/init.d/matinee-mcp` (heredoc in `setup-head.sh`, like `matinee`)

```sh
#!/sbin/openrc-run
name="matinee-mcp"
description="Matinee MCP server (Streamable HTTP, behind Cloudflare Access)"
# OpenRC runs this as root, so it can read the root-only config; the service user never reads it.
[ -r /etc/conf.d/matinee ] && . /etc/conf.d/matinee
: ${PORT:=8769}
: ${MATINEE_URL:=http://127.0.0.1:${PORT}}
# Only what this service needs: TMDB_READ_TOKEN and OMDB_API_KEY deliberately stay out of its environment.
export MATINEE_URL MATINEE_API_KEY MATINEE_MCP_READONLY MCP_HTTP_BIND MCP_HTTP_PORT MCP_HTTP_PATH \
       MCP_PUBLIC_HOSTS CF_ACCESS_TEAM_DOMAIN CF_ACCESS_AUD MCP_ALLOWED_EMAILS CF_TEAM_DOMAIN
supervisor="supervise-daemon"
respawn_delay=5
respawn_max=10
respawn_period=600
command="/opt/matinee-mcp/venv/bin/python"
command_args="/opt/matinee/mcp_http.py"
command_user="matinee-mcp:matinee-mcp"
directory="/opt/matinee"
output_log="/var/log/matinee-mcp.log"
error_log="/var/log/matinee-mcp.log"
depend() { need net matinee; after firewall; }
start_pre() { checkpath -f -m 0640 -o matinee-mcp:matinee-mcp /var/log/matinee-mcp.log; }
```

### 9.2 Installer changes (`installer/setup-head.sh`, `installer/build.sh`)

1. **build.sh:** add `mcp_http.py mcp_remote.py requirements-mcp.txt` to the `cp -r` list.
2. **Runtime and the Alpine rule.** `apk add py3-pip` plus a venv for the new service only: `python3 -m venv /opt/matinee-mcp/venv && pip install --no-cache-dir -r /opt/matinee/requirements-mcp.txt`. **`mcp` is not available as a `py3-*` Alpine package** (an exact-name search of the Alpine `edge` x86_64 index for `py3-mcp` returned nothing), so this one dependency stack (`mcp>=2.3,<3`, `uvicorn>=0.31`, `pyjwt[crypto]>=2.10`, and what they pull in: starlette, anyio, pydantic, …) is installed with pip into the venv, exactly as the sibling servers do. This is the one explicit exception to the "apk only" rule. The Flask app and its `apk` packages are untouched; no frontend change.
3. **A pip/PyPI failure must not break the main install:** the venv step is guarded; on failure it prints a warning, skips `matinee-mcp`, and the `matinee` service still installs and starts.
4. **User and log:** `matinee-mcp` system user and group (no shell, no home data), `/var/log/matinee-mcp.log`.
5. **Config:** `add_setting` for each key in §5 (only if the key is absent). `MCP_HTTP_BIND` is the detected LAN address (the installer already computes `$IP`). `MATINEE_API_KEY` is **not** generated.
6. **Secrets file mode:** `/etc/conf.d/matinee` becomes `chmod 600`, `chown root:root` (was `640 root:matinee`). OpenRC reads it as root; the cron jobs (`matinee-backup`, `matinee-refresh`) run as root; `ops/import_titles.py` documents `. /etc/conf.d/matinee` as root. Verified by reading the repo; nothing reads it as the `matinee` user.
7. **Start only when configured:** `rc-update add matinee-mcp default` always; `rc-service matinee-mcp start` only if `CF_ACCESS_AUD`, `MCP_PUBLIC_HOSTS` and `MATINEE_API_KEY` are non-empty, then poll `http://$IP:8719/healthz`. Otherwise print exactly which keys to fill and the start command.
8. **Health checks:** append `Matinee MCP|http://$IP:8719/healthz|matinee-mcp` to `HEALTH_CHECKS` using the existing awk append logic.
9. **`--uninstall`:** also stop and remove `matinee-mcp` and `/opt/matinee-mcp`, keeping config and logs like today.
10. **Final banner:** print the tunnel route values from §8.

Re-running the installer is idempotent: it never overwrites a key already in the config and never touches `$DATA_DIR`.

## 10. Docs

- **`docs/mcp-setup.md`:** new §"Remote access from the Claude app" with the architecture, the settings table, the exact tunnel route (§8), connector steps, and "turning on write tools". Correct the stale `:8769` examples to `:8771`. State which steps were verified on Android and which were not (§3).
- **`.env.example`:** §5 block.
- **`README.md`:** one-line pointer.
- **Project docs:** `app-overview.md` should say the port is 8771 and mention `matinee-mcp` (to update after merge).

## 11. Test plan

No live network. New file `tests/test_mcp_http.py`; every test skips with `pytest.importorskip("mcp.server.mcpserver")` so the Python 3.10 job and old SDKs stay green.

Harness: a generated RSA key, `mcp_remote.AccessVerifier(policy, key_resolver=…)` (the hook the shared module exposes for tests), and a fake Matinee on an ephemeral loopback socket or a monkeypatched `urlopen`. Preferred driver is Starlette's `TestClient` used as a context manager (runs the lifespan the MCP session manager needs); the prototype used uvicorn on a loopback port, which is the fallback. Decided in plan task 1.

| Area | Cases |
|------|-------|
| **Auth accept/reject** | R1–R7 from §7.2 each assert the exact status and body; a valid token returns 200 and `where_is("Paddington 2")` returns the §7.1 body. For every rejection, a spy on `_default_client` asserts **no tool function ran** and the fake Matinee saw **no** request. |
| **Read-only mode** | `build_server(writes=False)` lists exactly the six read tools; `tools/call rate_movie` returns `Unknown tool`; `writes=True` lists nine. Env parsing: unset, `""`, `1`, `true`, `garbage` ⇒ read-only; `0`, `false`, `no`, `off` ⇒ writes. Default with no env is read-only. |
| **Path prefix** | `/matinee/mcp` works; `/mcp` and `/matinee/` and `/matinee/healthz` → 404; `MCP_HTTP_PATH=/x/mcp` moves the endpoint; wrong `Host` → 421; `/healthz` → 200 without auth. |
| **Matinee unreachable / HTTP error** | `MATINEE_URL` at a closed loopback port → `isError` with `Matinee is unreachable right now…`; fake Matinee answering 401 → API-key message; 500 with an HTML body → no HTML in the message; `/healthz` → 503 when Matinee is down. `pytest.raises(RuntimeError)` still passes for `MatineeError`. |
| **Secrets** | `caplog` at DEBUG across rejected and accepted requests and error paths: the API key, the test JWT and the string `Bearer` never appear in log text or in any response body. |
| **Startup refusals** | Empty `MATINEE_API_KEY` → non-zero exit with the message; non-loopback bind with no Access settings → `ConfigError`; `MATINEE_DB_PATH` set → ignored (forced empty). |
| **Copy integrity** | `mcp_remote.py` minus its three header lines has SHA-256 `cb14c336…6dc84`. |
| **`where_is` parity** | REST `where_is("Up")` picks the exact title over alphabetically earlier substring matches; same answer as the SQLite branch (extends `test_mcp_direct_sqlite_matches_api`). |
| **stdio unchanged** | All existing `tests/test_mcp_server.py` tests pass untouched after the refactor commit; `mcp_server.main()` still calls `mcp.run()`. |
| **Installer (static)** | A `test_installer_static.py` in the style of `test_static_ui.py` checks the built script contains the `matinee-mcp` init script, `need net matinee`, `MATINEE_MCP_READONLY=1` as the default, `chmod 600`, the guarded pip step, and the health-check line. |

## 12. CI

- `requirements-dev.txt`: `mcp>=1.0.0` stays for the existing tests; add `uvicorn>=0.31`, `pyjwt[crypto]>=2.10`, `anyio`, and whatever Starlette's `TestClient` needs (`httpx` or `httpx2`, decided in plan task 1; the sibling dev extras use `httpx2`). If `mcp` 2.x does not install on 3.10, the HTTP tests skip there; that is acceptable and will be noted in the PR.
- Alpine job: `py3-pip` is already in its package list; add a `pip install` of the same HTTP stack beside `mcp`. The existing "Build self-extracting installer" and `sh -n` steps cover the new heredoc syntax.
- `node -c static/app.js` unchanged. Python 3.10–3.13 matrix unchanged.

## 13. Rollback

| Goal | Action |
|------|--------|
| Stop remote access now | `rc-service matinee-mcp stop` (and `rc-update del matinee-mcp`). The web app is unaffected. |
| Remove public exposure | Delete the `^/matinee/` tunnel route (leave the Access app alone); remove the connector in Claude. |
| Disable only writes | `MATINEE_MCP_READONLY=1` in `/etc/conf.d/matinee`, `rc-service matinee-mcp restart`. |
| Key compromised | New `MATINEE_API_KEY` in `/etc/conf.d/matinee`; `rc-service matinee restart && rc-service matinee-mcp restart`. |
| Full revert | Revert the PR; re-run the previous installer (leaves `/etc/init.d/matinee-mcp` and `/opt/matinee-mcp`, remove them with `rc-update del matinee-mcp; rm -rf /etc/init.d/matinee-mcp /opt/matinee-mcp`). If anything needs `/etc/conf.d/matinee` readable by the `matinee` user again: `chmod 640; chown root:matinee`. |
| Data | No schema or `$DATA_DIR` changes, so there is nothing to restore. |

## 14. Security and blast radius

| If this leaks or is abused | What the holder gets |
|----------------------------|----------------------|
| A request with no/forged/expired Access JWT (internet or LAN) | 403 (or Access 401). No tool runs. |
| A stolen Access session | Whatever the tools allow, until it expires (15 minutes if the Access app is set as in the webos README). Read-only by default: titles, shelves, ratings and watch notes — the same data `GET /api/titles/<id>` serves anonymously when `PUBLIC_VIEW=1` (`title_detail()` does not gate them; only copies' `added_by` is editor-only). To the read tools `MATINEE_API_KEY` adds only `search_tmdb` and copies' `added_by` (the editor's Access email, via `get_movie`). |
| Someone on the LAN who can reach `192.168.1.174:8719` | `/healthz` only. They cannot mint a Cloudflare-signed JWT. (The Flask app on `0.0.0.0:8771` with `LAN_EDIT=1` is a pre-existing, separate exposure.) |
| Code execution as `matinee-mcp` | `MATINEE_API_KEY`, which is **write-capable** on the admin API regardless of `MATINEE_MCP_READONLY`. Mitigations: dedicated user with no database access, bound to a LAN address, `MATINEE_DB_PATH` forced empty, TMDB/OMDb keys not in its environment. A scoped read-only key is deferred (§16). |
| A prompt-injected model | In read-only mode: nothing destructive is reachable. With writes on: it can rate, log watches and import titles. |

## 15. Commit sequence (Conventional Commits, one logical change each)

1. `refactor(mcp): register tools via build_server()` (no behavior change)
2. `feat(mcp): MatineeError surfaces readable tool errors`
3. `fix(mcp): REST where_is prefers exact title match`
4. `feat(mcp): vendor mcp-server-shared remote.py as mcp_remote.py`
5. `feat(mcp): add mcp_http.py with read-only default` (tests ship in the same commit)
6. `test(mcp): HTTP path, host, health, unreachable and secrets`
7. `feat(mcp): mcp_http main with startup refusals`
8. `feat(installer): matinee-mcp OpenRC service and venv`
9. `docs: remote MCP setup, correct 8769 → 8771 in mcp-setup`
10. `ci: HTTP MCP dependencies`
11. `docs: record remote MCP deployment results`

All on `feat/remote-mcp`, merged by PR. Author email `SDNick484@users.noreply.github.com`.

## 16. Deferred

- A **read-only API key** on the Flask side, so the remote service can't write even if compromised (`MATINEE_API_KEY` is all-or-nothing today).
- A tiered write flag (`MATINEE_MCP_WRITES=none|log|all`): allow `log_watch` and `rate_movie` but not `add_movie`.
- Per-tool rate limiting and log redaction (`mcp-server-shared`'s `ratelimit.py` and `logsafe.py`, not taken with option B).
- Pulling upstream fixes to `remote.py`: manual, by re-copying and changing the header.
- Tool annotations (`readOnlyHint` / `destructiveHint`) on the write tools.
- Re-defaulting the repo's port from 8769 to 8771 (`app.py`, installer, `.env.example`, README): a separate `fix:` PR.
- Per-person identity from the Access email (needs the backlog's per-person feature).
- Direct-SQLite reads for the remote service (would need DB group access).
- Docker packaging.

## 17. To verify on CT 210 during the last plan task

1. `ss -tlnp | grep ':8719'` is empty before the first start.
2. `rc-service matinee restart` does or does not restart `matinee-mcp`, and the docs say which.
3. Android: whether the connector can be added from the phone or must be added on claude.ai/Desktop first; then update `mcp-setup.md` to what worked.
4. The unauthenticated public `curl` in §7.2 returns 401 with `www-authenticate`.
5. After `rc-service matinee-mcp restart`, the phone re-initializes without manual action.
6. Python 3.10 CI result for the HTTP tests (expected skip or pass).
