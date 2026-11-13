"""The read side that app.py registers under both /api and /admin/api: serializers (copies, watches, title
detail), location paths, the poster-grid query and cards, facets, stats and recommendations. Every handler takes
`private`; a public request never gets watch notes or attendance (tests/test_privacy.py walks these functions).
Split out of app.py."""
import datetime as dt

from flask import abort, jsonify, request

from recommend import (FORMAT_ORDER, SEEN_CTE, ParamError, has_people, parse_params, recommend, resolve_audience,
                       sorted_formats, title_seen_by, watch_attendance)
from settings import IMAGE_DIR
from common import clean, words, make_err
from db import FORMATS, SERVICES, db
from images import image_name, image_url, poster_file_of
from wishlist import get_want, wishes_for_title


# ======================================================================= serializers
PRIVATE_COPY = ("added_by", "code_status", "purchased_on", "price_cents")
PRIVATE_WATCH = ("notes", "attendance", "attendees")  # family diary text and who was there: editors only; dates and counts stay public
TITLE_COLS = ("tmdb_id", "imdb_id", "title", "sort_title", "search_text", "year", "release_date", "runtime",
              "overview", "certification", "collection_tmdb_id", "collection_name", "tmdb_vote", "tmdb_votes",
              "poster_path", "backdrop_path", "poster_file", "backdrop_file", "fetched_at")


def get_locations(con):
    """Return sorted flat list of locations with path, depth, and copy_count."""
    rows = con.execute("SELECT id, parent_id, name, sort_order FROM locations ORDER BY sort_order ASC, name COLLATE NOCASE ASC").fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    children = {}
    for r in rows:
        pid = r["parent_id"]
        children.setdefault(pid, []).append(r["id"])

    counts = dict(con.execute("SELECT location_id, count(*) FROM copies WHERE location_id IS NOT NULL GROUP BY location_id").fetchall())

    def build_path(loc_id, visited=None):
        if visited is None:
            visited = set()
        if loc_id in visited:
            return by_id[loc_id]["name"]
        visited.add(loc_id)
        loc = by_id[loc_id]
        pid = loc["parent_id"]
        if pid and pid in by_id and pid not in visited:
            return f"{build_path(pid, visited)} › {loc['name']}"
        return loc["name"]

    result = []
    visited_c = set()

    def traverse(parent_id, depth):
        for cid in children.get(parent_id, []):
            if cid in visited_c:
                continue
            visited_c.add(cid)
            loc = by_id[cid]
            result.append({
                "id": loc["id"],
                "parent_id": loc["parent_id"],
                "name": loc["name"],
                "sort_order": loc["sort_order"],
                "path": build_path(loc["id"]),
                "depth": depth,
                "copy_count": counts.get(loc["id"], 0),
            })
            traverse(cid, depth + 1)

    traverse(None, 0)
    for r in rows:
        if r["id"] not in visited_c:
            visited_c.add(r["id"])
            result.append({
                "id": r["id"],
                "parent_id": r["parent_id"],
                "name": r["name"],
                "sort_order": r["sort_order"],
                "path": build_path(r["id"]),
                "depth": 0,
                "copy_count": counts.get(r["id"], 0),
            })
    return result


def get_location_path_map(con):
    """Return dict mapping location_id -> full breadcrumb path string."""
    return {l["id"]: l["path"] for l in get_locations(con)}


def get_descendant_location_ids(con, location_id):
    """Return set of location_id and all its direct and indirect child IDs."""
    rows = con.execute("SELECT id, parent_id FROM locations").fetchall()
    children = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r["id"])

    descendants = set()
    queue = [location_id]
    while queue:
        curr = queue.pop()
        descendants.add(curr)
        for child in children.get(curr, []):
            if child not in descendants:
                queue.append(child)
    return descendants


def copy_label(c):
    return f"{c['format']} ({c['service']})" if c.get("service") else c["format"]


