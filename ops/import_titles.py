#!/usr/bin/env python3
"""CLI bulk import utility for Matinee.
Imports a list of movies (text file with one title per line, or CSV) into Matinee.

Usage:
  python3 ops/import_titles.py movies_anywhere.txt
  python3 ops/import_titles.py movies.txt --format "Digital HD" --service "Movies Anywhere"
  python3 ops/import_titles.py collection.csv --dry-run
"""
import argparse, csv, datetime as dt, os, re, sys, time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import app as matinee


def next_year():
    return dt.date.today().year + 1


def parse_line(line):
    """Parse a single text line into {query, year, format, service}."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Check for (Year) or [Year]
    m = re.match(r"^(.*?)\s*[\(\[]\s*(\d{4})\s*[\)\]]$", line)
    if m:
        title, year_str = m.group(1).strip(), m.group(2)
        y = int(year_str)
        if 1888 <= y <= next_year():
            return {"query": title, "year": y, "format": None, "service": None}

    # Check for trailing year without parens
    m = re.match(r"^(.*?)\s+(\d{4})$", line)
    if m:
        title, year_str = m.group(1).strip(), m.group(2)
        y = int(year_str)
        if 1888 <= y <= next_year():
            return {"query": title, "year": y, "format": None, "service": None}

    return {"query": line, "year": None, "format": None, "service": None}


def parse_input_file(path):
    """Parse text file or CSV into list of entry dictionaries."""
    p = Path(path)
    entries = []

    # Check if CSV
    is_csv = p.suffix.lower() == ".csv"
    if is_csv:
        with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            # Find title column
            title_col = None
            for col in reader.fieldnames or []:
                if col.strip().lower() in ("title", "movie", "name", "movie title"):
                    title_col = col
                    break

            if not title_col:
                # Fallback to plain line reading if header has no title column
                f.seek(0)
                for line in f:
                    parsed = parse_line(line)
                    if parsed:
                        entries.append(parsed)
                return entries

            year_col = next((c for c in reader.fieldnames if c.strip().lower() in ("year", "release year")), None)
            fmt_col = next((c for c in reader.fieldnames if c.strip().lower() in ("format", "fmt")), None)
            srv_col = next((c for c in reader.fieldnames if c.strip().lower() in ("service", "store", "platform")), None)

            for row in reader:
                t = (row.get(title_col) or "").strip()
                if not t:
                    continue
                y = None
                if year_col and row.get(year_col):
                    try:
                        y = int(row[year_col].strip())
                    except ValueError:
                        pass
                fmt = (row.get(fmt_col) or "").strip() or None
                srv = (row.get(srv_col) or "").strip() or None
                entries.append({"query": t, "year": y, "format": fmt, "service": srv})
        return entries

    # Plain text file
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parsed = parse_line(line)
            if parsed:
                entries.append(parsed)
    return entries


def import_titles(file_path, kind="digital", default_format="Digital HD",
                  default_service="Movies Anywhere", unresolved_path="unresolved.txt",
                  dry_run=False, delay=0.15, tmdb_token=None):
    """Import movies from file into Matinee catalog."""
    with matinee.app.app_context():
        return _import_titles_impl(
            file_path,
            kind=kind,
            default_format=default_format,
            default_service=default_service,
            unresolved_path=unresolved_path,
            dry_run=dry_run,
            delay=delay,
            tmdb_token=tmdb_token,
        )


def _import_titles_impl(file_path, kind="digital", default_format="Digital HD",
                        default_service="Movies Anywhere", unresolved_path="unresolved.txt",
                        dry_run=False, delay=0.15, tmdb_token=None):
    if tmdb_token:
        matinee.TMDB_READ_TOKEN = tmdb_token.strip()

    entries = parse_input_file(file_path)
    total = len(entries)
    print(f"Loaded {total} movies from {file_path}")

    unresolved_list = []
    stats = {"total": total, "added": 0, "skipped": 0, "unresolved": 0}

    con = matinee.connect()
    matinee.init_db()

    for idx, entry in enumerate(entries, 1):
        q = entry["query"]
        if entry["year"]:
            search_query = f"{q} {entry['year']}"
        else:
            search_query = q

        fmt = entry["format"] or default_format
        srv = (entry["service"] or default_service) if kind == "digital" else None

        # Validate copy format
        copy_data = {"kind": kind, "format": fmt, "service": srv}
        try:
            validated_vals = matinee.copy_values(copy_data)
        except Exception as e:
            print(f"[{idx}/{total}] ERROR '{q}': {e}")
            unresolved_list.append(f"{q} -> Invalid format/service: {e}\n")
            stats["unresolved"] += 1
            continue

        # TMDB Search
        try:
            results = matinee.tmdb_search(search_query)
        except Exception as e:
            print(f"[{idx}/{total}] TMDB search error for '{q}': {e}")
            unresolved_list.append(f"{q} -> TMDB Search Error: {e}\n")
            stats["unresolved"] += 1
            continue

        if not results:
            print(f"[{idx}/{total}] NOT FOUND on TMDB: '{q}'")
            unresolved_list.append(f"{q} -> No TMDB match found\n")
            stats["unresolved"] += 1
            continue

        top = results[0]
        tmdb_id = top["tmdb_id"]
        movie_title = top["title"]
        movie_year = top.get("year") or ""

        # Check if this exact copy is already owned
        existing_copy = con.execute(
            """SELECT c.id FROM copies c
               JOIN titles t ON t.id = c.title_id
               WHERE t.tmdb_id = ? AND c.kind = ? AND c.format = ? AND (c.service = ? OR (c.service IS NULL AND ? IS NULL))""",
            (tmdb_id, validated_vals["kind"], validated_vals["format"], validated_vals["service"], validated_vals["service"])
        ).fetchone()

        if existing_copy:
            srv_str = f" · {srv}" if srv else ""
            print(f"[{idx}/{total}] ALREADY OWNED: {movie_title} ({movie_year}) -> {fmt}{srv_str}")
            stats["skipped"] += 1
            continue

        # Add Title & Copy
        if dry_run:
            srv_str = f" · {srv}" if srv else ""
            print(f"[{idx}/{total}] [DRY RUN] Would add: {movie_title} ({movie_year}) -> {fmt}{srv_str}")
            stats["added"] += 1
        else:
            try:
                row = con.execute("SELECT id FROM titles WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
                if row:
                    title_id = row["id"]
                else:
                    details = matinee.tmdb_details(tmdb_id)
                    files = matinee.fetch_images(details)
                    title_id = matinee.write_title(con, details, files)

                con.execute(
                    "INSERT INTO copies (title_id, kind, format, service, added_by) VALUES (?,?,?,?,?)",
                    (title_id, validated_vals["kind"], validated_vals["format"], validated_vals["service"], "cli-import")
                )
                con.commit()
                srv_str = f" · {srv}" if srv else ""
                print(f"[{idx}/{total}] ADDED: {movie_title} ({movie_year}) -> {fmt}{srv_str}")
                stats["added"] += 1
            except Exception as e:
                print(f"[{idx}/{total}] FAILED to save '{movie_title}': {e}")
                unresolved_list.append(f"{q} -> Save Error: {e}\n")
                stats["unresolved"] += 1

        if delay > 0 and idx < total:
            time.sleep(delay)

    con.close()

    # Write unresolved list if any
    if unresolved_list and unresolved_path:
        with open(unresolved_path, "w", encoding="utf-8") as f:
            f.writelines(unresolved_list)
        print(f"\nWrote {len(unresolved_list)} unmatched/failed titles to: {unresolved_path}")

    print("\nImport Summary:")
    print(f"  Total processed : {stats['total']}")
    print(f"  Added           : {stats['added']}" + (" (dry run)" if dry_run else ""))
    print(f"  Already owned   : {stats['skipped']}")
    print(f"  Unresolved      : {stats['unresolved']}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Bulk import movie titles into Matinee")
    parser.add_argument("file", help="Path to text file (one title per line) or CSV file")
    parser.add_argument("--kind", choices=["physical", "digital"], default="digital",
                        help="Default copy kind (default: digital)")
    parser.add_argument("--format", default="Digital HD",
                        help="Default copy format (e.g. 'Digital HD', 'Digital 4K', 'Blu-ray', 'DVD')")
    parser.add_argument("--service", default="Movies Anywhere",
                        help="Default digital service (e.g. 'Movies Anywhere', 'Apple TV')")
    parser.add_argument("--unresolved", default="unresolved.txt",
                        help="Path to save unmatched/ambiguous titles (default: unresolved.txt)")
    parser.add_argument("--delay", type=float, default=0.15,
                        help="Delay in seconds between TMDB requests (default: 0.15)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Perform search and show what would be added without writing to database")
    parser.add_argument("--tmdb-token", default=None,
                        help="TMDB API Read Access Token (overrides TMDB_READ_TOKEN env/conf)")

    args = parser.parse_args()

    if args.tmdb_token:
        matinee.TMDB_READ_TOKEN = args.tmdb_token.strip()

    if not matinee.TMDB_READ_TOKEN:
        print("\nERROR: TMDB_READ_TOKEN is not set in the environment or in /etc/conf.d/matinee.")
        print("To load it from /etc/conf.d/matinee, run:")
        print("  set -a; . /etc/conf.d/matinee; set +a")
        print("Or pass it directly:")
        print("  python3 ops/import_titles.py <file> --tmdb-token \"<token>\"\n")
        sys.exit(1)

    if not Path(args.file).exists():
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    import_titles(
        args.file,
        kind=args.kind,
        default_format=args.format,
        default_service=args.service,
        unresolved_path=args.unresolved,
        dry_run=args.dry_run,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
