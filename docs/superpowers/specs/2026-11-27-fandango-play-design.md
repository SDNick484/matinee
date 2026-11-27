# Play a Fandango at Home title on the Family Room TV: design

Date: 2026-11-27. Builds on `2026-11-26-play-on-tv-design.md` (Play, the YouTube id on a digital copy); everything
there stands unless this says otherwise.

## 1. Why

Play starts a film on YouTube (films owned through Movies Anywhere) or from a disc. Some films are owned only on
Fandango at Home and are not in Movies Anywhere, so they have no YouTube listing and no ▶. *The Addams Family*
(1991) is one: its page is `https://athome.fandango.com/content/browse/details/The-Addams-Family/15429`.

**Amended 2026-11-27 (Nick):** Fandango at Home is the primary way to play and YouTube the fallback, and most films
will have both links. Decisions 3, 4, 5 and 9 changed; the sections below say what is built.

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
| 3 | How it is entered | **Changed by Nick on 2026-11-27.** A digital copy's form has two fields, **Fandango at Home link** then **YouTube link**; the API takes `fandango_link` beside `youtube_link`, and it behaves exactly as `youtube_link` does. Matinee keeps only the id. (Was: one **Play link** field taking either address.) | Nick |
| 4 | Links per copy | **Changed by Nick on 2026-11-27.** One copy can hold both ids: `fandango_id` and `youtube_id` are independent. (Was: a YouTube id or a Fandango id, never both.) | Nick |
| 5 | What ▶ does | **Changed by Nick on 2026-11-27.** Fandango at Home is primary. A one-tap place (the grid ▶, the button under the Movie Night pick, MCP `play_on_tv` with no mode) starts Fandango at once when the title has a Fandango link, with no chooser; without one it behaves as before. The details page shows a button per way: **Play on Fandango at Home**, **Play on YouTube**, **Start the disc**. (Was: the chooser lists every way.) | Nick |
| 6 | What Home Assistant gets | The same `matinee_play` event, `mode: "stream"`, `source: "Fandango at Home"`, `content_id: "15429"` | Claude's call |
| 7 | The shared script | `script.wonderful_play_on_tv` gains one branch beside Netflix and YouTube. This changes decision 6 of the 2026-11-26 design ("the shared script is not changed"): the script is extended, and what it does for Netflix, YouTube and discs is unchanged | Claude's call, told to Nick |
| 8 | Who sees the id | Editors only, exactly like the YouTube id | Claude's call |
| 9 | Loading a list | **Changed by Nick on 2026-11-27.** `POST /admin/api/fandango-links`, the YouTube list load generalised to the Fandango id (§3a). (Was: none; links pasted per film.) | Nick |
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

