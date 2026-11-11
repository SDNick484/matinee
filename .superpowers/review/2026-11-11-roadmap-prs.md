# Review: roadmap PRs #18–#29 (018ec06 → 3423d08)

**Date:** 2026-11-11 · **Reviewed:** `main` @ `3423d0896c7ce54061c8f3941ec2cd6e2ced25aa` (merge of PR #29), against `018ec06` (merge of PR #17)
**Branch:** `fix/roadmap-review` (review committed as `fc882ce` before any code change; fixes F1–F7 follow, one commit each) · **Method:** phase 1 read-only; code read, suites run, behaviour probed with the Flask test client, a worktree of `98f5ef7` and headless Chromium. PR descriptions, spec status lines and the SDD ledger's "review clean" lines were not taken on trust.
**Baseline:** `python -m pytest -q` → **1043 passed, 13 skipped** (Python 3.13.18, Playwright Chromium present; the 13 skips are `tests/test_restore_test.py`, no `sqlite3` CLI in this sandbox). CI on `3423d08`: all seven check runs green.

> Every finding is labelled **Verified** (file:line, or a command and its output), **Likely**, or **Can't verify from the repo** (needs the host).

---

## Decisions for Nick

Phase 1 left these alone: each needed a product call or changes who can use what. **Nick decided all four on
2026-11-11 (14:19 PT): D1 refuse (option b), D2 yes (b), D3 refuse (c), D4 delete.** Outcome per row in the last
column.

| # | Decision | Options | Recommendation | Outcome |
|---|---|---|---|---|
| D1 (F10) | **The read key can spend third-party quota.** `MATINEE_READ_KEY` passes `editor_required` for any GET, so it can call `/admin/api/upc/<upc>` (UPCitemdb trial: 100 lookups/day, and it writes an `upc_cache` row) and `/admin/api/tmdb/search` (TMDB; no daily quota, rate-limited). `matinee-mcp` at level `none` registers `search_tmdb`, which uses exactly that. | (a) keep as is; (b) refuse the read key on `upc/<upc>` only (no MCP tool uses it); (c) refuse it on both and drop `search_tmdb` from level `none`. | (b): the phone keeps "do we own Blade Runner 2049 / what else is out there", and the one route that costs a scarce quota and writes a row stays editor-only. | **Decided (b): `5b0b094`** — the read key gets 403 `The read key can't look up barcodes.`; TMDB search unchanged. |
| D2 (F22) | **A watch logged with an explicit date more than ten years back** (date picker, `2012-06-01`) still defaults to everyone. R21 as decided names the "10+ Years" bucket only; the read-time rule for old unrecorded watches covers both the bucket *and* old dates. | (a) bucket only (as implemented here); (b) also default to adults for an explicit date before today − 10 years. | (b) is a one-line change in `add_watch` and the page's default; say the word and it is the same pattern. | **Decided (b): `3e3d919`** — `default_attendees` uses `recommend.attendance_of`; the date field switches the kids off for an old date. |
| D3 (F8) | **Side-effecting GETs.** `upc/<upc>` writes a cache row and spends quota on a GET, so it is reachable by a cross-site GET from a browser that sends neither `Origin` nor `Sec-Fetch-Site` (Safari < 16.4) and by a same-site page (another app on `*.example.com`). | (a) leave it; (b) make the UPC lookup a POST; (c) require `Sec-Fetch-Site` to be `same-origin` or `none` on that route. | (c), smallest change; (b) is cleaner but changes the API the SPA and any script use. | **Decided (c): `7248aa7`** — browser identities need `Sec-Fetch-Site: same-origin` or `none`, else 403 `Barcode lookups must come from the Matinee page.`; `MATINEE_API_KEY` scripts exempt. |
| D4 (F18) | **Two fully merged remote branches still exist** (`feat/filters-stats-polish`, `feat/historical-watches-and-ui-polish`, both ahead 0 of `main`). Deleting a remote branch is not something this branch should do on its own. | `git push origin --delete feat/filters-stats-polish feat/historical-watches-and-ui-polish` | Delete them. | **Decided: delete.** Not done from this session: its git proxy refused the remote deletes (only the session branch may be pushed). Run the command in the Options column. |

---

## 1. Summary

**No blockers.** No auth or privacy path lets an anonymous or cross-site caller read notes, people or attendance, or write anything. Seven fix-grade findings (one flaky CI gate, one UX bug from #29, one privacy-test gap, four docs) plus Nick's ruling R21; fourteen notes.

| PR | Intent | Verdict |
|---|---|---|
| #18 `fix/lan-cross-site` | Cross-site refusal on every editor route; "lan" only for IP/localhost/`LAN_HOSTS`; security headers | **Go.** Every editor route, including #29's, refuses `Origin` mismatch, `Origin: null` and `Sec-Fetch-Site: cross-site` (F8 table). Host parsing holds up (§2.2). Two notes (F8, F9). |
| #19 `chore/repo-housekeeping` | `CLAUDE.md`, OpenSpec commands removed, port 8771 | **Go with a fix.** 8769 survives only in exempt design history; installer keeps an existing `PORT=`. `CLAUDE.md`'s Definition of done is stale (F6). |
| #20 `ci/hardening` (+ #21, #22) | ui-smoke that cannot skip, shellcheck, ruff, concurrency, Dependabot | **Go with a fix.** The skip guard works, but the gate it protects is flaky: 4 of 40 runs fail (F1). Dependabot majors run green inside the Alpine container. |
| #23 `feat/privacy-read-key` | Notes editor-only; `MATINEE_READ_KEY` GET-only; `matinee-mcp` at `none` holds only the read key | **Go.** Notes never reach `/api` (Verified across every public route, F4 adds the guard to keep it so). Decision D1 on quota. Notes F11. |
| #24 `feat/csv-export` | `GET /admin/api/export.csv`, `ops/export_csv.py` | **Go.** Columns, BOM, CRLF, RFC 4180 quoting, formula prefixes, `no-store` all match the spec and are tested. |
| #25 `feat/ops-refresh-restore` | Nightly TMDB refresh, restore runbook and weekly test, optional logrotate | **Go with notes.** Refresh keeps custom covers and certification overrides; restore test is weekly and notifies. Runs as root (F12); counts only titles and copies (F13). |
| #26 `feat/mcp-tiered-writes` | `MATINEE_MCP_WRITES` none/log/all, annotations | **Go with a docs fix.** Code, shell twin and spec agree; `docs/mcp-setup.md` says an unset `READONLY` keeps the service read-only, which is false (F5). |
| #27 `refactor/js-modules` | Pure move into ES modules, import-map versioning | **Go.** After stripping import/export lines, comments and the IIFE wrapper, `static/app.js` @ `9923979` and the modules @ `b2f0fe3` are line-for-line identical (§2.6). One note (F14). |
| #28/#29 people and attendance (v9) | People, attendance, audiences; dormant until the first person | **Go with fixes.** Migration v1–v8 → v9 is lossless and idempotent; with no people every public response is byte-identical to `98f5ef7` and editor responses only gain keys (F20). R21 implemented (F2); People modal close bug (F3); one definition of "seen" everywhere (§2.13). |

Counts: **blocker 0 · fix 7 (+ R21) · note 14**, plus decisions D1–D3 implemented after Nick's answers.

---

## 2. Leads checked

### 2.1 Cross-site check (#18, lead 1)

`editor_required` (`app.py:586-598`) calls `cross_site()` (`app.py:576-583`) for every identity except the two key identities (`KEY_IDENTITIES`, `app.py:508`). Exemptions:

| Identity | Exempt? | Justified? |
|---|---|---|
| `api-key`, `read-key` | yes | Yes. A key travels in `Authorization` or `X-API-Key`; a browser cannot attach either cross-site without a CORS preflight, and Matinee sends no CORS headers. |
| Access email (`Cf-Access-Jwt-Assertion`) | no | Correct: Cloudflare adds the header from a cookie, so a cross-site request can carry it. |
| `lan` | no | Correct: ambient, like a cookie. |

Probe with the "lan" identity (`Host: 192.168.1.100:8771`, test client, network stubbed to fail):

| Route | cross-site GET (`Sec-Fetch-Site: cross-site`) | form POST (`Origin: https://evil.example.com`) | `Origin: null` | no headers | `Sec-Fetch-Site: same-site` |
|---|---|---|---|---|---|
| `GET /admin/api/tmdb/search?q=alien` | 403 | 403 | 403 | 503 (passes; TMDB off) | 503 (passes) |
| `GET /admin/api/upc/883929637581` | 403 | 403 | 403 | 502 (passes; reached UPCitemdb) | 502 (passes) |
| `GET /admin/api/export.csv` | 403 | 403 | 403 | 200 | 200 |
| `GET /admin/api/people` (#29) | 403 | 403 | 403 | 200 | 200 |
| `POST /admin/api/people` (#29) | 403 | 403 | 403 | 400 (validation) | 400 |

State-changing or quota-spending GETs under `/admin/api`: `tmdb/search` (TMDB call), `upc/<upc>` (UPCitemdb call **and** an `INSERT OR REPLACE INTO upc_cache`, `app.py:1769,1774`), and the `export.csv` / people / title reads (no side effects, and a cross-site page cannot read the body without CORS). #29 added only `GET /admin/api/people` as a GET; its writes are POST/PUT/DELETE. A request with neither header passes by design: that is curl on the LAN, or an old browser — see F8 / D3. **Verified.**

### 2.2 `lan_host()` parsing (#18, lead 2)

`app.py:552-573`, through Werkzeug's `request.host` validation. `LAN_HOSTS={"matinee.lan"}`:

| Host header | `request.host` | lan? |
|---|---|---|
| `192.168.1.100.` / `[::1]:8771` / `[::1]` / `[::ffff:192.168.1.5]` | as sent | **yes** |
| `LOCALHOST` / `localhost.` / `MATINEE.LAN:8771` / `matinee.lan.:8771` | as sent | **yes** |
| `1.2.3.4.evil.com` / `0x7f.1` / `127.1` / `2130706433` | as sent | no |
| `::1` (unbracketed) / `[fe80::1%25eth0]:8771` / `[::1]evil.com` / `[::1].evil.com` | `''` (Werkzeug rejects) | no |
| `user@192.168.1.100` / `192.168.1.100@evil.com` / `evil.com:80@1.2.3.4` | `''` | no |
| `192.168.1.100:8771:1` / `localhost:abc` / empty | `''` | no |

A request with **no** Host header falls back to `SERVER_NAME`; under gunicorn bound to `0.0.0.0` that is `0.0.0.0`, an IP literal, so it gets "lan" (F9). **Verified** (except the gunicorn fallback: **Likely**).

### 2.3 Who can see notes, people and attendance (#23, #29, lead 3)

| Response | Notes | People / attendance | Who sees it |
|---|---|---|---|
| `GET /api/titles/<id>` | stripped (`PRIVATE_WATCH`, `app.py:1001,1115-1120`) | none | anyone (PUBLIC_VIEW=1) |
| `GET /admin/api/titles/<id>` | yes | `attendance`, `attendees` (ids), `seen_by` (ids) | editors, read key |
| grid `/api/titles`, `/admin/api/titles` | never selected (`CARD_SELECT`) | `unseen_by` / `attendance` filters: editor only; public 400 | as stated |
| `/api/stats`, `/api/facets`, `/api/locations` | counts only | none | anyone |
| `/api/recommendations`, all six modes | none; reason text uses household watches | `audience` → public 400 (`people_params_refused`, `app.py:1251-1255`); with an audience the editor reason names people ("New to Midge and Alice") and results carry `seen_by` names | editors, read key |
| `GET /admin/api/export.csv` | not exported (tested) | none | editors, read key |
| Ask (`/admin/api/ask`) | tool results carry notes and names to Anthropic (README discloses it) | yes | editors only (key identities refused, `ask.py:396-397`) |
| Error messages | — | `Unknown person: 99. People: Nick, Lauren, …` on `/admin/api` only; public gets `Sign in to pick for specific people.` | as stated |
| MCP REST, no key (`/api`) | none | none (server 400) | — |
| MCP REST, read or API key | yes | names (client maps ids → names) | the MCP caller |
| MCP DB mode / Ask's LocalClient | yes | names | the MCP caller |

`tests/test_privacy.py`'s AST guard walks only functions in `app.py` (`_public_handlers`, `:143`); the calls public routes make into `recommend.py` are not followed, and the runtime check (`:63`) is a fixed list of eight paths with no people seeded and only two of six recommendation modes. A future public route is not covered automatically. → **F4**. **Verified.**

### 2.4 MCP levels and keys (#23 × #26, lead 4)

| `READONLY` | `WRITES` | Level (`mcp_http._resolve`, `:33-49`) | Init script exports | Missing key |
|---|---|---|---|---|
| `1` (installer default) | any | none | `MATINEE_READ_KEY` only, `unset MATINEE_API_KEY` (`setup-head.sh:204-207`) | exit 2 (`mcp_http.py:main`) |
| `0` | unset | all | `MATINEE_API_KEY` only | exit 2 |
| `0` | `log` / `all` / `none` | log / all / none | API key at log/all, read key at none | exit 2 |
| unset / empty | `log` | **log** | API key | exit 2 |

At `none` the service also refuses to start when `MATINEE_API_KEY` is present anyway (`test_main_refuses_write_key_in_read_only_environment`). The shell twin `mcp_level` (`setup-head.sh:80-93`, `:188-201`) matches `_resolve` row for row, and the spec's table (§3 of the tiered-writes spec) agrees. `.env.example:42-46` is consistent. `docs/mcp-setup.md:147` is not: "(including unset and empty) keeps the service read-only" — the last row above says otherwise → **F5**. **Verified** (process environment on the host: §4 H4).

### 2.5 Read key and quota (#23, lead 5)

Verified: read key → `GET /admin/api/tmdb/search` and `GET /admin/api/upc/<upc>` pass `editor_required` (`app.py:592-595` refuses only non-GET/HEAD). Not stated as intended in the privacy spec, which only says the key "can only read". → **D1**.

### 2.6 JS modules (#27, lead 6)

```sh
git show 9923979:static/app.js > old.js        # just before f9367be
for m in core icons router grid detail add forms stats about ask movie-night locations main; do
  git show b2f0fe3:static/js/$m.js; done > new.js
# strip import {…} from "…"; export {…}; // and /** */ comments; the IIFE wrapper and "use strict"; trim each line
diff old.n new.n          # → no output; both 3236 non-blank lines
```

No exported `let` is reassigned across modules (live bindings are read-only), no inline handler calls a former IIFE-scope function (the six inline handlers only use `location`/`this`). **Verified: pure move.**

Caching: `render_index()` puts every module in an import map as `/static/js/<m>.js?v=<sha256[:10]>` (`app.py:643-656`); the page is `no-store`. `GET /static/js/main.js?v=…` → `public, max-age=31536000`; `GET /static/js/core.js` (no `v`) → `no-cache, max-age=0` with an ETag (`app.py:690-700`). Changing a module changes its hash, so only its URL changes; a cached old `main.js?v=X` imports `./core.js`, which the *current* page's map resolves to the new hash, so a browser cannot assemble a mixed set. Any `?v=` value, even a wrong one, gets the year-long header (F14). **Verified.**

### 2.7 CI (#20, #29, lead 7)

`test_modules_load_versioned_once_and_every_view_renders`, 40 runs: **4 failed** (runs 9, 12, 22 and 35), each a 30 s timeout. Cause: `renderStats` writes a `.stats-view` "Loading…" placeholder, then awaits `/admin/api/stats` (`static/js/stats.js:7-16`); the test sees the placeholder, sets `#/about`, `renderAbout` draws `.about-view`, and the late stats response then overwrites the view. `handleRoute` (`static/js/router.js`) has no route token. Reproduced deterministically by holding the stats response until after `.about-view` appears: the view ends up as stats. Same race for the grid and title view on a slow phone (tap a title, go back before it loads). → **F1** (fix).

Skips in a plain checkout and where each test runs:

| Tests | Skip reason locally | Runs in CI |
|---|---|---|
| `tests/test_restore_test.py` (13) | no `sqlite3` CLI | Python 3.10–3.13 (Ubuntu runners ship `sqlite3`) and Alpine (`apk add sqlite`). CI log, Python 3.13 job on `3423d08`: `1033 passed, 2 skipped`, restore tests PASSED. |
| `tests/test_ask_ui.py`, `tests/test_people_ui.py` (module `importorskip`) | no Playwright | ui-smoke only; the job fails on any skip. The matrix's 2 skips are these two modules. |

Nothing runs nowhere. Dependabot majors (`actions/checkout@v7`, `actions/setup-python@v7`) inside `alpine:latest`: Alpine job green on `3423d08`. **Verified.**

### 2.8 Ops (#25, lead 8)

- Refresh: `--limit 25 --delay 1.0` (`ops/matinee-refresh:20`, `ops/refresh_titles.py`) → ≤ 25 TMDB detail calls plus images per night; TMDB's limit is per-second. A 429 stops the run, rows untouched. **Verified** (tests).
- Custom covers and certification overrides survive (`TITLE_COLS` excludes them; `test_custom_cover_and_certification_override_survive`). **Verified.**
- `matinee-restore-test` installed to `/etc/periodic/weekly` (`setup-head.sh`), notifies through `ops-notify`/`ct210-notify` on failure, logs only when neither exists. **Verified**; that crond runs `weekly`: **Can't verify** (§4 H9).
- Logrotate: written only when `logrotate` is installed and no config already names either log. **Verified** (`test_logrotate_skipped_when_another_config_names_either_log`).
- `.backup` copies every table, so v9's `people` and `watch_people` are in the nightly file; the restore test counts only `titles` and `copies` (F13). The cron jobs run as root (F12).

### 2.9 CSV export (#24, lead 9)

`EXPORT_COLUMNS` (`app.py:1518`) = spec §2 list, same order. BOM `\xef\xbb\xbf`, `csv.writer` default (CRLF, minimal quoting, `""`), `Cache-Control: no-store`, `Content-Disposition: attachment; filename="matinee-YYYY-MM-DD.csv"`. Formula lead `= + - @ \t \r` gets `'`; numbers pass through. **Verified** (`tests/test_export.py`, 20 tests).

### 2.10 Housekeeping (#19, lead 10)

`grep -rn 8769` outside `openspec/`: only `.superpowers/review/2026-11-08-*`, `docs/superpowers/plans/2026-11-06-*`, `docs/superpowers/specs/2026-11-0[16]-*` (exempt design history). Installer: `[ -f "$CONF" ] && PORT=$(sed -n 's/^PORT=//p' …)` (`setup-head.sh:33`) keeps an existing port. `CLAUDE.md`'s Definition of done lists five CI jobs (seven exist), no ruff, no shellcheck, nothing on people/attendance or `tests/test_privacy.py` → **F6**. **Verified.**

### 2.11 Docs drift (lead 11)

- `.env.example` ↔ `add_setting`: every key matches except `PORT` and `DATA_DIR`, which the installer writes in the heredoc that creates the file (by design). **Verified.**
- `.env.example` ↔ init `export` lines: the app's line exports all 15 app settings; `matinee-mcp`'s exports all MCP settings plus `CF_ACCESS_TEAM_DOMAIN`, which `.env.example` and `add_setting` deliberately omit since PR #15 (`mcp_http._http_config` falls back to `CF_TEAM_DOMAIN`) — F19. **Verified.**
- `docs/mcp-setup.md:147` — F5. Spec status lines — F7. README and `docs/vision.md` describe people, the editor-only boundary and the old-watch rule correctly; README did not yet say what a new "10+ Years" log records (updated with R21).

### 2.12 Migration and dormancy (#29, lead 12)

`tests/test_people.py::test_migration_from_every_earlier_version[1..8]` builds each historical schema, migrates twice via `init_db()` and `migrate()`, and compares every pre-existing row; `test_migrate_twice_on_v9_keeps_people_and_attendance` covers the second run on v9. Both pass. Dormancy: one seeded database (Dark Knight, Princess Bride with a `10+ Years` dateless watch and a note, Chamber of Secrets 2014, Toy Story ×2, a digital-only title) served by a `98f5ef7` worktree and by HEAD over 34 GETs (every public route, every recommendation mode on both prefixes, title details, stats, export): **public responses identical**; `/admin/api/titles/<id>` gains `seen_by: []` and per-watch `attendance` / `attendees: []`, nothing else (F20). **Verified.**

### 2.13 One definition of "seen" (lead 13)

`recommend.SEEN_CTE` (`recommend.py:90-100`) and its Python twin `attendance_of` (`:124-133`) are the only definitions. Consumers: grid `unseen_by` (`app.py:1365-1376`), recommender (`recommend.py`), editor detail (`watch_attendance`, `title_seen_by`), MCP DB mode (`mcp_server.py:_db_attendance`, `search_collection`), MCP REST (server), Ask (`LocalClient` = DB mode). The grid's `watched=` filter and `forgotten_gems` without an audience use household watches on purpose. Probed with people seeded: Princess Bride's `10+ Years` watch → `assumed_adults`, `seen_by [1, 2]`; a 2014 watch → `assumed_adults`; Paige deactivated → `audience=kids` = Midge, Alice; `audience=5` (inactive Paige) → 200; `audience=99` and `unseen_by=99` → 400 `Unknown person: 99. People: …`. A person with recorded watches cannot be deleted (`delete_person` 400). **Verified.**

### 2.14 Parked items in the ledger (lead 14)

| Ledger line | Fix now? |
|---|---|
| People modal close re-runs `handleRoute` for any route (Add form and scanner reset) | **Yes → F3.** Only the grid and the title view show people. |
| Numeric MCP REST audience ids not checked against `/people` | No: the server validates and answers 400 either way (F16). |
| Close while a People change is in flight skips the refresh | No: next close refreshes; rare. |
| R21 | **Decided by Nick → F2.** |
| T1 check-active-room race; non-object JSON 500 in people routes | No: one household; same pattern as older routes. |
| T2 ids inlined via `_in_ids` | No: `int()`-coerced from the people table. |
| R8 / UTC vs local date for the ten-year line | No (F17). |
| T7 `loadPeople` failure silent after a change | No: cosmetic. |
| T9 §7.2 desktop layout untested | No. |

### 2.15 MCP levels, people and logs (lead 15)

`log_watch` (and so `attendees`) is registered at `log` and `all` only (`WRITE_LEVELS`, `mcp_server.py`). `list_people`, `audience` and `unseen_by` are GETs, so they work with the read key at `none` (`test_read_only_service_reads_notes_with_the_read_key`; `_people_rows` uses `_read_prefix`). Names in the access log: the SPA sends person ids (`grid.js:377`, `movie-night.js:44`); MCP REST swaps names for ids before building a query string (`_people_ids`); `log_watch` sends names in a POST body, which gunicorn does not log; MCP warnings log the path without the query (`mcp_server.py:_api_request`). **Verified.** Whether `/var/log/matinee.log` on the host holds a name: §4 H6.

### 2.16 `app.py` seams (lead 16)

2,396 lines. Proposed split, each a module that registers routes on the shared `app` the way `ask.py` does (no Blueprint renames, so `url_for` names and tests stay):

| Module | Moves | Approx. lines |
|---|---|---|
| `db.py` | `SCHEMA`, `SCHEMA_VERSION`, `connect`, `db`, `migrate`, `init_db` | 330 |
| `auth.py` | `presented_key`, `access_identity`, `lan_host`, `cross_site`, `editor_required`, `security_headers`, `who` | 140 |
| `tmdb.py` / `omdb.py` | `tmdb_*`, `title_from_tmdb`, images, `refresh_from_tmdb`; `omdb_get`, `write_omdb_ratings` | 330 |
| `browse.py` | `list_titles`, `CARD_SELECT`, cards, `facets`, `collection_stats`, `recommendations`, the `/api` ↔ `/admin/api` loop | 450 |
| `people.py` | people routes, `resolve_people`, `set_attendees`, `watch_response`, `add_watch`, attendees PUT | 300 |
| `export.py` | CSV export | 60 |
| `app.py` | Flask app, settings, pages/static, copies, locations, titles writes | ~800 |

`tests/test_privacy.py`'s AST guard reads `app.py` only; it must learn to walk the new modules in the same PR (F4's runtime guard does not depend on file layout).

### 2.17 Branches (lead 17)

`feat/filters-stats-polish` (141ea48, ahead 0 / behind 194) and `feat/historical-watches-and-ui-polish` (f9a882c, ahead 0 / behind 173) still exist → D4. **Verified** (`gh api repos/SDNick484/matinee/compare/main...<branch>`).

---

## 3. Findings

Severity: **blocker** = breaks prod/CI or an auth/privacy boundary · **fix** = fixed on this branch · **note** = recorded.

| # | Sev | PR | Finding | Evidence | Proposed fix | Fixed in |
|---|---|---|---|---|---|---|
| F1 | fix | #20, #27, #29 | **Route race makes the ui-smoke gate flaky (4/40) and can show the wrong view on a slow phone.** An async render that resolves after the user moved on overwrites `#view`. | §2.7; `static/js/stats.js:7-16`; `static/js/router.js:handleRoute`; deterministic reproduction (held `/admin/api/stats`) | A route sequence number in `state`; `renderStats`, `renderGrid` and `renderDetail` stop after each await when it changed. Regression test holds the stats response. | `eb9f968` |
| F2 | fix (R21) | #29 | **A "10+ Years" watch logged with default attendees records the kids**, unlike the read-time rule for old unrecorded watches. MCP `log_watch` has no way to log that bucket at all. | `app.py:2201-2202`; probe: `POST …/watches {"date_bucket":"over_10_years"}` → `recorded [1, 2, 3, 4]` | Nick's ruling: default to active adults for that bucket (server), the page switches the kids off when that bucket is chosen and sends `attendees` only when the selection differs from the bucket's default, MCP `log_watch` gains `date_bucket`. Spec §6.2/§7.2, README, ledger updated. | `1b410f9` |
| F3 | fix | #29 | **Closing the People modal after a change re-renders any route**, which resets the Add form (and the scanner) and jumps the grid. Only the grid and the title view show people. | `static/js/people.js:135-143`; ledger "Final: parked" | Re-run the route only when it is `/` or `/t/:id`. | `4886e65` |
| F4 | fix | #23, #29 | **The privacy guard does not cover every public route.** The AST walk stops at `app.py`'s module boundary; the runtime check is a fixed list of 8 paths, no people seeded, 2 of 6 recommendation modes. | `tests/test_privacy.py:63-66,143-160` | Runtime test that seeds people, recorded and assumed attendance and a note, then GETs every `/api/` rule in `app.url_map` (all six modes, every title id) and asserts no note text, no person name, no attendance key. | `26d748d` |
| F5 | fix | #26 | **`docs/mcp-setup.md` says an unset or empty `MATINEE_MCP_READONLY` keeps the service read-only.** Code, shell twin and spec defer to `MATINEE_MCP_WRITES` (`READONLY` unset + `WRITES=log` → `log`). | `docs/mcp-setup.md:147-148`; `mcp_http.py:33-49`; `tests/test_mcp_http.py` case `(None, "log", "log")` | Correct the two table rows; add a test that pins the doc sentence to `_resolve`. | `124c3ad` |
| F6 | fix | #19 | **`CLAUDE.md`'s Definition of done is stale**: five CI jobs (seven exist), no `ruff check` / `shellcheck`, nothing on people/attendance being editor-only or `tests/test_privacy.py`. | `CLAUDE.md:52-61`; `.github/workflows/ci.yml` | Name all seven jobs, add ruff and shellcheck to the commands, add the privacy rule. | `9748b8b` |
| F7 | fix | #23–#29 | **Four 2026-11-11 spec status lines still say "this PR / this branch / awaiting merge".** | `docs/superpowers/specs/2026-11-11-{csv-export,mcp-tiered-writes,per-person,privacy-read-key}-design.md:6` | "Implemented; merged in PR #N (`<merge sha>`)". | `58feec9` |
| F8 | note | #18 | Side-effecting GETs pass for "lan"/Access identities when a browser sends neither `Origin` nor `Sec-Fetch-Site`, or sends `same-site`. Cannot read the response (no CORS); can spend UPC quota and write a cache row. | §2.1 table | D3. | `7248aa7` (D3) |
| F9 | note | #18 | No `Host` header → `SERVER_NAME` (`0.0.0.0` under gunicorn) → "lan". Browsers always send Host; a raw LAN client is already trusted. | `app.py:552-573` | None. | — |
| F10 | note | #23 | Read key can call `tmdb/search` and `upc/<upc>` (quota, and a cache row from a "read-only" key). | `app.py:592-595,1706-1777` | D1. | `5b0b094` (D1) |
| F11 | note | #23 | `/api/me` answers `editor: true` for the read key; with `PUBLIC_VIEW=0` the read key also opens `/images/`. Both harmless. | `app.py:1603-1605,703-707` | None. | — |
| F12 | note | #25 | `ops/matinee-refresh` runs `refresh_titles.py` as root: images it rewrites are root-owned (still replaceable by the app through `os.replace`), and while it runs the WAL `-wal`/`-shm` files may be root-created, which would make a concurrent app request fail with "readonly database". Same pre-existing pattern as `refresh_ratings.py` and the backup. **Likely.** | `ops/matinee-refresh:15-21`; `app.py:206-211` (WAL) | Run the Python under `su -s /bin/sh matinee -c …` with the needed vars; host check H10. | — |
| F13 | note | #25 | Restore test counts `titles` and `copies` only; `people` / `watch_people` are in the backup but unchecked. | `ops/matinee-restore-test:58-60` | Also log `watches` and (when present) `people` counts. | — |
| F14 | note | #27 | Any `?v=` value gets a one-year cache, matched or not. No path produces a mismatched URL today (index is `no-store`, the installer stops the service before swapping files, no dynamic `import()`). | `app.py:690-700` | Optional: long-cache only when `v` equals the current hash. | — |
| F15 | note | — | Ask `BUDGET_S = 60` equals gunicorn `--timeout 60`; gthread workers heartbeat from the main loop, so a long Ask should not be killed. **Can't verify.** | `ask.py:29`; `setup-head.sh:173` | Host check H7; lower `BUDGET_S` to 50 if it is. | — |
| F16 | note | #29 | MCP REST passes numeric audience ids without checking `/people`; the server answers 400 for unknown ids. | `mcp_server.py:_people_ids` | Ledger ruling stands. | — |
| F17 | note | #29 | The ten-year line uses the UTC date (SQLite `date('now')`, `attendance_of`); in the Pacific evening the boundary is a day off. | `recommend.py:90-100,124-133` | None. | — |
| F18 | note | — | Merged remote branches still exist. | §2.17 | D4. | — |
| F19 | note | #26 | `CF_ACCESS_TEAM_DOMAIN` is exported by `matinee-mcp`'s init script and read by `mcp_remote.py`, but not in `.env.example` or `add_setting` (removed on purpose in PR #15; falls back to `CF_TEAM_DOMAIN`). The only exception to settings parity. | `setup-head.sh:208`; `mcp_http.py:_http_config` | None. | — |
| F20 | note | #29 | With no people, editor title detail gains `seen_by: []` and per-watch `attendance`, `attendees: []`; everything else, and every public response, is identical to `98f5ef7`. | §2.12 | None (additive, spec §6.4). | — |
| F21 | note | — | `app.py` is 2,396 lines; seams in §2.16. | — | Next refactor. | — |
| F22 | note | #29 | An explicit date more than ten years back still defaults to everyone (R21 covers the bucket). | `app.py:add_watch` | D2. | `3e3d919` (D2) |

---

## 4. Cross-PR interactions

| PRs | Interaction | Status |
|---|---|---|
| #18 × #29 | Every route #29 added carries `@editor_required`, so the cross-site and Host rules apply to people and attendance writes (§2.1). | Verified |
| #18 × #23 | Key identities are exempt from the cross-site check; the read key is GET/HEAD-only after that exemption. | Verified (`app.py:592-595`) |
| #23 × #26 | `matinee-mcp` holds exactly one key per level; the startup refusal applies at every level (§2.4). | Verified (code); host H4 |
| #23 × #29 | Attendance is stripped by the same `PRIVATE_WATCH` path as notes; people parameters 400 publicly. F4 makes the guard route-complete. | Verified |
| #26 × #29 | `list_people` registered at every level; `log_watch` with `attendees` at `log`/`all`. F2 adds `date_bucket` to `log_watch` at the same levels. | Verified |
| #20 × #29 | ui-smoke runs both Playwright files and fails on a skip; the flaky test predates #29 (ledger) and is fixed by F1. | Verified |
| #25 × #29 | Backups include v9 tables; restore test does not count them (F13). | Verified |
| #27 × #29 | `people.js` follows the module rules (`tests/test_js_modules.py`); versioned through the same import map. | Verified |
| #24 × #29 | Export has no per-person columns (spec §non-goals). | Verified |

---

## 5. Host checklist (CT 210)

Run as root on the Matinee host unless noted. `movies.example.com` / `mcp.example.com` stand for the real hostnames.

| # | Check | Command | Expected |
|---|---|---|---|
| H1 | Does LAN traffic reach the app through Cloudflare? | From a LAN machine: `curl -sI https://movies.example.com/ \| grep -i '^cf-ray'` | A `cf-ray:` line → LAN requests come through the tunnel, the "lan" identity never applies, and **`LAN_EDIT=0`** is the safe setting (editing needs Access). No line (split DNS to the LXC) → keep `LAN_EDIT=1` and set **`LAN_HOSTS=movies.example.com`**, since the browser's Host is the name, not an IP. |
| H2 | Settings that follow from H1 | `grep -E '^(LAN_EDIT\|LAN_HOSTS)=' /etc/conf.d/matinee` | Matches the H1 row; after editing: `rc-service matinee restart`. |
| H3 | Schema after the upgrade | `sqlite3 /var/lib/matinee/matinee.db "SELECT v FROM meta WHERE k='schema'; SELECT name FROM sqlite_master WHERE name IN ('people','watch_people');"` | `9`, `people`, `watch_people` |
| H4 | `matinee-mcp` holds only the read key at level none | `p=$(pgrep -f mcp_http.py \| head -n1); tr '\0' '\n' < /proc/$p/environ \| grep -E '^MATINEE_(API\|READ)_KEY=' \| cut -d= -f1` | `MATINEE_READ_KEY` only (no `MATINEE_API_KEY`). |
| H5 | `rc-service matinee restart` brings `matinee-mcp` back | `rc-service matinee restart; sleep 3; rc-service matinee-mcp status; wget -qO- "http://$(sed -n 's/^MCP_HTTP_BIND=//p' /etc/conf.d/matinee):8719/healthz"` | `status: started`, health JSON. If stopped: `rc-service matinee-mcp start` and note that a matinee restart needs both. |
| H6 | No person name in the access log | `grep -nE 'audience=\|unseen_by=' /var/log/matinee.log \| grep -vE '(audience\|unseen_by)=(everyone\|adults\|kids\|[0-9,%2C]+)(&\| )' \| head` | No output. |
| H7 | Ask near gunicorn's 60 s timeout | Signed in on the phone, Ask: *"Compare every Harry Potter and Toy Story film we own, which ones have the kids not seen, and where is each on the shelf?"*; then `sqlite3 /var/lib/matinee/matinee.db "SELECT datetime(ts,'unixepoch','localtime'), rounds, status FROM ask_log ORDER BY id DESC LIMIT 3"` and `grep -i 'WORKER TIMEOUT' /var/log/matinee.log` | An answer (or a JSON "unavailable" error, not a Cloudflare 502), `status` `ok` or `timeout`, and no `WORKER TIMEOUT`. |
| H8 | First people and a watch from an Android phone | Phone, `https://movies.example.com/admin` → People: add Nick, Lauren (adult), Midge, Alice, Paige (kid). Open *The Princess Bride* → tap **10+ Years**, leave the toggles as switched (kids off) → **Log**. | Watch row shows N and L dots. `sqlite3 /var/lib/matinee/matinee.db "SELECT p.name FROM watch_people wp JOIN people p ON p.id=wp.person_id WHERE wp.watch_id=(SELECT max(id) FROM watches)"` → `Nick`, `Lauren`. Then **Watched Today** on any title → all five dots. |
| H9 | Weekly restore test is scheduled and passes | `ls -l /etc/periodic/weekly/matinee-restore-test; crontab -l \| grep weekly; /etc/periodic/weekly/matinee-restore-test; echo $?; tail -n1 /var/log/ops.log` | Executable, a `run-parts /etc/periodic/weekly` line, exit `0`, `matinee-restore-test: ok <date>: integrity ok, N titles, M copies`. |
| H10 | Root-run refresh did not leave root-owned DB files | `ls -l /var/lib/matinee/ \| grep -E 'matinee\.db(-wal\|-shm)?$'` | All owned by `matinee`. Root-owned `-wal`/`-shm` while the app is up → F12 is real; `rc-service matinee restart` clears it. |
| H11 | Watches over ten years old logged since PR #29 with kids recorded (R21 and D2 do not rewrite them) | `sqlite3 -header /var/lib/matinee/matinee.db "SELECT w.id, t.title, coalesce(w.watched_date, w.date_bucket) AS watched, w.created_at, group_concat(p.name, ', ') AS kids FROM watches w JOIN titles t ON t.id=w.title_id JOIN watch_people wp ON wp.watch_id=w.id JOIN people p ON p.id=wp.person_id WHERE (w.date_bucket='over_10_years' OR w.watched_date < date('now','-10 years')) AND p.kind='kid' GROUP BY w.id ORDER BY w.created_at"` | Each row is a watch to check by hand: if the kids were not there, open the title, tap its dots, switch them off, Save. Empty = nothing to do. |
| H12 | Connector from the phone | Claude app on the Pixel → Settings → Connectors → add `https://mcp.example.com/matinee/mcp`, sign in through Access; ask *"Has Paige seen The Princess Bride?"* | Tools listed (7 at `none`, 9 at `log`); answer "no" after H8, via `get_movie` (`not_seen_by` includes Paige). |
| H13 | MCP access log has no names | `grep -ciE 'nick\|lauren\|midge\|alice\|paige' /var/log/matinee-mcp.log` | `0` |

---

## 6. Phase 2 notes

- **F2 (R21) UX.** With people set up, a bucket button now *chooses* a bucket: the toggles switch to its default
  (kids off for "10+ Years"), the editor can switch anyone back on, and a 44 px **Log 10+ Years** button sends it.
  That second tap is what makes "switch Midge back on" possible; without people the buckets still log on one tap.
  Every log sends `attendees` only when the toggles differ from the default for that log, so an untouched form
  leaves the choice to `app.default_attendees`. MCP `log_watch` gained `date_bucket` so both modes can log the
  bucket at all.
- **F1** also guards `renderGrid` and `renderDetail`'s later awaits; the regression test covers Stats, a title and
  the grid. The previously flaky test passed 25 of 25 runs after the fix.
- **F4** was mutation-checked: making `recommend()` attach `seen_by` without an audience fails only the new test.

## 7. Not done here (by instruction)

- No product decisions taken (D1–D4); no change to who can see data.
- `app.py` not split (§2.16).
- Watches logged since PR #29 not rewritten (H11).
