"""Curated lists (schema v12, spec 2026-11-12-lists-design): the built-in Watch Soon list and editor-made seasonal
and custom lists of titles.

Standard library only, like recommend.py: recommend.py and the MCP server's direct-SQLite mode use it without Flask.
The routes are in app.py. A refusal here is a ListError(status, message), which app.py answers with make_err.
Lists, their windows and their members are public; only `seen_by` on a list's titles is editor-only."""
import datetime as dt

WATCH_SOON = "Watch Soon"
LIST_KINDS = ("watch_soon", "seasonal", "custom")
EDITOR_LIST_KINDS = ("seasonal", "custom")   # what an editor may create or switch between
LIST_NAME_MAX = 40
NO_LISTS = "This Matinee database has no lists yet. Update Matinee."
_MAX_ROWID = 2 ** 63 - 1


class ListError(ValueError):
    """A list request that is refused: `status` is the HTTP status (400, 404 or 409), the message is for people."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def has_lists(con) -> bool:
    """False for a database older than schema v12 (the MCP server may open one read-only before the app migrates)."""
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lists'").fetchone() is not None


# ------------------------------------------------------------------------------------------------ season windows
def parse_month_day(raw, field="season_start"):
    """'MM-DD' for a day that exists in a leap year (02-29 is fine, 02-30 is not), or ListError 400."""
    msg = f"{field} must be a month and day like 11-25."
    s = raw.strip() if isinstance(raw, str) else None
    if not s or len(s) != 5 or s[2] != "-" or not (s[:2] + s[3:]).isdigit() or not s.isascii():
        raise ListError(400, msg)
    try:
        dt.date(2000, int(s[:2]), int(s[3:]))
    except ValueError:
        raise ListError(400, msg) from None
    return s


def in_window(start, end, month_day) -> bool:
    """True when 'MM-DD' `month_day` falls in the window. start > end wraps the year: 11-25 to 01-01 holds
    12-05 and 01-01 but not 01-02. A list with no window is never in season."""
    if not start or not end:
        return False
    if start <= end:
        return start <= month_day <= end
    return month_day >= start or month_day <= end


def _first_sunday(year, month) -> int:
    first = dt.date(year, month, 1)
    return 1 + (6 - first.weekday()) % 7


def pacific_by_rule(now_utc: dt.datetime) -> dt.date:
    """The America/Los_Angeles date by the US rule (UTC-8; UTC-7 from 02:00 on the second Sunday of March to
    02:00 on the first Sunday of November). Used when the system has no tz database (a bare Alpine image)."""
    y = now_utc.year
    utc = dt.timezone.utc
    dst_on = dt.datetime(y, 3, _first_sunday(y, 3) + 7, 10, tzinfo=utc)   # 02:00 PST
    dst_off = dt.datetime(y, 11, _first_sunday(y, 11), 9, tzinfo=utc)     # 02:00 PDT
    hours = -7 if dst_on <= now_utc < dst_off else -8
    return (now_utc + dt.timedelta(hours=hours)).date()


def pacific_today(now: "dt.datetime | None" = None) -> dt.date:
    """Today in America/Los_Angeles: the household's date for `in_season`."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Los_Angeles")).date()
    except Exception:  # noqa: BLE001 - no zoneinfo module or no tz database: the rule is the same
        return pacific_by_rule(now.astimezone(dt.timezone.utc))


def month_day(day: dt.date) -> str:
    return day.strftime("%m-%d")


# ---------------------------------------------------------------------------------------------------------- reads
LIST_SELECT = "SELECT l.*, (SELECT count(*) FROM list_titles lt WHERE lt.list_id = l.id) AS n FROM lists l"
LIST_ORDER = "ORDER BY l.sort_order, l.id"


def list_out(row, today: dt.date) -> dict:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "season_start": row["season_start"],
            "season_end": row["season_end"],
            "in_season": in_window(row["season_start"], row["season_end"], month_day(today)),
            "sort_order": row["sort_order"], "count": row["n"]}


def all_lists(con, today: dt.date) -> list:
    if not has_lists(con):
        return []
    return [list_out(r, today) for r in con.execute(f"{LIST_SELECT} {LIST_ORDER}")]


