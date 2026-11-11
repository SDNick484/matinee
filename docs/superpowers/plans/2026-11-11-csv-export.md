# CSV export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /admin/api/export.csv` and `ops/export_csv.py` produce one CSV row per owned copy; the admin Stats view links to it.

**Architecture:** `app.py` gains `export_rows(con)`, `export_csv(con, today)` and the route. The CLI imports `app` like the other `ops/` scripts and calls the same builder on a read-only connection. One small link in `static/app.js`.

**Tech Stack:** Python `csv`/`io` (standard library), Flask, SQLite, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-11-11-csv-export-design.md` is the source of truth. If the code contradicts it, stop and ask.

## Global Constraints

- No new dependency, setting, schema change or MCP tool. Watch notes are never exported.
- Columns, in order: `title, year, tmdb_id, imdb_id, certification, runtime, kind, format, service, location_path, user_rating, watch_count, last_watched, added_on`.
- UTF-8 BOM, CRLF, RFC 4180 quoting; text cells starting with `= + - @` (or tab, CR) get a `'` prefix; numbers are never prefixed.
- `python -m pytest -q` green after every task. Commits: Conventional Commits, author `SDNick484 <SDNick484@users.noreply.github.com>`.

---

### Task 1: Builder and route

**Files:** modify `app.py` (below `collection_stats`; docstring route list); create `tests/test_export.py`.

**Interfaces:**
- Produces `app.EXPORT_COLUMNS`, `app.export_rows(con) -> list[list]`, `app.export_csv(con, today=None) -> tuple[bytes, str]` (body, filename), and `GET /admin/api/export.csv`.

- [ ] **Step 1:** Write the failing tests in `tests/test_export.py` (scenario, accent, auth, headers, quoting, injection, edge cases, no notes).
- [ ] **Step 2:** `python -m pytest -q tests/test_export.py` → FAIL (404 / missing names).
- [ ] **Step 3:** Implement the builder and route.
- [ ] **Step 4:** `python -m pytest -q` → green.
- [ ] **Step 5:** Commit `feat(export): add GET /admin/api/export.csv`.

### Task 2: CLI

**Files:** create `ops/export_csv.py`, `tests/test_export_cli.py`.

- [ ] **Step 1:** Failing tests: stdout equals the route body; `-o DIR` writes the dated file, mode 0600, no temp left; missing DB exits 1; the connection is read-only.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** `python -m pytest -q` green.
- [ ] **Step 5:** Commit `feat(ops): add export_csv.py for cron use`.

### Task 3: Stats link

**Files:** modify `static/app.js` (`renderStats`), `static/style.css`, `tests/test_static_ui.py`.

- [ ] **Step 1:** Failing static test (link inside `renderStats`, gated on `isAdmin`, correct href, absent from the public path).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Add the link and one CSS rule. **Step 4:** `python -m pytest -q && node -c static/app.js`.
- [ ] **Step 5:** Commit `feat(ui): Export CSV link in admin stats`.

### Task 4: Docs

**Files:** modify `README.md`.

- [ ] **Step 1:** Add an "Export" section: the endpoint, the columns, the read key, the cron line, what is not exported. Use `movies.example.com`.
- [ ] **Step 2:** `python -m pytest -q` (hygiene) green.
- [ ] **Step 3:** Commit `docs(export): document CSV export and the cron CLI`.

### Task 5: Release

- [ ] Run `python -m pytest -q`, `node -c static/app.js`, `sh installer/build.sh && sh -n dist/setup-matinee.sh`; push `feat/csv-export`; open the PR against `main` (do not merge); poll CI until Python 3.10–3.13 and Alpine (and any other job) are green; notify.
- [ ] Manual, on the host after merge: open `/admin#/stats`, tap **Export CSV**, open the file in a spreadsheet and check *Amélie*. Deploy needs no config change.
