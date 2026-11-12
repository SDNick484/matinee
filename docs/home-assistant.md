# Home Assistant: movie night

When someone logs a watch for **today**, Matinee can tell Home Assistant, so an automation reacts: dim the lights,
announce the film, start a scene. Matinee only reports what was logged; Home Assistant decides what to do. It is off
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
automation's **Traces** to see whether it ran. Then tap **+ Watched Today** on a title in Matinee (or call `log_watch`
with today's date from the Claude app) and check the trace again.

## 3a. When Matinee sends it

Only when a watch is **logged for today** (the server's local date) with an explicit date and no date bucket:

| What was logged | Sent? |
|---|---|
| Today's date, from the phone UI or `log_watch` with `watched_date` = today | yes |
| An older date, e.g. 2019-07-04 | no |
| A bucket: "Past Year" … "10+ Years" ago, "Date Unknown" | no |
| No date (a dateless watch), including `log_watch` with no `watched_date` | no |
| Editing a watch's people afterwards, rating, deleting | no |

So importing or back-filling years of history never starts a movie night. A film logged through the Claude app
must carry today's date to fire: with no date the app records a dateless watch.

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
