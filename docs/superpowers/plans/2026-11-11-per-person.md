# People and Watch Attendance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Editors manage a list of people, every watch records who attended (everyone by default), and the recommender, the MCP tools and Ask can pick for an audience such as `kids`, `adults` or `Alice,Paige`.

**Architecture:** Schema v9 adds `people` and `watch_people` and writes no rows. One SQL fragment in `recommend.py` (`SEEN_CTE`) defines who has seen a title, including the read-time rule for watches with no attendees, and every consumer uses it. People, attendance and audience filters exist only under `/admin/api`. The SPA gains a People modal, attendance dots on the title view, an audience row in Movie Night and two grid filters, all for signed-in editors.

**Tech Stack:** Python 3.10–3.13, Flask, SQLite (WAL); vanilla JS ES modules in `static/js/`; pytest; Playwright for the UI smoke test.

**Spec:** `docs/superpowers/specs/2026-11-11-per-person-design.md` is the source of truth. If the code contradicts it, stop and ask; don't redesign.

## Global Constraints

- `SCHEMA_VERSION = "9"`. The DDL is verbatim from spec §4, lives only in `migrate()` after the v8 block, and inserts nothing. No existing table, column or row changes. `user_ratings` is not touched anywhere in this plan.
- No seeding of people, and no real surname, email, hostname or address in the tree (`tests/test_hygiene.py`). Tests and docs use the five first names of spec §6.1.
- `kind` ∈ `adult`, `kid`. `color` ∈ `green`, `blue`, `red`, `pink`, `purple`, mapped in that order to `--p-user1` … `--p-user5`. Name: 1–40 characters, unique case-insensitively, not `everyone`/`adults`/`kids`, no comma, not all digits. At most 8 active people.
- Audience grammar (spec §5): `everyone`, `adults`, `kids`, or comma-separated names or ids. Strict semantics: nobody in the audience has seen the title.
- Attendance values: `recorded`, `assumed_everyone`, `assumed_adults`. The adults-only rule: `date_bucket = 'over_10_years'` or `watched_date < date('now', '-10 years')`.
- A log request with no `attendees` records all active people. An empty list is 400. With no people, nothing is recorded and every response is as it is today.
- Privacy: `/api` never returns a person's name, `attendance`, `attendees`, `seen_by` or `audience`. `audience`, `unseen_by` and `attendance` on `/api` answer 400 `Sign in to pick for specific people.` There is no `/api/people`.
- With no `audience`, `recommend()` output is unchanged: every existing test in `tests/test_recommend.py` and `tests/test_recommendations.py` passes untouched.
- Error bodies are `{"error": "<message>"}` with the exact strings in spec §5 and §6.
- No new runtime dependency, no new setting, no build step. New JS follows CLAUDE.md rule 1 (one `export { … };` at the end; `core.js` and `icons.js` import nothing).
- Tap targets ≥ 44 px; no horizontal scroll at 412 px or 360 px; dot color pairs meet spec §7.6.
- Commits: Conventional Commits, one per task, author `SDNick484 <SDNick484@users.noreply.github.com>`, on `feat/per-person`. `python -m pytest -q` is green after every task.

## Review Focus

1. **A person is deactivated between opening the title view and tapping "+ Watched Today"** (the SPA sends a stale id). The server accepts an explicit inactive id rather than failing the log; only the *default* set skips inactive people. Test in Task 3 (`test_explicit_inactive_person_is_accepted`).
2. **Two people share an initial or a color** (Alice and a guest "Ann", both pink). The dots stay distinguishable by `aria-label` and title text, and nothing keys on the initial. Test in Task 7 (`test_dots_carry_full_name_labels`).
3. **A watch with no date and no bucket** (`watched_date IS NULL`, `date_bucket IS NULL`) and no attendees. It is `assumed_everyone`, not an SQL NULL comparison that drops it from `seen`. Test in Task 2 (`test_dateless_unresolved_watch_counts_for_everyone`).
4. **A name with characters that matter to SQL or HTML** (`O'Brien`, `<b>Zoe</b>`). Stored and returned verbatim, resolved by `resolve_audience`, escaped in the SPA. Tests in Task 1 (`test_name_round_trips_verbatim`) and Task 7 (static `esc()` check).
5. **`localStorage` holds an audience naming a person who has since been deleted.** Movie Night falls back to Everyone instead of showing a 400. Test in Task 9 (`test_movie_night_stale_audience_falls_back`).

## File Structure

