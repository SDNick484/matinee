"""Fandango at Home ids on digital copies (schema v14, spec 2026-11-27-fandango-play-design §3).

Standard library only and route-free, like youtube.py: the routes and their error answers are in app.py. The id is
the number at the end of a film's Fandango at Home address (The Addams Family is
https://athome.fandango.com/content/browse/details/The-Addams-Family/15429, so 15429); the TV's Fandango at Home app
(webOS id `vudu`) starts the film from it. Like the YouTube id it is private to editors."""
import re
import urllib.parse

# The rule for an id, in one place (used when storing and again before anything is sent): 1 to 12 digits 0-9.
FANDANGO_ID_RE = re.compile(r"^[0-9]{1,12}$")

# athome.fandango.com is the service's address; www.vudu.com is the old address of the same pages.
_HOSTS = ("athome.fandango.com", "www.athome.fandango.com", "vudu.com", "www.vudu.com")


def fandango_id_ok(value) -> bool:
    """True for a well-formed id. False for None, a non-string, or anything but 1 to 12 ASCII digits (a trailing
    newline included)."""
    return isinstance(value, str) and FANDANGO_ID_RE.fullmatch(value) is not None


def parse_fandango_link(value):
    """The id in what someone pasted: None for None or blank, the id for a bare number or a film's Fandango at Home
    address (athome.fandango.com or the old www.vudu.com, `.../details/<name>/<id>`; http or https, the scheme and
    www. may be left off; a trailing slash, a query string, a #fragment and surrounding whitespace are ignored).
    Raises ValueError for anything else: another host, a path without a number at the end, a number longer than 12
    digits."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("not text")
    text = value.strip()
    if not text:
        return None
    if fandango_id_ok(text):
        return text
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("not an address") from None
    if parts.scheme not in ("http", "https") or host not in _HOSTS:
        raise ValueError("not a Fandango at Home address")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2 or "details" not in segments[:-1] or not fandango_id_ok(segments[-1]):
        raise ValueError("no Fandango at Home id in it")
    return segments[-1]


def fandango_details_url(fandango_id):
    """The address shown back and exported for an id, or None for no id or one that is not well formed. The film's
    name in the address is not stored, so it reads "title"; Fandango at Home ignores that part."""
    if not fandango_id_ok(fandango_id):
        return None
    return f"https://athome.fandango.com/content/browse/details/title/{fandango_id}"
