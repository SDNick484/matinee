"""YouTube ids on digital copies (schema v13, spec 2026-11-26-play-on-tv-design §4).

Standard library only and route-free, like lists.py: the routes and their error answers are in app.py. A YouTube id
is private to editors, like a copy's code status and price."""
import re
import urllib.parse

# The rule for an id, in one place (used when storing and again before anything is sent): exactly 11 characters.
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id_ok(value) -> bool:
    """True for a well-formed id. False for None, a non-string, or an id with a stray character or newline."""
    return isinstance(value, str) and YOUTUBE_ID_RE.fullmatch(value) is not None


_WATCH_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com")
_SHORT_HOSTS = ("youtu.be", "www.youtu.be", "m.youtu.be")


def parse_youtube_link(value):
    """The id in what someone pasted: None for None or blank, the id for a bare id or a YouTube address
    (youtube.com/watch with a `v` anywhere in the query, or youtu.be/<id>; www., m. or neither; http or https; the
    scheme may be left off; surrounding whitespace and extra parameters such as `si` or `t` are ignored).
    Raises ValueError for anything else, including a well-formed address whose id is not 11 characters."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("not text")
    text = value.strip()
    if not text:
        return None
    if youtube_id_ok(text):
        return text
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("not an address") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("not an http address")
    found = None
    if host in _WATCH_HOSTS and parts.path.rstrip("/") == "/watch":
        found = (urllib.parse.parse_qs(parts.query).get("v") or [None])[0]
    elif host in _SHORT_HOSTS:
        found = parts.path.strip("/") if parts.path.strip("/").count("/") == 0 else None
    if not youtube_id_ok(found):
        raise ValueError("no YouTube video id in it")
    return found


def youtube_watch_url(youtube_id):
    """The full address for an id (what the export carries), or None for no id or one that is not well formed."""
    return f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id_ok(youtube_id) else None
