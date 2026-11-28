"""Static and shell-level checks on the installer (no Alpine needed).

installer/setup-head.sh is the installer header; build.sh appends the app payload. These tests pin
the matinee-mcp service: its init script, its settings, secrets-file permissions, and the rules that
a re-run keeps edited values and a pip failure cannot stop the main install.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HEAD = (ROOT / "installer" / "setup-head.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "installer" / "build.sh").read_text(encoding="utf-8")


def _init_script():
    m = re.search(r"cat > /etc/init\.d/matinee-mcp <<'EOF'\n(.*?)\nEOF\n", HEAD, re.S)
    assert m, "matinee-mcp init script heredoc not found"
    return m.group(1)


def _code_lines():
    return [l.strip() for l in HEAD.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_head_is_valid_sh():
    subprocess.run(["sh", "-n", str(ROOT / "installer" / "setup-head.sh")], check=True)


def test_init_script_contents():
    s = _init_script()
    for needle in ('name="matinee-mcp"', "need net matinee", 'command_user="matinee-mcp:matinee-mcp"',
                   "/opt/matinee-mcp/venv/bin/python", "/opt/matinee/mcp_http.py",
                   "supervise-daemon", "/var/log/matinee-mcp.log"):
        assert needle in s, needle
    export = next(l for l in s.splitlines() if l.startswith("export "))
    assert "TMDB_READ_TOKEN" not in export and "OMDB_API_KEY" not in export
    for key in ("MATINEE_URL", "MATINEE_MCP_READONLY", "MATINEE_MCP_WRITES", "MCP_HTTP_BIND", "CF_ACCESS_AUD",
                "MCP_ALLOWED_SERVICE_TOKENS"):
        assert key in export, key
    # the keys are exported by the per-mode case below, never unconditionally
    assert "MATINEE_API_KEY" not in export and "MATINEE_READ_KEY" not in export



@pytest.mark.parametrize("key,value", [
    ("MATINEE_MCP_READONLY", "1"), ("MCP_HTTP_PORT", "8719"), ("MCP_HTTP_PATH", "/matinee/mcp"),
    ("MCP_HTTP_BIND", '"$IP"'), ("MCP_PUBLIC_HOSTS", '""'),
    ("CF_ACCESS_AUD", '""'), ("MCP_ALLOWED_EMAILS", '""'), ("MCP_ALLOWED_SERVICE_TOKENS", '""'),
])
def test_new_settings_are_added_with_defaults(key, value):
    assert re.search(rf'^add_setting "\$CONF" {key} {re.escape(value)} ".+"$', HEAD, re.M), key


def test_team_domain_is_one_setting():
    # matinee-mcp reuses CF_TEAM_DOMAIN; the installer must not write a second team-domain key
    assert "add_setting \"$CONF\" CF_ACCESS_TEAM_DOMAIN" not in HEAD
    assert "CF_ACCESS_TEAM_DOMAIN=" not in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_conf_is_root_only():
    assert 'chmod 600 "$CONF"' in HEAD and 'chown root:root "$CONF"' in HEAD
    assert 'chmod 640 "$CONF"' not in HEAD


def test_pip_step_is_guarded():
    line = next(l for l in HEAD.splitlines() if re.search(r'pip"? install', l) and not l.strip().startswith("#"))
    assert "||" in line and "exit" not in line


def test_service_start_only_when_configured():
    lines = HEAD.splitlines()
    idx = [i for i, l in enumerate(lines) if l.strip().startswith("rc-service matinee-mcp start")]
    assert len(idx) == 1
    cond = next(lines[j] for j in range(idx[0], -1, -1) if lines[j].lstrip().startswith("if "))
    for key in ("CF_ACCESS_AUD", "MCP_PUBLIC_HOSTS", '"$(mcp_key_name)"'):
        assert key in cond, key
    assert "MATINEE_API_KEY" not in cond  # a read-only service starts without the write key


def test_health_check_and_uninstall():
    assert "Matinee MCP|http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT)/healthz|matinee-mcp" in HEAD
    block = HEAD[HEAD.index('"--uninstall"'):HEAD.index("exit 0")]
    assert "matinee-mcp" in block and "/opt/matinee-mcp" in block


def test_restore_test_is_installed_weekly_and_removed_on_uninstall():
    assert 'install -m 755 "$APP_DIR/ops/matinee-restore-test" /etc/periodic/weekly/matinee-restore-test' in HEAD
    assert "mkdir -p /etc/periodic/daily /etc/periodic/weekly" in HEAD
    block = HEAD[HEAD.index('"--uninstall"'):HEAD.index("exit 0")]
    assert "/etc/periodic/weekly/matinee-restore-test" in block
    # the build ships all of ops/, so the new scripts travel with it
    assert re.search(r"\bops\b", BUILD)
    for f in ("matinee-restore-test", "refresh_titles.py"):
        assert (ROOT / "ops" / f).exists()


def test_build_copies_mcp_files():
    for f in ("mcp_http.py", "mcp_remote.py", "requirements-mcp.txt"):
        assert f in BUILD, f


def test_build_ships_every_top_level_module():
    # Every *.py beside app.py is imported by an entry point (gunicorn app:app, ops/*.py, matinee-mcp), so a module
    # left out of build.sh would install cleanly and then fail at import on the host.
    cp = next(line for line in BUILD.splitlines() if line.startswith("cp -r ")).split()
    modules = sorted(p.name for p in ROOT.glob("*.py"))
    assert "app.py" in modules and "ask.py" in modules, modules
    missing = [m for m in modules if m not in cp]
    assert not missing, f"installer/build.sh does not ship {missing}"


def test_build_ships_js_modules():
    # cp -r of the whole static/ directory carries static/js/ (and its package.json) into the payload.
    cp = next(line for line in BUILD.splitlines() if line.startswith("cp -r "))
    assert " static " in cp
    assert (ROOT / "static" / "js" / "main.js").is_file() and (ROOT / "static" / "js" / "package.json").is_file()


def test_requirements_mcp_pins():
    text = (ROOT / "requirements-mcp.txt").read_text()
    for pin in ("mcp>=2.3,<3", "uvicorn>=0.31", "pyjwt[crypto]>=2.10"):
        assert pin in text, pin


def test_add_setting_keeps_an_edited_value(tmp_path):
    fn = re.search(r"^add_setting\(\) \{.*?^\}$", HEAD, re.S | re.M).group(0)
    conf = tmp_path / "matinee"
    conf.write_text("PORT=8771\nMATINEE_MCP_READONLY=0\nMATINEE_API_KEY=abc123\n")
    script = fn + (
        f'\nadd_setting "{conf}" MATINEE_MCP_READONLY 1 "read-only"'
        f'\nadd_setting "{conf}" MATINEE_API_KEY "" "key"'
        f'\nadd_setting "{conf}" MCP_HTTP_PORT 8719 "port"\n')
    subprocess.run(["sh", "-c", script], check=True)
    text = conf.read_text()
    assert text.count("MATINEE_MCP_READONLY=") == 1 and "MATINEE_MCP_READONLY=0" in text
    assert "MATINEE_API_KEY=abc123" in text and text.count("MATINEE_API_KEY=") == 1
    assert "MCP_HTTP_PORT=8719" in text


@pytest.mark.parametrize("initial", [
    'HEALTH_CHECKS="\nRoasted|http://127.0.0.1:8768/healthz|roasted\n"\n',
    "FOO=1\n",
    'HEALTH_CHECKS="Roasted|x|roasted"\n',
])
def test_health_checks_added_once_and_matinee_does_not_hide_matinee_mcp(tmp_path, initial):
    fn = re.search(r"^add_health_check\(\) \{.*?^\}$", HEAD, re.S | re.M).group(0)
    ops = tmp_path / "ops"
    ops.write_text(initial)
    script = (f"OPS_CONF={ops}\n{fn}\n"
              'add_health_check "Matinee MCP|http://1.2.3.4:8719/healthz|matinee-mcp" matinee-mcp\n'
              'add_health_check "Matinee|http://127.0.0.1:8771/healthz|matinee" matinee\n'
              'add_health_check "Matinee|http://127.0.0.1:8771/healthz|matinee" matinee\n'
              'add_health_check "Matinee MCP|http://1.2.3.4:8719/healthz|matinee-mcp" matinee-mcp\n')
    subprocess.run(["sh", "-c", script], check=True)
    text = ops.read_text()
    assert text.count("|matinee-mcp") == 1 and text.count("8771/healthz|matinee") == 1
    subprocess.run(["sh", "-n", str(ops)], check=True)


# ------------------------------------------------------------------ docs
NEW_KEYS = ["MATINEE_MCP_READONLY", "MATINEE_MCP_WRITES", "MCP_HTTP_PORT", "MCP_HTTP_BIND", "MCP_HTTP_PATH", "MCP_PUBLIC_HOSTS",
            "CF_ACCESS_AUD", "MCP_ALLOWED_EMAILS", "MCP_ALLOWED_SERVICE_TOKENS"]


@pytest.mark.parametrize("key", NEW_KEYS)
def test_env_example_documents_new_settings(key):
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    i = next((n for n, l in enumerate(lines) if l.startswith(f"{key}=")), None)
    assert i is not None, f"{key} missing from .env.example"
    assert lines[i - 1].startswith("# "), f"{key} needs a comment line above it"


def test_mcp_setup_mentions_remote():
    text = (ROOT / "docs" / "mcp-setup.md").read_text(encoding="utf-8")
    for needle in ("https://mcp.example.com/matinee/mcp", "^/matinee/", "http://192.168.1.100:8719",
                   "MATINEE_MCP_READONLY", "rc-service matinee-mcp restart"):
        assert needle in text, needle


def test_readme_points_to_remote_setup():
    assert "Remote access from the Claude app" in (ROOT / "README.md").read_text(encoding="utf-8")


def _nearest_if(lines, idx):
    return next(lines[j] for j in range(idx, -1, -1) if lines[j].lstrip().startswith("if "))


def test_runlevel_and_health_check_only_once_the_service_is_up():
    lines = HEAD.splitlines()
    for prefix in ("rc-update add matinee-mcp", 'add_health_check "$HC_MCP_LINE"'):
        idx = [i for i, l in enumerate(lines) if l.strip().startswith(prefix)]
        assert len(idx) == 1, prefix
        assert "MCP_STATE" in _nearest_if(lines, idx[0]), prefix


def test_tunnel_hint_uses_the_configured_address():
    line = next(l for l in HEAD.splitlines() if "MCP tunnel route" in l)
    assert "8719" not in line and "conf_val MCP_HTTP_BIND" in line


@pytest.mark.parametrize("value,warns", [("", True), ("you@example.com", False)])
def test_final_banner_warns_when_allowed_emails_is_empty(tmp_path, value, warns):
    # The Access app usually fronts several MCP servers; with no MCP_ALLOWED_EMAILS anyone its policy
    # admits can read the collection, so the banner must say so whatever state matinee-mcp ended in.
    lines = HEAD.splitlines()
    warn = [l for l in lines if "MCP_ALLOWED_EMAILS is empty" in l]
    assert len(warn) == 1
    assert "rc-service matinee-mcp restart" in warn[0]
    case_end = next(i for i, l in enumerate(lines) if l.strip() == "esac" and i > lines.index(
        next(l for l in lines if l.startswith('case "$MCP_STATE" in'))))
    assert lines.index(warn[0]) > case_end, "the warning belongs after the MCP_STATE case, not inside one branch"
    conf = tmp_path / "matinee"
    conf.write_text(f"MCP_ALLOWED_EMAILS={value}\n")
    conf_val = next(l for l in lines if l.startswith("conf_val()"))
    out = subprocess.run(["sh", "-c", f'CONF="{conf}"\n{conf_val}\n{warn[0]}\n:'],
                         capture_output=True, text=True, check=True).stdout
    assert ("MCP_ALLOWED_EMAILS is empty" in out) is warns


def test_mcp_setup_states_what_the_read_key_adds_to_reads():
    # Security framing: through /admin/api the read-only service sees watch notes, search_tmdb and copies'
    # added_by (the editor's Access email), which /api does not serve. Pinned by
    # test_rest_get_movie_added_by_only_with_key and tests/test_privacy.py.
    lines = (ROOT / "docs" / "mcp-setup.md").read_text(encoding="utf-8").splitlines()
    row = next(l for l in lines if l.startswith("| `MATINEE_READ_KEY` | *(generated"))
    assert "search_tmdb" in row and "added_by" in row and "watch notes" in row and "cannot write" in row
    api = next(l for l in lines if l.startswith("| `MATINEE_API_KEY` | *(empty)*"))
    assert "MATINEE_MCP_READONLY=0" in api


def test_lan_hosts_setting_is_written_and_exported():
    assert re.search(r'^add_setting "\$CONF" LAN_HOSTS "" ".+"$', HEAD, re.M)
    export = next(l for l in HEAD.splitlines() if l.startswith("export PORT "))
    assert " LAN_EDIT LAN_HOSTS " in f" {export} "
    assert re.search(r"^# .+\nLAN_HOSTS=$", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)


def test_ha_webhook_url_is_written_exported_and_documented():
    # The URL is a secret (the webhook id is the only credential): empty by default, added when missing and never
    # overwritten, exported to the Flask service only. The matinee-mcp service never posts to Home Assistant.
    assert re.search(r'^add_setting "\$CONF" HA_WEBHOOK_URL "" ".+"$', HEAD, re.M)
    export = next(l for l in HEAD.splitlines() if l.startswith("export PORT "))
    assert " HA_WEBHOOK_URL " in f" {export} "
    mcp_export = next(l for l in _init_script().splitlines() if l.startswith("export "))
    assert "HA_WEBHOOK_URL" not in mcp_export
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^# .+\nHA_WEBHOOK_URL=$", env, re.M)
    assert "HA_WEBHOOK_URL=http" not in env, "no example webhook id in the committed example"


def test_wish_list_settings_are_written_exported_and_documented():
    # Two settings for the family Wish Lists app: written when missing, never overwritten, exported to the Flask
    # service only (matinee-mcp never talks to it), and shown in .env.example with a comment above each.
    export = next(l for l in HEAD.splitlines() if l.startswith("export PORT "))
    mcp_export = next(l for l in _init_script().splitlines() if l.startswith("export "))
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key, default in (("WISHLIST_URL", "http://127.0.0.1:8766"),
                         ("WISHLIST_MOVIE_URL", "https://www.amazon.com/s?k={q}")):
        assert re.search(rf'^add_setting "\$CONF" {key} \'?"?{re.escape(default)}"?\'? ".+"$', HEAD, re.M), key
        assert f" {key} " in f" {export} ", key
        assert key not in mcp_export, key
        assert re.search(rf"^# .+\n{key}={re.escape(default)}$", env, re.M), key


def test_wishlist_module_is_shipped():
    cp = next(line for line in BUILD.splitlines() if line.startswith("cp -r ")).split()
    assert "wishlist.py" in cp


# ------------------------------------------------------------------ MATINEE_READ_KEY
# Mirrors mcp_http.readonly(): only 0/false/no/off (case-insensitive, trimmed) turn writes on.
READONLY_CASES = [(None, "read"), ("", "read"), ("1", "read"), ("true", "read"), ("garbage", "read"),
                  ("0", "write"), ("false", "write"), (" OFF ", "write"), ("No", "write")]


def _fn(name):
    return re.search(rf"^{name}\(\) \{{.*?^\}}$", HEAD, re.S | re.M).group(0)


def test_read_key_is_generated_only_when_absent(tmp_path):
    assert re.search(r'^add_setting "\$CONF" MATINEE_READ_KEY "\$\(gen_key\)" ".+"$', HEAD, re.M)
    conf = tmp_path / "matinee"
    conf.write_text("PORT=8771\n")
    script = _fn("add_setting") + "\n" + _fn("gen_key") + (
        f'\nadd_setting "{conf}" MATINEE_READ_KEY "$(gen_key)" "read key"\n')
    subprocess.run(["sh", "-c", script], check=True)
    first = re.search(r"^MATINEE_READ_KEY=(.*)$", conf.read_text(), re.M).group(1)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", first), first
    subprocess.run(["sh", "-c", script], check=True)  # a re-run keeps the generated key
    assert conf.read_text().count("MATINEE_READ_KEY=") == 1
    assert f"MATINEE_READ_KEY={first}" in conf.read_text()
    conf.write_text("MATINEE_READ_KEY=chosen-by-hand\n")  # and a value set by hand
    subprocess.run(["sh", "-c", script], check=True)
    assert conf.read_text() == "MATINEE_READ_KEY=chosen-by-hand\n"


def test_matinee_service_gets_both_keys():
    export = next(l for l in HEAD.splitlines() if l.startswith("export PORT "))
    assert " MATINEE_API_KEY " in f" {export} " and " MATINEE_READ_KEY " in f" {export} "


def _mcp_key_case():
    """The init script's mcp_level function and its key-selection case, ready to run in sh."""
    s = _init_script()
    fn = re.search(r"^mcp_level\(\) \{.*?^\}$", s, re.S | re.M)
    assert fn, "matinee-mcp init script has no mcp_level function"
    m = re.search(r"^case .*?^esac$", s, re.S | re.M)
    assert m, "matinee-mcp init script has no key-selection case"
    return fn.group(0) + "\n" + m.group(0)