| File | Responsibility |
|---|---|
| `app.py` | v9 migration; people routes; `attendees` on watch routes; private watch and title fields; `audience`, `unseen_by`, `attendance` query parameters. |
| `recommend.py` | `SEEN_CTE`, `resolve_audience`, audience-aware modes. Still imports nothing from Flask. |
| `mcp_server.py` | `list_people`; `audience`, `unseen_by`, `attendees` arguments; names in `get_movie`; older-server refusal. |
| `ask.py` | Tool schemas, `list_people`, system prompt. |
| `static/js/people.js` (new) | People modal, and the shared dot renderers (`personDotsHTML`, `personTogglesHTML`). |
| `static/js/core.js` | `state.people`. |
| `static/js/detail.js`, `movie-night.js`, `grid.js`, `router.js`, `main.js` | Call sites. |
| `static/style.css` | Dot, toggle and People modal styles. |
| `tests/conftest.py` | `seed_people`; `people.js` in `JS_MODULES`. |

---

### Task 1: Schema v9 and the people API

**Files:**
- Modify: `app.py` (`SCHEMA_VERSION`; end of `migrate()`; new routes after the locations routes), `tests/conftest.py`
- Create: `tests/test_people.py`

**Interfaces:**
- Produces: tables `people`, `watch_people` and index `watch_people_person` (spec §4, verbatim).
- Produces: `app.PERSON_KINDS = ("adult", "kid")`, `app.PERSON_COLORS = ("green", "blue", "red", "pink", "purple")`, `app.MAX_ACTIVE_PEOPLE = 8`.
- Produces: `app.person_out(row) -> dict` with keys `id name kind color sort_order active watch_count` (`active` a bool).
- Produces: `app.list_people(con, active_only=False) -> list[dict]`, ordered `sort_order, id`.
- Produces routes, all `@editor_required`: `GET /admin/api/people`, `POST /admin/api/people`, `PUT /admin/api/people/<int:person_id>`, `DELETE /admin/api/people/<int:person_id>`, with the bodies and messages of spec §6.1.
- Produces: `conftest.seed_people(con) -> dict[str, int]` inserting Nick (adult, green), Lauren (adult, blue), Midge (kid, red), Alice (kid, pink), Paige (kid, purple) with `sort_order` 0–4, returning name → id.

- [ ] **Step 1: Write the failing tests** in `tests/test_people.py`. Fixture `client` as in `tests/test_privacy.py`, with `LAN_EDIT=True` for editor calls and a second fixture for anonymous.

```python
def test_schema_v9_is_additive_and_idempotent(client):
    con = db()
    tid = seed_title(con, 155, watched=("2026-11-07", None), rating=9.5)
    before = [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("watches", "user_ratings")]
    matinee.migrate(con); matinee.migrate(con)
    assert [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("watches", "user_ratings")] == before
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "9"
    assert con.execute("SELECT count(*) FROM people").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM watch_people").fetchone()[0] == 0

def test_create_list_update(client):
    r = client.post("/admin/api/people", json={"name": "Midge", "kind": "kid", "color": "red"})
    assert r.status_code == 200 and r.json["person"]["name"] == "Midge" and r.json["person"]["active"] is True
    pid = r.json["person"]["id"]
    assert client.put(f"/admin/api/people/{pid}", json={"active": False}).json["person"]["active"] is False
    assert [p["name"] for p in client.get("/admin/api/people").json["people"]] == ["Midge"]

@pytest.mark.parametrize("body,msg", [
    ({"name": "", "kind": "kid", "color": "red"}, "A name is required (up to 40 characters)."),
    ({"name": "x" * 41, "kind": "kid", "color": "red"}, "A name is required (up to 40 characters)."),
    ({"name": "Kids", "kind": "kid", "color": "red"}, "That name can't be used. Pick a name without commas that isn't everyone, adults or kids."),
    ({"name": "A,B", "kind": "kid", "color": "red"}, "That name can't be used. Pick a name without commas that isn't everyone, adults or kids."),
    ({"name": "42", "kind": "kid", "color": "red"}, "That name can't be used. Pick a name without commas that isn't everyone, adults or kids."),
    ({"name": "Zoe", "kind": "teen", "color": "red"}, "kind must be adult or kid."),
    ({"name": "Zoe", "kind": "kid", "color": "teal"}, "color must be one of green, blue, red, pink, purple."),
])
def test_validation(client, body, msg): ...            # 400 and r.json == {"error": msg}

def test_duplicate_name_case_insensitive(client): ...   # "midge" after "Midge" → 400 "A person named Midge already exists."
def test_ninth_active_person_refused(client): ...       # 400 "At most 8 active people."; a 9th inactive one is allowed via PUT
def test_delete_without_attendance(client): ...         # {"ok": True, "deleted_id": pid}
def test_delete_with_attendance_refused(client): ...    # insert one watch_people row → 400 "Midge is on 1 watches. Deactivate them instead."
def test_unknown_id_404(client): ...                    # PUT and DELETE → 404 {"error": "No such person"}
def test_name_round_trips_verbatim(client): ...         # "O'Brien" and "<b>Zoe</b>" come back unchanged from GET
def test_read_key_can_list_not_write(client): ...       # GET 200; POST 403 "The read key can only read"
def test_anonymous_401_and_no_public_route(anon): ...   # GET /admin/api/people → 401; GET /api/people → 404
def test_sort_order_defaults_to_next(client): ...       # three creates → 0, 1, 2
```

  A second migration test builds a database from the v8 `SCHEMA` text plus the v8 `ask_log` DDL, sets `meta.schema` to `'8'`, inserts a title, a watch and a rating, runs `migrate()`, and asserts both new tables exist and the three rows are intact.

