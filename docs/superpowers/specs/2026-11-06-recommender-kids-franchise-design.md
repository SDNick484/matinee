# Design Spec: Recommender — Kids' Cap, Next in Franchise, Start a Series

**Date:** 2026-11-06
**Branch:** `feat/recommender-kids-franchise`
**Schema:** v6 → v7

---

## 1. Intent

Movie night has two recurring questions the recommender can't answer yet:

1. *"What can the kids watch?"* — a rating cap that works with every existing mode.
2. *"What's next in the series we're watching?"* and *"Which series should we start?"*

Both must give **identical answers** in the web UI (`/api/recommendations`) and through the MCP server's `get_recommendations`, in both its REST and direct-SQLite modes.

### Goals

- `max_cert` filter (G < PG < PG-13 < R < NC-17) usable with any mode.
- Editor override for a title's US certification, so a family-friendly unrated disc can still appear under a cap.
- New modes `next_in_franchise` and `start_franchise`, one pick per TMDB collection.
- One recommender implementation shared by `app.py` and `mcp_server.py`.
- MCP changes that follow [awesome-mcp-best-practices](https://github.com/lirantal/awesome-mcp-best-practices).
- Fix the existing drift between the two recommender copies (§3).

### Non-goals

- Per-person watch history ("everyone hasn't seen it") — backlog item, needs people.
- Non-US rating systems.
- ML ranking (project non-goal).

## 2. Decisions from brainstorming

| # | Question | Decision |
|---|----------|----------|
| 1 | Share the SQL? | Yes: new stdlib-only module `recommend.py`, plus a parity test. |
| 2 | Unrated titles under a cap | Excluded (NULL, `NR`, `Unrated`, anything non-standard). Editors can override a title's certification (schema v7). |
| 3 | Unwatched entry earlier than a watched one | Suggest the earliest unwatched owned entry (gap fill); reason text says which case it is. |
| 4 | Collections with no watches | Excluded from `next_in_franchise`; covered by the new `start_franchise` mode. |
| 5 | Default cap | API: none. Movie Night UI: **Any** (everything shown until filtered). Anything that asks for "kids" without naming a rating (MCP instructions, `what_to_watch(kids=True)`): **PG-13**. |

## 3. Current state and drift (verified on `main` @ `d12af4d`)

- `SCHEMA_VERSION` is **"6"** (project notes say v5).
- `titles` already has `certification`, `collection_tmdb_id`, `collection_name`, `release_date`, and index `titles_collection`. The new modes need no migration; only the override does.
- `certification` is set from TMDB by `us_certification()` and is display-only (`app.js` `.cert-badge`). There is no way to edit it today.
- `recommendations()` (`app.py:1282`) is registered under `/api` and `/admin/api`; its `private` argument is unused, so both answer identically.
- `mcp_server.py` duplicates the SQL in `MatineeClient.get_recommendations` (line 353). Drift found:
  - **Bug:** line 431 calls `dt.date.fromisoformat` but `datetime` is never imported; the `NameError` is swallowed by `except Exception`, so every direct-SQLite forgotten-gems reason reads "Forgotten gem".
  - `genre_mood` reason: app "Top-rated Animation pick (8.0 ★)", MCP "Curated collection pick".
  - `formats`: app uses `sorted_formats()`, MCP returns raw `group_concat` order.
  - The `@mcp.tool()` wrapper (line 600) does not pass `medium` or `random` although the client supports them.
- `FastMCP("matinee")` is created without `instructions`.
- The UI for recommendations is the **Movie Night Picker modal** (`openMovieNightModal`, `app.js` ~3070): four `.movie-night-tab` pills, 2×2 on phone.
- `installer/build.sh:8` copies an explicit file list.
- TMDB fixtures are hand-written in TMDB's response shape (see `tests/fixtures/tmdb/README.md`), not recorded.

## 4. Semantics

### 4.1 `max_cert`

- Accepted: `G`, `PG`, `PG-13`, `R`, `NC-17`, case-insensitive; normalized to upper case.
- Compared against the **effective certification** `COALESCE(t.certification_override, t.certification)`. The allowed set is computed in Python (`PG` → `IN ('G','PG')`).
- NULL, `NR`, `Unrated` and any value outside the five are excluded whenever `max_cert` is set.
- Any other value → **400** `{"error": "max_cert must be one of G, PG, PG-13, R, NC-17"}`. This intentionally differs from `max_runtime`/`min_rating` (silently ignored when invalid): silently dropping a kids' filter is unsafe.
- Absent → no filtering (today's behavior).

### 4.2 `next_in_franchise`

- A collection is **started** when *any* title in it has a watch — including titles still in the DB with no copies (a disc watched, then sold).
- The **pick** is the earliest-released *owned* title in the collection with no watches. Order: `release_date IS NULL, release_date, sort_title`.
- **Filters apply after the pick** (`max_cert`, `genre`, `medium`, `max_runtime`, `min_rating`). If the pick fails a filter, that collection yields nothing; the mode never skips ahead to a later entry.
- Order of results: most recent watch in the collection first, then `collection_name`. `random=1` overrides, as today.
- Reason:
  - `"<Collection> · Next after <T>"` where *T* is the latest-released watched title released before the pick;
  - otherwise `"<Collection> · Before <T> (watched)"` where *T* is the earliest-released watched title after the pick.

### 4.3 `start_franchise`

- A collection qualifies when it has **≥ 2 owned titles** and **no title in it has any watch**.
- The pick is the earliest-released owned title; filters apply after the pick.
- Order: pick's `COALESCE(user rating, tmdb_vote)` descending, then `collection_name`. `random=1` overrides.
- Reason: `"Start the <Collection> · <n> owned"`.

### 4.4 Response additions (all modes)

Every result gains `certification` (effective), `collection_tmdb_id`, `collection_name`. When `count == 0` the response carries a top-level `hint` naming the next thing to try:

| Mode | Hint |
|------|------|
| `next_in_franchise` | "No series in progress. Try mode=start_franchise." |
| `start_franchise` | "No unstarted series with 2+ owned films. Try mode=never_watched." |
| any, with `max_cert` | appended: "Unrated films are hidden while max_cert is set." |
| others | "Nothing matched these filters. Try removing a filter or mode=genre_mood." |

## 5. Code layout

### 5.1 `recommend.py` (new, stdlib only)

```python
CERT_ORDER = ("G", "PG", "PG-13", "R", "NC-17")
CERT_VALUES = CERT_ORDER + ("NR",)            # allowed for the override
MODES = ("forgotten_gems", "never_watched", "quick_picks", "genre_mood",
         "next_in_franchise", "start_franchise")
FORMAT_ORDER = (...)                          # moved from app.py

class ParamError(ValueError): ...

def parse_params(m) -> dict        # Mapping from Flask request.args or MCP kwargs
def recommend(con, p, today=None) -> dict    # {"mode","count","results",["hint"]}
def sorted_formats(fs) -> list     # moved from app.py; app.py imports it
```

- `recommend()` never builds `poster_url`; `app.py` adds `poster_url`/`poster_file` per result. Image URLs are a web concern.
- `today` is injectable so forgotten-gems reasons are testable without freezing time.

### 5.2 Callers

- `app.recommendations()`: `parse_params(request.args)` → `recommend(db(), p)` → add `poster_url` → `jsonify`. `ParamError` → `make_err(400, str(e))`.
- `mcp_server.MatineeClient.get_recommendations`: direct-SQLite branch does a lazy `import recommend` (the REST-only path never needs it, and the module never imports Flask). REST branch appends `max_cert`, `medium`, `random=1` to the query string.
- `installer/build.sh`: add `recommend.py` to the copy list.

**Divergence from Roasted / Cookbook-Collection:** this is Matinee's first second Python module. Justified because two entry points must share the logic and one of them must not import Flask. It is also the first step of the backlog's "split `app.py`" item.

### 5.3 SQL sketch (franchise pick)

```sql
WITH started AS (
  SELECT t.collection_tmdb_id, MAX(w.watched_date) AS last_watch
  FROM titles t JOIN watches w ON w.title_id = t.id
  WHERE t.collection_tmdb_id IS NOT NULL
  GROUP BY t.collection_tmdb_id
), ranked AS (
  SELECT t.*, s.last_watch,
         ROW_NUMBER() OVER (PARTITION BY t.collection_tmdb_id
                            ORDER BY t.release_date IS NULL, t.release_date, t.sort_title) AS rn
  FROM titles t JOIN started s ON s.collection_tmdb_id = t.collection_tmdb_id
  WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id = t.id)
    AND NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id = t.id)
)
SELECT ... FROM ranked t
WHERE rn = 1 AND <max_cert / genre / medium / runtime / min_rating>
ORDER BY last_watch DESC, collection_name
LIMIT ?
```

`start_franchise` replaces `started` with collections that have ≥ 2 owned titles and no watched title (`NOT IN (SELECT collection_tmdb_id FROM started)`). Window functions need SQLite ≥ 3.25; Python 3.10+ and Alpine ship 3.4x. Reason text costs one small indexed query per result (≤ `limit` ≤ 50).

## 6. Data model and migration (v7)

```python
# v7: editor override for the US certification (survives TMDB refresh; not in TITLE_COLS)
if "certification_override" not in title_cols:
    con.execute("ALTER TABLE titles ADD COLUMN certification_override TEXT")
```

- Appended to `migrate()` after the v6 block; idempotent on every prior version (v1 → v7). `SCHEMA_VERSION = "7"`. No index needed.
- `write_title()` only writes `TITLE_COLS`, so TMDB refresh never touches the override.
- Every place that reads `certification` for display or filtering uses the effective value.

## 7. API

All read routes stay under `/api/` and `/admin/api/` with today's `PUBLIC_VIEW` handling. No new private data is exposed: watch dates and counts are already public on this route. The write route is editor-only.

Scenario data (from fixtures): owned Toy Story (862), Toy Story 3 (10193), Inside Out (150540), A Bug's Life (9487), Kantara (848326); Toy Story watched 2026-10-31.

### `GET /api/recommendations?mode=next_in_franchise`

```json
{"mode": "next_in_franchise", "count": 1, "results": [{
  "id": 2, "title": "Toy Story 3", "year": 2010, "runtime": 103,
  "certification": "G", "collection_tmdb_id": 10194, "collection_name": "Toy Story Collection",
  "user_rating": null, "tmdb_vote": 7.8, "last_watched": null, "watch_count": 0,
  "formats": ["4K UHD", "Blu-ray"], "genres": ["Animation", "Family", "Comedy"],
  "poster_url": "/images/10193-p342.jpg?v=2026-11-06T08%3A40%3A00Z",
  "poster_file": "10193-p342.jpg",
  "reason": "Toy Story Collection · Next after Toy Story",
  "recommendation_reason": "Toy Story Collection · Next after Toy Story"}]}
```

### `GET /api/recommendations?mode=quick_picks&max_cert=PG`

```json
{"mode": "quick_picks", "count": 3, "results": [
  {"title": "Toy Story",    "runtime": 81, "certification": "G",  "tmdb_vote": 8.0,
   "collection_name": "Toy Story Collection", "reason": "81 mins · Perfect quick watch"},
  {"title": "Inside Out",   "runtime": 95, "certification": "PG", "tmdb_vote": 7.9,
   "collection_name": "Inside Out Collection", "reason": "95 mins · Perfect quick watch"},
  {"title": "A Bug's Life", "runtime": 95, "certification": "G",  "tmdb_vote": 7.0,
   "collection_name": null, "reason": "95 mins · Perfect quick watch"}]}
```

(Other fields as in the previous example, omitted for brevity.)

### `GET /api/recommendations?mode=start_franchise` — nothing eligible

```json
{"mode": "start_franchise", "count": 0, "results": [],
 "hint": "No unstarted series with 2+ owned films. Try mode=never_watched."}
```

### `GET /api/recommendations?max_cert=PG13`

`400 {"error": "max_cert must be one of G, PG, PG-13, R, NC-17"}`

### `PUT /admin/api/titles/<id>/certification` (`@editor_required`)

Request `{"certification": "PG-13"}` → `200`:

```json
{"ok": true, "certification": "PG-13", "certification_tmdb": null, "certification_override": "PG-13"}
```

`{"certification": null}` clears the override. Values outside `G | PG | PG-13 | R | NC-17 | NR` → 400. Unknown id → 404.

### `GET /api/titles/<id>`

Adds `certification_tmdb` and `certification_override`; `certification` becomes the effective value. All public, like the badge today.

## 8. MCP

### 8.1 Tool

```python
Mode = Literal["forgotten_gems", "never_watched", "quick_picks", "genre_mood",
               "next_in_franchise", "start_franchise"]
Cert = Literal["G", "PG", "PG-13", "R", "NC-17"]

@mcp.tool(**_READONLY)
def get_recommendations(mode: Mode = "forgotten_gems", genre: Optional[str] = None,
                        medium: Optional[Literal["physical", "digital"]] = None,
                        max_runtime: Optional[int] = None, min_rating: Optional[float] = None,
                        max_cert: Optional[Cert] = None, limit: int = 5,
                        random: bool = False) -> Dict[str, Any]:
```

- `_READONLY = {"annotations": ToolAnnotations(readOnlyHint=True, openWorldHint=False)}` when `mcp.types.ToolAnnotations` imports, else `{}`, so the `mcp>=1.0.0` floor stays.
- Name stays `snake_case` (allowed by best practice 1.1; renaming would break existing client configs).
- `Literal` types put the allowed values in the input schema; invalid values are rejected by the client before reaching Matinee.

### 8.2 Description (best practices 1.2, 1.3)

```
Pick movies to watch from the family's own collection (movie night, "what should we watch",
kids' night, family-friendly, sequel, next in the series, start a franchise, quick short movie).
<use_case>
- mode=next_in_franchise: next unwatched owned film in each series someone has started (one per series)
- mode=start_franchise: first film of an owned series (2+ films) nobody has started
- max_cert=G|PG|PG-13|R|NC-17: rating cap; works with any mode. For "kids" with no rating named, use PG-13.
- forgotten_gems, never_watched, quick_picks (≤95 min default), genre_mood as before
</use_case>
<important_notes>
- Films with no US rating (NR/unrated/unknown) are EXCLUDED whenever max_cert is set.
- Franchise modes choose the next film first, then apply filters: if the next Harry Potter is
  PG-13 and max_cert=PG, that series yields nothing rather than skipping ahead.
- An empty result includes a `hint` with what to try next; follow it instead of telling the
  user nothing matched.
- Each result has `id`; pass it to get_movie or where_is for the shelf location.
</important_notes>
```

### 8.3 Server instructions (best practice 2.1)

`FastMCP("matinee", instructions=SERVER_INSTRUCTIONS)`:

- The collection is the family's own discs and digital copies. Recommend only what `get_recommendations` / `search_collection` return; use `search_tmdb` only when the user asks about movies they don't own.
- "Kids' night", "something the kids can watch" → `get_recommendations(max_cert="PG-13")` unless the user names a rating.
- "What's next in the series we're watching" → `mode="next_in_franchise"`; "start a new series" → `mode="start_franchise"`.
- After a pick, "where is it?" → `where_is(title)`. After watching → `log_watch`, then `rate_movie` if they give a rating.

### 8.4 Prompt and resource

- `what_to_watch(mood="", max_runtime=0, kids=False)`: when `kids`, instruct `max_cert="PG-13"` and mention that unrated films were left out.
- `matinee://recommendations` unchanged.

### 8.5 Parity

Both client paths return the same dict shape (direct-SQLite has no `poster_url`). Errors: direct path raises `ParamError`; REST path raises `RuntimeError("Matinee API error (400): …")`. FastMCP returns both as `isError` results.

## 9. UI (412 px phone first)

### 9.1 Movie Night Picker

```
┌──────────── 412 px ────────────┐
│ Movie Night Picker           × │
│ ┌────────┐┌────────┐┌────────┐ │  tabs: grid, 3 columns at ≤ 600 px
│ │Forgottn││ Quick  ││ Never  │ │  ≈114 × 44 px each
│ │  Gems  ││ Picks  ││Watched │ │
│ ├────────┤├────────┤├────────┤ │
│ │Next in ││ Start a││Surprise│ │
│ │ Series ││ Series ││   Me   │ │
│ └────────┘└────────┘└────────┘ │
│ Rating cap                     │  --fs-xs, --ink-3
│ [ Any │  G  │ PG  │ PG-13 ]   │  radiogroup, 4 × ≈86 × 44 px
│ ┌──────┐ Toy Story 3    ★ 7.8 │
│ │poster│ 2010 · 103m · 4K UHD │
│ │      │ [G] Animation Family │  .cert-badge reused
│ └──────┘ Toy Story Collection │
│          · Next after Toy Story│
│ [Roll Again] [Open Movie]      │
└────────────────────────────────┘
```

- **Default cap: Any.** Everything shows until a cap is chosen. The last choice is remembered per device in `localStorage` key `matinee.movieNight.maxCert` (reads/writes in `try/catch`; renders correctly without storage).
- Changing tab or cap re-rolls immediately; the cap is sent as `&max_cert=…` on every mode.
- Tabs get `role="tablist"`/`aria-selected`; the cap is `role="radiogroup"` with `aria-checked`.
- Existing tokens only: segments reuse the `.movie-night-tab` look (`--surface`, `--line`, `--ink-2`, active `--accent` with `#fff`, the same AA pair as today). Desktop keeps the wrapping flex row.
- Empty state shows the server's `hint` instead of the hard-coded "Try Quick Picks"; with a cap set it adds "Unrated films are hidden while a rating cap is on."
- No horizontal scroll at 412 px; every new target ≥ 44 px.

### 9.2 Certification override (editor only, title view)

- Beside `.cert-badge`, a 44 px `<select>`: first option "TMDB (G)" or "TMDB (none)", then G, PG, PG-13, R, NC-17, NR.
- Change → `PUT /admin/api/titles/<id>/certification`; the TMDB option sends `null`. Toast on success or error.
- An overridden badge gets a small "edited" marker (same treatment as the custom-cover badge). Public visitors see only the effective badge.

## 10. Scenarios

Seeded via `title_from_tmdb(fixture)` → `write_title`. New hand-written fixtures in the existing format: `movie-863` Toy Story 2 (1999, 92 min, G, collection 10194), `movie-672` Harry Potter and the Chamber of Secrets (2002, PG), `movie-673` Harry Potter and the Prisoner of Azkaban (2004, PG), `movie-674` Harry Potter and the Goblet of Fire (2005, PG-13) — collection 1241 — and `movie-920` Cars (2006, G, collection 87118).

1. **Next after.** Own Toy Story + Toy Story 3; Toy Story watched; Toy Story 2 not owned. `next_in_franchise` → **Toy Story 3**, "Toy Story Collection · Next after Toy Story".
2. **Gap fill.** Same, but only Toy Story 3 watched → **Toy Story**, "Toy Story Collection · Before Toy Story 3 (watched)".
3. **Sold disc still counts.** Toy Story in the DB with no copies and a watch; only Toy Story 3 owned → **Toy Story 3**.
4. **Filter after pick.** Own Harry Potter 1–4; 1–3 watched. `next_in_franchise&max_cert=PG` → no Harry Potter result. Without a cap → **Harry Potter and the Goblet of Fire**, "Harry Potter Collection · Next after Harry Potter and the Prisoner of Azkaban".
5. **Start a series.** Own Cars + Cars 2, none watched. `start_franchise` → **Cars**, "Start the Cars Collection · 2 owned". Own only Cars 2 → not suggested.
6. **Kids' cap.** `quick_picks&max_cert=PG` → Toy Story, Inside Out, A Bug's Life; Kantara (no US rating) never appears with any cap.
7. **Override.** Editor sets Kantara to `PG-13` → appears in `genre_mood&max_cert=PG-13`, not in `genre_mood&max_cert=PG`; `POST /admin/api/titles/<id>/refresh` keeps the override; `null` clears it. Anonymous `PUT` → 403.
8. **Bad input.** `max_cert=PG13` → 400 from the API; MCP schema rejects it.
9. **Empty hint.** `next_in_franchise` with no watches → `count: 0`, hint names `start_franchise`.
10. **MCP parity.** Every mode × {no cap, `PG`} returns the same ids, order and reasons from `/api/recommendations` and `MatineeClient(db_path=…)`.
11. **MCP kids phrasing.** `what_to_watch(kids=True)` text contains `max_cert="PG-13"`.

## 11. Testing

- `tests/test_recommend.py` (new): `parse_params` and `recommend()` with injected `today`; scenarios 1–6, 8, 9.
- `tests/test_recommendations.py`: existing tests unchanged (refactor safety net); add 400, `hint`, `poster_url` checks.
- `tests/test_app.py`: migration v1 → v7 run twice; override route auth, validation, refresh survival; `get_title` fields.
- `tests/test_mcp_server.py`: parity (scenario 10); REST query string carries `max_cert`/`medium`/`random`; tool schema enumerates the six modes and five ratings; `readOnlyHint` present when supported; server instructions present; regression for the `datetime` bug ("Not watched in" in SQLite mode); scenario 11.
- `tests/test_static_ui.py`: 3-column tab grid rule, radiogroup markup, ≥ 44 px on new controls.
- No network: all TMDB data from `tests/fixtures/tmdb/`.
- CI: Python 3.10–3.13, Alpine job, `node -c static/app.js`.

## 12. Ops

- No new files in `$DATA_DIR`, no new settings, nothing for `.env.example`. `ops/matinee-backup` already covers `matinee.db`.
- Migration runs on `rc-service matinee restart`; no manual DB step.
- Installer: `recommend.py` added to `build.sh`.
- Deploy check on CT 210:

  ```sh
  sqlite3 /var/lib/matinee/matinee.db "SELECT value FROM meta WHERE key='schema'"   # 6 before, 7 after
  sqlite3 /var/lib/matinee/matinee.db "SELECT count(*) FROM titles t
    WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)
      AND COALESCE(t.certification_override, t.certification, '') NOT IN ('G','PG','PG-13','R','NC-17')"
  ```

  The second number is how many owned titles are hidden by any cap until overridden.

## 13. Commit sequence

Each is independently shippable:

1. `fix(mcp): import datetime in direct-SQLite recommendations`
2. `refactor(recommend): extract recommender into recommend.py` — behavior-neutral for the API; brings MCP's `genre_mood` reason and `formats` order into parity (noted in the body); adds the parity test.
3. `feat(recommend): add max_cert rating cap`
4. `feat(db): certification override (schema v7)`
5. `feat(recommend): add next_in_franchise and start_franchise modes`
6. `feat(mcp): expose max_cert, medium, random and franchise modes; add server instructions`
7. `feat(ui): movie night mode grid and rating cap; certification override control`
8. `docs: update mcp-setup and README for recommender modes`

## 14. Deferred

- Per-person watch history and "everyone hasn't seen it".
- Non-US certifications.
- Per-family-member default caps.
- `readOnlyHint` / `destructiveHint` annotations on the other eight MCP tools.
- An "unstarted" tier inside `next_in_franchise`.