def get_list_row(con, list_id):
    if not isinstance(list_id, int) or isinstance(list_id, bool) or not 0 < list_id <= _MAX_ROWID:
        return None
    return con.execute(f"{LIST_SELECT} WHERE l.id = ?", (list_id,)).fetchone()


def find_list(con, token):
    """The list a token names: an id, or a name in any case. None when there is no such list."""
    if isinstance(token, int) and not isinstance(token, bool):
        return get_list_row(con, token)
    t = str(token if token is not None else "").strip()
    if not t:
        return None
    if t.isascii() and t.isdigit():
        row = get_list_row(con, int(t)) if len(t) <= 19 else None
        if row is not None:
            return row
    return con.execute(f"{LIST_SELECT} WHERE l.name = ?", (t,)).fetchone()   # name is COLLATE NOCASE


def unknown_list_message(con, token) -> str:
    names = [r[0] for r in con.execute("SELECT name FROM lists ORDER BY sort_order, id")]
    return f"Unknown list: {token}. Lists: {', '.join(names)}."


def watch_soon_id(con):
    row = con.execute("SELECT id FROM lists WHERE kind = 'watch_soon'").fetchone()
    return row[0] if row else None


def title_lists(con, title_id, today: dt.date) -> list:
    """The lists a title is on, in list order: [{"id", "name", "kind", "in_season"}]."""
    rows = con.execute("""SELECT l.id, l.name, l.kind, l.season_start, l.season_end FROM list_titles lt
                          JOIN lists l ON l.id = lt.list_id WHERE lt.title_id = ? ORDER BY l.sort_order, l.id""",
                       (title_id,))
    md = month_day(today)
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"],
             "in_season": in_window(r["season_start"], r["season_end"], md)} for r in rows]


def in_season_lists(con, today: dt.date) -> list:
    """Rows (id, name) of every list whose window holds today, in list order."""
    md = month_day(today)
    return [r for r in con.execute("SELECT id, name, season_start, season_end FROM lists "
                                   "WHERE season_start IS NOT NULL ORDER BY sort_order, id")
            if in_window(r["season_start"], r["season_end"], md)]


def season_windows(con) -> str:
    """'Christmas (11-25 to 01-01), Halloween (10-01 to 10-31)', for hints."""
    return ", ".join(f"{r['name']} ({r['season_start']} to {r['season_end']})" for r in con.execute(
        "SELECT name, season_start, season_end FROM lists WHERE season_start IS NOT NULL ORDER BY sort_order, id"))


def list_members(con, list_id, private) -> list:
    """A list's titles, oldest added first: id, title, year, formats, owned, added_at (a date), plus poster_file and
    fetched_at for the caller to turn into a URL. For an editor (private), and once people are set up, each title
    also names everyone who has seen it; a public request never gets that key."""
    from recommend import sorted_formats
    rows = con.execute("""
        SELECT t.id, t.title, t.year, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file, t.fetched_at,
               lt.added_at,
               (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id = t.id) AS formats
        FROM list_titles lt JOIN titles t ON t.id = lt.title_id
        WHERE lt.list_id = ?
        ORDER BY lt.added_at, t.sort_title, t.id""", (list_id,)).fetchall()
    out = [{"id": r["id"], "title": r["title"], "year": r["year"],
            "formats": sorted_formats(r["formats"].split(",")) if r["formats"] else [],
            "owned": bool(r["formats"]), "added_at": (r["added_at"] or "")[:10] or None,
            "poster_file": r["poster_file"], "fetched_at": r["fetched_at"]} for r in rows]
    if private:
        import recommend
        if recommend.has_people(con):
            names = recommend._seen_by(con, [o["id"] for o in out])
            for o in out:
                o["seen_by"] = names[o["id"]]
    return out


# --------------------------------------------------------------------------------------------------------- writes
def list_name_arg(con, raw, list_id=None) -> str:
    name = " ".join(raw.split()) if isinstance(raw, str) else None
    if not name or len(name) > LIST_NAME_MAX:
        raise ListError(400, f"A list name is required (up to {LIST_NAME_MAX} characters).")
    other = con.execute("SELECT name FROM lists WHERE name = ? AND id IS NOT ?", (name, list_id)).fetchone()
    if other:  # the column is COLLATE NOCASE, so this is case-insensitive
        raise ListError(409, f"A list named {other['name']} already exists.")
    return name


