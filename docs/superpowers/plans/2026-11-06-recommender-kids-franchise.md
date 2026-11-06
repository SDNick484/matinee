# Recommender: Kids' Cap, Next in Franchise, Start a Series — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `max_cert` rating cap, an editor certification override (schema v7), and `next_in_franchise` / `start_franchise` modes, served identically by the web API and the MCP server.

**Architecture:** The recommender moves into a new stdlib-only module `recommend.py` (`parse_params` + `recommend`). `app.py` wraps it with `poster_url` and `jsonify`; `mcp_server.py` imports it lazily in direct-SQLite mode and forwards params in REST mode. The Movie Night modal gains a 3-column mode grid and a rating-cap radiogroup; the title view gains an editor-only certification select.

**Tech Stack:** Python 3.10–3.13, Flask, SQLite (window functions, ≥ 3.25), `mcp` (FastMCP), vanilla JS/CSS, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-recommender-kids-franchise-design.md`

## Global Constraints

- No new runtime dependency; `recommend.py` imports only the standard library and never imports Flask.
- No frontend framework or build step; `node -c static/app.js` passes.
- CI green: Python 3.10–3.13 on Ubuntu, the Alpine job, `node -c static/app.js`.
- Tests never hit the network; TMDB data comes from `tests/fixtures/tmdb/` (hand-written in TMDB's shape, per its README).
- `CERT_ORDER = ("G", "PG", "PG-13", "R", "NC-17")`; override values `CERT_ORDER + ("NR",)`.
- Effective certification everywhere: `COALESCE(t.certification_override, t.certification)`.
- API: no default `max_cert`. Movie Night UI default: **Any**. "Kids" with no rating named (MCP instructions, `what_to_watch(kids=True)`): **PG-13**.
- Invalid `max_cert` → 400 `{"error": "max_cert must be one of G, PG, PG-13, R, NC-17"}`.
- `SCHEMA_VERSION = "7"`; migration idempotent from v1, no manual DB step on CT 210.
- New write route under `/admin/api/` with `@editor_required`; read routes keep `PUBLIC_VIEW` handling.
- MCP tool name stays `get_recommendations`; `mcp>=1.0.0` floor stays.
- UI: ≥ 44 px tap targets, no horizontal scroll at 412 px, existing CSS tokens only.
- Commits: Conventional Commits, author `SDNick484@users.noreply.github.com`, one logical change each; every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01FaboogRK2BeD1sCvcDYXzt
  ```
- Shell: OpenRC (`rc-service matinee restart`), `vim`.

## Review Focus

