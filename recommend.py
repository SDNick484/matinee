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

    return {
        "mode": mode,
        "limit": limit,
        "genre": _clean(m.get("genre")),
        "medium": medium,
        "min_rating": min_rating,
        "max_runtime": max_runtime,
        "max_cert": max_cert,
        "random": str(m.get("random") or "").strip().lower() in ("1", "true", "yes"),
    }


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

_FRANCHISE_CTE = {
    # Owned, unwatched titles of started collections.
    "next_in_franchise": _STARTED_CTE + f""", ranked AS (
          SELECT t.*, s.last_watch,
                 ROW_NUMBER() OVER (PARTITION BY t.collection_tmdb_id ORDER BY {_PICK_ORDER}) AS rn
          FROM titles t JOIN started s ON s.collection_tmdb_id = t.collection_tmdb_id
          WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id = t.id)
            AND NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id = t.id)
        )""",
    # Owned titles of collections with no watch at all; owned_n counts the owned titles.
    "start_franchise": _STARTED_CTE + f""", ranked AS (
          SELECT t.*,
                 ROW_NUMBER() OVER (PARTITION BY t.collection_tmdb_id ORDER BY {_PICK_ORDER}) AS rn,
                 COUNT(*) OVER (PARTITION BY t.collection_tmdb_id) AS owned_n
          FROM titles t
          WHERE t.collection_tmdb_id IS NOT NULL
            AND t.collection_tmdb_id NOT IN (SELECT collection_tmdb_id FROM started)
            AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id = t.id)
        )""",
}

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


def _franchise_candidates(con, mode) -> bool:
    """True when the mode had something to offer before the user filters (so an empty result
    means the filters emptied it): any started collection, or any unstarted one with 2+ owned."""
    if mode == "next_in_franchise":
        return con.execute(_STARTED_CTE + " SELECT 1 FROM started LIMIT 1").fetchone() is not None
    return con.execute(_FRANCHISE_CTE[mode] + " SELECT 1 FROM ranked WHERE rn = 1 AND owned_n >= 2 LIMIT 1").fetchone() is not None


def _next_reason(con, r) -> str:
    """'Next after <latest earlier watched>' or, failing that, 'Before <earliest later watched> (watched)'.
    Neighbours are found by the same (release_date, sort_title) order that chose the pick."""
    key = "(COALESCE(x.release_date, '9999-99-99'), x.sort_title)"
    pick = (r["release_date"] or "9999-99-99", r["sort_title"])
    base = f"""SELECT x.title FROM titles x
               WHERE x.collection_tmdb_id = ? AND EXISTS (SELECT 1 FROM watches w WHERE w.title_id = x.id)
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

    owned = "EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"  # plain modes only; the franchise CTEs apply it themselves
    where = []
    args = []

    franchise = mode in FRANCHISE_MODES
    # Mode-specific conditions
    if franchise:
        order_by = _FRANCHISE_ORDER[mode]
    elif mode == "forgotten_gems":
        where.append("(SELECT rating FROM user_ratings r WHERE r.title_id=t.id) >= 8.0")
        where.append("COALESCE((SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id), '0000-00-00') < date('now', '-365 days')")
        order_by = "user_rating DESC, COALESCE(last_watched, '0000-00-00') ASC, t.sort_title ASC"
    elif mode == "never_watched":
        where.append("NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")
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
    rows = con.execute(f"""{_FRANCHISE_CTE[mode] if franchise else ""}
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
               (SELECT group_concat(g.name, ', ') FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=t.id) AS genres
        FROM {'ranked' if franchise else 'titles'} t
        WHERE {' AND '.join(where)}
        ORDER BY {order_by}
        LIMIT ?
    """, args).fetchall()

    results = []
    for r in rows:
        genres_list = [g.strip() for g in (r["genres"] or "").split(",") if g.strip()]
        user_r = r["user_rating"]
        tmdb_v = r["tmdb_vote"]
        last_w = r["last_watched"]
        runtime = r["runtime"]

        if mode == "forgotten_gems":
            if last_w:
                try:
                    w_date = dt.date.fromisoformat(last_w)
                    months = max(1, (today - w_date).days // 30)
                    reason = f"Rated {user_r:.1f} ★ · Not watched in {months} months"
                except (ValueError, TypeError):
                    reason = f"Rated {user_r:.1f} ★ · Not watched recently"
            elif r["watch_count"] > 0:
                reason = f"Rated {user_r:.1f} ★ · Historical watch (Date unknown)"
            else:
                reason = f"Rated {user_r:.1f} ★ · Never logged as watched"
        elif mode == "never_watched":
            reason = f"Unwatched backlog · TMDB {tmdb_v:.1f} ★" if tmdb_v else "Unwatched backlog"
        elif mode == "quick_picks":
            reason = f"{runtime} mins · Perfect quick watch" if runtime else "Quick watch"
        elif mode == "next_in_franchise":
            reason = _next_reason(con, r)
        elif mode == "start_franchise":
            reason = f"Start the {r['collection_name']} · {r['owned_n']} owned"
        elif mode == "genre_mood":
            score_str = f"{user_r:.1f} ★" if user_r else (f"{tmdb_v:.1f} ★" if tmdb_v else "")
            genre_name = genre or (genres_list[0] if genres_list else "movie")
            reason = f"Top-rated {genre_name} pick" + (f" ({score_str})" if score_str else "")
        else:
            reason = "Curated collection pick"

        results.append({
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
        })

    out = {"mode": mode, "count": len(results), "results": results}
    if not results:
        hint = _EMPTY_HINTS.get(mode, "Nothing matched these filters. Try removing a filter or mode=genre_mood.")
        if franchise and _franchise_candidates(con, mode):
            hint = _FILTERED_HINTS[mode]
        if p.get("max_cert"):
            hint += " Unrated films are hidden while max_cert is set."
        out["hint"] = hint
    return out