def copy_out(r, private, loc_map=None):
    d = dict(r)
    if not private:
        for k in PRIVATE_COPY:
            d.pop(k, None)
    d["label"] = copy_label(d)
    loc_id = d.get("location_id")
    if loc_id:
        if loc_map is not None:
            d["location_path"] = loc_map.get(loc_id)
        else:
            try:
                con = db()
                loc_map = get_location_path_map(con)
                d["location_path"] = loc_map.get(loc_id)
            except Exception:
                d["location_path"] = None
    else:
        d["location_path"] = None
    return d


def watch_out(r, private):
    d = dict(r)
    if not private:
        for k in PRIVATE_WATCH:
            d.pop(k, None)
    return d


def title_detail(title_id, private):
    con = db()
    t = con.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not t:
        abort(make_err(404, "No such title"))
    d = {k: t[k] for k in t.keys() if k not in ("search_text", "sort_title")}
    d["genres"] = [r["name"] for r in con.execute(
        "SELECT g.name FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=? ORDER BY g.name",
        (title_id,))]
    loc_map = get_location_path_map(con)
    d["copies"] = [copy_out(r, private, loc_map) for r in con.execute(
        "SELECT * FROM copies WHERE title_id=? ORDER BY created_at, id", (title_id,))]
    d["owned"] = bool(d["copies"])
    d["added_on"] = min((c["created_at"] for c in d["copies"]), default=None)
    d["added_on"] = d["added_on"][:10] if d["added_on"] else None
    p780 = image_name(t["tmdb_id"], "p780")
    d["certification_tmdb"] = t["certification"]
    d["certification_override"] = t["certification_override"]
    d["certification"] = t["certification_override"] or t["certification"]
    d["custom_poster"] = bool(t["custom_poster_file"])
    d["poster_url"] = image_url(poster_file_of(t), t["fetched_at"])
    if d["custom_poster"]:
        d["poster_large_url"] = d["poster_url"]
    else:
        d["poster_large_url"] = image_url(p780, t["fetched_at"]) if (IMAGE_DIR / p780).exists() else d["poster_url"]
    d["backdrop_url"] = image_url(t["backdrop_file"], t["fetched_at"])
    d["tmdb_url"] = f"https://www.themoviedb.org/movie/{t['tmdb_id']}"
    rating_row = con.execute("SELECT rating FROM user_ratings WHERE title_id=?", (title_id,)).fetchone()
    d["user_rating"] = rating_row["rating"] if rating_row else None
    watches = con.execute("SELECT id, watched_date, notes, date_bucket, created_at FROM watches WHERE title_id=? ORDER BY watched_date DESC, id DESC",
                          (title_id,)).fetchall()
    if private:
        extra = watch_attendance(con, watches)
        d["watches"] = [watch_out({**dict(w), **extra[w["id"]]}, True) for w in watches]
        d["seen_by"] = title_seen_by(con, title_id)
        d["want"] = get_want(con, title_id)           # editors only, like attendance: never in a public response
        d["wishes"] = wishes_for_title(con, title_id)
    else:
        d["watches"] = [watch_out(w, False) for w in watches]
    d["last_watched"] = watches[0]["watched_date"] if watches else None
    d["watch_count"] = len(watches)
    omdb_row = con.execute("SELECT imdb_id, imdb_rating, imdb_votes, rotten_tomatoes_score, metacritic_score, fetched_at FROM omdb_ratings WHERE title_id=?", (title_id,)).fetchone()
    if omdb_row:
        d["omdb"] = {
            "imdb_id": omdb_row["imdb_id"],
            "imdb_rating": omdb_row["imdb_rating"],
            "imdb_votes": omdb_row["imdb_votes"],
            "rotten_tomatoes_score": omdb_row["rotten_tomatoes_score"],
            "metacritic_score": omdb_row["metacritic_score"],
            "fetched_at": omdb_row["fetched_at"],
        }
    else:
        d["omdb"] = None
    return d


