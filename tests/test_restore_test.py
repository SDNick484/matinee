"""ops/matinee-restore-test: unpack the newest backup, check it, log the result, call the notifier on failure.

Runs the real shell script against backups built in a temp folder (no /var/backups, no /var/log)."""
import gzip
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ops" / "matinee-restore-test"

pytestmark = pytest.mark.skipif(shutil.which("sqlite3") is None, reason="needs the sqlite3 command-line tool")


def make_db(path, titles=2, copies=3):
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE titles (id INTEGER PRIMARY KEY, title TEXT);"
                      "CREATE TABLE copies (id INTEGER PRIMARY KEY, title_id INTEGER, format TEXT);")
    for i in range(titles):
        con.execute("INSERT INTO titles (title) VALUES (?)", (["The Dark Knight", "Inside Out", "Paddington 2"][i % 3],))
    for i in range(copies):
        con.execute("INSERT INTO copies (title_id, format) VALUES (1, 'Blu-ray')")
    con.commit()
    con.close()


def backup(local_dir, day, **kw):
    """<local_dir>/<day>/matinee-matinee.db.gz, like ops/matinee-backup writes it."""
    d = local_dir / day
    d.mkdir(parents=True)
    raw = d / "matinee-matinee.db"
    make_db(raw, **kw)
    with open(raw, "rb") as src, gzip.open(str(raw) + ".gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    raw.unlink()
    return d / "matinee-matinee.db.gz"


@pytest.fixture
def env(tmp_path):
    local = tmp_path / "backups"
    local.mkdir()
    notify = tmp_path / "notify"
    notify.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/notified\n')
    notify.chmod(0o755)
    log = tmp_path / "ops.log"
    e = dict(os.environ, LOCAL_DIR=str(local), LOG=str(log), NOTIFY=str(notify), OPS_CONF=str(tmp_path / "no-ops-conf"),
             TMPDIR=str(tmp_path))
    return local, log, tmp_path / "notified", e, tmp_path


def run(e):
    return subprocess.run(["sh", str(SCRIPT)], env=e, capture_output=True, text=True)


def test_script_is_valid_sh_and_executable():
    subprocess.run(["sh", "-n", str(SCRIPT)], check=True)
    assert os.access(SCRIPT, os.X_OK)


def test_good_backup_passes_and_logs_counts(env):
    local, log, notified, e, _ = env
    backup(local, "2026-11-10", titles=2, copies=3)
    r = run(e)
    assert r.returncode == 0, r.stderr
    line = log.read_text().strip()
    assert "matinee-restore-test: ok 2026-11-10: integrity ok, 2 titles, 3 copies" in line
    assert not notified.exists()


def test_newest_backup_is_the_one_checked(env):
    local, log, _, e, _ = env
    backup(local, "2026-11-08", titles=1, copies=1)
    backup(local, "2026-11-10", titles=3, copies=4)
    assert run(e).returncode == 0
    assert "2026-11-10: integrity ok, 3 titles, 4 copies" in log.read_text()


def test_folder_without_a_db_backup_is_skipped(env):
    local, log, _, e, _ = env
    backup(local, "2026-11-09", titles=2, copies=2)
    (local / "2026-11-10").mkdir()      # a newer folder where the backup failed
    assert run(e).returncode == 0
    assert "2026-11-09" in log.read_text()


def test_custom_covers_are_counted(env):
    local, log, _, e, tmp = env
    gz = backup(local, "2026-11-10")
    (tmp / "155-custom-0123456789.jpg").write_bytes(b"\xff\xd8x")
    subprocess.run(["tar", "-czf", str(gz.parent / "matinee-custom-covers.tar.gz"), "-C", str(tmp),
                    "155-custom-0123456789.jpg"], check=True)
    assert run(e).returncode == 0
    assert "1 custom covers" in log.read_text()


def test_no_backups_fails_and_notifies(env):
    _, log, notified, e, _ = env
    r = run(e)
    assert r.returncode == 1
    assert "FAILED: no matinee-matinee.db.gz found" in log.read_text()
    assert notified.read_text().startswith("Matinee restore test down")


def test_stale_backup_fails(env):
    local, log, notified, e, _ = env
    gz = backup(local, "2026-11-01")
    old = time.time() - 5 * 86400
    os.utime(gz, (old, old))
    assert run(e).returncode == 1
    assert "hours old" in log.read_text() and notified.exists()


def test_stale_limit_is_configurable(env):
    local, _, _, e, _ = env
    gz = backup(local, "2026-11-01")
    old = time.time() - 5 * 86400
    os.utime(gz, (old, old))
    assert run(dict(e, MAX_AGE_HOURS="240")).returncode == 0


def test_not_gzip_fails(env):
    local, log, notified, e, _ = env
    d = local / "2026-11-10"
    d.mkdir()
    (d / "matinee-matinee.db.gz").write_bytes(b"this is not gzip")
    assert run(e).returncode == 1
    assert "does not gunzip" in log.read_text() and notified.exists()


def test_corrupt_database_fails_integrity_check(env):
    local, log, notified, e, _ = env
    gz = backup(local, "2026-11-10", titles=300, copies=300)
    raw = bytearray(gzip.decompress(gz.read_bytes()))
    for i in range(4096 * 2, len(raw) - 4096, 97):    # scribble over table pages, keep the header intact
        raw[i] = (raw[i] + 91) % 256
    gz.write_bytes(gzip.compress(bytes(raw)))
    assert run(e).returncode == 1
    assert "integrity_check on" in log.read_text() and notified.exists()


def test_database_without_titles_table_fails(env):
    local, log, notified, e, _ = env
    d = local / "2026-11-10"
    d.mkdir()
    raw = d / "matinee-matinee.db"
    con = sqlite3.connect(raw)
    con.execute("CREATE TABLE other (id INTEGER)")
    con.commit()
    con.close()
    (d / "matinee-matinee.db.gz").write_bytes(gzip.compress(raw.read_bytes()))
    raw.unlink()
    assert run(e).returncode == 1
    assert "no readable titles table" in log.read_text() and notified.exists()


def test_empty_gz_fails(env):
    local, log, _, e, _ = env
    d = local / "2026-11-10"
    d.mkdir()
    (d / "matinee-matinee.db.gz").write_bytes(gzip.compress(b""))
    assert run(e).returncode == 1
    assert "is empty" in log.read_text()


def test_temp_files_are_cleaned_up(env):
    local, _, _, e, tmp = env
    backup(local, "2026-11-10")
    run(e)
    assert not list(tmp.glob("matinee-restore-test.*"))
