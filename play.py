"""Play on the Family Room TV (spec 2026-11-26-play-on-tv-design §6): what a title can play, the body Home Assistant
gets, the synchronous call that sends it, and who watched. Route-free like youtube.py: the route, its answers and
the watch it logs are in app.py.

HA_PLAY_WEBHOOK_URL is a SECRET (the webhook id in it is the only credential): it is never logged (a failure names
the host and the kind of failure only), never returned, never put in a payload. Empty means Play is off."""
import json
import urllib.error
import urllib.parse
import urllib.request

from db import FORMATS
from people import list_people
from settings import HA_PLAY_WEBHOOK_URL, UA
from youtube import youtube_id_ok

TIMEOUT = 5                      # seconds: the editor waits for Home Assistant to take the request
TV_NAME = "Family Room TV"
YOUTUBE_SOURCE = "YouTube"       # the TV input the shared script launches the YouTube app on
DISC_SOURCE = "AV Receiver"
EVENT = "matinee_play"
MODES = ("youtube", "disc")
EVERYONE_CERTS = ("G", "PG", "PG-13")  # Play logs every active person for these; the adults for anything else
_PHYSICAL_ORDER = {f: i for i, f in enumerate(FORMATS["physical"])}


class PlayFailed(Exception):
    """Home Assistant could not be reached, was too slow, or did not answer 2xx. The message is the kind of failure
    only, never the URL."""


def enabled() -> bool:
    """True when the play webhook is set. Reads this module's HA_PLAY_WEBHOOK_URL at call time, so a patch of
    app.HA_PLAY_WEBHOOK_URL (which _AppModule copies here) takes effect."""
    return bool(HA_PLAY_WEBHOOK_URL)


def _location_paths(con):
    from browse import get_location_path_map  # browse.py imports this module, so this import waits for the call
    return get_location_path_map(con)


def _label(row):
    return f"{row['format']} ({row['service']})" if row["service"] else row["format"]


def ways_for(con, title_ids=None, paths=None) -> dict:
    """{title_id: ways} for the given titles (every title when None), in one query whatever the number of titles.
    A title with no way to play maps to {"youtube": None, "disc": None}. See ways()."""
    wanted = None if title_ids is None else set(title_ids)
    out = {tid: {"youtube": None, "disc": None} for tid in (wanted or ())}
    rows = con.execute("SELECT id, title_id, kind, format, service, location_id, youtube_id FROM copies "
                       "WHERE kind = 'physical' OR (kind = 'digital' AND youtube_id IS NOT NULL) "
                       "ORDER BY created_at, id").fetchall()
    rows = [r for r in rows if wanted is None or r["title_id"] in wanted]
    if any(r["kind"] == "physical" and r["location_id"] for r in rows) and paths is None:
        paths = _location_paths(con)
    paths = paths or {}
    # best format first; the sort is stable, so copies of one format keep their created_at, id order
    rows.sort(key=lambda r: r["kind"] == "physical" and _PHYSICAL_ORDER.get(r["format"], len(_PHYSICAL_ORDER)))
    for r in rows:
        w = out.setdefault(r["title_id"], {"youtube": None, "disc": None})
        if r["kind"] == "digital":
            if w["youtube"] is None and youtube_id_ok(r["youtube_id"]):
                w["youtube"] = {"copy_id": r["id"], "label": _label(r)}
        else:
            path = paths.get(r["location_id"])
            w["disc"] = (w["disc"] or []) + [{"copy_id": r["id"], "label": _label(r), "location_path": path,
                                              "line": f"{_label(r)} · {path}" if path else _label(r)}]
    return out


def ways(con, title_id, paths=None) -> dict:
    """How a title can play, from the database only:
    {"youtube": {"copy_id", "label"} | None, "disc": [{"copy_id", "label", "location_path", "line"}, ...] | None}.
    YouTube is the first digital copy (by created_at, id) whose youtube_id passes the id rule; a digital copy with
    no id, or one that is not well formed, is not a way. Disc is every physical copy, best format first in
    FORMATS["physical"] order; `line` is "4K UHD · Family Room › Media Cabinet › Shelf 2" (the format alone when
    the copy has no shelf)."""
    return ways_for(con, [title_id], paths)[title_id]


def youtube_id_for(con, title_id):
    """The id ways() would play on YouTube, or None."""
    for r in con.execute("SELECT youtube_id FROM copies WHERE title_id = ? AND kind = 'digital' "
                         "AND youtube_id IS NOT NULL ORDER BY created_at, id", (title_id,)):
        if youtube_id_ok(r["youtube_id"]):
            return r["youtube_id"]
    return None


def payload(mode, title, youtube_id=None) -> dict:
    """The body Home Assistant gets (spec §6.3). Raises ValueError for an unknown mode, or for `youtube` with an id
    that fails the id rule: such an id is never sent."""
    if mode == "youtube":
        if not youtube_id_ok(youtube_id):
            raise ValueError("not a well-formed YouTube id")
        return {"event": EVENT, "mode": "stream", "source": YOUTUBE_SOURCE, "title": title, "content_id": youtube_id}
    if mode == "disc":
        return {"event": EVENT, "mode": "disc", "source": DISC_SOURCE, "title": title}
    raise ValueError("mode must be youtube or disc")


def _host(url):
    try:
        return urllib.parse.urlsplit(url).hostname or "?"
    except ValueError:
        return "?"


def post_play(body, log) -> None:
    """POST `body` as JSON to HA_PLAY_WEBHOOK_URL and wait up to TIMEOUT seconds. Returns on a 2xx answer. Raises
    PlayFailed, after exactly one `log.warning` that names the host and the kind of failure (a status or an
    exception class, never the URL or str(exc), both of which can echo the webhook id), when the setting is empty,
    on any exception, or on a status outside 200-299."""
    url = HA_PLAY_WEBHOOK_URL
    if not url:
        log.warning("Play on TV: HA_PLAY_WEBHOOK_URL is not set")
        raise PlayFailed("not set")
    host = _host(url)
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            e.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
    except Exception as e:  # noqa: BLE001 - unreachable, timeout, a bad URL: all one answer to the editor
        log.warning("Play on TV: Home Assistant (host %s) failed: %s", host, type(e).__name__)
        raise PlayFailed(type(e).__name__) from None
    if not 200 <= status < 300:
        log.warning("Play on TV: Home Assistant (host %s) answered HTTP %s", host, status)
        raise PlayFailed(f"HTTP {status}")


def attendees_for(con, title_id) -> list:
    """Who a play logs when the request names nobody (decision 11): every active person when the title's rating
    (certification_override, else certification) is G, PG or PG-13; the active adults for R, NC-17, NR or no
    rating. Play's own rule: it neither calls nor changes people.default_attendees (the hand-logged rule)."""
    t = con.execute("SELECT certification, certification_override FROM titles WHERE id = ?", (title_id,)).fetchone()
    cert = (t["certification_override"] or t["certification"]) if t else None
    active = list_people(con, active_only=True)
    if cert in EVERYONE_CERTS:
        return [p["id"] for p in active]
    return [p["id"] for p in active if p["kind"] == "adult"]
