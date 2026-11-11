#!/usr/bin/env python3
"""Matinee Model Context Protocol (MCP) Server.
Exposes read and write tools, resources, and prompts for the Matinee movie collection.

Can operate in:
1. REST Mode: Connects to a running Matinee instance via MATINEE_URL and MATINEE_API_KEY (or, for reads only,
   MATINEE_READ_KEY; with neither it reads the public /api, which has no watch notes).
2. Direct SQLite Mode: Accelerates read operations directly against MATINEE_DB_PATH.
"""
import inspect
import json
import logging
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        FastMCP = None


def _fold(s: str) -> str:
    """Copy of app.fold: strip accents, lower-case, trim."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower().strip()


def _words(s: str) -> List[str]:
    """Copy of app.words ('WALL·E' -> ['wall', 'e']); kept local so this module never imports app."""
    return re.sub(r"[^\w]+", " ", _fold(s)).split()


def _like_escape(s: str) -> str:
    """Copy of app.like_escape; use with ESCAPE '\\'."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _location_paths(con) -> Dict[int, str]:
    """Map location id -> 'Parent › Child' breadcrumb (cycle-safe)."""
    by_id = {r["id"]: dict(r) for r in con.execute("SELECT id, parent_id, name FROM locations").fetchall()}

    def build_path(loc_id, visited=None):
        if visited is None:
            visited = set()
        if loc_id in visited or loc_id not in by_id:
            return by_id.get(loc_id, {}).get("name", "")
        visited.add(loc_id)
        loc = by_id[loc_id]
        pid = loc["parent_id"]
        if pid and pid in by_id and pid not in visited:
            parent_path = build_path(pid, visited)
            return f"{parent_path} › {loc['name']}" if parent_path else loc["name"]
        return loc["name"]

    return {i: build_path(i) for i in by_id}


# (readOnlyHint, destructiveHint, idempotentHint, openWorldHint) per tool; the reasons are in
# docs/superpowers/specs/2026-11-11-mcp-tiered-writes-design.md §5.
TOOL_HINTS: Dict[str, Tuple[bool, bool, bool, bool]] = {
    "search_collection": (True, False, True, False),
    "get_movie": (True, False, True, False),
    "where_is": (True, False, True, False),
    "get_recommendations": (True, False, True, False),
    "get_collection_stats": (True, False, True, False),
    "list_people": (True, False, True, False),
    "search_tmdb": (True, False, True, True),   # reads TMDB
    "rate_movie": (False, True, True, False),   # overwrites or clears the previous rating
    "log_watch": (False, False, False, False),  # appends a watch row each call
    "add_movie": (False, False, False, True),   # inserts a copy each call, may fetch TMDB
}


def _annotation_kwargs(server_cls, name: str) -> Dict[str, Any]:
    """Tool annotations for `name`, only when this mcp release supports them on tool()."""
    if server_cls is None or name not in TOOL_HINTS:
        return {}
    try:
        from mcp.types import ToolAnnotations
        if "annotations" not in inspect.signature(server_cls.tool).parameters:
            return {}
    except (ImportError, TypeError, ValueError):
        return {}
    read_only, destructive, idempotent, open_world = TOOL_HINTS[name]
    return {"annotations": ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                                           idempotentHint=idempotent, openWorldHint=open_world)}


def _readonly_kwargs(server_cls) -> Dict[str, Any]:
    """Read-only tool annotations (kept for callers that predate TOOL_HINTS; same as get_recommendations')."""
    return _annotation_kwargs(server_cls, "get_recommendations")


try:
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    try:
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:
        ToolError = Exception

log = logging.getLogger("matinee.mcp")


