# Copy Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record `edition` (public), `code_status`, `purchased_on` and `price_cents` (editor-only) on each copy, with schema v10, API, CSV, MCP, Stats and a phone-sized UI.

**Architecture:** Four columns appended to `copies` through an idempotent `migrate()` block that runs after the old copies rebuild. `copy_values()` validates; `PRIVATE_COPY` in `browse.py` strips the three editor-only keys for public requests. UI fields sit in a native `<details>` "More".

**Tech Stack:** Flask, SQLite, pytest, native-ES-module vanilla JS, Playwright (CI UI smoke). No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-11-copy-details-design.md`

## Global Constraints

- `SCHEMA_VERSION` `"9"` → `"10"`; no manual DB step; new columns appended after `updated_at` in `SCHEMA` and added by `ALTER TABLE` in `migrate()` (same `PRAGMA table_info` either way).
- `edition` ≤ 60 characters; `code_status` in `none | unredeemed | redeemed | expired`, default `none`; `price_cents` integer 0 to 10,000,000; `purchased_on` a real `YYYY-MM-DD` date.
- Public: `edition`. Editor-only: `code_status`, `purchased_on`, `price_cents` (anonymous `/api` never has those keys).
- Writes under `/admin/api/` with `@editor_required`; no new MCP tool; no new setting, dependency, `$DATA_DIR` file or installer change.
- CSV: the existing 14 columns keep their positions; append `edition, code_status, purchased_on, price`.
- Commits: Conventional Commits, author `SDNick484 <SDNick484@users.noreply.github.com>`, one logical change each.
- Scenario data: *The Dark Knight* (TMDB 155), 4K UHD "Steelbook", Family Room › Media Cabinet › Shelf 1, `unredeemed`, `2026-10-31`, 2499 cents.

## Review Focus

- Price `10**30` as JSON: expect 400, not an `OverflowError` 500 (Task 2).
- Price `24.99` (float) or `true`: expect 400, not a silent cast (Task 2).
- Date `2026-02-30` and `10/31/2026`: expect 400 on every Python 3.10 to 3.13 (Task 2).
- PUT that sends only `{"edition": ""}`: clears the edition and keeps code, date and price (Task 2).
- Editing a physical copy with a code to Digital without sending `code_status: "none"`: 400 with a readable message, the UI sends `none` (Tasks 2, 6).
- A database not yet migrated to v10 read by the MCP server in direct mode: still answers, `edition` null (Task 5).
- An edition starting with `=` in the CSV: quoted (Task 4).

## File Structure

- `db.py`: `SCHEMA`, `migrate()`, `SCHEMA_VERSION`, constants `CODE_STATUSES`, `EDITION_MAX`, `PRICE_MAX_CENTS`.
- `app.py`: `copy_values()`, `add_copy()`, `edit_copy()`, re-exports.
- `browse.py`: `PRIVATE_COPY`, `list_titles()` (`code`), `collection_stats()` (`unredeemed_codes`).
- `export.py`: `EXPORT_COLUMNS`, `export_rows()`.
- `mcp_server.py`: `_get_movie()`, `where_is()`.
- `static/js/core.js` (`dollarsToCents`, `centsToDollars`), `forms.js`, `detail.js`, `stats.js`, `grid.js`, `static/style.css`.
- Tests: `tests/test_copy_details.py` and `tests/test_copy_details_ui.py` (new); `test_privacy.py`, `test_export.py`, `test_mcp_server.py`, `test_static_ui.py`, `test_people.py`, `test_app.py` (extend).
- Docs: `docs/mcp-setup.md`, `README.md`.

### Task 1: Schema v10

**Files:** Modify `db.py`, `tests/test_people.py`, `tests/test_app.py`; Create `tests/test_copy_details.py`.

**Interfaces:**
- Produces: `copies` columns `edition TEXT`, `code_status TEXT NOT NULL DEFAULT 'none'`, `purchased_on TEXT`, `price_cents INTEGER`; `db.CODE_STATUSES = ("none","unredeemed","redeemed","expired")`, `db.EDITION_MAX = 60`, `db.PRICE_MAX_CENTS = 10_000_000`, re-exported by `app`.

- [ ] **Step 1: Failing tests** in `tests/test_copy_details.py`: import `HISTORICAL` and `_rows` from `test_people`; `V9 = HISTORICAL[8] + PEOPLE_DDL` (people, watch_people, index). Parametrize `test_migrates_every_version` over 1..9 with the Dark Knight copy, watch and (v9) people and attendance seeded; run `init_db()` twice and `migrate()` twice; assert every old table's rows are unchanged (compare the old column list), the copy has `edition IS NULL`, `code_status == 'none'`, `purchased_on IS NULL`, `price_cents IS NULL`, `integrity_check == "ok"`, `foreign_key_check == []`, schema stamp `"10"`. Also: `test_fresh_and_migrated_tables_match` (equal `PRAGMA table_info(copies)`); `test_checks_reject_bad_values` (`IntegrityError` for `code_status='opened'`, a 61-character edition, `price_cents=-1`).
- [ ] **Step 2:** `python -m pytest tests/test_copy_details.py -q` → FAIL (no columns).
- [ ] **Step 3:** Add the four columns to the end of `CREATE TABLE copies` in `SCHEMA` with the spec §3 definitions; add the constants; in `migrate()` add the v10 block after the v9 block (`for col, ddl in COPY_DETAIL_COLUMNS`, guarded by a non-empty `copy_cols`); set `SCHEMA_VERSION = "10"`. Change the hard-coded `"9"` stamps in `tests/test_people.py` (lines asserting `== "9"`, `SCHEMA_VERSION == "9"`, and the v9 test's stamp) and `tests/test_app.py` to `"10"`.
- [ ] **Step 4:** `python -m pytest tests/test_copy_details.py tests/test_people.py tests/test_app.py tests/test_omdb.py tests/test_locations.py tests/test_custom_poster.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(db): schema v10 adds edition, code status, purchase date and price to copies`.

### Task 2: Validation, routes and privacy

**Files:** Modify `app.py`, `browse.py`, `tests/test_copy_details.py`, `tests/test_privacy.py`.

**Interfaces:**
- Consumes: Task 1 columns and constants.
- Produces: `copy_values(data) -> {"kind","format","service","location_id","edition","code_status","purchased_on","price_cents"}` (aborts 400 with the spec §4 messages); `add_copy`/`edit_copy` store all eight; `PRIVATE_COPY = ("added_by","code_status","purchased_on","price_cents")`.

- [ ] **Step 1: Failing tests** (client fixture with the write key, as in `test_export.py`): the Dark Knight scenario (POST with all four fields → 201; anonymous `/api/titles/<id>` copy has `edition == "Steelbook"` and none of the three keys; `/admin/api/titles/<id>` copy has `code_status == "unredeemed"`, `purchased_on == "2026-10-31"`, `price_cents == 2499`); parametrized 400s with the exact messages from spec §4 (digital + `unredeemed`; `code_status` `"opened"`; prices `"abc"`, `-2499`, `24.99`, `True`, `10**30`; dates `2026-13-01`, `2026-02-30`, `10/31/2026`; 61-character edition); digital + `"none"` is 201; digital copy with price and date is 201; PUT `{"edition": ""}` clears only the edition; PUT kind digital without `code_status` on a copy that has `unredeemed` is 400. In `test_privacy.py`: add the three keys and `unredeemed_codes` to `PRIVATE_KEYS`; in `family_with_history` set Steelbook, `unredeemed`, `2026-10-31`, `2499` on The Dark Knight's copy and assert `"2026-10-31"` is in no public body.
- [ ] **Step 2:** run `tests/test_copy_details.py tests/test_privacy.py` → FAIL.
- [ ] **Step 3:** Extend `copy_values()` (helpers `_edition`, `_purchased_on`, `_price_cents`, `_code_status(kind, raw)` beside it): `edition` via `clean()` and a length check; dates by regex `^\d{4}-\d{2}-\d{2}$` then `dt.date(y, m, d)`; price accepts `int` (not `bool`) or a digit string, bounded by `PRICE_MAX_CENTS`; `code_status` lower-cased and checked against `CODE_STATUSES`, digital allows only `none`. Extend `add_copy`'s INSERT and `edit_copy`'s merge/UPDATE; add the three keys to `PRIVATE_COPY`.
- [ ] **Step 4:** the same test run → PASS; then `python -m pytest -q` (whole suite) → PASS.
- [ ] **Step 5: Commit** `feat(api): store copy edition, code status, purchase date and price`.

### Task 3: Unredeemed filter and count

**Files:** Modify `browse.py`, `tests/test_copy_details.py`, `tests/test_privacy.py`.

**Interfaces:**
- Consumes: `code_status` column; `make_err`.
- Produces: `GET /admin/api/titles?code=unredeemed`; `collection_stats(private)` adds `"unredeemed_codes": int` only when `private`.

- [ ] **Step 1: Failing tests:** editor `code=unredeemed` returns The Dark Knight (and not an Amélie with `none`); `code=redeemed` → 400 `"code must be unredeemed."`; `/api/titles?code=unredeemed` and `?code=` → 400 `"Sign in to filter by digital code."`; editor stats `unredeemed_codes == 1`; public stats has no such key.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** In `list_titles` refuse a public `code` right after `people_params_refused` (new `code_param_refused(private)`); for editors validate and add `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.code_status='unredeemed')`. In `collection_stats` build the body as a dict and add the count under `if private:`.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5: Commit** `feat(api): unredeemed code filter and count for editors`.

### Task 4: CSV export

**Files:** Modify `export.py`, `tests/test_export.py`.

**Interfaces:**
- Produces: `EXPORT_COLUMNS` = old 14 + `("edition","code_status","purchased_on","price")`; `price` rendered `f"{c // 100}.{c % 100:02d}"` or `None`.

- [ ] **Step 1: Failing tests:** `HEADER` gains the four; the Dark Knight row ends `["Steelbook","unredeemed","2026-10-31","24.99"]` and its first 14 cells are unchanged; a copy with no details ends `["","none","",""]`; an edition `=HYPERLINK("x")` is exported with a leading `'`.
- [ ] **Step 2:** run `tests/test_export.py tests/test_export_cli.py` → FAIL.
- [ ] **Step 3:** select the four columns in `export_rows()` and append the cells; keep `csv_cell` for `edition`.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5: Commit** `feat(export): add edition, code status, purchase date and price columns`.

