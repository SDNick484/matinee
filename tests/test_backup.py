"""ops/matinee-backup: private files, one copy per run, no pruning behind a failed run, DATA_DIR from the conf file.

Runs the real shell script against a database built in a temp folder (no /var/lib, /var/backups or /var/log)."""
import gzip
import os
import shutil
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ops" / "matinee-backup"
DAY = time.strftime("%Y-%m-%d")

pytestmark = pytest.mark.skipif(shutil.which("sqlite3") is None, reason="needs the sqlite3 command-line tool")


def make_db(path, titles=2):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE titles (id INTEGER PRIMARY KEY, title TEXT, note TEXT)")
    for i in range(titles):
        con.execute("INSERT INTO titles (title, note) VALUES (?, 'watched with the kids')",
                    (["The Dark Knight", "Inside Out", "Paddington 2"][i % 3],))
    con.commit()
    con.close()


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    (data / "images").mkdir(parents=True)
    make_db(data / "matinee.db")
    conf = tmp_path / "conf-matinee"
    conf.write_text(f"PORT=8771\nDATA_DIR={data}\nTMDB_READ_TOKEN=secret\n")
    local = tmp_path / "backups"
    notify = tmp_path / "notify"
    notify.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/notified\n')
    notify.chmod(0o755)
    e = dict(os.environ, LOCAL_DIR=str(local), LOG=str(tmp_path / "ops.log"), MATINEE_CONF=str(conf),
             OPS_CONF=str(tmp_path / "no-ops-conf"), NOTIFY_CMD=str(notify), TMPDIR=str(tmp_path))
    e.pop("MATINEE_DATA", None)
    return {"data": data, "conf": conf, "local": local, "log": tmp_path / "ops.log",
            "notified": tmp_path / "notified", "env": e, "tmp": tmp_path}


def run(env, *args, **extra):
    return subprocess.run(["sh", str(SCRIPT), *args], env=dict(env["env"], **extra), capture_output=True, text=True)


def copies(env, day=DAY):
    return sorted(p.name for p in (env["local"] / day).glob("matinee-matinee*.db.gz"))


def count(gz):
    raw = gz.parent / "check.db"
    raw.write_bytes(gzip.decompress(gz.read_bytes()))
    n = sqlite3.connect(raw).execute("SELECT COUNT(*) FROM titles").fetchone()[0]
    raw.unlink()
    return n


def test_script_is_valid_sh_and_executable():
    subprocess.run(["sh", "-n", str(SCRIPT)], check=True)
    assert os.access(SCRIPT, os.X_OK)


def test_scheduled_run_writes_the_plain_name_and_logs(env):
    r = run(env)
    assert r.returncode == 0, r.stderr
    assert copies(env) == ["matinee-matinee.db.gz"]
    assert count(env["local"] / DAY / "matinee-matinee.db.gz") == 2
    assert "matinee-backup: ok matinee/matinee.db" in env["log"].read_text()
    assert not env["notified"].exists()
    assert not list((env["local"] / DAY).glob(".*")), "temp files left behind"


def test_files_are_600_and_folders_700(env):
    (env["data"] / "images" / "155-custom-0123456789.jpg").write_bytes(b"\xff\xd8x")
    assert run(env).returncode == 0
    for p in [env["local"], env["local"] / DAY]:
        assert mode(p) == 0o700, p
    for p in (env["local"] / DAY).iterdir():
        assert mode(p) == 0o600, p
    assert (env["local"] / DAY / "matinee-custom-covers.tar.gz").exists()


def test_existing_world_readable_copies_are_tightened(env):
    old = env["local"] / "2026-01-01"
    old.mkdir(parents=True)
    (old / "matinee-matinee.db.gz").write_bytes(b"x")
    os.chmod(env["local"], 0o755)
    os.chmod(old, 0o755)
    os.chmod(old / "matinee-matinee.db.gz", 0o644)
    assert run(env).returncode == 0
    assert mode(env["local"]) == 0o700 and mode(old) == 0o700
    assert mode(old / "matinee-matinee.db.gz") == 0o600