@pytest.mark.parametrize("value,mode", READONLY_CASES)
def test_mcp_init_script_exports_one_key_per_mode(value, mode):
    setv = "" if value is None else f"MATINEE_MCP_READONLY='{value}'\n"
    script = f"MATINEE_API_KEY=w-key\nMATINEE_READ_KEY=r-key\n{setv}{_mcp_key_case()}\nenv\n"
    out = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", "sh", "-c", script],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    if mode == "read":
        assert "MATINEE_READ_KEY=r-key" in out and not any(l.startswith("MATINEE_API_KEY=") for l in out)
    else:
        assert "MATINEE_API_KEY=w-key" in out and not any(l.startswith("MATINEE_READ_KEY=") for l in out)


def test_mcp_init_script_unsets_an_inherited_write_key():
    script = f"MATINEE_MCP_READONLY=1\nMATINEE_READ_KEY=r-key\n{_mcp_key_case()}\nenv\n"
    out = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", "MATINEE_API_KEY=inherited", "sh", "-c", script],
                         capture_output=True, text=True, check=True).stdout
    assert "MATINEE_API_KEY" not in out


@pytest.mark.parametrize("value,mode", READONLY_CASES)
def test_installer_picks_the_key_matinee_mcp_needs(tmp_path, value, mode):
    conf = tmp_path / "matinee"
    conf.write_text("" if value is None else f"MATINEE_MCP_READONLY={value}\n")
    conf_val = next(l for l in HEAD.splitlines() if l.startswith("conf_val()"))
    out = subprocess.run(["sh", "-c", f'CONF="{conf}"\n{conf_val}\n{_fn("mcp_level")}\n{_fn("mcp_key_name")}\nmcp_key_name'],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ("MATINEE_READ_KEY" if mode == "read" else "MATINEE_API_KEY")


def test_not_started_banner_names_the_mode_key():
    line = next(l for l in HEAD.splitlines() if l.strip().startswith("for k in MCP_PUBLIC_HOSTS"))
    assert '"$(mcp_key_name)"' in line and "MATINEE_API_KEY" not in line


# ------------------------------------------------------------------ log rotation
def _logrotate_fn():
    return re.search(r"^install_logrotate\(\) \{.*?^\}$", HEAD, re.S | re.M).group(0)


def _run_logrotate(tmp_path, *, have_logrotate=True, existing=None, conf_text="", make_dir=True):
    """Run install_logrotate from the real installer under `set -eu`, pointed at a temp /etc/logrotate*."""
    d = tmp_path / "logrotate.d"
    if make_dir:
        d.mkdir()
    conf = tmp_path / "logrotate.conf"
    conf.write_text(conf_text)
    for name, text in (existing or {}).items():
        (d / name).write_text(text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if have_logrotate:
        (bindir / "logrotate").write_text("#!/bin/sh\n")
        (bindir / "logrotate").chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin" if have_logrotate else str(bindir),   # no PATH to find a real one
           "LOGROTATE_D": str(d), "LOGROTATE_CONF": str(conf)}
    script = f"set -eu\n{_logrotate_fn()}\ninstall_logrotate\necho done\n"
    r = subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True)
    return r, d / "matinee"


