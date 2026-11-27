# Play a Fandango at Home title on the Family Room TV: design

Date: 2026-11-27. Builds on `2026-11-26-play-on-tv-design.md` (Play, the YouTube id on a digital copy); everything
there stands unless this says otherwise.

## 1. Why

Play starts a film on YouTube (films owned through Movies Anywhere) or from a disc. Some films are owned only on
Fandango at Home and are not in Movies Anywhere, so they have no YouTube listing and no ▶. *The Addams Family*
(1991) is one: its page is `https://athome.fandango.com/content/browse/details/The-Addams-Family/15429`.

Tested on the TV on 2026-11-27: launching the Fandango at Home app (webOS app id `vudu`) with `contentId: "15429"`
opened the app and **began playing The Addams Family at once**. So a Fandango copy can play exactly as a YouTube
copy does; no "just open the app" fallback is needed.

## 2. Decisions (2026-11-27)

"Nick" means he said it. "Claude's call" means Claude proposed it in the conversation and Nick went on from it, or
it was settled while writing this; check these when reviewing.

| # | Decision | Choice | Whose |
|---|---|---|---|
| 1 | Fandango-only films | Get a ▶ like any other playable film | Nick |
| 2 | What is stored | The number at the end of the film's Fandango at Home address (`15429`), on the digital copy, as `copies.fandango_id` | Claude's call |
| 3 | How it is entered | The copy form's one link field (now labelled **Play link**) takes a YouTube address or a Fandango at Home address; Matinee keeps only the id | Claude's call |
| 4 | One link per copy | A copy holds a YouTube id or a Fandango id, never both | Claude's call |
| 5 | A film with both kinds of copy | The chooser lists every way: **Play on YouTube**, **Play on Fandango at Home**, **Start the disc** | Claude's call |
| 6 | What Home Assistant gets | The same `matinee_play` event, `mode: "stream"`, `source: "Fandango at Home"`, `content_id: "15429"` | Claude's call |
| 7 | The shared script | `script.wonderful_play_on_tv` gains one branch beside Netflix and YouTube. This changes decision 6 of the 2026-11-26 design ("the shared script is not changed"): the script is extended, and what it does for Netflix, YouTube and discs is unchanged | Claude's call, told to Nick |
| 8 | Who sees the id | Editors only, exactly like the YouTube id | Claude's call |
| 9 | Loading a list | None. Fandango links are pasted per film; there are few of them | Claude's call |
| 10 | The watch, who watched, one a day | Exactly as for a YouTube play | Claude's call |

## 3. The id and the link

`fandango_id` is 1 to 12 digits, nothing else (`^[0-9]{1,12}$`), checked when stored and again just before it is
sent, as the YouTube id is. One place holds the rule: `fandango.py` (new, route-free, standard library only, in
`_SPLIT_MODULES` and `installer/build.sh`), with `fandango_id_ok(value)` and `parse_fandango_link(value)`.

`parse_fandango_link` accepts, with or without the scheme, `www.` and a trailing slash or query:

- `https://athome.fandango.com/content/browse/details/The-Addams-Family/15429`
- `https://www.vudu.com/content/movies/details/The-Addams-Family/15429` (the old address of the same page)
- the bare number `15429`

and raises `ValueError` for anything else: another host, a path without a number at the end, a number longer than
12 digits. None or blank is None.

**Storage.** Schema v14 through `migrate()`: `copies.fandango_id TEXT`, last column, with a unique index where it
is not null made in `migrate()` (never in `SCHEMA`), as v13 did for `youtube_id`. Idempotent from v1 and from v13.

**Writing it.** `POST /admin/api/copies` and `PUT /admin/api/copies/<id>` take a new `play_link`: a YouTube
address or id sets `youtube_id` and clears `fandango_id`; a Fandango address sets `fandango_id` and clears
`youtube_id`; `""` or null clears both; anything else is a 400 with one sentence naming both kinds of address.
A bare all-digit value is read as a Fandango id (an 11-character YouTube id made only of digits must be pasted as
its address). `youtube_link` keeps working exactly as it does now (the list load and existing callers use it), and
a request that carries both `play_link` and `youtube_link` is a 400. Digital copies only; the same rules as the
YouTube id for a physical copy, for turning a linked digital copy into a physical one, and for an id that is
already on another copy (409, naming the film).

**Reading it.** Editors' copy rows carry `fandango_id` beside `youtube_id`; the public `/api` and the read key's
restricted fields never do (`browse.PRIVATE_COPY`). `tests/test_privacy.py` must pass. The copy export gains a last
column `fandango_link` (the full `https://athome.fandango.com/content/browse/details/-/15429` form is not
reconstructible, the slug is not stored, so the column carries
`https://athome.fandango.com/content/browse/details/title/<id>`; the app ignores the slug). MCP copy rows never
carry it, like the YouTube id.