def _window_value(raw, field):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return parse_month_day(raw, field)


def list_window_args(data, current=(None, None)):
    """(season_start, season_end) after applying `data` to `current`: both set or both empty."""
    start = _window_value(data["season_start"], "season_start") if "season_start" in data else current[0]
    end = _window_value(data["season_end"], "season_end") if "season_end" in data else current[1]
    if (start is None) != (end is None):
        raise ListError(400, "season_start and season_end go together: send both, or both empty.")
    return start, end


def _sort_arg(raw):
    if isinstance(raw, bool) or not isinstance(raw, int) or abs(raw) > 1_000_000:
        raise ListError(400, "sort_order must be a whole number.")
    return raw


def _kind_arg(raw):
    if raw == "watch_soon":
        raise ListError(400, "There is only one Watch Soon list.")
    if raw not in EDITOR_LIST_KINDS:
        raise ListError(400, "kind must be seasonal or custom.")
    return raw


def create_list(con, data) -> int:
    """Insert a list from a POST body; returns its id. The caller commits."""
    if not isinstance(data, dict):
        raise ListError(400, "Send a JSON object.")
    name = list_name_arg(con, data.get("name"))
    start, end = list_window_args(data)
    kind = _kind_arg(data["kind"]) if data.get("kind") is not None else ("seasonal" if start else "custom")
    if "sort_order" in data:
        sort_order = _sort_arg(data["sort_order"])
    else:
        sort_order = con.execute("SELECT coalesce(max(sort_order) + 1, 0) FROM lists").fetchone()[0]
    return con.execute("INSERT INTO lists (name, kind, season_start, season_end, sort_order) VALUES (?, ?, ?, ?, ?)",
                       (name, kind, start, end, sort_order)).lastrowid


def update_list(con, row, data) -> None:
    """Apply a PUT body (any of name, kind, season_start, season_end, sort_order) to a list. Watch Soon keeps its
    name, kind and empty window; only its place in the order can change. The caller commits."""
    if not isinstance(data, dict):
        raise ListError(400, "Send a JSON object.")
    soon = row["kind"] == "watch_soon"
    name = row["name"]
    if "name" in data:
        if soon and data["name"] != row["name"]:
            raise ListError(400, "Watch Soon can't be renamed.")
        name = list_name_arg(con, data["name"], row["id"])
    start, end = list_window_args(data, (row["season_start"], row["season_end"]))
    kind = row["kind"]
    if "kind" in data and data["kind"] != row["kind"]:
        if soon:
            raise ListError(400, "Watch Soon can't change kind.")
        kind = _kind_arg(data["kind"])
    if soon and start is not None:
        raise ListError(400, "Watch Soon has no season.")
    sort_order = _sort_arg(data["sort_order"]) if "sort_order" in data else row["sort_order"]
    con.execute("UPDATE lists SET name = ?, kind = ?, season_start = ?, season_end = ?, sort_order = ? WHERE id = ?",
                (name, kind, start, end, sort_order, row["id"]))


def delete_list(con, row) -> None:
    if row["kind"] == "watch_soon":
        raise ListError(400, "Watch Soon can't be deleted.")
    con.execute("DELETE FROM lists WHERE id = ?", (row["id"],))


def set_membership(con, list_id, title_id, on) -> None:
    """Put a title on a list (keeping the first added_at) or take it off. Both are idempotent. The caller commits."""
    if on:
        con.execute("INSERT OR IGNORE INTO list_titles (list_id, title_id) VALUES (?, ?)", (list_id, title_id))
    else:
        con.execute("DELETE FROM list_titles WHERE list_id = ? AND title_id = ?", (list_id, title_id))


def clear_watch_soon(con, title_id) -> list:
    """Take a title off Watch Soon after a real-dated watch: ["Watch Soon"] when it was there, else []. Seasonal and
    custom lists never change on a watch. Runs on the caller's transaction; the caller commits."""
    if not has_lists(con):
        return []
    cur = con.execute("DELETE FROM list_titles WHERE title_id = ? AND list_id IN "
                      "(SELECT id FROM lists WHERE kind = 'watch_soon')", (title_id,))
    if not cur.rowcount:
        return []
    return [r[0] for r in con.execute("SELECT name FROM lists WHERE kind = 'watch_soon'")]