def test_logrotate_is_installed_with_the_other_packages():
    apk = next(l for l in HEAD.splitlines() if l.startswith("apk add --no-cache python3"))
    assert " logrotate" in apk
    assert re.search(r"^install_logrotate$", HEAD, re.M)


def test_logrotate_config_written_when_installed_and_unconfigured(tmp_path):
    r, f = _run_logrotate(tmp_path, existing={"syslog": "/var/log/messages {\n    weekly\n}\n"})
    assert r.returncode == 0 and "done" in r.stdout, r.stderr
    text = f.read_text()
    assert text.splitlines()[0] == "/var/log/matinee.log /var/log/matinee-mcp.log {" and text.splitlines()[-1] == "}"
    for needle in ("weekly", "rotate 8", "copytruncate", "missingok", "notifempty"):
        assert needle in text, needle


def test_logrotate_skipped_when_not_installed(tmp_path):
    r, f = _run_logrotate(tmp_path, have_logrotate=False)
    assert r.returncode == 0 and "done" in r.stdout and not f.exists()


@pytest.mark.parametrize("where", ["d", "conf"])
@pytest.mark.parametrize("log", ["/var/log/matinee.log", "/var/log/matinee-mcp.log"])
def test_logrotate_skipped_when_another_config_names_either_log(tmp_path, where, log):
    stanza = f"{log} {{\n    daily\n    rotate 3\n}}\n"
    kw = {"existing": {"mine": stanza}} if where == "d" else {"conf_text": stanza}
    r, f = _run_logrotate(tmp_path, **kw)
    assert r.returncode == 0 and "done" in r.stdout and not f.exists()


def test_logrotate_rerun_keeps_an_edited_file(tmp_path):
    r, f = _run_logrotate(tmp_path, existing={"matinee": "/var/log/matinee.log {\n    rotate 2\n}\n"})
    assert r.returncode == 0 and f.read_text() == "/var/log/matinee.log {\n    rotate 2\n}\n"


def test_logrotate_missing_config_dir_does_not_abort_the_install(tmp_path):
    r, f = _run_logrotate(tmp_path, make_dir=False)
    assert r.returncode == 0 and "done" in r.stdout and not f.exists()


