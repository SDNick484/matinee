"""Custom cover upload: an editor replaces the TMDB poster with their own image."""
import io
import json
import sqlite3
from pathlib import Path

import pytest

import app as matinee

FIX = Path(__file__).resolve().parent / "fixtures" / "tmdb"

# Smallest valid-looking headers for each accepted type (the server sniffs magic bytes only).
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(matinee, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(matinee, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee, "LAN_EDIT", True)
    monkeypatch.setattr(matinee, "PUBLIC_VIEW", True)
    matinee.init_db()
    with matinee.app.test_client() as c:
        yield c


@pytest.fixture
def paddington(client):
    """Paddington 2 (TMDB 346648) with a Blu-ray copy and a cached TMDB poster."""
    con = matinee.connect()
    con.execute("""INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, poster_path,
                   poster_file, fetched_at) VALUES (346648, 'Paddington 2', 'paddington 2',
                   'paddington 2', 2017, '/p2.jpg', '346648-p342.jpg', '2026-11-01T00:00:00')""")
    tid = con.execute("SELECT id FROM titles WHERE tmdb_id=346648").fetchone()[0]
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'physical', 'Blu-ray')", (tid,))
    con.commit()
    con.close()
    (matinee.IMAGE_DIR / "346648-p342.jpg").write_bytes(b"\xff\xd8tmdb")
    return tid


def upload(c, tid, data, name="steelbook.jpg"):
    return c.post(f"/admin/api/titles/{tid}/poster",
                  data={"poster": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def custom_files():
    return sorted(p.name for p in matinee.IMAGE_DIR.glob("*-custom-*"))


def test_upload_jpeg_sets_custom_poster_everywhere(client, paddington):
    r = upload(client, paddington, JPEG)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["custom_poster"] is True
    files = custom_files()
    assert len(files) == 1 and files[0].startswith("346648-custom-") and files[0].endswith(".jpg")
    assert (matinee.IMAGE_DIR / files[0]).read_bytes() == JPEG
    assert body["poster_url"].startswith(f"/images/{files[0]}")

    detail = client.get(f"/api/titles/{paddington}").get_json()
    assert detail["custom_poster"] is True
    assert detail["poster_url"].startswith(f"/images/{files[0]}")
    assert detail["poster_large_url"].startswith(f"/images/{files[0]}")

    card = client.get("/api/titles").get_json()["titles"][0]
    assert card["poster_url"].startswith(f"/images/{files[0]}")
    assert client.get(detail["poster_url"]).data == JPEG


@pytest.mark.parametrize("data, ext", [(PNG, ".png"), (WEBP, ".webp")])
def test_png_and_webp_accepted(client, paddington, data, ext):
    assert upload(client, paddington, data, "cover" + ext).status_code == 200
    assert custom_files()[0].endswith(ext)


@pytest.mark.parametrize("data", [b"%PDF-1.7 not an image", b"just text", b""])
def test_non_images_rejected(client, paddington, data):
    r = upload(client, paddington, data, "notes.pdf")
    assert r.status_code == 400
    assert "JPEG, PNG or WebP" in r.get_json()["error"]
    assert custom_files() == []
    assert client.get(f"/api/titles/{paddington}").get_json()["custom_poster"] is False


def test_missing_field_rejected(client, paddington):
    r = client.post(f"/admin/api/titles/{paddington}/poster", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_oversize_rejected(client, paddington):
    big = JPEG + b"\x00" * (matinee.MAX_POSTER_BYTES + 1)
    assert upload(client, paddington, big).status_code == 413
    assert custom_files() == []


def test_unknown_title_404(client):
    assert upload(client, 9999, JPEG).status_code == 404


def test_requires_editor(client, paddington, monkeypatch):
    monkeypatch.setattr(matinee, "LAN_EDIT", False)
    assert upload(client, paddington, JPEG).status_code == 401
    assert client.delete(f"/admin/api/titles/{paddington}/poster").status_code == 401


def test_new_upload_replaces_previous_file(client, paddington):
    upload(client, paddington, JPEG)
    first = custom_files()
    upload(client, paddington, PNG, "other.png")
    second = custom_files()
    assert len(second) == 1 and second != first


def test_revert_restores_tmdb_poster_and_removes_file(client, paddington):
    upload(client, paddington, JPEG)
    r = client.delete(f"/admin/api/titles/{paddington}/poster")
    assert r.status_code == 200
    body = r.get_json()
    assert body["custom_poster"] is False
    assert body["poster_url"].startswith("/images/346648-p342.jpg")
    assert custom_files() == []
    assert (matinee.IMAGE_DIR / "346648-p342.jpg").exists()


def test_refresh_keeps_custom_poster(client, paddington, monkeypatch):
    upload(client, paddington, JPEG)
    movie = json.loads((FIX / "movie-194.json").read_text())
    movie["id"] = 346648
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "test-token")
    monkeypatch.setattr(matinee, "tmdb_get", lambda path, **params: movie)
    monkeypatch.setattr(matinee, "http_bytes", lambda url, timeout=15: b"\xff\xd8refetched")
    assert client.post(f"/admin/api/titles/{paddington}/refresh").status_code == 200
    detail = client.get(f"/api/titles/{paddington}").get_json()
    assert detail["custom_poster"] is True
    assert "-custom-" in detail["poster_url"]
    assert len(custom_files()) == 1


def test_delete_title_removes_custom_file(client, paddington):
    upload(client, paddington, JPEG)
    assert client.delete(f"/admin/api/titles/{paddington}").status_code == 200
    assert custom_files() == []


def test_migration_adds_column_to_v5_idempotently(tmp_path):
    con = sqlite3.connect(tmp_path / "v5.db")
    con.row_factory = sqlite3.Row
    con.executescript(matinee.SCHEMA)
    # Simulate a v5 database: titles without the new column.
    con.executescript("""
      DROP TABLE titles;
      CREATE TABLE titles (id INTEGER PRIMARY KEY, tmdb_id INTEGER NOT NULL UNIQUE, title TEXT NOT NULL,
        sort_title TEXT NOT NULL, search_text TEXT NOT NULL, poster_file TEXT, fetched_at TEXT NOT NULL);
      INSERT INTO titles (tmdb_id, title, sort_title, search_text, fetched_at)
        VALUES (346648, 'Paddington 2', 'paddington 2', 'paddington 2', '2026-11-01');
    """)
    matinee.migrate(con)
    matinee.migrate(con)  # second run is a no-op
    cols = [r[1] for r in con.execute("PRAGMA table_info(titles)")]
    assert cols.count("custom_poster_file") == 1
    assert con.execute("SELECT custom_poster_file FROM titles").fetchone()[0] is None
    con.close()