def test_labelled_runs_keep_every_copy_of_the_day(env):
    assert run(env).returncode == 0
    con = sqlite3.connect(env["data"] / "matinee.db")
    con.execute("INSERT INTO titles (title) VALUES ('Paddington 2')")
    con.commit()
    con.close()
    assert run(env, "pre-upgrade").returncode == 0
    assert run(env, "pre-upgrade").returncode == 0      # a second upgrade the same day, even in the same second
    assert run(env).returncode == 0                     # and the next scheduled run
    names = copies(env)
    assert len(names) == 3, names   # the scheduled name, replaced by rename, plus two labelled
    assert "matinee-matinee.db.gz" in names
    labelled = [n for n in names if "pre-upgrade" in n]
    assert len(labelled) == 2 and all(n.startswith("matinee-matinee-pre-upgrade-") for n in labelled)
    # the scheduled run replaced only its own file: it now holds the extra title, the labelled ones too
    assert count(env["local"] / DAY / "matinee-matinee.db.gz") == 3


def test_scheduled_run_never_overwrites_a_labelled_copy(env):
    assert run(env, "pre-upgrade").returncode == 0
    (labelled,) = copies(env)
    before = (env["local"] / DAY / labelled).read_bytes()
    assert run(env).returncode == 0
    assert (env["local"] / DAY / labelled).read_bytes() == before
    assert len(copies(env)) == 2


def test_bad_label_is_refused(env):
    r = run(env, "../escape")
    assert r.returncode == 2
    assert not env["local"].exists() or not copies(env)


def test_failing_sqlite3_keeps_old_copies_and_skips_pruning(env, tmp_path):
    assert run(env).returncode == 0
    good = env["local"] / DAY / "matinee-matinee.db.gz"
    before = good.read_bytes()
    ancient = env["local"] / "2026-01-01"
    ancient.mkdir()
    (ancient / "matinee-matinee.db.gz").write_bytes(b"x")
    old = time.time() - 60 * 86400
    os.utime(ancient, (old, old))
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "sqlite3").write_text("#!/bin/sh\nexit 1\n")
    (fakebin / "sqlite3").chmod(0o755)
    r = run(env, PATH=f"{fakebin}:{os.environ['PATH']}")
    assert r.returncode != 0
    assert good.read_bytes() == before
    assert ancient.exists(), "pruned behind a failed run"
    log = env["log"].read_text()
    assert "FAILED: matinee/matinee.db" in log and "not pruning" in log
    assert env["notified"].read_text().startswith("Matinee backup down")
    assert not list((env["local"] / DAY).glob(".*")), "temp files left behind"


def test_failed_integrity_check_is_not_kept_or_pruned_behind(env, tmp_path):
    ancient = env["local"] / "2026-01-01"
    ancient.mkdir(parents=True)
    old = time.time() - 60 * 86400
    os.utime(ancient, (old, old))
    (env["data"] / "matinee.db").write_bytes(b"this is not a database" * 500)
    r = run(env)
    assert r.returncode != 0
    assert copies(env) == []
    assert ancient.exists()


def test_pruning_after_a_good_run_removes_only_old_folders(env):
    old = env["local"] / "2026-01-01"
    recent = env["local"] / "2026-11-20"
    other = env["local"] / "not-a-date"
    for d in (old, recent, other):
        d.mkdir(parents=True)
        (d / "f").write_text("x")
    t = time.time() - 60 * 86400
    os.utime(old, (t, t))
    os.utime(other, (t, t))
    t2 = time.time() - 2 * 86400
    os.utime(recent, (t2, t2))
    assert run(env, KEEP_DAYS="14").returncode == 0
    assert not old.exists()
    assert recent.exists() and other.exists()
    assert (env["local"] / DAY / "matinee-matinee.db.gz").exists()


def test_custom_data_dir_from_the_conf_file(env, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    make_db(other / "matinee.db", titles=3)
    env["conf"].write_text(f'PORT=8771\nDATA_DIR="{other}"\n')
    assert run(env).returncode == 0
    assert count(env["local"] / DAY / "matinee-matinee.db.gz") == 3


def test_missing_database_fails_loudly(env):
    (env["data"] / "matinee.db").unlink()
    r = run(env)
    assert r.returncode != 0
    assert "FAILED: no database at" in env["log"].read_text()
    assert "no database" in r.stderr
    assert env["notified"].exists()


def test_failure_is_logged_and_exits_nonzero_without_a_notify_tool(env):
    (env["data"] / "matinee.db").unlink()
    r = run(env, NOTIFY_CMD=str(env["tmp"] / "not-installed"))
    assert r.returncode != 0
    assert "FAILED" in env["log"].read_text()
    assert not env["notified"].exists()


def test_backup_target_gets_the_labelled_copy(env, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    assert run(env, "pre-upgrade", BACKUP_TARGET=str(target)).returncode == 0
    (name,) = copies(env)
    assert (target / "db" / DAY / name).exists()
    assert mode(target / "db" / DAY) == 0o700
    assert not list((target / "db" / DAY).glob(".*"))
