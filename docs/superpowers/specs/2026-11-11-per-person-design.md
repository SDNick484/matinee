# People and watch attendance ("who has seen it") — design

**Date:** 2026-11-11
**Branch:** `feat/per-person` (spec and plan in a draft PR; the build is a later session)
**Schema:** v8 → v9 (two new tables, no change to an existing table, column or row)
**Status:** DRAFT, waiting for Nick's review. No implementation has started.
**Verified against:** `matinee` `main` @ `b2f0fe3` (merge of PR #27), `python -m pytest -q` → 842 passed, 13 skipped.

The feature is dormant until an editor adds the first person, so upgrading an install changes nothing by default.

---

## 1. Intent

The recommender can say "nobody has watched this", but not "the girls haven't seen this" or "something for
just the two of us". A watch has a date and a note and no record of who was on the couch. This change adds
people and watch attendance, so the Movie Night picker, the MCP tools and Ask can pick for an audience.

Example questions it must answer:

- "Something the girls haven't seen, PG or under."
- "What's next in *Harry Potter* for Alice and Paige?" (Midge is two films ahead.)
- "Something for us two tonight that neither of us has seen."
- "Has Paige seen *Inside Out*?"

### Goals

- An editor-managed list of people, each an adult or a kid, each with a color.
- A watch records its attendees. The default is everyone, so logging stays one tap; people can be left out
  at the moment of logging and corrected afterwards.
- The recommender takes an audience and treats "unwatched" as "nobody in the audience has seen it".
- MCP and Ask gain the same audience parameter.
- People and attendance are editor-only. The public pages and `/api` are unchanged.
- The household rating (`user_ratings`, one row per title) stays the title's rating, untouched.
- The migration is additive and writes no rows: no existing watch or rating is changed.

### Non-goals

- Per-person ratings (§12 records the options and why they are deferred).
- Linking a person to a Cloudflare Access email, or working out "me" from the signed-in editor. The kids
  have no devices or emails; this is revisited when the first one signs in.
- Any public display of people, colors or attendance.
- Per-person stats and per-person CSV columns.

## 2. Decisions from brainstorming (2026-11-11)

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Who is a person? | **A `people` table an editor manages**: name, `kind` (adult or kid), color, order, active. | The kids have no Access emails. `lan`, `api-key` and `read-key` identities map to nobody. Whoever logs a watch is not the audience: one phone logs for five people. |
| 2 | How does a watch record who was there? | **Everyone by default; omit people when logging; correct later.** A `watch_people` join table. | Whole-family watches stay one tap. A watch by the two adults is one tap plus three taps on the kids' dots. |
| 3 | Watches logged before this feature | **Unresolved, counted as seen by everyone**, except a watch in the "10+ Years" bucket or dated more than ten years ago, which counts as **seen by the adults only**. Applied when reading; nothing is written. | Conservative for "haven't seen" lists, and the guess stays visible and fixable. Ten years predates the kids' movie nights. |
| 4 | Public or editor-only? | **Editor-only**: people, colors, attendance, and every audience filter. | Children's names and viewing history do not belong on an unauthenticated API. A public audience filter would leak attendance one query at a time. |
| 5 | What does "haven't seen" mean for a group? | **Strict: nobody in the audience has seen it.** | It is what "new to everyone on the couch" means, and it extends cleanly to every mode. |
| 6 | Per-person ratings | **None in this change** (option 1). | Ratings are agreed aloud and entered once; that is already the model. Option 2 stays a later additive table. |
| 7 | Audience and rating cap | **Independent.** Picking Kids does not set a cap. | The cap is already remembered per device; coupling them hides a setting. |

## 3. Current state (verified in code)

| Area | `main` @ `b2f0fe3` |
|---|---|
| `watches` | `id, title_id, watched_date, notes, date_bucket, created_at`. No person. `watched_date` is nullable; a bucket watch stores an estimated date (`today − offset`). |
| `user_ratings` | `title_id` is `UNIQUE`: one rating per title. Public on `/api`. |
| Logging | `detail.js` posts `{watched_date}` from "+ Watched Today", a date input, or a bucket button, to `POST /admin/api/titles/<id>/watches`. |
| "Unwatched" | `recommend.py` tests `NOT EXISTS (SELECT 1 FROM watches …)` in `never_watched` and in the `next_in_franchise` CTE; `started` and `forgotten_gems` use any watch by anyone. `list_titles` does the same for `watched=unwatched`. |
| Privacy | `PRIVATE_WATCH = ("notes",)` strips notes from public watches (`watch_out`). `tests/test_privacy.py` pins it. |
| Read key | `MATINEE_READ_KEY` may `GET /admin/api/*` and nothing else. The remote MCP service uses it at write level `none`. |
| MCP write levels | `none`, `log` (`rate_movie`, `log_watch`), `all`. |
| Colors | `static/style.css` already defines `--p-user1` … `--p-user5` (`#5cc178` green, `#5b9cf0` blue, `#ef5a52` red, `#f27fb8` pink, `#a98bf5` purple). Nothing in the app uses them yet; `docs/tokens.html` shows them as "Member 1–5". |

### Drift

- `backlog.md` (project notes) says the per-person colors are in `openspec/config.yaml`. That file has no
  colors. They are the `--p-user*` tokens in `static/style.css`.
- The roadmap of 2026-11-10 lists this as PR #9, to start after the JS-module split. That split has merged
  (PR #27), so the UI work lands in `static/js/` modules.

### Not verified

- Whether a sibling app (Roasted, Cookbook Collection, Dinner Log) has a people table whose shape this
  should follow. None was read for this spec. If one exists, compare column names before the build.
- The count of existing watches by bucket on the production database. The read-time rule in decision 3 does
  not depend on it.

## 4. Data model

```sql
-- app.migrate(), under "# v9: people and watch attendance"
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('adult', 'kid')),
  color TEXT NOT NULL,                       -- palette key: green | blue | red | pink | purple
  sort_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS watch_people (
  watch_id  INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
  PRIMARY KEY (watch_id, person_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS watch_people_person ON watch_people(person_id);
```

- **Migration.** `SCHEMA_VERSION = "9"`. The DDL lives only in `migrate()`, appended after the v8 block, so
  it runs after the older step that may recreate `watches` on a v1–v4 database. Idempotent from every
  earlier version. It inserts nothing.
- **No seeding.** People are created in the admin UI. The repository is public; names stay in the database.
- **Deleting.** Deleting a watch or a title cascades to `watch_people`. A person with attendance cannot be
  deleted (`RESTRICT`, and the route answers 400 first); they are deactivated instead, which keeps history.
- **Color** is a palette key, not a hex value, so the palette can be retuned in CSS without a migration.
  Keys map to `--p-user1` … `--p-user5` in the order green, blue, red, pink, purple. Two people may share a
  color.
- **Limit.** At most 8 active people (the dots row is sized for that at 412 px).
- **Backup.** Both tables are in `matinee.db`; `ops/matinee-backup` already covers them. No new setting.

### 4.1 Who has seen a title

One SQL fragment in `recommend.py` defines it, and every consumer uses it (`recommend()`, `list_titles`,
`title_detail`, MCP DB mode), so they cannot drift:

```sql
-- recommend.SEEN_CTE: one row per (title, person, watch date)
seen(title_id, person_id, watched_date) AS (
  SELECT w.title_id, wp.person_id, w.watched_date
    FROM watches w JOIN watch_people wp ON wp.watch_id = w.id
  UNION ALL
  SELECT w.title_id, p.id, w.watched_date
    FROM watches w CROSS JOIN people p
   WHERE NOT EXISTS (SELECT 1 FROM watch_people x WHERE x.watch_id = w.id)
     AND (p.kind = 'adult'
          OR NOT (COALESCE(w.date_bucket, '') = 'over_10_years'
                  OR (w.watched_date IS NOT NULL AND w.watched_date < date('now', '-10 years'))))
)
```

A watch's **attendance** is one of:

| Value | Meaning | Counts as seen by |
|---|---|---|
| `recorded` | The watch has `watch_people` rows. | Exactly those people. |
| `assumed_everyone` | No rows, and not ten or more years old. | Every person. |
| `assumed_adults` | No rows; bucket `over_10_years`, or dated more than ten years ago. | Every adult. |

A new watch is always `recorded` once people exist. With no people at all, every watch is unresolved, `seen`
is empty, and no audience can be named, so the app behaves exactly as it does today.

## 5. Audience

An audience is a set of people. It is written the same way everywhere (query string, MCP argument, Ask):

| Value | Resolves to |
|---|---|
| `everyone` | All active people. |
| `adults` | Active people with `kind = 'adult'`. |
| `kids` | Active people with `kind = 'kid'`. |
| `Alice,Paige` or `4,5` | The named people or ids, case-insensitive, active or not. |

`recommend.resolve_audience(con, raw) -> {"label": str, "people": [{"id", "name"}]}` raises `ParamError`
(HTTP 400) with these messages:

| Case | Message |
|---|---|
| No people exist | `No people are set up yet. Add them under People in the admin view.` |
| Unknown name or id | `Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige.` |
| `kids` or `adults` matches nobody | `No kids are set up.` / `No adults are set up.` |
| Empty after trimming | `audience is empty.` |

To keep that grammar unambiguous, a person's name may not be `everyone`, `adults` or `kids`, may not contain
a comma, and may not be all digits.

### 5.1 What an audience changes in each mode

No audience means today's behavior, unchanged. With audience **A**:

| Mode | Rule |
|---|---|
| `never_watched` | Owned titles with no `seen` row for anyone in A. |
| `next_in_franchise` | A collection is *started* when someone in A has seen any title in it. The pick is the first owned title, in release order, that nobody in A has seen. The reason names the neighbour A watched ("Harry Potter Collection · Next after Harry Potter and the Chamber of Secrets"). |
| `start_franchise` | Collections where nobody in A has seen any title, with two or more owned. |
| `forgotten_gems` | Household rating ≥ 8.0, and A's own most recent watch is over 365 days ago or absent. The reason uses A's last watch. |
| `quick_picks`, `genre_mood` | Unchanged: these never filtered on watches. |

Filters (`genre`, `medium`, `min_rating`, `max_runtime`, `max_cert`) apply as today, and franchise modes
still apply them after the pick. `min_rating` keeps using the household rating.

When an audience is given, the response echoes it and each result lists who has seen the title:

```json
{"mode": "never_watched", "count": 1,
 "audience": {"label": "kids", "people": [{"id": 3, "name": "Midge"}, {"id": 4, "name": "Alice"}, {"id": 5, "name": "Paige"}]},
 "results": [{"id": 17, "title": "Inside Out", "year": 2015, "certification": "PG",
              "seen_by": ["Nick", "Lauren"], "reason": "New to Midge, Alice and Paige · TMDB 7.9 ★"}]}
```

`seen_by` and `audience` appear only when an audience was given, which only an editor can do (§6.4), so
`recommend()` needs no second privacy switch. Without an audience the response is byte-for-byte today's.

## 6. API

Every route below is under `/admin/api` with `@editor_required`. `GET` routes accept the read key; writes
need an editor or the write key. Errors are `{"error": "<message>"}`.

### 6.1 People

`GET /admin/api/people`

```json
{"people": [
  {"id": 1, "name": "Nick",   "kind": "adult", "color": "green",  "sort_order": 0, "active": true, "watch_count": 41},
  {"id": 2, "name": "Lauren", "kind": "adult", "color": "blue",   "sort_order": 1, "active": true, "watch_count": 38},
  {"id": 3, "name": "Midge",  "kind": "kid",   "color": "red",    "sort_order": 2, "active": true, "watch_count": 29},
  {"id": 4, "name": "Alice",  "kind": "kid",   "color": "pink",   "sort_order": 3, "active": true, "watch_count": 27},
  {"id": 5, "name": "Paige",  "kind": "kid",   "color": "purple", "sort_order": 4, "active": true, "watch_count": 27}]}
```

Ordered by `sort_order, id`. `watch_count` counts recorded attendance only.

`POST /admin/api/people` with `{"name": "Midge", "kind": "kid", "color": "red"}` →
`{"ok": true, "person": {…}}`. `sort_order` defaults to one past the current maximum.

`PUT /admin/api/people/<id>` takes any of `name`, `kind`, `color`, `sort_order`, `active` →
`{"ok": true, "person": {…}}`.

`DELETE /admin/api/people/<id>` → `{"ok": true, "deleted_id": 6}`.

| Status | When | Message |
|---|---|---|
| 400 | Name empty or over 40 characters | `A name is required (up to 40 characters).` |
| 400 | Name reserved, contains a comma, or all digits | `That name can't be used. Pick a name without commas that isn't everyone, adults or kids.` |
| 400 | Name taken (case-insensitive) | `A person named Midge already exists.` |
| 400 | `kind` or `color` not in the allowed set | `kind must be adult or kid.` / `color must be one of green, blue, red, pink, purple.` |
| 400 | A 9th active person | `At most 8 active people.` |
| 400 | Delete a person with attendance | `Midge is on 29 watches. Deactivate them instead.` |
| 404 | No such id | `No such person` |

### 6.2 Logging a watch

`POST /admin/api/titles/<id>/watches` gains an optional `attendees`: a list of person ids or names.

| Body | Result |
|---|---|
| `attendees` absent | All active people. With no people set up: no rows, as today. |
| `"attendees": [1, 2]` or `["Nick", "Lauren"]` | Exactly those people. |
| `"attendees": []` | 400 `A watch needs at least one person.` |
| Unknown id or name | 400 `Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige.` |

The response gains two keys; nothing else changes:

```json
{"ok": true, "watch": {"id": 88, "title_id": 3, "watched_date": "2026-11-11", "notes": null,
                       "date_bucket": null, "attendance": "recorded", "attendees": [1, 2]}}
```

A request that sends no `attendees` (the current SPA, an older MCP client, a `curl`) therefore records
everyone. That is the agreed default, and it is why the upgrade needs no client change to stay correct.

### 6.3 Correcting a watch

`PUT /admin/api/titles/<id>/watches/<watch_id>/attendees` with `{"attendees": [1, 2]}` replaces the set in
one transaction → `{"ok": true, "watch": {…as above…}}`. The same validation as §6.2; 404
`No such watch entry` for a watch that is not on that title. This is also how an unresolved watch is
resolved: sending all five ids turns `assumed_everyone` into `recorded`.

### 6.4 Reads

| Route | Editor (`/admin/api`) | Public (`/api`) |
|---|---|---|
| `titles/<id>` | Each watch gains `attendance` and `attendees` (effective ids, so an assumed watch lists who it is assumed for). The title gains `seen_by`: the ids of everyone with a `seen` row. | No new key. `watch_out` strips `attendance` and `attendees`; `seen_by` is not added. |
| `recommendations?audience=kids` | Applies §5. | 400 `Sign in to pick for specific people.` |
| `titles?unseen_by=kids` | Owned titles nobody in the audience has seen. | 400, same message. |
| `titles?attendance=unknown` | Owned titles with at least one unresolved watch. Empty when no people exist. | 400, same message. |
| `people` | §6.1. | No such route (404). |

A public request gets 400, not a silently unfiltered list: an ignored `audience` would look as if it had
worked.

Editor title detail, *The Dark Knight* with one old watch and one recorded one:

```json
{"id": 3, "title": "The Dark Knight", "user_rating": 9.5, "seen_by": [1, 2],
 "watches": [
   {"id": 88, "watched_date": "2026-11-11", "date_bucket": null, "notes": "girls at Grandma's",
    "attendance": "recorded", "attendees": [1, 2]},
   {"id": 12, "watched_date": "2015-06-01", "date_bucket": "over_10_years", "notes": null,
    "attendance": "assumed_adults", "attendees": [1, 2]}]}
```

The same title on public `/api/titles/3` is exactly today's body: rating 9.5, two watches with dates and
buckets, no notes, no people.

## 7. UI (412 px, Android Chrome first; signed-in editors only)

Nothing below renders for an anonymous visitor, and nothing renders for an editor until a person exists.

### 7.1 People screen

A **People** button joins the admin header row next to Shelves & Locations and opens a modal in the style
of the locations manager (new module `static/js/people.js`). Each row: a color dot, the name, an
Adult/Kid switch, a color picker of five swatches, up/down order buttons, and Deactivate (or Delete when the
person has no watches). An "Add person" form sits at the bottom. `state.people` is fetched once on admin
load, beside `/admin/api/me`, and refreshed after any change here.

### 7.2 Title view: logging

A **Who watched** row sits directly under "+ Watched Today": one dot per active person, in order, each a
filled circle in the person's color with their initial (N L M A P) and `aria-pressed`. All start on.
Tapping a dot turns that person off for the next log (outlined, dimmed). Every log button in the section
(Watched Today, Log Date, the six buckets) sends the current selection as `attendees`. After a successful
log the selection resets to everyone. With every dot off, the log buttons are disabled.

This row is its own line, not inline with the button: each dot needs a 44 px tap target, and five of them
are 220 px, which does not fit beside "+ Watched Today" at 412 px. It costs editors about 48 px of height in
the watch section. On desktop the row sits beside the button.

### 7.3 Title view: saved watches

Each watch row shows small display dots (16 px, not buttons) for its attendees after the date. An
unresolved watch shows a grey "?" dot and the text "assumed everyone" or "assumed adults". Tapping the dots
opens an inline editor on that row: the same 44 px toggles, plus **Save** and **Cancel**. Save sends §6.3.

### 7.4 Movie Night

Above the rating cap, a **Who's watching** radiogroup: Everyone, Adults, Kids, and Custom. Custom reveals
the person dots. The choice is remembered per device in `localStorage` (`matinee.movieNight.audience`), with
the same guarded access as the cap. The request adds `&audience=…`. The pick card gains a line of display
dots for `seen_by` when anyone outside the audience has seen the film.

Anonymous visitors get today's picker with no audience row.

### 7.5 Grid

The existing Watched filter popover gains two editor-only groups: **Unseen by** (Everyone, Adults, Kids,
then each person) which sets `unseen_by`, and **Who watched: unknown** which sets `attendance=unknown`.
The second is the worklist for resolving old watches.

### 7.6 Colors and contrast

Dots use `--p-user1` … `--p-user5` as the background with `#0b0b0e` initials. Each pair must meet WCAG AA
(≥ 4.5:1), and each color must reach 3:1 against `#16161c` as a non-text shape. An off dot is a 2 px outline
in the same color with `--ink-2` text. Color is never the only signal: the initial and `aria-label`
("Midge, watched") carry the same information.

## 8. MCP (`mcp_server.py`)

| Tool | Change |
|---|---|
| `list_people` (new, read) | `[{"id", "name", "kind", "color", "active"}]`. Registered at every write level. |
| `get_recommendations` | New `audience: Optional[str]` (§5). The result carries `audience` and per-result `seen_by`. |
| `search_collection` | New `unseen_by: Optional[str]`, same grammar. |
| `get_movie` | Each watch gains `attendance` and `attendees` as names. The movie gains `seen_by` and `not_seen_by` (names of active people). |
| `log_watch` | New `attendees: Optional[List[str]]` (names). Omitted means everyone. Stays in the `log` and `all` levels. |

- **DB mode** reads `people` and `watch_people` directly and uses `recommend.SEEN_CTE`. A database older
  than v9 has no `people` table; DB mode checks `sqlite_master` and treats that as "no people".
- **REST mode** uses the `/admin/api` routes above. The read key is enough for every read here.
- **Never trust an older server with an audience.** As with `max_cert`, a server that predates this change
  ignores `audience` and `unseen_by` and would return an unfiltered list. The REST client calls
  `GET /people` first whenever either argument is set; a 404 raises
  `MatineeError("This Matinee server is too old to pick for specific people. Update Matinee.")`.
  For `get_recommendations` it also requires the `audience` echo in the response.
- **Public mode** (no key, `/api`): the server's 400 message reaches the model as the tool error.
- **Server instructions** gain: `"The girls" or "the kids": audience="kids". "Us two", "date night":
  audience="adults". A named person: audience="Alice". After watching, pass attendees to log_watch only when
  someone was missing.`
- **Annotations:** `list_people` is read-only and idempotent; the others keep theirs.
- `docs/mcp-setup.md` documents the argument, the tool and the older-server refusal.

### Scenario: next in the series for the twins

Given the family owns all eight *Harry Potter* films, Midge has seen the first four, and Alice and Paige
have seen the first two with everyone, when the Claude app asks "what's next in Harry Potter for Alice and
Paige?" the model calls `get_recommendations(mode="next_in_franchise", audience="Alice,Paige")`. The result
is *Harry Potter and the Prisoner of Azkaban* with `seen_by: ["Midge"]` and the reason
"Harry Potter Collection · Next after Harry Potter and the Chamber of Secrets". With `audience="kids"` the
same call returns *Harry Potter and the Order of the Phoenix*: the first film none of the three has seen.

## 9. Ask (`ask.py`)

- The hand-written schemas gain `audience` on `get_recommendations` and `unseen_by` on `search_collection`,
  and `list_people` joins the read tools (six read tools plus `show_results`). The schema-drift test keeps
  them in step with `MatineeClient`.
- The system prompt replaces its kids' line with: `"The girls", "the kids": audience="kids"; add
  max_cert="PG-13" unless a rating is named. "Us two": audience="adults". A named person: audience is their
  name.`
- Ask is editor-only, so it may read attendance. Rows are still hydrated from the database by id, and they
  do not show attendance.
- **Data sent to Anthropic** now includes people's first names and who watched what, when a tool result
  carries them. The README's "what is sent" paragraph says so.
- This closes the "per-person history" deferral in the Ask spec for reads. Saved threads per person stay
  deferred.

### Scenario: the girls haven't seen it

Given *Toy Story 3* (G) has a recorded watch by all five, *Inside Out* (PG) has a recorded watch by Nick and
Lauren only, and *Harry Potter and the Philosopher's Stone* (PG) has a 2014 watch with no attendees, when
an editor asks "something the girls haven't seen", the model calls
`get_recommendations(mode="never_watched", audience="kids", max_cert="PG-13")`. *Toy Story 3* is left out
(they saw it). *Inside Out* is offered. *Philosopher's Stone* is offered too: its only watch is over ten
years old, so it counts for the adults alone.

## 10. Testing

No live network. Fixtures already in the repo: 155 *The Dark Knight*, 150540 *Inside Out*, 10193
*Toy Story 3*, 671 *Harry Potter and the Philosopher's Stone*, plus the franchise fixtures
`tests/test_recommend.py` uses. A helper `seed_people(con)` in `tests/conftest.py` inserts the five people
of §6.1.

| File | Cases |
|---|---|
| `tests/test_people.py` (new) | Migration: v9 from a fresh DB and from a v8 database with watches and ratings, run twice; row counts of `watches` and `user_ratings` unchanged; `meta.schema == '9'`. CRUD happy paths. Every 400 and 404 of §6.1. Read key may `GET`, not `POST`. Anonymous gets 401. No `/api/people` route. Delete with attendance refused; deactivate keeps `watch_people` rows. |
| `tests/test_attendance.py` (new) | Log with no `attendees` records all active people (an inactive one is left out). Ids and names both accepted. Empty list and unknown person are 400 and write no watch. With no people, a log writes no `watch_people` rows. `PUT …/attendees` replaces the set and turns an assumed watch into a recorded one. Deleting a watch removes its rows. `attendance` is `assumed_adults` for bucket `over_10_years` and for a date 11 years back, `assumed_everyone` for a date 9 years back. `seen_by` on the title. |
| `tests/test_privacy.py` (extend) | Anonymous `/api/titles/<id>` has no `attendance`, `attendees` or `seen_by`, at any depth. `audience`, `unseen_by` and `attendance` on `/api` are 400. `GET /api/people` is 404. Names of seeded people appear in no public response (titles, detail, stats, recommendations, facets). |
| `tests/test_recommend.py` (extend) | `resolve_audience`: keywords, names, ids, mixed case, inactive by name, each error message. Each mode of §5.1 with audience `kids`, `adults` and a pair. No audience: every existing test passes unchanged, and the response has no `audience` or `seen_by` key. The assumed-adults rule in `never_watched`. Franchise filters still apply after the pick. |
| `tests/test_recommendations.py` (extend) | Route: `audience` on `/admin/api` works; on `/api` is 400; unknown person is 400 with the list. `unseen_by` and `attendance=unknown` on `/admin/api/titles`. |
| `tests/test_mcp_server.py` (extend) | `list_people`; `audience` and `unseen_by` in DB and REST mode give the same answer; `get_movie` names; `log_watch` sends `attendees`; a pre-v9 database in DB mode means "no people"; an older REST server (404 on `/people`) raises the refusal instead of returning results; tool list per write level. |
| `tests/test_ask.py` (extend) | Tool names are the six read tools plus `show_results`; schema drift test covers the new arguments; the system prompt names `audience`. |
| `tests/test_static_ui.py` (extend) | People button and every dot are inside `isAdmin` branches; names go through `esc()`; `people.js` is in the module list; new CSS uses defined custom properties; the five dot pairs pass the contrast rules of §7.6; toggles are ≥ 44 px; the Movie Night audience storage is guarded. |
| `tests/test_people_ui.py` (new) | Playwright at 412×915 and 360 px, with `/admin/api/*` stubbed: five dots render under "+ Watched Today"; turning three off and tapping the button posts `attendees: [1, 2]`; a row editor saves; `scrollWidth` does not exceed the viewport. Runs in the CI UI smoke job. |

CI is otherwise unchanged; `tests/test_js_modules.py` and the `node --check` loop pick up `people.js`.

## 11. Docs and rollout

- `docs/vision.md`: a short "People" paragraph after "Core model", and principle 6 notes that attendance is
  editor-only.
- `README.md`: a "People and who watched" section, and the Ask "what is sent" paragraph.
- `docs/mcp-setup.md`: §8.
- **Deploy:** run the installer; `SELECT v FROM meta WHERE k='schema'` → `9`; add the five people in the
  admin view; resolve old watches from the grid's "Who watched: unknown" filter over time.
- **Rollback:** reverting the code leaves two unused tables. Nothing existing was altered, so there is
  nothing to restore.

## 12. Per-person ratings (considered, deferred)

| Option | Migration | 412 px consequence | Verdict |
|---|---|---|---|
| **1. None** — the household rating only | Nothing beyond §4. | Unchanged: *Paddington 2* shows one "★ 9.5". | **Chosen.** It is what the family does. |
| **2. Optional per-person ratings** beside the household one | One additive table `person_ratings(person_id, title_id, rating, updated_at, PRIMARY KEY (person_id, title_id))`. `user_ratings` untouched. | A row of up to five pills under the big number (`N 9` `L 10` `M 10`), about 60 px each, editor-only. Two numbers can disagree. | Deferred. Additive on top of this change whenever it is wanted. |
| **3. Household rating derived** from per-person ones | The same table, plus a rule for the existing ratings (keep as a fallback, or copy to all five and invent opinions). | The one-tap household control goes away; the big number becomes "avg of 2". A public number would be derived from editor-only data. | Ruled out. It replaces an agreed number with arithmetic. |

## 13. Deferred

- Per-person ratings (§12, option 2).
- A person's Access email, and preselecting "me" from the signed-in editor. Trigger: the first kid with her
  own device.
- A near-miss fallback when the strict list is empty ("New to Alice and Paige · Midge saw it Nov 2026").
- A `seen_by=` filter ("everything Midge has seen") and per-person stats.
- Per-person columns in the CSV export.
- Guests as people. The table allows them today (up to 8 active); nothing is designed around them.
- Any public display of people or attendance.
- Bulk-resolving old watches (for example "mark all assumed-everyone watches before 2020 as adults").

## 14. Security and privacy

- **Editor-only by construction.** There is no public route for people; `watch_out` strips the two watch
  keys; `seen_by` is added only on the private path; an audience can only be named under `/admin/api`.
- **Read key.** It can read people and attendance, as it can already read watch notes. The remote MCP
  service therefore sees them at write level `none`.
- **Write key and `lan`.** They can create people and set attendance, like any other editor write.
- **Third parties.** First names and attendance go to Anthropic through Ask and through any Claude client
  using the MCP tools. Nothing else leaves the host.
- **Injection.** A person's name is editor-entered text. It is escaped in the SPA, and tool results that
  carry it are data like any other collection text.
