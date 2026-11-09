#!/usr/bin/env python3
"""Matinee Model Context Protocol (MCP) Server.
Exposes read and write tools, resources, and prompts for the Matinee movie collection.

Can operate in:
1. REST Mode: Connects to a running Matinee instance via MATINEE_URL and MATINEE_API_KEY.
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
from typing import Any, Dict, List, Literal, Optional

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


def _readonly_kwargs(server_cls) -> Dict[str, Any]:
    """Read-only tool annotations, only when this mcp release supports them on tool()."""
    if server_cls is None:
        return {}
    try:
        from mcp.types import ToolAnnotations
        if "annotations" not in inspect.signature(server_cls.tool).parameters:
            return {}
    except (ImportError, TypeError, ValueError):
        return {}
    return {"annotations": ToolAnnotations(readOnlyHint=True, openWorldHint=False)}


_READONLY = _readonly_kwargs(FastMCP)

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


MATINEE_URL = os.environ.get("MATINEE_URL", "http://127.0.0.1:8769").rstrip("/")
MATINEE_API_KEY = os.environ.get("MATINEE_API_KEY", "").strip()
MATINEE_DB_PATH = os.environ.get("MATINEE_DB_PATH", "").strip()


class MatineeClient:
    """Client for Matinee supporting direct SQLite reads and REST API mutations."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.api_url = (api_url or MATINEE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else MATINEE_API_KEY
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
                    f"Matinee rejected the service's API key (HTTP {e.code}). Check MATINEE_API_KEY on the Matinee host."
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
    ) -> List[Dict[str, Any]]:
        """Search collection by keyword, genre, medium, format, watched status, or rating."""
        con = self._get_db()
        if con is not None:
            try:
                where = ["EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"]
                args = []

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
                    SELECT t.id, t.title, t.year, t.runtime, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file, t.tmdb_vote,
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

        qs = urllib.parse.urlencode(params)
        res = self._api_request("GET", f"{self._read_prefix}/titles?{qs}")
        return res.get("titles", [])[:limit]

    def get_movie(self, title_id: int) -> Dict[str, Any]:
        """Fetch detailed information for a specific movie in the collection."""
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
                    "SELECT id, watched_date, notes, created_at FROM watches WHERE title_id=? ORDER BY watched_date DESC, id DESC",
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
                d["watches"] = [dict(w) for w in watches]
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
            return self._api_request("GET", f"{self._read_prefix}/titles/{title_id}")
        except MatineeError as e:
            if e.status == 404:
                raise MatineeError(f"Movie with id {title_id} not found.") from None
            raise

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
        movie = self.get_movie(pick["id"])
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
    ) -> Dict[str, Any]:
        """Query recommendation heuristics (see the get_recommendations tool for modes and filters)."""
        con = self._get_db()
        if con is not None:
            try:
                import recommend
                out = recommend.recommend(con, recommend.parse_params({
                    "mode": mode, "genre": genre, "medium": medium, "max_runtime": max_runtime,
                    "min_rating": min_rating, "limit": limit, "random": random,
                    "max_cert": max_cert,
                }))
                for r in out["results"]:
                    r.pop("poster_file", None)
                    r.pop("fetched_at", None)
                return out
            finally:
                con.close()

        params = {"mode": mode, "limit": limit}
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
    ) -> Dict[str, Any]:
        """Record a watch event for a title with date and optional notes."""
        date_val = watched_date if watched_date is not None else watched_at
        payload: Dict[str, Any] = {"watched_date": date_val}
        if notes:
            payload["notes"] = notes
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
- "Kids' night" or "something the kids can watch": call get_recommendations(max_cert="PG-13") unless the user names a rating. Unrated films are left out whenever max_cert is set.
- "What's next in the series we're watching": mode="next_in_franchise". "Let's start a new series": mode="start_franchise".
- After a pick, "where is it?": call where_is(title). After watching: call log_watch, then rate_movie if the user gives a rating.
"""


def _make_server():
    """Build the FastMCP server; older mcp releases may not accept instructions=."""
    if not FastMCP:
        return None
    try:
        return FastMCP("matinee", instructions=SERVER_INSTRUCTIONS)
    except TypeError:
        return FastMCP("matinee")


_default_client = MatineeClient()

WRITE_TOOLS = ("rate_movie", "log_watch", "add_movie")


def search_collection(
    query: str = "",
    genre: Optional[str] = None,
    medium: Optional[str] = None,
    format: Optional[str] = None,
    watched: Optional[str] = None,
    min_rating: Optional[float] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Search the movie collection by title, genre, medium (physical/digital), format, watched status, or rating."""
    return _default_client.search_collection(
        query=query,
        genre=genre,
        medium=medium,
        format=format,
        watched=watched,
        min_rating=min_rating,
        limit=limit,
    )


def get_movie(title_id: int) -> Dict[str, Any]:
    """Get full details for a movie in the collection including physical/digital copies, user rating, and watch history."""
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
) -> Dict[str, Any]:
    """Pick movies to watch from the family's own collection (movie night, "what should we watch",
    kids' night, family-friendly, sequel, next in the series, start a franchise, quick short movie).
    <use_case>
    - mode=next_in_franchise: next unwatched owned film in each series someone has started (one per series)
    - mode=start_franchise: first film of an owned series (2+ films) nobody has started
    - max_cert=G|PG|PG-13|R|NC-17: rating cap; works with any mode. For "kids" with no rating named, use PG-13.
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
    )


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
) -> Dict[str, Any]:
    """Record a watch event with an optional ISO date (YYYY-MM-DD; omit or pass null for dateless/historical watch) and optional notes."""
    return _default_client.log_watch(title_id, watched_date=watched_date, notes=notes, watched_at=watched_at)


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
            f'{len(instructions)}. This is a kids\' night: call `get_recommendations` with max_cert="PG-13" and tell the '
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


def build_server(*, writes: bool = True):
    """Build a MCP server with every tool; writes=False leaves out WRITE_TOOLS.

    Tool functions are module-level and read _default_client at call time.
    """
    server = _make_server()
    if not server:
        return None
    server.tool()(search_collection)
    server.tool()(get_movie)
    server.tool()(where_is)
    server.tool(**_READONLY)(get_recommendations)
    server.tool()(get_collection_stats)
    server.tool()(search_tmdb)
    if writes:
        for fn in (rate_movie, log_watch, add_movie):
            server.tool()(fn)
    server.resource("matinee://stats")(resource_stats)
    server.resource("matinee://recommendations")(resource_recommendations)
    server.prompt("what_to_watch")(prompt_what_to_watch)
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
