"""The collection as CSV, one row per copy: served by GET /admin/api/export.csv (app.py) and written by
ops/export_csv.py. Split out of app.py."""
import csv, datetime as dt, io

from browse import get_location_path_map


# ============================================================================ export
EXPORT_COLUMNS = ("title", "year", "tmdb_id", "imdb_id", "certification", "runtime", "kind", "format", "service",
                  "location_path", "user_rating", "watch_count", "last_watched", "added_on",
                  "edition", "code_status", "purchased_on", "price")   # the last four came with schema v10
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


def export_rows(con):
    """One row per owned copy, in EXPORT_COLUMNS order, sorted so two exports diff cleanly. Watch notes are
    editor-only and deliberately absent; the title's household rating and watch history repeat on each row."""
    paths = get_location_path_map(con)
    rows = []
    for r in con.execute(
            """SELECT t.title, t.year, t.tmdb_id, t.imdb_id, t.certification, t.certification_override, t.runtime,
                      c.kind, c.format, c.service, c.location_id, c.created_at,
                      c.edition, c.code_status, c.purchased_on, c.price_cents,
                      (SELECT rating FROM user_ratings u WHERE u.title_id=t.id) AS user_rating,
                      (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count,
                      (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched
               FROM copies c JOIN titles t ON t.id=c.title_id
               ORDER BY t.sort_title, t.year, c.created_at, c.id"""):
        rows.append([r["title"], r["year"], r["tmdb_id"], r["imdb_id"],
                     r["certification_override"] or r["certification"], r["runtime"],
                     r["kind"], r["format"], r["service"], paths.get(r["location_id"]),
                     r["user_rating"], r["watch_count"], r["last_watched"], (r["created_at"] or "")[:10],
                     r["edition"], r["code_status"], r["purchased_on"], dollars(r["price_cents"])])
    return rows


def export_csv(con, today=None):
    """(body, filename): UTF-8 with a BOM so Excel reads accents, RFC 4180 quoting, CRLF line endings."""
    out = io.StringIO(newline="")
    w = csv.writer(out)             # default dialect: CRLF, quote only where needed, "" for a quote
    w.writerow(EXPORT_COLUMNS)
    for row in export_rows(con):
        w.writerow([csv_cell(v) for v in row])
    day = (today or dt.date.today()).isoformat()
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"), f"matinee-{day}.csv"