**The YouTube list load** (`youtube.plan_links`) refuses a row whose one digital copy already has a Fandango id,
with the reason "The digital copy already has a Fandango at Home link." It never replaces one.

## 4. Play

`play.py`: `MODES` gains `"fandango"`; `FANDANGO_SOURCE = "Fandango at Home"`. A title's `play` value gains a third
key, `fandango`: `{"copy_id", "label"}` or null, the first digital copy with a Fandango id. A title is playable when
any of the three is set.

The body for a Fandango play:

```json
{"event": "matinee_play", "mode": "stream", "source": "Fandango at Home", "title": "The Addams Family", "content_id": "15429"}
```

`POST /admin/api/titles/<id>/play` takes `{"mode": "fandango"}`; without a Fandango copy it answers 409
"The Addams Family has no Fandango at Home link." Everything else about the route (the 5 second wait, the errors,
the watch dated today, one a day, who watched, the answer's shape) is as for `youtube`. The message is
"Starting The Addams Family on Family Room TV."

## 5. The page

- **Copy form** (`static/js/forms.js`): the field is labelled **Play link**, placeholder "Paste the film's YouTube
  or Fandango at Home address". It shows the stored link back as a full address (YouTube as today; Fandango as
  `https://athome.fandango.com/content/browse/details/title/15429`) and sends `play_link`. Errors show beside the
  field as they do now.
- **Play buttons** (`static/js/play.js`, grid, details, Movie Night): a title with exactly one way starts at once.
  With two or three ways the chooser opens and lists each one that exists, in this order: Play on YouTube, Play on
  Fandango at Home, Start the disc. The details page shows a button per way.
- Nothing shows for a visitor, for the read key, or with `HA_PLAY_WEBHOOK_URL` empty.

## 6. MCP

`play_on_tv` accepts `mode: "fandango"`. With more than one way and no mode it plays nothing and answers with the
choices, now up to three. Still only at `MATINEE_MCP_WRITES=all`. Answers never carry either id.
`docs/mcp-setup.md` says so.

## 7. Home Assistant

`docs/home-assistant.md` §7: the script gains this branch in the inner `choose`, after YouTube (placeholder entity
name as in the rest of the doc), and the payload example for Fandango is added:

```yaml
- conditions:
    - condition: template
      value_template: "{{ content_id is not none and (content_id | default('')) | string is match('^[0-9]{1,12}$') and source == 'Fandango at Home' }}"
  sequence:
    - action: webostv.command
      target:
        entity_id: media_player.family_room_tv
      data:
        command: system.launcher/launch
        payload:
          id: vudu
          contentId: "{{ content_id }}"
```

The automation needs no change: its check already admits a `content_id` of digits and any non-empty `source`.
The Netflix branch also matches digits but only for `source == 'Netflix'`, so the two cannot be confused. Without
this branch the script falls through to selecting the "Fandango at Home" input, which opens the app without the
film: harmless, and the reason the script change can go in before or after Matinee is deployed.

The change to the owner's Home Assistant is made by hand (or by Claude with the owner's go-ahead) after the PR is
green; nothing in the repo or its tests contacts Home Assistant or a TV.

## 8. Tests

No live network; Home Assistant is the existing local stub.

- `tests/test_fandango.py`: the id rule; every accepted and refused form of the link (the two hosts, bare number,
  no scheme, trailing slash, query string, 13 digits, another host, a YouTube address).
- Schema: new database has `fandango_id` last and is v14; migrates from v1 and v13; migrating twice changes nothing.
- Copy API: `play_link` with a YouTube address, a Fandango address, a bare number, blank (clears both), junk (400);
  switching a copy from YouTube to Fandango clears the YouTube id; both `play_link` and `youtube_link` is a 400;
  a Fandango id already on another copy is a 409 naming the film; a physical copy is refused; the public `/api` and
  the read key never see `fandango_id`.
- Export: the `fandango_link` column.
- YouTube list load: a copy with a Fandango id is refused with the new reason.
- Play: `ways` with each combination of the three; the Fandango body exactly; `mode: "fandango"` logs the watch
  once; 409 without a Fandango copy; the id is re-checked before sending.
- MCP: `play_on_tv` with `fandango`; three choices; no id in any answer.
- UI (`tests/test_play_ui.py`, `tests/test_youtube_link_ui.py`): pasting a Fandango address in the copy form saves
  and shows it back; a Fandango-only title's ▶ starts at once and the stub receives the Fandango body; a title with
  YouTube, Fandango and a disc shows three choices; no sideways scroll at 412 and 360 px.

## 9. Out of scope

Other services (Apple TV, Prime Video); a bulk load of Fandango links; renaming `youtube_link` or the YouTube
column; changing the automation.