- [ ] **Step 2:** Run `python -m pytest -q tests/test_people.py`. Expected: FAIL (no `people` table, 404 on the routes).
- [ ] **Step 3: Implement** per Interfaces. Append the DDL under `# v9: people and watch attendance (no rows written)`. The delete message uses the count of `watch_people` rows. The pluralisation is literal ("1 watches") to keep one message; do not add a plural helper.
- [ ] **Step 4:** Run `python -m pytest -q`. Expected: green; `tests/test_app.py`'s schema assertions updated from `"8"` to `"9"` where they pin the version.
- [ ] **Step 5: Commit:** `feat(db): add people and watch_people (schema v9) with a people API`

### Task 2: Who has seen it — `SEEN_CTE`, `resolve_audience`, audience-aware modes

**Files:**
- Modify: `recommend.py`
- Test: `tests/test_recommend.py`

**Interfaces:**
- Consumes: the Task 1 tables. `recommend.py` must not import `app`.
- Produces: `recommend.SEEN_CTE: str`, the `seen(title_id, person_id, watched_date) AS (…)` fragment of spec §4.1, without the leading `WITH`.
- Produces: `recommend.has_people(con) -> bool`, False when the `people` table is missing (a pre-v9 database) or empty.
- Produces: `recommend.resolve_audience(con, raw: str) -> dict` → `{"label": str, "people": [{"id": int, "name": str}]}`. `label` is the keyword, or the names joined with `", "`. Raises `ParamError` with the messages of spec §5.
- Produces: `recommend.attendance_of(watch_row, has_attendees: bool) -> str` returning one of the three attendance values, using the same ten-year rule as `SEEN_CTE` (`today` injectable).
- Changes: `parse_params` passes `audience` through as a cleaned string or `None`. `recommend(con, p, today=None)` resolves it; with an audience, the output gains `"audience"` and each result gains `"seen_by": [names]` (everyone with a `seen` row, in people order), and the modes follow spec §5.1.

- [ ] **Step 1: Write the failing tests** in `tests/test_recommend.py`, using `seed_people` and direct `INSERT INTO watch_people`:

