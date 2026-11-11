"""ops/export_csv.py: the same CSV as GET /admin/api/export.csv, for cron.

Scenario: The Dark Knight (TMDB 155) with a 4K UHD disc and a Digital HD copy on Movies Anywhere, plus Amelie.
"""
import datetime as dt
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as matinee
from app import db, init_db
from conftest import seed_title
from ops import export_csv as cli


@pytest.fixture
def dbfile(tmp_path, monkeypatch):
    path = tmp_path / "data" / "matinee.db"
    path.parent.mkdir()
    monkeypatch.setattr("app.DB_PATH", path)
    monkeypatch.setattr("app.DATA_DIR", path.parent)
    monkeypatch.setattr("app._standalone_db", None)
    init_db()
    con = db()
    seed_title(con, 155, copies=("4K UHD",), digital=("Digital HD",), rating=9.5)
    seed_title(con, 194)
    return path


def expected_body(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return matinee.export_csv(con)[0]
    finally:
        con.close()


def test_stdout_is_the_same_csv_as_the_route(dbfile, capsysbinary):
    assert cli.main(["--db-path", str(dbfile)]) == 0
    out = capsysbinary.readouterr().out
    assert out == expected_body(dbfile)
    assert out.startswith(b"\xef\xbb\xbf" + b"title,year,tmdb_id")
    assert out.decode("utf-8-sig").count("\r\n") == 4          # header + Amelie + two Dark Knight copies


def test_output_directory_gets_a_dated_file(dbfile, tmp_path):
    out_dir = tmp_path / "exports"
    out_dir.mkdir()
    assert cli.main(["--db-path", str(dbfile), "-o", str(out_dir)]) == 0
    name = f"matinee-{dt.date.today().isoformat()}.csv"
    assert [p.name for p in out_dir.iterdir()] == [name]       # no temp file left behind
    f = out_dir / name
    assert f.read_bytes() == expected_body(dbfile)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert "Amélie" in f.read_text(encoding="utf-8-sig")


def test_output_file_is_replaced_atomically(dbfile, tmp_path):
    target = tmp_path / "movies.csv"
    target.write_text("stale")
    assert cli.main(["--db-path", str(dbfile), "-o", str(target)]) == 0
    assert target.read_bytes() == expected_body(dbfile)
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["movies.csv"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_missing_database_exits_1_and_creates_nothing(tmp_path, capsys):
    missing = tmp_path / "nope.db"
    assert cli.main(["--db-path", str(missing), "-o", str(tmp_path / "out.csv")]) == 1
    assert "not found" in capsys.readouterr().err
    assert not missing.exists() and not (tmp_path / "out.csv").exists()


def test_database_is_opened_read_only(dbfile):
    con = cli.open_readonly(dbfile)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM copies")
    finally:
        con.close()


def test_failed_write_leaves_no_partial_file(dbfile, tmp_path, monkeypatch):
    target = tmp_path / "movies.csv"
    target.write_text("keep me")

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        cli.main(["--db-path", str(dbfile), "-o", str(target)])
    assert target.read_text() == "keep me"
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["movies.csv"]