1. **Dateless watches** (`watches.watched_date IS NULL`, historical "date unknown" logs): a collection with only a dateless watch still counts as started, and sorts after collections with dated watches. → Task 5, `test_dateless_watch_starts_series`.
2. **A title owned in two formats** (4K UHD + Blu-ray copies): appears once as the franchise pick, never twice. → Task 5, `test_two_copies_one_pick`.
3. **Missing `release_date`** in a collection: that title sorts after dated titles, so it isn't picked ahead of the real first film. → Task 5, `test_null_release_date_sorts_last`.
4. **Sloppy `max_cert` input** (`" pg-13 "`, `"pg"`): normalized, not rejected; empty string means no cap. → Task 4, `test_max_cert_normalizes`.
5. **Override set to `NR`**: the title stays hidden under every cap (NR isn't a standard rating), but the badge shows NR. → Task 4, `test_nr_override_still_hidden`.

---

### Task 1: Fix the missing `datetime` import in MCP direct-SQLite recommendations

**Files:**
- Modify: `mcp_server.py:9-17` (imports)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: the existing `test_env` fixture (Inception, rated 9.5, watched 2025-01-15).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

```python
def test_mcp_forgotten_gems_reason_uses_date_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    r = client.get_recommendations(mode="forgotten_gems")["results"][0]
    assert "Not watched in" in r["reason"]
```

- [ ] **Step 2: Run it** — `python3 -m pytest tests/test_mcp_server.py::test_mcp_forgotten_gems_reason_uses_date_direct_sqlite -v` → FAIL (reason is "Rated 9.5 ★ · Forgotten gem").
- [ ] **Step 3: Add `import datetime as dt`** to the stdlib imports of `mcp_server.py`.
- [ ] **Step 4: Run the full suite** — `python3 -m pytest -q` → all pass.
- [ ] **Step 5: Commit** — `fix(mcp): import datetime in direct-SQLite recommendations`

---

### Task 2: Extract the recommender into `recommend.py` (behavior-neutral for the API)

**Files:**
- Create: `recommend.py`
- Modify: `app.py:1282-1411` (`recommendations()` becomes a thin wrapper), `app.py:961-962` and its `FORMAT_ORDER` (moved; `app.py` does `from recommend import FORMAT_ORDER, sorted_formats`), `mcp_server.py:353-478` (direct branch delegates), `installer/build.sh:8`
- Create: `tests/test_recommend.py`
- Modify: `tests/conftest.py` (shared `seed_title` helper), `tests/test_mcp_server.py` (parity test)

**Interfaces:**
- Produces (in `recommend.py`):
  - `MODES: tuple[str, ...]` — the four existing modes for now (Task 5 adds two).
  - `FORMAT_ORDER`, `sorted_formats(fs: Iterable[str]) -> list[str]` — moved verbatim from `app.py`.
  - `class ParamError(ValueError)`.
  - `parse_params(m: Mapping[str, Any]) -> dict` — keys: `mode: str`, `limit: int` (1–50, default 10), `genre: str|None`, `medium: "physical"|"digital"|None`, `min_rating: float|None`, `max_runtime: int|None`, `random: bool`. Accepts Flask `request.args` (strings) or MCP kwargs (typed); same rules as today's code (bad numbers ignored; unknown mode kept and served with the default ordering).
  - `recommend(con: sqlite3.Connection, p: dict, today: datetime.date | None = None) -> dict` — returns `{"mode", "count", "results"}`; each result has today's API keys **except** `poster_url`, plus `fetched_at`. `app.py` adds `poster_url = image_url(poster_file, fetched_at)` and drops `fetched_at`; the MCP direct branch drops `poster_file` and `fetched_at` (its output never had them). `con.row_factory` must be `sqlite3.Row`.
  - `tests/conftest.py::seed_title(con, tmdb_id: int, *, copies: tuple[str, ...] = ("Blu-ray",), watched: tuple[str|None, ...] = (), rating: float|None = None) -> int` — loads `fixtures/tmdb/movie-<id>.json`, maps it with `app.title_from_tmdb`, writes with `app.write_title(con, t, {})`, inserts one physical copy per format and one `watches` row per entry (`None` = dateless), optional `user_ratings`; commits; returns the title id.
- Consumes: Task 1.

- [ ] **Step 1: Write parity test** in `tests/test_mcp_server.py`, parametrized over `forgotten_gems`, `never_watched`, `quick_picks`, `genre_mood`, each with and without `genre="Animation"`. Seed with `seed_title` (Toy Story 862 watched `2025-01-01` rated 9.0; Inside Out 150540 unwatched; WALL·E 10681 rated 8.5; Inception 27205 with `copies=("Blu-ray", "4K UHD")` — Blu-ray inserted first so today's MCP `group_concat` order differs from `sorted_formats`). Call `/api/recommendations?...&limit=50` through `app.test_client()` and `MatineeClient(db_path=...)` with the same params; assert equal lists of `(id, reason, formats)`.
- [ ] **Step 2: Run it** — FAIL on `genre_mood` reasons and on Inception's `formats` order.
- [ ] **Step 3: Create `recommend.py`** by moving the SQL and reason logic out of `app.recommendations()` unchanged (the app's version is the reference behavior), with `today` replacing `dt.date.today()`. Rewrite `app.recommendations()` as: `p = parse_params(request.args)` → `out = recommend(db(), p)` → per result add `poster_url` and drop `fetched_at` → `jsonify(**out)`; `ParamError` → `make_err(400, str(e))`.
- [ ] **Step 4: Rewrite the direct-SQLite branch** of `MatineeClient.get_recommendations` as: lazy `import recommend`; `recommend.recommend(con, recommend.parse_params({...kwargs}))`; drop `poster_file` and `fetched_at`. Keep the REST branch as is (Task 6 extends it). Add `recommend.py` to the `cp` line in `installer/build.sh`.
- [ ] **Step 5: Add unit tests** in `tests/test_recommend.py` covering `parse_params` (`limit` clamps to 1 and 50, `"abc"` limit → 10, bad `max_runtime` ignored, `random` accepts `"1"`, `"true"`, `True`) and `recommend(..., today=date(2026, 11, 6))` giving "Rated 9.0 ★ · Not watched in 22 months" for Toy Story watched 2025-01-01.
- [ ] **Step 6: Run** — `python3 -m pytest -q` → all pass, including every existing test in `tests/test_recommendations.py` unchanged. `python3 -c "import recommend, sys; assert 'flask' not in sys.modules"` → no output.
- [ ] **Step 7: Commit** — `refactor(recommend): extract recommender into recommend.py`, body noting MCP's `genre_mood` reason and `formats` order now match the API.

---

### Task 3: Certification override (schema v7)

**Files:**
- Modify: `app.py:158` (`SCHEMA_VERSION = "7"`), `app.py:migrate()` (append after the v6 block), `app.py:SCHEMA` (add `certification_override TEXT` to `titles` for fresh DBs), `app.py:title_detail()` (~965), new route after `set_title_rating` (~1801)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces:
  - Column `titles.certification_override TEXT`.
  - `PUT /admin/api/titles/<int:title_id>/certification`, `@editor_required`, body `{"certification": "G"|"PG"|"PG-13"|"R"|"NC-17"|"NR"|null}` (case-insensitive, stripped) → `200 {"ok": true, "certification": <effective>, "certification_tmdb": <tmdb or null>, "certification_override": <value or null>}`; invalid → 400 `{"error": "certification must be one of G, PG, PG-13, R, NC-17, NR"}`; unknown id → 404.
  - `title_detail()` output: `certification` = effective, plus `certification_tmdb`, `certification_override`.
- Consumes: `recommend.CERT_ORDER` is not needed yet; define the allowed override set locally as `("G", "PG", "PG-13", "R", "NC-17", "NR")` and replace it with `recommend.CERT_VALUES` in Task 4.

- [ ] **Step 1: Write failing tests**
  - `test_migration_v7_idempotent`: build a v6-shaped `titles` table without the column (same approach as `tests/test_custom_poster.py::test_migration_adds_column_to_v5_idempotently`), run `migrate()` twice; separately `init_db()` on a fresh DB; `PRAGMA table_info(titles)` contains `certification_override` once; `meta.schema == "7"`.
  - `test_certification_override_roundtrip`: add Kantara (848326) via the fixture-backed add flow; `PUT .../certification {"certification": "pg-13"}` → 200, body `certification == "PG-13"`, `certification_tmdb is None`, `certification_override == "PG-13"`; `GET /api/titles/<id>` shows the same three fields; `PUT {"certification": null}` → `certification is None`, `certification_override is None`.
  - `test_certification_override_survives_refresh`: Spirited Away (129, TMDB "PG") overridden to `"G"`, then `POST /admin/api/titles/<id>/refresh` → `certification == "G"`, `certification_tmdb == "PG"`.
  - `test_certification_override_validation`: `"PG13"` → 400 with the exact error; `/admin/api/titles/999999/certification` → 404; with `LAN_EDIT` off and no identity → 403.
- [ ] **Step 2: Run** — FAIL (404 route / missing column).
- [ ] **Step 3: Implement** the migration block (spec §6), the `SCHEMA` column, the route (mirrors `set_title_rating`), and the three `title_detail` fields.
- [ ] **Step 4: Run** — `python3 -m pytest -q` → all pass.
- [ ] **Step 5: Commit** — `feat(db): certification override (schema v7)`

---

### Task 4: `max_cert` rating cap, result fields, empty-result hint

**Files:**
- Modify: `recommend.py`, `app.py` (override route uses `recommend.CERT_VALUES`)
- Test: `tests/test_recommend.py`, `tests/test_recommendations.py`

**Interfaces:**
- Produces (in `recommend.py`):
  - `CERT_ORDER = ("G", "PG", "PG-13", "R", "NC-17")`, `CERT_VALUES = CERT_ORDER + ("NR",)`.
  - `parse_params` adds `max_cert: str|None` — stripped, upper-cased; `""`/absent → `None`; not in `CERT_ORDER` → `ParamError("max_cert must be one of G, PG, PG-13, R, NC-17")`.
  - When set, the WHERE gains `COALESCE(t.certification_override, t.certification) IN (<CERT_ORDER up to max_cert>)`.
  - Every result gains `certification` (effective), `collection_tmdb_id`, `collection_name`.
  - When `count == 0`: `hint` = `"Nothing matched these filters. Try removing a filter or mode=genre_mood."`, and when `max_cert` is set append `" Unrated films are hidden while max_cert is set."`. (Task 5 adds the two franchise-specific hints.)

- [ ] **Step 1: Write failing tests**
  - `test_quick_picks_max_cert_pg`: seed Toy Story 862, Inside Out 150540, A Bug's Life 9487, WALL·E 10681, Kantara 848326, The Dark Knight 155 (none watched/rated). `recommend(con, parse_params({"mode": "quick_picks", "max_cert": "PG"}))` → titles `["Toy Story", "Inside Out", "A Bug's Life"]`, certifications `["G", "PG", "G"]`.
  - `test_max_cert_excludes_unrated_in_every_mode`: with `genre_mood` and `max_cert` in each of `CERT_ORDER`, Kantara never appears; without `max_cert` it does.
  - `test_max_cert_normalizes` (Review Focus 4): `" pg-13 "` → `"PG-13"`, `"pg"` → `"PG"`, `""` → `None`.
  - `test_nr_override_still_hidden` (Review Focus 5): Kantara overridden to `"NR"` → absent under `max_cert="NC-17"`; result `certification` shows `"NR"` without a cap.
  - `test_override_counts_for_cap`: Kantara overridden to `"PG-13"` → present in `genre_mood` with `max_cert="PG-13"`, absent with `"PG"`.
  - `test_empty_hint_mentions_unrated` and, in `tests/test_recommendations.py`, `test_api_bad_max_cert_400` (`/api/recommendations?max_cert=PG13` → 400 with the exact message) and `test_api_results_have_poster_and_cert` (`poster_url` and `certification` present).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** in `recommend.py`; switch the Task 3 route to `recommend.CERT_VALUES`.
- [ ] **Step 4: Run** — `python3 -m pytest -q` → all pass (the Task 2 parity test now also covers `max_cert="PG"`: add it to its parametrization).
- [ ] **Step 5: Commit** — `feat(recommend): add max_cert rating cap`

---

### Task 5: `next_in_franchise` and `start_franchise`

**Files:**
- Create: `tests/fixtures/tmdb/movie-863.json` (Toy Story 2, 1999-10-30, 92 min, G, collection 10194 "Toy Story Collection"), `movie-672.json` (Harry Potter and the Chamber of Secrets, 2002-11-13, 161 min, PG), `movie-673.json` (Harry Potter and the Prisoner of Azkaban, 2004-05-31, 141 min, PG), `movie-674.json` (Harry Potter and the Goblet of Fire, 2005-11-16, 157 min, PG-13) — all three collection 1241 "Harry Potter Collection" — and `movie-920.json` (Cars, 2006-06-08, 117 min, G, collection 87118 "Cars Collection"). Same shape as the existing `movie-*.json`, genres from TMDB, placeholder overview/vote_count/image paths as the README describes.
- Modify: `recommend.py`
- Test: `tests/test_recommend.py`, `tests/test_mcp_server.py` (extend parity parametrization)

**Interfaces:**
- Produces:
  - `MODES` gains `"next_in_franchise"`, `"start_franchise"`.
  - Franchise query per spec §5.3 (CTE `started`, `ROW_NUMBER()` partition by `collection_tmdb_id` ordered `release_date IS NULL, release_date, sort_title`, filters in the outer WHERE, `rn = 1`).
  - `next_in_franchise` order: `last_watch DESC NULLS LAST` (write as `last_watch IS NULL, last_watch DESC`), then `collection_name`. `start_franchise`: collections with ≥ 2 owned titles and not in `started`; order `COALESCE(user_rating, tmdb_vote, 0) DESC, collection_name`. `random` overrides both.
  - Reasons: `"{collection} · Next after {T}"` (T = latest-released watched title released before the pick) else `"{collection} · Before {T} (watched)"` (T = earliest-released watched title after it); `"Start the {collection} · {n} owned"`.
  - Hints when empty: `next_in_franchise` → `"No series in progress. Try mode=start_franchise."`; `start_franchise` → `"No unstarted series with 2+ owned films. Try mode=never_watched."`; the `max_cert` sentence still appended.

- [ ] **Step 1: Add the five fixtures.**
- [ ] **Step 2: Write failing tests** (scenario numbers from spec §10):
  - `test_next_after` (1): own 862, 10193; 862 watched → `[("Toy Story 3", "Toy Story Collection · Next after Toy Story")]`.
  - `test_gap_fill` (2): own 862, 10193; 10193 watched → `[("Toy Story", "Toy Story Collection · Before Toy Story 3 (watched)")]`.
  - `test_sold_disc_counts` (3): 862 seeded with `copies=()` and one watch; own 10193 → Toy Story 3.
  - `test_filter_after_pick` (4): own 671–674; 671–673 watched; with `max_cert="PG"` → no Harry Potter result; without → Goblet of Fire, reason `"Harry Potter Collection · Next after Harry Potter and the Prisoner of Azkaban"`.
  - `test_start_franchise` (5): own 920, 49013, none watched → `[("Cars", "Start the Cars Collection · 2 owned")]`; with only 49013 owned → `[]` and the start-franchise hint.
  - `test_next_in_franchise_empty_hint` (9).
  - `test_one_pick_per_collection_ordered_by_last_watch`: Toy Story started 2026-10-31, Harry Potter started 2026-09-01 → Toy Story's pick first.
  - `test_dateless_watch_starts_series` (Review Focus 1): 671 watched with `None`, own 672 → Chamber of Secrets picked; sorts after a collection with a dated watch.
  - `test_two_copies_one_pick` (Review Focus 2): 10193 with `copies=("4K UHD", "Blu-ray")` → exactly one result, `formats == ["4K UHD", "Blu-ray"]`.
  - `test_null_release_date_sorts_last` (Review Focus 3): set 863's `release_date` to NULL after seeding; own 863 and 10193, 862 watched → Toy Story 3 picked.
- [ ] **Step 3: Run** — FAIL.
- [ ] **Step 4: Implement** in `recommend.py`.
- [ ] **Step 5: Run** — `python3 -m pytest -q` → all pass; add both modes to the parity parametrization (with and without `max_cert="PG"`).
- [ ] **Step 6: Commit** — `feat(recommend): add next_in_franchise and start_franchise modes`

---

### Task 6: MCP tool, server instructions, prompt, REST params

**Files:**
- Modify: `mcp_server.py` (FastMCP construction ~563, tool ~600, prompt ~664, REST branch of `MatineeClient.get_recommendations`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `recommend.MODES`, `recommend.CERT_ORDER` (values only; the `Literal`s are written out in `mcp_server.py` so the REST-only path doesn't import `recommend`).
- Produces:
  - `MatineeClient.get_recommendations(mode="forgotten_gems", genre=None, medium=None, max_runtime=None, min_rating=None, max_cert=None, limit=5, random=False) -> dict`.
  - Tool signature, description and `_READONLY` exactly as spec §8.1–8.2.
  - `SERVER_INSTRUCTIONS: str` per spec §8.3, passed as `FastMCP("matinee", instructions=SERVER_INSTRUCTIONS)`.
  - `what_to_watch(mood: str = "", max_runtime: int = 0, kids: bool = False)`.

- [ ] **Step 1: Write failing tests**
  - `test_rest_recommendations_forwards_params`: monkeypatch `MatineeClient._api_request` to capture the path; `MatineeClient(db_path="").get_recommendations(mode="next_in_franchise", medium="physical", max_cert="PG-13", random=True)` → query string contains `mode=next_in_franchise`, `medium=physical`, `max_cert=PG-13`, `random=1`.
  - `test_tool_schema_enumerates_modes_and_certs`: from the registered tool's input schema (`asyncio.run(mcp_server.mcp.list_tools())`), `mode.enum` == the six modes, `max_cert` allows exactly `CERT_ORDER`, `medium` allows `physical`/`digital`.
  - `test_tool_read_only_annotation`: if `mcp.types.ToolAnnotations` imports, the tool's `annotations.readOnlyHint is True`; otherwise `pytest.skip`.
  - `test_server_instructions_present`: instructions mention `next_in_franchise`, `start_franchise` and `max_cert="PG-13"`.
  - `test_what_to_watch_kids_uses_pg13` (scenario 11): prompt text with `kids=True` contains `max_cert="PG-13"` and the word "unrated"; with `kids=False` it doesn't mention `max_cert`.
  - `test_direct_sqlite_bad_max_cert_raises`: `pytest.raises(ValueError, match="max_cert must be one of")`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Before relying on `instructions=`/`annotations=`, check `inspect.signature(FastMCP.__init__)` and `FastMCP.tool` on the installed `mcp`; guard each with a fallback so `mcp>=1.0.0` still loads.
- [ ] **Step 4: Run** — `python3 -m pytest -q` → all pass. Also, on Python 3.10–3.12, install the floor version in a throwaway venv and import the module: `python3 -m venv /tmp/mcpfloor && /tmp/mcpfloor/bin/pip install -q "mcp==1.0.0" && /tmp/mcpfloor/bin/python -c "import sys; sys.path.insert(0,'.'); import mcp_server"` → no error.
- [ ] **Step 5: Commit** — `feat(mcp): expose max_cert, medium, random and franchise modes; add server instructions`

---

### Task 7: Movie Night grid + rating cap; certification override control

**Files:**
- Modify: `static/app.js` (`openMovieNightModal` ~3070–3180; title view near the `.cert-badge` at ~1043), `static/style.css` (`.movie-night-tabs` ~1849 and the ≤ 600 px block ~2001; new `.movie-night-cap`, `.movie-night-cap-option`, `.cert-override`)
- Test: `tests/test_static_ui.py`

**Interfaces:**
- Consumes: API from Tasks 3–5 (`max_cert` param, `hint`, `certification`, `collection_name`, override route).
- Produces (DOM contract the static tests check):
  - Tabs container `.movie-night-tabs` with `role="tablist"`; six `button.movie-night-tab[data-mode]` in this order: `forgotten_gems` "Forgotten Gems", `quick_picks` "Quick Picks", `never_watched` "Never Watched", `next_in_franchise` "Next in Series", `start_franchise` "Start a Series", `genre_mood` "Surprise Me"; each `role="tab"` with `aria-selected`.
  - Cap: `div.movie-night-cap[role="radiogroup"][aria-label="Rating cap"]` with four `button.movie-night-cap-option[role="radio"][data-cert]`: `""` "Any", `G`, `PG`, `PG-13`; `aria-checked` on the active one.
  - `localStorage` key `matinee.movieNight.maxCert`; reads/writes wrapped in `try/catch`; absent → `""` (Any).
  - Editor-only `select.cert-override` beside the badge, options `""` "TMDB (<value or none>)", then `G`, `PG`, `PG-13`, `R`, `NC-17`, `NR`.

- [ ] **Step 1: Write failing static tests**
  - `test_movie_night_has_six_modes_in_order`: regex over `JS` finds the six `data-mode` values in the order above.
  - `test_movie_night_cap_markup`: `JS` contains `role="radiogroup"`, `matinee.movieNight.maxCert`, and `max_cert=`.
  - `test_movie_night_cap_storage_guarded`: every `localStorage` occurrence in `JS` sits inside a `try {` block (regex: no `localStorage` outside `try`).
  - `test_new_controls_have_tap_targets`: `_rule(".movie-night-cap-option")` and `_rule(".cert-override")` contain `min-height: 44px`.
  - `test_mobile_tabs_three_columns`: the ≤ 600 px block's `.movie-night-tabs` contains `grid-template-columns: repeat(3, 1fr)`.
  - Existing `test_every_css_variable_used_is_defined` keeps passing (tokens only).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Changing tab or cap re-fetches; the cap is appended as `&max_cert=…` only when not Any. The pick card shows the `.cert-badge` and, when present, `collection_name` before the reason. Empty state shows `res.hint` (fallback to today's text), plus "Unrated films are hidden while a rating cap is on." when a cap is set. Certification select: `PUT /admin/api/titles/<id>/certification` with `null` for the TMDB option, toast on success/error, re-render the badge; overridden badges get the same "edited" marker style the custom-cover badge uses.
- [ ] **Step 4: Run** — `python3 -m pytest -q && node -c static/app.js` → all pass.
- [ ] **Step 5: Manual check** — `DATA_DIR=$(mktemp -d) flask --app app run -p 8769` with a few titles added; Chrome DevTools device toolbar at 412 × 915: six tabs in two rows of three, cap row fits on one line, no horizontal scroll (`document.documentElement.scrollWidth === 412`), every new control ≥ 44 px tall.
- [ ] **Step 6: Commit** — `feat(ui): movie night mode grid and rating cap; certification override control`

---

### Task 8: Docs

**Files:**
- Modify: `docs/mcp-setup.md` (tool table; `get_recommendations` modes, `max_cert`, `medium`, `random`; unrated-exclusion rule; filters-after-pick; example phrasings "kids' night", "what's next in Harry Potter", "start a new series"), `README.md` (recommendation modes and the certification override)

- [ ] **Step 1: Update both files.** Example request/response for `next_in_franchise` copied from spec §7.
- [ ] **Step 2: Verify** — `grep -n "next_in_franchise\|start_franchise\|max_cert" docs/mcp-setup.md README.md` shows each term in both files.
- [ ] **Step 3: Commit** — `docs: update mcp-setup and README for recommender modes`

---

### Task 9: Build, deploy to CT 210, smoke-test on Android

- [ ] **Step 1: Full local check** — `python3 -m pytest -q && node -c static/app.js` → all pass. Push the branch; CI green on 3.10–3.13, Alpine, and the JS check.
- [ ] **Step 2: Build** — `installer/build.sh` → `dist/setup-matinee.sh`; `bash dist/setup-matinee.sh --help` or list its payload and confirm `recommend.py` is inside.
- [ ] **Step 3: Pre-deploy on CT 210**
  ```sh
  ops/matinee-backup
  sqlite3 /var/lib/matinee/matinee.db "SELECT value FROM meta WHERE key='schema'"   # expect 6
  ```
- [ ] **Step 4: Deploy** — copy and run `setup-matinee.sh` on CT 210, then `rc-service matinee restart`. Schema query → `7`. `/etc/conf.d/matinee` unchanged.
- [ ] **Step 5: Smoke via curl**
  ```sh
  curl -s 'http://127.0.0.1:8769/api/recommendations?mode=next_in_franchise' | python3 -m json.tool | head -40
  curl -s 'http://127.0.0.1:8769/api/recommendations?mode=quick_picks&max_cert=PG' | python3 -m json.tool | head -40
  curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8769/api/recommendations?max_cert=PG13'   # 400
  sqlite3 /var/lib/matinee/matinee.db "SELECT count(*) FROM titles t
    WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)
      AND COALESCE(t.certification_override, t.certification, '') NOT IN ('G','PG','PG-13','R','NC-17')"
  ```
  Report the last number to Nick (owned titles hidden by any cap until overridden).
- [ ] **Step 6: Android Chrome** — Movie Night: each of the six tabs rolls; cap "PG" excludes a known PG-13 title; empty states show the hint; no sideways scroll. As editor, set and clear an override on an unrated title and confirm it appears/disappears under a cap.
- [ ] **Step 7: MCP** — from Claude Desktop: "what's next in a series we've started?" and "pick something for kids' night" call `get_recommendations` with `next_in_franchise` and `max_cert="PG-13"`.
- [ ] **Step 8: Open the PR** to `main` with the spec and plan linked.
