# Tasks

## 1. Project scaffold

- [x] 1.1 Run `git init` and add the roasted-style layout: `app.py`, `static/`, `tests/`, `installer/`, `ops/`, and a `.gitignore` copied from roasted (data/, dist/, caches). Verify with `git status`, which should show the new files and no `data/` folder.
- [x] 1.2 Write the `app.py` skeleton: the docstring header (model, routes, env vars), config from env (`DATA_DIR`, `CF_TEAM_DOMAIN`, `CF_AUD`, `ADMIN_EMAILS`, `LAN_EDIT`, `PUBLIC_VIEW`, `TMDB_READ_TOKEN`), `APP_NAME = "Matinee"`, a UA string, and roasted's `clean`/`fold`/`make_err`/`http_json` helpers. Add `/healthz`. Verify that `python3 app.py` serves `{"ok": true, "titles": 0, "copies": 0}` at `http://localhost:8769/healthz`.
- [x] 1.3 Add `.env.example`, listing `TMDB_READ_TOKEN` and the Access/LAN settings with one-line comments (design, divergence 3), and add a short note at the top of the README. Verify that every variable the docstring names appears in `.env.example`.

## 2. Data model and editor access

- [x] 2.1 Implement `SCHEMA` (meta, titles, genres, title_genres, copies, exactly as in design D1), `connect()` with foreign keys + WAL, `migrate()`, and `init_db()` writing `meta.schema = '1'`. Add a `tests/test_app.py` test that the tables and indexes exist, and that deleting a title cascades to its copies and title_genres.
- [x] 2.2 Add the `FORMATS` (physical/digital lists) and `SERVICES` constants and a `copy_values(data)` validator. Cover with tests:
  - DVD as digital is rejected, with the message about physical formats.
  - Digital without a service is rejected.
  - A physical copy with a service has the service dropped.
  - Valid *Frozen* Digital HD/Movies Anywhere is accepted.
- [x] 2.3 Port roasted's `access_identity()`, `editor_required` and `who()`, and the `/` vs `/admin` page routes (honoring `PUBLIC_VIEW`). Add tests:
  - `Cf-Ray` without a JWT gives 401 "Sign in to edit" on `/admin/api/me`.
  - LAN with `LAN_EDIT=1` is allowed.
  - `LAN_EDIT=0` refuses.
  - A JWT with the wrong audience is refused (monkeypatched JWKS).
  - An email that is not on `ADMIN_EMAILS` is refused.

## 3. Design tokens (review step)

- [x] 3.1 Build a static token sheet (`docs/tokens.html`) showing the proposed dark home-theater palette (page/card/ink/accent/amber), type scale (bundled Figtree from roasted, plus any display face proposed), spacing, radii, the four format-chip colors with measured contrast ratios, and the reserved person colors. Get approval or edits before task 7.1. Verify by recording the approved values in `static/style.css` `:root`, and confirm every chip/ink pairing is ≥ 4.5:1.

## 4. TMDB client

- [x] 4.1 Implement `tmdb_get()` with the bearer token, 8 s timeout and the `TmdbError` reasons (`not_configured`, `not_found`, `rate_limited` with Retry-After, `unavailable`) mapped to the HTTP codes in design D2. Add unit tests with a fake `urlopen`, one per reason: no token; 404 for id 99999999; 429 for "jaws"; a timeout.
- [x] 4.2 Implement `tmdb_search(q)`. It parses a trailing year from 1888 through next year and falls back to the title without the year, and it tags `owned`. Save recorded TMDB responses as fixtures in `tests/fixtures/tmdb/`, and test:
  - "Dune 1984" puts the 1984 film first.
  - "Elf 2004" falls back to "Elf".
  - "Blade Runner 2049" sends no year.
  - "toy story" marks only the owned 1995 film.
  - "teh godfathr part iv" returns an empty list with the "No TMDB matches" message.
- [x] 4.3 Implement `tmdb_details(tmdb_id)` and the mapping to Title columns, including `sort_title`, `search_text` and the US certification rule (theatrical → limited → any → NULL). Test against fixtures:
  - *Spirited Away* gives PG.
  - *Harry Potter and the Philosopher's Stone* gives tt0241527, "Harry Potter Collection", Adventure/Fantasy/Family.
  - A fixture with no US entry gives NULL.
  - `sort_title` for *The Incredibles* is "incredibles".
