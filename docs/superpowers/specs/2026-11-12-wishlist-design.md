# Movie wish list — design

**Date:** 2026-11-12
**Branch:** `claude/matinee-wishlist` (one PR, closes #36)
**Schema:** v10 → v11 (assigned by the issue queue; the lists change is stacked on top as v12)
**Ported from:** `SDNick484/cookbook-collection` (`wishlist.py`, the `wanted` book status, `static/app.js` `wishDialog`,
`tests/test_v2.py`, README "Family wish list (optional)").

## 1. Intent

There is nowhere to note "we want this on 4K disc" or "buy *Spirited Away*", and nothing links Matinee to the family
Wish Lists app the way Cookbook-Collection already does. Cookbook treats "wanted" as a book **status** that replaces
"owned". For movies that is wrong: the family often owns a streaming copy and wants the 4K UHD disc, or owns the disc
and wants a Digital 4K copy. So a want here is its own row, independent of copies.

### Goals

- A **want** on a title: optionally a format ("4K UHD") and a note up to 140 characters. A title has at most one.
- A title with no copies and a want is **Want to buy**: still hidden from the public grid.
- An owned title with a want shows "Wanted: 4K UHD" to editors on its page.
- **Add to a wish list…** pushes the movie to a person's list in the family Wish Lists app over localhost, with a
  shopping search link, and remembers that it did.
- A **Wanted** view for editors lists every want.
- MCP: a read tool `get_wishlist` and a write tool `want_movie`.

### Non-goals (deferred)

- An automatic "owned on DVD or VHS only" upgrade report.
- Per-person wants inside Matinee (a want belongs to the household; the person is chosen when pushing).
- CSV export of wants.
- Reading purchase status back from the Wish Lists app. It is never read or shown, so there are no spoilers.
- Pushing to the Wish Lists app from MCP: that stays in the app.

## 2. Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| 1 | Model | New table `wants`, one row per title; not a copy status and not a title column | A movie can be wanted while owned in another format. |
| 2 | Format | `wanted_format` is NULL (any copy) or one of `db.FORMATS`; checked in the app, not by a table CHECK | `FORMATS` is deliberately app-checked so a new format needs no rebuild. The 140-character note limit is mirrored by a CHECK, like `edition`. |
| 3 | Clearing | Adding a copy deletes the want in the same transaction when the copy satisfies it (same format, or any copy for a want with no format); the response carries `"want_cleared": true`. Editing a copy's format does not clear | Buying the thing is the end of wanting it. Only `POST /admin/api/copies` is a purchase; an edit corrects a record. |
| 4 | History | `wish_items` rows stay after the want is cleared | Cookbook keeps them; it answers "did we already put this on Lauren's list?". They go only with the title (`ON DELETE CASCADE`). |
| 5 | Visibility | Editors only, as in Cookbook. Routes under `/admin/api`, `@editor_required`. No `want`, `wanted_format` or `wishes` key in any `/api` response | Wants are gift-adjacent: Margaret, Alice and Paige can browse the public grid. |
| 6 | Public grid | `list_titles` already requires a copy, so a Want-to-buy title never shows | No change. `/api/titles/<id>` still answers for an unowned title (`owned: false`) as it does today, without any want key. |
| 7 | Module | New route-free `wishlist.py` (constants, `wishlist_call`, `wishlist_available`, want helpers). The routes stay in `app.py` | CLAUDE.md: sibling modules never import `app`. **Divergence from Cookbook**, whose `wishlist.py` imports `app` and registers its own routes. |
| 8 | Settings | `WISHLIST_URL` (default `http://127.0.0.1:8766`) and `WISHLIST_MOVIE_URL` (default `https://www.amazon.com/s?k={q}`) live in `settings.py`; both in `.env.example`, the installer `add_setting` list and the init script `export` line | CLAUDE.md rule 7. Cookbook read them in its own module. |
| 9 | Shopping query | `Spirited Away 2001 4K UHD`: title, year, wanted format (each when present) | Cookbook used the ISBN; movies have no single identifier. |
| 10 | Pushed item | name `Spirited Away (2001)`, kind `online`, url from the template, notes `Movie · 4K UHD · TMDB 129` plus the editor's note joined with ` · ` | Same shape as Cookbook's `Cookbook · ISBN …`. |
| 11 | People | From the Wish Lists app (`GET /api/people`), not Matinee's `people` table | Its lists are keyed by its own people; Matinee's attendance people are a different set. |
| 12 | Availability | `wishlist_available()` probes `GET /api/people` with a 1 s timeout; `/admin/api/me` answers `wishlist: true/false` and the button shows only when true | A default URL is set even when the app is not installed. A refused loopback connection returns at once. |
| 13 | Refusals | Unknown person: 400 `Pick whose wish list`. Wish Lists app down: 502 with its message. Format outside `FORMATS`: 400. Note over 140 characters: 400 | Per the prompt; Cookbook's wording for the first two. |
| 14 | Create-with-want | `POST /admin/api/wants` takes `title_id` or `tmdb_id`. A `tmdb_id` not in Matinee creates the title with **no copies** (TMDB fetch, images, OMDb, exactly as `add_copy` does; the shared code moves into `ensure_title()`) | The "Want to buy" button in the TMDB add flow and `want_movie` both need it. |
| 15 | Upsert | `POST /admin/api/wants` for a title that already has a want replaces its format and note (200); a new want is 201. `DELETE /admin/api/titles/<id>/want` removes it | One want per title; the form is the same either way. |
| 16 | TMDB errors | The existing shapes: a miss is 404 `{"error": "TMDB has no movie with id N", "reason": "not_found"}`, a 429 from TMDB is 503 `reason: "rate_limited"` | **Clarification of the prompt**, which says "TMDB 429": the app already maps TMDB's 429 to 503 and the want routes reuse that handler. |
| 17 | Schema constants | `NOTE_MAX = 140` in `db.py` next to the other limits | Same pattern as `EDITION_MAX`. |

## 3. Data model (v11)

Created in `migrate()` only (a self-contained block after v10; no `SCHEMA` entry, like `people` and `ask_log`), so
every earlier database gets it and a fresh one gets the same tables:

```sql
CREATE TABLE IF NOT EXISTS wants (
  title_id      INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
  wanted_format TEXT,                                  -- NULL = any copy; else one of FORMATS (checked in the app)
  note          TEXT CHECK (note IS NULL OR length(note) <= 140),
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wish_items (
  id            INTEGER PRIMARY KEY,
  title_id      INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  person_id     INTEGER,                               -- the Wish Lists app's person id
  person_name   TEXT,
  item_id       INTEGER,                               -- the item's id in the Wish Lists app
  wanted_format TEXT,                                  -- the format wanted when it was pushed
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS wish_items_title ON wish_items(title_id);
```

Idempotent (`IF NOT EXISTS`), no data rewritten, so v1 to v10 databases keep every copy, watch and attendee.

## 4. API

All under `/admin/api`, `@editor_required`; anonymous requests are 401. Nothing new under `/api`.

**`POST /admin/api/wants`**, body `{"tmdb_id": 129, "format": "4K UHD", "note": "the Studio Ghibli box"}` (or `title_id`).
*Spirited Away* is not in Matinee: 201

```json
{"id": 41, "title": "Spirited Away", "year": 2001, "owned": false, "copies": [],
 "want": {"wanted_format": "4K UHD", "note": "the Studio Ghibli box", "created_at": "2026-11-12 18:04:11"},
 "wishes": [], "...": "the rest of the editor title view"}
```

`format` is omitted or null for "any copy". Refusals: 400 `Format must be one of 4K UHD, Blu-ray, 3D Blu-ray, DVD, VHS,
Digital 4K, Digital HD, Digital SD.`; 400 `A note can be up to 140 characters.`; 404 `No such title`; TMDB errors as in
decision 16.

**`DELETE /admin/api/titles/<id>/want`** → `{"ok": true}` (404 `No such title`; 200 when there was no want).

**`GET /admin/api/wants`** → every want, newest first:

```json
{"wants": [{"title_id": 12, "tmdb_id": 129, "title": "Spirited Away", "year": 2001,
            "poster_url": "/images/129-p342.jpg?v=...", "wanted_format": "4K UHD", "note": null,
            "owned_formats": [], "pushed_to": ["Lauren"], "created_at": "2026-11-12 18:04:11"}]}
```

`GET` only, so `MATINEE_READ_KEY` may use it (the MCP read tool does).

**`GET /admin/api/wishlist/people`** → `{"people": [{"id": 3, "name": "Lauren", "color": "blue"}]}`; 502 with the
app's message when it is down.

**`POST /admin/api/titles/<id>/wishlist`**, body `{"person_id": 3, "notes": "birthday"}` (404 `No such title`; 400 `Add a want first` when the
title has none, as there is nothing to push; 400 `Pick whose wish list`; 502 `wish list isn't reachable at http://127.0.0.1:8766 (...)`).
It posts to the Wish Lists app and stores a `wish_items` row:

```http
POST http://127.0.0.1:8766/api/people/3/items
{"name": "Spirited Away (2001)", "kind": "online", "priority": 2,
 "url": "https://www.amazon.com/s?k=Spirited+Away+2001+4K+UHD",
 "notes": "Movie · 4K UHD · TMDB 129 · birthday"}
```

→ `{"ok": true, "person": "Lauren", "item_id": 55}`.

**`POST /admin/api/copies`** gains `"want_cleared": true` in its 201 body when it deleted a want.
**`GET /admin/api/titles/<id>`** gains `want` (object or null) and `wishes` (`[{person_name, wanted_format, created_at}]`).
**`GET /admin/api/me`** gains `wishlist` (bool).

## 5. Scenarios

1. *Spirited Away* (TMDB 129) is not in Matinee. Want to buy with 4K UHD creates it with no copies and a want. It is
   absent from `/api/titles` and the public grid; `/api/titles/<id>` has no `want`.
2. *The Dark Knight* is owned only as Digital HD on Movies Anywhere; a want for 4K UHD shows "Wanted: 4K UHD" to an
   editor and nothing on `/api/titles/<id>`. A Blu-ray copy leaves the want; a 4K UHD copy clears it
   (`want_cleared: true`) and the `wish_items` row stays.
3. *Paddington 2* with a want for Any is cleared by a DVD copy.
4. "Add to a wish list…" for *Spirited Away* posts one item to the stub app with the search URL for
   `Spirited Away 2001 4K UHD` and stores a `wish_items` row; an unknown person is 400; the app down (port 1) is 502
   and `wishlist_available()` is false.
5. A TMDB miss and a TMDB rate limit while adding a want return the existing error shapes.
6. Anonymous requests to every want and wish route are 401.

## 6. UI at 412 px

- **Title page (editors):** a "Want to buy" / "Wanted: 4K UHD" line under the copies with **Want…** (format chips
  including Any, a 140-character note) and, once a want exists, **Add to a wish list…** (shown only when
  `state.me.wishlist`) and **Remove**. It opens Cookbook's person picker: radio rows with the person's color.
- **TMDB add flow:** in the copy dialog for a title not yet in Matinee, **Want to buy…** sits beside **Save Copy** and opens the
  same want form; it creates the title with no copies plus the want.
- **Wanted view** (`#/wanted`, header link for editors, not on the dashboard): poster rows with a format chip, the
  note and who it was pushed to. Rows link to the title page.

## 7. MCP

- `get_wishlist` (read, registered at every level): title, year, `wanted_format`, `owned_formats`, `note`,
  `pushed_to` names. It reads `GET /admin/api/wants` only, never direct SQLite (wants are editor-only and Ask runs on
  direct SQLite); without a key it says so, like `list_people`.
- `want_movie(title_id | tmdb_id, format, note)` (write, level `all` only, as `add_movie` can also create titles):
  `POST /admin/api/wants`.
- Tool counts: `none` 8 (was 7), `log` 10 (was 9), `all` 12 (was 10). `TOOL_HINTS` gets both.

## 8. Ops and docs

Nothing new in `$DATA_DIR` (the tables are in `matinee.db`, which `ops/matinee-backup` already covers). README section
"Family wish list (optional)", `docs/mcp-setup.md`, `.env.example`, `installer/setup-head.sh`.

## 9. Unverified here

Against the real Wish Lists app on the host (only a stub is tested), and on an Android phone at 412 px.
