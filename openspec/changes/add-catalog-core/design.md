# Design

## Context

The project is greenfield. The directory holds only `docs/movie-app-prompt.md` (the product vision; `openspec/config.yaml` points to `docs/vision.md`, which does not exist) and OpenSpec scaffolding. It is not a git repository yet.

The reference implementation is SDNick484/roasted. Its conventions, which Matinee follows unless noted under *Divergences*:

- One Flask module, `app.py`, plus SQLite, with sections marked by `# ===` banners.
- Stdlib `urllib` for outbound HTTP; no `requests` dependency.
- Schema is `CREATE TABLE IF NOT EXISTS` in a `SCHEMA` string. A `migrate(con)` step adds columns to older databases, and a `meta` table records the schema version.
- Pages and routes:
  - `/` and `/api/...` are public. `/admin` and `/admin/api/...` require `editor_required`, which checks the Cloudflare Access JWT (PyJWT + JWKS) and allows LAN requests when `LAN_EDIT=1`.
  - The same `static/index.html` + `app.js` single-page app serves both; it detects `/admin` from `location.pathname`.
  - The frontend is vanilla JS with hash routing and no build step; fonts are bundled.
- Config lives in `/etc/conf.d/<app>` and is exported by the OpenRC script. gunicorn runs under `supervise-daemon`.
- Deployment is `installer/build.sh`, which produces a one-file `dist/setup-<app>.sh` (header + base64 tarball). It is re-runnable and keeps the data directory and config.
- Ops: `ops/<app>-backup` runs in `/etc/periodic/daily`. It is registered in `HEALTH_CHECKS` in `/etc/conf.d/ct210-ops` and has a `/healthz` endpoint.
- Tests are `tests/test_app.py`, using Flask's `test_client`, `DATA_DIR` in a temp folder, and monkeypatched network calls.

Requirements are in `specs/` (catalog, tmdb-metadata, collection-browse, editor-access). This document covers how they are met.

## Goals / Non-Goals

**Goals:**
- A schema that phases 2–5 can extend by adding columns and tables, without rewriting phase-1 tables.
- At most 4 outbound requests per added movie (1 TMDB details + up to 3 images), and none for browsing.
- A grid that stays fast on a mid-range Android phone with ~1,000 titles.

**Non-Goals:**
- No background jobs or schedulers. Phase 4 adds the refresh job.
- No pagination. The whole filtered list is returned; the collection is small.
- No offline/PWA caching beyond the browser's normal HTTP cache. Roasted ships a manifest, and Matinee ships one too for the home-screen icon, but there is no service worker.

## Decisions

### D1. Data model (phase 1 tables)

```sql
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);              -- schema version = '1'
CREATE TABLE titles (
  id INTEGER PRIMARY KEY,
  tmdb_id INTEGER NOT NULL UNIQUE,
  imdb_id TEXT,
  title TEXT NOT NULL,
  sort_title TEXT NOT NULL,        -- folded, leading The/A/An removed
  search_text TEXT NOT NULL,       -- folded title + original title, for accent-free LIKE
  year INTEGER, release_date TEXT, runtime INTEGER, overview TEXT,
  certification TEXT,              -- NULL = no US certification (never '')
  collection_tmdb_id INTEGER, collection_name TEXT,
  tmdb_vote REAL, tmdb_votes INTEGER,
  poster_path TEXT, backdrop_path TEXT,      -- TMDB paths, kept so images can be re-fetched
  poster_file TEXT, backdrop_file TEXT,      -- local files; NULL = placeholder / retry on refresh
  fetched_at TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX titles_collection ON titles(collection_tmdb_id);
CREATE TABLE genres (id INTEGER PRIMARY KEY, name TEXT NOT NULL);   -- id = TMDB genre id
CREATE TABLE title_genres (
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genres(id),
  PRIMARY KEY (title_id, genre_id)
);
CREATE TABLE copies (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('physical','digital')),
  format TEXT NOT NULL,
  service TEXT,                    -- digital only
  added_by TEXT,                   -- editor email, 'lan', or NULL
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX copies_title ON copies(title_id);
```

