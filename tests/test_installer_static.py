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
    for key in ("MATINEE_URL", "MATINEE_MCP_READONLY", "MCP_HTTP_BIND", "CF_ACCESS_AUD"):
        assert key in export, key
    # the keys are exported by the per-mode case below, never unconditionally
    assert "MATINEE_API_KEY" not in export and "MATINEE_READ_KEY" not in export



@pytest.mark.parametrize("key,value", [
    ("MATINEE_MCP_READONLY", "1"), ("MCP_HTTP_PORT", "8719"), ("MCP_HTTP_PATH", "/matinee/mcp"),
    ("MCP_HTTP_BIND", '"$IP"'), ("MCP_PUBLIC_HOSTS", '""'),
    ("CF_ACCESS_AUD", '""'), ("MCP_ALLOWED_EMAILS", '""'),
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


def test_build_copies_mcp_files():
    for f in ("mcp_http.py", "mcp_remote.py", "requirements-mcp.txt"):
        assert f in BUILD, f


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
NEW_KEYS = ["MATINEE_MCP_READONLY", "MCP_HTTP_PORT", "MCP_HTTP_BIND", "MCP_HTTP_PATH", "MCP_PUBLIC_HOSTS",
            "CF_ACCESS_AUD", "MCP_ALLOWED_EMAILS"]


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


def test_mcp_setup_states_what_the_api_key_adds_to_reads():
    # Security framing: the key is not just for search_tmdb; /admin/api also returns copies' added_by
    # (the editor's Access email). Pinned by test_rest_get_movie_added_by_only_with_key.
    row = next(l for l in (ROOT / "docs" / "mcp-setup.md").read_text(encoding="utf-8").splitlines()
               if l.startswith("| `MATINEE_API_KEY` |"))
    assert "search_tmdb" in row and "added_by" in row
    assert "adds only `search_tmdb`" not in row


def test_lan_hosts_setting_is_written_and_exported():
    assert re.search(r'^add_setting "\$CONF" LAN_HOSTS "" ".+"$', HEAD, re.M)
    export = next(l for l in HEAD.splitlines() if l.startswith("export PORT "))
    assert " LAN_EDIT LAN_HOSTS " in f" {export} "
    assert re.search(r"^# .+\nLAN_HOSTS=$", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)


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
    s = _init_script()
    m = re.search(r"^case .*?^esac$", s, re.S | re.M)
    assert m, "matinee-mcp init script has no key-selection case"
    return m.group(0)


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
    out = subprocess.run(["sh", "-c", f'CONF="{conf}"\n{conf_val}\n{_fn("mcp_key_name")}\nmcp_key_name'],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ("MATINEE_READ_KEY" if mode == "read" else "MATINEE_API_KEY")


def test_not_started_banner_names_the_mode_key():
    line = next(l for l in HEAD.splitlines() if l.strip().startswith("for k in MCP_PUBLIC_HOSTS"))
    assert '"$(mcp_key_name)"' in line and "MATINEE_API_KEY" not in line
