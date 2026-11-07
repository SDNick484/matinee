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
    for key in ("MATINEE_URL", "MATINEE_API_KEY", "MATINEE_MCP_READONLY", "MCP_HTTP_BIND", "CF_ACCESS_AUD"):
        assert key in export, key



@pytest.mark.parametrize("key,value", [
    ("MATINEE_MCP_READONLY", "1"), ("MCP_HTTP_PORT", "8719"), ("MCP_HTTP_PATH", "/matinee/mcp"),
    ("MCP_HTTP_BIND", '"$IP"'), ("MCP_PUBLIC_HOSTS", '""'), ("CF_ACCESS_TEAM_DOMAIN", '""'),
    ("CF_ACCESS_AUD", '""'), ("MCP_ALLOWED_EMAILS", '""'),
])
def test_new_settings_are_added_with_defaults(key, value):
    assert re.search(rf'^add_setting "\$CONF" {key} {re.escape(value)} ".+"$', HEAD, re.M), key


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
    for key in ("CF_ACCESS_AUD", "MCP_PUBLIC_HOSTS", "MATINEE_API_KEY"):
        assert key in cond, key


def test_health_check_and_uninstall():
    assert "Matinee MCP|http://$IP:8719/healthz|matinee-mcp" in HEAD
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
