# Remote (HTTP) MCP Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve Matinee's MCP tools to the Claude app over Streamable HTTP as an always-on `matinee-mcp` service behind Cloudflare Access, read-only by default, with stdio unchanged.

**Architecture:** A new ASGI entry point `mcp_http.py` builds the existing tools via a new `build_server(writes=…)` factory and wraps them with a verbatim copy of `mcp-server-shared`'s `remote.py` (Access JWT check, Host check, health). It talks to Flask over loopback REST with `MATINEE_API_KEY`. A separate OpenRC service and pip venv run it on CT 210.

**Tech Stack:** Python 3, `mcp` 2.3 (`MCPServer`), uvicorn, Starlette, PyJWT, pytest, POSIX `sh` installer, OpenRC.

**Spec:** `docs/superpowers/specs/2026-11-06-remote-mcp-design.md` (read it first; section numbers below refer to it)

## Global Constraints

- Port **8719**, path **`/matinee/mcp`**, public host `mcp.nickandrade.com`, tunnel route regex `^/matinee/` → `http://192.168.1.174:8719` (§5, §8).
- `MATINEE_MCP_READONLY` defaults to **`1`**; only `0`, `false`, `no`, `off` (case-insensitive, trimmed) enable writes; unset, empty or garbage ⇒ read-only (§5).
- `mcp_remote.py` = three header lines + byte-identical upstream `remote.py`; upstream SHA-256 `cb14c336003cf48afe1aee5bead964adf2c8ca3c23627eb166d07ef3a4dc6c84`, commit `3ccb5f1e1139abb708c113f6663e64eb953cb702` (§6.1). No sync tooling.
- `mcp_server.py` stays importable and runnable over stdio with unchanged behavior; every existing test in `tests/test_mcp_server.py` passes untouched (§6.2).
- No secrets (API key, JWT, `Bearer`) in logs, URLs, or error text; no URL in client-facing error text (§6.3, §7.2).
- `MATINEE_DB_PATH` is forced empty in the service; `TMDB_READ_TOKEN` / `OMDB_API_KEY` are not exported to it (§5, §9.1).
- Alpine rule exception, stated explicitly: `mcp` is not an Alpine `py3-*` package, so only the `matinee-mcp` service gets a pip venv (`mcp>=2.3,<3`, `uvicorn>=0.31`, `pyjwt[crypto]>=2.10`). Flask stays apk-only (§9.2).
- `/etc/conf.d/matinee`: installer adds missing keys, never overwrites; file ends `chmod 600`, `chown root:root` (§9.2).
- Tests: no live network; HTTP tests start with `pytest.importorskip("mcp.server.mcpserver")`. CI stays green on Python 3.10–3.13, the Alpine job, and `node -c static/app.js`.
- Conventional Commits, one logical change per commit, branch `feat/remote-mcp`, author email `SDNick484@users.noreply.github.com`, commit trailers per the session's attribution reminder.
- Shell hints for docs: `vim`, `rc-service matinee-mcp restart`.

## Review Focus

Failure modes the spec implies but no obvious test covers. Each has a test in the task named in brackets.

1. **Non-ASCII titles** — `where_is("WALL·E")` and `"Amélie"` must reach Matinee URL-encoded and still match. [Task 3]
2. **A hung Matinee** — a loopback socket that accepts and never answers must end in the "unreachable" message within the 15 s client timeout, not hang the tool. [Task 2]
3. **Access token with no `email` claim** (for example a service token) while `MCP_ALLOWED_EMAILS` is set → 403, no tool runs. [Task 5]
4. **A stale `Mcp-Session-Id`** after a `matinee-mcp` restart → a clean 404, never a tool run or a 500, so the client can re-initialize. [Task 6]
5. **Installer re-run** — a second run must not reset `MATINEE_MCP_READONLY=0` back to `1`, must not touch `MATINEE_API_KEY`, and a failing `pip install` must not stop the main `matinee` service installing. [Task 8]

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `mcp_server.py` | modify | `build_server(writes)`, `WRITE_TOOLS`, `MatineeError`, REST `where_is` fix. Stdio entry unchanged. |
| `mcp_remote.py` | create | Verbatim `remote.py` copy plus 3-line header. Never edited. |
| `mcp_http.py` | create | Env → config → read-only → ASGI app → `serve_http`. |
| `requirements-mcp.txt` | create | Pins for the `matinee-mcp` venv. |
| `tests/test_mcp_server.py` | modify | New tests for `build_server`, `MatineeError`, `where_is`. |
| `tests/test_mcp_http.py` | create | Harness plus auth, read-only, path, health, unreachable, secrets, startup, copy-integrity tests. |
| `tests/test_installer_static.py` | create | Static checks on `installer/setup-head.sh`. |
| `installer/setup-head.sh`, `installer/build.sh` | modify | Service, venv, user, settings, perms, health check, uninstall. |
| `.env.example`, `docs/mcp-setup.md`, `README.md` | modify | Settings and remote setup docs. |
| `requirements-dev.txt`, `.github/workflows/ci.yml` | modify | HTTP stack in CI. |

