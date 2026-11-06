# Proposal

## Why

The family's movies are spread across shelves, bins and five or more digital storefronts, and nobody can answer "do we own *Paddington 2*, and where is it?" without hunting. This is build phase 1 of the tracker in `docs/movie-app-prompt.md`. It sets up the Title-versus-Copy data model that every later phase builds on, and ships a usable collection on day one: search TMDB, add what you own, then browse it as a poster grid on the phone.

## What Changes

- New Flask + SQLite app laid out with standard lightweight conventions (`app.py`, `static/`, `tests/`, `installer/`, `ops/`), named **Matinee**. It runs as the OpenRC service `matinee` on port 8769, at movies.example.com.
- **Catalog data model, first slice**:
  - **Title** is one movie, keyed by TMDB id. It holds the cached TMDB metadata: IMDb id, year, runtime, overview, US certification, genres, collection/franchise, poster, backdrop, TMDB vote average and `fetched_at`.
  - **Copy** is one owned instance with only kind (physical/digital), format, and service (digital only).
- **Add by TMDB search**: an editor types a title (optionally with a year, e.g. "Dune 1984"), picks one of the poster results, chooses kind/format/service, and saves. Adding a movie that is already in the catalog adds a Copy to the existing Title. The editor is told which copies are already owned first.
- **TMDB integration**: search, a full detail fetch on add, the US certification from release dates, local caching of the poster and backdrop images, a manual "Refresh from TMDB" per title, and clear handling when nothing matches or TMDB is rate-limiting.
- **Browse**:
  - A phone-first poster grid of owned titles, with text search, filters (physical/digital, format, service, genre) and sorts (title, year, date added).
  - A detail page with a blurred backdrop hero, poster, metadata, the TMDB score, and every copy shown as format chips with its service.
- **Access**: the same pattern as roasted. `/` and `/api` are open for family browsing. `/admin` and `/admin/api` sit behind Cloudflare Access with in-app JWT verification, and LAN editing is allowed when `LAN_EDIT=1`.
- An About page with the required TMDB attribution.
- A dark "home theater" design-token set, proposed and approved before the screens are built out.
- An installer, a nightly backup, a health check entry, and a README with deploy notes, all matching roasted.

### Explicitly deferred

| Deferred item | Later change (phase) |
|---|---|
| Editions, location tree and "Where is it?", loaned_to, digital-code status, purchase date/price/notes, cover photos, tags, CSV import/export, bulk paste | phase 2 (`add-collection-details`) |
| Packages/UPCs, box sets, barcode scanning, batch queue, scan-time duplicate warnings | phase 3 |
| OMDb ratings (IMDb/RT/Metacritic) and the scheduled background refresh job (6-month TMDB re-fetch) | phase 4 |
| People, seen/ratings/vetoes, watch log, audience profiles, recommender, person color dots | phase 5 |
| MCP server, "unwatched / not seen by" filters, collections (franchise) view, TV poster-wall view | phase 6 |

## Capabilities

### New Capabilities
- `catalog`: Titles and Copies. Adding owned copies of a movie, editing and removing copies, removing titles, and folding a repeat add into the existing Title.
- `tmdb-metadata`: Finding movies on TMDB and caching their metadata and images locally. Covers manual refresh, rate-limit and lookup-miss behavior, and TMDB attribution.
- `collection-browse`: The public poster grid with search, filters and sorts, plus the title detail page.
- `editor-access`: Which paths are public and which need an editor, how editors are identified (Cloudflare Access JWT, LAN), and what anonymous visitors can and cannot do.

### Modified Capabilities
- None. There are no existing specs.

## Impact

- **New code**: the whole app (greenfield; this directory currently holds only docs and OpenSpec).
- **New external dependency**: the TMDB v3 API, via a read access token (`TMDB_READ_TOKEN`) and `image.tmdb.org` for images.
- **Systems**:
  - On CT 210: a new `matinee` user, `/opt/matinee`, `/var/lib/matinee` (`matinee.db`), `/etc/conf.d/matinee` and port 8769.
  - In `/etc/conf.d/ct210-ops`: a new `HEALTH_CHECKS` entry, and nightly backups into `/var/backups/ct210`.
  - In Cloudflare: a tunnel hostname, plus an Access application on path `admin`.
- **Runtime packages** (Alpine): python3, py3-flask, py3-gunicorn, py3-jwt, py3-cryptography, sqlite. These are the same as roasted's, minus py3-pillow, which is not needed until phase 2 cover photos.
