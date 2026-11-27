# Play on the Family Room TV, and YouTube links on digital copies (design)

Date: 2026-11-26. Repo `main` @ `856870d`, schema v12.
Size: a project. Schema v13 (one column), one new setting, three new routes, UI on three screens, one new Home
Assistant automation, one new MCP tool. Planned as five small PRs (§11).

## 1. Intent

Nick or Lauren, on a phone or a laptop, deciding what to watch. A film comes up on the main page or in the Movie
Night picker, and one tap should put it on the Family Room TV: the film itself on YouTube when we own a digital
copy there (through Movies Anywhere), or the TV set up for the disc with the shelf shown.

For that, a digital copy needs to hold its YouTube video id, the 223 films already owned on YouTube need loading
once, and the owned answer needs to carry the id so It's a Wonderful App can use it later.

### Goals

1. A digital copy stores a YouTube video id, set by pasting a link into the copy's form.
2. A one-time load of a list of owned YouTube films links the clean matches and hands back a list of the rest.
3. A ▶ on a playable film starts it on the Family Room TV through Home Assistant, and adds it to watch history.
4. `GET /admin/api/tmdb/<tmdb_id>/owned` carries the id on each digital copy.
5. An assistant can start a film on the TV through Matinee's MCP server ("put on Brooklyn").

### Non-goals

- Hearing back from the TV. Matinee does not listen for TV events or confirm the film is up (decision 5).
- A triage screen, or any stored review state, for list rows that don't load (decisions 8, 9).
- Starting a title in any app but YouTube. A digital copy with no YouTube id is not a way to play.
- MCP carrying the YouTube id in its answers (§7). The assistant names the film; Matinee looks up the link.
- The It's a Wonderful App follow-up that uses the id. Don't make it harder; don't do it.
- Volume, pause, resume position, any TV but the Family Room one.
- Correcting titles Matinee has matched to the wrong film (the comparison in §9 found some; Nick fixes them).

### Anti-goals (fails even if it technically works)

- The list load links an id to the wrong film. Anything that is not one clean match is left for a person.
- Anything but a well-formed id reaches Home Assistant.
- The webhook URL shows up in a log, an API answer, the page or the repo.
- A Home Assistant outage breaks anything that works today, or a failed play still says "Starting…".
- The personal list of 223 films, or the export it was compared with, lands in the repo (it is public).
- With the setting empty, anything about Play shows.

## 2. Decisions from brainstorming (2026-11-26)

"Nick" means he said it. "Claude's call" means Claude proposed it and Nick accepted it when it was played back
("that works"), or it was settled while writing this; these are the ones to check while reading.