def test_logrotate_second_run_is_a_no_op(tmp_path):
    r, f = _run_logrotate(tmp_path)
    first = f.read_text()
    assert r.returncode == 0 and first
    d = f.parent
    script = f"set -eu\n{_logrotate_fn()}\ninstall_logrotate\n"
    bindir = tmp_path / "bin"
    r2 = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True,
                        env={"PATH": f"{bindir}:/usr/bin:/bin", "LOGROTATE_D": str(d), "LOGROTATE_CONF": str(tmp_path / "logrotate.conf")})
    assert r2.returncode == 0 and f.read_text() == first and "Added" not in r2.stdout


# ------------------------------------------------------------------ MATINEE_MCP_WRITES: none | log | all
# (MATINEE_MCP_READONLY, MATINEE_MCP_WRITES, level): the table in the tiered-writes spec, section 3.
# tests/test_mcp_http.py has the same rows for mcp_http.write_level(); test_mcp_level_matches_python_resolver
# runs the shell function against that resolver directly.
LEVEL_CASES = [
    (None, None, "none"), ("", "", "none"), ("1", None, "none"), ("true", "none", "none"),
    (" yes ", "", "none"), ("garbage", None, "none"),
    ("1", "log", "none"), ("true", "all", "none"), ("garbage", "log", "none"), ("1", "none", "none"),
    ("0", None, "all"), ("false", "", "all"), ("No", "  ", "all"), (" off ", None, "all"),
    ("0", "none", "none"), ("0", "log", "log"), ("0", " ALL ", "all"), ("0", "Log", "log"),
    ("0", "rw", "none"), ("0", "1", "none"), ("0", "yes", "none"),
    (None, "log", "log"), ("", "all", "all"), (None, "rw", "none"),
]


def _sh_level(fn_text, ro, w):
    return subprocess.run(["sh", "-c", fn_text + '\nmcp_level "$1" "$2"', "sh", ro or "", w or ""],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_mcp_level_is_defined_identically_in_installer_and_init_script():
    defs = re.findall(r"^mcp_level\(\) \{.*?^\}$", HEAD, re.S | re.M)
    assert len(defs) == 2, "expected one definition for the installer and one inside the init script heredoc"
    assert defs[0] == defs[1]
    assert defs[0] in _init_script()


@pytest.mark.parametrize("ro,w,level", LEVEL_CASES)
def test_mcp_level_shell_function(ro, w, level):
    assert _sh_level(_fn("mcp_level"), ro, w) == level


def test_mcp_level_matches_python_resolver():
    pytest.importorskip("mcp.server.mcpserver")
    import mcp_http
    for ro, w, _ in LEVEL_CASES:
        env = {}
        if ro is not None:
            env["MATINEE_MCP_READONLY"] = ro
        if w is not None:
            env["MATINEE_MCP_WRITES"] = w
        assert _sh_level(_fn("mcp_level"), ro, w) == mcp_http.write_level(env), (ro, w)


@pytest.mark.parametrize("ro,w,level", LEVEL_CASES)
def test_mcp_init_script_exports_one_key_per_level(ro, w, level):
    setv = ("" if ro is None else f"MATINEE_MCP_READONLY='{ro}'\n") + ("" if w is None else f"MATINEE_MCP_WRITES='{w}'\n")
    script = f"MATINEE_API_KEY=w-key\nMATINEE_READ_KEY=r-key\n{setv}{_mcp_key_case()}\nenv\n"
    out = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", "sh", "-c", script],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    if level == "none":
        assert "MATINEE_READ_KEY=r-key" in out and not any(l.startswith("MATINEE_API_KEY=") for l in out)
    else:
        assert "MATINEE_API_KEY=w-key" in out and not any(l.startswith("MATINEE_READ_KEY=") for l in out)


@pytest.mark.parametrize("ro,w,level", [c for c in LEVEL_CASES if c[2] == "none"])
def test_mcp_init_script_unsets_an_inherited_write_key_at_none(ro, w, level):
    setv = ("" if ro is None else f"MATINEE_MCP_READONLY='{ro}'\n") + ("" if w is None else f"MATINEE_MCP_WRITES='{w}'\n")
    script = f"MATINEE_READ_KEY=r-key\n{setv}{_mcp_key_case()}\nenv\n"
    out = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", "MATINEE_API_KEY=inherited", "sh", "-c", script],
                         capture_output=True, text=True, check=True).stdout
    assert "MATINEE_API_KEY" not in out