# ============================================================================ browse
SORTS = {"added": "added DESC, t.id DESC",
         "title": "t.sort_title, t.year",
         "year": "t.year IS NULL, t.year DESC, t.sort_title",
         "rating": "user_rating IS NULL, user_rating DESC, t.sort_title",
         "unrated": "user_rating IS NOT NULL, t.sort_title",
         "last_watched": "last_watched IS NULL, last_watched DESC, t.sort_title",
         "least_watched": "last_watched IS NOT NULL, last_watched ASC, t.sort_title"}


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


QUALITY_MAP = {
    "4k": ["4K UHD", "Digital 4K"],
    "hd": ["Blu-ray", "3D Blu-ray", "Digital HD"],
    "sd": ["DVD", "VHS", "Digital SD"],
}


CARD_SELECT = """SELECT t.id, t.title, t.year, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file, t.fetched_at,
              (SELECT MIN(c.created_at) FROM copies c WHERE c.title_id=t.id) AS added,
              (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id=t.id) AS formats,
              EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical') AS has_disc,
              EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital') AS has_digital,
              (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
              (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
              (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count
            FROM titles t"""


def _card(r):
    """The grid card for one CARD_SELECT row."""
    return {"id": r["id"], "title": r["title"], "year": r["year"],
            "poster_url": image_url(r["poster_file"], r["fetched_at"]),
            "poster_file": r["poster_file"],
            "formats": sorted_formats((r["formats"] or "").split(",")) if r["formats"] else [],
            "has_disc": bool(r["has_disc"]),
            "has_digital": bool(r["has_digital"]),
            "has_both": bool(r["has_disc"] and r["has_digital"]),
            "added_on": (r["added"] or "")[:10] or None,
            "user_rating": r["user_rating"],
            "last_watched": r["last_watched"],
            "watch_count": r["watch_count"]}


def title_cards(con, ids):
    """Grid cards plus copies for the owned titles in `ids`, in the given order.
    Unknown and unowned ids are skipped; de-duplicating `ids` is the caller's job."""
    ids = list(ids)
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in con.execute(
        f"{CARD_SELECT} WHERE t.id IN ({q}) AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)", ids)}
    paths = get_location_path_map(con)
    copies = {}
    for cp in con.execute(
            f"SELECT title_id, kind, format, service, location_id FROM copies WHERE title_id IN ({q}) "
            "ORDER BY created_at, id", ids):
        copies.setdefault(cp["title_id"], []).append(
            {"kind": cp["kind"], "format": cp["format"], "service": cp["service"],
             "location_path": paths.get(cp["location_id"])})
    return [dict(_card(rows[i]), copies=copies.get(i, [])) for i in ids if i in rows]


# Query parameters that pick for specific people. Editors only: a public request that carries one gets a 400
# rather than a silently unfiltered list, which would look as if it had worked. The key alone is enough, even
# with an empty value, and the check runs before any other validation.
PEOPLE_PARAMS = ("audience", "unseen_by", "attendance")
PEOPLE_PARAMS_PUBLIC_ERROR = "Sign in to pick for specific people."


def people_params_refused(private):
    """The 400 a public request gets when it names a people parameter; None otherwise."""
    if not private and any(k in request.args for k in PEOPLE_PARAMS):
        return make_err(400, PEOPLE_PARAMS_PUBLIC_ERROR)
    return None


# The grid filter for copies whose digital code is still unredeemed. Editors only, like the code status itself: a
# public filter would reveal which titles have a code left. Only "unredeemed" is accepted.
CODE_PARAM_PUBLIC_ERROR = "Sign in to filter by digital code."
CODE_PARAM_VALUE_ERROR = "code must be unredeemed."


def code_param_refused(private):
    """The 400 for a public request that names `code` (even empty), or an editor's `code` other than unredeemed."""
    if "code" not in request.args:
        return None
    if not private:
        return make_err(400, CODE_PARAM_PUBLIC_ERROR)
    if request.args["code"].strip().lower() != "unredeemed":
        return make_err(400, CODE_PARAM_VALUE_ERROR)
    return None