| # | Decision | Choice | Whose |
|---|---|---|---|
| 1 | Where Play starts from | The main page (the grid) and the Movie Night picker | Nick |
| 2 | Grid: a film with one way to play | A ▶ on the poster; one tap starts it, no confirm | Nick |
| 3 | Grid: a film with a disc and a YouTube copy | The ▶ opens a small sheet: **Play on YouTube**, **Start the disc**, the shelf line | Nick |
| 4 | The rest of the poster | Opens the details page, as today | Nick |
| 5 | What the phone says | "Starting Brooklyn on Family Room TV". No second message when the TV gets there; finding out a play didn't land by looking at the TV is good enough | Nick |
| 6 | Reaching Home Assistant | Matinee gets its own webhook and automation and sends the event `matinee_play`; the existing automation and the shared script are not changed | Nick (option 3 of three) |
| 7 | Future purchases | Nick pastes the YouTube link into the copy when he adds it | Nick |
| 8 | The list of 223 | Clean matches link automatically; the rest come back as a list to fix | Nick |
| 9 | Fixing the rest | A plain list, fixed by pasting links into copies. No triage screen, not a permanent page | Nick |
| 10 | Play and watch history | Pressing ▶ adds the film to watch history automatically | Nick |
| 11 | Who watched | Everyone for G, PG and PG-13; the adults for R, NC-17, unrated or no rating | Nick |
| 12 | A digital copy with no YouTube id | Not a way to play; a title owned only that way gets no ▶ | Claude's call |
| 13 | Two discs of one film | One way, "Start the disc"; the best format's shelf first, the others under it | Claude's call |
| 14 | Also on the details page | The play buttons sit at the top of the title's details | Claude's call |
| 15 | Who may press ▶ | Any signed-in editor, at home or away. Signed-out visitors and the read key never see or use it | Claude's call |
| 16 | A disc's message | Carries the shelf line and stays until dismissed | Claude's call |
| 17 | When the watch is logged | Only after Home Assistant accepts the request | Claude's call |
| 18 | Two presses in a day | One watch per film per day: a press adds nothing when the film already has a watch dated today | Claude's call |
| 19 | Movie Night's audience | An audience other than "everyone" chosen in the picker is logged instead of rule 11 | Claude's call |
| 20 | Storage | `copies.youtube_id`, digital copies only, exactly 11 characters of `A-Z a-z 0-9 _ -`, unique across copies | Claude's call |
| 21 | Service | The id can sit on a digital copy of any service (12 of the matches are Fandango at Home copies) | Claude's call |
| 22 | Who sees the id | Editors only; the public `/api` never carries it | Claude's call |
| 23 | How the list is loaded | One editor-only request (a documented `curl`); the answer is the list of what didn't load | Claude's call |
| 24 | The CSV export | Gains a `youtube_link` column | Claude's call |
| 25 | The play setting | `HA_PLAY_WEBHOOK_URL`, the name It's a Wonderful App uses; 5 second wait | Claude's call |
| 26 | The id in MCP answers | Not carried. Told to Nick as "the MCP copy tools carry it" in the playback; corrected, see §7 | Claude's call, **changed after playback**, told to Nick |
| 27 | An assistant starting a film | Yes: an MCP tool, as a fifth PR | Nick (asked after reading the design; it had been a non-goal by Claude's call) |
| 28 | Which MCP write level has the tool | `all` only, the most restricted; never `log` or `none` | Claude's call, said to Nick when he chose 27 |
| 29 | The tool with two ways to play and no mode given | It plays nothing and answers with the two choices | Claude's call |

## 3. What exists (looked up 2026-11-26)

- **Home Assistant, live and shared.** `script.wonderful_play_on_tv` takes `mode` (`stream` or `disc`), `source`
  (the TV input name) and an optional `content_id`. For a stream it starts the Sofabaton "Watch TV" activity, waits
  for the TV (`media_player.77g1`, the webOS integration), then, when `source` is `YouTube` and `content_id`
  matches `^[A-Za-z0-9_-]{1,64}$`, launches `youtube.leanback.v4` with `contentId: "v=<id>"`; otherwise it selects
  the source. For a disc it starts "Watch a Bluray". The script checks `content_id` itself. Tried by hand on the TV
  that day: the YouTube launch went straight to a film owned through Movies Anywhere and started it.
- **The pattern to reuse** is It's a Wonderful App's: `ha.py` `post_play` (a synchronous POST, 5 s timeout, one
  host-only warning on failure, `PlayFailed`), `play.py` (route-free rule and `start`), `static/js/play.js`
  (one request at a time). Its specs are `2026-11-26-play-on-tv-design.md` and `2026-11-26-netflix-title-design.md`
  in that repo, and its `docs/home-assistant.md` has the script and automation YAML. Matinee takes the outbound
  half only: no `/hooks/tv`, no `GET /api/tv`, no polling.
- **Matinee today.** `copies` has `kind`, `format`, `service`, `location_id` and the v10 details; `SCHEMA_VERSION`
  is `"12"`. `browse.copy_out(r, private)` serialises a copy and drops `PRIVATE_COPY` for the public.
  `api_owned` returns `copy_out(r, True)` per copy, so a new private field reaches It's a Wonderful App with no
  new route. The only Home Assistant code is the fire-and-forget "watch logged" post (`fire_watch_event`,
  `post_ha_event`, `_ha_host` in `app.py`; `HA_WEBHOOK_URL`). People have a `kind` of `adult` or `kid`.
  `add_watch` logs a watch, sets attendance, clears Watch Soon for a dated watch and fires the movie-log event.

## 4. The YouTube id on a copy

**Schema v13.** `copies.youtube_id TEXT`, NULL when there is none. Added by `migrate()` from every earlier version
(it must survive the older copies rebuild in `migrate()`), with a partial unique index on non-NULL values created
in `migrate()`. `migrate()` twice changes nothing.

**The rule for an id** (one function, used when storing and again before sending): exactly 11 characters of
letters, digits, `_` or `-`.

**Writing.** `POST /admin/api/copies` and `PUT /admin/api/copies/<id>` take `youtube_link`: a YouTube address
(`https://www.youtube.com/watch?v=<id>` with or without other query parameters, `https://youtu.be/<id>`, with or
without `www.` or `m.`) or the bare id. An empty string or null clears it; leaving the key out of a `PUT` keeps
what is there.

- Anything else: 400, "That isn't a YouTube link. Paste the address of the film's YouTube page."
- On a physical copy, or when an edit turns a digital copy with an id into a physical one: 400 with a sentence.
- The id is already on another copy: 409, naming the film that has it ("That YouTube link is already on
  *Superbad* (2007).").

**Reading.** A copy in an editor's answer carries `youtube_id` (string or null); `youtube_id` joins `PRIVATE_COPY`,
so the public `/api` never has it. **Privacy: editor-only.** The read key reads it on GETs like the other
editor-only copy fields, gets `play: null`, and is refused by the play and load routes (Claude's call). `tests/test_privacy.py` must still pass, and a test
asserts the public title detail and titles list don't carry the key. `GET /admin/api/tmdb/<tmdb_id>/owned` carries
it through `copy_out` with nothing else changed.

**The form.** The digital copy form (add and edit) gets a **YouTube link** field, shown for digital copies only.
It shows the stored link as a full address, takes a paste, and reports the 400 and 409 sentences in place.

**Export.** `export.csv` (shape `copy`) gains a last column `youtube_link`: the full
`https://www.youtube.com/watch?v=<id>` address, empty when there is none. An address, not the bare id, because the
export guards cells that start with `-` and some ids do. The title shape is unchanged.

## 5. Loading a list of owned YouTube films

`POST /admin/api/youtube-links`, `@editor_required`, never the read key. The body is the list as exported: a JSON
array of `{"title", "year", "video_id", "play_link"}`, or the same four columns as `text/csv`. `?dry_run=1` changes
nothing and answers as if it had. `?format=csv` answers the not-loaded list as CSV instead of JSON.

**A row links by itself only when all of these hold:**

1. `video_id` passes the id rule, and the `v=` of `play_link` is the same id.
2. No other row in the list has the same cleaned title and year.
3. Exactly one title in Matinee has the same cleaned title and the same year.
4. That title has exactly one digital copy, and the copy has no id yet.
5. The id is not on any other copy.

A row whose id is already on the copy it would match counts as already linked. Nothing ever overwrites an id that
is on a copy.

**Cleaning a title** (both sides, for comparison only): case and punctuation ignored, `&` read as "and", a leading
"The", "A" or "An" dropped, a trailing ", The" moved to the front, and these retail extras removed: `Marvel
Studios'`, `Illumination Presents:`, `Dr. Seuss'`, `(Unrated)`, a trailing `(YYYY)`, ` The Movie`, `: Based on a
True Story`, ` - Director's Cut`, `: Director's Definitive Cut`, `: Final Cut`. So "Marvel Studios' Thor: Ragnarok"
(2017) meets "Thor: Ragnarok" (2017) and "40 Year-Old Virgin, The" (2005) meets "The 40 Year-Old Virgin" (2005).
The year is never loosened: "Inception" listed as 2020 does not link to *Inception* (2010).

**The answer:** `{"linked": n, "already_linked": n, "not_loaded": [...]}`. Each not-loaded row has the four
fields it came with, a `reason` sentence, and the Matinee titles it came near (`title`, `year`, and what we hold:
"Digital 4K · Movies Anywhere" or "Blu-ray only"), so the person can find the copy and paste the link. Reasons
to cover: the id and the link disagree ("Easy A" in the real list); listed twice with different ids; the year
differs; the title is in Matinee on disc only; more than one title or more than one digital copy matches; the copy
already has a different id; the id is on another film; nothing like it in Matinee; the id is not well formed.

Loading the same list again answers only what is still unlinked. Nothing about the list is stored.

**The personal list never enters the repo.** Tests use a short fixture of well-known films with made-up ids that
exercises every reason above. `docs/` shows the `curl` command with `movies.example.com` and a file path of the
person's own.

For scale, not a contract: against Nick's collection on 2026-11-26 (253 digital copies, none doubled) about 185 of
the 223 rows link and about 38 come back, three of them films listed twice.

## 6. Play

### 6.1 The setting

`HA_PLAY_WEBHOOK_URL`: the Home Assistant webhook that starts something on the Family Room TV, e.g.
`http://192.168.1.100:8123/api/webhook/<id>`. A secret: the id is the only credential. Empty means Play is off.
It follows CLAUDE.md rule 7 (`.env.example`, the installer's `add_setting` list, the init script's `export` line)
and is handled like `HA_WEBHOOK_URL`: never logged, never returned, never in a payload. `tests/conftest.py` blanks
it so a developer's environment can't turn the feature on in tests.

### 6.2 What can play (one rule, used by the grid, the details page, Movie Night and the route)

For a title, from the database only:

- **YouTube:** the title has a digital copy with a `youtube_id` that passes the id rule (the first such copy by
  `created_at, id`).
- **Disc:** the title has any physical copy. Its lines are every physical copy's format and shelf path, best
  format first in `FORMATS["physical"]` order ("4K UHD · Family Room › Media Cabinet › Shelf 2").

Editors' title answers (list and detail) gain a `play` value saying which of the two apply and carrying the disc
lines; it is null when the setting is empty, and absent from the public `/api`. Its exact shape is the builder's.

### 6.3 Pressing ▶

`POST /admin/api/titles/<id>/play`, `@editor_required`, body `{"mode": "youtube" | "disc", "attendees"?}`.

1. 503 with a sentence when the setting is empty; 404 for an unknown title; 400 for a bad body; 409 for a mode the
   title can't do ("We don't own *Heat* on disc.").
2. Post to Home Assistant and wait up to 5 seconds:
   - YouTube: `{"event": "matinee_play", "mode": "stream", "source": "YouTube", "title": "Brooklyn",
     "content_id": "<the id>"}`. The id is checked against the id rule again here; one that fails is a 409, never
     sent.
   - Disc: `{"event": "matinee_play", "mode": "disc", "source": "AV Receiver", "title": "Heat"}`.
3. If Home Assistant can't be reached, is slow or answers outside 200-299: 502, "Couldn't reach the TV. *Brooklyn*
   wasn't started." Nothing is written. One warning names the host and the kind of failure, never the URL or
   `str(exc)`.
4. Otherwise log the watch (§6.4) and answer with the mode, the message, the disc lines for a disc, whether a
   watch was added, and whatever the page needs to refresh the title.

A 200 from Home Assistant means it took the request, not that the TV arrived (Home Assistant also answers 200 to a
webhook id it doesn't know). That gap is accepted (decision 5); the setup doc says to check the first play in the
automation's Traces.

### 6.4 The watch

After Home Assistant accepts, in one transaction that takes the write lock first (two presses at once must not log
two watches):

- If the title already has a watch dated today (the date `pacific_today()` gives, logged by Play or by hand), add
  nothing.
- Otherwise add a watch dated today with no bucket and no notes, exactly as **+ Watched Today** does: it takes the
  title off Watch Soon and posts the existing movie-log event to `HA_WEBHOOK_URL`.
- **Who watched:** with the title's rating (`certification_override`, else `certification`) G, PG or PG-13, every
  active person; with R, NC-17, NR or none, the active adults. When the request carries `attendees` (Movie Night,
  decision 19), those people instead: a list of person ids, resolved by `resolve_people` as the watch routes do.
  The page turns the picker's "adults", "kids" or custom choice into ids before sending (Claude's call). With no people set up,
  no attendance rows, as today.

A film abandoned after ten minutes keeps its watch until someone deletes it, like any watch.

### 6.5 The pages (editors only, and only when `play` is not null)

- **The grid.** A ▶ in a corner of each playable poster, with a 44 px touch target and an accessible name ("Play
  Brooklyn on the Family Room TV"). One way to play: the tap sends it. Both ways: the tap opens a sheet with
  **Play on YouTube**, **Start the disc** and the disc lines. A tap anywhere else on the poster opens the details
  page as today; the ▶ must not also open it.
- **The details page.** The same buttons at the top: one or both, with the disc lines.
- **Movie Night.** The same buttons under the pick. It sends the picker's audience when that is anything but
  "everyone".
- **Messages.** While the request is out the control is disabled, so a double tap sends once. On success:
  "Starting Brooklyn on Family Room TV. Added to watch history." (without the second sentence when today already
  had a watch). For a disc the message carries the disc lines and stays until dismissed. On failure: the server's
  sentence, or "Couldn't send that. Check the connection and try again." when Matinee itself can't be reached.
- Phone first: nothing scrolls sideways at 412 px. `tests/test_play_ui.py` (Playwright) covers the ▶ on a
  one-way title, the sheet on a two-way title, the poster still opening details, the failure message, nothing
  showing with the setting empty or signed out, and the grid on a second visit. It joins the list of UI tests in
  `CLAUDE.md`'s Definition of done.

## 7. MCP

**One new tool, `play_on_tv`** (decision 27): it takes a film (the way `log_watch` names one) and an optional
`mode` (`youtube` or `disc`), and calls `POST /admin/api/titles/<id>/play`, so every rule in §6 holds: the id is
checked, Home Assistant is waited for, the watch is logged once per day with attendance by rating. It sends no
`attendees`.

- With one way to play and no `mode`, it plays that way. With both ways and no `mode`, it plays nothing and
  answers with the two choices (decision 29). A film that can't play, Play being off (503) and Home Assistant not
  taking it (502) come back as the route's sentence.
- Its answer is the route's message, with the disc lines for a disc.
- It only goes through the app's route: the webhook setting stays in the app's environment and is never given to
  the `matinee-mcp` service. A connection that can't write to the app can't play.
- **Level:** it joins `WRITE_TOOLS` at `all` only (decision 28). The remote service at `log` or `none` doesn't
  register it, and the server instructions name it only where it is registered. stdio registers it like every tool.
- **Hints:** not read-only, not destructive, not idempotent (a call starts the TV again), open world (it reaches
  the TV through Home Assistant).
- Rule 6: the tool, tests in `tests/test_mcp_server.py` (one way, two ways with and without `mode`, 409, 502, 503,
  not registered below `all`), and `docs/mcp-setup.md`.

**The id is not in MCP's answers.** The copy rows MCP returns are public fields only by an earlier decision ("the
editor-only `code_status`, `purchased_on` and `price_cents` are never in either shape"), and the id is
editor-only. The playback told Nick the MCP copy tools would carry it; that was wrong about the existing rule and
is reversed here (decision 26). A test asserts `list_copies` and `get_movie` do not carry `youtube_id`.

## 8. The Home Assistant side

Written up in `docs/home-assistant.md` with placeholder names (`media_player.family_room_tv`, `192.168.1.100`),
as a new section beside the movie-log one. The script is the shared one and is not changed. The doc describes what
the automation needs from it (the three fields and what each mode does) and shows the script's YAML inline, so
Matinee's public docs don't depend on a link into another repo (Claude's call).

**One new automation, "Matinee plays on the Family Room TV":** a webhook trigger (`POST` only, `local_only: true`,
its own long random id), a condition that `trigger.json.event == 'matinee_play'`, `mode` is `stream` or `disc`,
`source` is non-empty, and `content_id` is absent or a string matching `^[A-Za-z0-9_-]{1,64}$`, and one action:
`script.turn_on` on `script.wonderful_play_on_tv` with `mode`, `source` and `content_id` (empty when absent). It is
the existing automation with a different id and event name.

**Claude creates nothing in Home Assistant until Nick says so**, after the code is merged. Nick puts the URL in
Matinee's settings on the host (`vim /etc/conf.d/matinee`, then `rc-service matinee restart`).

**The TV is not turned on to test without asking Nick first.** The hardware pass after deploy, with him: a YouTube
play from a cold TV and from a TV already on; a disc play; a play while the other activity is running.

## 9. What the comparison found (2026-11-26)

Nick's YouTube list against a digital-only export of Matinee, by cleaned title and year: 182 rows one-to-one; 3
films listed twice (6 rows); 11 with the year off by one or a listing year; 1 edition ("Blade Runner: Final Cut");
5 where Matinee seemed to hold a different film of the same name (Nick fixed four the same day; *Safe* is open); 4
under another name ("Harry Potter and the Sorcerer's Stone" against "…Philosopher's Stone"); 14 with no digital
copy. All 223 ids are 11 characters of the allowed set and none repeats. This is why the load never loosens the
year and never picks between candidates.

## 10. Failure cases reviewers check first

1. Home Assistant down, slow (over 5 s), answering 404 or 500: 502 with the sentence, no watch, no URL in the log.
2. `HA_PLAY_WEBHOOK_URL` empty: `play` is null everywhere, no ▶ renders, the route is a 503, and every existing
   test passes untouched.
3. A stored id that is not well formed (written straight into the table): the title is not YouTube-playable and
   nothing is sent.
4. Signed out with `PUBLIC_VIEW` on: no `youtube_id`, no `play`, no ▶; the play and load routes refuse.
5. Two presses: one request while the first is out; a second press the same day starts the TV and adds no watch.
6. An R film logs the active adults; a PG-13 film logs everyone; a title with a rating override uses the override.
7. The list load: every reason in §5 leaves the database untouched for that row; a dry run changes nothing; a
   second load links nothing new and never overwrites.
8. A v1 database and a v12 database both migrate to v13; `migrate()` twice changes nothing.
9. The ▶ doesn't open the details page, and still works on the second visit to the grid.
10. `play_on_tv` is absent from the remote service at `none` and `log`, and with both ways and no `mode` it sends
    nothing to Home Assistant.

## 11. Left to the builder

- The plan, as five PRs, each shippable alone: (1) the id on the copy: schema, writes, reads, the form field, the
  owned answer, the export column; (2) the list load and its doc; (3) Play behind the setting: the setting, the
  rule, the route, the watch, the Home Assistant doc, no UI; (4) the buttons on the three pages with the
  Playwright checks; (5) the MCP `play_on_tv` tool.
- Module layout. The play rule and the outbound call are route-free and belong in a sibling module (CLAUDE.md:
  it goes in `_SPLIT_MODULES` and the installer's `cp` line); routes and `app.logger` calls stay in `app.py`.
  No new runtime dependency: `urllib` as `post_ha_event` uses.
- The shape of `play` in title answers and of the play route's answer, error sentences not quoted here, CSS, where
  the sheet comes from (the existing modal), the exact disc message wording.
- Whether "Family Room TV", "YouTube" and "AV Receiver" are constants or settings; constants are fine.
- `README.md` and `.env.example` entries.
