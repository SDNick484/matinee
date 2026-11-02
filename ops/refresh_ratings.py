#!/usr/bin/env python3
"""Daily background ratings refresh CLI for Matinee.
Refreshes IMDb, Rotten Tomatoes, and Metacritic ratings via OMDb API for titles
whose ratings are stale (older than --stale-days, default 180) or missing.

Usage:
  python3 ops/refresh_ratings.py [--limit 50] [--stale-days 180] [--db-path PATH]
"""
import argparse, datetime as dt, os, sqlite3, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import app as matinee


def main():
    parser = argparse.ArgumentParser(description="Refresh stale external ratings from OMDb API.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of titles to refresh (default: 50)")
    parser.add_argument("--stale-days", type=int, default=180, help="Refresh ratings older than N days (default: 180)")
    parser.add_argument("--db-path", type=str, default=None, help="Path to matinee.db (default: from config)")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds to sleep between API requests (default: 0.25)")
    args = parser.parse_args()

    if not matinee.OMDB_API_KEY:
        print(f"[{dt.datetime.now().isoformat()}] OMDb API key not configured; skipping refresh.")
        return 0

    db_path = Path(args.db_path) if args.db_path else matinee.DB_PATH
    if not db_path.exists():
        print(f"[{dt.datetime.now().isoformat()}] Database not found at {db_path}; exiting.", file=sys.stderr)
        return 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    stale_threshold = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.stale_days)).isoformat()

    rows = con.execute("""
        SELECT t.id, t.imdb_id, t.title, r.fetched_at
        FROM titles t
        LEFT JOIN omdb_ratings r ON r.title_id = t.id
        WHERE t.imdb_id IS NOT NULL AND t.imdb_id != ''
          AND (r.fetched_at IS NULL OR r.fetched_at < ?)
        ORDER BY COALESCE(r.fetched_at, '1970-01-01') ASC
        LIMIT ?
    """, (stale_threshold, args.limit)).fetchall()

    if not rows:
        print(f"[{dt.datetime.now().isoformat()}] No titles require ratings refresh.")
        con.close()
        return 0

    print(f"[{dt.datetime.now().isoformat()}] Refreshing up to {len(rows)} titles with stale/missing ratings...")
    refreshed = 0
    skipped = 0
    errors = 0

    for r in rows:
        title_id = r["id"]
        imdb_id = r["imdb_id"]
        title_name = r["title"]
        try:
            ratings = matinee.omdb_get(imdb_id)
            if ratings:
                matinee.write_omdb_ratings(con, title_id, imdb_id, ratings)
                con.commit()
                refreshed += 1
                imdb_str = f"IMDb {ratings.get('imdb_rating') or '-'}"
                rt_str = f"RT {ratings.get('rotten_tomatoes_score') or '-'}%"
                meta_str = f"Meta {ratings.get('metacritic_score') or '-'}"
                print(f"  [OK] {title_name} ({imdb_id}): {imdb_str}, {rt_str}, {meta_str}")
            else:
                skipped += 1
                print(f"  [SKIP] {title_name} ({imdb_id}): no data returned")
        except Exception as e:
            errors += 1
            print(f"  [ERROR] {title_name} ({imdb_id}): {e}", file=sys.stderr)

        if args.delay > 0:
            time.sleep(args.delay)

    con.close()
    print(f"[{dt.datetime.now().isoformat()}] Ratings refresh complete: {refreshed} updated, {skipped} skipped, {errors} errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
