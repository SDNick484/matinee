# Split `app.py` into sibling modules — Implementation Plan

**Goal:** `app.py` (2,483 lines at `f81b715`, past the project's ~2,500 split threshold) drops to about 1,150 lines
by moving cohesive, route-free code into eight sibling modules, with **no behavior change**.

**Spec:** none (pure refactor). Starts from the seams in `.superpowers/review/2026-11-11-roadmap-prs.md` §2.16, adjusted
after reading the file (see "Differences from the review's seams"). This plan is the source of truth.

## Global constraints

- Pure move. Every moved statement keeps its exact bytes (including comments inside bodies). The only new lines are
  each new module's docstring and imports, `app.py`'s re-export imports, and the `_AppModule` block described below.
- No test is deleted or weakened. Test files change only where they name a file to scan (three places, below).
- No new dependency. Python 3.10–3.13, Alpine. No import cycle: no new module imports `app` or `ask`.
- Every route stays in `app.py` with its endpoint name, so `url_for`, the url_map and the tests are unchanged.
- Commits: one Conventional Commit per moved module, author `SDNick484 <SDNick484@users.noreply.github.com>`, branch
  `refactor/app-modules`.

## Seams

A new module may not hold the Flask object. `python3 app.py` executes `app.py` twice (as `__main__`, then as `app`),
and both runs would register their routes on one shared Flask object. So everything that is decorated
(`@app.get`, `@app.errorhandler`, …) or that calls `app.logger` stays, and so does anything that calls one of those
(`read_key_setting`, `access_identity`, `editor_required`, `save_image`, `fetch_images`, `refresh_from_tmdb`,
`post_ha_event`, `fire_watch_event`). The handlers the `/api` ↔ `/admin/api` loop registers are plain functions, so
they can move. Within that constraint, the split was computed from the call graph, so each module imports only
modules above it in this table:

| # | Module | Moves | Lines | Imports from |
|---|---|---|---|---|
| 1 | `settings.py` | `_load_env()` and every environment setting (`DATA_DIR`, `DB_PATH`, `LAN_EDIT`, `TMDB_READ_TOKEN`, …) | 64 | — |
| 2 | `common.py` | `clean`, `fold`, `words`, `search_fold`, `sort_key`, `make_err`, `validate_rating`, `now_iso` | 65 | — |
| 3 | `db.py` | `SCHEMA`, `SCHEMA_VERSION`, `FORMATS`, `SERVICES`, `SERVICE_ALIASES`, `connect`, `db`, `migrate`, `init_db` | 296 | settings |
| 4 | `clients.py` | `http_json`, `http_bytes`, TMDB (`TmdbError`, `tmdb_get`, `tmdb_search`, `title_from_tmdb`, …), OMDb (`omdb_get`, `write_omdb_ratings`), `clean_upc_title` | 262 | settings, common, db |
| 5 | `images.py` | image names and URLs, `delete_images`, custom covers (`sniff_image`, `poster_file_of`, …) | 48 | settings |
| 6 | `people.py` | person validation, `list_people`, `resolve_people`, `default_attendees`, `set_attendees`, `watch_response` | 113 | recommend, common |
| 7 | `browse.py` | serializers (`copy_out`, `watch_out`, `title_detail`), location paths, `list_titles`, cards, `facets`, `collection_stats`, `recommendations` | 546 | recommend, settings, common, db, images |
| 8 | `export.py` | `EXPORT_COLUMNS`, `csv_cell`, `export_rows`, `export_csv` | 52 | browse |
| | `app.py` | Flask app, auth, pages and static files, every route, `_AppModule` | ~1,150 | all of the above |

Stays in `app.py`: the UPC lookup (its client code is inside the route body), the people and watch routes, HA
events (`app.logger`), image downloads (`save_image` and `fetch_images`), and the write helpers (`copy_values`,
`write_title`).

**Differences from the review's seams:** `auth.py` is dropped (`access_identity` logs through `app.logger`, and
`editor_required` depends on it). The TMDB and OMDb clients share one `clients.py` with the HTTP helpers and the UPC
title cleaner. `settings.py` and `common.py` are added, because every other module needs them and must not import
`app`. Section banners (`# ===== tmdb`) move with their code, and also stay in `app.py` while code from that
section remains there.