@pytest.mark.parametrize("ro,w,level", LEVEL_CASES)
def test_installer_picks_the_key_for_the_level(tmp_path, ro, w, level):
    conf = tmp_path / "matinee"
    conf.write_text("".join(f"{k}={v}\n" for k, v in
                            (("MATINEE_MCP_READONLY", ro), ("MATINEE_MCP_WRITES", w)) if v is not None))
    conf_val = next(l for l in HEAD.splitlines() if l.startswith("conf_val()"))
    out = subprocess.run(["sh", "-c", f'CONF="{conf}"\n{conf_val}\n{_fn("mcp_level")}\n{_fn("mcp_key_name")}\nmcp_key_name'],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ("MATINEE_READ_KEY" if level == "none" else "MATINEE_API_KEY")


def _writes_default_script(conf):
    default = re.search(r"^MCP_WRITES_DEFAULT=.*$", HEAD, re.M)
    add = re.search(r'^add_setting "\$CONF" MATINEE_MCP_WRITES "\$MCP_WRITES_DEFAULT" ".+"$', HEAD, re.M)
    assert default and add, "installer must derive MCP_WRITES_DEFAULT and add MATINEE_MCP_WRITES with it"
    conf_val = next(l for l in HEAD.splitlines() if l.startswith("conf_val()"))
    return f'CONF="{conf}"\n{_fn("add_setting")}\n{conf_val}\n{_fn("mcp_level")}\n{default.group(0)}\n{add.group(0)}\n'


@pytest.mark.parametrize("existing,expected", [
    ("MATINEE_MCP_READONLY=1\n", "none"),       # a read-only host stays at none
    ("MATINEE_MCP_READONLY=0\n", "all"),        # a host that already has writes keeps all nine tools
    ("MATINEE_MCP_READONLY=OFF\n", "all"),
    ("MATINEE_MCP_READONLY=garbage\n", "none"),
    ("", "none"),                                # no READONLY line at all
])
def test_installer_derives_the_writes_default_from_readonly(tmp_path, existing, expected):
    conf = tmp_path / "matinee"
    conf.write_text(existing)
    subprocess.run(["sh", "-c", _writes_default_script(conf)], check=True)
    text = conf.read_text()
    assert text.count("MATINEE_MCP_WRITES=") == 1 and f"\nMATINEE_MCP_WRITES={expected}\n" in text


def test_installer_keeps_an_edited_writes_value(tmp_path):
    conf = tmp_path / "matinee"
    conf.write_text("MATINEE_MCP_READONLY=0\nMATINEE_MCP_WRITES=log\n")
    script = _writes_default_script(conf)
    subprocess.run(["sh", "-c", script], check=True)
    subprocess.run(["sh", "-c", script], check=True)  # a re-run too
    assert conf.read_text() == "MATINEE_MCP_READONLY=0\nMATINEE_MCP_WRITES=log\n"


def test_writes_setting_comes_after_readonly_in_the_installer():
    lines = HEAD.splitlines()
    ro = next(i for i, l in enumerate(lines) if l.startswith('add_setting "$CONF" MATINEE_MCP_READONLY'))
    default = next(i for i, l in enumerate(lines) if l.startswith("MCP_WRITES_DEFAULT="))
    add = next(i for i, l in enumerate(lines) if l.startswith('add_setting "$CONF" MATINEE_MCP_WRITES'))
    assert ro < default < add  # the default is derived from a READONLY line that is already in the file


def test_env_example_writes_default_is_none():
    assert re.search(r"^MATINEE_MCP_WRITES=none$", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)


def test_banner_prints_the_write_level():
    assert re.search(r"^\s*MCP_LEVEL=\$\(mcp_level ", HEAD, re.M)
    assert "write level: $MCP_LEVEL" in HEAD


# ------------------------------------------------------------------ docs: write levels
def _setup_text():
    return (ROOT / "docs" / "mcp-setup.md").read_text(encoding="utf-8")


def test_mcp_setup_documents_the_write_levels():
    text = _setup_text()
    for needle in ("MATINEE_MCP_WRITES", "`none`", "`log`", "`all`", "MATINEE_MCP_READONLY=0",
                   "log_watch", "rate_movie", "add_movie"):
        assert needle in text, needle
    # the section the phone-logging recipe lives in
    sec = text.split("### 4.4 Turning on write tools", 1)[1].split("### 4.5", 1)[0]
    assert "MATINEE_MCP_WRITES=log" in sec and "MATINEE_MCP_READONLY=0" in sec and "rc-service matinee-mcp restart" in sec
    assert "Paddington 2" in sec


def test_mcp_setup_settings_table_has_the_writes_row():
    lines = _setup_text().splitlines()
    row = next(l for l in lines if l.startswith("| `MATINEE_MCP_WRITES` |"))
    assert "none" in row and "log" in row and "all" in row and "MATINEE_MCP_READONLY" in row
    ro = next(l for l in lines if l.startswith("| `MATINEE_MCP_READONLY` |"))
    assert "MATINEE_MCP_WRITES" in ro


def test_mcp_setup_key_table_says_the_api_key_is_used_at_log_and_all():
    lines = _setup_text().splitlines()
    api = next(l for l in lines if l.startswith("| `MATINEE_API_KEY` | *(empty)*"))
    assert "`log`" in api and "`all`" in api and "MATINEE_MCP_READONLY=0" in api


def test_mcp_setup_rollback_has_a_row_for_dropping_to_log():
    sec = _setup_text().split("### 4.5 Rollback", 1)[1].split("\n---", 1)[0]
    assert "MATINEE_MCP_WRITES=log" in sec


def test_mcp_setup_write_tool_section_lists_levels():
    sec = _setup_text().split("### 5.2 Write Tools", 1)[1].split("### 5.3", 1)[0]
    assert "MATINEE_MCP_WRITES" in sec and "`log`" in sec and "`all`" in sec


def test_readme_mentions_the_write_levels():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "MATINEE_MCP_WRITES" in text


# ------------------------------------------------------------------ safe upgrade
# The static checks read the installer's text; the sandbox tests below run the real script (its paths rewritten
# into a temp tree, every system command a stub) through install, upgrade, rollback and uninstall.
def _lines():
    return HEAD.splitlines()


def _first(pred, lines=None):
    return next(i for i, l in enumerate(lines or _lines()) if pred(l))


def test_backup_runs_before_anything_is_stopped():
    backup = _first(lambda l: 'matinee-backup" pre-upgrade' in l and not l.lstrip().startswith("#"))
    # the stops of the install flow (the uninstall block and the rollback function stop services too, but the
    # first is a different run and the second only exists after GUARD=1)
    stops = [i for i, l in enumerate(_lines()) if l.startswith("[ -x /etc/init.d/matinee") and " stop " in l]
    assert len(stops) == 2 and backup < min(stops)
    # the NEW script from the unpacked payload, never the copy already on the host (an old one ignores the label)
    assert 'sh "$APP_DIR.new/ops/matinee-backup" pre-upgrade' in HEAD
    assert "/etc/periodic/daily/matinee-backup\" pre-upgrade" not in HEAD
    # and a failed backup stops the installer before the stop
    assert re.search(r'if ! sh "\$APP_DIR\.new/ops/matinee-backup" pre-upgrade; then(\n.*)*?\n\s+exit 1\n\s+fi', HEAD)


def test_slow_work_happens_before_the_stop():
    unpack = _first(lambda l: l.startswith('echo "==> Unpacking app"'))
    pip = _first(lambda l: re.search(r'pip"? install', l) and not l.lstrip().startswith("#"))
    stop = _first(lambda l: l.startswith("GUARD=1"))
    assert unpack < pip < stop
    assert '--upgrade' in _lines()[pip] and '-r "$APP_DIR.new/requirements-mcp.txt"' in _lines()[pip]


def test_a_trap_rolls_back_after_the_stop():
    assert re.search(r"^trap on_exit EXIT$", HEAD, re.M)
    assert re.search(r"^trap 'exit 1' INT TERM HUP$", HEAD, re.M)
    guard_on = _first(lambda l: l.startswith("GUARD=1"))
    guard_off = _first(lambda l: l.startswith("GUARD=0") and not l.startswith("GUARD=0 "))
    health = _first(lambda l: "/healthz" in l and l.lstrip().startswith("until wget"))
    assert guard_on < health < guard_off
    stop = _first(lambda l: "rc-service matinee stop" in l and "|| true" in l and l.startswith("[ -x"))
    assert guard_on < stop
    assert "put" in HEAD and "mv \"$APP_DIR.old\" \"$APP_DIR\"" in HEAD


def test_previous_directory_is_kept_after_the_swap():
    lines = _lines()
    swap = _first(lambda l: l.startswith('mv "$APP_DIR.new" "$APP_DIR"'))
    assert not [l for l in lines[swap:] if re.search(r"\brm\b", l) and "$APP_DIR.old" in l], \
        "the previous app directory must stay until the next upgrade"
    assert 'mv "$APP_DIR" "$APP_DIR.old"' in HEAD
    assert "$APP_DIR.old" in HEAD[HEAD.index("To go back to it"):]


def test_conf_is_created_private_from_its_first_byte(tmp_path):
    block = re.search(r'^if \[ ! -f "\$CONF" \]; then\n.*?^fi$', HEAD, re.S | re.M).group(0)
    assert "umask 077" in block
    conf = tmp_path / "matinee"
    script = f'CONF="{conf}" PORT=8771 DATA_DIR=/var/lib/matinee\n{block}\n'
    subprocess.run(["sh", "-c", "umask 022\n" + script], check=True, capture_output=True)   # no chmod in the block
    assert oct(conf.stat().st_mode & 0o777) == "0o600"
    assert conf.read_text().startswith("# Matinee")
    # an older, wider file is closed before the read key is appended
    assert HEAD.index('chmod 600 "$CONF"') < HEAD.index('add_setting "$CONF" MATINEE_READ_KEY')


def test_service_log_is_640():
    assert "checkpath -f -m 0640 -o matinee:matinee /var/log/matinee.log" in HEAD
    assert "chmod 640 /var/log/matinee.log" in HEAD
    assert HEAD.index("chmod 640 /var/log/matinee.log") < HEAD.index("\nrc-service matinee start\n")


def test_first_backup_is_not_taken_after_an_upgrade():
    # the plain nightly copy would be overwritten with the migrated database
    m = re.search(r'if \[ "\$UPGRADE" = 1 \]; then(\n  echo "==> Pre-upgrade backup taken.*?)\nelse(.*?)\nfi', HEAD, re.S)
    assert m and "matinee-backup" not in m.group(1) and "/etc/periodic/daily/matinee-backup" in m.group(2)


def test_uninstall_removes_what_install_created():
    block = HEAD[HEAD.index('if [ "${1:-}" = "--uninstall" ]'):HEAD.index("exit 0")]
    for needle in ("deluser", "delgroup", "remove_health_check matinee", "remove_health_check matinee-mcp",
                   "logrotate.d", '"$APP_DIR.old"'):
        assert needle in block, needle


@pytest.mark.parametrize("initial,expected", [
    ('HEALTH_CHECKS="\nRoasted|http://127.0.0.1:8768/healthz|roasted\nMatinee|http://127.0.0.1:8771/healthz|matinee\n"\n',
     'HEALTH_CHECKS="\nRoasted|http://127.0.0.1:8768/healthz|roasted\n"\n'),
    ('HEALTH_CHECKS="\nRoasted|http://127.0.0.1:8768/healthz|roasted\n\nMatinee|http://127.0.0.1:8771/healthz|matinee"\n',
     'HEALTH_CHECKS="\nRoasted|http://127.0.0.1:8768/healthz|roasted\n"\n'),   # the blank line takes the quote
    ('HEALTH_CHECKS="Matinee|http://127.0.0.1:8771/healthz|matinee"\n', 'HEALTH_CHECKS=""\n'),
    ('HEALTH_CHECKS="Matinee|http://127.0.0.1:8771/healthz|matinee\nRoasted|x|roasted"\n',
     'HEALTH_CHECKS="\nRoasted|x|roasted"\n'),
    ("FOO=1\n", "FOO=1\n"),
])
def test_uninstall_removes_only_this_apps_health_checks(tmp_path, initial, expected):
    ops = tmp_path / "ops"
    ops.write_text(initial)
    script = (f"OPS_CONF={ops}\n{_fn('remove_health_check')}\nremove_health_check matinee\nremove_health_check matinee-mcp\n")
    subprocess.run(["sh", "-c", script], check=True)
    assert ops.read_text() == expected
    subprocess.run(["sh", "-n", str(ops)], check=True)


def test_remove_health_check_keeps_a_longer_service_name(tmp_path):
    ops = tmp_path / "ops"
    ops.write_text('HEALTH_CHECKS="\nMatinee MCP|http://1.2.3.4:8719/healthz|matinee-mcp\nMatinee|u|matinee\nOther|u|matinee-other\n"\n')
    script = f"OPS_CONF={ops}\n{_fn('remove_health_check')}\nremove_health_check matinee\n"
    subprocess.run(["sh", "-c", script], check=True)
    assert ops.read_text() == 'HEALTH_CHECKS="\nMatinee MCP|http://1.2.3.4:8719/healthz|matinee-mcp\nOther|u|matinee-other\n"\n'


# ------------------------------------------------------------------ the real script, in a sandbox
STUBS = {
    "id": '[ "$1" = "-u" ] && { echo 0; exit 0; }\n[ "$1" = "-nG" ] && { echo "$2"; exit 0; }\nexit 0\n',
    "apk": 'echo "apk $*" >> "$S/calls"\n',
    "addgroup": 'echo "addgroup $*" >> "$S/calls"\n',
    "adduser": 'echo "adduser $*" >> "$S/calls"\n',
    "deluser": 'echo "deluser $*" >> "$S/calls"\n',
    "delgroup": 'echo "delgroup $*" >> "$S/calls"\n',
    "chown": "exit 0\n",
    "ip": "exit 0\n",
    "sleep": "exit 0\n",
    "logrotate": "exit 0\n",
    "rc-update": 'echo "rc-update $*" >> "$S/calls"\n',
    "netstat": '[ -e "$S/portbusy" ] && echo "tcp 0 0 0.0.0.0:8771 0.0.0.0:* LISTEN"\nexit 0\n',
    # healthy only while the service is up and the installed app is not the deliberately broken one
    "wget": '[ -e "$S/matinee.up" ] && [ ! -e "$R/opt/matinee/BAD" ] && { echo ok; exit 0; }\nexit 1\n',
    "rc-service": (
        'echo "rc-service $*" >> "$S/calls"\n'
        'svc=$1; act=$2\n'
        'case "$act" in\n'
        '  status) [ -e "$S/$svc.up" ];;\n'
        '  stop) rm -f "$S/$svc.up";;\n'
        '  start) if [ "$svc" = matinee ] && [ -e "$S/startfails" ] && [ -e "$R/opt/matinee/BAD" ]; then exit 1; fi\n'
        '         touch "$S/$svc.up";;\n'
        'esac\n'),
}


class Sandbox:
    def __init__(self, tmp_path):
        self.root = tmp_path / "root"
        self.state = tmp_path / "state"
        self.bin = tmp_path / "bin"
        for d in ("opt", "etc/conf.d", "etc/init.d", "etc/logrotate.d", "var/log", "var/lib", "var/backups", "tmp"):
            (self.root / d).mkdir(parents=True)
        (self.root / "etc/group").write_text("")
        self.state.mkdir()
        self.bin.mkdir()
        for name, body in STUBS.items():
            f = self.bin / name
            f.write_text("#!/bin/sh\n" + body)
            f.chmod(0o755)
        self.tmp_path = tmp_path
        self.n = 0

    def path(self, p):
        return self.root / p.lstrip("/")

    def app(self, mark, *, backup_exit=0, bad=False, commit=None):
        """Make a payload tarball (base64) of an app tagged `mark`."""
        self.n += 1
        d = self.tmp_path / f"payload{self.n}" / "matinee"
        (d / "ops").mkdir(parents=True)
        backup = ('#!/bin/sh\necho "backup $*" >> "$S/calls"\n'
                  f'[ "$1" = pre-upgrade ] && [ {backup_exit} != 0 ] && exit {backup_exit}\n'
                  'if [ "$1" = pre-upgrade ]; then d="$R/var/backups/matinee/2026-11-27"; mkdir -p "$d"; : > "$d/matinee-matinee-pre-upgrade-120000.db.gz"; fi\n'
                  'exit 0\n')
        for name in ("matinee-backup", "matinee-refresh", "matinee-restore-test"):
            (d / "ops" / name).write_text(backup if name == "matinee-backup" else "#!/bin/sh\n")
            (d / "ops" / name).chmod(0o755)
        (d / "requirements-mcp.txt").write_text("mcp\n")
        (d / "MARK").write_text(mark)
        if commit is not None:
            (d / "COMMIT").write_text(commit, encoding="utf-8")
        if bad:
            (d / "BAD").write_text("")
        tar = subprocess.run(["tar", "-czf", "-", "matinee"], cwd=d.parent, capture_output=True, check=True).stdout
        import base64
        return base64.encodebytes(tar).decode()

    def script(self, payload):
        head = re.sub(r"(?<![\w$.])/(opt|etc|var)/", lambda m: f"{self.root}/{m.group(1)}/", HEAD)
        f = self.tmp_path / "setup.sh"
        f.write_text(head + "__PAYLOAD_BELOW__\n" + payload)
        return f

    def install_old(self, mark="OLD", *, running=True):
        """What an earlier run left: an app directory, a conf, init scripts, a database."""
        d = self.path("/opt/matinee")
        d.mkdir()
        (d / "MARK").write_text(mark)
        self.path("/etc/conf.d/matinee").write_text("PORT=8771\nDATA_DIR=" + str(self.path("/var/lib/matinee")) + "\n")
        self.path("/etc/conf.d/matinee").chmod(0o600)
        (self.path("/var/lib/matinee")).mkdir()
        (self.path("/var/lib/matinee") / "matinee.db").write_text("db")
        for svc in ("matinee", "matinee-mcp"):
            init = self.path(f"/etc/init.d/{svc}")
            init.write_text("#!/bin/sh\n")
            init.chmod(0o755)
        venv = self.path("/opt/matinee-mcp/venv/bin")
        venv.mkdir(parents=True)
        for n in ("python", "pip"):
            (venv / n).write_text("#!/bin/sh\n" + ('echo "pip $*" >> "$S/calls"\n[ -e "$S/pipfails" ] && exit 1\n' if n == "pip" else "") + "exit 0\n")
            (venv / n).chmod(0o755)
        if running:
            (self.state / "matinee.up").write_text("")
            (self.state / "matinee-mcp.up").write_text("")

    def run(self, payload, *args):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "S": str(self.state), "R": str(self.root),
               "LOGROTATE_D": str(self.path("/etc/logrotate.d")), "LOGROTATE_CONF": str(self.root / "logrotate.conf")}
        r = subprocess.run(["sh", str(self.script(payload)), *args], env=env, capture_output=True, text=True,
                           cwd=self.tmp_path)
        return r

    def calls(self):
        f = self.state / "calls"
        return f.read_text().splitlines() if f.exists() else []

    def mark(self, p):
        return (self.path(p) / "MARK").read_text()

    def up(self, svc="matinee"):
        return (self.state / f"{svc}.up").exists()


