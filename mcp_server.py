#!/usr/bin/env python3
"""Matinee Model Context Protocol (MCP) Server.
Exposes read and write tools, resources, and prompts for the Matinee movie collection.

Can operate in:
1. REST Mode: Connects to a running Matinee instance via MATINEE_URL and MATINEE_API_KEY.
2. Direct SQLite Mode: Accelerates read operations directly against MATINEE_DB_PATH.
"""
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        FastMCP = None

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
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error") or err_json.get("message") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"Matinee API error ({e.code}): {msg}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Matinee at {self.api_url}: {e}") from e

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

                if query:
                    clean_q = f"%{query.strip().lower()}%"
                    where.append("(lower(t.title) LIKE ? OR lower(t.search_text) LIKE ?)")
                    args.extend([clean_q, clean_q])

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
                    SELECT t.id, t.title, t.year, t.runtime, t.poster_file, t.tmdb_vote,
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
                    raise RuntimeError(f"Movie with id {title_id} not found.")
                copies = con.execute(
                    "SELECT id, kind, format, service, created_at FROM copies WHERE title_id=? ORDER BY created_at, id",
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
                d["copies"] = [dict(c) for c in copies]
                d["user_rating"] = rating_row["rating"] if rating_row else None
                d["watches"] = [dict(w) for w in watches]
                d["last_watched"] = watches[0]["watched_date"] if watches else None
                d["watch_count"] = len(watches)
                d["genres"] = genres
                return d
            finally:
                con.close()

        return self._api_request("GET", f"{self._read_prefix}/titles/{title_id}")

    def get_recommendations(
        self,
        mode: str = "forgotten_gems",
        genre: Optional[str] = None,
        medium: Optional[str] = None,
        max_runtime: Optional[int] = None,
        min_rating: Optional[float] = None,
        limit: int = 5,
        random: bool = False,
    ) -> Dict[str, Any]:
        """Query recommendation heuristics (forgotten_gems, never_watched, quick_picks, genre_mood)."""
        con = self._get_db()
        if con is not None:
            try:
                where = ["EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"]
                args = []

                if mode == "forgotten_gems":
                    where.append("(SELECT rating FROM user_ratings r WHERE r.title_id=t.id) >= 8.0")
                    where.append("COALESCE((SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id), '0000-00-00') < date('now', '-365 days')")
                    order_by = "user_rating DESC, COALESCE(last_watched, '0000-00-00') ASC, t.sort_title ASC"
                elif mode == "never_watched":
                    where.append("NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")
                    order_by = "COALESCE(t.tmdb_vote, 0) DESC, t.year DESC, t.sort_title ASC"
                elif mode == "quick_picks":
                    max_rt = max_runtime or 95
                    where.append("t.runtime > 0 AND t.runtime <= ?")
                    args.append(max_rt)
                    order_by = "COALESCE((SELECT rating FROM user_ratings r WHERE r.title_id=t.id), t.tmdb_vote, 0) DESC, t.runtime ASC"
                elif mode == "genre_mood":
                    order_by = "COALESCE((SELECT rating FROM user_ratings r WHERE r.title_id=t.id), t.tmdb_vote, 0) DESC, t.sort_title ASC"
                else:
                    order_by = "COALESCE((SELECT rating FROM user_ratings r WHERE r.title_id=t.id), t.tmdb_vote, 0) DESC, t.sort_title ASC"

                if genre:
                    where.append("EXISTS (SELECT 1 FROM title_genres tg JOIN genres gn ON gn.id=tg.genre_id WHERE tg.title_id=t.id AND gn.name = ? COLLATE NOCASE)")
                    args.append(genre)

                if medium in ("physical", "digital"):
                    where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind = ?)")
                    args.append(medium)

                if min_rating is not None:
                    where.append("COALESCE((SELECT rating FROM user_ratings r WHERE r.title_id=t.id), t.tmdb_vote, 0) >= ?")
                    args.append(min_rating)

                if max_runtime is not None and mode != "quick_picks":
                    where.append("t.runtime > 0 AND t.runtime <= ?")
                    args.append(max_runtime)

                if random:
                    order_by = "RANDOM()"

                sql = f"""
                    SELECT t.id, t.title, t.year, t.runtime, t.poster_file, t.tmdb_vote,
                           (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
                           (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
                           (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count,
                           (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id=t.id) AS formats,
                           (SELECT group_concat(g.name, ', ') FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=t.id) AS genres
                    FROM titles t
                    WHERE {' AND '.join(where)}
                    ORDER BY {order_by}
                    LIMIT ?
                """
                args.append(limit)
                rows = con.execute(sql, args).fetchall()
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
                                months = max(1, (dt.date.today() - w_date).days // 30)
                                reason = f"Rated {user_r:.1f} ★ · Not watched in {months} months"
                            except Exception:
                                reason = f"Rated {user_r:.1f} ★ · Forgotten gem"
                        elif r["watch_count"] > 0:
                            reason = f"Rated {user_r:.1f} ★ · Historical watch (Date unknown)"
                        else:
                            reason = f"Rated {user_r:.1f} ★ · Never logged as watched"
                    elif mode == "never_watched":
                        reason = f"Unwatched backlog · TMDB {tmdb_v:.1f} ★" if tmdb_v else "Unwatched backlog"
                    elif mode == "quick_picks":
                        reason = f"{runtime} mins · Perfect quick watch" if runtime else "Quick watch"
                    else:
                        reason = "Curated collection pick"

                    results.append({
                        "id": r["id"],
                        "title": r["title"],
                        "year": r["year"],
                        "runtime": r["runtime"],
                        "user_rating": user_r,
                        "tmdb_vote": tmdb_v,
                        "last_watched": last_w,
                        "watch_count": r["watch_count"],
                        "formats": (r["formats"] or "").split(",") if r["formats"] else [],
                        "genres": genres_list,
                        "recommendation_reason": reason,
                        "reason": reason,
                    })
                return {"mode": mode, "count": len(results), "results": results}
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
        if random:
            params["random"] = "1"

        qs = urllib.parse.urlencode(params)
        return self._api_request("GET", f"{self._read_prefix}/recommendations?{qs}")

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


# FastMCP Server Instance
mcp = FastMCP("matinee") if FastMCP else None

_default_client = MatineeClient()

if mcp:
    @mcp.tool()
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

    @mcp.tool()
    def get_movie(title_id: int) -> Dict[str, Any]:
        """Get full details for a movie in the collection including physical/digital copies, user rating, and watch history."""
        return _default_client.get_movie(title_id)

    @mcp.tool()
    def get_recommendations(
        mode: str = "forgotten_gems",
        genre: Optional[str] = None,
        max_runtime: Optional[int] = None,
        min_rating: Optional[float] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Get movie recommendations using heuristics: 'forgotten_gems', 'never_watched', 'quick_picks', or 'genre_mood'."""
        return _default_client.get_recommendations(
            mode=mode,
            genre=genre,
            max_runtime=max_runtime,
            min_rating=min_rating,
            limit=limit,
        )

    @mcp.tool()
    def get_collection_stats() -> Dict[str, Any]:
        """Get summary statistics for the collection: total movies, formats, watch coverage, and ratings."""
        return _default_client.get_collection_stats()

    @mcp.tool()
    def rate_movie(title_id: int, rating: Optional[float]) -> Dict[str, Any]:
        """Set, update, or clear a movie's personal rating (0.0 to 10.0 in 0.5 steps, or null/None to clear)."""
        return _default_client.rate_movie(title_id, rating)

    @mcp.tool()
    def log_watch(
        title_id: int,
        watched_date: Optional[str] = None,
        notes: Optional[str] = None,
        watched_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a watch event with an optional ISO date (YYYY-MM-DD; omit or pass null for dateless/historical watch) and optional notes."""
        return _default_client.log_watch(title_id, watched_date=watched_date, notes=notes, watched_at=watched_at)

    @mcp.tool()
    def search_tmdb(query: str) -> List[Dict[str, Any]]:
        """Search TMDB for movies not yet in the collection (or to add additional copies)."""
        return _default_client.search_tmdb(query)

    @mcp.tool()
    def add_movie(
        tmdb_id: int,
        kind: str = "physical",
        format: str = "Blu-ray",
        service: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import a movie from TMDB into the collection with copy details (kind='physical'|'digital')."""
        return _default_client.add_movie(tmdb_id, kind=kind, format=format, service=service)

    @mcp.resource("matinee://stats")
    def resource_stats() -> str:
        """Collection statistics summary."""
        stats = _default_client.get_collection_stats()
        return json.dumps(stats, indent=2)

    @mcp.resource("matinee://recommendations")
    def resource_recommendations() -> str:
        """Current top recommended movies from the collection."""
        recs = _default_client.get_recommendations(mode="forgotten_gems", limit=5)
        return json.dumps(recs, indent=2)

    @mcp.prompt("what_to_watch")
    def prompt_what_to_watch(mood: str = "", max_runtime: int = 0) -> str:
        """Suggest a curated movie night lineup based on mood, genre, or time constraints."""
        instructions = [
            "You are helping the family choose a movie to watch tonight from their personal collection.",
            "1. Use `get_recommendations` or `search_collection` to check available movies.",
        ]
        if mood:
            instructions.append(f"2. Prioritize movies matching the mood or genre '{mood}'.")
        if max_runtime > 0:
            instructions.append(f"3. Ensure suggested movies do not exceed {max_runtime} minutes.")
        instructions.append("4. Present 2-3 distinct choices with format (e.g. 4K UHD vs Blu-ray) and reason why they fit.")
        return "\n".join(instructions)

    @mcp.prompt("log_movie_night")
    def prompt_log_movie_night(movie_title: str = "") -> str:
        """Prompt to help log and rate a movie that was just watched."""
        return (
            f"Let's log the movie that was just watched: '{movie_title or 'movie'}'.\n"
            "1. Use `search_collection` to find the exact movie ID.\n"
            "2. Ask the user for their rating (0.0 to 10.0 in 0.5 steps) and any notes/impressions.\n"
            "3. Use `rate_movie` and `log_watch` to update Matinee."
        )


def main():
    if not mcp:
        print("MCP SDK not installed. Please install with: pip install mcp")
        return
    mcp.run()


if __name__ == "__main__":
    main()