- **Integer surrogate `titles.id` with a UNIQUE `tmdb_id`, not `tmdb_id` as the primary key.** Later phases may need Titles that TMDB lacks, such as home videos or unmatched bulk-paste lines. A nullable or synthetic TMDB key would be awkward then. Detail URLs use `titles.id`.
- **Genres are their own table keyed by the TMDB genre id.** Phase 2 tags are user-defined and colored, so they get a separate `tags` table and are not mixed into `genres`.
- **Formats and services are Python constants with a `CHECK` only on `kind`.** Format and service are checked in the app, so adding a format later needs no table rebuild. Services may become an editable table later, like roasted's `methods`, once the family has answered which ones they actually use.
- **No `packages` table yet.** Phase 3 adds `packages` and `copies.package_id` through `migrate()`. Phase 2 adds `edition`, `location_id`, `loaned_to`, `code_status`, `purchased_on`, `price` and `notes` to `copies` the same way.
- **"Date added" is derived (`MIN(copies.created_at)`), not stored on titles**, so deleting and re-adding copies keeps it accurate with no extra bookkeeping.
- **Owned** means `EXISTS (SELECT 1 FROM copies WHERE title_id = titles.id)`.
- **Migrations** follow the roasted pattern: idempotent `SCHEMA`, then `migrate()` with `PRAGMA table_info` checks, then a bump of `meta.schema`. Phase 1 starts at schema `1`. `PRAGMA foreign_keys=ON` and WAL mode are set as in roasted.

### D2. TMDB client

- v3 API with the bearer read token `TMDB_READ_TOKEN`, through a `tmdb_get(path, **params)` built on roasted's `http_json` (with its UA string and an 8 s timeout).
- **Search** calls `/search/movie` with `query`, `include_adult=false`, `language=en-US` and `page=1`, plus `primary_release_year` when a trailing year from 1888 through next year is present. It falls back to the title without the year when the narrowed search returns nothing. Each result is tagged `owned` with a single `tmdb_id IN (...)` query.
- **Details** call `/movie/{id}` with `append_to_response=release_dates`. That one request returns `imdb_id`, genres, runtime, `belongs_to_collection`, image paths and certifications.
- **Certification**: take the `US` entry of `release_dates`. Use the first non-empty `certification` of type 3 (theatrical), then type 2 (limited), then any type. Store NULL if there is none.
- **Errors** map to one `TmdbError(reason, message)`, which routes return as JSON `{error, reason}`:

  | reason | HTTP | when |
  |---|---|---|
  | `not_configured` | 503 | no token |
  | `not_found` | 404 | TMDB answers 404 |
  | `rate_limited` | 503 | TMDB answers 429; the message includes Retry-After when present |
  | `unavailable` | 502 | timeout, connection error or 5xx |

  There are no automatic retries. A person is always waiting on the request, and a 429 shows up straight away.
- **Search thumbnails in the add screen hotlink `image.tmdb.org/t/p/w154`.** They are transient, editor-only results, and caching images for movies we never add would waste disk and requests. Only Titles get local images.
- **Alternative considered:** an existing TMDB client library. Rejected, because roasted avoids extra Python deps (Alpine packages only), and the two endpoints we need are trivial.

### D3. Image cache

- On create or refresh, download `w342` and `w780` posters (grid and detail) and a `w780` backdrop. The files are `DATA_DIR/images/<tmdb_id>-p342.jpg`, `-p780.jpg` and `-b780.jpg`, and are served from `/images/<file>` with `max-age` of 30 days. URLs carry `?v=<fetched_at>` so a refresh busts the cache.
- **Backdrop at w780, not w1280**: it is shown blurred, so the extra resolution is wasted bytes on phones. The blur is CSS (`filter: blur()` on a scaled-down layer), so no server-side image processing is needed. That keeps Pillow out of the install.
- A failed or missing image leaves its `*_file` NULL. The UI then shows a placeholder card (title + year on a dark card) or a plain hero, and the next refresh retries. Each image download is independent, so a failed backdrop does not discard a good poster.
- Writes go to a temp name followed by `os.replace`, so a half-downloaded file is never served.
- Deleting a Title unlinks its image files.

