import pytest
from app import clean_upc_title, connect, init_db


def test_clean_upc_title_patterns():
    # 4K UHD variations
    title, fmt = clean_upc_title("Oppenheimer [4K Ultra HD + Blu-ray + Digital]")
    assert title == "Oppenheimer"
    assert fmt == "4K UHD"

    title, fmt = clean_upc_title("Spider-Man: Across the Spider-Verse (4K UHD) [Steelbook]")
    assert title == "Spider-Man: Across the Spider-Verse"
    assert fmt == "4K UHD"

    title, fmt = clean_upc_title("The Dark Knight - 4K Ultra HD (2-Disc)")
    assert title == "The Dark Knight"
    assert fmt == "4K UHD"

    # Standard Blu-ray
    title, fmt = clean_upc_title("Inception (Blu-ray + DVD + Digital Copy)")
    assert title == "Inception"
    assert fmt == "Blu-ray"

    # DVD
    title, fmt = clean_upc_title("Gladiator [Widescreen DVD]")
    assert title == "Gladiator"
    assert fmt == "DVD"


def test_upc_cache_schema(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    init_db()
    con = connect()
    cols = [r[1] for r in con.execute("PRAGMA table_info(upc_cache)").fetchall()]
    assert "upc" in cols
    assert "raw_title" in cols
    assert "data" in cols
    con.close()
