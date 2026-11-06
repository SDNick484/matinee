"""Recommendation engine shared by the Flask app and the MCP server's direct-SQLite mode.

Standard library only: this module must never import Flask (or app.py), so mcp_server.py
can use it without the web stack installed.
"""
import datetime as dt
from typing import Any, Iterable, Mapping

MODES = ("forgotten_gems", "never_watched", "quick_picks", "genre_mood")

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


def recommend(con, p: dict, today: "dt.date | None" = None) -> dict:
    """Run a recommendation query. `con.row_factory` must be sqlite3.Row.

    Results carry `poster_file` and `fetched_at` so the caller can build poster URLs;
    callers that don't need them drop them.
    """
    today = today or dt.date.today()
    mode = p["mode"]
    genre = p.get("genre")

    where = ["EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"]
    args = []

    # Mode-specific conditions
    if mode == "forgotten_gems":
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

    # Additional filters
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

    if p.get("random"):
        order_by = "RANDOM()"

    args.append(p["limit"])
    rows = con.execute(f"""
        SELECT t.id, t.title, t.year, t.runtime, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file,
               t.fetched_at, t.tmdb_vote,
               COALESCE(t.certification_override, t.certification) AS certification,
               t.collection_tmdb_id, t.collection_name,
               (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
               (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
               (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count,
               (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id=t.id) AS formats,
               (SELECT group_concat(g.name, ', ') FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=t.id) AS genres
        FROM titles t
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
        hint = "Nothing matched these filters. Try removing a filter or mode=genre_mood."
        if p.get("max_cert"):
            hint += " Unrated films are hidden while max_cert is set."
        out["hint"] = hint
    return out
