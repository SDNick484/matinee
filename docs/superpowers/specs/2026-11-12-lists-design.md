# Lists: Watch Soon and seasonal lists — design

**Date:** 2026-11-12
**Branch:** `claude/matinee-lists` (one PR, closes #35)
**Schema:** v10 → v12. v11 is reserved for the movie wish list (#36), built in parallel and merged first; the
lists step in `migrate()` is self-contained and marked where the v11 step sits, so the rebase is an insertion.
**Verified against:** `matinee` `main` @ `1b92d87` (merge of PR #34, copy details).

## 1. Intent

Recommendations with no input fall back to `forgotten_gems`, and there is no way to say "these are the ones we
want to get to next" or "these are our Christmas movies". This adds curated lists of titles: one built-in
**Watch Soon** list, and editor-made holiday and seasonal lists (Halloween, Thanksgiving, Christmas, Summer).
An assistant asked to "pick something" uses Watch Soon first; in early December it can ask for what is in
season and lean towards *Elf*. Watching something from Watch Soon takes it off the list.

### Goals

- Two tables, `lists` and `list_titles`; Watch Soon exists on every database exactly once and can be neither
  renamed nor deleted.
- Public reads (`GET /api/lists`, `GET /api/lists/<id>`, a `lists` key on the title detail), editor writes.
- A real-dated watch (app or MCP `log_watch`) removes the title from Watch Soon in the same transaction.
- A `watch_soon` recommendation mode, a smarter default when no mode is given, and `list` / `in_season`
  restrictions for every mode.
- MCP: `get_lists`, `get_list` (every level), `set_list_membership` (level `log`).
- UI: list chips on the title page, an editor **Lists** sheet there, and a **Lists** manager in the admin header.

### Non-goals

- Any grid or dashboard surface: no filter chip, no stats card, no home section, no Movie Night tab.
- Manual ordering of titles inside a list (they are ordered by when they were added).
- CSV export changes; the export stays one row per copy.
- New Ask tools. Ask gets the `watch_soon` mode and the `list` / `in_season` arguments on its existing
  `get_recommendations` tool (a three-line schema change, so "trivial"); `get_lists` and `get_list` are not
  added to Ask.
- Per-person lists.

## 2. Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| 1 | Visibility | Lists, their names, windows and members are **public**. Only `seen_by` on a list's titles is editor-only (the `/admin/api` read and keyed MCP). | The issue's call: lists are "our Christmas movies", not a diary. Who has seen what stays behind the editor gate, as everywhere else. |
| 2 | Where the code lives | New stdlib-only module `lists.py` (no Flask), in `_SPLIT_MODULES` and the installer's `cp` line. Routes in `app.py`. | `recommend.py` (stdlib-only, used by the MCP server's direct-SQLite mode) needs the season logic, so it cannot live in a Flask module. Validation raises `ListError(status, message)`; `app.py` turns it into `make_err`. |
| 3 | Kind | `watch_soon` (one row, created by `migrate()`), `seasonal`, `custom`. A new list is `seasonal` when it has a window, else `custom`, unless `kind` is sent. | The kind is a label for the editor; the window, not the kind, decides `in_season`. |
| 4 | Window | `season_start` and `season_end` as `MM-DD`, both or neither, a real day in a leap year (`02-29` is fine, `02-30` is 400). `start > end` wraps the year (`11-25` → `01-01`). Watch Soon has no window. | Month-day avoids a yearly edit. Checked with `datetime.date(2000, m, d)`. |
| 5 | "Today" for `in_season` | The date in America/Los_Angeles: `zoneinfo` when the tz database is there, otherwise a hand-written US Pacific rule (UTC−8, UTC−7 from the second Sunday of March to the first Sunday of November). | Alpine images ship without `tzdata`, and a new apk package is a footprint change. The fallback is exact to the hour and only the date is used. |
| 6 | Name rules | 1 to 40 characters, unique case-insensitively (`COLLATE NOCASE UNIQUE`). A collision is **409**. | The issue's refusal. People use 400 for the same case; lists follow the issue. |
| 7 | Watch Soon exactly once | `migrate()` inserts it `WHERE NOT EXISTS (… kind = 'watch_soon')`, and a partial unique index `lists_one_watch_soon ON lists(kind) WHERE kind = 'watch_soon'` makes a second one impossible. | Idempotent on every run and every version. |
| 8 | Members | Any title row, owned or not. Recommendations only ever pick owned titles. A list page shows unowned members with `owned: false`. | The issue's call; a title whose last copy was deleted stays on its lists. |
| 9 | Order inside a list | `added_at` ascending, then `sort_title`. | "Oldest added first" is also Watch Soon's tiebreak. |
| 10 | What clears Watch Soon | `POST /admin/api/titles/<id>/watches` with an explicit `watched_date` and no `date_bucket`, whatever the date. The `DELETE` runs before the single `commit()`. The response always carries `removed_from` (`["Watch Soon"]` or `[]`). | The issue: "a real date (not a `date_bucket`)". MCP `log_watch` writes through this route, so both paths share it. Seasonal and custom lists never change on a watch. |
| 11 | Default mode | No `mode`: run `watch_soon` with every other filter; if it returns nothing, run `forgotten_gems` (or `genre_mood` when `list` or `in_season` is given). The response's `mode` is the mode that ran. | **Refines the issue** ("use `watch_soon` if it has at least one owned title"): trying it with the filters means a kids' cap or a list restriction that empties Watch Soon still gets an answer. With `in_season=true` and no mode, `forgotten_gems` would demand a rating of 8+ and a year since the last watch, so a seasonal list with unrated films would come back empty; `genre_mood` ranks the list's owned titles by rating. |
| 12 | `watch_soon` order | Unseen first (household: no watch; with `audience`: nobody in it has seen it), then oldest added, then title. | The issue. |
| 13 | `list` parameter | A list id or name (any case). Unknown is 400: `Unknown list: Xmas. Lists: Watch Soon, Christmas, Halloween.` The response echoes `"list": {"id", "name"}`. | The echo lets the MCP REST client refuse an older server that ignored it (as with `audience`). |
| 14 | `in_season` parameter | `true`/`1`/`yes`: titles on any list whose window holds today. The response echoes `"in_season": {"date", "lists"}`. With nothing in season the result is empty with a hint naming the seasonal windows; not a 400. | Asking "what's in season" in July is a fair question with an empty answer. |
| 15 | Franchise modes with a list | The list restriction is one more filter: the franchise pick is chosen first, then filtered, like `max_cert`. | Consistent with the existing rule. |
| 16 | `seen_by` on `get_list` | Names, on `/admin/api/lists/<id>` and in direct-SQLite MCP mode, when people exist. Never on `/api`. | Same shape as `get_recommendations`' `seen_by` (names), so the MCP REST client needs no id lookup. |
| 17 | MCP write level | `set_list_membership` at `log` and `all`. | The issue: adding to Watch Soon is a phone task. |
| 18 | Older database in direct-SQLite MCP mode | No `lists` table: `get_lists` returns no lists, `get_list` and `list=`/`in_season` raise "This Matinee database has no lists yet. Update Matinee.", and the default mode goes straight to `forgotten_gems`. | The MCP server opens the file read-only and may start before the app migrated it. |

## 3. Data model (v12)

Created in `migrate()` only (as `people` and `ask_log` are), so a fresh and a migrated database are identical:

```sql
CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(name) BETWEEN 1 AND 40),
  kind TEXT NOT NULL CHECK (kind IN ('watch_soon', 'seasonal', 'custom')),
  season_start TEXT CHECK (season_start IS NULL OR season_start GLOB '[01][0-9]-[0-3][0-9]'),
  season_end   TEXT CHECK (season_end   IS NULL OR season_end   GLOB '[01][0-9]-[0-3][0-9]'),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  CHECK ((season_start IS NULL) = (season_end IS NULL)),
  CHECK (kind != 'watch_soon' OR season_start IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS lists_one_watch_soon ON lists(kind) WHERE kind = 'watch_soon';
CREATE TABLE IF NOT EXISTS list_titles (
  list_id  INTEGER NOT NULL REFERENCES lists(id)  ON DELETE CASCADE,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (list_id, title_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS list_titles_title ON list_titles(title_id);
INSERT INTO lists (name, kind, sort_order)
  SELECT 'Watch Soon', 'watch_soon', 0 WHERE NOT EXISTS (SELECT 1 FROM lists WHERE kind = 'watch_soon');
```

No new file in `$DATA_DIR`, no setting, no dependency: `ops/matinee-backup` already copies the database.

## 4. API

List object (`lists.list_out`), the same on `/api` and `/admin/api`:

```json
{"id": 2, "name": "Christmas", "kind": "seasonal", "season_start": "11-25", "season_end": "01-01",
 "in_season": true, "sort_order": 1, "count": 3}
```

| Route | Auth | Body / answer |
|-------|------|---------------|
| `GET /api/lists`, `/admin/api/lists` | public (`PUBLIC_VIEW`) / editor | `{"today": "2026-12-05", "lists": [list, …]}` in `sort_order, id` order |
| `GET /api/lists/<id>`, `/admin/api/lists/<id>` | public / editor | list + `"titles": [{"id", "title", "year", "poster_url", "formats", "owned", "added_at"}]`; editor adds `"seen_by": ["Nick", "Lauren"]` per title when people exist; 404 unknown |
| `GET /api/titles/<id>` (both prefixes) | as today | adds `"lists": [{"id", "name", "kind", "in_season"}]` |
| `POST /admin/api/lists` | editor | `{"name": "Christmas", "season_start": "11-25", "season_end": "01-01"}` → 201 `{"ok": true, "list": …}` |
| `PUT /admin/api/lists/<id>` | editor | any of `name`, `kind`, `season_start`, `season_end`, `sort_order` → `{"ok": true, "list": …}` |
| `DELETE /admin/api/lists/<id>` | editor | `{"ok": true, "deleted_id": 2}` |
| `PUT /admin/api/lists/<id>/titles/<title_id>` | editor | add (idempotent; keeps the first `added_at`) → `{"ok": true, "list_id", "title_id", "on": true, "lists": [title's lists]}` |
| `DELETE /admin/api/lists/<id>/titles/<title_id>` | editor | remove (idempotent) → same shape with `"on": false` |

Refusals: deleting Watch Soon, renaming it, giving it a window or another kind, or `kind: "watch_soon"` on
another list → **400**; a name that collides case-insensitively → **409**; a malformed or half window → **400**;
unknown list or title → **404**; anonymous `POST`/`PUT`/`DELETE` → **401** (`editor_required`).

Watch log: `POST /admin/api/titles/214/watches {"watched_date": "2026-11-12"}` →
`{"ok": true, "watch": {…}, "removed_from": ["Watch Soon"]}`; with `{"date_bucket": "over_10_years"}` →
`"removed_from": []` and Watch Soon unchanged.

Recommendations (`/api/recommendations` and `/admin/api/recommendations`): `mode` adds `watch_soon` and may be
omitted; new `list` and `in_season`. Example, Watch Soon holding *Paddington 2* and *Spirited Away*:

```json
GET /api/recommendations
{"mode": "watch_soon", "count": 2, "results": [
  {"id": 7, "title": "Paddington 2", "reason": "On Watch Soon · Not watched yet", …},
  {"id": 3, "title": "Spirited Away", "reason": "On Watch Soon · Not watched yet", …}]}
```

## 5. UI (412 px phone)

- **Title page:** under the genres, a `.list-chips` row of buttons, one per list the title is on (an in-season
  list gets a small "in season" marker). Tapping one opens a sheet listing that list's titles; a row links to
  `#/t/<id>`. Anonymous visitors see the chips too. Chips wrap; no horizontal scroll.
- **Editor "Lists" button** at the end of the same row (shown even when the title is on no list) opens a
  membership sheet: one 44 px toggle row per list (`aria-pressed`), each tap saves at once with
  `PUT`/`DELETE …/titles/<id>`, and closing the sheet re-renders the title page.
- **Lists manager** in the admin header (after People), in the People modal's style: a row per list with name,
  window (`MM-DD` inputs), ↑/↓ order and Delete (not on Watch Soon, whose name field is read-only), and an
  add form.

## 6. MCP

| Tool | Level | Notes |
|------|-------|-------|
| `get_lists` | every level | `{"today", "lists": [{"id", "name", "kind", "season_start", "season_end", "in_season", "count"}]}` |
| `get_list(list)` | every level | name or id; titles with `year`, `formats`, `owned`, `added_at`, and `seen_by` (names) when keyed or direct |
| `set_list_membership(list, title_id, on)` | `log`, `all` | REST `PUT`/`DELETE /admin/api/lists/<id>/titles/<title_id>`; the list name is resolved through `GET …/lists` |
| `get_recommendations` | every level | `mode` now optional (default chosen by the server), `watch_soon` added, plus `list` and `in_season` |

Tool counts: `none` 9 (7 + 2 reads), `log` 12, `all` 13. Instructions: "pick something" with nothing else →
`get_recommendations()` with no mode; near a holiday, `get_lists` and `in_season=true`; "add it to Watch
Soon" → `set_list_membership` (named only at `log`/`all`). A REST server older than lists answers 404 to
`/lists` (→ "This Matinee server is too old for lists. Update Matinee."), and a recommendations response with
no `list` / `in_season` echo is refused the same way rather than returned unfiltered.

## 7. Scenarios

- Watch Soon holds *Paddington 2* and *Spirited Away* (both owned); `get_recommendations()` returns them with
  mode `watch_soon`. With Watch Soon empty, the same call reports `forgotten_gems`.
- A watch of *Paddington 2* dated 2026-11-12 removes it from Watch Soon and answers `removed_from:
  ["Watch Soon"]`. *Elf* on Christmas (`11-25` → `01-01`) stays on Christmas after a watch. A watch logged
  as `over_10_years` leaves Watch Soon unchanged.
- On 2026-12-05 `in_season=true` returns *Elf* (Christmas); on 2026-10-15 it returns *Hocus Pocus*
  (Halloween, `10-01` → `10-31`) and not *Elf*.
- *Elf*'s title page shows chips for Watch Soon and Christmas to an anonymous visitor; `/api/titles`,
  `/api/stats` and the grid are unchanged.
- Deleting Watch Soon is 400; renaming Christmas to "christmas" while another list is named "Christmas" is
  409 (renaming a list to its own name in another case is allowed). Anonymous `POST`, `PUT` and `DELETE` on
  every list route are 401.
- v1, v9 and v10 databases migrate and end with exactly one Watch Soon; a second `migrate()` changes nothing.

## 8. Deferred

- Ordering titles inside a list by hand; a list-of-lists page; showing in-season lists on the grid.
- `get_lists` / `get_list` in Ask.
- Removing a title from Watch Soon when it is watched through an edit (there is no watch edit route today).
