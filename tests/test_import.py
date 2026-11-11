import sys
from pathlib import Path

# Ensure app can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as matinee
from ops.import_titles import parse_line, parse_input_file, import_titles


def test_parse_line():
    assert parse_line("Inception") == {"query": "Inception", "year": None, "format": None, "service": None}
    assert parse_line("Dune (2021)") == {"query": "Dune", "year": 2021, "format": None, "service": None}
    assert parse_line("Toy Story [1995]") == {"query": "Toy Story", "year": 1995, "format": None, "service": None}
    assert parse_line("Elf 2003") == {"query": "Elf", "year": 2003, "format": None, "service": None}
    # 2049 is beyond next year, so it stays in title
    assert parse_line("Blade Runner 2049") == {"query": "Blade Runner 2049", "year": None, "format": None, "service": None}
    assert parse_line("") is None
    assert parse_line("   # Comment line  ") is None


def test_parse_csv_file(tmp_path):
    csv_file = tmp_path / "movies.csv"
    csv_file.write_text("Title,Year,Format,Service\nInception,2010,Digital 4K,Apple TV\nFrozen,2013,Digital HD,Movies Anywhere\n")
    entries = parse_input_file(csv_file)
    assert len(entries) == 2
    assert entries[0] == {"query": "Inception", "year": 2010, "format": "Digital 4K", "service": "Apple TV"}
    assert entries[1] == {"query": "Frozen", "year": 2013, "format": "Digital HD", "service": "Movies Anywhere"}


def test_import_titles(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(matinee, "DB_PATH", db_file)
    monkeypatch.setattr(matinee, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee, "IMAGE_DIR", tmp_path / "images")
    matinee.init_db()

    # Mock search and details using existing fixtures
    def fake_search(q):
        if "godzilla" in q.lower():
            return []
        return [{"tmdb_id": 155, "title": "The Dark Knight", "year": 2008, "owned": False}]

    def fake_details(tmdb_id):
        return {
            "tmdb_id": 155,
            "imdb_id": "tt0468569",
            "title": "The Dark Knight",
            "sort_title": "dark knight",
            "search_text": "the dark knight dark knight",
            "year": 2008,
            "release_date": "2008-07-16",
            "runtime": 152,
            "overview": "Batman raises the stakes.",
            "certification": "PG-13",
            "collection_tmdb_id": None,
            "collection_name": None,
            "tmdb_vote": 8.5,
            "tmdb_votes": 30000,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "genres": [(28, "Action"), (80, "Crime")],
        }

    monkeypatch.setattr(matinee, "tmdb_search", fake_search)
    monkeypatch.setattr(matinee, "tmdb_details", fake_details)
    monkeypatch.setattr(matinee, "fetch_images", lambda t: {"poster_file": "p.jpg", "backdrop_file": "b.jpg"})

    txt_file = tmp_path / "movies.txt"
    txt_file.write_text("The Dark Knight (2008)\nGodzilla 9999\n")
    unresolved_file = tmp_path / "unresolved.txt"

    res = import_titles(
        txt_file,
        kind="digital",
        default_format="Digital HD",
        default_service="Movies Anywhere",
        unresolved_path=unresolved_file,
        dry_run=False,
    )

    assert res["added"] == 1
    assert res["unresolved"] == 1
    assert res["skipped"] == 0

    con = matinee.connect()
    copies = con.execute("SELECT * FROM copies").fetchall()
    assert len(copies) == 1
    assert copies[0]["format"] == "Digital HD"
    assert copies[0]["service"] == "Movies Anywhere"


def test_import_titles_token_argument(tmp_path, monkeypatch):
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "")
    txt_file = tmp_path / "movies.txt"
    txt_file.write_text("Movie (2020)\n")
    # Setting token in import_titles updates matinee.TMDB_READ_TOKEN
    import_titles(txt_file, dry_run=True, tmdb_token="custom_token_123")
    assert matinee.TMDB_READ_TOKEN == "custom_token_123"


def test_import_titles_skip_existing(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(matinee, "DB_PATH", db_file)
    monkeypatch.setattr(matinee, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "fake_token")
    matinee.init_db()

    def fake_search(q):
        if "interstellar" in q.lower():
            return [{"tmdb_id": 157336, "title": "Interstellar", "year": 2014, "owned": False}]
        return [{"tmdb_id": 155, "title": "The Dark Knight", "year": 2008, "owned": False}]

    def fake_details(tmdb_id):
        return {
            "tmdb_id": tmdb_id,
            "title": "Movie",
            "sort_title": "movie",
            "search_text": "movie",
            "year": 2014,
            "release_date": "2014-11-05",
            "runtime": 120,
            "overview": "Overview",
            "certification": "PG-13",
            "collection_tmdb_id": None,
            "collection_name": None,
            "tmdb_vote": 8.0,
            "tmdb_votes": 1000,
            "poster_path": None,
            "backdrop_path": None,
            "genres": [(28, "Action")],
        }

    monkeypatch.setattr(matinee, "tmdb_search", fake_search)
    monkeypatch.setattr(matinee, "tmdb_details", fake_details)
    monkeypatch.setattr(matinee, "fetch_images", lambda t: {"poster_file": None, "backdrop_file": None})

    # Step 1: Add The Dark Knight under Movies Anywhere
    con = matinee.connect()
    t_id = matinee.write_title(con, fake_details(155), {"poster_file": None, "backdrop_file": None})
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (?, 'digital', 'Digital HD', 'Movies Anywhere')", (t_id,))
    con.commit()
    con.close()

    # Step 2: Import file with both The Dark Knight and Interstellar, with skip_existing=True
    txt_file = tmp_path / "vudu.txt"
    txt_file.write_text("The Dark Knight\nInterstellar\n")

    res = import_titles(
        txt_file,
        kind="digital",
        default_format="Digital HD",
        default_service="Fandango at Home",
        skip_existing=True,
    )

    assert res["total"] == 2
    assert res["skipped"] == 1  # The Dark Knight skipped
    assert res["added"] == 1    # Interstellar added

    con = matinee.connect()
    copies = con.execute("SELECT * FROM copies").fetchall()
    assert len(copies) == 2
    services = {c["service"] for c in copies}
    assert services == {"Movies Anywhere", "Fandango at Home"}