---

### Task 1: `build_server()` refactor (no behavior change)

**Files:**
- Modify: `mcp_server.py:534-680` (the `if mcp:` registration block)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `WRITE_TOOLS: tuple[str, ...] = ("rate_movie", "log_watch", "add_movie")`; `build_server(*, writes: bool = True)` returns a fresh server (or `None` without the SDK) with all tools, resources and prompts registered; `mcp = build_server()` stays the module-level default. Tool bodies keep calling the module-level `_default_client` **at call time** (so tests can monkeypatch it).

- [ ] **Step 1: Write the failing tests** in `tests/test_mcp_server.py` (use the file's existing `asyncio.run(...)` style and the same list-tools call the existing `test_tool_schema_enumerates_modes_and_certs` uses):
  - `test_build_server_writes_true_lists_nine_tools`: names equal `{"search_collection","get_movie","where_is","get_recommendations","get_collection_stats","rate_movie","log_watch","search_tmdb","add_movie"}`.
  - `test_build_server_writes_false_lists_six_tools`: same set minus `mcp_server.WRITE_TOOLS`.
  - `test_build_server_is_independent`: after `build_server(writes=False)`, `mcp_server.mcp` still lists nine tools.
- [ ] **Step 2: Run** `python -m pytest tests/test_mcp_server.py -k build_server -v` → FAIL (`build_server` undefined).
- [ ] **Step 3: Implement** by wrapping the existing block in `def build_server(*, writes: bool = True)`; rename `@mcp.` to `@server.`; register `rate_movie`, `log_watch`, `add_movie` only under `if writes:`; keep resources and prompts registered always; `return server`. Then `mcp = build_server()`. Pure move plus rename: no body changes.
- [ ] **Step 4: Run** `python -m pytest tests/test_mcp_server.py -v` → all old and new tests PASS. Confirm `git diff -w --stat mcp_server.py` is small (renames only).
- [ ] **Step 5: Commit** `refactor(mcp): register tools via build_server()`.

### Task 2: `MatineeError` — readable tool errors

**Files:**
- Modify: `mcp_server.py` (`_api_request`, imports)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `class MatineeError(RuntimeError, ToolError)` where `ToolError` comes from `mcp.server.mcpserver.exceptions`, else `mcp.server.fastmcp.exceptions`, else `Exception`. `MatineeClient._api_request` raises only `MatineeError`.

- [ ] **Step 1: Write the failing tests** (fake the transport by monkeypatching `urllib.request.urlopen`, as `test_mcp_rest_reads_fallback` already does for requests):
  - `test_connection_refused_message`: `URLError(ConnectionRefusedError())` → `MatineeError` whose `str` is exactly `"Matinee is unreachable right now. Try again in a minute."`.
  - `test_timeout_message`: `socket.timeout` → same string. **(Review Focus 2)** Also one test that opens a real loopback listener that accepts and never replies and passes `MATINEE_URL` to it with `urlopen` timeout patched to 0.2 s: result is the same message within 2 s.
  - `test_http_401_message`: `HTTPError 401` → `"Matinee rejected the service's API key (HTTP 401). Check MATINEE_API_KEY on CT 210."`; same for 403 with `403`.
  - `test_http_500_html_body_hidden`: body `<!DOCTYPE HTML>…` → `"Matinee API error (500)"` and the string contains no `<`.
  - `test_http_500_json_body_kept`: body `{"error":"disk full"}` → `"Matinee API error (500): disk full"`.
  - `test_message_never_contains_url_or_key`: for every case above, neither `127.0.0.1` nor the test key appears in `str(exc)`.
  - `test_matinee_error_is_runtimeerror_and_toolerror`: `issubclass` of both (skip the ToolError half when it is plain `Exception`).
- [ ] **Step 2: Run** `python -m pytest tests/test_mcp_server.py -k "message or matinee_error" -v` → FAIL.
- [ ] **Step 3: Implement** `MatineeError` and rewrite the two `raise RuntimeError(...)` sites in `_api_request` per §6.3's table, truncating bodies to 200 characters and logging the URL and underlying exception with `logging.getLogger("matinee.mcp").warning` (no headers). `get_movie`'s "not found" error becomes `MatineeError` too.
- [ ] **Step 4: Run** the whole `tests/test_mcp_server.py` → PASS (existing `pytest.raises(RuntimeError)` checks still hold).
- [ ] **Step 5: Commit** `feat(mcp): MatineeError surfaces readable tool errors`.

### Task 3: REST `where_is` prefers an exact title

**Files:**
- Modify: `mcp_server.py:252-371` (`where_is`, REST branch only)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `MatineeClient.search_collection`, `get_movie`.
- Produces: unchanged `where_is(title: str) -> Dict[str, Any]` result shape.

- [ ] **Step 1: Write the failing tests** (monkeypatch `client._api_request` to serve `/admin/api/titles?…` and `/admin/api/titles/<id>`):
  - `test_rest_where_is_prefers_exact_title`: search returns `[Super 8 (id 5), Up (id 9), Upstream Color (id 11)]` for query `"Up"`; the result's `title` is `"Up"` (the request for details used id 9).
  - `test_rest_where_is_falls_back_to_shortest_title`: query `"Paddington"` with results `[Paddington in Peru, Paddington 2]` (no exact match) → picks `Paddington 2`, the shortest title.
  - `test_rest_where_is_requests_limit_50`: the captured query string contains `limit=50`.
  - `test_rest_where_is_encodes_non_ascii` **(Review Focus 1)**: `where_is("WALL·E")` and `where_is("Amélie")` produce a request path containing `WALL%C2%B7E` / `Am%C3%A9lie` and return `found: True`.
  - `test_rest_matches_sqlite_branch` extends the existing `test_mcp_direct_sqlite_matches_api` pattern: same chosen title for the *Up* fixture in both modes.
- [ ] **Step 2: Run** `-k where_is` → FAIL.
- [ ] **Step 3: Implement** in the REST branch: `limit=50`, then pick the first result whose `title.strip().lower() == title.strip().lower()`, else the result with the shortest title; unchanged output building.
- [ ] **Step 4: Run** `tests/test_mcp_server.py` → PASS.
- [ ] **Step 5: Commit** `fix(mcp): REST where_is prefers exact title match`.

### Task 4: Vendor `remote.py` as `mcp_remote.py`

**Files:**
- Create: `mcp_remote.py`
- Create: `tests/test_mcp_http.py` (first test only; Task 5 adds the harness)

**Interfaces:**
- Produces: module `mcp_remote` exposing the upstream names used later: `HttpConfig`, `AccessPolicy`, `AccessVerifier`, `ConfigError`, `add_http_arguments`, `http_config`, `build_app`, `serve_http`.

- [ ] **Step 1: Write the failing test** `test_mcp_remote_copy_is_unmodified`: read `mcp_remote.py`, drop the leading `#` header lines, assert `hashlib.sha256(rest.encode()).hexdigest() == "cb14c336003cf48afe1aee5bead964adf2c8ca3c23627eb166d07ef3a4dc6c84"`, and assert the first line names commit `3ccb5f1e1139abb708c113f6663e64eb953cb702`. Add `pytest.importorskip("mcp.server.mcpserver")` at module top.
- [ ] **Step 2: Run** `python -m pytest tests/test_mcp_http.py -v` → FAIL (file missing).
- [ ] **Step 3: Create `mcp_remote.py`**: the three header lines from §6.1 followed by upstream `src/mcp_shared/remote.py` byte for byte (take it from `https://raw.githubusercontent.com/SDNick484/mcp-server-shared/3ccb5f1e1139abb708c113f6663e64eb953cb702/src/mcp_shared/remote.py` or any sibling's `remote.py`; `sha256sum` it first and stop if it is not `cb14c336…6dc84`).
- [ ] **Step 4: Run** the test → PASS.
- [ ] **Step 5: Commit** `feat(mcp): vendor mcp-server-shared remote.py as mcp_remote.py`.

### Task 5: `mcp_http.py` — app with Access auth and read-only default

**Files:**
- Create: `mcp_http.py`
- Test: `tests/test_mcp_http.py`

**Interfaces:**
- Consumes: `mcp_server.build_server`, `mcp_remote.build_app`, `AccessVerifier`.
- Produces: `readonly(env: Mapping[str, str]) -> bool`; `build_http_app(env: Mapping[str, str], verifier: AccessVerifier | None = None) -> ASGIApp` (reads `MCP_HTTP_*`, `CF_ACCESS_*`, `MCP_ALLOWED_EMAILS`, `MATINEE_MCP_READONLY`; `verifier` injection is for tests).

- [ ] **Step 1: Write the harness** in `tests/test_mcp_http.py`: constants `KEY = "test-matinee-api-key-0123456789"`, `AUD = "aud-tag-matinee"`, `TEAM = "nick.cloudflareaccess.com"`, `EMAIL = "nick@example.com"`, `HOST = "mcp.nickandrade.com"`; fixtures `signing_key` (RSA-2048), `token(**claims)` (RS256, header `kid=k1`, claims `iss=https://{TEAM}`, `aud=[AUD]`, `iat`, `exp=+300`, `email`), `fake_matinee` (a `ThreadingHTTPServer` on `127.0.0.1:0` that records every request and answers `/healthz`, `/admin/api/titles?…` and `/admin/api/titles/214` for *Paddington 2* only when `Authorization == f"Bearer {KEY}"`), `env` (dict with the §5 variables, bind `127.0.0.1`, `MCP_PUBLIC_HOSTS=HOST`, `MATINEE_URL` at the fake), `client` (Starlette `TestClient(app, base_url=f"http://{HOST}")` used as a context manager so the lifespan runs; fixture also does `monkeypatch.setattr(mcp_server, "_default_client", MatineeClient(api_url=fake_url, api_key=KEY))`), and `rpc(client, method, params=None, *, token=None, sid=None, path="/matinee/mcp", host=None)` that posts JSON-RPC with `Accept: application/json, text/event-stream` and `Cf-Access-Jwt-Assertion` when `token` is set. Parse SSE bodies by reading the `data:` line.
- [ ] **Step 2: Write the failing tests**:
  - `test_readonly_parsing`: parametrized: unset, `""`, `"1"`, `"true"`, `"garbage"`, `" yes "` ⇒ `True`; `"0"`, `"false"`, `"No"`, `" off "` ⇒ `False`.
  - `test_reject_matrix` parametrized over R1–R6 (§7.2): no header; `not-a-jwt`; wrong `aud`; expired; signed by another key; email `other@example.com` — each asserts `status_code == 403` and `response.json() == {"error": "forbidden"}`.
  - `test_reject_missing_email_claim` **(Review Focus 3)**: valid token minus `email` with `MCP_ALLOWED_EMAILS=nick@example.com` ⇒ 403.
  - `test_rejected_requests_never_reach_a_tool`: wrap `mcp_server._default_client.where_is` in a spy; after the whole reject matrix the spy has zero calls and `fake_matinee.requests == []`.
  - `test_accepts_valid_token_and_where_is`: `initialize` → 200 with a `mcp-session-id` header; `notifications/initialized` → 202; `tools/call where_is {"title":"Paddington 2"}` → `isError` false and `structuredContent.result.summary == "Paddington 2 (2017): Blu-ray at Family Room › Media Cabinet › Shelf 2"`; the fake saw `Authorization: Bearer <KEY>` on its calls.
  - `test_default_is_read_only`: with no `MATINEE_MCP_READONLY`, `tools/list` names equal the six read tools; `rate_movie` is absent.
  - `test_writes_enabled_lists_nine`: `MATINEE_MCP_READONLY=0` → nine tools.
  - `test_read_only_call_to_write_tool_is_unknown`: `tools/call rate_movie` → `isError` true, text `"Unknown tool: rate_movie"`, and the fake saw no `PUT`.
- [ ] **Step 3: Run** `python -m pytest tests/test_mcp_http.py -v` → FAIL (no `mcp_http`).
- [ ] **Step 4: Implement** `mcp_http.py` per §6.5: `readonly()` as in Global Constraints; `build_http_app` builds `HttpConfig` via `mcp_remote.http_config` using an argparse namespace from `mcp_remote.add_http_arguments(default_port=8719, default_path="/matinee/mcp")` with empty argv, applies `CF_ACCESS_TEAM_DOMAIN ← CF_TEAM_DOMAIN` fallback, builds `mcp_server.build_server(writes=not readonly(env))`, and returns `mcp_remote.build_app(server, cfg, verifier, health_check=...)`. Use `health_check=None` for now.
- [ ] **Step 5: Run** the tests → PASS. Also `python -m pytest -q` for the whole suite.
- [ ] **Step 6: Commit** `feat(mcp): add mcp_http.py with read-only default` (tests included).

### Task 6: Path prefix, Host, health, unreachable, secrets, sessions

**Files:**
- Modify: `mcp_http.py` (add `matinee_healthy`, wire the health check)
- Test: `tests/test_mcp_http.py`

**Interfaces:**
- Produces: `matinee_healthy(base_url: str) -> bool` (blocking `GET {base_url}/healthz`, 2 s timeout, `True` only on HTTP 200); `build_http_app` passes an async wrapper (via `anyio.to_thread.run_sync`) as `health_check`.

- [ ] **Step 1: Write the failing tests**:
  - `test_health_ok_without_auth`: `GET /healthz` → 200 `{"status": "ok"}`.
  - `test_health_503_when_matinee_down`: stop the fake → 503 `{"status": "degraded", "error": "health check returned False"}`.
  - `test_path_prefix`: `POST /matinee/mcp` works; `POST /mcp` → 404 `Not Found`; `GET /matinee/healthz` → 404; with `MCP_HTTP_PATH=/x/mcp` the endpoint moves and `/matinee/mcp` is 404.
  - `test_wrong_host_is_421`: valid token, `Host: evil.example.com` → 421 body `Invalid Host header`.
  - `test_matinee_unreachable_tool_message`: fake stopped → `tools/call where_is` → `isError` true and text `"Error executing tool where_is: Matinee is unreachable right now. Try again in a minute."`.
  - `test_matinee_401_and_500_messages`: fake switched to answer 401, then 500 HTML → texts per §6.3, none containing `<` or the key.
  - `test_stale_session_id_is_404` **(Review Focus 4)**: valid token, `Mcp-Session-Id: deadbeef…` → 404, spy shows no tool call.
  - `test_secrets_never_logged`: `caplog.set_level(DEBUG)` across a reject, an accept and an error path; the joined log text and every response body contain neither `KEY`, the token string, nor `Bearer`.
- [ ] **Step 2: Run** → the health, unreachable-message and secrets tests FAIL where `health_check` is missing; the rest should already PASS (they pin shared-module behavior).
- [ ] **Step 3: Implement** `matinee_healthy` and the wrapper; nothing else changes.
- [ ] **Step 4: Run** `tests/test_mcp_http.py` → PASS.
- [ ] **Step 5: Commit** `test(mcp): HTTP path, host, health, unreachable and secrets`.

### Task 7: `main()` and startup refusals

**Files:**
- Modify: `mcp_http.py`
- Test: `tests/test_mcp_http.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int` — returns 2 with a one-line stderr message on refusal; otherwise sets logging (`INFO`, to stderr, `%(asctime)s %(levelname)s %(message)s`) and calls `mcp_remote.serve_http`. Before importing `mcp_server` it exports `MATINEE_DB_PATH=""` and the derived `MATINEE_URL` into `os.environ`.

- [ ] **Step 1: Write the failing tests** (patch `mcp_remote.serve_http` to a recorder):
  - `test_main_refuses_empty_api_key`: returns 2; stderr mentions `MATINEE_API_KEY`; `serve_http` not called.
  - `test_main_refuses_public_bind_without_access`: `MCP_HTTP_BIND=192.168.1.174`, no `CF_ACCESS_*` → returns 2; stderr names `Cloudflare Access`.
  - `test_main_refuses_public_host_without_access`: `MCP_PUBLIC_HOSTS` set, no Access → 2.
  - `test_main_forces_db_path_empty`: env has `MATINEE_DB_PATH=/var/lib/matinee/matinee.db` → `os.environ["MATINEE_DB_PATH"] == ""` and the client has no `db_path`.
  - `test_main_derives_matinee_url_from_port`: `PORT=8771`, no `MATINEE_URL` → `http://127.0.0.1:8771`.
  - `test_main_serves_with_configured_values`: a full env → `serve_http` called once with `cfg.port == 8719`, `cfg.path == "/matinee/mcp"`, and a server whose tool list is the six read tools.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `main()` plus `if __name__ == "__main__": raise SystemExit(main())`.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(mcp): mcp_http main with startup refusals`.

### Task 8: Installer — `matinee-mcp` service, venv, settings, perms

**Files:**
- Modify: `installer/setup-head.sh`, `installer/build.sh`
- Create: `requirements-mcp.txt`, `tests/test_installer_static.py`

**Interfaces:**
- Consumes: `mcp_http.py` path `/opt/matinee/mcp_http.py` and the settings in §5.
- Produces: init script `/etc/init.d/matinee-mcp` (§9.1 verbatim), user/group `matinee-mcp`, venv `/opt/matinee-mcp/venv`, log `/var/log/matinee-mcp.log`.

- [ ] **Step 1: Write the failing tests** in `tests/test_installer_static.py` (read `installer/setup-head.sh` and `installer/build.sh` as text, in the style of `tests/test_static_ui.py`):
  - the init script heredoc contains `name="matinee-mcp"`, `need net matinee`, `command_user="matinee-mcp:matinee-mcp"`, `/opt/matinee-mcp/venv/bin/python`, `/opt/matinee/mcp_http.py`, and its `export` line does **not** contain `TMDB_READ_TOKEN` or `OMDB_API_KEY`.
  - `add_setting` lines exist for `MATINEE_MCP_READONLY` with value `1`, `MCP_HTTP_PORT` `8719`, `MCP_HTTP_PATH` `/matinee/mcp`, `MCP_PUBLIC_HOSTS`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `MCP_ALLOWED_EMAILS`.
  - the config permissions are `chmod 600` and `chown root:root` (and the old `chmod 640` line is gone).
  - the pip step is guarded (an `if`/`||` branch that prints a warning and does not `exit`) **(Review Focus 5)**.
  - `rc-service matinee-mcp start` appears only inside a condition on `CF_ACCESS_AUD`, `MCP_PUBLIC_HOSTS` and `MATINEE_API_KEY`.
  - the health-check line `Matinee MCP|http://$IP:8719/healthz|matinee-mcp` and `--uninstall` handling of `matinee-mcp` exist.
  - `build.sh` copies `mcp_http.py`, `mcp_remote.py` and `requirements-mcp.txt`.
  - `requirements-mcp.txt` contains `mcp>=2.3,<3`, `uvicorn>=0.31`, `pyjwt[crypto]>=2.10`.
  - behavior test: run the `add_setting` function body in a temp `sh` against a config that already has `MATINEE_MCP_READONLY=0`; afterwards the value is still `0` and the key appears once **(Review Focus 5)**.
- [ ] **Step 2: Run** `python -m pytest tests/test_installer_static.py -v` → FAIL.
- [ ] **Step 3: Implement** the ten installer changes in §9.2 and the `build.sh` list; `requirements-mcp.txt` with the three pins. Bind default is the already-computed `$IP`.
- [ ] **Step 4: Run** the new tests, then `sh installer/build.sh && sh -n dist/setup-matinee.sh` → PASS and "Built dist/setup-matinee.sh".
- [ ] **Step 5: Commit** `feat(installer): matinee-mcp OpenRC service and venv`.

### Task 9: Docs

**Files:**
- Modify: `.env.example`, `docs/mcp-setup.md`, `README.md`

- [ ] **Step 1: Write the failing check** in `tests/test_installer_static.py`: `test_env_example_documents_new_settings` asserts `.env.example` contains each §5 key with a comment line directly above it, and `test_mcp_setup_mentions_remote` asserts `docs/mcp-setup.md` contains `https://mcp.nickandrade.com/matinee/mcp`, `^/matinee/`, `http://192.168.1.174:8719`, `MATINEE_MCP_READONLY` and no remaining `:8769`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Write** the `.env.example` block from §5; add section "Remote access from the Claude app" to `docs/mcp-setup.md` (architecture, settings table, tunnel route, Access note, connector steps from §8, turning on write tools, rollback commands from §13) and replace the three `:8769` examples with `:8771`; one-line pointer in `README.md`. Mark the Android connector steps as **unverified until Task 10**.
- [ ] **Step 4: Run** the tests → PASS.
- [ ] **Step 5: Commit** `docs: remote MCP setup, correct 8769 → 8771 in mcp-setup`.

### Task 10: CI

**Files:**
- Modify: `requirements-dev.txt`, `.github/workflows/ci.yml`

- [ ] **Step 1:** Add `uvicorn>=0.31`, `pyjwt[crypto]>=2.10`, `anyio`, and the Starlette test-client dependency (`httpx`; if Starlette in the resolved environment asks for `httpx2`, use that) to `requirements-dev.txt`; add the same `pip install` to the Alpine job next to the existing `mcp` install.
- [ ] **Step 2: Verify locally** in a fresh venv: `pip install -r requirements-dev.txt && python -m pytest -q` → all pass.
- [ ] **Step 3: Commit** `ci: HTTP MCP dependencies`.
- [ ] **Step 4: Push** the branch and open a draft PR (`gh pr create --draft`); wait for Python 3.10, 3.11, 3.12, 3.13, Alpine and `node -c` to finish. Expected: all green; if `mcp` 2.x cannot install on 3.10, the HTTP tests must show as skipped, not failed. If a job fails, fix it here and push again.

### Task 11: Build, deploy to CT 210, tunnel route, connector, smoke test

Manual steps run with Nick; the agent prepares each command and records results into `docs/mcp-setup.md`.

- [ ] **Step 1: Build:** `sh installer/build.sh` → `dist/setup-matinee.sh`. Copy to CT 210: `scp dist/setup-matinee.sh root@192.168.1.174:/tmp/`.
- [ ] **Step 2: Pre-flight on CT 210:** `ss -tlnp | grep ':8719'` must print nothing. Back up: `/etc/periodic/daily/matinee-backup`.
- [ ] **Step 3: Install:** `sh /tmp/setup-matinee.sh`. Expected: `matinee` still healthy (`wget -qO- http://127.0.0.1:8771/healthz`), `/etc/conf.d/matinee` is `-rw-------` `root root`, new keys appended, banner says `matinee-mcp` was **not** started and lists the missing keys.
- [ ] **Step 4: Configure:** `vim /etc/conf.d/matinee`: set `MCP_PUBLIC_HOSTS=mcp.nickandrade.com`, `CF_ACCESS_AUD=<AUD tag of the mcp.nickandrade.com Access app>`, `MCP_ALLOWED_EMAILS=<your Access email>`; confirm `MATINEE_API_KEY` is set and `MCP_HTTP_BIND=192.168.1.174`. Then `rc-service matinee-mcp start`; `wget -qO- http://192.168.1.174:8719/healthz` → `{"status": "ok"}`. Direct origin rejection: `wget -S -O- --post-data='{}' http://192.168.1.174:8719/matinee/mcp` → `403`.
- [ ] **Step 5: Cloudflare:** add the tunnel route (hostname `mcp.nickandrade.com`, path `^/matinee/`, service `http://192.168.1.174:8719`, Host header override unset). Confirm no Access app or Bypass policy is more specific for `/matinee`.
- [ ] **Step 6: Unauthenticated public check** from any machine: the `curl` in spec §7.2 → expect `HTTP 401` with `www-authenticate`. Record the real output in `docs/mcp-setup.md`.
- [ ] **Step 7: Connector:** Claude → Customize > Connectors > Add custom connector, URL `https://mcp.nickandrade.com/matinee/mcp`, Authentication **Sign in now**; sign in through Access. Try Android directly first; if the phone can't add it, add it on claude.ai or Desktop and confirm it appears on the phone. Record which worked.
- [ ] **Step 8: Smoke test:** Pixel 9, Wi-Fi off, ask "Where is Paddington 2?" → the shelf path. Then ask to rate it → the assistant must say it has no rating tool (read-only). Then `MATINEE_MCP_READONLY=0`, `rc-service matinee-mcp restart`, retry on the phone (check session re-init), rate it, and restore the original rating in `/admin`; set the flag back to `1` and restart.
- [ ] **Step 9: Checks from spec §17:** `rc-service matinee restart` and whether `matinee-mcp` follows; `Matinee MCP` appears in `HEALTH_CHECKS`; log `/var/log/matinee-mcp.log` has `Denied` lines but no token, key or `Bearer`.
- [ ] **Step 10: Rollback drill:** `rc-service matinee-mcp stop` → phone reports the connector unavailable; `start` → recovers.
- [ ] **Step 11: Wrap up:** update `docs/mcp-setup.md` with what was verified (remove "unverified" markers), update the Project's `app-overview.md` port to 8771 and mention `matinee-mcp`, commit `docs: record remote MCP deployment results`, push, run `superpowers:requesting-code-review` on the whole branch, then mark the PR ready.
