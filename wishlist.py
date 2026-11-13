"""The movie wish list: wants (one per title) and the link to the family Wish Lists app. Route-free, like the other
split modules: the /admin/api routes are in app.py. Ported from Cookbook-Collection's wishlist.py, whose version
registers its own routes; here the routes stay in app.py and this module never imports it.

A want is independent of copies: a title can be wanted on 4K UHD while owned on Digital HD. It is editor-only data.
Adding a copy that satisfies it ends it (want_satisfied_by). What was pushed to the Wish Lists app is kept in
wish_items even after that. The Wish Lists app's own purchase status is never read, so there are no spoilers.
"""
import json, urllib.error, urllib.parse, urllib.request

from flask import abort

from settings import WISHLIST_URL, WISHLIST_MOVIE_URL
from common import clean, fold, make_err
from db import FORMATS, NOTE_MAX


ALL_FORMATS = [f for kind in FORMATS.values() for f in kind]


# ----------------------------------------------------------------------------- validation
def want_format_arg(v):
    """None for no format ("any copy"), else the canonical name from FORMATS; anything else is a 400."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    folded = fold(v) if isinstance(v, str) else None
    fmt = next((f for f in ALL_FORMATS if fold(f) == folded), None) if folded else None
    if fmt is None:
        abort(make_err(400, f"Format must be one of {', '.join(ALL_FORMATS)}."))
    return fmt


def want_note_arg(v):
    """The trimmed note (None when blank); a non-string or one over NOTE_MAX characters is a 400."""
    if v is None:
        return None
    if not isinstance(v, str):
        abort(make_err(400, f"A note can be up to {NOTE_MAX} characters."))
    note = clean(v)
    if note and len(note) > NOTE_MAX:
        abort(make_err(400, f"A note can be up to {NOTE_MAX} characters."))
    return note


def want_satisfied_by(wanted_format, copy_format):
    """A copy ends a want when it is the format wanted, or when the want names no format."""
    return wanted_format is None or wanted_format == copy_format


# ----------------------------------------------------------------------------- reading wants
def get_want(con, title_id):
    r = con.execute("SELECT wanted_format, note, created_at FROM wants WHERE title_id=?", (title_id,)).fetchone()
    return {"wanted_format": r["wanted_format"], "note": r["note"], "created_at": r["created_at"]} if r else None


def wishes_for_title(con, title_id):
    """What was pushed to the Wish Lists app for this title, oldest first (history: kept after a want is cleared)."""
    return [dict(r) for r in con.execute(
        "SELECT person_name, wanted_format, created_at FROM wish_items WHERE title_id=? ORDER BY id", (title_id,))]


def list_wants(con, image_url, poster_file_of):
    """Every want, newest first, for the Wanted view and the MCP get_wishlist tool."""
    out = []
    for r in con.execute("""SELECT w.title_id, w.wanted_format, w.note, w.created_at, t.tmdb_id, t.title, t.year,
                                   t.poster_file, t.custom_poster_file, t.fetched_at
                            FROM wants w JOIN titles t ON t.id = w.title_id
                            ORDER BY w.created_at DESC, w.title_id DESC"""):
        owned = [x[0] for x in con.execute("SELECT DISTINCT format FROM copies WHERE title_id=?", (r["title_id"],))]
        pushed = []
        for x in con.execute("SELECT person_name FROM wish_items WHERE title_id=? ORDER BY id", (r["title_id"],)):
            if x[0] and x[0] not in pushed:
                pushed.append(x[0])
        out.append({"title_id": r["title_id"], "tmdb_id": r["tmdb_id"], "title": r["title"], "year": r["year"],
                    "poster_url": image_url(poster_file_of(r), r["fetched_at"]),
                    "wanted_format": r["wanted_format"], "note": r["note"], "owned_formats": owned,
                    "pushed_to": pushed, "created_at": r["created_at"]})
    return out


# ----------------------------------------------------------------------------- the Wish Lists app
def want_query(title, year, wanted_format):
    """The shopping search: 'Spirited Away 2001 4K UHD' (each part only when there is one)."""
    return " ".join(str(p) for p in (title, year, wanted_format) if p)


def wish_item(title, want, extra_note=None, priority=None, price=None):
    """The item to add to a person's list. `title` is a titles row, `want` the dict from get_want."""
    name = f"{title['title']} ({title['year']})" if title["year"] else title["title"]
    q = want_query(title["title"], title["year"], want["wanted_format"])
    notes = ["Movie", want["wanted_format"] or "Any format", f"TMDB {title['tmdb_id']}"]
    for n in (want["note"], extra_note):
        if n:
            notes.append(str(n)[:300])
    item = {"name": name[:200], "kind": "online",
            "url": WISHLIST_MOVIE_URL.replace("{q}", urllib.parse.quote_plus(q)),
            "notes": " · ".join(notes), "priority": priority if priority in (1, 2, 3) else 2}
    if price not in (None, ""):
        item["price"] = price
    return item


def wishlist_call(method, path, body=None, timeout=5):
    """Talk to the Wish Lists app over localhost (it treats loopback requests as LAN editors). RuntimeError carries
    a sentence for the editor: the app's own message when it answered with one, else where it could not be reached."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(WISHLIST_URL + path, data=data, method=method, headers={
        "X-Wishlist": "1", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except Exception:  # noqa: BLE001 — any unreadable body just means no message
            msg = None
        raise RuntimeError(msg or f"wish list answered {e.code}") from None
    except (OSError, ValueError) as e:
        raise RuntimeError(f"wish list isn't reachable at {WISHLIST_URL} ({e})") from None


def wishlist_available():
    """True only when the Wish Lists app answers on WISHLIST_URL. The URL is set by default even when the app isn't
    installed, so checking the URL alone would show a button that always fails. A refused loopback connection
    returns at once, so asking on every /admin/api/me is cheap."""
    if not WISHLIST_URL:
        return False
    try:
        wishlist_call("GET", "/api/people", timeout=1)
        return True
    except RuntimeError:
        return False
