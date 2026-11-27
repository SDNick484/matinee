"""YouTube ids on digital copies (schema v13, spec 2026-11-26-play-on-tv-design §4).

Standard library only and route-free, like lists.py: the routes and their error answers are in app.py. A YouTube id
is private to editors, like a copy's code status and price. The second half (spec §5) matches a list of owned
YouTube films to digital copies; `plan_links` only reads, and the route in app.py writes what it plans."""
import csv
import io
import json
import re
import unicodedata
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


# ----------------------------------------------------------------------------------------------- loading a list
# The retail extras that are dropped when comparing titles (spec §5 "Cleaning a title"). Both sides are cleaned the
# same way, for comparison only: nothing stored is changed.
_PREFIXES = ("marvel studios'", "illumination presents:", "dr. seuss'")
_SUFFIX_RES = tuple(re.compile(p, re.I) for p in (
    r"\s*\(unrated\)$", r"\s*\(\d{4}\)$", r"\s+the movie$", r":\s*based on a true story$",
    r"\s+[-\u2013\u2014]\s+director's cut$", r":\s*director's definitive cut$", r":\s*final cut$"))
_TRAILING_THE = re.compile(r",\s*the$", re.I)


def clean_title(title) -> str:
    """A title as a person would write it, without the retail extras: `Marvel Studios'`, `Illumination Presents:`,
    `Dr. Seuss'`, `(Unrated)`, a trailing `(YYYY)`, ` The Movie`, `: Based on a True Story`, ` - Director's Cut`,
    `: Director's Definitive Cut` and `: Final Cut` come off; a trailing `, The` moves to the front; `&` reads as
    "and". The year is not part of this. Never empty: a title that is all extras comes back as it was."""
    original = " ".join(str(title or "").replace("\u2019", "'").split())
    text = original
    changed = True
    while changed and text:
        changed = False
        for prefix in _PREFIXES:
            if text.lower().startswith(prefix):
                text, changed = text[len(prefix):].strip(), True
        for rx in _SUFFIX_RES:
            cut = rx.sub("", text, count=1)
            if cut != text:
                text, changed = cut.strip(), True
    if _TRAILING_THE.search(text) and _TRAILING_THE.sub("", text).strip():
        text = "The " + _TRAILING_THE.sub("", text).strip()
    text = " ".join(text.replace("&", " and ").split())
    return text or original


def title_key(title) -> str:
    """What two titles are compared by: the cleaned title with case, accents and punctuation ignored and a leading
    "The", "A" or "An" dropped ("WALL·E" meets "WALL-E"; "Dune" never meets "Dune: Part Two")."""
    text = unicodedata.normalize("NFKD", clean_title(title))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("'", "")
    words = re.sub(r"[^0-9a-z]+", " ", text).split()
    if len(words) > 1 and words[0] in ("the", "a", "an"):
        words = words[1:]
    return " ".join(words)


LIBRARY_FIELDS = ("title", "year", "video_id", "play_link")
MAX_LIBRARY_ROWS = 5000


