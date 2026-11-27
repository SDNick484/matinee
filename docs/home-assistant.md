# Home Assistant: movie night

When someone taps **+ Watched Today**, Matinee can tell Home Assistant, so an automation reacts: log the film, dim
the lights, announce it, start a scene. Matinee only reports what was logged; Home Assistant decides what to do. It is off
until you set one URL.

## 1. Create the webhook in Home Assistant

Create an automation with a **Webhook** trigger (Settings → Automations & scenes → Create automation). Home
Assistant generates the webhook id. The URL Matinee needs is:

```
http://homeassistant.local:8123/api/webhook/<id>
```

**Treat that URL like a password.** The id is the only credential: anyone who can reach Home Assistant and knows it
can fire the automation. Matinee therefore keeps it only in `/etc/conf.d/matinee` (mode 600, root only), never
writes the full URL to its log (a failure names the host and the kind of failure only), never returns it from any
route, and never puts it in the payload.

> **Resolving `homeassistant.local`.** That name is mDNS, and Alpine's musl resolver does not do mDNS, so the Matinee
> host often cannot resolve it. If the test in section 3 fails with "could not resolve host", use Home Assistant's
> address or a DNS name instead, for example `http://192.168.1.100:8123/api/webhook/<id>`.

## 2. Turn it on in Matinee

On the Matinee host:

```sh
vim /etc/conf.d/matinee        # add or edit: HA_WEBHOOK_URL=http://homeassistant.local:8123/api/webhook/<id>
rc-service matinee restart
```

Leave `HA_WEBHOOK_URL` empty to turn the feature off. Use `https://` if Home Assistant is not on your own network.

## 3. Test it

From the Matinee host, post a sample body (this is exactly what Matinee sends for *Paddington 2*):

```sh
curl -sS -X POST 'http://homeassistant.local:8123/api/webhook/<id>' \
  -H 'Content-Type: application/json' \
  -d '{"event":"matinee_watch_logged","title":"Paddington 2","year":2017,"runtime":103,"certification":"PG","formats":["Blu-ray"],"location":"Family Room › Media Cabinet › Shelf 2","watched_date":"2026-11-11","title_id":42}'
```

Home Assistant answers 200 even for an id no automation listens on, so a typo in the id fails silently: open the
automation's **Traces** to see whether it ran. Then tap **+ Watched Today** on a title in Matinee and check the trace again.

## 3a. When Matinee sends it

Only when someone taps the **+ Watched Today** button on a title. That button sends `live: true` along with today's
date; the server fires only when that flag is present, the date is today (the server's local date) and no date bucket
was sent:

| What was logged | Sent? |
|---|---|
| **+ Watched Today** in the web UI | yes |
| **Log Date** with today's date | no |
| `log_watch` from the Claude app or any other MCP client, even with today's date | no |
| An older date, a bucket ("Past Year" … "10+ Years"), or no date | no |
| Editing a watch's people afterwards, rating, deleting | no |

So only a deliberate "we are watching this now" tap starts a movie night; back-filling history, logging a date by
hand and assistants never do. A film you did watch today but logged some other way sends nothing.

**"Today" is the Matinee host's today.** The phone sends its own local date (**+ Watched Today** sends today's date
where the phone is) and Matinee compares it with the date on the host's clock, in the host's time zone. If the host
runs in UTC and the phone is in Pacific time, a movie night after about 4 PM Pacific is already "tomorrow" on the
host, so the watch counts as yesterday's and nothing is sent. Check with `date` on the Matinee host. If the time zone
is wrong, set it (on Alpine: `apk add tzdata && setup-timezone -z America/Los_Angeles`) and
`rc-service matinee restart`.

## 4. What Home Assistant receives

