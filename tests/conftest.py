import os
import tempfile
from pathlib import Path

# Ensure DATA_DIR is isolated to a clean temp directory during pytest collection
if "DATA_DIR" not in os.environ or not os.environ["DATA_DIR"]:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="matinee-test-conftest-")


FIXTURES_TMDB = Path(__file__).parent / "fixtures" / "tmdb"


def seed_title(con, tmdb_id, *, copies=("Blu-ray",), digital=(), watched=(), rating=None):
    """Insert a title from tests/fixtures/tmdb/movie-<id>.json with physical copies (and optional `digital` copies),
    watches (None = dateless) and an optional personal rating; returns the title id."""
    import json
    import app

    d = json.loads((FIXTURES_TMDB / f"movie-{tmdb_id}.json").read_text())
    title_id = app.write_title(con, app.title_from_tmdb(d), {})
    for fmt in copies:
        con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'physical', ?)", (title_id, fmt))
    for fmt in digital:
        con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'digital', ?)", (title_id, fmt))
    for day in watched:
        con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, ?)", (title_id, day))
    if rating is not None:
        con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (?, ?)", (title_id, rating))
    con.commit()
    return title_id