```python
def test_resolve_audience_keywords_names_ids(con):
    ids = seed_people(con)
    assert [p["name"] for p in recommend.resolve_audience(con, "kids")["people"]] == ["Midge", "Alice", "Paige"]
    assert [p["name"] for p in recommend.resolve_audience(con, "ADULTS")["people"]] == ["Nick", "Lauren"]
    assert [p["name"] for p in recommend.resolve_audience(con, " alice , PAIGE ")["people"]] == ["Alice", "Paige"]
    assert [p["id"] for p in recommend.resolve_audience(con, f"{ids['Nick']},Lauren")["people"]] == [ids["Nick"], ids["Lauren"]]
    assert recommend.resolve_audience(con, "kids")["label"] == "kids"
    assert recommend.resolve_audience(con, "alice,paige")["label"] == "Alice, Paige"

@pytest.mark.parametrize("raw,msg", [
    ("Bob", "Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige."),
    (" , ", "audience is empty."),
])
def test_resolve_audience_errors(con, raw, msg): ...
def test_resolve_audience_no_people(con): ...            # "No people are set up yet. Add them under People in the admin view."
def test_resolve_audience_no_kids(con): ...              # only adults seeded → "No kids are set up."
def test_inactive_person_by_name_only(con): ...          # deactivated Paige: "kids" omits her, "Paige" resolves her

def test_never_watched_for_kids_is_strict(con):
    # Toy Story 3 seen by all five; Inside Out seen by Nick+Lauren; Dark Knight seen by Midge only
    out = recommend.recommend(con, recommend.parse_params({"mode": "never_watched", "audience": "kids"}))
    assert [r["title"] for r in out["results"]] == ["Inside Out"]
    assert out["audience"]["label"] == "kids" and out["results"][0]["seen_by"] == ["Nick", "Lauren"]

def test_pair_audience_ignores_the_third(con): ...       # audience "Alice,Paige" also offers The Dark Knight
def test_unresolved_recent_watch_counts_for_everyone(con): ...   # watch 2 years ago, no rows → not offered to kids
def test_unresolved_over_ten_years_counts_for_adults_only(con): ...  # bucket over_10_years AND a date 11 years back → offered to kids, not to adults
def test_dateless_unresolved_watch_counts_for_everyone(con): ...     # watched_date NULL, bucket NULL
def test_next_in_franchise_per_audience(con): ...        # spec §8 scenario: "Alice,Paige" → third film, "kids" → fifth; reason names the audience's neighbour
def test_start_franchise_per_audience(con): ...          # a series the adults started is still "unstarted" for kids
def test_forgotten_gems_uses_audience_last_watch(con): ... # rated 9.0; adults watched last month, kids two years ago → in for kids, out for adults
def test_franchise_filter_still_after_pick_with_audience(con): ...
def test_no_audience_output_has_no_new_keys(con): ...    # "audience" not in out; "seen_by" not in any result
def test_no_people_table_is_no_people(tmp_path): ...     # a v8-shaped DB: has_people False; resolve_audience raises the no-people message
```

- [ ] **Step 2:** Run `python -m pytest -q tests/test_recommend.py`. Expected: the new tests FAIL; the existing ones pass.
- [ ] **Step 3: Implement.** Keep the no-audience SQL textually as it is today and branch on `audience_ids`: with an audience, the `NOT EXISTS (… FROM watches …)` and `MAX(watched_date)` predicates read from `seen` restricted to `person_id IN (…)`, and `_STARTED_CTE` and `_FRANCHISE_CTE` are built by a function taking the id list. `SEEN_CTE` is prefixed with `WITH` in plain modes and merged into the existing `WITH` chain in franchise modes. `_next_reason` takes the id list so its neighbours are the audience's watches. The `never_watched` reason with an audience is `"New to <names> · TMDB 7.9 ★"` (names joined with commas and a final "and"; the TMDB part as today).
- [ ] **Step 4:** Run `python -m pytest -q`. Expected: green, with `tests/test_recommend.py` and `tests/test_recommendations.py` unmodified apart from additions.
- [ ] **Step 5: Commit:** `feat(recommend): audience-aware picks from a shared seen() fragment`

### Task 3: Attendance on watches and on the title detail

**Files:**
- Modify: `app.py` (`PRIVATE_WATCH`, `watch_out`, `title_detail`, `add_watch`, new route)
- Create: `tests/test_attendance.py`
- Modify: `tests/test_privacy.py`

**Interfaces:**
- Consumes: Task 1 `list_people`; Task 2 `recommend.SEEN_CTE`, `recommend.attendance_of`.
- Produces: `app.resolve_people(con, items) -> list[int]` accepting ids or names (case-insensitive, active or not), order-preserving and deduplicated; raises a 400 with `Unknown person: <x>. People: <names>.`; an empty list raises 400 `A watch needs at least one person.`
- Produces: `app.set_attendees(con, watch_id, person_ids) -> None` (delete then insert, caller commits).
- Changes: `POST /admin/api/titles/<id>/watches` accepts `attendees` (spec §6.2) and returns `attendance` and `attendees`.
- Produces: `PUT /admin/api/titles/<int:title_id>/watches/<int:watch_id>/attendees` (spec §6.3).
- Changes: `PRIVATE_WATCH = ("notes", "attendance", "attendees")`. Private `title_detail` adds the two keys to each watch and `seen_by` (ids) to the title; the public one adds nothing.

- [ ] **Step 1: Write the failing tests.** `tests/test_attendance.py` (editor client, `seed_people`, *The Dark Knight* 155):

