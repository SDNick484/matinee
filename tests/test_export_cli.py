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


# ------------------------------------------------------------------------------------- shapes and filters

def rows_in(body):
    import csv
    import io
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"), newline="")))


@pytest.fixture
def shelved(dbfile):
    """The Dark Knight's disc on Family Room > Media Cabinet > Shelf 1, its Digital HD copy on Movies Anywhere."""
    con = db()
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room'), "
                "(2, 1, 'Media Cabinet'), (3, 2, 'Shelf 1')")
    dk = "(SELECT id FROM titles WHERE tmdb_id=155)"
    con.execute(f"UPDATE copies SET location_id=3 WHERE kind='physical' AND title_id={dk}")
    con.execute(f"UPDATE copies SET service='Movies Anywhere' WHERE kind='digital' AND title_id={dk}")
    con.commit()
    return dbfile


def test_title_shape_to_stdout_matches_export_csv(shelved, capsysbinary):
    assert cli.main(["--db-path", str(shelved), "--shape", "title"]) == 0
    out = capsysbinary.readouterr().out
    con = sqlite3.connect(shelved)
    con.row_factory = sqlite3.Row
    assert out == matinee.export_csv(con, shape="title")[0]
    rows = rows_in(out)
    assert [r[0] for r in rows[1:]] == ["Amélie", "The Dark Knight"]
    assert rows[2][6:11] == ["2", "4K UHD; Digital HD", "Movies Anywhere", "Family Room › Media Cabinet › Shelf 1",
                             "4K UHD @ Family Room › Media Cabinet › Shelf 1; Digital HD @ Movies Anywhere"]


def test_filters_keep_only_the_matching_copies(shelved, capsysbinary):
    assert cli.main(["--db-path", str(shelved), "--service", "Movies Anywhere"]) == 0
    assert [(r[0], r[7]) for r in rows_in(capsysbinary.readouterr().out)[1:]] == [("The Dark Knight", "Digital HD")]
    assert cli.main(["--db-path", str(shelved), "--kind", "physical", "--format", "4K UHD",
                     "--location", "Family Room › Media Cabinet"]) == 0
    assert [(r[0], r[7]) for r in rows_in(capsysbinary.readouterr().out)[1:]] == [("The Dark Knight", "4K UHD")]


def test_filters_and_shape_in_the_output_directory_file_name(shelved, tmp_path):
    out_dir = tmp_path / "exports"
    out_dir.mkdir()
    assert cli.main(["--db-path", str(shelved), "-o", str(out_dir), "--shape", "title", "--location", "3"]) == 0
    [f] = out_dir.iterdir()
    assert f.name == f"matinee-{dt.date.today().isoformat()}-titles.csv"
    assert [r[0] for r in rows_in(f.read_bytes())[1:]] == ["The Dark Knight"]


def test_default_run_ignores_the_new_options(shelved, capsysbinary):
    assert cli.main(["--db-path", str(shelved)]) == 0
    assert capsysbinary.readouterr().out == expected_body(shelved)


def test_a_title_starting_with_equals_is_quoted_in_both_shapes(shelved, capsysbinary):
    db().execute("UPDATE titles SET title='=SUM(1)' WHERE tmdb_id=155")
    db().commit()
    for shape in ("copy", "title"):
        assert cli.main(["--db-path", str(shelved), "--shape", shape]) == 0
        titles = [r[0] for r in rows_in(capsysbinary.readouterr().out)[1:]]
        assert "'=SUM(1)" in titles and "=SUM(1)" not in titles


@pytest.mark.parametrize("args, sentence", [
    (["--format", "Betamax"], 'Unknown format "Betamax". Use one of: 4K UHD, Blu-ray,'),
    (["--kind", "laserdisc"], 'Unknown kind "laserdisc". Use one of: physical, digital.'),
    (["--shape", "wide"], 'Unknown shape "wide". Use one of: copy, title.'),
    (["--location", "Attic"], 'Unknown location "Attic".'),
])
def test_bad_values_exit_2_with_the_sentence_and_write_nothing(shelved, tmp_path, capsys, args, sentence):
    target = tmp_path / "out.csv"
    assert cli.main(["--db-path", str(shelved), "-o", str(target), *args]) == 2
    assert sentence in capsys.readouterr().err
    assert not target.exists()
