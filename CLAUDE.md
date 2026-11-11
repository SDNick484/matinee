# Matinee — rules for agents and contributors

Self-hosted, phone-first movie collection tracker (physical discs + digital libraries). Single-file Flask app
(`app.py`), SQLite, vanilla-JS SPA (`static/`, native ES modules in `static/js/`), a FastMCP server
(`mcp_server.py`, `mcp_http.py`), an Ask feature (`ask.py`), and an Alpine/OpenRC installer (`installer/`). Read
the code before proposing changes; trust the code over any doc that disagrees.

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
   `PUBLIC_VIEW`. State whether any new data is public or editor-only.
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

Run and report all of these before opening the PR:

```sh
python -m pytest -q
for f in static/js/*.js; do node --check "$f"; done
sh installer/build.sh && sh -n dist/setup-matinee.sh
```

Then push the branch, open a PR against `main` (do not merge it), and wait for CI: every job (Python 3.10, 3.11,
3.12, 3.13 and Alpine) must finish green. If one fails, fix it, push, and wait again.