**Writing it.** `POST /admin/api/copies` and `PUT /admin/api/copies/<id>` take `fandango_link`, which behaves
exactly as `youtube_link` does: a Fandango at Home address or a bare number sets `fandango_id`; `""` or null clears
it; leaving the key out of a `PUT` keeps what is there; anything else is a 400 ("That isn't a Fandango at Home link.
Paste the address of the film's Fandango at Home page."). Digital copies only, with the same sentences as the
YouTube id for a physical copy and for turning a linked digital copy into a physical one; an id already on another
copy is a 409 naming the film ("That Fandango at Home link is already on *The Addams Family* (1991)."). The two
fields are independent: setting or clearing one never touches the other. `youtube_link` is unchanged.

**Reading it.** Editors' copy rows carry `fandango_id` beside `youtube_id`; the public `/api` and the read key's
restricted fields never do (`browse.PRIVATE_COPY`). `tests/test_privacy.py` must pass. The copy export gains a last
column `fandango_link` (the full `https://athome.fandango.com/content/browse/details/-/15429` form is not
reconstructible, the slug is not stored, so the column carries
`https://athome.fandango.com/content/browse/details/title/<id>`; the app ignores the slug). MCP copy rows never
carry it, like the YouTube id.

**The YouTube list load** is unchanged, byte for byte: a copy's Fandango id never stops a YouTube id being loaded
onto it.

### 3a. Loading a list of Fandango at Home links

The YouTube list load (`POST /admin/api/youtube-links`, 2026-11-26 design §5) is generalised over the kind of id,
not copied. `POST /admin/api/fandango-links` is editor-only (never the read key) and takes the same options
(`?dry_run=1`, `?format=csv`), runs in the same all-or-nothing transaction and answers in the same shape. Its rows
are `title, year, fandango_id, play_link`, as a JSON array or CSV; `play_link` must be a Fandango at Home (or old
vudu.com) address whose number is the `fandango_id`. The five matching rules are the YouTube ones applied to
`fandango_id`: the year is never loosened, an id already on a copy is never replaced, a row whose copy already has
this id counts as already linked, and the title must have exactly one digital copy. The two loads are independent:
a copy's YouTube id never stops a Fandango id being loaded, and the reverse. The not-loaded CSV's link column is
`fandango_link`. `docs/fandango-links.md` shows the dry run and the real load, and says that a list gathered by a
script or an assistant must be checked before it is loaded.

## 4. Play

`play.py`: `MODES` gains `"fandango"`; `FANDANGO_SOURCE = "Fandango at Home"`. A title's `play` value gains a third
key, `fandango`: `{"copy_id", "label"}` or null, the first digital copy with a Fandango id. A title is playable when
any of the three is set. Nothing else is added to the answers: the page and the MCP tool apply the one-tap rule
(decision 5).

The body for a Fandango play:

```json
{"event": "matinee_play", "mode": "stream", "source": "Fandango at Home", "title": "The Addams Family", "content_id": "15429"}
```

`POST /admin/api/titles/<id>/play` takes `{"mode": "fandango"}`; without a Fandango copy it answers 409
"The Addams Family has no Fandango at Home link." Everything else about the route (the 5 second wait, the errors,
the watch dated today, one a day, who watched, the answer's shape) is as for `youtube`. The message is
"Starting The Addams Family on Family Room TV."

## 5. The page

- **Copy form** (`static/js/forms.js`): a digital copy has two fields, in this order: **Fandango at Home link**
  then **YouTube link**, each with its own error line. Each shows its stored id back as a full address (Fandango as
  `https://athome.fandango.com/content/browse/details/title/15429`, YouTube as today) and sends `fandango_link` and
  `youtube_link`.
- **Play buttons** (`static/js/play.js`): the grid's ▶ and the button under the Movie Night pick start Fandango at
  once when the title has a Fandango link, with no chooser. Without one they behave as before: one way starts at
  once, YouTube and a disc open the two-way chooser (the grid) or show both buttons (Movie Night). The details page
  shows a button per way, in this order: Play on Fandango at Home, Play on YouTube, Start the disc.
- Nothing shows for a visitor, for the read key, or with `HA_PLAY_WEBHOOK_URL` empty.

## 6. MCP

`play_on_tv` accepts `mode: "fandango"`. With no mode it plays Fandango when the title has a Fandango link; without
one it behaves as before (one way plays; YouTube and a disc answer with the two choices and play nothing). An
explicit mode plays that mode. Still only at `MATINEE_MCP_WRITES=all`. Answers never carry either id.
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
- Copy API: `fandango_link` with an address, a bare number, blank or null (clears it), absent (keeps it), junk
  (400); setting either link leaves the other alone; a Fandango id already on another copy is a 409 naming the
  film; a physical copy is refused; the public `/api` never sees `fandango_id`.
- Export: the `fandango_link` column.
- Fandango list load: tests mirroring the YouTube ones (`tests/test_fandango_load.py`,
  `tests/test_fandango_load_api.py`) on a fixture of well-known films with made-up numbers (The Addams Family
  1991 = 15429 is the one real pair); each load ignores the other kind's id.
- Play: `ways` with each combination of the three; the Fandango body exactly; `mode: "fandango"` logs the watch
  once; 409 without a Fandango copy; the id is re-checked before sending.
- MCP: `play_on_tv` with `fandango`; no mode plays Fandango when present; YouTube and a disc still answer with the
  two choices; no id in any answer.
- UI (`tests/test_play_ui.py`, `tests/test_youtube_link_ui.py`): pasting a Fandango address in the copy form saves
  and shows it back beside a YouTube link; a title with Fandango, YouTube and a disc: the grid ▶ sends the Fandango
  body with no chooser, the details page shows three buttons in order and the YouTube one sends the YouTube body; a
  title with YouTube and a disc still opens the two-way chooser; no sideways scroll at 412 and 360 px.

## 9. Out of scope

Other services (Apple TV, Prime Video); renaming `youtube_link` or the YouTube
column; changing the automation.
