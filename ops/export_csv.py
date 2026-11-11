#!/usr/bin/env python3
"""Export the Matinee collection as CSV (one row per copy), for cron or a one-off backup.

The same file GET /admin/api/export.csv serves: UTF-8 with a BOM, RFC 4180 quoting, CRLF. Opens the database
read-only, so it is safe beside the running service. Writes atomically with mode 0600.

Usage:
  python3 ops/export_csv.py                              # CSV to stdout
  python3 ops/export_csv.py -o /var/backups/matinee/     # writes matinee-YYYY-MM-DD.csv in that directory
  python3 ops/export_csv.py -o movies.csv --db-path /var/lib/matinee/matinee.db

Exit codes: 0 written, 1 database not found.
"""
import argparse, os, sqlite3, sys, tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import app as matinee


def open_readonly(db_path):
    con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def write_atomic(path, data):
    """Write `data` to `path` via a temp file in the same directory, then rename. Mode 0600."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".matinee-export-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export the Matinee collection as CSV (one row per copy).")
    parser.add_argument("-o", "--output", default="-",
                        help="file to write, or an existing directory (gets matinee-YYYY-MM-DD.csv); default: stdout")
    parser.add_argument("--db-path", default=None, help="path to matinee.db (default: from config)")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path) if args.db_path else matinee.DB_PATH
    if not db_path.exists():
        print(f"Database not found at {db_path}; exiting.", file=sys.stderr)
        return 1

    con = open_readonly(db_path)
    try:
        body, filename = matinee.export_csv(con)
    finally:
        con.close()

    if args.output == "-":
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()
        return 0
    target = Path(args.output)
    if target.is_dir():
        target = target / filename
    write_atomic(target, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
