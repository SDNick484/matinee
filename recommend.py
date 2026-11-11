"""Recommendation engine shared by the Flask app and the MCP server's direct-SQLite mode.

Standard library only: this module must never import Flask (or app.py), so mcp_server.py
can use it without the web stack installed.
"""
import datetime as dt
from typing import Any, Iterable, Mapping

MODES = ("forgotten_gems", "never_watched", "quick_picks", "genre_mood",
         "next_in_franchise", "start_franchise")

# US certifications, most to least restrictive-friendly. "NR" is an editor-only override
# value meaning "explicitly unrated"; it never satisfies a max_cert cap.
CERT_ORDER = ("G", "PG", "PG-13", "R", "NC-17")
CERT_VALUES = CERT_ORDER + ("NR",)

# Display order for copy formats (physical first, then digital). Mirrors app.FORMATS.
FORMAT_ORDER = ["4K UHD", "Blu-ray", "3D Blu-ray", "DVD", "VHS",
                "Digital 4K", "Digital HD", "Digital SD"]


def sorted_formats(fs: Iterable[str]) -> list:
    return sorted(set(fs), key=lambda f: FORMAT_ORDER.index(f) if f in FORMAT_ORDER else len(FORMAT_ORDER))


class ParamError(ValueError):
    """A request parameter is invalid (callers map this to HTTP 400)."""


def _clean(s):
    if s is None:
        return None
    s = str(s).strip()
    return s or None


def parse_params(m: Mapping[str, Any]) -> dict:
    """Normalise request args (strings) or MCP kwargs (typed values) into recommend() params."""
    mode = (m.get("mode") or "forgotten_gems").strip().lower()
    try:
        limit = max(1, min(int(m.get("limit") or 10), 50))
    except (ValueError, TypeError):
        limit = 10

    medium = (m.get("medium") or "").strip().lower()
    if medium not in ("physical", "digital"):
        medium = None

    min_rating = None
    if m.get("min_rating") not in (None, ""):
        try:
            min_rating = float(m.get("min_rating"))
        except (ValueError, TypeError):
            pass

    max_runtime = None
    if m.get("max_runtime") not in (None, ""):
        try:
            max_runtime = max(1, int(m.get("max_runtime")))
        except (ValueError, TypeError):
            pass

    max_cert = (_clean(m.get("max_cert")) or "").upper() or None
    if max_cert is not None and max_cert not in CERT_ORDER:
        raise ParamError("max_cert must be one of " + ", ".join(CERT_ORDER))

    out = {
        "mode": mode,
        "limit": limit,
        "genre": _clean(m.get("genre")),
        "medium": medium,
        "min_rating": min_rating,
        "max_runtime": max_runtime,
        "max_cert": max_cert,
        "random": str(m.get("random") or "").strip().lower() in ("1", "true", "yes"),
    }
    # `audience` is resolved against the database by recommend(); here it is only cleaned. The key is
    # present only when given, so the no-audience params are exactly what they were before people existed.
    audience = _clean(m.get("audience"))
    if audience is not None:
        out["audience"] = audience
    return out


# --- people and attendance (spec 2026-11-11-per-person §4.1, §5) ---------------

# One row per (title, person, watch date): who has seen what. A watch with watch_people rows counts for
# exactly those people; an unresolved watch counts for everyone, or for adults only when it is ten or more
# years old (bucket over_10_years, or dated before today minus ten years). attendance_of() is the same rule.
SEEN_CTE = """seen(title_id, person_id, watched_date) AS (
  SELECT w.title_id, wp.person_id, w.watched_date
    FROM watches w JOIN watch_people wp ON wp.watch_id = w.id
  UNION ALL
  SELECT w.title_id, p.id, w.watched_date
    FROM watches w CROSS JOIN people p
   WHERE NOT EXISTS (SELECT 1 FROM watch_people x WHERE x.watch_id = w.id)
     AND (p.kind = 'adult'
          OR NOT (COALESCE(w.date_bucket, '') = 'over_10_years'
                  OR (w.watched_date IS NOT NULL AND w.watched_date < date('now', '-10 years'))))
)"""

ATTENDANCE = ("recorded", "assumed_everyone", "assumed_adults")
AUDIENCE_KEYWORDS = {"everyone": None, "adults": "adult", "kids": "kid"}
_NO_PEOPLE = "No people are set up yet. Add them under People in the admin view."
_MAX_ROWID = 2 ** 63 - 1  # SQLite's largest integer; a bigger id cannot be bound, so it is simply unknown