class MatineeError(RuntimeError, ToolError):
    """Every Matinee failure. ToolError text reaches the model; RuntimeError keeps old callers working."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


MATINEE_URL = os.environ.get("MATINEE_URL", "http://127.0.0.1:8771").rstrip("/")
MATINEE_API_KEY = os.environ.get("MATINEE_API_KEY", "").strip()
MATINEE_READ_KEY = os.environ.get("MATINEE_READ_KEY", "").strip()
MATINEE_DB_PATH = os.environ.get("MATINEE_DB_PATH", "").strip()


TOO_OLD_FOR_PEOPLE = "This Matinee server is too old to pick for specific people. Update Matinee."


class MatineeClient:
    """Client for Matinee supporting direct SQLite reads and REST API mutations."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
        key_name: Optional[str] = None,
    ):
        self.api_url = (api_url or MATINEE_URL).rstrip("/")
        if api_key is not None:
            self.api_key, default_name = api_key, "MATINEE_API_KEY"
        elif MATINEE_API_KEY or not MATINEE_READ_KEY:
            self.api_key, default_name = MATINEE_API_KEY, "MATINEE_API_KEY"
        else:  # reads only: /admin/api with notes; the write tools get Matinee's 403
            self.api_key, default_name = MATINEE_READ_KEY, "MATINEE_READ_KEY"
        self.key_name = key_name or default_name  # named in the 401/403 message, never the key itself
        self.db_path = db_path if db_path is not None else MATINEE_DB_PATH

    def _get_db(self) -> Optional[sqlite3.Connection]:
        if not self.db_path:
            return None
        p = Path(self.db_path)
        if not p.is_file():
            return None
        try:
            uri = f"{p.resolve().as_uri()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout = 5000")
            return con
        except Exception:
            return None

    @property
    def _read_prefix(self) -> str:
        return "/admin/api" if self.api_key else "/api"

    def _api_request(
        self, method: str, path: str, data: Optional[Dict[str, Any]] = None
    ) -> Any:
        url = f"{self.api_url}{path}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "matinee-mcp/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {"ok": True}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:2000]
            log.warning("Matinee %s %s -> HTTP %s", method, path.split("?")[0], e.code)
            if e.code in (401, 403):
                raise MatineeError(
                    f"Matinee rejected the service's key (HTTP {e.code}). Check {self.key_name} on the Matinee host."
                ) from None
            detail = ""
            try:
                err_json = json.loads(err_body)
                if isinstance(err_json, dict):
                    detail = str(err_json.get("error") or err_json.get("message") or "")[:200]
            except ValueError:
                pass
            raise MatineeError(
                f"Matinee API error ({e.code})" + (f": {detail}" if detail else ""), status=e.code
            ) from None
        except Exception as e:
            log.warning("Matinee %s %s failed: %s", method, path.split("?")[0], type(e).__name__)
            raise MatineeError("Matinee is unreachable right now. Try again in a minute.") from None

    # ------------------------------------------------------------------------ People
    def _people_rows(self) -> List[Dict[str, Any]]:
        """REST: the people list as the server returns it. A 404 means the server predates people."""
        try:
            res = self._api_request("GET", f"{self._read_prefix}/people")
        except MatineeError as e:
            if e.status == 404:
                raise MatineeError(TOO_OLD_FOR_PEOPLE, status=404) from None
            raise
        return res.get("people", []) if isinstance(res, dict) else []

    def _require_people_api(self) -> None:
        """REST: refuse before sending when the server predates people, since it would ignore `audience` and
        `unseen_by` and return an unfiltered list. Public mode (no key) is not probed: /api/people does not
        exist, and the server's own 400 ("Sign in to pick for specific people.") is the right answer there."""
        if self.api_key:
            self._people_rows()

    def list_people(self) -> List[Dict[str, Any]]:
        """The family members Matinee knows: id, name, kind (adult/kid), color, active."""
        keys = ("id", "name", "kind", "color", "active")
        con = self._get_db()
        if con is not None:
            try:
                if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='people'").fetchone() is None:
                    return []  # a database older than schema v9
                return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "color": r["color"],
                         "active": bool(r["active"])}
                        for r in con.execute("SELECT id, name, kind, color, active FROM people ORDER BY sort_order, id")]
            finally:
                con.close()
        if not self.api_key:
            raise MatineeError("People are only available with a Matinee key (MATINEE_API_KEY or MATINEE_READ_KEY).")
        return [{k: p.get(k) for k in keys} for p in self._people_rows()]

    def _name_people(self, movie: Dict[str, Any]) -> Dict[str, Any]:
        """REST get_movie: turn the server's person ids into names and add `not_seen_by`. A response without
        `seen_by` (public /api, or a server that predates people) is returned as it came; nothing is invented."""
        if not isinstance(movie, dict) or "seen_by" not in movie:
            return movie
        people = self._people_rows()
        names = {p["id"]: p["name"] for p in people}
        seen_ids = set(movie.get("seen_by") or [])
        out = dict(movie)
        out["seen_by"] = [names[i] for i in movie.get("seen_by") or [] if i in names]
        out["not_seen_by"] = [p["name"] for p in people if p.get("active") and p["id"] not in seen_ids]
        out["watches"] = [
            {**w, "attendees": [names[i] for i in w.get("attendees") or [] if i in names]}
            if "attendees" in w else w
            for w in movie.get("watches") or []
        ]
        return out

    # ------------------------------------------------------------------------ Reads
    def search_collection(
        self,
        query: str = "",
        genre: Optional[str] = None,
        medium: Optional[str] = None,
        format: Optional[str] = None,
        watched: Optional[str] = None,
        min_rating: Optional[float] = None,
        limit: int = 20,
        unseen_by: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search collection by keyword, genre, medium, format, watched status, rating, or who has not seen it."""
        con = self._get_db()
        if con is not None:
            try:
                where = ["EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"]
                args = []
                cte = ""
                if unseen_by is not None:
                    import recommend
                    if not unseen_by.strip():
                        raise MatineeError("unseen_by is empty.")
                    who = recommend.resolve_audience(con, unseen_by)["people"]
                    where.append("NOT EXISTS (SELECT 1 FROM seen s WHERE s.title_id=t.id AND s.person_id IN (%s))"
                                 % ",".join("?" * len(who)))
                    args += [x["id"] for x in who]
                    cte = f"WITH {recommend.SEEN_CTE} "

                for w in _words(query or ""):
                    where.append("t.search_text LIKE ? ESCAPE '\\'")
                    args.append(f"%{_like_escape(w)}%")

                if genre:
                    where.append(
                        "EXISTS (SELECT 1 FROM title_genres tg JOIN genres g ON g.id=tg.genre_id "
                        "WHERE tg.title_id=t.id AND g.name = ? COLLATE NOCASE)"
                    )
                    args.append(genre)

                if medium:
                    where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind = ?)")
                    args.append(medium)

                if format:
                    where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.format = ?)")
                    args.append(format)

                if watched == "watched":
                    where.append("EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")
                elif watched == "unwatched":
                    where.append("NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")

                if min_rating is not None:
                    where.append("(SELECT rating FROM user_ratings r WHERE r.title_id=t.id) >= ?")
                    args.append(min_rating)

                sql = f"""
                    {cte}SELECT t.id, t.title, t.year, t.runtime, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file, t.tmdb_vote,
                           (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
                           (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
                           (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count,
                           (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id=t.id) AS formats
                    FROM titles t
                    WHERE {' AND '.join(where)}
                    ORDER BY t.sort_title ASC
                    LIMIT ?
                """
                args.append(limit)
                rows = con.execute(sql, args).fetchall()
                results = []
                for r in rows:
                    results.append({
                        "id": r["id"],
                        "title": r["title"],
                        "year": r["year"],
                        "runtime": r["runtime"],
                        "user_rating": r["user_rating"],
                        "tmdb_vote": r["tmdb_vote"],
                        "last_watched": r["last_watched"],
                        "watch_count": r["watch_count"],
                        "formats": (r["formats"] or "").split(",") if r["formats"] else [],
                    })
                return results
            finally:
                con.close()

        # Fallback to REST API
        if unseen_by is not None:
            self._require_people_api()
        params = {"limit": limit}
        if query:
            params["q"] = query
        if genre:
            params["genre"] = genre
        if medium:
            params["medium"] = medium
        if format:
            params["format"] = format
        if watched:
            params["watched"] = watched
        if min_rating is not None:
            params["rating"] = str(min_rating)
        if unseen_by is not None:
            params["unseen_by"] = unseen_by

        qs = urllib.parse.urlencode(params)
        res = self._api_request("GET", f"{self._read_prefix}/titles?{qs}")
        return res.get("titles", [])[:limit]

    def get_movie(self, title_id: int) -> Dict[str, Any]:
        """Fetch detailed information for a specific movie in the collection. Watches carry `attendance` and
        `attendees` (names); the movie carries `seen_by` and `not_seen_by` (names of active people). With no people
        set up, or on a database older than schema v9, the name lists are empty. Public REST mode has none of them."""
        return self._get_movie(title_id, names=True)

    def _get_movie(self, title_id: int, names: bool) -> Dict[str, Any]:
        """get_movie; `names=False` skips the REST people lookup (where_is only needs the copies)."""
        con = self._get_db()
        if con is not None:
            try:
                t = con.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
                if not t:
                    raise MatineeError(f"Movie with id {title_id} not found.")
                copies = con.execute(
                    "SELECT id, kind, format, service, location_id, created_at FROM copies WHERE title_id=? ORDER BY created_at, id",
                    (title_id,),
                ).fetchall()
                rating_row = con.execute("SELECT rating FROM user_ratings WHERE title_id=?", (title_id,)).fetchone()
                watches = con.execute(
                    "SELECT id, watched_date, date_bucket, notes, created_at FROM watches WHERE title_id=? ORDER BY watched_date DESC, id DESC",
                    (title_id,),
                ).fetchall()
                genres = [
                    r["name"]
                    for r in con.execute(
                        "SELECT g.name FROM genres g JOIN title_genres tg ON tg.genre_id=g.id WHERE tg.title_id=?",
                        (title_id,),
                    ).fetchall()
                ]

                d = dict(t)
                paths = _location_paths(con)
                d["copies"] = [
                    {**dict(c), "location_path": paths.get(c["location_id"]) if c["location_id"] else None}
                    for c in copies
                ]
                d["user_rating"] = rating_row["rating"] if rating_row else None
                d["watches"], d["seen_by"], d["not_seen_by"] = self._db_attendance(con, title_id, watches)
                d["last_watched"] = watches[0]["watched_date"] if watches else None
                d["watch_count"] = len(watches)
                d["genres"] = genres
                d["custom_poster"] = bool(d.get("custom_poster_file"))  # same key as GET /api/titles/<id>
                # effective certification, same shape as GET /api/titles/<id>
                d["certification_tmdb"] = t["certification"]
                d["certification_override"] = t["certification_override"]
                d["certification"] = t["certification_override"] or t["certification"]
                return d
            finally:
                con.close()

        try:
            movie = self._api_request("GET", f"{self._read_prefix}/titles/{title_id}")
        except MatineeError as e:
            if e.status == 404:
                raise MatineeError(f"Movie with id {title_id} not found.") from None
            raise
        return self._name_people(movie) if names else movie

    @staticmethod
    def _db_attendance(con, title_id: int, watches) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        """(watches with attendance and attendee names, seen_by names, not_seen_by names), by the rules the app
        uses: recommend.attendance_of for each watch and recommend.SEEN_CTE for who has seen the title."""
        import recommend
        people = []
        recorded: Dict[int, set] = {}
        seen_ids: set = set()
        if recommend.has_people(con):
            people = con.execute("SELECT id, name, kind, active FROM people ORDER BY sort_order, id").fetchall()
            ids = [w["id"] for w in watches]
            for r in con.execute("SELECT watch_id, person_id FROM watch_people WHERE watch_id IN (%s)"
                                 % ",".join("?" * len(ids)), ids) if ids else []:
                recorded.setdefault(r["watch_id"], set()).add(r["person_id"])
            seen_ids = {r[0] for r in con.execute(
                f"WITH {recommend.SEEN_CTE} SELECT DISTINCT person_id FROM seen WHERE title_id=?", (title_id,))}
        out = []
        for w in watches:
            mine = recorded.get(w["id"])
            kind = recommend.attendance_of(w, bool(mine))
            if mine:
                who = [p["name"] for p in people if p["id"] in mine]
            else:
                who = [p["name"] for p in people if kind == "assumed_everyone" or p["kind"] == "adult"]
            d = {k: w[k] for k in w.keys() if k != "date_bucket"}
            d["attendance"], d["attendees"] = kind, who
            out.append(d)
        return (out, [p["name"] for p in people if p["id"] in seen_ids],
                [p["name"] for p in people if p["active"] and p["id"] not in seen_ids])

    def where_is(self, title: str) -> Dict[str, Any]:
        """Find where physical copies of a movie are stored or which digital services host it."""
        con = self._get_db()
        if con is not None:
            try:
                q_clean = title.strip().lower()
                row = con.execute(
                    "SELECT id, title, year FROM titles WHERE lower(title) = ? AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=titles.id)",
                    (q_clean,),
                ).fetchone()
                if not row:
                    # Fold and split like the grid's list_titles, so 'Wall-E' finds 'WALL·E' in both modes.
                    ws = _words(title)
                    clauses = "".join(" AND search_text LIKE ? ESCAPE '\\'" for _ in ws)
                    row = con.execute(
                        "SELECT id, title, year FROM titles WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=titles.id)"
                        f"{clauses} ORDER BY length(title) ASC LIMIT 1",
                        [f"%{_like_escape(w)}%" for w in ws],
                    ).fetchone()
                if not row:
                    return {
                        "found": False,
                        "title": title,
                        "message": f"'{title}' is not currently in the collection.",
                        "copies": [],
                        "summary": f"'{title}' is not currently in the collection.",
                    }

                title_id = row["id"]
                title_name = row["title"]
                year = row["year"]

                paths = _location_paths(con)

                copies_rows = con.execute(
                    "SELECT id, kind, format, service, location_id FROM copies WHERE title_id=? ORDER BY created_at, id",
                    (title_id,),
                ).fetchall()

                copies_list = []
                summary_parts = []
                for c in copies_rows:
                    loc_path = paths.get(c["location_id"]) if c["location_id"] else None
                    cp_dict = {
                        "id": c["id"],
                        "kind": c["kind"],
                        "format": c["format"],
                        "service": c["service"],
                        "location_id": c["location_id"],
                        "location_path": loc_path,
                    }
                    copies_list.append(cp_dict)
                    if c["kind"] == "physical":
                        loc_str = loc_path or "Unassigned shelf"
                        summary_parts.append(f"{c['format']} at {loc_str}")
                    else:
                        srv_str = c["service"] or "Digital"
                        summary_parts.append(f"{c['format']} on {srv_str}")

                summary_str = f"{title_name} ({year}): " + "; ".join(summary_parts) if summary_parts else f"{title_name}: No copies recorded."
                return {
                    "found": True,
                    "id": title_id,
                    "title": title_name,
                    "year": year,
                    "copies": copies_list,
                    "summary": summary_str,
                }
            finally:
                con.close()

        # REST mode
        # Matinee ignores `limit` and returns every match in title order; take them all so a short
        # exact title that sorts late is not cut off.
        qs = urllib.parse.urlencode({"q": title})
        results = self._api_request("GET", f"{self._read_prefix}/titles?{qs}").get("titles", [])
        if not results:
            return {
                "found": False,
                "title": title,
                "message": f"'{title}' is not currently in the collection.",
                "copies": [],
                "summary": f"'{title}' is not currently in the collection.",
            }
        # Mirror the SQLite branch: exact title first, else the shortest title.
        wanted = title.strip().lower()
        pick = next((r for r in results if (r.get("title") or "").strip().lower() == wanted), None)
        if pick is None:
            pick = min(results, key=lambda r: len(r.get("title") or ""))
        movie = self._get_movie(pick["id"], names=False)
        copies = movie.get("copies", [])
        copies_list = []
        summary_parts = []
        for c in copies:
            cp_dict = {
                "id": c.get("id"),
                "kind": c.get("kind"),
                "format": c.get("format"),
                "service": c.get("service"),
                "location_id": c.get("location_id"),
                "location_path": c.get("location_path"),
            }
            copies_list.append(cp_dict)
            if c.get("kind") == "physical":
                loc_str = c.get("location_path") or "Unassigned shelf"
                summary_parts.append(f"{c.get('format')} at {loc_str}")
            else:
                srv_str = c.get("service") or "Digital"
                summary_parts.append(f"{c.get('format')} on {srv_str}")

        year_str = f" ({movie.get('year')})" if movie.get("year") else ""
        summary_str = f"{movie.get('title')}{year_str}: " + "; ".join(summary_parts) if summary_parts else f"{movie.get('title')}: No copies recorded."
        return {
            "found": True,
            "id": movie.get("id"),
            "title": movie.get("title"),
            "year": movie.get("year"),
            "copies": copies_list,
            "summary": summary_str,
        }

    def get_recommendations(
        self,
        mode: str = "forgotten_gems",
        genre: Optional[str] = None,
        medium: Optional[str] = None,
        max_runtime: Optional[int] = None,
        min_rating: Optional[float] = None,
        limit: int = 5,
        random: bool = False,
        max_cert: Optional[str] = None,
        audience: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query recommendation heuristics (see the get_recommendations tool for modes and filters)."""
        con = self._get_db()
        if con is not None:
            try:
                import recommend
                if audience is not None and not audience.strip():
                    raise MatineeError("audience is empty.")
                out = recommend.recommend(con, recommend.parse_params({
                    "mode": mode, "genre": genre, "medium": medium, "max_runtime": max_runtime,
                    "min_rating": min_rating, "limit": limit, "random": random,
                    "max_cert": max_cert, "audience": audience,
                }))
                for r in out["results"]:
                    r.pop("poster_file", None)
                    r.pop("fetched_at", None)
                return out
            finally:
                con.close()

        if audience is not None:
            self._require_people_api()
        params = {"mode": mode, "limit": limit}
        if audience is not None:
            params["audience"] = audience
        if genre:
            params["genre"] = genre
        if medium:
            params["medium"] = medium
        if max_runtime is not None:
            params["max_runtime"] = max_runtime
        if min_rating is not None:
            params["min_rating"] = min_rating
        if max_cert:
            params["max_cert"] = max_cert
        if random:
            params["random"] = "1"

        qs = urllib.parse.urlencode(params)
        out = self._api_request("GET", f"{self._read_prefix}/recommendations?{qs}")
        if audience is not None and self.api_key and isinstance(out, dict) and "audience" not in out:
            # The server ignored it (it echoes `audience` whenever it applies one); never return an unfiltered list.
            raise MatineeError(TOO_OLD_FOR_PEOPLE)
        if max_cert and isinstance(out, dict) and isinstance(out.get("results"), list):
            # Older Matinee servers ignore max_cert; never trust them with a kids-safety filter.
            # Written out locally: this REST path must not import recommend.py.
            order = ("G", "PG", "PG-13", "R", "NC-17")
            allowed = order[:order.index(max_cert) + 1] if max_cert in order else ()
            kept = [r for r in out["results"] if r.get("certification") in allowed]
            if len(kept) != len(out["results"]):
                out = {**out, "results": kept, "count": len(kept),
                       "hint": "Server did not apply max_cert; update Matinee."}
        return out

    def get_collection_stats(self) -> Dict[str, Any]:
        """Fetch overall collection metrics, formats, ratings, and watch statistics."""
        con = self._get_db()
        if con is not None:
            try:
                titles_count = con.execute(
                    "SELECT count(DISTINCT t.id) FROM titles t WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"
                ).fetchone()[0]
                physical_count = con.execute("SELECT count(*) FROM copies WHERE kind='physical'").fetchone()[0]
                digital_count = con.execute("SELECT count(*) FROM copies WHERE kind='digital'").fetchone()[0]
                dual_count = con.execute(
                    """SELECT count(DISTINCT t.id) FROM titles t
                       WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical')
                         AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')"""
                ).fetchone()[0]
                total_watched = con.execute("SELECT count(DISTINCT title_id) FROM watches").fetchone()[0]
                rated_count = con.execute("SELECT count(*) FROM user_ratings").fetchone()[0]
                avg_rating = con.execute("SELECT avg(rating) FROM user_ratings").fetchone()[0]

                return {
                    "overview": {
                        "titles_count": titles_count,
                        "physical_count": physical_count,
                        "digital_count": digital_count,
                        "dual_count": dual_count,
                    },
                    "watched_stats": {
                        "total_watched_titles": total_watched,
                        "watched_percent": round((total_watched / titles_count * 100), 1) if titles_count else 0.0,
                    },
                    "rating_stats": {
                        "rated_titles_count": rated_count,
                        "average_user_rating": round(avg_rating, 2) if avg_rating else None,
                    },
                }
            finally:
                con.close()

        return self._api_request("GET", f"{self._read_prefix}/stats")

    # ------------------------------------------------------------------------ Writes
    def rate_movie(self, title_id: int, rating: Optional[float]) -> Dict[str, Any]:
        """Set or clear a movie's personal rating (0.0 to 10.0 in 0.5 steps, or None to clear)."""
        return self._api_request("PUT", f"/admin/api/titles/{title_id}/rating", {"rating": rating})

    def log_watch(
        self,
        title_id: int,
        watched_date: Optional[str] = None,
        notes: Optional[str] = None,
        watched_at: Optional[str] = None,
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Record a watch event for a title with date, optional notes and optional attendees (names).
        No `attendees` key is sent when None (the server then counts everyone); an empty list is sent as is."""
        date_val = watched_date if watched_date is not None else watched_at
        payload: Dict[str, Any] = {"watched_date": date_val}
        if notes:
            payload["notes"] = notes
        if attendees is not None:
            payload["attendees"] = list(attendees)
        return self._api_request("POST", f"/admin/api/titles/{title_id}/watches", payload)

    def search_tmdb(self, query: str) -> List[Dict[str, Any]]:
        """Search TMDB for movies to add to the collection."""
        qs = urllib.parse.urlencode({"q": query})
        res = self._api_request("GET", f"/admin/api/tmdb/search?{qs}")
        return res.get("results", [])

    def add_movie(
        self,
        tmdb_id: int,
        kind: str = "physical",
        format: str = "Blu-ray",
        service: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import a movie from TMDB into the collection with copy details."""
        payload = {
            "tmdb_id": tmdb_id,
            "kind": kind,
            "format": format,
            "service": service,
        }
        return self._api_request("POST", "/admin/api/copies", payload)


SERVER_INSTRUCTIONS = """\
Matinee is the family's own collection of physical discs and digital copies.
- Recommend only what get_recommendations or search_collection return. Use search_tmdb only when the user asks about movies they do not own.
- "Kids' night" or "something the kids can watch": call get_recommendations(audience="kids", max_cert="PG-13") unless the user names a rating. Unrated films are left out whenever max_cert is set.
- "What's next in the series we're watching": mode="next_in_franchise". "Let's start a new series": mode="start_franchise".
- "The girls" or "the kids": audience="kids". "Us two", "date night": audience="adults". A named person: audience="Alice". After watching, pass attendees to log_watch only when someone was missing.
- After a pick, "where is it?": call where_is(title). After watching: call log_watch, then rate_movie if the user gives a rating.
"""
# For build_server(writes=False): the remote read-only service has no log_watch or rate_movie to point at.
READONLY_INSTRUCTIONS = SERVER_INSTRUCTIONS.replace(
    " After watching: call log_watch, then rate_movie if the user gives a rating.", "").replace(
    " After watching, pass attendees to log_watch only when someone was missing.", "")
# Said at the levels that lack tools, in words that name none of them.
_NO_WRITES_NOTE = ("- This connection is read-only: it cannot log watches, rate movies or add titles. "
                   "For those, tell the user to use the Matinee app.\n")
_LOG_ONLY_NOTE = ("- This connection can log watches and ratings but cannot add movies or copies; "
                  "a title they do not own has to be added in the Matinee app.\n")


def instructions_for(writes) -> str:
    """Server instructions that only mention tools registered at this write level."""
    level = _level(writes)
    if level == "all":
        return SERVER_INSTRUCTIONS
    if level == "log":
        return SERVER_INSTRUCTIONS + _LOG_ONLY_NOTE
    return READONLY_INSTRUCTIONS + _NO_WRITES_NOTE


def _make_server(instructions: str = SERVER_INSTRUCTIONS):
    """Build the FastMCP server; older mcp releases may not accept instructions=."""
    if not FastMCP:
        return None
    try:
        return FastMCP("matinee", instructions=instructions)
    except TypeError:
        return FastMCP("matinee")


_default_client = MatineeClient()

WRITE_TOOLS = ("rate_movie", "log_watch", "add_movie")
# Which write tools each level registers. "log" is what the phone needs to log a movie night: it can record a
# watch and a rating but cannot import titles (add_movie spends TMDB quota and adds copies).
WRITE_LEVELS: Dict[str, Tuple[str, ...]] = {
    "none": (),
    "log": ("rate_movie", "log_watch"),
    "all": WRITE_TOOLS,
}


def _level(writes) -> str:
    """Normalize build_server's `writes`: True is "all", False is "none", or one of WRITE_LEVELS."""
    if writes is True:
        return "all"
    if writes is False:
        return "none"
    if isinstance(writes, str) and writes in WRITE_LEVELS:
        return writes
    raise ValueError(f"writes must be True, False or one of {sorted(WRITE_LEVELS)}, not {writes!r}")


def search_collection(
    query: str = "",
    genre: Optional[str] = None,
    medium: Optional[str] = None,
    format: Optional[str] = None,
    watched: Optional[str] = None,
    min_rating: Optional[float] = None,
    limit: int = 20,
    unseen_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search the movie collection by title, genre, medium (physical/digital), format, watched status, or rating.
    unseen_by: only titles none of these people has seen; same grammar as get_recommendations' audience
    ("kids", "adults", "everyone", or names such as "Alice,Paige")."""
    return _default_client.search_collection(
        query=query,
        genre=genre,
        medium=medium,
        format=format,
        watched=watched,
        min_rating=min_rating,
        limit=limit,
        unseen_by=unseen_by,
    )


def get_movie(title_id: int) -> Dict[str, Any]:
    """Get full details for a movie in the collection including physical/digital copies, user rating, and watch history.
    Each watch lists who was there (attendance, attendees); seen_by and not_seen_by name the family members who
    have and have not seen it (empty when no people are set up)."""
    return _default_client.get_movie(title_id)


def where_is(title: str) -> Dict[str, Any]:
    """Find where physical copies of a movie are stored (shelf/cabinet/room) or which digital services host it."""
    return _default_client.where_is(title)


def get_recommendations(
    mode: Literal["forgotten_gems", "never_watched", "quick_picks", "genre_mood",
                  "next_in_franchise", "start_franchise"] = "forgotten_gems",
    genre: Optional[str] = None,
    medium: Optional[Literal["physical", "digital"]] = None,
    max_runtime: Optional[int] = None,
    min_rating: Optional[float] = None,
    max_cert: Optional[Literal["G", "PG", "PG-13", "R", "NC-17"]] = None,
    limit: int = 5,
    random: bool = False,
    audience: Optional[str] = None,
) -> Dict[str, Any]:
    """Pick movies to watch from the family's own collection (movie night, "what should we watch",
    kids' night, family-friendly, sequel, next in the series, start a franchise, quick short movie).
    <use_case>
    - mode=next_in_franchise: next unwatched owned film in each series someone has started (one per series)
    - mode=start_franchise: first film of an owned series (2+ films) nobody has started
    - max_cert=G|PG|PG-13|R|NC-17: rating cap; works with any mode. For "kids" with no rating named, use PG-13.
    - audience=kids|adults|everyone or names ("Alice,Paige"): pick for who is watching. Titles anyone in the audience
      has seen are left out (franchise modes: the next film nobody in it has seen). Results carry seen_by. Call
      list_people to see who is set up.
    - forgotten_gems, never_watched, quick_picks (≤95 min default), genre_mood as before
    </use_case>
    <important_notes>
    - Films with no US rating (NR/unrated/unknown) are EXCLUDED whenever max_cert is set.
    - Franchise modes choose the next film first, then apply filters: if the next Harry Potter is
      PG-13 and max_cert=PG, that series yields nothing rather than skipping ahead.
    - An empty result includes a `hint` with what to try next; follow it instead of telling the
      user nothing matched.
    - Each result has `id`; pass it to get_movie or where_is for the shelf location.
    </important_notes>
    """
    return _default_client.get_recommendations(
        mode=mode,
        genre=genre,
        medium=medium,
        max_runtime=max_runtime,
        min_rating=min_rating,
        max_cert=max_cert,
        limit=limit,
        random=random,
        audience=audience,
    )


def list_people() -> List[Dict[str, Any]]:
    """List the family members Matinee knows (id, name, kind adult/kid, color, active). Use it to resolve a name
    before passing audience, unseen_by or attendees."""
    return _default_client.list_people()


def get_collection_stats() -> Dict[str, Any]:
    """Get summary statistics for the collection: total movies, formats, watch coverage, and ratings."""
    return _default_client.get_collection_stats()


def rate_movie(title_id: int, rating: Optional[float]) -> Dict[str, Any]:
    """Set, update, or clear a movie's personal rating (0.0 to 10.0 in 0.5 steps, or null/None to clear)."""
    return _default_client.rate_movie(title_id, rating)


def log_watch(
    title_id: int,
    watched_date: Optional[str] = None,
    notes: Optional[str] = None,
    watched_at: Optional[str] = None,
    attendees: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Record a watch event with an optional ISO date (YYYY-MM-DD; omit or pass null for dateless/historical watch) and optional notes.
    attendees: names of who watched; omit it when everyone did."""
    return _default_client.log_watch(title_id, watched_date=watched_date, notes=notes, watched_at=watched_at,
                                     attendees=attendees)


def search_tmdb(query: str) -> List[Dict[str, Any]]:
    """Search TMDB for movies not yet in the collection (or to add additional copies)."""
    return _default_client.search_tmdb(query)


def add_movie(
    tmdb_id: int,
    kind: str = "physical",
    format: str = "Blu-ray",
    service: Optional[str] = None,
) -> Dict[str, Any]:
    """Import a movie from TMDB into the collection with copy details (kind='physical'|'digital')."""
    return _default_client.add_movie(tmdb_id, kind=kind, format=format, service=service)


def resource_stats() -> str:
    """Collection statistics summary."""
    stats = _default_client.get_collection_stats()
    return json.dumps(stats, indent=2)


def resource_recommendations() -> str:
    """Current top recommended movies from the collection."""
    recs = _default_client.get_recommendations(mode="forgotten_gems", limit=5)
    return json.dumps(recs, indent=2)


def prompt_what_to_watch(mood: str = "", max_runtime: int = 0, kids: bool = False) -> str:
    """Suggest a curated movie night lineup based on mood, genre, or time constraints."""
    instructions = [
        "You are helping the family choose a movie to watch tonight from their personal collection.",
        "1. Use `get_recommendations` or `search_collection` to check available movies.",
    ]
    if mood:
        instructions.append(f"{len(instructions)}. Prioritize movies matching the mood or genre '{mood}'.")
    if max_runtime > 0:
        instructions.append(f"{len(instructions)}. Ensure suggested movies do not exceed {max_runtime} minutes.")
    instructions.append(f"{len(instructions)}. Present 2-3 distinct choices with format (e.g. 4K UHD vs Blu-ray) and reason why they fit.")
    if kids:
        instructions.append(
            f'{len(instructions)}. This is a kids\' night: call `get_recommendations` with audience="kids" and max_cert="PG-13" and tell the '
            "family that unrated films were left out."
        )
    return "\n".join(instructions)


def prompt_log_movie_night(movie_title: str = "") -> str:
    """Prompt to help log and rate a movie that was just watched."""
    return (
        f"Let's log the movie that was just watched: '{movie_title or 'movie'}'.\n"
        "1. Use `search_collection` to find the exact movie ID.\n"
        "2. Ask the user for their rating (0.0 to 10.0 in 0.5 steps) and any notes/impressions.\n"
        "3. Use `rate_movie` and `log_watch` to update Matinee."
    )


def build_server(*, writes=True):
    """Build a MCP server. `writes` is True (all ten tools), False (the seven read tools) or a level from
    WRITE_LEVELS: "none", "log" (adds rate_movie and log_watch: nine tools) or "all" (adds add_movie: ten).
    Anything else raises ValueError.

    Tool functions are module-level and read _default_client at call time.
    """
    level = _level(writes)
    server = _make_server(instructions_for(level))
    if not server:
        return None

    def register(fn):
        server.tool(**_annotation_kwargs(FastMCP, fn.__name__))(fn)

    for fn in (search_collection, get_movie, where_is, get_recommendations, get_collection_stats, list_people,
               search_tmdb):
        register(fn)
    for fn in (rate_movie, log_watch, add_movie):
        if fn.__name__ in WRITE_LEVELS[level]:
            register(fn)
    server.resource("matinee://stats")(resource_stats)
    server.resource("matinee://recommendations")(resource_recommendations)
    server.prompt("what_to_watch")(prompt_what_to_watch)
    if level != "none":  # log_movie_night names rate_movie and log_watch
        server.prompt("log_movie_night")(prompt_log_movie_night)
    return server


# FastMCP Server Instance
mcp = build_server()


def main():
    if not mcp:
        print("MCP SDK not installed. Please install with: pip install mcp", file=sys.stderr)
        sys.exit(1)
    mcp.run()


if __name__ == "__main__":
    main()