### D4. HTTP API shape

- Public routes:
  - `GET /api/titles?q=&kind=&format=&service=&genre=&sort=` returns owned titles, each with id, title, year, poster URL and the distinct owned formats.
  - `GET /api/titles/<id>` returns the detail with copies.
  - `GET /api/facets` returns the formats, services and genres present in the collection.
  - `GET /api/me` returns whether the request is from an editor.
- Admin routes mirror these. They add `added_by`, and the writes:

  | Route | Purpose |
  |---|---|
  | `GET /admin/api/tmdb/search?q=` | TMDB search |
  | `GET /admin/api/tmdb/<tmdb_id>/owned` | what is already owned, for the add form |
  | `POST /admin/api/copies` | add a copy; body `{tmdb_id, kind, format, service}`, creates the Title if needed |
  | `PUT /admin/api/copies/<id>` | edit a copy |
  | `DELETE /admin/api/copies/<id>` | delete a copy |
  | `POST /admin/api/titles/<id>/refresh` | refresh from TMDB |
  | `DELETE /admin/api/titles/<id>` | delete a title |

- Public serializers drop `added_by`. This is the roasted `PRIVATE_*` tuple pattern.
- **Search and filters run in SQL**, as one query with `EXISTS` subqueries on `copies` and `title_genres`. Text search folds the query with roasted's `fold()` and ANDs one `search_text LIKE %word%` per word.
- Sorts:
  - `title` sorts by `sort_title`.
  - `year` sorts by `year DESC, sort_title`.
  - `added` (the default) sorts by `MIN(copies.created_at) DESC`.
- This JSON surface is the start of phase 6's "API for everything the UI does".

### D5. Frontend

- Vanilla JS with hash routes, as in roasted: `#/` grid, `#/t/<id>` detail, `#/add` and `#/about`. Grid state lives in the hash query, e.g. `#/?genre=Animation&sort=title`.
- The grid view's DOM and scroll position are kept in memory when the detail page opens, so going Back restores both without a re-fetch.
- The grid is CSS `grid-template-columns: repeat(auto-fill, minmax(104px, 1fr))`, which gives 3 columns at 360–412 px. Posters use `loading="lazy"`, `aspect-ratio: 2/3` and explicit sizes to avoid layout shift.
- On the add flow:
  - Search is debounced at 400 ms and runs at a minimum of 2 characters.
  - Picking a result fetches what is already owned and opens the copy form. Kind is a toggle; format and service are chip pickers filtered by kind.
  - A failed save keeps the form state. On a 401, it shows roasted's "Your sign-in expired…" toast.
- **Design tokens get a review step before the screens are built out**. Task 3.1 delivers a token sheet (colors, type scale, spacing, radius, the chip palette) as a static page for review and approval. The starting direction is:
  - Near-black surfaces (`#0b0b0e` page, `#16161c` card) and warm ivory ink.
  - A cinema-red accent for actions, and a marquee-bulb amber used sparingly for highlights.
  - Format chips: gold `4K`, blue `Blu-ray`, gray `DVD/VHS`, teal `Digital HD/SD`. Every chip pairing is checked for ≥ 4.5:1 text contrast. Digital 4K uses gold (resolution wins over medium), and the service text tells it apart from physical 4K.
  - Person colors are reserved in the tokens now (green, blue, red, pink, purple) but unused until phase 5.

### D6. Deployment

