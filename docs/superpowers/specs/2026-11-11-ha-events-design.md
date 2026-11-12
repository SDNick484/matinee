# Home Assistant watch events — design

**Date:** 2026-11-11
**Branch:** `feat/ha-events` (one PR)
**Schema:** unchanged (v9)
**Status:** proposed in PR #31 (decisions from Nick's prompt 13, 2026-11-11; amended the same day: fires only for the + Watched Today button).
**Verified against:** `matinee` `main` @ `225af12` (merge of PR #30).

## 1. Intent

When someone taps + Watched Today, Matinee tells Home Assistant, so an automation can react: dim the lights,
announce the film, start "movie night". Matinee only reports; Home Assistant decides what to do.

### Goals

- One new setting, `HA_WEBHOOK_URL`. Empty (the default) turns the feature off and changes nothing.
- A fire-and-forget JSON `POST` to that URL when the web UI's **+ Watched Today** button logs a watch. Log Date and
  the MCP `log_watch` tool never fire (amendment: Nick wants only the deliberate tap).
- A failure, timeout or non-2xx answer from Home Assistant is logged and never changes the editor's response.

### Non-goals

- Events for ratings, new copies, edits or deletes. Retries, queues or a delivery log. Signing the request.
- Sending people, attendance or watch notes (see §3). A settings screen: it is a host setting like `LAN_EDIT`.
- A second event type for "movie finished" or "movie started". Matinee does not know when a film starts.

## 2. Data model

None. `HA_WEBHOOK_URL` is read once at import from the environment, like `OMDB_API_KEY`, and exposed as the module
constant `app.HA_WEBHOOK_URL` (tests monkeypatch it).

**It is a secret.** In Home Assistant the webhook id in the URL is the only credential. Therefore:

- It is never logged in full. Every log line names the host only (`urlsplit(url).hostname`) and the failure kind
  (an exception class name or an HTTP status), never `str(exc)`, which for some `urllib` errors can echo the URL.
- It is never returned by any route (`/api/*` or `/admin/api/*`), including the About/health views.
- It lives in `/etc/conf.d/matinee`, which the installer already keeps root-readable.

## 3. When it fires

`add_watch()` (`POST /admin/api/titles/<id>/watches`) fires after `con.commit()` succeeds, when **all** hold:

0. The body carries `"live": true` (JSON boolean). Only the `+ Watched Today` handler in `static/js/detail.js` sends it;
   Log Date, MCP and any other client do not, so they never fire. The flag is not stored and not echoed.
1. `HA_WEBHOOK_URL` is non-empty.
2. The request carried an explicit `watched_date` (or its alias `watched_at`) and **no** `date_bucket`.
3. That date equals `dt.date.today().isoformat()`, today in the **server's local time zone** (the same clock
   `add_watch` already uses for its future-date check and for bucket estimates).

So these never fire:

| Request body | Why |
|---|---|
| today's date without `live: true` (Log Date, MCP `log_watch`) | not the button |
| `{"watched_date": "2019-07-04"}` | historical |
| `{"watched_date": null}` or no date at all | dateless |
| `{"date_bucket": "over_10_years"}` | bucket: `add_watch` *estimates* a date from it, which is not "today" |
| `{"watched_date": "2026-11-11", "date_bucket": "past_year"}` | a bucket was sent, so it is a back-fill |

Back-filling years of history therefore never triggers a "movie night".

Editing afterwards never fires: `PUT .../watches/<id>/attendees`, rating, certification and delete are separate
routes and do not call the hook. Only the insert does.

### MCP: never fires

