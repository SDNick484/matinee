"""YouTube ids on digital copies (schema v13, spec 2026-11-26-play-on-tv-design §4).

Standard library only and route-free, like lists.py: the routes and their error answers are in app.py. A YouTube id
is private to editors, like a copy's code status and price."""
import re

# The rule for an id, in one place (used when storing and again before anything is sent): exactly 11 characters.
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id_ok(value) -> bool:
    """True for a well-formed id. False for None, a non-string, or an id with a stray character or newline."""
    return isinstance(value, str) and YOUTUBE_ID_RE.fullmatch(value) is not None
