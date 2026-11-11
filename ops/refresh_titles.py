#!/usr/bin/env python3
"""Background TMDB refresh CLI for Matinee.
Re-fetches cached TMDB metadata (details, certification, collection, posters) for titles whose
`fetched_at` is older than --stale-days (default 180), oldest first, at most --limit per run, with a
pause between calls. It reuses the app's own refresh code (app.refresh_from_tmdb), so uploaded covers
and certification overrides survive exactly as they do for the Refresh button.

A TMDB 404 skips that title and leaves its row untouched. A 429 (or TMDB being unreachable) ends the run
early, also leaving rows untouched. Both exit 0, so the nightly job never reports a failure for them.

Usage:
  python3 ops/refresh_titles.py [--limit 25] [--stale-days 180] [--delay 1.0] [--db-path PATH]
"""
import argparse, datetime as dt, sqlite3, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import app as matinee


def stamp():
    return dt.datetime.now().isoformat(timespec="seconds")


def main():
    parser = argparse.ArgumentParser(description="Refresh stale cached TMDB metadata.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum number of titles to refresh (default: 25)")
    parser.add_argument("--stale-days", type=int, default=180, help="Refresh titles fetched more than N days ago (default: 180)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to sleep between TMDB calls (default: 1.0)")
    parser.add_argument("--db-path", type=str, default=None, help="Path to matinee.db (default: from config)")
    args = parser.parse_args()

    if not matinee.TMDB_READ_TOKEN:
        print(f"[{stamp()}] TMDB is not configured; skipping title refresh.")
        return 0

    db_path = Path(args.db_path) if args.db_path else matinee.DB_PATH
    if not db_path.exists():
        print(f"[{stamp()}] Database not found at {db_path}; exiting.", file=sys.stderr)
        return 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")

    # fetched_at is written by app.now_iso() as %Y-%m-%dT%H:%M:%SZ, so a string compare is a time compare
    threshold = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.stale_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = con.execute("SELECT id, tmdb_id, title, year FROM titles WHERE fetched_at < ? ORDER BY fetched_at ASC, id ASC LIMIT ?",
                       (threshold, max(args.limit, 0))).fetchall()
    if not rows:
        print(f"[{stamp()}] No titles need a TMDB refresh.")
        con.close()
        return 0

    print(f"[{stamp()}] Refreshing up to {len(rows)} titles last fetched before {threshold}...")
    refreshed = missing = errors = 0
    stopped = None
    for i, r in enumerate(rows):
        label = f"{r['title']} ({r['year'] or '?'}, tmdb {r['tmdb_id']})"
        try:
            matinee.refresh_from_tmdb(con, r["id"], r["tmdb_id"])
            con.commit()
            refreshed += 1
            print(f"  [OK] {label}")
        except matinee.TmdbError as e:
            con.rollback()
            if e.reason == "not_found":
                missing += 1
                print(f"  [SKIP] {label}: TMDB no longer has this movie; saved details kept")
            else:  # rate_limited / unavailable: later calls would fail the same way
                errors += 1
                stopped = e.reason
                print(f"  [STOP] {label}: {e.message}", file=sys.stderr)
                break
        except Exception as e:  # noqa: BLE001 - one bad title must not end the run
            con.rollback()
            errors += 1
            print(f"  [ERROR] {label}: {e}", file=sys.stderr)
        if args.delay > 0 and i < len(rows) - 1:
            time.sleep(args.delay)

    con.close()
    tail = f"; stopped early ({stopped})" if stopped else ""
    print(f"[{stamp()}] TMDB refresh complete: {refreshed} updated, {missing} not on TMDB, {errors} errors{tail}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