`MatineeClient.log_watch` cannot send `live`, so MCP-logged watches never fire, in REST mode and in direct-database
mode alike (Mode B's SQLite connection is `?mode=ro`, so every write reaches `add_watch()` over REST anyway).
`mcp_server.py` needs no code change; tests pin both modes to zero posts and a read-only connection.

## 4. Payload

`POST` with `Content-Type: application/json` and `User-Agent: matinee/1.0`. Scenario: *Paddington 2* (2017, 103
min, rated PG), one Blu-ray copy on Family Room › Media Cabinet › Shelf 2, logged on 2026-11-11 for title id 42:

```json
{
  "event": "matinee_watch_logged",
  "title": "Paddington 2",
  "year": 2017,
  "runtime": 103,
  "certification": "PG",
  "formats": ["Blu-ray"],
  "location": "Family Room › Media Cabinet › Shelf 2",
  "watched_date": "2026-11-11",
  "title_id": 42
}
```

- `certification` is the effective one: the editor override when set, else TMDB's. `null` when unrated.
- `formats`: the distinct formats of all the title's copies, in `recommend.sorted_formats` order (physical first).
  A title with a 4K UHD disc and a Digital HD copy gives `["4K UHD", "Digital HD"]`.
- `location`: the breadcrumb of the title's first copy that has one (copies ordered by `created_at, id`), else
  `null`. A digital-only title has `null`.
- `year`, `runtime` are `null` when TMDB had none.
- **Not sent, by design:** watch notes, people, attendees, attendance, person ids, who logged it. All are
  editor-only (CLAUDE.md rule 5) and stay inside Matinee. `tests/test_ha_events.py` asserts a notes string, the
  names Nick, Lauren, Midge, Alice, Paige and the person ids appear nowhere in the posted body.

## 5. Delivery

- `fire_watch_event(con, title_id, watched_date)` builds the payload **on the request thread** (it needs the
  request's SQLite connection, which must not cross threads), then starts a `threading.Thread(daemon=True)` that
  calls `urllib.request.urlopen(req, timeout=3)`. Standard library only: no new dependency.
- The whole of `fire_watch_event` is wrapped in `try/except Exception`: a failure building the payload is a
  warning too, never a 500. Home Assistant being down must not stop a family logging a movie.
- Outcomes, all at `WARNING` and never `str(exc)`:
  - non-2xx (`HTTPError`): `Home Assistant webhook (host homeassistant.local) answered HTTP 500`
  - timeout, refused, DNS: `Home Assistant webhook (host homeassistant.local) failed: TimeoutError`
- The 3 s is `urlopen`'s per-socket-operation timeout, not a wall-clock cap; a server that trickles bytes could
  hold the daemon thread longer. That costs one idle thread and never delays the editor, so it is accepted.
- Under `gunicorn --preload --workers 2 --threads 4` the thread is started in the worker that served the request.
  A worker restart mid-flight drops the event; there is no retry (non-goal).
- Success logs nothing (the editor already got their answer; Home Assistant's own trace shows the trigger).

## 6. Auth and privacy

- No new route. The write path is unchanged: `@editor_required` on `add_watch`. There is no public read of the
  setting or the payload, so `tests/test_privacy.py` (which walks every `/api/` rule) is unaffected.
- Data sent to Home Assistant is **title metadata plus the date**, all of it already public in the grid, except
  the fact that someone watched it today, which Home Assistant on the same LAN is trusted with.
- `http://` is expected (Home Assistant on the LAN). A user pointing the URL at an internet host should use
  `https://`; the docs say so.

## 7. Tests (`tests/test_ha_events.py`, `tests/test_mcp_server.py`)

`urllib.request.urlopen` is monkeypatched everywhere; no network. A fake `threading.Thread` runs the target
synchronously so assertions need no sleeps, and records `daemon`.

| # | Test | Expect |
|---|---|---|
| 1 | Paddington 2 logged with today's date | one POST with exactly the §4 payload, `timeout=3`, JSON content type, daemon thread |
| 2 | `watched_date: "2019-07-04"` | no call |
| 3 | `date_bucket: "over_10_years"`, and `past_year` | no call |
| 4 | no date, `watched_date: null` | no call |
| 5 | `HA_WEBHOOK_URL` empty | no call, and no thread started |
| 6 | HTTP 500 | editor still gets 200 and the watch is saved; one warning naming the host; webhook id absent from every log record |
| 7 | `TimeoutError` | same as 6 |
| 8 | people defined, `attendees` sent, notes "pizza night" | payload has no notes, no names, no person ids |
| 9 | `PUT .../attendees` after a today watch | no extra call |
| 10 | payload builder raises | editor still gets 200, warning logged |
| 11 | MCP `log_watch` with today's date, REST mode | no POST |
| 12 | MCP `log_watch`, direct-database mode | no POST, and a write on `_get_db()` raises `OperationalError` |
| 15 | today's date with `live` absent, false, `"true"`, `1` | no call |
| 16 | `live: true` with a past date, a bucket, or no date | no call |
| 13 | digital + physical copies; copy with no location; certification override | `formats`/`location`/`certification` per §4 |
| 14 | static: `.env.example`, installer `add_setting`, init-script `export` line | all three name `HA_WEBHOOK_URL` |

## 8. Settings and ops

- `.env.example`: a comment line, then `HA_WEBHOOK_URL=` (empty).
- `installer/setup-head.sh`: `add_setting "$CONF" HA_WEBHOOK_URL "" "..."` (adds the key when missing, never
  overwrites an edited value) and `HA_WEBHOOK_URL` appended to the `matinee` init script's `export` line. It is
  **not** added to the `matinee-mcp` script: the MCP service never posts to Home Assistant.
- Nothing new in `$DATA_DIR`, so `ops/matinee-backup` is unaffected. `/etc/conf.d/matinee` already holds the
  other secrets and is not part of the data backup, same as today.
- Host steps to turn it on, with `vim` and OpenRC: `vim /etc/conf.d/matinee`, set `HA_WEBHOOK_URL`,
  `rc-service matinee restart`.

## 9. Docs

- `docs/home-assistant.md`: how to create the automation, with a complete example (webhook trigger,
  `local_only: true`, a condition on `trigger.json.certification`, an action), the payload table, the when-it-fires
  table, the secret handling and a `curl` to test. Example URL `http://homeassistant.local:8123/api/webhook/<id>`.
- `README.md`: one line under the MCP/headless section, linking the doc.
- `docs/mcp-setup.md` §5.2 `log_watch`: one sentence that a watch logged for today also pings Home Assistant when
  `HA_WEBHOOK_URL` is set, in both running modes.

## 10. Deferred

- A "Test webhook" button in the admin UI (the `curl` in the doc covers it).
- Retries and a failure counter in `/healthz`.
- Firing for a bucket or past watch on request, a per-title opt-out, or an HMAC signature header.
- Other events (new title added, rating set).