def _stop_index(calls):
    return next(i for i, c in enumerate(calls) if re.fullmatch(r"rc-service matinee(-mcp)? stop", c))


def test_sandbox_upgrade_backs_up_first_swaps_and_keeps_the_old_directory(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    r = sb.run(sb.app("NEW"))
    assert r.returncode == 0, r.stdout + r.stderr
    calls = sb.calls()
    assert calls.index("backup pre-upgrade") < _stop_index(calls)
    # the venv is refreshed from the NEW requirements, before the stop
    pip = next(i for i, c in enumerate(calls) if c.startswith("pip install"))
    assert pip < _stop_index(calls) and "--upgrade" in calls[pip] and "matinee.new/requirements-mcp.txt" in calls[pip]
    assert sb.mark("/opt/matinee") == "NEW" and sb.mark("/opt/matinee.old") == "OLD"
    assert not sb.path("/opt/matinee.new").exists()
    assert sb.up("matinee")
    # no plain first backup after an upgrade (it would overwrite tonight's copy with the migrated database)
    assert [c for c in calls if c.startswith("backup")] == ["backup pre-upgrade"]
    assert "/opt/matinee.old" in r.stdout and "rc-service matinee restart" in r.stdout
    assert "matinee-matinee-pre-upgrade-120000.db.gz" in r.stdout and "restore-aside" in r.stdout
    assert oct(sb.path("/var/log/matinee.log").stat().st_mode & 0o777) == "0o640"
    assert (sb.path("/etc/logrotate.d") / "matinee").exists()


def test_sandbox_a_second_upgrade_replaces_only_the_older_copy(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("V1")
    assert sb.run(sb.app("V2")).returncode == 0
    assert sb.run(sb.app("V3")).returncode == 0
    assert sb.mark("/opt/matinee") == "V3" and sb.mark("/opt/matinee.old") == "V2"


def test_sandbox_failed_backup_changes_nothing(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    r = sb.run(sb.app("NEW", backup_exit=1))
    assert r.returncode != 0 and "pre-upgrade backup failed" in r.stdout
    assert not [c for c in sb.calls() if re.fullmatch(r"rc-service matinee(-mcp)? stop", c)]
    assert sb.mark("/opt/matinee") == "OLD" and not sb.path("/opt/matinee.old").exists()
    assert not sb.path("/opt/matinee.new").exists()
    assert sb.up("matinee") and sb.up("matinee-mcp")


def test_sandbox_health_failure_rolls_back_and_restarts_both_services(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    r = sb.run(sb.app("NEW", bad=True))
    assert r.returncode != 0
    assert sb.mark("/opt/matinee") == "OLD" and not sb.path("/opt/matinee.old").exists()
    assert sb.mark("/opt/matinee.failed") == "NEW"
    assert sb.up("matinee") and sb.up("matinee-mcp")
    assert "previous version is running again" in r.stderr
    calls = sb.calls()
    assert calls[-1] == "rc-service matinee-mcp start" and "rc-service matinee start" in calls[_stop_index(calls):]


def test_sandbox_a_failed_start_rolls_back(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    (sb.state / "startfails").write_text("")
    r = sb.run(sb.app("NEW", bad=True))
    assert r.returncode != 0 and sb.mark("/opt/matinee") == "OLD" and sb.up("matinee")


def test_sandbox_a_busy_port_after_the_stop_restarts_the_old_version(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    (sb.state / "portbusy").write_text("")
    r = sb.run(sb.app("NEW"))
    assert r.returncode != 0 and "already in use" in r.stdout
    assert sb.mark("/opt/matinee") == "OLD" and not sb.path("/opt/matinee.old").exists()
    assert sb.up("matinee") and sb.up("matinee-mcp")


def test_sandbox_a_service_that_was_stopped_is_not_started_by_a_rollback(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    (sb.state / "matinee-mcp.up").unlink()
    r = sb.run(sb.app("NEW", bad=True))
    assert r.returncode != 0 and sb.up("matinee") and not sb.up("matinee-mcp")


def test_sandbox_unreachable_pypi_keeps_the_existing_venv(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    (sb.state / "pipfails").write_text("")
    r = sb.run(sb.app("NEW"))
    assert r.returncode == 0 and "keeping the installed one" in r.stdout and sb.mark("/opt/matinee") == "NEW"


def test_sandbox_fresh_install_takes_no_pre_upgrade_copy(tmp_path):
    sb = Sandbox(tmp_path)
    venv = sb.path("/opt/matinee-mcp/venv/bin")
    venv.mkdir(parents=True)
    for n in ("python", "pip"):
        (venv / n).write_text("#!/bin/sh\nexit 0\n")
        (venv / n).chmod(0o755)
    r = sb.run(sb.app("NEW"))
    assert r.returncode == 0, r.stdout + r.stderr
    calls = sb.calls()
    assert "backup pre-upgrade" not in calls and "backup " in calls     # the first, plain backup
    assert sb.mark("/opt/matinee") == "NEW" and not sb.path("/opt/matinee.old").exists()
    conf = sb.path("/etc/conf.d/matinee")
    assert oct(conf.stat().st_mode & 0o777) == "0o600" and "MATINEE_READ_KEY=" in conf.read_text()
    assert oct(sb.path("/var/log/matinee.log").stat().st_mode & 0o777) == "0o640"
    assert "rc-service matinee start" in calls and sb.up("matinee")


def test_sandbox_fresh_install_that_fails_leaves_no_half_swapped_services(tmp_path):
    sb = Sandbox(tmp_path)
    (sb.state / "startfails").write_text("")
    sb.path("/opt/matinee-mcp/venv/bin").mkdir(parents=True)
    r = sb.run(sb.app("NEW", bad=True))
    assert r.returncode != 0 and not sb.up("matinee")


def test_sandbox_uninstall_removes_users_logrotate_and_health_checks_but_keeps_data(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    (sb.path("/opt/matinee.old")).mkdir()
    ours = f"{sb.root}/var/log/matinee.log {sb.root}/var/log/matinee-mcp.log {{\n    weekly\n}}\n"   # paths as the sandbox rewrites them
    (sb.path("/etc/logrotate.d") / "matinee").write_text(ours)
    sb.path("/etc/conf.d/ops").write_text(
        'HEALTH_CHECKS="\nRoasted|http://127.0.0.1:8768/healthz|roasted\n'
        'Matinee|http://127.0.0.1:8771/healthz|matinee\nMatinee MCP|http://192.168.1.100:8719/healthz|matinee-mcp\n"\n')
    r = sb.run("", "--uninstall")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = sb.calls()
    for c in ("deluser matinee", "deluser matinee-mcp", "delgroup matinee", "delgroup matinee-mcp"):
        assert c in calls, c
    assert not (sb.path("/etc/logrotate.d") / "matinee").exists()
    assert sb.path("/etc/conf.d/ops").read_text() == 'HEALTH_CHECKS="\nRoasted|http://127.0.0.1:8768/healthz|roasted\n"\n'
    assert not sb.path("/opt/matinee").exists() and not sb.path("/opt/matinee.old").exists()
    assert (sb.path("/var/lib/matinee") / "matinee.db").exists() and sb.path("/etc/conf.d/matinee").exists()


def test_sandbox_uninstall_leaves_a_hand_made_logrotate_file(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    (sb.path("/etc/logrotate.d") / "matinee").write_text(f"{sb.root}/var/log/matinee.log {{\n    rotate 2\n}}\n")
    assert sb.run("", "--uninstall").returncode == 0
    assert (sb.path("/etc/logrotate.d") / "matinee").exists()


# ------------------------------------------------------------------ commit stamp
# build.sh runs in a temp git checkout whose sources are empty placeholders (only the stamp is under test); the
# payload it writes is opened, and the COMMIT inside it is what the host gets.
def _build_checkout(tmp_path):
    tree = tmp_path / "tree"
    (tree / "installer").mkdir(parents=True)
    (tree / "installer" / "build.sh").write_text(BUILD, encoding="utf-8")
    (tree / "installer" / "setup-head.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    names = re.search(r'^cp -r (.*) "\$STAGE/matinee/"$', BUILD, re.M).group(1).split()
    for n in names:
        if n in ("ops", "static", "tests"):
            (tree / n).mkdir()
            (tree / n / "placeholder").write_text("")
        else:
            (tree / n).write_text("", encoding="utf-8")
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True)
    return tree, git


def _build(tree):
    r = subprocess.run(["sh", "installer/build.sh"], cwd=tree, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _payload_file(tree, name):
    import base64, io, tarfile
    text = (tree / "dist" / "setup-matinee.sh").read_text(encoding="utf-8")
    tar = base64.b64decode(text.split("\n__PAYLOAD_BELOW__\n", 1)[1])
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        if f"matinee/{name}" not in t.getnames():
            return None
        return t.extractfile(f"matinee/{name}").read().decode("utf-8")


def test_build_writes_the_commit_file_into_the_payload(tmp_path):
    tree, _ = _build_checkout(tmp_path)
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    date = subprocess.run(["git", "-C", str(tree), "log", "-1", "--format=%cs"], capture_output=True, text=True,
                          check=True).stdout.strip()
    r = _build(tree)
    lines = _payload_file(tree, "COMMIT").splitlines()
    assert lines == [head, "", date], lines       # no remote: an empty repo line, no dirty line
    assert re.fullmatch(r"[0-9a-f]{7,40}", lines[0]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
    assert "uncommitted" not in r.stdout + r.stderr


def test_build_names_the_github_repo_without_any_credential_in_the_remote(tmp_path):
    tree, git = _build_checkout(tmp_path)
    subprocess.run([*git, "remote", "add", "origin", "https://x-access-token:SECRET@github.com/SDNick484/matinee.git"],
                   check=True)
    _build(tree)
    lines = _payload_file(tree, "COMMIT").splitlines()
    assert lines[1] == "SDNick484/matinee"
    assert "SECRET" not in _payload_file(tree, "COMMIT")


def test_build_from_a_dirty_tree_warns_and_marks_the_commit(tmp_path):
    tree, _ = _build_checkout(tmp_path)
    (tree / "app.py").write_text("changed\n", encoding="utf-8")
    r = _build(tree)
    assert "uncommitted" in r.stdout + r.stderr
    lines = _payload_file(tree, "COMMIT").splitlines()
    assert lines[3] == "dirty" and re.fullmatch(r"[0-9a-f]{7,40}", lines[0])


def test_build_outside_git_ships_no_commit_file(tmp_path):
    tree = tmp_path / "tree"
    _build_checkout(tmp_path)                       # lays the files down, with a .git we then remove
    subprocess.run(["rm", "-rf", str(tree / ".git")], check=True)
    _build(tree)
    assert _payload_file(tree, "COMMIT") is None


# ------------------------------------------------------------------ commit stamp in the installer
OLD_COMMIT = "aaaaaaa\nSDNick484/matinee\n2026-11-20\n"
NEW_COMMIT = "bbbbbbb\nSDNick484/matinee\n2026-11-27\n"


def test_sandbox_upgrade_names_the_commit_it_replaces_and_the_one_it_installs(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    sb.path("/opt/matinee/COMMIT").write_text(OLD_COMMIT, encoding="utf-8")
    r = sb.run(sb.app("NEW", commit=NEW_COMMIT + "dirty\n"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "upgrading aaaaaaa \u2192 bbbbbbb-dirty" in r.stdout


def test_sandbox_upgrade_from_a_build_without_a_commit_file_says_unknown(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    r = sb.run(sb.app("NEW", commit=NEW_COMMIT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "upgrading unknown \u2192 bbbbbbb" in r.stdout


def test_sandbox_fresh_install_prints_no_upgrade_line(tmp_path):
    sb = Sandbox(tmp_path)
    sb.path("/opt/matinee-mcp/venv/bin").mkdir(parents=True)
    r = sb.run(sb.app("NEW", commit=NEW_COMMIT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "upgrading" not in r.stdout


def test_sandbox_matinee_mcp_gets_the_same_commit_file(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    r = sb.run(sb.app("NEW", commit=NEW_COMMIT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert sb.path("/opt/matinee-mcp/COMMIT").read_text(encoding="utf-8") == NEW_COMMIT
    assert sb.path("/opt/matinee/COMMIT").read_text(encoding="utf-8") == NEW_COMMIT


def test_sandbox_a_payload_without_a_commit_file_clears_the_stale_mcp_copy(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    sb.path("/opt/matinee-mcp/COMMIT").write_text(OLD_COMMIT, encoding="utf-8")
    r = sb.run(sb.app("NEW"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not sb.path("/opt/matinee-mcp/COMMIT").exists()


def test_sandbox_a_failed_upgrade_leaves_the_mcp_commit_file_alone(tmp_path):
    sb = Sandbox(tmp_path)
    sb.install_old("OLD")
    sb.path("/opt/matinee-mcp/COMMIT").write_text(OLD_COMMIT, encoding="utf-8")
    r = sb.run(sb.app("NEW", commit=NEW_COMMIT, bad=True))
    assert r.returncode != 0 and sb.mark("/opt/matinee") == "OLD"
    assert sb.path("/opt/matinee-mcp/COMMIT").read_text(encoding="utf-8") == OLD_COMMIT


def test_the_app_can_start_beside_its_root_only_config():
    """The config file is 600 root:root and gunicorn runs as the matinee user, so the app cannot read the file: the
    init script must export the settings and say that it did (MATINEE_CONF_EXPORTED), or settings.py refuses to
    start. It must also send what gunicorn prints before its own log opens to the log, so such a refusal is seen."""
    head = (ROOT / "installer" / "setup-head.sh").read_text(encoding="utf-8")
    init = head.split("cat > /etc/init.d/matinee <<'EOF'", 1)[1].split("\nEOF\n", 1)[0]
    assert 'chmod 600 "$CONF"; chown root:root "$CONF"' in head
    assert 'command_user="matinee"' in init
    assert "\nMATINEE_CONF_EXPORTED=1\n" in init
    assert re.search(r"^export PORT .*\bMATINEE_CONF_EXPORTED\b", init, re.M)
    assert 'error_log="/var/log/matinee.log"' in init