- Service id, user and paths are all `matinee`: `/opt/matinee`, `/var/lib/matinee/matinee.db` plus `images/`, `/etc/conf.d/matinee`, `/var/log/matinee.log`.
- Port 8769 is the next free one after Roasted's 8768. `MATINEE_PORT` overrides it on first install, the same as `ROASTED_PORT`.
- Settings: `PORT`, `DATA_DIR`, `CF_TEAM_DOMAIN` and `ADMIN_EMAILS` (both copied from `/etc/conf.d/cookbooks` when present), `CF_AUD`, `LAN_EDIT=1`, `PUBLIC_VIEW=1`, and `TMDB_READ_TOKEN`.
- The installer appends `Matinee|http://127.0.0.1:8769/healthz|matinee` to `HEALTH_CHECKS`. `/healthz` returns `{ok, titles, copies}`.
- `ops/matinee-backup` writes the `matinee-matinee.db.gz` snapshot. With `BACKUP_TARGET` set, it also rsyncs `images/` to `<target>/files/matinee/images/`, so a restore needs no re-download from TMDB.

### Divergences from roasted

| # | Divergence | Why |
|---|---|---|
| 1 | Its own dark-only "home theater" tokens instead of Café Green, with no light theme | The product brief asks for a dark home-theater look. A light variant can be added later behind `prefers-color-scheme` if wanted. |
| 2 | No `py3-pillow` | No server-side image processing in phase 1 (D3). Phase 2 cover photos will bring it back. |
| 3 | `.env.example` in the repo | The brief lists it as a deliverable. It documents the variables only; runtime config stays in `/etc/conf.d/matinee`, as in roasted, and the app does not load a `.env` file. |
| 4 | `DATA_DIR/images/` for TMDB art, separate from a future `photos/` | It keeps re-fetchable TMDB art apart from irreplaceable family photos, which matters for backup priority. |
| 5 | No MCP endpoint yet | It is deferred to phase 6. When it lands, it will follow roasted's `/mcp` and `/mcp/<MCP_TOKEN>` pattern. |

Still the same as roasted: the single `app.py`. If it grows past roughly 2,500 lines in later phases (scanner pipeline, recommender), splitting out `tmdb.py`, `omdb.py` and similar will be proposed as its own decision.

## Risks / Trade-offs

- [TMDB changes or removes a movie id] → Refresh keeps the cached data and reports `not_found` (spec). Titles never depend on TMDB being live.
- [TMDB terms require refreshing cached data older than ~6 months, but phase 1 has no scheduler] → `fetched_at` is recorded now, and manual refresh exists. If phase 4 slips, a one-line cron calling the refresh endpoint can bridge the gap.
- [The blurred CSS backdrop is janky on low-end phones] → The blur is applied to a small layer with `will-change: transform`. If it is still slow, fall back to a pre-darkened gradient over the unblurred w780 image.
- [Hotlinked search thumbnails fail when image.tmdb.org is down] → `<img>` errors swap in a placeholder. Search itself is already failing in that case.
- [Accent folding in Python while SQLite matches with `LIKE`] → `search_text` is stored pre-folded, and queries are folded the same way, so both sides match. Unit tests cover *Amélie* and *WALL·E*.
- [The provisional service list does not match what the family uses] → "Other" covers gaps, and the list is a constant that is easy to change without a migration.

## Migration Plan

This is a first install, so there is nothing to migrate.

1. Run `sh installer/build.sh`, then `pct push 210 dist/setup-matinee.sh /root/` and `pct exec 210 -- sh /root/setup-matinee.sh`.
2. Set `TMDB_READ_TOKEN` and `CF_AUD` in `/etc/conf.d/matinee`, then run `rc-service matinee restart`.
3. Cloudflare setup:
   - Add the tunnel public hostname `movies.example.com` → `http://<server-ip>:8769`.
   - Add an Access self-hosted app on path `admin`, with a One-time PIN policy for the editors.
   - Keep `/` and `/api` outside the Access app.
4. Rollback: `sh setup-matinee.sh --uninstall` removes the app and service and keeps `/var/lib/matinee` and the backups. Remove the tunnel hostname to take it offline.

## Open Questions

- The final token values (exact hex, display face if any) are settled in the token review (task 3.1). The review changes no spec and no task breakdown.
- The GitHub repo name and visibility are settled when ready to push. The plan only runs `git init` locally.
