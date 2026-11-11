# Privacy and a read-only key — design

**Date:** 2026-11-11
**Branch:** `feat/privacy-read-key` (one PR)
**Schema:** unchanged (v8). No table, column or row changes.
**Status:** approved decisions (roadmap 2026-11-10 §2, decision 1); implemented on this branch.
**Verified against:** `main` @ `a273ec2` (PRs #18 LAN cross-site, #19 housekeeping, #20 CI hardening merged).

Addresses review findings F2 (the read-only remote MCP service holds a write-capable key) and F3 (watch notes are
public), and finding F5 of `.superpowers/review/2026-11-08-spec-reconciliation.md`.

---

## 1. Intent

Watch notes are family diary entries ("girls loved it", "Grandma fell asleep halfway"). They should be visible
to editors and to the family's own assistant, not to anyone with the public URL. Ratings are agreed by the family
together and stay public.

The always-on `matinee-mcp` service only reads by default, yet today it can only authenticate with
`MATINEE_API_KEY`, which can delete titles. A second key that can only read lets the service keep its read
tools (including notes and `search_tmdb`) without holding a write credential.

### Goals

- Anonymous `GET /api/titles/<id>`: each watch keeps `id`, `watched_date`, `date_bucket`, `created_at`; the
  `notes` key is absent. `user_rating`, `last_watched` and `watch_count` stay.
- `GET /admin/api/titles/<id>` (any editor identity, including the new read key): notes as today.
- `MATINEE_READ_KEY`: a second key; identity `"read-key"`; `GET`/`HEAD` under `/admin/api` only; any other
  method answers `403 {"error": "The read key can only read"}`. `MATINEE_API_KEY` is unchanged.
- `matinee-mcp` read-only (`MATINEE_MCP_READONLY=1`, the default): uses `MATINEE_READ_KEY` and does not have
  `MATINEE_API_KEY` in its environment. With writes on: `MATINEE_API_KEY`, as today. It refuses to start when
  the key it needs is empty.
- The read tools still return watch notes.

### Non-goals

- Making ratings editor-only (decided: public).
- Scoping the read key to particular routes beyond the method rule (§7).
- Key rotation tooling, multiple read keys, per-person keys.
- Tiered MCP writes (`MATINEE_MCP_WRITES`), which is roadmap PR #5.

## 2. What is public today, and after (audit of every `/api` response)

Seeded as in §8: *The Dark Knight* (TMDB 155), rating 9.5, one watch on 2026-11-07 with the note
"girls loved it".

| Public response | Watch data in it today | Notes? | After |
|---|---|---|---|
| `GET /api/titles/<id>` (`title_detail(private=False)`) | `watches[]`: `id, watched_date, notes, date_bucket, created_at`; `last_watched`, `watch_count` | **Yes** | `notes` key removed from each watch |
| `GET /api/titles` grid cards (`_card`) | `last_watched`, `watch_count` | No | unchanged |
| `GET /api/facets` | none | No | unchanged |
| `GET /api/stats` | counts only (`watched_stats`) | No | unchanged |
| `GET /api/recommendations` (`recommend.py`) | `last_watched`, `watch_count`; watches used only in `WHERE`/CTEs | No | unchanged |
| `GET /api/locations` | none | No | unchanged |
| `GET /api/me` | none | No | unchanged |
| Search (`?q=`) | `search_text` is built from title and original title only (`search_fold`) | No (no search oracle) | unchanged |

`title_detail()` is the only public path that reads `watches.notes`. The other private field is copies'
`added_by` (`PRIVATE_COPY`), already stripped on `/api`. The fix follows that pattern:

```python
PRIVATE_WATCH = ("notes",)
...
d["watches"] = [watch_out(w, private) for w in watches]
```

### Example

```http
GET /api/titles/1
```
```json
{"title": "The Dark Knight", "year": 2008, "user_rating": 9.5,
 "last_watched": "2026-11-07", "watch_count": 1,
 "watches": [{"id": 1, "watched_date": "2026-11-07", "date_bucket": null,
              "created_at": "2026-11-07 20:41:03"}], "...": "..."}
```

```http
GET /admin/api/titles/1
Authorization: Bearer <MATINEE_READ_KEY>
```
```json
{"title": "The Dark Knight", "user_rating": 9.5,
 "watches": [{"id": 1, "watched_date": "2026-11-07", "notes": "girls loved it", "date_bucket": null,
              "created_at": "2026-11-07 20:41:03"}], "...": "..."}
```

**UI (412 px).** The public title view renders notes only under `${w.notes ? … : ""}` (`static/app.js`), so
an absent key renders the same as a watch with no notes: the date line, no notes line. No JS change. A static
test pins that every `notes` use in `app.js` is guarded.

## 3. `MATINEE_READ_KEY`

### 3.1 Identity

`access_identity()` reads one bearer token (`Authorization: Bearer …` or `X-API-Key: …`) and compares it,
in constant time, with `MATINEE_API_KEY` first (→ `"api-key"`), then `MATINEE_READ_KEY` (→ `"read-key"`).
A token that matches neither falls through to the Access JWT and LAN checks exactly as today.

If both keys are set to the same value the token is the API key: the app logs an error at import and ignores
`MATINEE_READ_KEY`. The installer generates distinct keys, so this only happens by hand-editing.

### 3.2 Gate

In `editor_required`, after the identity is known:

| Identity | Method | Result |
|---|---|---|
| `read-key` | `GET`, `HEAD` | allowed (HEAD is GET without a body) |
| `read-key` | `POST`, `PUT`, `PATCH`, `DELETE`, … | `403 {"error": "The read key can only read"}` |
| `api-key`, `read-key` | any | exempt from the cross-site check (not browser credentials) |
| others | any | unchanged |

The rule lives in `editor_required`, so it covers every `/admin/api` route present and future, including
`POST /admin/api/ask`. `ask_route()` also refuses both key identities explicitly
(`403 "Ask is for signed-in editors."`), so Ask stays closed to keys even if the method rule changes.

### 3.3 What the read key can reach

Every `GET` under `/admin/api`: titles with notes, `added_by`, facets, stats, recommendations, locations,
`/admin/api/me` (`"email": "read-key"`), `/admin/api/tmdb/search` (what `search_tmdb` uses),
`/admin/api/tmdb/<id>/owned`, and `/admin/api/upc/<upc>`. Also `/images/*` when `PUBLIC_VIEW=0`.

`GET /admin/api/upc/<upc>` is the one GET with a side effect: it spends a UPCitemdb lookup (100/day) and
writes `upc_cache`. Accepted: no MCP tool uses it and the worst case for a leaked read key is a day's
UPC quota. Recorded in §7.

## 4. `matinee-mcp`

```python
def service_key(env) -> tuple[str, str]:
    """(name, value) of the key this service uses: MATINEE_READ_KEY when read-only, else MATINEE_API_KEY."""
```

`main()` order:

1. `name, key = service_key(env)`; empty → exit 2, `matinee-mcp: MATINEE_READ_KEY is empty; …`.
2. Read-only and `MATINEE_API_KEY` non-empty in the environment → exit 2,
   `matinee-mcp: MATINEE_API_KEY must not be in this service's environment while it is read-only …`.
   The init script never exports it in that mode (§5); this catches a hand-run or a stale init script.
3. Access settings check (unchanged).
4. `MatineeClient(api_url=base, api_key=key, key_name=name, db_path="")`. Reads go to `/admin/api`, so
   `get_movie` returns notes.

`MatineeClient` gains `key_name` (default `"MATINEE_API_KEY"`) for the 401/403 message only:
`Matinee rejected the service's key (HTTP 401). Check MATINEE_READ_KEY on the Matinee host.`

**stdio (`mcp_server.py`)** keeps its defaults, with one addition: when `MATINEE_API_KEY` is empty and
`MATINEE_READ_KEY` is set, the client uses the read key. A workstation that only needs reads can then see notes
without holding a write key; its write tools answer the 403 above.

## 5. Installer and settings

| Setting | Default written | Read by | Exported to |
|---|---|---|---|
| `MATINEE_READ_KEY` | generated, 43 URL-safe chars (`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`), only when the line is absent | `matinee`, `matinee-mcp` | `matinee` always; `matinee-mcp` only when read-only |
| `MATINEE_API_KEY` | `""` (unchanged) | `matinee`, `matinee-mcp` | `matinee` always; `matinee-mcp` only when writes are on |

`/etc/init.d/matinee-mcp` (it sources the root-only config, then exports what the service needs):

```sh
case "$(printf %s "${MATINEE_MCP_READONLY:-1}" | tr -d ' \t' | tr 'A-Z' 'a-z')" in
  0|false|no|off) unset MATINEE_READ_KEY; export MATINEE_API_KEY ;;
  *)              unset MATINEE_API_KEY;  export MATINEE_READ_KEY ;;
esac
```

The `case` mirrors `mcp_http.readonly()`. `unset` matters: a sourced but unexported variable does not reach the
child, but `unset` also covers one inherited from the caller's environment.

The installer starts `matinee-mcp` only when the key for its mode is set (was: always `MATINEE_API_KEY`), and the
"NOT started" banner names that key. An existing install picks up a generated `MATINEE_READ_KEY` on its next
installer run; nothing else changes until then (§9).

`.env.example` gains `MATINEE_READ_KEY=` with a comment line.

## 6. Security after this change

| If this leaks or is abused | What the holder gets |
|---|---|
| Anonymous visitor, `PUBLIC_VIEW=1` | Titles, copies (no `added_by`), ratings, watch dates and counts. **No notes.** |
| `MATINEE_READ_KEY` | Everything an editor can read (notes, `added_by`, TMDB search) and a day's UPC quota. No writes: every non-GET is 403. |
| Code execution as `matinee-mcp`, read-only | `MATINEE_READ_KEY` only. Was: the write-capable `MATINEE_API_KEY`. |
| Code execution as `matinee-mcp`, writes on | `MATINEE_API_KEY`, as before. |
| A stolen Access session on the MCP endpoint | Read tools: notes included. Writes only if `MATINEE_MCP_READONLY=0`. |

## 7. Deferred

- Route-scoped read key (deny `GET /admin/api/upc/<upc>`), if the UPC quota ever matters.
- `MATINEE_MCP_WRITES=none|log|all` (roadmap #5); with it, `log` mode would still need the write key.
- Read-only server instructions and the `log_movie_night` prompt when writes are off (review F10).
- Rotating keys without a restart.

## 8. Tests (no live network)

Scenario: *The Dark Knight* (fixture 155) via `seed_title`, rating 9.5, one watch on 2026-11-07 with the note
"girls loved it".

| File | Cases |
|---|---|
| `tests/test_privacy.py` (new) | anonymous `/api/titles/<id>`: rating 9.5, watch date present, no `notes` key; LAN editor `/admin/api/titles/<id>`: notes present; grid, stats, recommendations, facets, locations bodies don't contain the note text; read key: `GET /admin/api/titles/<id>` 200 with notes, `HEAD` 200, `X-API-Key` header works; `POST /admin/api/titles/<id>/watches`, `PUT …/rating`, `DELETE /admin/api/titles/<id>`, `PUT /admin/api/copies/<id>`, `DELETE …/watches/<id>` → 403 and nothing changed; read key exempt from the cross-site check on GET; equal keys → `api-key`; empty read key never matches; `/admin/api/me` reports `read-key`; Ask refuses `api-key` and `read-key`; `app.js` guards every `notes` use |
| `tests/test_watch_notes.py` | the existing public-notes assertion moves to `/admin/api` |
| `tests/test_mcp_http.py` | read-only main uses `MATINEE_READ_KEY`; refuses when it is empty; refuses when `MATINEE_API_KEY` is also set; writes main uses `MATINEE_API_KEY`, refuses when empty; read tools through the HTTP app return notes from the fake Matinee; 401 message names the key |
| `tests/test_mcp_server.py` | stdio falls back to `MATINEE_READ_KEY`; `/admin/api` prefix with a read key |
| `tests/test_installer_static.py` | `add_setting … MATINEE_READ_KEY` with a generated value; the generated line is not overwritten on a re-run (run `add_setting` in `sh`); `matinee` exports it; the `matinee-mcp` init script's `case` exports exactly one key per mode (executed in `sh` for `1`, `0`, ` OFF `, `garbage`, unset); start condition and banner use the mode's key |

## 9. Deploy (host)

1. Build and run the installer. It adds `MATINEE_READ_KEY=<generated>` and rewrites both init scripts.
2. `rc-service matinee restart && rc-service matinee-mcp restart` (the installer restarts both when configured).
3. Check: anonymous `curl -s http://127.0.0.1:8771/api/titles/<id>` has no `notes`; the Claude app's
   `get_movie` for a watched title still shows them; `cat /proc/$(pgrep -f mcp_http.py)/environ | tr '\0' '\n' | grep -c MATINEE_API_KEY` prints `0`.

Rollback: revert the PR and re-run the previous installer. The stray `MATINEE_READ_KEY` line in
`/etc/conf.d/matinee` is harmless to the old code.