def _field(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _ten_years_before(today: dt.date) -> str:
    """SQLite's date(today, '-10 years'): the same month and day ten years back, with Feb 29 rolling over to
    Mar 1 (SQLite normalises the impossible date forward rather than raising)."""
    try:
        return today.replace(year=today.year - 10).isoformat()
    except ValueError:
        return dt.date(today.year - 10, 3, 1).isoformat()


def attendance_of(watch_row, has_attendees: bool, today: "dt.date | None" = None) -> str:
    """'recorded', 'assumed_everyone' or 'assumed_adults' for one watch, by the same rule as SEEN_CTE.
    `watch_row` needs `watched_date` and `date_bucket` (a dict or sqlite3.Row). Dates compare as text, as
    SQLite compares them. `today` defaults to the UTC date, which is what SQLite's date('now') uses."""
    if has_attendees:
        return "recorded"
    cutoff = _ten_years_before(today or dt.datetime.now(dt.timezone.utc).date())
    day = _field(watch_row, "watched_date")
    if (_field(watch_row, "date_bucket") or "") == "over_10_years" or (day is not None and day < cutoff):
        return "assumed_adults"
    return "assumed_everyone"


def has_people(con) -> bool:
    """False for a database older than schema v9 (no `people` table) or one where nobody is set up yet."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'people'").fetchone() is None:
        return False
    return con.execute("SELECT 1 FROM people LIMIT 1").fetchone() is not None


def watch_attendance(con, watches):
    """{watch_id: {"attendance", "attendees"}} for watch rows (needing id, watched_date, date_bucket). A watch with
    watch_people rows lists exactly those people; an unresolved one lists who recommend.SEEN_CTE counts for it:
    every person, or every adult when attendance_of says assumed_adults. Active or not, in sort_order, id order."""
    people = con.execute("SELECT id, kind FROM people ORDER BY sort_order, id").fetchall()
    order = {p["id"]: i for i, p in enumerate(people)}
    recorded = {}
    ids = [w["id"] for w in watches]
    if ids:
        for r in con.execute(f"SELECT watch_id, person_id FROM watch_people WHERE watch_id IN ({','.join('?' * len(ids))})", ids):
            recorded.setdefault(r["watch_id"], []).append(r["person_id"])
    out = {}
    for w in watches:
        mine = recorded.get(w["id"])
        kind = attendance_of(w, bool(mine))
        if mine:
            who = sorted(mine, key=order.__getitem__)
        else:
            who = [p["id"] for p in people if kind == "assumed_everyone" or p["kind"] == "adult"]
        out[w["id"]] = {"attendance": kind, "attendees": who}
    return out


def title_seen_by(con, title_id):
    """Ids of everyone with a `seen` row for the title (recommend.SEEN_CTE), active or not, in people order."""
    return [r[0] for r in con.execute(
        f"""WITH {SEEN_CTE}
            SELECT DISTINCT p.id FROM seen s JOIN people p ON p.id = s.person_id
            WHERE s.title_id = ? ORDER BY p.sort_order, p.id""", (title_id,))]


def find_person(con, token):
    """The (id, name) row of the person a token names (an id, or a name in any case; active or not), or None."""
    token = str(token).strip()
    if token.isascii() and token.isdigit():  # str.isdigit alone accepts "²", which int() rejects
        n = int(token)
        return con.execute("SELECT id, name FROM people WHERE id = ?", (n,)).fetchone() if n <= _MAX_ROWID else None
    # `name` is COLLATE NOCASE, so this match ignores case
    return con.execute("SELECT id, name FROM people WHERE name = ?", (token,)).fetchone()


def unknown_person_message(con, token) -> str:
    everyone = [r[0] for r in con.execute("SELECT name FROM people ORDER BY sort_order, id")]
    return f"Unknown person: {token}. People: {', '.join(everyone)}."


def resolve_audience(con, raw: str) -> dict:
    """Resolve `everyone` / `adults` / `kids` (active people, in sort order) or a comma list of names or ids
    (active or not, in the order given, deduplicated) to {"label", "people": [{"id", "name"}]}."""
    tokens = [t.strip() for t in str(raw or "").split(",")]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise ParamError("audience is empty.")
    if not has_people(con):
        raise ParamError(_NO_PEOPLE)

    if len(tokens) == 1 and tokens[0].lower() in AUDIENCE_KEYWORDS:
        keyword = tokens[0].lower()
        kind = AUDIENCE_KEYWORDS[keyword]
        rows = con.execute(
            "SELECT id, name FROM people WHERE active = 1 AND (? IS NULL OR kind = ?) ORDER BY sort_order, id",
            (kind, kind)).fetchall()
        if not rows:
            raise ParamError(f"No {keyword} are set up." if kind else _NO_PEOPLE)
        return {"label": keyword, "people": [{"id": r[0], "name": r[1]} for r in rows]}

    people, seen_ids = [], set()
    for token in tokens:
        row = find_person(con, token)
        if row is None:
            raise ParamError(unknown_person_message(con, token))
        if row[0] not in seen_ids:
            seen_ids.add(row[0])
            people.append({"id": row[0], "name": row[1]})
    return {"label": ", ".join(p["name"] for p in people), "people": people}


def _in_ids(ids) -> str:
    """`IN (…)` over person ids. They come from the people table (ints), so they are inlined rather than bound,
    which keeps the positional `?` arguments of the shared filter fragments in order."""
    return "IN (" + ",".join(str(int(i)) for i in ids) + ")"


def _and_join(names) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _seen_by(con, title_ids) -> dict:
    """{title_id: [names]} of everyone with a `seen` row (active or not), in people order."""
    if not title_ids:
        return {}
    out = {tid: [] for tid in title_ids}
    rows = con.execute(f"""WITH {SEEN_CTE}
        SELECT DISTINCT s.title_id, p.name, p.sort_order, p.id FROM seen s JOIN people p ON p.id = s.person_id
        WHERE s.title_id {_in_ids(title_ids)}
        ORDER BY p.sort_order, p.id""").fetchall()
    for r in rows:
        out[r[0]].append(r[1])
    return out


FRANCHISE_MODES = ("next_in_franchise", "start_franchise")

# Pick order inside a collection (rn = 1 is the pick); undated releases sort last.
_PICK_ORDER = "t.release_date IS NULL, t.release_date, t.sort_title"

# `started`: every collection with at least one watch, on ANY title (even one with no copies left).
_STARTED_CTE = """WITH started AS (
          SELECT t.collection_tmdb_id, MAX(w.watched_date) AS last_watch
          FROM titles t JOIN watches w ON w.title_id = t.id
          WHERE t.collection_tmdb_id IS NOT NULL
          GROUP BY t.collection_tmdb_id
        )"""

# start_franchise's `ranked`, after whichever `started` applies (household or audience).
_START_RANKED = f""", ranked AS (
          SELECT t.*,
                 ROW_NUMBER() OVER (PARTITION BY t.collection_tmdb_id ORDER BY {_PICK_ORDER}) AS rn,
                 COUNT(*) OVER (PARTITION BY t.collection_tmdb_id) AS owned_n
          FROM titles t
          WHERE t.collection_tmdb_id IS NOT NULL
            AND t.collection_tmdb_id NOT IN (SELECT collection_tmdb_id FROM started)
            AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id = t.id)
        )"""

# next_in_franchise's `ranked`: owned titles of started collections that {unseen} keeps (household: no
# watch at all; audience: nobody in it has seen the title). Filled with str.format, so no other braces here.
_NEXT_RANKED = f""", ranked AS (
          SELECT t.*, s.last_watch,
                 ROW_NUMBER() OVER (PARTITION BY t.collection_tmdb_id ORDER BY {_PICK_ORDER}) AS rn
          FROM titles t JOIN started s ON s.collection_tmdb_id = t.collection_tmdb_id
          WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id = t.id)
            AND {{unseen}}
        )"""

_FRANCHISE_CTE = {
    # Owned, unwatched titles of started collections.
    "next_in_franchise": _STARTED_CTE + _NEXT_RANKED.format(
        unseen="NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id = t.id)"),
    # Owned titles of collections with no watch at all; owned_n counts the owned titles.
    "start_franchise": _STARTED_CTE + _START_RANKED,
}


def _started_cte(ids=None) -> str:
    """`started` for the household (ids None: today's text) or for audience `ids` (a collection is started
    when someone in the audience has seen any title in it). With ids it opens the WITH chain with `seen`."""
    if ids is None:
        return _STARTED_CTE
    return f"""WITH {SEEN_CTE}, started AS (
          SELECT t.collection_tmdb_id, MAX(s.watched_date) AS last_watch
          FROM titles t JOIN seen s ON s.title_id = t.id
          WHERE t.collection_tmdb_id IS NOT NULL AND s.person_id {_in_ids(ids)}
          GROUP BY t.collection_tmdb_id
        )"""


def _franchise_cte(mode, ids=None) -> str:
    if ids is None:
        return _FRANCHISE_CTE[mode]
    started = _started_cte(ids)
    if mode == "next_in_franchise":
        return started + _NEXT_RANKED.format(
            unseen=f"NOT EXISTS (SELECT 1 FROM seen x WHERE x.title_id = t.id AND x.person_id {_in_ids(ids)})")
    return started + _START_RANKED


_FRANCHISE_ORDER = {
    "next_in_franchise": "t.last_watch IS NULL, t.last_watch DESC, t.collection_name",
    "start_franchise": "COALESCE(user_rating, t.tmdb_vote, 0) DESC, t.collection_name",
}

_EMPTY_HINTS = {
    "next_in_franchise": "No series in progress. Try mode=start_franchise.",
    "start_franchise": "No unstarted series with 2+ owned films. Try mode=never_watched.",
}


_FILTERED_HINTS = {
    "next_in_franchise": "Nothing in progress passes these filters. Try removing a filter.",
    "start_franchise": "No unstarted series passes these filters. Try removing a filter.",
}


def _franchise_candidates(con, mode, ids=None) -> bool:
    """True when the mode had something to offer before the user filters (so an empty result
    means the filters emptied it): any started collection, or any unstarted one with 2+ owned."""
    if mode == "next_in_franchise":
        return con.execute(_started_cte(ids) + " SELECT 1 FROM started LIMIT 1").fetchone() is not None
    return con.execute(_franchise_cte(mode, ids) + " SELECT 1 FROM ranked WHERE rn = 1 AND owned_n >= 2 LIMIT 1").fetchone() is not None


def _next_reason(con, r, ids=None) -> str:
    """'Next after <latest earlier watched>' or, failing that, 'Before <earliest later watched> (watched)'.
    Neighbours are found by the same (release_date, sort_title) order that chose the pick; with audience
    `ids`, a neighbour is a title someone in the audience has seen."""
    key = "(COALESCE(x.release_date, '9999-99-99'), x.sort_title)"
    pick = (r["release_date"] or "9999-99-99", r["sort_title"])
    if ids is None:
        prefix, watched = "", "EXISTS (SELECT 1 FROM watches w WHERE w.title_id = x.id)"
    else:
        prefix = f"WITH {SEEN_CTE} "
        watched = f"EXISTS (SELECT 1 FROM seen s WHERE s.title_id = x.id AND s.person_id {_in_ids(ids)})"
    base = prefix + f"""SELECT x.title FROM titles x
               WHERE x.collection_tmdb_id = ? AND {watched}
                 AND {key} {{op}} (?, ?)
               ORDER BY COALESCE(x.release_date, '9999-99-99') {{dir}}, x.sort_title {{dir}} LIMIT 1"""
    args = (r["collection_tmdb_id"], *pick)
    prev = con.execute(base.format(op="<", dir="DESC"), args).fetchone()
    if prev:
        return f"{r['collection_name']} · Next after {prev['title']}"
    nxt = con.execute(base.format(op=">", dir="ASC"), args).fetchone()
    # The final fallback is unreachable from next_in_franchise (a started collection always has a
    # watched title, so one of the two neighbour queries finds it); kept as a safe default.
    return f"{r['collection_name']} · Before {nxt['title']} (watched)" if nxt else f"{r['collection_name']} · Next in the series"


def _filter_conditions(p: dict, mode: str):
    """User-facing filters (genre, medium, min_rating, max_runtime, max_cert) as WHERE
    fragments over alias `t`, shared by the plain modes and the franchise modes (which apply
    them to the already-chosen pick)."""
    where, args = [], []
    genre = p.get("genre")
    if genre:
        where.append("EXISTS (SELECT 1 FROM title_genres tg JOIN genres gn ON gn.id=tg.genre_id WHERE tg.title_id=t.id AND gn.name = ? COLLATE NOCASE)")
        args.append(genre)

    if p.get("medium"):
        where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind = ?)")
        args.append(p["medium"])

    if p.get("min_rating") is not None:
        where.append("COALESCE((SELECT rating FROM user_ratings r WHERE r.title_id=t.id), t.tmdb_vote, 0) >= ?")
        args.append(p["min_rating"])

    if p.get("max_runtime") is not None and mode != "quick_picks":
        where.append("t.runtime > 0 AND t.runtime <= ?")
        args.append(p["max_runtime"])

    if p.get("max_cert"):
        allowed = CERT_ORDER[:CERT_ORDER.index(p["max_cert"]) + 1]
        where.append(f"COALESCE(t.certification_override, t.certification) IN ({','.join('?' * len(allowed))})")
        args.extend(allowed)
    return where, args


def recommend(con, p: dict, today: "dt.date | None" = None) -> dict:
    """Run a recommendation query. `con.row_factory` must be sqlite3.Row.

    Results carry `poster_file` and `fetched_at` so the caller can build poster URLs;
    callers that don't need them drop them.
    """
    today = today or dt.date.today()
    mode = p["mode"]
    genre = p.get("genre")
    audience = resolve_audience(con, p["audience"]) if p.get("audience") else None
    ids = [x["id"] for x in audience["people"]] if audience else None

    owned = "EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"  # plain modes only; the franchise CTEs apply it themselves
    where = []
    args = []

    franchise = mode in FRANCHISE_MODES
    # Mode-specific conditions
    if franchise:
        order_by = _FRANCHISE_ORDER[mode]
    elif mode == "forgotten_gems":
        where.append("(SELECT rating FROM user_ratings r WHERE r.title_id=t.id) >= 8.0")
        if ids is None:
            where.append("COALESCE((SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id), '0000-00-00') < date('now', '-365 days')")
            order_by = "user_rating DESC, COALESCE(last_watched, '0000-00-00') ASC, t.sort_title ASC"
        else:
            where.append(f"COALESCE((SELECT MAX(s.watched_date) FROM seen s WHERE s.title_id=t.id AND s.person_id {_in_ids(ids)}), '0000-00-00') < date('now', '-365 days')")
            order_by = "user_rating DESC, COALESCE(aud_last_watched, '0000-00-00') ASC, t.sort_title ASC"
    elif mode == "never_watched":
        if ids is None:
            where.append("NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")
        else:
            where.append(f"NOT EXISTS (SELECT 1 FROM seen s WHERE s.title_id=t.id AND s.person_id {_in_ids(ids)})")
        order_by = "COALESCE(t.tmdb_vote, 0) DESC, t.year DESC, t.sort_title ASC"
    elif mode == "quick_picks":
        where.append("t.runtime > 0 AND t.runtime <= ?")
        args.append(p.get("max_runtime") or 95)
        order_by = "COALESCE((SELECT rating FROM user_ratings r WHERE r.title_id=t.id), t.tmdb_vote, 0) DESC, t.runtime ASC"
    else:  # genre_mood and unknown modes share the default ordering
        order_by = "COALESCE((SELECT rating FROM user_ratings r WHERE r.title_id=t.id), t.tmdb_vote, 0) DESC, t.sort_title ASC"

    f_where, f_args = _filter_conditions(p, mode)
    where += f_where
    args += f_args

    if p.get("random"):
        order_by = "RANDOM()"

    args.append(p["limit"])
    if franchise:
        # Filters go in the OUTER query, after rn = 1: a pick that fails a filter yields nothing
        # for its collection instead of falling through to a later entry.
        where = ["t.rn = 1"] + (["t.owned_n >= 2"] if mode == "start_franchise" else []) + where
    else:
        where = [owned] + where
    if ids is None:
        cte, aud_cols = (_FRANCHISE_CTE[mode] if franchise else ""), ""
    else:
        # The audience's own last watch and watch count (forgotten_gems uses them for its filter and reason).
        cte = _franchise_cte(mode, ids) if franchise else f"WITH {SEEN_CTE}"
        aud_cols = f""",
               (SELECT MAX(s.watched_date) FROM seen s WHERE s.title_id=t.id AND s.person_id {_in_ids(ids)}) AS aud_last_watched,
               (SELECT count(*) FROM seen s WHERE s.title_id=t.id AND s.person_id {_in_ids(ids)}) AS aud_watch_count"""
    rows = con.execute(f"""{cte}
        SELECT t.id, t.title, t.year, t.runtime, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file,
               t.fetched_at, t.tmdb_vote,
               COALESCE(t.certification_override, t.certification) AS certification,
               t.collection_tmdb_id, t.collection_name,
               t.release_date, t.sort_title,
               {"t.owned_n" if mode == "start_franchise" else "NULL"} AS owned_n,
               (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
               (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
               (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count,
               (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id=t.id) AS formats,
               (SELECT group_concat(g.name, ', ') FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=t.id) AS genres{aud_cols}
        FROM {'ranked' if franchise else 'titles'} t
        WHERE {' AND '.join(where)}
        ORDER BY {order_by}
        LIMIT ?
    """, args).fetchall()

    seen_by = _seen_by(con, [r["id"] for r in rows]) if audience else {}
    results = []
    for r in rows:
        genres_list = [g.strip() for g in (r["genres"] or "").split(",") if g.strip()]
        user_r = r["user_rating"]
        tmdb_v = r["tmdb_vote"]
        last_w = r["last_watched"]
        runtime = r["runtime"]
        # The reason speaks for the audience when there is one; the result's own fields stay household facts.
        reason_last_w = r["aud_last_watched"] if audience else last_w
        reason_count = r["aud_watch_count"] if audience else r["watch_count"]

        if mode == "forgotten_gems":
            if reason_last_w:
                try:
                    w_date = dt.date.fromisoformat(reason_last_w)
                    months = max(1, (today - w_date).days // 30)
                    reason = f"Rated {user_r:.1f} ★ · Not watched in {months} months"
                except (ValueError, TypeError):
                    reason = f"Rated {user_r:.1f} ★ · Not watched recently"
            elif reason_count > 0:
                reason = f"Rated {user_r:.1f} ★ · Historical watch (Date unknown)"
            else:
                reason = f"Rated {user_r:.1f} ★ · Never logged as watched"
        elif mode == "never_watched" and audience:
            new_to = "New to " + _and_join(x["name"] for x in audience["people"])
            reason = f"{new_to} · TMDB {tmdb_v:.1f} ★" if tmdb_v else new_to
        elif mode == "never_watched":
            reason = f"Unwatched backlog · TMDB {tmdb_v:.1f} ★" if tmdb_v else "Unwatched backlog"
        elif mode == "quick_picks":
            reason = f"{runtime} mins · Perfect quick watch" if runtime else "Quick watch"
        elif mode == "next_in_franchise":
            reason = _next_reason(con, r, ids)
        elif mode == "start_franchise":
            reason = f"Start the {r['collection_name']} · {r['owned_n']} owned"
        elif mode == "genre_mood":
            score_str = f"{user_r:.1f} ★" if user_r else (f"{tmdb_v:.1f} ★" if tmdb_v else "")
            genre_name = genre or (genres_list[0] if genres_list else "movie")
            reason = f"Top-rated {genre_name} pick" + (f" ({score_str})" if score_str else "")
        else:
            reason = "Curated collection pick"

        result = {
            "id": r["id"],
            "title": r["title"],
            "year": r["year"],
            "runtime": r["runtime"],
            "poster_file": r["poster_file"],
            "fetched_at": r["fetched_at"],
            "certification": r["certification"],
            "collection_tmdb_id": r["collection_tmdb_id"],
            "collection_name": r["collection_name"],
            "user_rating": user_r,
            "tmdb_vote": tmdb_v,
            "last_watched": last_w,
            "watch_count": r["watch_count"],
            "formats": sorted_formats((r["formats"] or "").split(",")) if r["formats"] else [],
            "genres": genres_list,
            "recommendation_reason": reason,
            "reason": reason,
        }
        if audience:
            result["seen_by"] = seen_by[r["id"]]
        results.append(result)

    out = {"mode": mode, "count": len(results)}
    if audience:
        out["audience"] = audience
    out["results"] = results
    if not results:
        hint = _EMPTY_HINTS.get(mode, "Nothing matched these filters. Try removing a filter or mode=genre_mood.")
        if franchise and _franchise_candidates(con, mode, ids):
            hint = _FILTERED_HINTS[mode]
        if p.get("max_cert"):
            hint += " Unrated films are hidden while max_cert is set."
        out["hint"] = hint
    return out
