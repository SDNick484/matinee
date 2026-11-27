"""The collection as a copy inventory and as CSV. `copy_inventory` is the one query behind GET /api/copies,
GET /admin/api/copies, GET /admin/api/export.csv (app.py) and ops/export_csv.py; the CSV comes in two shapes,
one row per copy or one row per title. Split out of app.py."""
import csv, datetime as dt, io

from browse import get_descendant_location_ids, get_location_path_map
from db import FORMATS, SERVICES
from youtube import youtube_watch_url


# ============================================================================ export
EXPORT_COLUMNS = ("title", "year", "tmdb_id", "imdb_id", "certification", "runtime", "kind", "format", "service",
                  "location_path", "user_rating", "watch_count", "last_watched", "added_on",
                  "edition", "code_status", "purchased_on", "price",   # these four came with schema v10
                  "youtube_link")   # v13: the full address of the copy's YouTube page; an address, since an id can start with "-"
FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")   # a spreadsheet would run a text cell that starts like this


def csv_cell(v):
    """One CSV cell: None is blank, numbers pass through, and text that could be read as a formula gets a
    leading quote (OWASP "CSV injection"). Titles and services are user-controlled text; numbers never are."""
    if v is None:
        return ""
    if isinstance(v, str) and v.startswith(FORMULA_LEAD):
        return "'" + v
    return v


def dollars(cents):
    """2499 -> "24.99" (a string a spreadsheet reads as a number); None stays blank. Never negative: the column is CHECKed."""
    return None if cents is None else f"{cents // 100}.{cents % 100:02d}"


KINDS = ("physical", "digital")
SHAPES = ("copy", "title")
PRIVATE_INVENTORY = ("code_status", "purchased_on", "price_cents")   # editor-only, like PRIVATE_COPY in browse.py
TITLE_EXPORT_COLUMNS = ("title", "year", "tmdb_id", "imdb_id", "certification", "runtime", "copies", "formats",
                        "services", "locations", "where", "user_rating", "watch_count", "last_watched", "added_on")


class InventoryError(ValueError):
    """A filter or shape value that is not one of the valid ones; str() is the sentence a 400 or the CLI prints."""


def _pick(label, value, valid):
    """`value` when it is one of `valid`; otherwise an InventoryError naming every valid value."""
    if value not in valid:
        raise InventoryError(f'Unknown {label} "{value}". Use one of: {", ".join(valid)}.')
    return value


def _path_key(path):
    """A breadcrumb for comparing: case-folded, split on a real or typed separator ("›" or ">"), re-joined."""
    return " › ".join(p.strip() for p in str(path).replace(">", "›").split("›")).casefold()


def _location_ids(con, value):
    """The ids a `location` filter means: an id or a breadcrumb path, plus every location below it. Two
    locations can share a path, so a path may name more than one."""
    paths = get_location_path_map(con)
    text = str(value).strip()
    if text.isdigit() and int(text) in paths:
        roots = [int(text)]
    else:
        roots = [i for i, p in paths.items() if _path_key(p) == _path_key(text)]
    if not roots:
        shown = sorted(paths.values())[:8]
        hint = f"Use a location id or a path such as: {', '.join(shown)}." if shown else "No locations are set up yet."
        raise InventoryError(f'Unknown location "{value}". {hint}')
    ids = set()
    for root in roots:
        ids |= get_descendant_location_ids(con, root)
    return sorted(ids)


def _inventory(con, kind, format, service, location_id):
    """Every copy that matches all the filters, as dicts with the title's export columns too, in export order."""
    where, args = [], []
    if kind not in (None, ""):
        where.append("c.kind=?")
        args.append(_pick("kind", kind, KINDS))
    if format not in (None, ""):
        where.append("c.format=?")
        args.append(_pick("format", format, [f for fs in FORMATS.values() for f in fs]))
    if service not in (None, ""):
        where.append("c.service=?")
        args.append(_pick("service", service, SERVICES))
    if location_id not in (None, ""):
        ids = _location_ids(con, location_id)
        where.append(f"c.location_id IN ({','.join('?' * len(ids))})")
        args += ids
    paths = get_location_path_map(con)
    rows = []
    for r in con.execute(
            f"""SELECT t.id AS title_id, t.title, t.year, t.tmdb_id, t.imdb_id, t.certification,
                       t.certification_override, t.runtime,
                       c.kind, c.format, c.service, c.location_id, c.created_at,
                       c.edition, c.code_status, c.purchased_on, c.price_cents, c.youtube_id,
                       (SELECT rating FROM user_ratings u WHERE u.title_id=t.id) AS user_rating,
                       (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count,
                       (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched
                FROM copies c JOIN titles t ON t.id=c.title_id
                {"WHERE " + " AND ".join(where) if where else ""}
                ORDER BY t.sort_title, t.year, c.created_at, c.id""", args):
        d = dict(r)
        d["location_path"] = paths.get(r["location_id"])
        rows.append(d)
    return rows