### Task 5: MCP and docs

**Files:** Modify `mcp_server.py`, `docs/mcp-setup.md`, `tests/test_mcp_server.py`.

**Interfaces:**
- Produces: direct `get_movie` copies carry `edition` (never the three editor-only keys); `where_is` copy dicts carry `edition`; summary part is `f"{format} {edition} at {loc}"` / `f"{format} {edition} on {service}"` (edition omitted when empty), in both modes.

- [ ] **Step 1: Failing tests:** direct `get_movie` copy has `edition == "Steelbook"` and none of `code_status`/`purchased_on`/`price_cents`; `where_is("The Dark Knight")["summary"]` contains `"4K UHD Steelbook at Family Room › Media Cabinet › Shelf 1"` in direct and (via `_api_backed_client`) REST mode; keyed REST `get_movie` copy has all four, unkeyed has `edition` only (pattern of `test_rest_get_movie_added_by_only_with_key`); a database whose `copies` table has no `edition` column answers with `edition` null; `ask.LocalClient(...).get_movie(1)` has none of the three keys.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** in `_get_movie` select `edition` only when `PRAGMA table_info(copies)` lists it (helper `_copy_select(con)`); in `where_is` add `edition` to the SQLite query, both `cp_dict`s and both summary builders. Docs: add the "Copy purchase details (`code_status`, `purchased_on`, `price_cents`)" column to the access table (no / yes / yes), note that direct SQLite mode returns the edition only, and extend the `where_is` bullet.
- [ ] **Step 4:** `python -m pytest tests/test_mcp_server.py tests/test_ask.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(mcp): get_movie and where_is carry the edition`.

