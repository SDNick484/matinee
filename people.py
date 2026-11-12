"""People and attendance (editor-only data): field validation, the people list, attendees by id or name,
who a new watch records by default, and the watch response. The /admin/api/people routes and the watch routes
are in app.py. Split out of app.py."""
from flask import abort

from recommend import attendance_of, find_person, unknown_person_message, watch_attendance
from common import clean, make_err


# ----------------------------------------------------------------------------- people (editor-only)
PERSON_KINDS = ("adult", "kid")
PERSON_COLORS = ("green", "blue", "red", "pink", "purple")  # --p-user1 .. --p-user5 in the CSS
MAX_ACTIVE_PEOPLE = 8
RESERVED_PERSON_NAMES = ("everyone", "adults", "kids")  # words the audience grammar uses


def person_out(row):
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "color": row["color"],
            "sort_order": row["sort_order"], "active": bool(row["active"]), "watch_count": row["watch_count"]}


PERSON_SELECT = """SELECT p.*, (SELECT count(*) FROM watch_people wp WHERE wp.person_id = p.id) AS watch_count
                   FROM people p"""


def list_people(con, active_only=False):
    where = " WHERE p.active = 1" if active_only else ""
    return [person_out(r) for r in con.execute(f"{PERSON_SELECT}{where} ORDER BY p.sort_order, p.id")]


def get_person(con, person_id):
    row = con.execute(f"{PERSON_SELECT} WHERE p.id = ?", (person_id,)).fetchone()
    return person_out(row) if row else None


def person_name_arg(con, raw, person_id=None):
    # Only a JSON string is a name: clean() would store ["Midge"] or 42 as their str() text.
    name = clean(raw) if isinstance(raw, str) else None
    if not name or len(name) > 40:
        abort(make_err(400, "A name is required (up to 40 characters)."))
    if name.lower() in RESERVED_PERSON_NAMES or "," in name or name.isdigit():
        abort(make_err(400, "That name can't be used. Pick a name without commas that isn't everyone, adults or kids."))
    other = con.execute("SELECT id, name FROM people WHERE name = ? AND id IS NOT ?", (name, person_id)).fetchone()
    if other:  # the column is COLLATE NOCASE, so this is case-insensitive
        abort(make_err(400, f"A person named {other['name']} already exists."))
    return name


def person_kind_arg(raw):
    if raw not in PERSON_KINDS:
        abort(make_err(400, "kind must be adult or kid."))
    return raw


def person_color_arg(raw):
    if raw not in PERSON_COLORS:
        abort(make_err(400, f"color must be one of {', '.join(PERSON_COLORS)}."))
    return raw


def person_sort_arg(raw):
    if isinstance(raw, bool) or not isinstance(raw, int):
        abort(make_err(400, "sort_order must be a whole number."))
    return raw


def check_active_room(con, person_id=None):
    """Refuse when making one more person active would pass MAX_ACTIVE_PEOPLE."""
    n = con.execute("SELECT count(*) FROM people WHERE active = 1 AND id IS NOT ?", (person_id,)).fetchone()[0]
    if n >= MAX_ACTIVE_PEOPLE:
        abort(make_err(400, f"At most {MAX_ACTIVE_PEOPLE} active people."))


def resolve_people(con, items):
    """Person ids or names (any case, active or not) -> ids, in request order, without duplicates. 400 on an
    unknown person or an empty list."""
    if not isinstance(items, list):
        abort(make_err(400, "attendees must be a list of person ids or names."))
    if not items:
        abort(make_err(400, "A watch needs at least one person."))
    ids = []
    for item in items:
        token = item if isinstance(item, (int, str)) and not isinstance(item, bool) else repr(item)
        row = find_person(con, token)
        if row is None:
            abort(make_err(400, unknown_person_message(con, item)))
        if row["id"] not in ids:
            ids.append(row["id"])
    return ids


def default_attendees(con, watched_date, date_bucket):
    """Who a log without `attendees` records: every active person, or only the active adults for a watch more than
    ten years old: the "10+ Years" bucket (ruling R21) or a date before today minus ten years (decision D2). That is
    recommend.attendance_of, the rule an old unrecorded watch is read with. With nobody to record (no people, or no
    active adult for an old watch) it is no rows, so the watch reads as assumed, as before people existed."""
    active = list_people(con, active_only=True)
    old = attendance_of({"watched_date": watched_date, "date_bucket": date_bucket}, False) == "assumed_adults"
    return [p["id"] for p in active if not old or p["kind"] == "adult"]


def set_attendees(con, watch_id, person_ids):
    """Replace a watch's attendance. The caller commits."""
    con.execute("DELETE FROM watch_people WHERE watch_id = ?", (watch_id,))
    con.executemany("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)",
                    [(watch_id, pid) for pid in person_ids])


def watch_response(con, watch_id, title_id):
    w = con.execute("SELECT id, title_id, watched_date, notes, date_bucket FROM watches WHERE id = ?",
                    (watch_id,)).fetchone()
    return {"id": w["id"], "title_id": title_id, "watched_date": w["watched_date"], "notes": w["notes"],
            "date_bucket": w["date_bucket"], **watch_attendance(con, [w])[watch_id]}