- [x] 4.4 Implement the image cache: w342 and w780 posters and a w780 backdrop into `DATA_DIR/images/`, written via a temp file + `os.replace`, with each image independent. Serve `/images/<file>` with a 30-day max-age. Test with a fake downloader:
  - All three save.
  - A 429 on the backdrop still saves the poster and leaves `backdrop_file` NULL.
  - A missing `poster_path` leaves `poster_file` NULL.

## 5. Catalog writes

- [x] 5.1 Implement `POST /admin/api/copies`. It validates the copy, reuses an existing Title with no TMDB call, or else fetches details and images and inserts the Title + genres + Copy in one transaction, and records `added_by`. Test:
  - *Spirited Away* Blu-ray creates the Title and the copy.
  - A second *Dark Knight* copy reuses the Title while `tmdb_get` raises.
  - A 404 or 429 from TMDB leaves no rows.
  - Two *Dune* ids give two Titles.
- [x] 5.2 Implement `GET /admin/api/tmdb/search` and `GET /admin/api/tmdb/<tmdb_id>/owned`. The latter returns a copy summary such as "DVD · Digital HD (Movies Anywhere)". Test with *The Princess Bride* fixtures, and test that a duplicate Blu-ray of *Jurassic Park* is allowed.
- [x] 5.3 Implement `PUT` and `DELETE /admin/api/copies/<id>`. Test:
  - *Inception* Blu-ray → 4K UHD.
  - An invalid kind/format change is rejected.
  - Deleting the last *Cars 2* copy keeps the Title and drops it from the owned list.
- [x] 5.4 Implement `POST /admin/api/titles/<id>/refresh` and `DELETE /admin/api/titles/<id>`. Test:
  - A refresh updates the overview and `fetched_at` and leaves the copies untouched.
  - A refresh hitting 404 or 429 leaves the row byte-for-byte unchanged and returns the matching message.
  - Deleting *Cats* removes the row, its copies and its image files.

## 6. Browse API

- [x] 6.1 Implement `GET /api/titles` (and its admin twin) with `q` (folded, per-word AND), `kind`, `format`, `service`, `genre`, and sort `added` (default) / `title` / `year`. It returns owned titles only, with their distinct formats and a date added derived from the earliest copy. Test:
  - "amelie" finds *Amélie*.
  - physical + Blu-ray filtering works.
  - Movies Anywhere filtering works.
  - Animation + "toy" excludes *Toys* (1992).
  - The order is *A Bug's Life*, *The Incredibles*, *Inside Out*.
  - *Moana*'s date added is 2026-10-01.
- [x] 6.2 Implement `GET /api/titles/<id>` (copies, genres, image URLs with `?v=fetched_at`; 404 for an unknown id) and `GET /api/facets` (only the values present). Test:
  - The public response has no `added_by`, and the admin one does.
  - VHS is absent from the facets when none is owned.
  - POST/PUT/DELETE on `/api/...` are refused.
  - Titles with no copies are still reachable by id.

## 7. Browse UI (phone-first)

- [x] 7.1 Port roasted's `index.html` / `app.js` shell: `api()` with the 401 toast, `esc`, the toast, and hash routing for `#/`, `#/t/<id>`, `#/add` and `#/about`. Add the approved tokens from 3.1 to `style.css`. Verify that `#/about` and an unknown hash both render on desktop Chrome with no console errors.
- [x] 7.2 Build the poster grid:
  - Lazy, 2:3 posters at `minmax(104px, 1fr)`, with placeholder cards and format chips.
  - The search box and filter chips are driven by `/api/facets`.
  - Sort select, with state kept in the hash query.
  - Empty states for both editors and non-editors.

  Verify in Chrome DevTools at 412×915: there are at least 3 columns and no horizontal scroll, `#/?genre=Animation&sort=title` restores on reload, and the "Nothing in the collection matches 'godzilla'" state shows.
- [x] 7.3 Build the detail page:
  - A blurred-backdrop hero with a plain fallback, and the poster.
  - The "1994 · 1h 29m · G" line, no badge when certification is NULL, genres, overview, collection name and the TMDB score.
  - Copy chips with "Digital 4K · Movies Anywhere" style text, and the "not in the collection right now" and not-found states.

  Keep the grid DOM and scroll position for Back. Verify with *The Lion King* (1994) seeded data, and that going Back from *Coco* returns to the same filtered scroll position.
- [x] 7.4 Add the editor controls under `/admin` only: "Add a copy", edit/delete on each copy, "Refresh from TMDB" and "Delete title" (each with a confirm). Verify that `/admin/#/t/<moana>` shows them, that `/#/t/<moana>` shows none, and that the refresh error toasts appear for mocked 404/429.