### Task 6: Copy form, title view and money helpers

**Files:** Modify `static/js/core.js`, `forms.js`, `detail.js`, `static/style.css`, `tests/test_static_ui.py`; Create `tests/test_copy_details_ui.py`.

**Interfaces:**
- Produces in `core.js` (exported): `dollarsToCents(text: string) -> number | null | undefined` (cents; empty → `null`; invalid → `undefined`) and `centsToDollars(cents: number) -> string` (`"$24.99"`, `"$1,299.00"`); in `detail.js` a module-local `copyExtraHTML(cp)` called only as `isAdmin ? copyExtraHTML(cp) : ""`.

- [ ] **Step 1: Failing tests:** static: `<details` with `More` in both modals; the three keys appear in `detail.js` only inside `copyExtraHTML`; the call is gated on `isAdmin`; `node -e` check of `dollarsToCents` for `"24.99"→2499`, `"$24.99"→2499`, `"1,299"→129900`, `"24.9"→2490`, `""→null`, `"abc"→undefined`, `"-5"→undefined`, `"24.999"→undefined`. Playwright (412 and 360, real server, one seeded Dark Knight, the `server`/`browser` harness of `test_people_ui.py`): open `/admin#/t/1`, tap **+ Add copy**; **More** is closed and the dialog has no vertical scroll and no horizontal scroll; open it, still no horizontal scroll; fill Steelbook, Unredeemed, `2026-10-31`, `24.99`; Save; the row shows `Steelbook`, `Code unredeemed`, `Oct 31, 2026`, `$24.99`; the stored copy has 2499 cents; a bad price (`abc`) shows the toast and sends no POST; Edit opens **More** with the values; switching to Digital sends `code_status: "none"`; anonymous page (`/` with `/api/me` editor false) shows `Steelbook` and none of `Unredeemed`, `$24.99`, `Oct 31, 2026`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement per spec §6: helpers in `core.js` (string arithmetic, no float); `More` blocks in both modals with `form-input` fields (`min-width: 0; width: 100%`), a four-chip `code_status` group hidden for digital, and the payload fields; `copy-edition` span for everyone; `copyExtraHTML` for editors; CSS for `.more-details`, `.more-fields`, `.copy-edition`, `.copy-extra` (`flex-basis: 100%`, amber for unredeemed).
- [ ] **Step 4:** `python -m pytest tests/test_static_ui.py tests/test_copy_details_ui.py tests/test_js_modules.py -q`; `for f in static/js/*.js; do node --check "$f"; done` → PASS.
- [ ] **Step 5: Commit** `feat(ui): copy form More section, edition chip and editor-only copy details`.