def list_titles(private):
    refused = people_params_refused(private) or code_param_refused(private)
    if refused:
        return refused
    a = request.args
    where, args = ["EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"], []
    if "code" in a:   # code_param_refused() let only an editor's "unredeemed" through
        where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.code_status='unredeemed')")
    cte = ""
    for w in words(a.get("q")):
        where.append("t.search_text LIKE ? ESCAPE '\\'")
        args.append(f"%{like_escape(w)}%")

    medium_tokens = [m.strip().lower() for m in (a.get("medium") or "").split(",") if m.strip()]
    quality_tokens = [q.strip().lower() for q in (a.get("quality") or "").split(",") if q.strip()]
    genre_tokens = [g.strip() for g in (a.get("genre") or "").split(",") if g.strip()]

    legacy_kind = clean(a.get("kind"))
    legacy_format = clean(a.get("format"))
    legacy_service = clean(a.get("service"))
    legacy_both = a.get("both") == "1" or legacy_kind == "both"

    target_formats = []
    for q in quality_tokens:
        target_formats.extend(QUALITY_MAP.get(q, []))
    if legacy_format and legacy_format not in target_formats:
        target_formats.append(legacy_format)

    kinds = []
    if "disc" in medium_tokens:
        kinds.append("physical")
    if "streaming" in medium_tokens:
        kinds.append("digital")
    if legacy_kind and legacy_kind in ("physical", "digital") and legacy_kind not in kinds:
        kinds.append(legacy_kind)

    is_both = "both" in medium_tokens or legacy_both

    if quality_tokens and not target_formats:
        where.append("0=1")
    if medium_tokens and not is_both and not kinds:
        where.append("0=1")
    if legacy_kind and legacy_kind not in ("physical", "digital", "both") and not kinds and not is_both:
        where.append("0=1")

    if is_both:
        where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical')")
        where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')")
        if target_formats:
            fmt_q = ",".join("?" * len(target_formats))
            where.append(f"EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.format IN ({fmt_q}))")
            args += target_formats
        if legacy_service:
            where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.service=?)")
            args.append(legacy_service)
    else:
        copy_conds, copy_args = [], []
        if kinds:
            k_q = ",".join("?" * len(kinds))
            copy_conds.append(f"c.kind IN ({k_q})")
            copy_args += kinds
        if target_formats:
            fmt_q = ",".join("?" * len(target_formats))
            copy_conds.append(f"c.format IN ({fmt_q})")
            copy_args += target_formats
        if legacy_service:
            copy_conds.append("c.service = ?")
            copy_args.append(legacy_service)
        if copy_conds:
            where.append(f"EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND {' AND '.join(copy_conds)})")
            args += copy_args

    if genre_tokens:
        g_q = ",".join("?" * len(genre_tokens))
        where.append("EXISTS (SELECT 1 FROM title_genres tg JOIN genres gn ON gn.id=tg.genre_id "
                     f"WHERE tg.title_id=t.id AND gn.name IN ({g_q}) COLLATE NOCASE)")
        args += genre_tokens

    raw_rating = (a.get("rating") or "").strip().lower()
    if raw_rating in ("8+", "8", "8 "):
        where.append("EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id AND r.rating >= 8.0)")
    elif raw_rating == "unrated":
        where.append("NOT EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id)")

    watched_param = (a.get("watched") or "").strip().lower()
    if watched_param == "watched":
        where.append("EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")
    elif watched_param == "unwatched":
        where.append("NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")

    raw_loc = a.get("location_id") or a.get("location")
    if raw_loc:
        try:
            loc_id = int(raw_loc)
            descendant_ids = list(get_descendant_location_ids(db(), loc_id))
            if descendant_ids:
                loc_q = ",".join("?" * len(descendant_ids))
                where.append(f"EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.location_id IN ({loc_q}))")
                args += descendant_ids
            else:
                where.append("0=1")
        except (ValueError, TypeError):
            pass

    if private:  # people filters are editor-only (people_params_refused already answered a public request)
        if "unseen_by" in a:
            if not a["unseen_by"].strip():
                return make_err(400, "unseen_by is empty.")
            try:
                audience = resolve_audience(db(), a["unseen_by"])
            except ParamError as e:
                return make_err(400, str(e))
            id_q = ",".join("?" * len(audience["people"]))
            where.append(f"NOT EXISTS (SELECT 1 FROM seen s WHERE s.title_id=t.id AND s.person_id IN ({id_q}))")
            args += [x["id"] for x in audience["people"]]
            cte = f"WITH {SEEN_CTE} "
        if "attendance" in a:
            if a["attendance"] != "unknown":
                return make_err(400, "attendance must be unknown.")
            if has_people(db()):
                where.append("EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id AND NOT EXISTS "
                             "(SELECT 1 FROM watch_people x WHERE x.watch_id=w.id))")
            else:
                where.append("0=1")

    sort = a.get("sort") if a.get("sort") in SORTS else "title"
    rows = db().execute(
        f"""{cte}{CARD_SELECT} WHERE {' AND '.join(where)} ORDER BY {SORTS[sort]}""", args).fetchall()
    titles = [_card(r) for r in rows]
    return jsonify(titles=titles, count=len(titles), sort=sort)



