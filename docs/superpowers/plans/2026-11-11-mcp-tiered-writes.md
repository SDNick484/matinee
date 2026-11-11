# Tiered remote MCP writes — implementation plan

**Spec:** `docs/superpowers/specs/2026-11-11-mcp-tiered-writes-design.md` (source of truth; if the code disagrees,
stop and ask).
**Branch:** `feat/mcp-tiered-writes`. One Conventional Commit per task, author
`SDNick484 <SDNick484@users.noreply.github.com>`. `python -m pytest -q` green after every task (baseline 583).

Scenario used throughout: *Paddington 2* (id 214 in the test fake), `log_watch` on 2026-11-10.

### Task 1 — Annotations on all nine tools

- [ ] Tests first (`tests/test_mcp_server.py`): each of the nine tools has `readOnlyHint`, `destructiveHint`,
      `idempotentHint`, `openWorldHint` equal to the spec §5 table (skip when `ToolAnnotations` is missing);
      `_annotation_kwargs(None)` and a class without `annotations` give `{}`; `_readonly_kwargs` keeps working.
- [ ] Run: fail (only `get_recommendations` has hints).
- [ ] `mcp_server.py`: `TOOL_HINTS` table, `_annotation_kwargs(server_cls, name)`, `build_server` passes it for
      every tool; `_readonly_kwargs` stays as a thin wrapper.
- [ ] Green. Commit `feat(mcp): readOnly/destructive/idempotent annotations on all nine tools`.

### Task 2 — `build_server()` takes a write level

- [ ] Tests first: `build_server(writes="none"|"log"|"all")` list 6 / 8 / 9 tools; `True`/`False` still give
      9 / 6; `"ALL"`, `""`, `None`, `2` raise `ValueError`; `instructions_for(level)` per spec §6; for every
      level, every tool name in the instructions and in each registered prompt's rendered text is registered;
      `log_movie_night` registered at `log` and `all` only; `mcp_server.mcp` still lists nine.
- [ ] Run: fail.
- [ ] `mcp_server.py`: `WRITE_LEVELS`, `LOG_TOOLS`, `instructions_for()`, level handling in `build_server`.
- [ ] Green. Commit `feat(mcp): build_server() accepts a write level (none, log, all)`.

### Task 3 — `MATINEE_MCP_WRITES` in the remote service

- [ ] Tests first (`tests/test_mcp_http.py`): `write_level()` on every row of spec §3 (including whitespace and
      case); `readonly()` cases unchanged; warnings text for the two warning rows; `service_key()` per level;
      `tools/list` over HTTP at `none`, `log`, `all`; at `log`, `add_movie` → `Unknown tool: add_movie` and no
      `POST` reaches the fake; the scenario (`where_is` then `log_watch` 214 / 2026-11-10 succeeds, fake saw
      `POST /admin/api/titles/214/watches`, body `{"watched_date": "2026-11-10"}`, `Bearer` write key); `rate_movie`
      succeeds at `log`; `main()` at `log` needs `MATINEE_API_KEY` (exit 2 naming it when empty), refuses an inherited
      read-key-only environment, builds eight tools, and prints the conflict warning for `READONLY=1` +
      `WRITES=log` while serving six tools with the read key.
- [ ] Run: fail.
- [ ] `mcp_http.py`: `_resolve()`, `write_level()`, `write_level_warnings()`, `readonly()` in terms of them,
      `build_server(writes=level)` in `build_http_app` and `main()`, startup log line and warnings, module docstring.
      Extend `FakeMatinee` with `POST …/watches` and a body recorder.
- [ ] Green. Commit `feat(mcp-http): MATINEE_MCP_WRITES none|log|all for the remote service`.

### Task 4 — Installer and `.env.example`

- [ ] Tests first (`tests/test_installer_static.py`): `MATINEE_MCP_WRITES` added with a derived default (`all` when
      the existing `READONLY` is off-like, else `none`; a value already in the file is kept); `.env.example`
      documents it with a comment line above; the init script's `export` line has it; the init script and the
      installer define the same `mcp_level` function; the function gives the spec §3 level for every row (run in
      `sh`); the init script exports exactly one key per level (`none` → read key only, `log`/`all` → API key
      only, an inherited key of the other kind is unset); `mcp_key_name` follows the level; the final banner
      prints the level.
- [ ] Run: fail.
- [ ] `installer/setup-head.sh`, `.env.example`.
- [ ] Green, plus `sh installer/build.sh && sh -n dist/setup-matinee.sh`.
- [ ] Commit `feat(installer): MATINEE_MCP_WRITES setting and per-level key selection`.

### Task 5 — Docs

- [ ] Tests first: `docs/mcp-setup.md` mentions `MATINEE_MCP_WRITES`, all three levels and the `READONLY=0`
      requirement; the §1.1 key table says `MATINEE_API_KEY` is used at `log` and `all`; `tests/test_hygiene.py`
      stays green.
- [ ] `docs/mcp-setup.md` (§1.1, §4.1, §4.4, §4.5, §5.2), README pointer sentence.
- [ ] Green. Commit `docs: turning on write tools by level (MATINEE_MCP_WRITES)`.

### Task 6 — Verify, push, PR, CI

- [ ] `python -m pytest -q`, `node -c static/app.js`, `sh installer/build.sh && sh -n dist/setup-matinee.sh`.
- [ ] Push `feat/mcp-tiered-writes`; open the PR against `main` (do not merge); poll
      `gh api repos/SDNick484/matinee/commits/<head-sha>/check-runs` until every job completes; fix and re-poll on
      failure.
- [ ] Manual, on the host after merge (Nick): `vim /etc/conf.d/matinee` → `MATINEE_MCP_READONLY=0`,
      `MATINEE_MCP_WRITES=log`, a non-empty `MATINEE_API_KEY`; `rc-service matinee-mcp restart`; from the phone,
      "log that we watched Paddington 2 tonight, 8.5"; confirm `add_movie` is not offered; then
      `sqlite3 /var/lib/matinee/matinee.db "SELECT watched_date FROM watches WHERE title_id=214 ORDER BY id DESC LIMIT 1"`.