```python
def test_log_without_attendees_records_all_active(client): ...   # Paige deactivated → rows for the other four; response attendance "recorded"
def test_log_with_ids_and_with_names(client): ...                 # [1, 2] and ["nick", "LAUREN"] give the same rows
def test_log_empty_list_400_and_writes_nothing(client): ...       # {"error": "A watch needs at least one person."}; count(watches) unchanged
def test_log_unknown_person_400_and_writes_nothing(client): ...   # "Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige."
def test_log_with_no_people_writes_no_rows(client): ...           # no seed_people → 200, watch_people empty, attendance "assumed_everyone", attendees []
def test_explicit_inactive_person_is_accepted(client): ...
def test_put_attendees_replaces_and_resolves(client): ...         # an unresolved watch becomes "recorded" with [1, 2]
def test_put_attendees_wrong_title_404(client): ...               # {"error": "No such watch entry"}
def test_delete_watch_removes_rows(client): ...
def test_attendance_values_on_detail(client): ...                 # bucket over_10_years → assumed_adults, attendees = adult ids;
                                                                  # date 11 years back → assumed_adults; 9 years back → assumed_everyone
def test_title_seen_by(client): ...                               # spec §6.4 example: seen_by == [nick_id, lauren_id]
def test_read_key_cannot_put_attendees(client): ...
```

  `tests/test_privacy.py` additions: anonymous `/api/titles/<id>` contains none of `attendance`, `attendees`, `seen_by` (search the serialized JSON for the key names) and none of the five seeded names; the existing `test_public_view_guards_every_notes_use` style check is extended so every use of `attendees` in `app.py` is on a private path.

- [ ] **Step 2:** Run `python -m pytest -q tests/test_attendance.py tests/test_privacy.py`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces. Validate `attendees` before the `INSERT INTO watches`, so a bad request writes nothing. Effective attendees for an assumed watch are all people, or all adults, by `attendance_of`.
- [ ] **Step 4:** Run `python -m pytest -q`. Expected: green.
- [ ] **Step 5: Commit:** `feat(watches): record attendees, default everyone, editor-only`

### Task 4: Query parameters — `audience`, `unseen_by`, `attendance`

**Files:**
- Modify: `app.py` (`recommendations`, `list_titles`)
- Test: `tests/test_recommendations.py`, `tests/test_privacy.py`

**Interfaces:**
- Consumes: Task 2 `resolve_audience`, `SEEN_CTE`, `has_people`.
- Changes: `recommendations(private)` and `list_titles(private)` answer 400 `Sign in to pick for specific people.` when `private` is false and any of `audience`, `unseen_by`, `attendance` is present in the query. On the private path: `audience` flows through `parse_params`; `unseen_by=<audience>` adds "no `seen` row for these people"; `attendance=unknown` adds "has a watch with no `watch_people` rows" and matches nothing when `has_people` is false. `ParamError` → 400 with its message.

- [ ] **Step 1: Write the failing tests:**

```python
def test_admin_recommendations_audience(client): ...      # /admin/api/recommendations?mode=never_watched&audience=kids → Inside Out; body has "audience"
def test_public_audience_is_400(anon): ...                # /api/recommendations?audience=kids → 400 {"error": "Sign in to pick for specific people."}
def test_public_unseen_by_and_attendance_are_400(anon): ...
def test_unknown_person_400_lists_people(client): ...
def test_titles_unseen_by(client): ...                    # ?unseen_by=Alice,Paige lists The Dark Knight and Inside Out, not Toy Story 3
def test_titles_attendance_unknown(client): ...           # only the title with an unresolved watch; [] when no people exist
def test_public_recommendations_unchanged(anon): ...      # no audience → same keys as before; no person name anywhere in the body
```

- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** `CARD_SELECT` gains the `seen` CTE only when `unseen_by` is used, so the default grid query is unchanged.
- [ ] **Step 4:** Run `python -m pytest -q`. Expected: green.
- [ ] **Step 5: Commit:** `feat(api): audience and unseen_by filters for editors`

### Task 5: MCP tools

**Files:**
- Modify: `mcp_server.py`, `docs/mcp-setup.md`
- Test: `tests/test_mcp_server.py`, `tests/test_mcp_http.py` (tool lists per level)

