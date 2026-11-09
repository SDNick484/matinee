# Spec reconciliation: remote MCP (Spec A) × AI search / Ask (Spec B)

**Date:** 2026-11-08 · **Reviewed:** `main` @ `f7904df64b801347c27f5a45a80b4c443c0caa0c` (merge of PR #15, 2026-11-07 10:02 PST)
**Inputs:** `docs/superpowers/specs/2026-11-06-remote-mcp-design.md` + `docs/superpowers/plans/2026-11-06-remote-mcp.md` (Spec A); `docs/superpowers/specs/2026-11-06-ai-search-design.md` + `docs/superpowers/plans/2026-11-06-ai-search.md` (Spec B). All four are in the repo.
**Method:** read-only. `python -m pytest -q` on main: **431 passed** (Python 3.13.18, mcp 2.3.0). Nothing committed; this file is untracked.

> Every finding is labelled **Verified** (file:line on `f7904df`), **Likely**, or **Can't verify from the repo** (needs CT 210).

---

## 1. Summary

**The premise of the review is stale: both specs are already implemented and merged.** Spec A landed as PR #13 (`feat/remote-mcp`, merged 969551d, 2026-11-06), Spec B as PR #14 (`feat/ai-search`, merged e468a1b, 2026-11-07), followed by PR #15 (`fix/cf-team-domain`, 4cd5ab5 + bbcc4bd). Neither feature branch exists on the remote any more; `main` is 34 commits past the specs' baseline `572d53d`. There is no merge order left to recommend, so §4 below records what the merge actually did and where the second branch (`feat/ai-search`) had to rebase.

| Spec | Verdict as a *spec* | Verdict as *shipped code* |
|---|---|---|
| **A — remote MCP** | Drift claims were accurate at `572d53d` (one miscount: 24 vs 25 tests). Several statements are now false on `main` (status line, installer writes `CF_ACCESS_TEAM_DOMAIN`, "editor-only data" framing). | **Go, already live-able.** No startup breakage found; the `chmod 600` lead is refuted. Two hygiene items and one security framing need a follow-up PR. |
| **B — Ask** | Drift claims verified. Status line stale. | **Go.** The DB-mode `where_is` parity fix in §7 was only half-done (hyphen/punctuation variants still miss in Ask but hit in remote MCP). |

**Blockers: none.** Nothing found that crashes `matinee`, `matinee-mcp`, the installer, or CI when both features are deployed together. Eight fix-grade items (hygiene, parity, test coverage, stale docs) and six notes follow.

---

## 2. Drift claims checked against `main`

| Claim (spec) | Code on `f7904df` | Status |
|---|---|---|
| A §3.1: 8769 is the code default, 8771 is production | `app.py:1960` (`PORT`, 8769), `mcp_server.py:101` (`MATINEE_URL` 8769), `installer/setup-head.sh:7,17,34,142,164`, `.env.example:5-6`, `README.md:73,92,102`. `docs/mcp-setup.md` now uses 8771 (`:66,82`). Unchanged defaults; re-defaulting still deferred. **New wrinkle:** `mcp_http.py:30` falls back to `PORT` default **8771** while `mcp_server.py:101` and the init script (`setup-head.sh:164`) default **8769**. Harmless on CT 210 (init script exports `MATINEE_URL` explicitly) but the two defaults disagree. | Verified |
| A §3: "24 tests today" in `tests/test_mcp_server.py` | 25 `def test_` at `572d53d`; 41 on `main`. Spec B said 25 — B was right. | Verified |
| B §3: `_load_env()` reads `/etc/conf.d/matinee` and `./.env` | `app.py:34-55`, called at import (`:55`). Both `.env.example:1-4` and `README.md:4` still say the app does not read a `.env` file. | Verified — docs wrong, code right |
| B §3: `SCHEMA_VERSION` 7 → 8 | `app.py:163` is `"8"`; `ask_log` DDL at `app.py:323-334` inside `migrate()` only. | Verified |
| A/B: `where_is` differs between modes | DB branch `mcp_server.py:328-393` (exact `lower(title)` match, else shortest `search_text LIKE %q%`); REST branch `:395-443` (fetch all matches, exact title else shortest). Both return `"id"` (`:386`, `:438`). The REST side now mirrors the DB side's *selection*, but the DB side's *matching* still differs from the grid: see F3. | Verified |
| B §3: DB-mode `search_collection` matched the whole query as one substring | Fixed: per-word `search_text LIKE ? ESCAPE '\'` at `mcp_server.py:199-201`, `_words()` at `:37`. | Verified |
| B §3: DB-mode `get_movie` copies lacked `location_path` | Fixed at `mcp_server.py:302-306`. | Verified |
| A §5: installer writes `CF_ACCESS_TEAM_DOMAIN` | Removed by PR #15 (`4cd5ab5`); `mcp_http.py:44-46` falls back to `CF_TEAM_DOMAIN`, `.env.example:47-48` documents it. Spec A's settings table is stale here. | Verified |
| A §9.2.6: nothing reads `/etc/conf.d/matinee` as the `matinee` user | `_load_env()` does (as gunicorn's user), but see F1 — it fails silently. `ops/matinee-refresh:8-11` sources it as root (cron). | Verified |
| B §3: container IDs still in CI job name and docs | `.github/workflows/ci.yml:43` "(CT 210 parity)"; see F2 for the full list. | Verified |

---

## 3. Findings

Severity: **blocker** = would break prod/CI as deployed · **fix** = fix before the next deploy or in the next PR · **note** = record and move on.

| # | Sev | Spec | Finding | Evidence | Proposed fix |
|---|---|---|---|---|---|
| F1 | note (lead refuted) | A×B | **`chmod 600 root:root` does not crash startup.** `_load_env()` wraps the read in `except Exception: pass`, so the `PermissionError` is swallowed and the process relies solely on the init script's `export` line. That line currently exports all 13 names the app reads. The 640 fallback that used to mask a missing export is gone, and the test guarding the export line only checks `MATINEE_API_KEY`. | `app.py:34-55`; `installer/setup-head.sh:104-105,145`; `tests/test_app.py:91-97`; app env names at `app.py:57-68`, `ask.py:24-26` | Strengthen `test_setup_head_exports_all_env_vars` to assert every `os.environ.get("…")` name in `app.py` + `ask.py` appears on the export line (hunk H1). Verify on CT 210 (§5 C3). | Verified |
| F2 | fix | A vs B | **Hygiene conflict shipped.** Spec B's deferred goal is to remove container IDs, LAN IPs and personal hostnames; Spec A added them: `192.168.1.174` and `mcp.nickandrade.com` in `.env.example`, docs, installer comment; `CT 210` in a model-facing error string, CI job name and tests. Worse, `test_mcp_setup_mentions_remote` **asserts** the personal IP and hostname are present in `docs/mcp-setup.md`, so a Cookbook-style `test_hygiene.py` would fail CI until that test is rewritten. | `.env.example:42,46-47`; `docs/mcp-setup.md:115-116,132,134,148-150,157,164`; `installer/setup-head.sh:101-102`; `mcp_server.py:165`; `tests/test_installer_static.py:144-149`; `tests/test_mcp_http.py:39,359,421`; `tests/test_mcp_server.py:447-449`; `.github/workflows/ci.yml:43` | One `docs:`/`fix(mcp):` PR: placeholders (`192.168.1.100`, `mcp.example.com`, "the Matinee host") everywhere; rewrite the test to assert placeholders (H2, H3); then add `test_hygiene.py`. | Verified |
| F3 | fix | B | **DB-mode `where_is` (what Ask uses) still misses punctuation/hyphen variants that REST mode (remote MCP) finds.** Fallback is `search_text LIKE '%'+title.lower()+'%'` with no folding, no per-word split, no `ESCAPE`. `search_text` for *WALL·E* is `'wall e | walle'`, so `where_is("Wall-E")` → exact fails, `%wall-e%` fails → "not currently in the collection"; the REST branch goes through `list_titles` → `words("Wall-E")` = `["wall","e"]` → found. Same for "where's the dark knight 4k" (both miss, but DB also misses "dark knight the"). Spec B §7 only applied the fold-and-split to `search_collection`. | `mcp_server.py:339-342` vs `app.py:359-365,1099-1103`; fixture `tests/fixtures/tmdb/movie-10681.json` (WALL·E) | Reuse `_words()`/`_like_escape()` in the `where_is` fallback (H4); add `test_where_is_db_hyphen_variant` with *WALL·E*. | Verified (reproduced with `app.search_fold`) |
| F4 | fix | A, B | **Both specs still read "DRAFT … No implementation has started."** The repo copies and the project-doc copies are identical drafts; nobody reading them can tell they shipped as PR #13/#14 or that PR #15 changed §5 of Spec A. | spec A `:5-6`; spec B `:5-6`; `git log -- docs/superpowers/specs/2026-11-06-*` shows no post-merge edit | Add a status line with PR numbers and a "post-merge deltas" list (H5, H6). Update `app-overview.md` in the project (still says schema v5, port 8769, no `matinee-mcp`, "does NOT read a .env file"). | Verified |
| F5 | fix | A | **Security framing in Spec A §14 and `docs/mcp-setup.md` is wrong in a way that matters for decisions.** "REST mode uses `/admin/api`, so editor-only data is visible" — but ratings and watch notes are **public** when `PUBLIC_VIEW=1`: `title_detail()` returns `user_rating` and `watches[].notes` regardless of `private`, and the only private copy field is `added_by`. Confirmed: `GET /api/titles/<id>` returned `user_rating: 9.5` and `notes: 'girls loved it'` anonymously. Consequence: the read-only remote service gains nothing from `MATINEE_API_KEY` except `search_tmdb` (`/admin/api/tmdb/search`), yet the key is write-capable and sits in the `matinee-mcp` environment. | `app.py:1011,1014,869,961-965,1342-1354`; `mcp_server.py:137,559-562`; Spec A §14 row 2; ratings spec `2026-11-01-ratings-and-watch-history-design.md:273` | Short term: correct the wording (H7). Medium term (Spec A §16 already defers a read-only key): in read-only mode construct `MatineeClient(api_key="")` so reads go to `/api`, and either drop `search_tmdb` from the remote read set or make the key the only thing it unlocks. | Verified |
| F6 | note | B | **Ask sends public data, not secrets, to Anthropic.** Per F5 the ratings/notes Spec B §12 flags as "personal" are already world-readable at `/api/titles/<id>`. The README wording is fine; just don't over-claim exposure. The real Ask-specific exposure is the *question text*, which is not stored (verified: `ask.py:108-112` logs no text). | `ask.py:108-112,320-370`; `README.md:157` | None required. | Verified |
| F7 | note (lead refuted) | A×B | **`MatineeError` text cannot reach Ask's model.** `run_tool` passes `RuntimeError` text through (`ask.py:243-244`) and `MatineeError` is a `RuntimeError`, but the only `MatineeError` with the "Check MATINEE_API_KEY on CT 210" text is raised in `_api_request`, which `LocalClient` overrides to raise `AskUnavailable` (ends the request with 502). DB-mode `get_movie`'s `MatineeError("Movie with id 99999 not found.")` *does* reach the model, as intended. The coupling is only via the override — if someone ever adds a REST call outside `_api_request`, the CT 210 string leaks. F2 removes the string anyway. | `ask.py:119-125,243-244`; `mcp_server.py:93-98,165,283` | Covered by F2. | Verified (exercised without `mcp` installed) |
| F8 | note (lead refuted) | B | **`mcp_server` imports cleanly without `mcp`; `build_server()` returns `None`.** `FastMCP = None` → `_readonly_kwargs(None)` → `{}`; `ToolError = Exception`; `MatineeError(RuntimeError, Exception)` MRO is fine; `ask` registers `/admin/api/ask`. | `mcp_server.py:23-29,67-80,82-88,591-598,753-760`; reproduced by blocking `import mcp` | None. | Verified |
| F9 | note (lead refuted) | A×B | **Spec A changed no `MatineeClient` signature**, so `test_schemas_match_client_signatures` is unaffected. AST diff of all nine public methods between `572d53d` and `f7904df`: identical. | `tests/test_ask.py:210-214`; `mcp_server.py:182-191,276,328,445-454,500,541,545-551,559,565-570` | None. | Verified |
| F10 | note | A | **Read-only remote server still advertises write tools in prose.** `SERVER_INSTRUCTIONS` ("After watching: call log_watch, then rate_movie") and the `log_movie_night` prompt are registered regardless of `writes=False`; the model gets "Unknown tool: log_watch" when it follows them. | `mcp_server.py:582-588,743-750,770-773` | Pass a read-only `instructions` variant and skip `log_movie_night` when `writes=False` (H8). | Verified |
| F11 | note | A | **Default `MCP_ALLOWED_EMAILS=""` + a shared Access app.** The Access app covers all of `mcp.nickandrade.com` (harmony, webos, …), so with the empty default anyone that policy admits can read the collection. Spec A §5 says "set it to Nick's Access email"; the installer leaves it empty and only the final banner would tell you. | `installer/setup-head.sh:103`; `mcp_remote.py:201,284`; Spec A §5 | Have the installer's "NOT started"/"running" banner nag when `MCP_ALLOWED_EMAILS` is empty (H9). Verify on CT 210 (§5 C7). | Verified (installer) / Can't verify (Access policy) |
| F12 | note | A | **`rc-service matinee restart` vs `need matinee`.** OpenRC stops dependents on `stop`; whether `restart` brings `matinee-mcp` back is not provable from the repo. Spec A §17.2 already lists it. Each tool call opens a fresh loopback request, so a stale-but-running `matinee-mcp` is fine; a *stopped* one is not. | `installer/setup-head.sh:178` | §5 C5. | Can't verify from the repo |
| F13 | note | A×B | **Two unrelated 60-second budgets.** gunicorn `--timeout 60` (gthread) and Ask `BUDGET_S = 60` + `CALL_TIMEOUT_S = 25`. gthread workers heartbeat from the main loop, so a 60 s Ask should not kill the worker, but it was not measured. | `installer/setup-head.sh:151`; `ask.py:29-30,327` | §5 C8. | Likely OK / Can't verify |
| F14 | note | A | **`.env.example` was only half-cleaned by `ea6804d`** ("drop the container ID"): the ID went, the LAN IP and hostname stayed (F2). | `.env.example:42,46`; commit `ea6804d` | Covered by F2. | Verified |

Rules check (apk-only, no build step, `migrate()`-only schema, no live network in tests, CI matrix, OpenRC): all hold on `main`. The pip venv is confined to `matinee-mcp` (`setup-head.sh:183-186`); `ask.py` uses `urllib` only; `ask_log` DDL is in `migrate()`; `grep -n urlopen tests/test_ask.py` shows only monkeypatches; CI runs 3.10–3.13 + Alpine + `node -c`.

---

## 4. What the merge actually did (in place of a merge-order recommendation)

Order was **A then B**: PR #13 merged 2026-11-06; `feat/ai-search` was branched after it (its first commit `712f7e0` sits on top of `ea6804d`) and took one back-merge (`247dcbd`) before PR #14. The rebase points Spec B §7 predicted were real and small:

| File | Spec A (PR #13) | Spec B (PR #14) | Interaction on `main` |
|---|---|---|---|
| `mcp_server.py` | `build_server()` refactor (`55ca70f`), `MatineeError` (`3d47948`), REST `where_is` exact-match (`1a06fbe`, `979fb5e`) | `981d142`: `_words`, `_like_escape`, `_location_paths`, DB `get_movie` paths, `"id"` in both `where_is` branches | Clean: B's `"id"` line landed inside A's rewritten REST branch (`:438`). Tool functions are module-level and read `_default_client` at call time (`:756`), which is what lets `mcp_http.main` swap the client (`mcp_http.py:88`) and lets `ask.LocalClient` subclass `MatineeClient` without touching registration. |
| `app.py` | untouched | `SCHEMA_VERSION="8"`, `ask_log`, `_card`/`title_cards`, `import ask` at the bottom, `me()` → `ask` flag | No overlap. |
| `installer/setup-head.sh` | `matinee-mcp` user/venv/init script, 8 `add_setting`s, `chmod 600`, health check, banner | 3 `add_setting`s (`:93-95`), 3 names on the `export` line (`:145`) | Adjacent lines, merged cleanly. PR #15 then removed A's `CF_ACCESS_TEAM_DOMAIN` `add_setting`. |
| `installer/build.sh` | `mcp_http.py mcp_remote.py requirements-mcp.txt` | `ask.py` | Same `cp` line (`:9`). |
| `.env.example` | §5 block (`:36-50`) | 3 lines (`:30-35`) | Adjacent. |
| `docs/mcp-setup.md` | §4 remote access; 8769→8771 | one paragraph (`:39`) | No overlap. |
| `requirements-dev.txt` / CI | `uvicorn pyjwt[crypto] anyio httpx`; Alpine pip line | none | — |

Spec A's *third* interaction — Ask's `LocalClient._api_request` override vs `MatineeError` — is F7: it works, by construction.

---

## 5. Items to check on CT 210

| # | Check | Command | Expect |
|---|---|---|---|
| C1 | Schema and both services up | `sqlite3 /var/lib/matinee/matinee.db "SELECT v FROM meta WHERE k='schema'"; rc-service matinee status; rc-service matinee-mcp status; ss -tlnp \| grep -E ':(8771\|8719) '` | `8`; both `started`; gunicorn on 8771, python on `192.168.1.174:8719` |
| C2 | Config file mode after the installer | `ls -l /etc/conf.d/matinee` | `-rw------- 1 root root` |
| C3 | gunicorn's env has every key despite the 600 file (F1) | `for p in $(pidof gunicorn); do tr '\0' '\n' < /proc/$p/environ \| grep -E '^(PORT\|DATA_DIR\|CF_TEAM_DOMAIN\|CF_AUD\|ADMIN_EMAILS\|LAN_EDIT\|PUBLIC_VIEW\|TMDB_READ_TOKEN\|MATINEE_API_KEY\|OMDB_API_KEY\|ANTHROPIC_SEARCH_KEY\|ANTHROPIC_SEARCH_MODEL\|ASK_DAILY_LIMIT)=' \| cut -d= -f1 \| sort -u \| wc -l; done` | `13` for each worker |
| C4 | `_load_env()` really is blind (no `.env` leak, no crash) | `grep -c 'PermissionError\|Traceback' /var/log/matinee.log; ls /opt/matinee/.env 2>&1` | `0`; "No such file" |
| C5 | Does restarting matinee take matinee-mcp down? (F12) | `rc-service matinee restart; sleep 3; rc-service matinee-mcp status; wget -qO- http://192.168.1.174:8719/healthz` | `started` and `{"status": "ok"}`; if `stopped`, add `rc-service matinee-mcp start` to the docs' restart lines |
| C6 | Public endpoint refuses anonymous calls | `curl -si -X POST https://mcp.nickandrade.com/matinee/mcp -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \| head -5` | `HTTP/2 401` + `www-authenticate` |
| C7 | Who can use the MCP endpoint (F11) | `grep -E '^(MCP_ALLOWED_EMAILS\|MATINEE_MCP_READONLY)=' /etc/conf.d/matinee` | `MCP_ALLOWED_EMAILS=<your Access email>`, `MATINEE_MCP_READONLY=1` |
| C8 | Ask within gunicorn's timeout (F13) | On the LAN: `time curl -s -X POST -H 'content-type: application/json' -d '{"thread":[{"q":"which of our 4K discs have we never rated?"}]}' http://127.0.0.1:8771/admin/api/ask \| head -c 300; grep -c 'WORKER TIMEOUT' /var/log/matinee.log` | JSON answer in < 60 s; `0` |
| C9 | Ask cross-site gate and api-key refusal | `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://evil.example.com' -H 'content-type: application/json' -d '{"thread":[{"q":"x"}]}' http://127.0.0.1:8771/admin/api/ask; curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $(sed -n 's/^MATINEE_API_KEY=//p' /etc/conf.d/matinee)" -H 'content-type: application/json' -d '{"thread":[{"q":"x"}]}' http://127.0.0.1:8771/admin/api/ask` | `403` and `403` |
| C10 | Ask logs no question text | `sqlite3 /var/lib/matinee/matinee.db "SELECT status, rounds, input_tokens, output_tokens FROM ask_log ORDER BY id DESC LIMIT 3"; sqlite3 /var/lib/matinee/matinee.db ".schema ask_log" \| grep -ci question` | `ok` rows; `0` |
| C11 | Public data exposure baseline (F5) | `curl -s http://127.0.0.1:8771/api/titles/$(sqlite3 /var/lib/matinee/matinee.db "SELECT id FROM titles WHERE title='The Dark Knight'") \| python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["user_rating"], [w.get("notes") for w in d["watches"]])'` | Prints the rating and notes anonymously (confirms they are public by design, so decide whether `PUBLIC_VIEW=0` or hiding notes from `/api` is wanted) |
| C12 | Android connector | Add `https://mcp.nickandrade.com/matinee/mcp` from the phone; ask "Where is Paddington 2?" | Spec A §17.3; update `docs/mcp-setup.md:157` to what worked |

---

## 6. Proposed spec / code edits (before → after)

**H1 — `tests/test_app.py:91-97`** (F1): make the export-line test cover every variable the app reads.
```diff
-    exported_vars = set(export_match.group(0).split()[1:])
-    assert "MATINEE_API_KEY" in exported_vars
+    exported_vars = set(export_match.group(0).split()[1:])
+    read = set()
+    for f in ("app.py", "ask.py"):
+        read |= set(re.findall(r'os\.environ(?:\.get)?\(?\[?"([A-Z_]+)"', (Path(matinee.APP_DIR) / f).read_text()))
+    assert read <= exported_vars, read - exported_vars   # the conf file is root-only; the export line is the only path in
```

**H2 — `.env.example:41-47`** (F2):
```diff
-# Address matinee-mcp listens on: the host's LAN address, because cloudflared runs in a container (127.0.0.1 would 502)
-MCP_HTTP_BIND=192.168.1.174
+# Address matinee-mcp listens on: the host's LAN address, because cloudflared runs in a container (127.0.0.1 would 502)
+MCP_HTTP_BIND=192.168.1.100
 ...
-MCP_PUBLIC_HOSTS=mcp.nickandrade.com
-# AUD tag of the Access app that covers mcp.nickandrade.com (this is NOT CF_AUD, the /admin app's tag);
+MCP_PUBLIC_HOSTS=mcp.example.com
+# AUD tag of the Access app that covers mcp.example.com (this is NOT CF_AUD, the /admin app's tag);
```
Same substitution in `docs/mcp-setup.md:115-116,132,134,148-150,157,164`, `installer/setup-head.sh:101-102` (comment text), `.github/workflows/ci.yml:43` ("Alpine Linux Container (production parity)"), and the tests that pin the strings (`tests/test_mcp_http.py:39`, `:421`; `tests/test_mcp_server.py:447-449`).

**H3 — `mcp_server.py:165` + `tests/test_installer_static.py:144-149`** (F2/F7):
```diff
-                    f"Matinee rejected the service's API key (HTTP {e.code}). Check MATINEE_API_KEY on CT 210."
+                    f"Matinee rejected the service's API key (HTTP {e.code}). Check MATINEE_API_KEY on the Matinee host."
```
```diff
-    for needle in ("https://mcp.nickandrade.com/matinee/mcp", "^/matinee/", "http://192.168.1.174:8719",
+    for needle in ("https://mcp.example.com/matinee/mcp", "^/matinee/", "http://192.168.1.100:8719",
                    "MATINEE_MCP_READONLY", "rc-service matinee-mcp restart"):
         assert needle in text, needle
-    assert ":8769" not in text
+    assert ":8769" not in text
+    assert not re.search(r"192\.168\.1\.174|nickandrade|CT ?210", text)
```

**H4 — `mcp_server.py:339-342`** (F3): fold and split the `where_is` fallback like the grid does.
```diff
-                if not row:
-                    row = con.execute(
-                        "SELECT id, title, year FROM titles WHERE search_text LIKE ? AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=titles.id) ORDER BY length(title) ASC LIMIT 1",
-                        (f"%{q_clean}%",),
-                    ).fetchone()
+                if not row:
+                    ws = _words(title)
+                    clauses = " AND ".join("search_text LIKE ? ESCAPE '\\'" for _ in ws) or "1"
+                    row = con.execute(
+                        f"SELECT id, title, year FROM titles WHERE {clauses} AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=titles.id) ORDER BY length(title) ASC LIMIT 1",
+                        [f"%{_like_escape(w)}%" for w in ws],
+                    ).fetchone()
```
Test: seed 10681, assert `MatineeClient(db_path=…).where_is("Wall-E")["title"] == "WALL·E"` and that the DB and REST answers match (extend `test_mcp_direct_sqlite_matches_api`).

**H5 — `docs/superpowers/specs/2026-11-06-remote-mcp-design.md:5-6`** (F4):
```diff
-**Status:** DRAFT — awaiting Nick's approval. No implementation has started.
-**Verified against:** `matinee` `main` @ `572d53d`, …
+**Status:** IMPLEMENTED — PR #13 (merged 969551d, 2026-11-06); PR #15 (4cd5ab5) removed the installer's `CF_ACCESS_TEAM_DOMAIN` key (§5: now derived from `CF_TEAM_DOMAIN`).
+**Verified against:** `matinee` `main` @ `572d53d` at design time, …
+**Post-merge deltas:** §5 `CF_ACCESS_TEAM_DOMAIN` row; §14 row 2 (ratings and watch notes are public, see 2026-11-08 reconciliation); §3 "24 tests" was 25.
```

**H6 — `docs/superpowers/specs/2026-11-06-ai-search-design.md:5`** (F4):
```diff
-**Status:** DRAFT, waiting for Nick's review. No implementation has started.
+**Status:** IMPLEMENTED — PR #14 (merged e468a1b, 2026-11-07). Open follow-up: §7 fold/split not applied to `where_is`'s DB fallback (reconciliation F3).
```

**H7 — Spec A §14, row "A stolen Access session"** (F5):
```diff
-| A stolen Access session | … Read-only by default: titles, shelves, your ratings and watch notes (REST mode uses `/admin/api`, so editor-only data is visible to the tool). |
+| A stolen Access session | … Read-only by default: titles, shelves, ratings and watch notes — the same data `GET /api/titles/<id>` serves anonymously when `PUBLIC_VIEW=1` (`title_detail()` does not gate them; only copies' `added_by` is editor-only). The key adds nothing to the read tools except `search_tmdb`. |
```
Same correction in `docs/mcp-setup.md` §4.1 (`MATINEE_API_KEY` row) and in Spec B §12 "what is sent to Anthropic" (say "ratings and watch notes, which are also public at `/api` when `PUBLIC_VIEW=1`").

**H8 — `mcp_server.py:753-774`** (F10):
```diff
 def build_server(*, writes: bool = True):
-    server = _make_server()
+    server = _make_server(SERVER_INSTRUCTIONS if writes else READONLY_INSTRUCTIONS)
     ...
-    server.prompt("log_movie_night")(prompt_log_movie_night)
+    if writes:
+        server.prompt("log_movie_night")(prompt_log_movie_night)
```
with `READONLY_INSTRUCTIONS` = `SERVER_INSTRUCTIONS` minus the "After watching: call log_watch…" sentence; `_make_server(instructions)` takes the string.

**H9 — `installer/setup-head.sh:240`** (F11):
```diff
-  up) echo "Matinee MCP is running (read-only unless MATINEE_MCP_READONLY=0): http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT)/healthz" ;;
+  up) echo "Matinee MCP is running (read-only unless MATINEE_MCP_READONLY=0): http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT)/healthz"
+      [ -n "$(conf_val MCP_ALLOWED_EMAILS)" ] || echo "  - MCP_ALLOWED_EMAILS is empty: anyone the Access policy admits can read the collection. Set it in $CONF and rc-service matinee-mcp restart" ;;
```

**H10 — `.env.example:1` and `README.md:4`** (drift):
```diff
-# Matinee settings. Documentation only: the app does not read a .env file.
+# Matinee settings. In production they live in /etc/conf.d/matinee; the app also reads a .env next to app.py
+# for local development (keys already in the environment win).
```

---

## 7. Not done here (by instruction)

No code, spec or project-doc edits; nothing committed. Project docs `claude/2026-11-06-*.md` are byte-identical copies of the draft specs and `app-overview.md` predates both features — both are candidates for H5/H6-style updates once you approve the wording.