## 8. Add flow UI

- [x] 8.1 Build the `#/add` search screen: debounced at 400 ms, minimum 2 characters, a results list with w154 thumbnails (falling back to a placeholder on error), and an "In collection" badge on owned results. Show distinct messages for no matches / rate-limited / unavailable / not configured, and keep the search text. Verify each message against a dev server with `TMDB_READ_TOKEN` unset, and against a monkeypatched 429.
- [x] 8.2 Build the copy form:
  - A physical/digital toggle, with format chips filtered by kind and service chips for digital only.
  - The "Already in the collection: …" line from `/owned`.
  - Save and "Add another copy".
  - The form state survives a failed save (TMDB 429, or a 401 sign-in-expired toast).

  Verify by adding *Paddington 2* Blu-ray and then a Digital HD copy on Movies Anywhere, which should show one Title with two chips in the grid.

## 9. About page and docs

- [x] 9.1 Build the About page with the TMDB logo (bundled SVG in `static/`), the exact attribution sentence and a link to themoviedb.org, plus a footer link to it on every view. Verify on a 412 px viewport that the logo and notice are visible from the grid footer.
- [x] 9.2 Write the README, following roasted's structure: what it is, how it's organized (Title vs Copy table), install/upgrade on CT 210, Cloudflare setup (tunnel + Access on path `admin`), TMDB token setup, adding a movie, backups and alerts, development (`python3 app.py`, `python3 -m pytest -q tests`), look and feel, and credits/attribution. Verify that the development commands run as written in a fresh clone.

## 10. Installer and ops

- [x] 10.1 Adapt `installer/build.sh` and `installer/setup-head.sh` from roasted for `matinee`:
  - Port 8769, with a `MATINEE_PORT` override.
  - The apk package list without pillow.
  - `/var/lib/matinee/images`.
  - `add_setting` lines for the design D6 variables (`CF_TEAM_DOMAIN` and `ADMIN_EMAILS` copied from cookbooks).
  - The OpenRC script under `supervise-daemon`.
  - The `HEALTH_CHECKS` entry, `--uninstall`, and the final hints, including "TMDB is off until you set TMDB_READ_TOKEN".

  Verify that `sh installer/build.sh` produces `dist/setup-matinee.sh` and that `sh -n` passes.
- [x] 10.2 Write `ops/matinee-backup`, adapted from roasted: the SQLite `.backup` + integrity check + gzip to `/var/backups/ct210/<date>/matinee-matinee.db.gz`, and an `images/` copy to `$BACKUP_TARGET/files/matinee/images/` when set. Verify by running it against a local test DB with `LOCAL_DIR` pointed at a temp folder; it should log "ok" and produce a readable gz.

## 11. Deploy to CT 210 and phone smoke test

- [ ] 11.1 Build and run the installer on CT 210 (`pct push 210 …`, then `pct exec 210 -- sh /root/setup-matinee.sh`). Set `TMDB_READ_TOKEN` and `CF_AUD` in `/etc/conf.d/matinee`, then run `rc-service matinee restart`. Verify that `wget -qO- http://127.0.0.1:8769/healthz` returns ok, and that a re-run of the installer keeps the data.
- [ ] 11.2 Configure Cloudflare: the tunnel hostname `movies.example.com` → port 8769, and an Access app on path `admin` with One-time PIN for the editors. Verify that `https://movies.example.com/` loads without a sign-in, `/admin` prompts for the PIN, and `curl https://movies.example.com/admin/api/me` without a session returns 401 or the Access redirect.
- [ ] 11.3 Smoke test on an Android phone in Chrome:
  - Sign in at `/admin`, search "Spirited Away" and add a Blu-ray copy.
  - Add *Paddington 2* in Digital HD on Movies Anywhere.
  - Add a second copy of *Spirited Away* and confirm the "Already in the collection" line shows.
  - Open the public `/` on the same phone without signing in, filter to Animation, open *Spirited Away* and go Back.
  - Confirm there is no horizontal scroll and that the About attribution shows.

  Record the outcome (pass/fail per step) in the change notes.
- [ ] 11.4 Confirm that the nightly backup ran (`/var/backups/ct210/<date>/matinee-matinee.db.gz` exists, and `/var/log/ct210-ops.log` shows ok) and that the 15-minute health check lists Matinee.

## Workflow follow-up

- Archive the change with `/opsx:archive` after the phone smoke test passes.
- Next planned change: phase 2 `add-collection-details` (editions, locations tree, tags, CSV import/export, bulk paste).