**Interfaces:**
- Consumes: Tasks 1–4 routes and `recommend` functions.
- Produces on `MatineeClient`: `list_people() -> list[dict]` (keys `id name kind color active`); `get_recommendations(..., audience: Optional[str] = None)`; `search_collection(..., unseen_by: Optional[str] = None)`; `log_watch(..., attendees: Optional[List[str]] = None)`; `get_movie` watches gain `attendance` and `attendees` (names) and the movie gains `seen_by` and `not_seen_by` (names; `not_seen_by` covers active people).
- Produces: tool `list_people`, registered at every write level, annotated read-only and idempotent.
- Produces: `MatineeClient._require_people_api()`, called in REST mode whenever `audience` or `unseen_by` is set: `GET {read_prefix}/people`; a 404 raises `MatineeError("This Matinee server is too old to pick for specific people. Update Matinee.")`. `get_recommendations` additionally raises the same error when the response has no `audience` key.
- Changes: `SERVER_INSTRUCTIONS` gains the audience line of spec §8; `READONLY_INSTRUCTIONS` must not mention `log_watch` attendees.

- [ ] **Step 1: Write the failing tests:**

```python
def test_list_people_db_and_rest(test_env, monkeypatch): ...
def test_audience_db_matches_rest(test_env, monkeypatch): ...        # same titles for audience="kids" in both modes
def test_unseen_by_db_matches_rest(test_env, monkeypatch): ...
def test_get_movie_names_attendees(test_env): ...                    # attendees == ["Nick", "Lauren"]; not_seen_by == ["Midge", "Alice", "Paige"]
def test_log_watch_sends_attendees(monkeypatch): ...                 # payload["attendees"] == ["Nick", "Lauren"]; key absent when not given
def test_pre_v9_database_has_no_people(tmp_path): ...                # list_people() == []; audience raises the no-people message; plain reads still work
def test_older_rest_server_is_refused(monkeypatch): ...              # fake 404 on /people → MatineeError; fake results never returned
def test_rest_response_without_audience_echo_is_refused(monkeypatch): ...
def test_tool_lists_per_level(): ...                                 # none: 7 read tools incl. list_people; log: +rate_movie, log_watch; all: 10
def test_readonly_instructions_name_no_write_tool(): ...
```

- [ ] **Step 2:** Run `python -m pytest -q tests/test_mcp_server.py tests/test_mcp_http.py`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces. DB mode guards every `people` query with `recommend.has_people(con)`. Update `docs/mcp-setup.md`: the new tool, the three arguments, the older-server refusal, and that the read key sees people and attendance.
- [ ] **Step 4:** Run `python -m pytest -q`. Expected: green.
- [ ] **Step 5: Commit:** `feat(mcp): audience, unseen_by, attendees and list_people`

### Task 6: Ask

**Files:**
- Modify: `ask.py`, `README.md` (the Ask "what is sent" paragraph)
- Test: `tests/test_ask.py`

**Interfaces:**
- Consumes: Task 5 `MatineeClient` signatures.
- Changes: `TOOLS` gains `("list_people", "list_people", "The family members and whether each is an adult or a kid.", _obj())`; the `get_recommendations` schema gains `audience` (string; description: `everyone, adults, kids, or names such as "Alice,Paige". Only films nobody in the audience has seen count as unwatched.`); the `search_collection` schema gains `unseen_by` (string, same grammar).
- Changes: `SYSTEM` replaces its kids' line with the line in spec §9.