def _text(value) -> str:
    """A list cell as text: None is blank, a whole number (2008 or 2008.0) has no decimals."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_library(body: bytes, content_type: str) -> list:
    """The rows of a list as exported: [{"title", "year", "video_id", "play_link"}], every value text. `body` is a
    JSON array of objects or a CSV with those four columns (a UTF-8 byte-order mark and blank lines are fine; the
    Content-Type says which, and a body that starts with `[` or `{` is JSON when it doesn't). A missing value is
    blank, and plan_links turns it into a not-loaded row. Raises ValueError(sentence) for a body that is not such a
    list."""
    try:
        text = (body or b"").decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("The list must be UTF-8 text.") from None
    if not text.strip():
        raise ValueError("There is no list in the request: send a JSON array or a CSV with the columns "
                         + ", ".join(LIBRARY_FIELDS) + ".")
    kind = (content_type or "").split(";")[0].strip().lower()
    as_json = "json" in kind or ("csv" not in kind and text.lstrip()[0] in "[{")
    if as_json:
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("The list isn't valid JSON.") from None
        if not isinstance(data, list):
            raise ValueError("The list must be a JSON array of films, each with " + ", ".join(LIBRARY_FIELDS) + ".")
        if any(not isinstance(item, dict) for item in data):
            raise ValueError("Every film in the list must be an object with " + ", ".join(LIBRARY_FIELDS) + ".")
        rows = [{f: _text(item.get(f)) for f in LIBRARY_FIELDS} for item in data]
    else:
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""))
            header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
            if any(f not in header for f in LIBRARY_FIELDS):
                raise ValueError("The CSV needs the columns " + ", ".join(LIBRARY_FIELDS) + " in its first row.")
            reader.fieldnames = header
            rows = []
            for item in reader:
                row = {f: _text(item.get(f)) for f in LIBRARY_FIELDS}
                if any(row.values()):                     # a blank line (or ,,,) is not a film
                    rows.append(row)
        except csv.Error:
            raise ValueError("The CSV could not be read.") from None
    if not rows:
        raise ValueError("The list has no films in it.")
    if len(rows) > MAX_LIBRARY_ROWS:
        raise ValueError(f"The list has more than {MAX_LIBRARY_ROWS} films; load it in parts.")
    return rows


def _held(copies) -> str:
    """What Matinee holds of a title: its digital copies ("Digital 4K · Movies Anywhere"), else "Blu-ray only"."""
    digital = [f"{c['format']} · {c['service']}" if c["service"] else c["format"] for c in copies if c["kind"] == "digital"]
    if digital:
        return "; ".join(digital)
    discs = sorted({c["format"] for c in copies})
    return ", ".join(discs) + " only" if discs else "No copies"


def _name(title) -> str:
    return f"{title['title']} ({title['year']})" if title["year"] else title["title"]


def plan_links(con, rows) -> dict:
    """Which rows of a list link by themselves, and why each of the others doesn't. Reads only. A row links when
    (1) its video_id is well formed and is the `v=` of its play_link, (2) no other row has the same cleaned title and
    year, (3) exactly one title in Matinee has that cleaned title and year (never loosened), (4) that title has
    exactly one digital copy, with no id yet, and (5) the id is on no other copy. A copy that already has this id
    counts as already linked; an id that is on a copy is never replaced.

    -> {"link": [(copy_id, video_id)], "already_linked": n, "not_loaded": [{title, year, video_id, play_link,
    reason, near: [{title, year, held}]}]}"""
    rows = [{f: _text(r.get(f)) for f in LIBRARY_FIELDS} for r in rows]
    titles = [dict(r) for r in con.execute("SELECT id, title, year FROM titles")]
    by_key, copies_of = {}, {}
    for t in titles:
        by_key.setdefault(title_key(t["title"]), []).append(t)
    for c in con.execute("SELECT id, title_id, kind, format, service, youtube_id FROM copies ORDER BY id"):
        copies_of.setdefault(c["title_id"], []).append(dict(c))
    holder = {}                                  # id -> (copy, title) for every id on a copy
    for t in titles:
        for c in copies_of.get(t["id"], []):
            if c["youtube_id"]:
                holder[c["youtube_id"]] = (c, t)

    def near(candidates):
        return [{"title": t["title"], "year": t["year"], "held": _held(copies_of.get(t["id"], []))}
                for t in candidates]

    keys = [(title_key(r["title"]), r["year"]) for r in rows]
    listed = {}
    for key in keys:
        if key[0] and key[1]:
            listed[key] = listed.get(key, 0) + 1
    ids_listed = {}
    for r in rows:
        ids_listed.setdefault(r["video_id"], set()).add(title_key(r["title"]) + "|" + r["year"])

    link, already, refused = [], 0, []

    def refuse(row, reason, candidates=()):
        refused.append({**row, "reason": reason, "near": near(candidates)})

    for row, key in zip(rows, keys):
        vid = row["video_id"]
        if not youtube_id_ok(vid):                                               # rule 1
            refuse(row, "The video_id isn't a YouTube id (11 letters, digits, - or _).")
            continue
        if not row["play_link"]:
            refuse(row, "There is no play_link to check the video_id against.")
            continue
        try:
            linked = parse_youtube_link(row["play_link"])
        except ValueError:
            linked = None
        if linked is None:
            refuse(row, "The play_link isn't a YouTube address.")
            continue
        if linked != vid:
            refuse(row, "The video_id and the play_link are different videos, so neither can be trusted.")
            continue
        if not key[0] or not key[1]:
            refuse(row, "The title or the year is missing, so it can't be matched.")
            continue
        if listed[key] > 1:                                                      # rule 2
            refuse(row, "Listed more than once for the same year, so there is no telling which id is right.")
            continue
        if len(ids_listed[vid]) > 1:
            refuse(row, "This video_id is listed for more than one film.")
            continue
        same_title = by_key.get(key[0], [])                                      # rule 3
        titles_here = [t for t in same_title if str(t["year"] or "") == key[1]]
        if not titles_here:
            if same_title:
                refuse(row, "Matinee has this title under a different year.", same_title)
            else:
                refuse(row, "Nothing in Matinee has this title.")
            continue
        if len(titles_here) > 1:
            refuse(row, "More than one title in Matinee has this title and year.", titles_here)
            continue
        title = titles_here[0]
        digital = [c for c in copies_of.get(title["id"], []) if c["kind"] == "digital"]      # rule 4
        if not digital:
            refuse(row, "Matinee has it on disc only: there is no digital copy to link.", titles_here)
            continue
        if len(digital) > 1:
            refuse(row, "That title has more than one digital copy, so there is no telling which one this is.",
                   titles_here)
            continue
        copy = digital[0]
        if copy["youtube_id"] == vid:
            already += 1
            continue
        if copy["youtube_id"]:
            refuse(row, "The digital copy already has a different YouTube link, and one that is there is never "
                        "replaced.", titles_here)
            continue
        if vid in holder:                                                        # rule 5
            other = holder[vid][1]
            refuse(row, f"That YouTube id is already on another film: {_name(other)}.", titles_here)
            continue
        link.append((copy["id"], vid))
    return {"link": link, "already_linked": already, "not_loaded": refused}
