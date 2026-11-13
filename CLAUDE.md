# Matinee — rules for agents and contributors

Self-hosted, phone-first movie collection tracker (physical discs + digital libraries). A Flask app (`app.py`:
the Flask object, auth, pages and every route) with route-free code in sibling modules (`settings.py`,
`common.py`, `db.py`, `clients.py`, `images.py`, `people.py`, `browse.py`, `export.py`, `wishlist.py`), SQLite, vanilla-JS SPA
(`static/`, native ES modules in `static/js/`), a FastMCP server (`mcp_server.py`, `mcp_http.py`), an Ask
feature (`ask.py`), and an Alpine/OpenRC installer (`installer/`). Read the code before proposing changes; trust
the code over any doc that disagrees.

**Python modules.** The sibling modules never import `app` (or `ask`), and none holds the Flask object, so a
route, an `@app` hook or an `app.logger` call stays in `app.py`. `app.py` re-exports every moved name: patch and
read `app.X` (tests, `ops/*.py`), never the sibling module, because `_AppModule` copies a write to `app.X` into
each module that holds `X`. A new module goes in `_SPLIT_MODULES` and in `installer/build.sh`'s `cp` line
(`tests/test_installer_static.py` fails otherwise).

## Engineering rules

1. **Footprint.** The Flask app's runtime dependencies are Alpine `py3-*` packages installed by `apk` (the CI
   Alpine job enforces this). The one exception is the separate `matinee-mcp` venv (`requirements-mcp.txt`).
   No frontend framework, bundler or npm build step; plain files in `static/`. The SPA is native ES modules in
   `static/js/` (`main.js` is the entry): import with `import { name } from "./x.js";`, list exports in one
   `export { … };` at the end, and keep `core.js` and `icons.js` free of imports. `render_index()` versions
   every module through an import map, so never hand-edit `?v=`. `tests/test_js_modules.py` checks the graph.
2. **Schema.** Changes go through an idempotent `migrate()` that works from every earlier version (v1 →
   current), and `SCHEMA_VERSION` is bumped. Indexes that depend on new columns are created in `migrate()`, not
   in `SCHEMA`. No manual DB step on the host, ever.
3. **Tests.** pytest with recorded fixtures (`tests/fixtures/tmdb/`) and no live network. Add a fixture rather
   than calling an API; external-API features test a lookup miss and quota/HTTP errors.
4. **CI** must stay green: Python 3.10–3.13 on Ubuntu, the Alpine container job, the lint and UI smoke jobs,
   and `node --check` on every `static/js/*.js`. `static/js/package.json` (`{"type": "module"}`) is what makes
   Node parse them as modules; without it a broken file passes.
5. **Auth.** Writes live under `/admin/api/` with `@editor_required`. Reads live under `/api/` and respect
   `PUBLIC_VIEW`. State whether any new data is public or editor-only. Watch notes, people and attendance are
   editor-only: the public `/api` never carries them and answers 400 to `audience`, `unseen_by` and
   `attendance`. `tests/test_privacy.py` walks every `/api/` rule in `app.url_map`; a new public route must pass it.
6. **MCP in step.** New data an assistant would want means a new or extended tool in `mcp_server.py`, a test in
   `tests/test_mcp_server.py`, and an update to `docs/mcp-setup.md`.
7. **Settings.** Every new setting goes in `.env.example` (with a comment line above it), in the installer's
   `add_setting` list (add missing keys, never overwrite), and in the `export` line of the init script that needs
   it. Anything new in `$DATA_DIR` must be covered by `ops/matinee-backup` and kept by the installer.
8. **Hygiene.** No personal hostnames, LAN addresses or container IDs outside the exempt design history
   (`tests/test_hygiene.py`). Use `movies.example.com`, `mcp.example.com`, `192.168.1.100`.
9. **Commits.** Conventional Commits (`feat(scope):`, `fix:`, `docs:`, `chore:`, `ci:`, `test:`), one logical
   change each, on a feature branch merged by PR. Author `SDNick484 <SDNick484@users.noreply.github.com>`.
10. **Docs and shell.** The owner runs Gentoo. Write OpenRC commands (`rc-service matinee restart`), never
    systemd, and say `vim`, not `vi`.

## Workflow (Superpowers)

OpenSpec is retired; `openspec/` is historical reference only.

- Specs: `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md`
- Plans: `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
- Subagent-driven-development ledger: `.superpowers/sdd/`; reviews: `.superpowers/review/`

Brainstorm, then spec, then plan, then TDD on a feature branch. Specs use real titles and realistic retail
strings in scenarios (for example UPC `883929637581` → "The Dark Knight Trilogy [4K UHD + Blu-ray + Digital]"),
never placeholders. Each change is independently shippable.

## Definition of done

Run and report all of these before opening the PR (CI runs the same):

```sh
python -m pytest -q                       # report the skip count and reasons
for f in static/js/*.js; do node --check "$f"; done
ruff check
shellcheck -s sh installer/setup-head.sh installer/build.sh ops/matinee-backup ops/matinee-refresh ops/matinee-restore-test
sh installer/build.sh && sh -n dist/setup-matinee.sh
```

`tests/test_ask_ui.py`, `tests/test_people_ui.py` and `tests/test_wishlist_ui.py` need Playwright and Chromium
(`pip install playwright && python -m playwright install chromium`); without them they skip locally, but the UI
smoke job fails on any skip.

Then push the branch, open a PR against `main` (do not merge it), and wait for CI: all seven jobs must finish
green — Lint (shellcheck + ruff), UI smoke (Playwright + Chromium, 412 px), Python 3.10, 3.11, 3.12 and 3.13 on
Ubuntu, and Alpine. If one fails, fix it, push, and wait again.