- [ ] **Step 1: Write the failing tests:** update `test_tool_defs_read_only` to expect `["search_collection", "get_movie", "where_is", "get_recommendations", "get_collection_stats", "list_people", "show_results"]`; `test_schemas_match_client_signatures` now covers `audience` and `unseen_by`; add `test_system_prompt_maps_groups_to_audience` (`'audience="kids"'` and `'audience="adults"'` in `ask.SYSTEM`) and `test_run_tool_passes_audience` (a scripted `get_recommendations {"mode": "never_watched", "audience": "kids"}` returns *Inside Out* from the seeded DB, and its id is hydrated by `show_results`).
- [ ] **Step 2:** Run `python -m pytest -q tests/test_ask.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** A `ParamError` from an unknown person already passes through `run_tool` as the tool error text; add no new handling.
- [ ] **Step 4:** Run `python -m pytest -q`. Expected: green.
- [ ] **Step 5: Commit:** `feat(ask): pick for an audience`

### Task 7: UI — people state, dots, and the People modal

**Files:**
- Create: `static/js/people.js`
- Modify: `static/js/core.js` (`state.people = []`), `static/js/router.js` (header button, admin load), `static/style.css`, `tests/conftest.py` (`JS_MODULES`), `app.py` only if `_asset_files()` lists modules by name
- Test: `tests/test_static_ui.py`, `tests/test_js_modules.py` (no edit expected; it must pass)

**Interfaces:**
- Consumes: `GET/POST/PUT/DELETE /admin/api/people`.
- Produces in `people.js`:
  - `loadPeople() -> Promise<void>`: fills `state.people`; on any error leaves it `[]`.
  - `activePeople() -> Array`.
  - `personDotsHTML(ids, {assumed} = {}) -> string`: 16 px display dots, each `<span class="person-dot person-<color>" title="<name>" aria-label="<name>">` with the initial; with `assumed` set, a grey `?` dot followed by the text `assumed everyone` or `assumed adults`.
  - `personTogglesHTML(selectedIds, groupLabel) -> string`: one `<button type="button" class="person-toggle person-<color>" data-person-id aria-pressed aria-label="<name>">` per active person inside a `role="group"`.
  - `readToggles(root) -> number[]`: ids with `aria-pressed="true"`.
  - `openPeopleModal()`: the manager of spec §7.1.
- Produces CSS: `.person-dot`, `.person-toggle` (min 44×44 px), `.person-green|blue|red|pink|purple` mapping to `--p-user1..5`, `.person-unknown`, `.people-row`.

- [ ] **Step 1: Write the failing tests** in `tests/test_static_ui.py`:

```python
def test_people_module_is_listed(): ...                  # "people.js" in JS_MODULES and on disk
def test_people_button_is_admin_only(): ...              # the header button and loadPeople() sit inside isAdmin branches in router.js
def test_person_names_are_escaped(): ...                 # every ${…name…} in people.js is wrapped in esc(
def test_dots_carry_full_name_labels(): ...              # personDotsHTML and personTogglesHTML set aria-label from the name, not the initial
def test_person_colors_pass_contrast(): ...              # for each --p-user1..5: _contrast(color, "#0b0b0e") >= 4.5 and _contrast(color, "#16161c") >= 3
def test_person_toggle_tap_target(): ...                 # .person-toggle min-width and min-height are 44px
def test_people_css_uses_defined_properties(): ...       # existing helper
def test_people_code_has_no_browser_storage(): ...       # people.js has no localStorage/sessionStorage
```

- [ ] **Step 2:** Run `python -m pytest -q tests/test_static_ui.py tests/test_js_modules.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** If a `--p-user*` value fails either contrast rule, adjust the token in `static/style.css` and `docs/tokens.html` together and record the old and new values in the task report.
- [ ] **Step 4:** Run `python -m pytest -q && for f in static/js/*.js; do node --check "$f"; done`. Expected: green.
- [ ] **Step 5: Commit:** `feat(ui): People manager and person dots`

### Task 8: UI — who watched, on the title view

**Files:**
- Modify: `static/js/detail.js`, `static/style.css`
- Create: `tests/test_people_ui.py`
- Modify: `.github/workflows/ci.yml` (the UI smoke job runs `tests/test_ask_ui.py tests/test_people_ui.py`)

**Interfaces:**
- Consumes: Task 7 `personTogglesHTML`, `personDotsHTML`, `readToggles`, `state.people`; Task 3 request and response shapes.
- Changes: the three log paths (Watched Today, Log Date, buckets) add `attendees: readToggles(row)` to the body when `activePeople().length > 0`; the selection resets after a successful log; with nothing selected the log buttons are disabled.
- Produces: an inline row editor opened by tapping a watch row's dots, with Save (`PUT …/attendees`) and Cancel.

- [ ] **Step 1: Write the failing tests.** Static, in `tests/test_static_ui.py`: the "Who watched" row and the row editor render only when `isAdmin` and people exist; all three log calls include `attendees`. Playwright, in `tests/test_people_ui.py` (same harness as `tests/test_ask_ui.py`, viewport 412×915, `/admin/api/me`, `/admin/api/people` and `/admin/api/titles/1` stubbed with the five people and *The Dark Knight* with the two watches of spec §6.4):
  - five `.person-toggle` buttons render under `#btn-watched-today`, all `aria-pressed="true"`;
  - after tapping M, A and P, tapping "+ Watched Today" posts a body whose `attendees` is `[1, 2]`;
  - the old watch row shows the `?` dot and the text `assumed adults`;
  - opening the row editor, toggling Midge on and saving sends `PUT /admin/api/titles/1/watches/12/attendees` with `[1, 2, 3]`;
  - `document.documentElement.scrollWidth <= 412`, and `<= 360` at a 360 px viewport.