`POST`, `Content-Type: application/json`, one request per logged watch. No retries: if Home Assistant is down, the
event is lost (Matinee logs one warning and the editor's watch is saved as usual).

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

| Field | Notes |
|---|---|
| `event` | always `matinee_watch_logged` |
| `title`, `year`, `runtime` | TMDB data; `year` and `runtime` (minutes) are `null` when unknown |
| `certification` | the US rating (G, PG, PG-13, R, NC-17, NR), the editor override if one is set; `null` when unrated |
| `formats` | the distinct formats of every copy, physical first: `["4K UHD", "Digital HD"]` |
| `location` | the shelf of the first copy that has one; `null` for a digital-only title |
| `watched_date` | today, `YYYY-MM-DD` |
| `title_id` | Matinee's id for the title |

**Never sent:** watch notes, who was there (people, attendance, person ids) and who logged it. Those are
editor-only and stay inside Matinee.

## 5. Example automation

Dim the family room and announce the film, but only for a family-friendly rating (Home Assistant 2024.10 or newer;
older versions write `trigger:` / `platform:` / `service:`). Replace the webhook id and the entity ids with yours.

```yaml
alias: Matinee movie night
description: Dim the family room and announce the film when Matinee logs a watch for today
mode: single
triggers:
  - trigger: webhook
    webhook_id: "<id>"            # the last part of HA_WEBHOOK_URL; treat it like a password
    allowed_methods:
      - POST
    local_only: true              # only requests from your own network; drop it if Matinee is elsewhere
conditions:
  - condition: template
    value_template: >-
      {{ trigger.json.certification in ['G', 'PG', 'PG-13'] }}
actions:
  - action: light.turn_on
    target:
      entity_id: light.family_room
    data:
      brightness_pct: 15
      transition: 3
  - action: tts.speak
    target:
      entity_id: tts.piper
    data:
      media_player_entity_id: media_player.family_room_speaker
      message: >-
        Now showing {{ trigger.json.title }}{% if trigger.json.year %}, {{ trigger.json.year }}{% endif %}.
        Enjoy the movie.
```

### A movie log on the dashboard

To keep a running list of what you watched, create a **Local To-do** list (Settings → Devices & services → Add
integration → Local To-do, name it "Movie Log" so it becomes `todo.movie_log`), add a **To-do list** card for it to
the dashboard, and use this action instead of the lights. `mode: queued` keeps two quick taps from racing.

```yaml
mode: queued
actions:
  - action: todo.add_item
    target:
      entity_id: todo.movie_log
    data:
      item: "{{ trigger.json.title }}{% if trigger.json.year %} ({{ trigger.json.year }}){% endif %}"
      due_date: "{{ trigger.json.watched_date }}"
      description: >-
        {{ [trigger.json.certification, (trigger.json.runtime ~ ' min') if trigger.json.runtime else none,
            (trigger.json.formats | join(', ')) if trigger.json.formats else none, trigger.json.location]
           | select('string') | select | join(' · ') }}
```

For *Paddington 2* that adds "Paddington 2 (2017)", due 2026-11-11, described "PG · 103 min · Blu-ray · Family Room › Media Cabinet › Shelf 2".

The condition drops an R-rated or unrated film (`null` is not in the list) so the speaker does not announce it;
remove the `conditions:` block to react to every film. Other fields work the same way: `trigger.json.formats`,
`trigger.json.runtime`, `trigger.json.location`.

## 6. Troubleshooting

- **Nothing happens in Home Assistant.** Check the automation's Traces. No trace means no request arrived: look in
  `/var/log/matinee.log` on the Matinee host for `Home Assistant webhook`.
- **`Home Assistant webhook (host …) failed: URLError`** or `gaierror`: the Matinee host cannot resolve or reach that
  host (see the mDNS note in section 1).
- **`… answered HTTP 4xx/5xx`**: Home Assistant refused it; `local_only: true` rejects a request that does not come
  from the local network.
- **`… failed: TimeoutError`**: Home Assistant did not answer within 3 seconds. The watch was still saved.
- **It works at lunch but not on movie night.** The Matinee host's time zone is probably UTC: see the note on "today" in section 3a.
- Logging a film for an earlier day, with a bucket, or with no date never fires (section 3a).

## 7. Play on the Family Room TV

The other direction, and separate from the movie log above: with `HA_PLAY_WEBHOOK_URL` set, an editor can ask Home
Assistant to start an owned film on the Family Room TV: on Fandango at Home for a digital copy with a Fandango at Home
link (the primary way), on YouTube for a digital copy with a YouTube link, or with the disc activity for a disc. Matinee posts to its own webhook (not the movie-log one), waits up to 5 seconds for the
answer, and only when Home Assistant takes the request (any 2xx) adds the film to watch history for today (one watch
per film per day; see below). With the setting empty, the default, nothing about Play shows and the play route
answers 503.

### What Home Assistant receives

`POST`, `Content-Type: application/json`, one request per press. A Fandango at Home copy (the number at the end of
the film's `athome.fandango.com/content/browse/details/<name>/<number>` page):

```json
{"event": "matinee_play", "mode": "stream", "source": "Fandango at Home", "title": "The Addams Family", "content_id": "15429"}
```

A YouTube copy:

```json
{"event": "matinee_play", "mode": "stream", "source": "YouTube", "title": "Brooklyn", "content_id": "dQw4w9WgXcQ"}
```

A disc:

```json
{"event": "matinee_play", "mode": "disc", "source": "AV Receiver", "title": "Heat"}
```

`content_id` is the copy's Fandango at Home id (1 to 12 digits) or YouTube video id (exactly 11 letters, digits, `_`
or `-`), checked again just before it is sent, so nothing else ever reaches Home Assistant. A disc payload has no `content_id` key. Nothing editor-only is sent:
no notes, no people, no copy ids, and never the webhook URL.

If Home Assistant can't be reached, takes longer than 5 seconds or answers outside 200-299, the editor gets "Couldn't
reach the TV. Brooklyn wasn't started.", nothing is written, and `/var/log/matinee.log` gets one line naming the
host and the kind of failure (`Play on TV: Home Assistant (host 192.168.1.100) failed: TimeoutError`, or `answered
HTTP 404`), never the URL.

**The watch.** A press that Home Assistant takes logs a watch dated today in America/Los_Angeles (the household's
day, whatever the host's clock says), with no notes, takes the film off Watch Soon and sends the movie-log event of
section 4 when `HA_WEBHOOK_URL` is set, exactly as **+ Watched Today** does. When the film already has a watch dated
today (from an earlier press or logged by hand), the TV still starts and nothing is added. Who watched: everyone for
G, PG and PG-13; the adults for R, NC-17, NR or no rating (the editor's rating override counts); or the people the
request names.

### Set it up

Two things in Home Assistant, a script that does the work and an automation that receives the webhook. If It's a
Wonderful App already plays on this TV, the script exists (`script.wonderful_play_on_tv`): add the automation, and add
the one Fandango at Home branch below to the script's inner `choose` (after the YouTube one); what the script does for
Netflix, YouTube and discs is unchanged. Without the branch a Fandango at Home press falls through to selecting the
"Fandango at Home" input, which opens the app without the film: harmless, so the branch can go in before or after
Matinee is updated. The entity names below are placeholders: `media_player.family_room_tv` (the TV, from the LG webOS TV
integration), `switch.watch_tv` and `switch.watch_a_bluray` (the remote's activities as switches).

1. **The script** (**Settings → Automations & Scenes → Scripts → Add script → Edit in YAML**). Save it as "Play on the
   Family Room TV" and make sure its entity id is `script.wonderful_play_on_tv` (**Settings → Devices & services →
   Entities**). What the automation needs from it: three fields, `mode` (`stream` or `disc`), `source` (the TV input
   name) and `content_id` (optional). For a stream it starts Watch TV if it is off, waits up to 60 seconds for the TV
   to be on, then, when `source` is `YouTube` and `content_id` is 1 to 64 letters, digits, `_` or `-`, launches the
   YouTube app on that video; when `source` is `Fandango at Home` and `content_id` is 1 to 12 digits, launches the
   Fandango at Home app (webOS id `vudu`) on that film, which starts playing it; otherwise it selects the input. For a disc it starts Watch a Bluray, which is trusted to
   put the TV on `AV Receiver`. The script checks `content_id` itself. `mode: restart` lets a second press replace
   the first.

   ```yaml
   alias: "Play on the Family Room TV"
   mode: restart
   fields:
     mode:
       description: "stream to open an app, disc to start the Blu-ray"
       required: true
       selector:
         select:
           options:
             - stream
             - disc
     source:
       description: "The TV input to select, e.g. YouTube or AV Receiver"
       required: true
       selector:
         text:
     content_id:
       description: "Optional: the title to start in the app, e.g. a YouTube video id. Empty just opens the app"
       selector:
         text:
   sequence:
     - choose:
         - conditions:
             - condition: template
               value_template: "{{ mode == 'stream' }}"
           sequence:
             - variables:
                 activity_started: "{{ not is_state('switch.watch_tv', 'on') }}"
             - if:
                 - condition: template
                   value_template: "{{ activity_started }}"
               then:
                 - action: switch.turn_on
                   target:
                     entity_id: switch.watch_tv
             - wait_template: >-
                 {{ states('media_player.family_room_tv') not in ['off', 'unavailable', 'unknown'] }}
               timeout: 60
               continue_on_timeout: false
             - if:
                 - condition: template
                   value_template: "{{ activity_started }}"
               then:
                 - delay:
                     seconds: 5
             - choose:
                 - conditions:
                     - condition: template
                       value_template: "{{ content_id is not none and (content_id | default('')) | string is match('^[0-9]{1,12}$') and source == 'Netflix' }}"
                   sequence:
                     - action: webostv.command
                       target:
                         entity_id: media_player.family_room_tv
                       data:
                         command: system.launcher/launch
                         payload:
                           id: netflix
                           contentId: "m=https://www.netflix.com/watch/{{ content_id }}&source_type=4"
                 - conditions:
                     - condition: template
                       value_template: "{{ content_id is not none and (content_id | default('')) | string is match('^[A-Za-z0-9_-]{1,64}$') and source == 'YouTube' }}"
                   sequence:
                     - action: webostv.command
                       target:
                         entity_id: media_player.family_room_tv
                       data:
                         command: system.launcher/launch
                         payload:
                           id: youtube.leanback.v4
                           contentId: "v={{ content_id }}"
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
               default:
                 - action: media_player.select_source
                   target:
                     entity_id: media_player.family_room_tv
                   data:
                     source: "{{ source }}"
         - conditions:
             - condition: template
               value_template: "{{ mode == 'disc' }}"
           sequence:
             - if:
                 - condition: not
                   conditions:
                     - condition: state
                       entity_id: switch.watch_a_bluray
                       state: "on"
               then:
                 - action: switch.turn_on
                   target:
                     entity_id: switch.watch_a_bluray
   ```

   The Netflix branch is for It's a Wonderful App; Matinee only sends `Fandango at Home` or `YouTube` with a
   `content_id`, and `AV Receiver` without one. The Netflix and Fandango at Home branches both take digits, but each
   only for its own `source`, so the two cannot be confused.

2. **The automation** (**Settings → Automations & scenes → Create automation → Edit in YAML**), with its own webhook
   id. It needs nothing new for Fandango at Home: its check already admits a `content_id` of digits and any non-empty
   `source`. It checks the payload and starts the script with `script.turn_on`, which returns at once: calling the script as
   an action would hold the webhook's answer for the script's whole run, and Matinee waits only 5 seconds.
   `local_only: true` limits the webhook to your own network, so Matinee must reach Home Assistant by its LAN address
   (not through Cloudflare). Replace `<a long random id>` with a long random string of your own (`openssl rand -hex
   24` makes one); it is the only credential:

   ```yaml
   alias: "Matinee plays on the Family Room TV"
   mode: queued
   triggers:
     - trigger: webhook
       webhook_id: "<a long random id>"
       allowed_methods:
         - POST
       local_only: true
   conditions:
     - condition: template
       value_template: >-
         {{ trigger.json is defined
            and trigger.json.event == 'matinee_play'
            and trigger.json.mode in ['stream', 'disc']
            and (trigger.json.source | default('')) | length > 0
            and (trigger.json.content_id is not defined
                 or (trigger.json.content_id is string
                     and trigger.json.content_id is match('^[A-Za-z0-9_-]{1,64}$'))) }}
   actions:
     - action: script.turn_on
       target:
         entity_id: script.wonderful_play_on_tv
       data:
         variables:
           mode: "{{ trigger.json.mode }}"
           source: "{{ trigger.json.source }}"
           content_id: "{{ trigger.json.content_id | default('') }}"
   ```

3. **Tell Matinee where it is.** The URL is a secret like `HA_WEBHOOK_URL`: it goes in `/etc/conf.d/matinee` on the
   Matinee host only, never in a repo, and Matinee never logs, returns or sends it. It is given to the Flask service
   only, never to `matinee-mcp`.

   ```sh
   vim /etc/conf.d/matinee        # HA_PLAY_WEBHOOK_URL=http://192.168.1.100:8123/api/webhook/<a long random id>
   rc-service matinee restart
   ```

### Try it

From a machine on the LAN. **This really starts the TV**, so do it when that's welcome. The id below is made up: use
the YouTube id of a film you own there, or send the Fandango at Home or disc payload instead.

```sh
curl -s -i -X POST 'http://192.168.1.100:8123/api/webhook/<a long random id>' \
  -H 'Content-Type: application/json' \
  -d '{"event": "matinee_play", "mode": "stream", "source": "YouTube", "title": "Brooklyn", "content_id": "dQw4w9WgXcQ"}'
```

Home Assistant answers an empty `200`. **A `200` doesn't prove the webhook id is right**: Home Assistant answers `200`
to an id it doesn't know too, and Matinee can't tell the difference (it doesn't listen for the TV). So after the first
play, open **Settings → Automations → Matinee plays on the Family Room TV → Traces**: a trace means the id matched; no
trace means fix the id in the automation or in `HA_PLAY_WEBHOOK_URL`. The script's own **Traces** show which branch
ran and whether the 60 second wait timed out. A `content_id` with anything else in it is stopped by the condition.