def get_title(title_id, private):
    return jsonify(title_detail(title_id, private))


def facets(private):
    con = db()
    formats = sorted_formats(r[0] for r in con.execute("SELECT DISTINCT format FROM copies"))
    kinds = [k for k in FORMATS if con.execute("SELECT 1 FROM copies WHERE kind=? LIMIT 1", (k,)).fetchone()]
    has_both = bool(con.execute(
        """SELECT 1 FROM copies c1 JOIN copies c2 ON c1.title_id=c2.title_id
           WHERE c1.kind='physical' AND c2.kind='digital' LIMIT 1""").fetchone())
    present = {r[0] for r in con.execute("SELECT DISTINCT service FROM copies WHERE service IS NOT NULL")}
    services = [s for s in SERVICES if s in present] + sorted(present - set(SERVICES))
    genres = [r[0] for r in con.execute(
        """SELECT DISTINCT gn.name FROM genres gn JOIN title_genres tg ON tg.genre_id=gn.id
           WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=tg.title_id) ORDER BY gn.name""")]
    locations = get_locations(con)
    return jsonify(kinds=kinds, formats=formats, services=services, genres=genres, has_both=has_both, locations=locations)


def api_locations(private):
    con = db()
    locs = get_locations(con)
    by_id = {l["id"]: dict(l, children=[]) for l in locs}
    tree = []
    for l in locs:
        node = by_id[l["id"]]
        if l["parent_id"] and l["parent_id"] in by_id:
            by_id[l["parent_id"]]["children"].append(node)
        else:
            tree.append(node)
    return jsonify(locations=locs, tree=tree)