### Task 7: Stats line and grid filter

**Files:** Modify `static/js/stats.js`, `static/js/grid.js`, `static/style.css`, `tests/test_static_ui.py`, `tests/test_copy_details_ui.py`.

**Interfaces:**
- Consumes: `data.unredeemed_codes` from `/admin/api/stats`; the grid forwards the whole route query to the API.
- Produces: grid query key `code`, handled like the people filters (dropped for visitors, one retry on a 400, cleared by Reset).

- [ ] **Step 1: Failing tests:** static: the Stats line is inside `renderStats` and gated on `isAdmin`; Playwright: with one unredeemed code, `/admin#/stats` shows `Unredeemed digital codes: 1` linking to `#/?code=unredeemed`, the grid shows The Dark Knight with an **Unredeemed codes ✕** button that clears just that filter; at zero the line is plain text; an anonymous visit to `/#/?code=unredeemed` drops the filter and shows the full grid; no horizontal scroll at 412 px.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement: Stats line after the overview cards; `grid.js` adds `code` to the editor-only filters (rename the three people-filter helpers to editor-filter names and update every caller), to `state.grid`, `hasFilters`, the empty-state check, Reset, and renders the active-filter button.
- [ ] **Step 4:** run the UI tests, `node --check`, `python -m pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(ui): unredeemed digital codes line on Stats and grid filter`.

### Task 8: README, final checks, PR and deploy checklist

**Files:** Modify `README.md`.

- [ ] **Step 1:** README: list the new CSV columns, the copy details and who sees them.
- [ ] **Step 2:** run, and record the output of: `python -m pytest -q` (report skips), `for f in static/js/*.js; do node --check "$f"; done`, `ruff check`, `shellcheck -s sh installer/setup-head.sh installer/build.sh ops/matinee-backup ops/matinee-refresh ops/matinee-restore-test`, `sh installer/build.sh && sh -n dist/setup-matinee.sh`.
- [ ] **Step 3: Commit** `docs: describe copy details in the README`; push `feat/copy-details`; open the PR against `main` (not merged).
- [ ] **Step 4:** poll `gh api repos/SDNick484/matinee/commits/<head-sha>/check-runs` until Lint, UI smoke, Python 3.10, 3.11, 3.12, 3.13 and Alpine have completed; fix and push on any failure and poll again.
- [ ] **Step 5 (Nick, after the PR is green):** build the installer, deploy to CT 210, `rc-service matinee restart`, then the Android smoke test listed in the final report.