- [ ] **Step 2:** Run `python -m pytest -q tests/test_static_ui.py tests/test_people_ui.py`. Expected: FAIL. The Playwright test must run, not skip, in the implementing session.
- [ ] **Step 3: Implement** per spec §7.2 and §7.3.
- [ ] **Step 4:** Run `python -m pytest -q && for f in static/js/*.js; do node --check "$f"; done`. Expected: green. Record the measured `scrollWidth` at 412 and 360 in the task report.
- [ ] **Step 5: Commit:** `feat(ui): choose who watched when logging, and fix it later`

### Task 9: UI — Movie Night audience and grid filters

**Files:**
- Modify: `static/js/movie-night.js`, `static/js/grid.js`, `static/style.css`
- Test: `tests/test_static_ui.py`, `tests/test_people_ui.py`

**Interfaces:**
- Consumes: Task 4 query parameters; Task 7 helpers.
- Produces: a "Who's watching" radiogroup (Everyone, Adults, Kids, Custom) shown when `isAdmin` and people exist; `localStorage` key `matinee.movieNight.audience` holding `everyone`, `adults`, `kids` or a comma-separated id list, read and written inside `try/catch`.
- Produces: grid query parameters `unseen_by` and `attendance=unknown`, set from two editor-only groups in the Watched popover and reflected in its label and in the active-filter count.

- [ ] **Step 1: Write the failing tests.** Static: the audience row is inside an `isAdmin` branch; storage access is guarded; `test_movie_night_has_six_modes_in_order` still passes; the grid's new groups are editor-only. Playwright additions in `tests/test_people_ui.py`: choosing Kids requests `/admin/api/recommendations?…&audience=kids`; `test_movie_night_stale_audience_falls_back` preloads `localStorage` with `99`, stubs the first recommendations call to answer 400 `Unknown person: 99. …`, and asserts the picker re-requests with `audience=everyone`, shows Everyone selected and shows no error text.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** per spec §7.4 and §7.5. The stale-audience fallback happens once per modal open, so a second 400 is shown as an error.
- [ ] **Step 4:** Run `python -m pytest -q && for f in static/js/*.js; do node --check "$f"; done`. Expected: green.
- [ ] **Step 5: Commit:** `feat(ui): pick for an audience in Movie Night and filter the grid by who has seen it`

### Task 10: Docs, review, PR, deploy and phone smoke test

**Files:**
- Modify: `docs/vision.md`, `README.md`; mark this spec's Status line as merged in the release commit

- [ ] **Step 1:** `docs/vision.md`: add the People paragraph and the principle 6 note (spec §11). `README.md`: a "People and who watched" section (what it is, that it is editor-only, how old watches are treated, the audience values). No personal hostname, address or email. Commit: `docs: people, watch attendance and audiences`.
- [ ] **Step 2:** Whole-branch review against the spec (subagent-driven-development's final review, or one fresh reviewer for native execution). Fix findings in follow-up commits.
- [ ] **Step 3:** Run and report the Definition of done from `CLAUDE.md`: `python -m pytest -q`; `for f in static/js/*.js; do node --check "$f"; done`; `sh installer/build.sh && sh -n dist/setup-matinee.sh`. Push `feat/per-person` and mark the draft PR ready. Do not merge; Nick merges. Wait for CI (Python 3.10–3.13, Alpine, lint, UI smoke).
- [ ] **Step 4 (Nick, after merge): deploy.** Back up first (`sh /etc/periodic/daily/matinee-backup`), run `setup-matinee.sh`, then check:
  - `sqlite3 /var/lib/matinee/matinee.db "SELECT v FROM meta WHERE k='schema'"` → `9`;
  - `sqlite3 /var/lib/matinee/matinee.db "SELECT count(*) FROM watches; SELECT count(*) FROM user_ratings"` matches the counts taken before the upgrade;
  - the app looks exactly as before (no People data yet, no dots).
- [ ] **Step 5 (Nick): set up people.** In the admin view open People and add the five family members with their kinds and colors.
- [ ] **Step 6 (Nick): Android smoke test** on the Pixel, signed in:
  1. Open a film the adults watched alone, turn the three kids' dots off, tap "+ Watched Today"; the new row shows two dots.
  2. Open a film with an old watch; it shows "? assumed everyone" (or "assumed adults" for a "10+ Years" one); fix it with the row editor.
  3. Movie Night → Kids → Never Watched offers only films none of the kids has seen; switching to Adults changes the pick.
  4. Grid → Watched → "Who watched: unknown" lists the old watches still to resolve.
  5. In a private tab (not signed in), a title page shows dates only, and `https://movies.example.com/api/people` is 404.
  6. From the Claude app: "what's something the girls haven't seen?" calls `get_recommendations` with `audience="kids"`.