def copy_inventory(con, *, kind=None, format=None, service=None, location_id=None, private=False):
    """One dict per copy: title_id, title, year, kind, format, service, edition, location_id, location_path, plus
    code_status, purchased_on and price_cents when `private`. The filters apply to the copy and combine with AND;
    `location_id` is a location id or a breadcrumb path and includes the locations below it. A value that is not
    valid raises InventoryError."""
    keys = ["title_id", "title", "year", "kind", "format", "service", "edition", "location_id", "location_path"]
    if private:
        keys += PRIVATE_INVENTORY
    return [{k: d[k] for k in keys} for d in _inventory(con, kind, format, service, location_id)]


def export_rows(con, **filters):
    """One row per owned copy, in EXPORT_COLUMNS order, sorted so two exports diff cleanly. Watch notes are
    editor-only and deliberately absent; the title's household rating and watch history repeat on each row.
    `filters` are copy_inventory's kind, format, service and location_id."""
    return [[d["title"], d["year"], d["tmdb_id"], d["imdb_id"], d["certification_override"] or d["certification"],
             d["runtime"], d["kind"], d["format"], d["service"], d["location_path"],
             d["user_rating"], d["watch_count"], d["last_watched"], (d["created_at"] or "")[:10],
             d["edition"], d["code_status"], d["purchased_on"], dollars(d["price_cents"]),
             youtube_watch_url(d["youtube_id"])]
            for d in _inventory(con, filters.get("kind"), filters.get("format"), filters.get("service"),
                                filters.get("location_id"))]


def _where(d):
    """'4K UHD Steelbook @ Family Room › Media Cabinet › Shelf 1': the format with its edition, then the place
    (the location path, or the service), with the words where_is uses when a copy has neither."""
    label = f"{d['format']} {d['edition']}" if d["edition"] else d["format"]
    place = d["location_path"] or d["service"] or ("Unassigned shelf" if d["kind"] == "physical" else "Digital")
    return f"{label} @ {place}"


def _distinct(values):
    return "; ".join(dict.fromkeys(v for v in values if v))


def title_rows(con, **filters):
    """One row per title, in TITLE_EXPORT_COLUMNS order, summarizing only the copies that match `filters`."""
    by_title = {}
    for d in _inventory(con, filters.get("kind"), filters.get("format"), filters.get("service"),
                        filters.get("location_id")):
        by_title.setdefault(d["title_id"], []).append(d)
    rows = []
    for copies in by_title.values():
        t = copies[0]
        rows.append([t["title"], t["year"], t["tmdb_id"], t["imdb_id"], t["certification_override"] or t["certification"],
                     t["runtime"], len(copies),
                     _distinct(c["format"] for c in copies), _distinct(c["service"] for c in copies),
                     _distinct(c["location_path"] for c in copies), "; ".join(_where(c) for c in copies),
                     t["user_rating"], t["watch_count"], t["last_watched"],
                     min((c["created_at"] or "")[:10] for c in copies)])
    return rows


def export_csv(con, today=None, *, shape="copy", **filters):
    """(body, filename): UTF-8 with a BOM so Excel reads accents, RFC 4180 quoting, CRLF line endings.
    `shape` is "copy" (a row per copy) or "title" (a row per title, filename ending -titles); `filters` are
    copy_inventory's kind, format, service and location_id. Raises InventoryError for a value that is not valid."""
    _pick("shape", shape, SHAPES)
    out = io.StringIO(newline="")
    w = csv.writer(out)             # default dialect: CRLF, quote only where needed, "" for a quote
    w.writerow(EXPORT_COLUMNS if shape == "copy" else TITLE_EXPORT_COLUMNS)
    for row in (export_rows if shape == "copy" else title_rows)(con, **filters):
        w.writerow([csv_cell(v) for v in row])
    day = (today or dt.date.today()).isoformat()
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"), f"matinee-{day}{'' if shape == 'copy' else '-titles'}.csv"


def not_loaded_csv(rows):
    """The not-loaded rows as a spreadsheet: title, year, youtube_link (the address that was given, else the id's),
    reason, near. UTF-8 with a BOM and CRLF like the export; text that starts like a formula gets a leading quote,
    and an address starts with "https", so an id that begins with "-" stays whole."""
    out = io.StringIO(newline="")
    w = csv.writer(out)
    w.writerow(("title", "year", "youtube_link", "reason", "near"))
    for r in rows:
        link = r["play_link"] or youtube_watch_url(r["video_id"]) or r["video_id"]
        near = "; ".join(f"{n['title']} ({n['year']}) [{n['held']}]" if n["year"] else f"{n['title']} [{n['held']}]"
                         for n in r["near"])
        w.writerow([csv_cell(v) for v in (r["title"], r["year"], link, r["reason"], near)])
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")