def collection_stats(private):
    con = db()
    titles_count = con.execute("SELECT count(DISTINCT title_id) FROM copies").fetchone()[0]
    physical_count = con.execute("SELECT count(*) FROM copies WHERE kind='physical'").fetchone()[0]
    digital_count = con.execute("SELECT count(*) FROM copies WHERE kind='digital'").fetchone()[0]
    dual_count = con.execute(
        """SELECT count(*) FROM titles t
           WHERE EXISTS (SELECT 1 FROM copies c1 WHERE c1.title_id=t.id AND c1.kind='physical')
             AND EXISTS (SELECT 1 FROM copies c2 WHERE c2.title_id=t.id AND c2.kind='digital')"""
    ).fetchone()[0]

    qualities = []
    for q_name, fmts in [("4K", QUALITY_MAP["4k"]), ("HD", QUALITY_MAP["hd"]), ("SD", QUALITY_MAP["sd"])]:
        q_placeholders = ",".join("?" * len(fmts))
        cnt = con.execute(
            f"SELECT count(DISTINCT title_id) FROM copies WHERE format IN ({q_placeholders})", fmts
        ).fetchone()[0]
        pct = round((cnt / titles_count * 100), 1) if titles_count else 0.0
        qualities.append({"quality": q_name, "count": cnt, "percent": pct})

    formats = []
    for fmt in FORMAT_ORDER:
        cnt = con.execute("SELECT count(*) FROM copies WHERE format=?", (fmt,)).fetchone()[0]
        k = "physical" if fmt in FORMATS["physical"] else "digital"
        formats.append({"format": fmt, "kind": k, "count": cnt})

    genres = [
        {"genre": r["genre"], "count": r["count"]}
        for r in con.execute(
            """SELECT gn.name AS genre, count(DISTINCT tg.title_id) AS count
               FROM genres gn
               JOIN title_genres tg ON tg.genre_id=gn.id
               JOIN copies c ON c.title_id=tg.title_id
               GROUP BY gn.id, gn.name
               ORDER BY count DESC, gn.name ASC"""
        ).fetchall()
    ]

    services = [
        {"service": r["service"], "count": r["count"]}
        for r in con.execute(
            """SELECT service, count(*) AS count
               FROM copies
               WHERE kind='digital' AND service IS NOT NULL
               GROUP BY service
               ORDER BY count DESC, service ASC"""
        ).fetchall()
    ]

    now = dt.date.today()
    year_start = f"{now.year:04d}-01-01"
    month_start = f"{now.year:04d}-{now.month:02d}-01"
    total_watched_titles = con.execute("SELECT count(DISTINCT title_id) FROM watches").fetchone()[0]
    watched_pct = round((total_watched_titles / titles_count * 100), 1) if titles_count else 0.0
    watched_year = con.execute("SELECT count(*) FROM watches WHERE watched_date >= ?", (year_start,)).fetchone()[0]
    watched_month = con.execute("SELECT count(*) FROM watches WHERE watched_date >= ?", (month_start,)).fetchone()[0]
    total_watches = con.execute("SELECT count(*) FROM watches").fetchone()[0]
    watched_stats = {
        "total_watched_titles": total_watched_titles,
        "watched_percent": watched_pct,
        "watched_this_year": watched_year,
        "watched_this_month": watched_month,
        "total_watch_events": total_watches,
    }

    rated_titles = con.execute("SELECT count(*) FROM user_ratings").fetchone()[0]
    rated_pct = round((rated_titles / titles_count * 100), 1) if titles_count else 0.0
    avg_rating = con.execute("SELECT avg(rating) FROM user_ratings").fetchone()[0]
    high_rated = con.execute("SELECT count(*) FROM user_ratings WHERE rating >= 8.0").fetchone()[0]
    rating_stats = {
        "rated_titles_count": rated_titles,
        "rated_percent": rated_pct,
        "average_rating": round(avg_rating, 1) if avg_rating is not None else 0.0,
        "high_rated_count": high_rated,
    }

    extra = {}
    if private:   # editor-only: how many physical copies still have a digital code to redeem
        extra["unredeemed_codes"] = con.execute(
            "SELECT count(*) FROM copies WHERE code_status='unredeemed'").fetchone()[0]
    return jsonify(
        **extra,
        overview={
            "titles_count": titles_count,
            "physical_count": physical_count,
            "digital_count": digital_count,
            "dual_count": dual_count,
        },
        qualities=qualities,
        formats=formats,
        genres=genres,
        services=services,
        watched_stats=watched_stats,
        rating_stats=rating_stats,
    )


def recommendations(private=False):
    refused = people_params_refused(private)
    if refused:
        return refused
    if "audience" in request.args and not request.args["audience"].strip():
        return make_err(400, "audience is empty.")
    try:
        p = parse_params(request.args)
        out = recommend(db(), p)  # resolves `audience` against the people table, so it can raise too
    except ParamError as e:
        return make_err(400, str(e))
    for r in out["results"]:
        r["poster_url"] = image_url(r["poster_file"], r.pop("fetched_at"))
    return jsonify(**out)
