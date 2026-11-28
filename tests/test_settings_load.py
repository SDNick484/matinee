"""settings.py at import: an unreadable config file stops the start with one line naming the file (it must not fall
back to the open defaults LAN_EDIT=1 and PUBLIC_VIEW=1), and a LAN_HOSTS entry that cannot be a host name warns.

Each case runs settings.py as a copy in a temp directory, so its APP_DIR/.env is the temp file and nothing here
patches the module (the app's tests read and patch app.X only)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app as matinee

SETTINGS = Path(matinee.APP_DIR) / "settings.py"
ROOT_CAN_READ_ANYTHING = hasattr(os, "geteuid") and os.geteuid() == 0


def run_settings(tmp_path, env_text=None, env_extra=None):
    """Import a copy of settings.py from tmp_path, with tmp_path/.env written when env_text is given."""
    shutil.copy(SETTINGS, tmp_path / "settings.py")
    env_file = tmp_path / ".env"
    if env_text is not None:
        env_file.write_text(env_text, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("LAN_HOSTS", "DATA_DIR", "PUBLIC_VIEW", "LAN_EDIT")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-c", "import settings"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60), env_file


@pytest.mark.skipif(ROOT_CAN_READ_ANYTHING, reason="root reads a chmod 000 file, so it cannot be made unreadable")
def test_an_unreadable_config_file_stops_the_start_and_names_the_file(tmp_path):
    proc, env_file = run_settings(tmp_path, env_text="PUBLIC_VIEW=0\n")
    assert proc.returncode == 0
    env_file.chmod(0)
    try:
        proc, _ = run_settings(tmp_path)
    finally:
        env_file.chmod(0o600)
    assert proc.returncode != 0
    assert str(env_file) in proc.stderr
    assert "cannot read" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1


def test_a_config_file_that_is_not_utf8_stops_the_start(tmp_path):
    shutil.copy(SETTINGS, tmp_path / "settings.py")
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"PORT=8771\n\xff\xfe\x00garbage\n")
    proc = subprocess.run([sys.executable, "-c", "import settings"], cwd=tmp_path, capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode != 0
    assert str(env_file) in proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_missing_config_file_is_fine_for_development(tmp_path):
    proc, env_file = run_settings(tmp_path)
    assert not env_file.exists()
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""


def test_a_readable_config_file_fills_in_unset_settings(tmp_path):
    proc, _ = run_settings(tmp_path, env_text='export PUBLIC_VIEW="0"\n# a comment\nLAN_EDIT=0\n')
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""


def test_a_lan_hosts_entry_that_cannot_be_a_host_name_warns(tmp_path):
    proc, _ = run_settings(tmp_path, env_extra={"LAN_HOSTS": "1,matinee.lan,192.168.1.100"})
    assert proc.returncode == 0, proc.stderr
    assert "LAN_HOSTS entry '1' is not a host name" in proc.stderr
    assert "'192.168.1.100' is not a host name" in proc.stderr
    assert "matinee.lan" not in proc.stderr


def test_valid_lan_hosts_entries_do_not_warn(tmp_path):
    proc, _ = run_settings(tmp_path, env_extra={"LAN_HOSTS": "Matinee.LAN, movies.home.arpa, pi-4b.lan"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""


@pytest.mark.skipif(ROOT_CAN_READ_ANYTHING, reason="root reads a chmod 000 file, so it cannot be made unreadable")
def test_an_unreadable_config_file_is_fine_when_the_service_exported_it(tmp_path):
    """The installed layout: /etc/conf.d/matinee is root-only, OpenRC reads it as root and exports the settings,
    and gunicorn runs as the matinee user, which cannot read the file. The init script says so with
    MATINEE_CONF_EXPORTED=1, and the settings then come from the environment."""
    proc, env_file = run_settings(tmp_path, env_text="PUBLIC_VIEW=0\n")
    env_file.chmod(0)
    try:
        shutil.copy(SETTINGS, tmp_path / "settings.py")
        env = dict(os.environ, MATINEE_CONF_EXPORTED="1", PUBLIC_VIEW="0", LAN_EDIT="0")
        proc = subprocess.run([sys.executable, "-c", "import settings; print(settings.PUBLIC_VIEW, settings.LAN_EDIT)"],
                              cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    finally:
        env_file.chmod(0o600)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["False", "False"]


@pytest.mark.skipif(ROOT_CAN_READ_ANYTHING, reason="root reads a chmod 000 file, so it cannot be made unreadable")
def test_the_marker_does_not_excuse_a_file_that_is_readable_but_broken(tmp_path):
    shutil.copy(SETTINGS, tmp_path / "settings.py")
    (tmp_path / ".env").write_bytes(b"PORT=8771\n\xff\xfe\x00garbage\n")
    proc = subprocess.run([sys.executable, "-c", "import settings"], cwd=tmp_path,
                          env=dict(os.environ, MATINEE_CONF_EXPORTED="1"), capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