## Settings: a settings module that `app` re-exports, plus write-through

The tests patch `app.DB_PATH`, `app.DATA_DIR`, `app.IMAGE_DIR`, `app.LAN_EDIT`, `app.MATINEE_API_KEY` and more,
and also functions (`app.tmdb_get`, `app.http_json`, `app.fetch_images`, `app._standalone_db`, …).
`ops/import_titles.py` assigns `matinee.TMDB_READ_TOKEN = …`. A moved function looks names up in its own module's
globals, so "settings stay in app and are read at call time" would mean rewriting every body to say `app.X`. That
is a body change, and it would also make each module import `app` (a cycle).

Chosen approach:

1. **`settings.py` holds the settings**, and each module imports the ones it uses (`from settings import DB_PATH`).
2. **`app.py` re-exports every name it used to define** (`from settings import …  # noqa: F401`), so `app.X`,
   `from app import X` (ask.py) and `import app as matinee` (ops/) keep working.
3. **Writes through `app` reach the moved code.** `app.py` sets its module class to `_AppModule`, whose
   `__setattr__` also rebinds the name in every split module that holds it. `monkeypatch.setattr(app, …)`, its
   undo, and plain assignment all go through `setattr`, so a moved function sees the patched value exactly as it
   did when it lived in `app.py`. This applies to settings and to patched functions alike.
4. **`_standalone_db` is forwarded, not copied.** `db.db()` rebinds it with `global`, so a copy in `app` would go
   stale. `app` keeps none: `_AppModule.__getattr__` and `__setattr__` forward that name to `db.py`.

Patch `app.X`, never the split module. The existing tests prove this unmodified (in particular `test_lan_security.py`,
`test_read_key.py`, `test_omdb.py`, `test_refresh_titles.py` and `test_import.py`).

## Hazards

| Hazard | Handling |
|---|---|
| gunicorn `app:app`, `python3 app.py`, `import app as matinee` (ops/), ask.py `from app import …`, mcp_server.py / recommend.py | Flask object, routes and the `import ask` tail stay in `app.py`; every moved name is re-exported. Smoke-run both servers and every `ops/*.py --help`; diff the url_map, error handlers and hooks against `main`. |
| Monkeypatched module attributes | Write-through above; the full suite runs unmodified. |
| Import cycles (3.10–3.13, Alpine) | New modules never import `app` or `ask`. Each is imported first in a fresh interpreter, and `app` must not be in `sys.modules` afterwards. CI's matrix and Alpine job cover the versions. |
| Installer payload | Each commit adds its module to `installer/build.sh`. A new test in `tests/test_installer_static.py` fails when any `*.py` beside `app.py` is missing from the `cp` line. The built payload is unpacked and checked by hand. |
| Init-script export test | `test_setup_head_exports_all_env_vars` scans `app.py`, `ask.py` and every module in `app._SPLIT_MODULES`. |
| Privacy AST guard | `tests/test_privacy.py` parses `app.py` and every split module as one tree, so the walk still reaches `list_titles`, `title_detail` and `watch_out`. Calls in moved bodies are bare names, which is what the walk follows. |

The three test edits, all to file names: `tests/test_app.py` (env-var scan list; the `CF_TEAM_DOMAIN =` line is now
in `settings.py`) and `tests/test_privacy.py` (`_app_tree` reads the split modules too, plus its comment). The new
installer test is the only added test.

## Tasks

- [ ] 1. `docs:` this plan.
- [ ] 2. `test(installer):` every top-level module ships in the payload (passes on `main`).
- [ ] 3–10. `refactor(<module>):` move one module per commit, in table order, with `build.sh` updated in the same
  commit. Commit 3 also adds `_AppModule` and the three scan-list edits. Each commit: full suite, ruff, and a pure-move
  check (same statements by AST, every original line of source and every comment kept, every name still `app.<name>`
  and the same object).
- [ ] 11. `docs:` CLAUDE.md, README.md and docs/vision.md no longer say "single-file Flask app" / "One Flask file".
- [ ] 12. Definition of done (CLAUDE.md), unpack `dist/setup-matinee.sh` and list the modules, push, open the PR, and
  poll CI until all seven jobs are green.

Deploying to the host is not part of this change. On the next install the payload carries the new files; there is no
data, schema or setting change.
