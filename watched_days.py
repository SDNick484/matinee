"""The Calendar's data (spec 2026-11-19-calendar-settings-design §3): the movies watched on each day of a month.

Standard library only, like lists.py: the MCP server's direct-SQLite mode uses it without Flask. The routes are in
app.py. Named so it never shadows the standard library's `calendar`.

A watch is placed on its `watched_date` only when that date is set and the watch has no `date_bucket`: a bucket
("1-2 Years") is stored with an estimated date, and an estimate must never show as a night we watched something.
Matinee stores a plain local date, so there is no midnight or 4 am rule. Public data: titles and dates only, never
notes, people or watch ids."""
import re

MONTH_RE = re.compile(r"(\d{4})-(\d{2})", re.ASCII)
MONTH_ERROR = "month must look like 2026-11"


def parse_month(raw, today):
    """(year, month) from "YYYY-MM"; None means the month `today` is in. Anything else is a ValueError(MONTH_ERROR)."""
    if raw is None:
        return today.year, today.month
    m = MONTH_RE.fullmatch(raw)
    if not m or int(m[1]) < 1 or not 1 <= int(m[2]) <= 12:
        raise ValueError(MONTH_ERROR)
    return int(m[1]), int(m[2])


def month_view(con, year, month, image_url=None):
    """{"YYYY-MM-DD": [{title_id, title, year[, poster_url]}]} for one month, only days that have a movie. Within a
    day the first logged (watch created_at, then id) comes first, and a movie watched twice that day shows once.
    `poster_url` is added when `image_url(file, fetched_at)` is given (the custom poster wins)."""
    ym = f"{year:04d}-{month:02d}"   # "-31" is past every real day, so one range covers any month length
    rows = con.execute(
        "SELECT w.watched_date, t.id, t.title, t.year, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file,"
        " t.fetched_at FROM watches w JOIN titles t ON t.id = w.title_id"
        " WHERE w.watched_date BETWEEN ? AND ? AND (w.date_bucket IS NULL OR w.date_bucket = '')"
        " ORDER BY w.watched_date, w.created_at, w.id", (f"{ym}-01", f"{ym}-31")).fetchall()
    days = {}
    for r in rows:
        day = days.setdefault(r["watched_date"], [])
        if any(e["title_id"] == r["id"] for e in day):
            continue
        entry = {"title_id": r["id"], "title": r["title"], "year": r["year"]}
        if image_url:
            entry["poster_url"] = image_url(r["poster_file"], r["fetched_at"])
        day.append(entry)
    return days
