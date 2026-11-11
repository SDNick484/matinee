# Privacy and read-only key — implementation plan

**Spec:** `docs/superpowers/specs/2026-11-11-privacy-read-key-design.md` (source of truth; if the code disagrees,
stop and ask).
**Branch:** `feat/privacy-read-key`. One Conventional Commit per task, author
`SDNick484 <SDNick484@users.noreply.github.com>`. `python -m pytest -q` green after every task.

Scenario used throughout: *The Dark Knight* (fixture 155), rating 9.5, one watch on 2026-11-07, note
"girls loved it".

### Task 1 — Watch notes editor-only on `/api`

- [ ] Tests first (`tests/test_privacy.py`): anonymous detail has rating 9.5, the watch date, no `notes` key;
      LAN-editor `/admin/api` detail has the note; the note text appears in no other public body
      (`/api/titles`, `/api/stats`, `/api/recommendations`, `/api/facets`, `/api/locations`); every `notes`
      use in `static/app.js` is inside a `w.notes ?` guard. Move the public-notes assertion in
      `tests/test_watch_notes.py` to `/admin/api`.
- [ ] Run: the anonymous test fails (notes present).
- [ ] `app.py`: `PRIVATE_WATCH = ("notes",)`, `watch_out(row, private)`, used by `title_detail`.
- [ ] Green. Commit `feat(privacy): watch notes are editor-only on the public API`.

### Task 2 — `MATINEE_READ_KEY` in the app

- [ ] Tests: read key GET detail 200 with notes; HEAD 200; `X-API-Key` works; POST watch, PUT rating, DELETE
      title, PUT copy, DELETE watch → 403 `"The read key can only read"` and the DB is unchanged; GET with
      `Origin: https://evil.example.com` still 200 (key exempt); equal keys → `api-key`; empty read key never
      matches an empty/blank token; `/admin/api/me` → `email: "read-key"`; Ask → 403 for `api-key` and
      `read-key` (the read key is refused before Ask runs).
- [ ] `app.py`: `MATINEE_READ_KEY` setting (ignored with a logged error when equal to the API key),
      `_presented_key()`, identity order api-key → read-key → JWT → LAN, `KEY_IDENTITIES`, the method gate in
      `editor_required`; module docstring env list. `ask.py`: refuse both key identities.
      `.env.example`: one commented line.
- [ ] Green, including `test_env_example_lists_every_documented_variable`.
- [ ] Commit `feat(auth): add MATINEE_READ_KEY, a key that can only read /admin/api`.

### Task 3 — `matinee-mcp` uses the read key in read-only mode

- [ ] Tests (`tests/test_mcp_http.py`): `service_key()` per mode; read-only main builds the client with the
      read key and `key_name="MATINEE_READ_KEY"`; refuses (exit 2, stderr names the key) when it is empty;
      refuses when `MATINEE_API_KEY` is also set; writes mode uses and requires `MATINEE_API_KEY`; `get_movie`
      through the HTTP app returns the note served by the fake Matinee's `/admin/api`; 401 message names the
      read key. Existing tests that passed only `MATINEE_API_KEY` in read-only mode switch to the read key.
      `tests/test_mcp_server.py`: stdio falls back to `MATINEE_READ_KEY`.
- [ ] `mcp_http.py`: `service_key()`, the two refusals, the client with `key_name`. `mcp_server.py`:
      `MATINEE_READ_KEY` module setting, the stdio fallback, `key_name` in the 401/403 message.
- [ ] Green. Commit `feat(mcp): read-only matinee-mcp authenticates with MATINEE_READ_KEY`.

### Task 4 — Installer

- [ ] Tests (`tests/test_installer_static.py`): `add_setting "$CONF" MATINEE_READ_KEY "$(gen_key)"`; a re-run
      keeps an existing value (execute the function in `sh`); the `matinee` export line has it; extract the
      `matinee-mcp` `case` block and run it in `sh` for `1`, `0`, ` OFF `, `garbage`, unset → exactly one of
      the two keys is exported, matching `mcp_http.readonly()`; the start condition and banner use the mode's
      key.
- [ ] `installer/setup-head.sh`: `gen_key()`, the setting, both init scripts, `mcp_key_name()` for the start
      condition and banner.
- [ ] `sh installer/build.sh && sh -n dist/setup-matinee.sh`; green.
- [ ] Commit `feat(installer): generate MATINEE_READ_KEY and give each service only its key`.

### Task 5 — Docs

- [ ] `README.md`: what `/api` shows (ratings yes, notes no), the read key. `docs/mcp-setup.md`: §1 and §4.1
      settings table (the read key replaces the API key in read-only mode), the rollback row for a leaked key;
      the existing `test_mcp_setup_states_what_the_api_key_adds_to_reads` is updated to the new wording.
      Remote-MCP spec §14 security table: the read-only rows now hold the read key. Hygiene test passes.
- [ ] Commit `docs: public vs editor-only data and the read key`.

### Task 6 — Release

- [ ] `python -m pytest -q`, `node -c static/app.js`, `sh installer/build.sh && sh -n dist/setup-matinee.sh`.
- [ ] Push, open the PR against `main` (do not merge), poll CI until every job is green.
- [ ] Nick, on the host: run the installer; check the three points in spec §9; ask the Claude app on the Pixel
      "What did we think of The Dark Knight?" and see the note; check the public detail page on the phone
      shows the watch date and no note.
