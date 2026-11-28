"""Engineering rule 7 (CLAUDE.md): every setting the Python code reads is in three places, with a comment line
in .env.example, an add_setting call in installer/setup-head.sh, and a name on an export line of that script.
This test reads the code, not a list, so a new os.environ read fails until all three places name it."""
import ast
import re
from pathlib import Path

import app as matinee

ROOT = Path(matinee.APP_DIR)
SETUP = ROOT / "installer" / "setup-head.sh"

# Names the code reads from the environment that are not Matinee settings (the process's own, not ours to
# document). A real setting belongs in all three places instead.
# MATINEE_CONF_EXPORTED: set by the matinee init script itself, never by a person; it tells settings.py that the
# root-only config file it cannot read was exported into the environment.
NOT_SETTINGS = {"MATINEE_CONF_EXPORTED"}
# Settings exempt from the .env.example and add_setting checks (still checked for an export line). The repo keeps
# one team-domain key, CF_TEAM_DOMAIN, which matinee-mcp falls back to; tests/test_installer_static.py::
# test_team_domain_is_one_setting pins that and forbids both the add_setting line and a .env.example line.
ONE_TEAM_DOMAIN_KEY = {"CF_ACCESS_TEAM_DOMAIN"}


def _is_env(expr):
    return ast.unparse(expr) in ("os.environ", "environ", "env")


def env_reads():
    """{KEY: [file, ...]} for every literal (or module-constant) name read from an environment mapping."""
    reads = {}
    for path in [*sorted(ROOT.glob("*.py")), *sorted((ROOT / "ops").glob("*.py"))]:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = node.value.value
        for node in ast.walk(tree):
            arg = None
            if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("get", "getenv") and (_is_env(node.func.value) or ast.unparse(node.func.value) == "os"):
                    arg = node.args[0]
            elif isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Name) and node.func.id == "env_int":
                arg = node.args[0]
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_env(node.value):
                arg = node.slice
            if arg is None:
                continue
            key = arg.value if isinstance(arg, ast.Constant) else consts.get(getattr(arg, "id", ""))
            if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                reads.setdefault(key, [])
                if path.name not in reads[key]:
                    reads[key].append(path.name)
    return reads


def documented_keys():
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))


def add_setting_keys():
    return set(re.findall(r'^add_setting "\$CONF" ([A-Z][A-Z0-9_]*) ', SETUP.read_text(encoding="utf-8"), re.M))


def exported_keys():
    keys = set()
    for line in SETUP.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*export\s+(.+)$", line)
        if m:
            keys |= {tok.split("=", 1)[0] for tok in m.group(1).split() if re.fullmatch(r"[A-Z][A-Z0-9_]*(=.*)?", tok)}
    return keys


def parity_gaps():
    """One message per (key, place) that is missing; empty when every read setting is in all three places."""
    documented, added, exported = documented_keys(), add_setting_keys(), exported_keys()
    gaps = []
    for key, files in sorted(env_reads().items()):
        if key in NOT_SETTINGS:
            continue
        where = f"read in {', '.join(files)}"
        if key not in documented and key not in ONE_TEAM_DOMAIN_KEY:
            gaps.append(f"{key} ({where}) is missing from .env.example: add a comment line and `{key}=` there")
        if key not in added and key not in ONE_TEAM_DOMAIN_KEY:
            gaps.append(f"{key} ({where}) is missing from installer/setup-head.sh's add_setting list: "
                        f"add `add_setting \"$CONF\" {key} ...`")
        if key not in exported:
            gaps.append(f"{key} ({where}) is missing from every export line in installer/setup-head.sh: "
                        f"add it to the export line of the init script that needs it")
    return gaps


def test_scanner_sees_the_known_settings():
    # guards the scanner itself: a regex or AST change that matched nothing would pass the parity check silently
    reads = env_reads()
    assert len(reads) >= 25, sorted(reads)
    for key in ("LAN_EDIT", "PUBLIC_VIEW", "MATINEE_READ_KEY", "CF_TEAM_DOMAIN", "MCP_COMMIT_FILE",
                "MCP_AUTOMATION_TIERS", "PORT"):
        assert key in reads, key


def test_every_read_setting_is_in_env_example_add_setting_and_an_export_line():
    gaps = parity_gaps()
    assert not gaps, "settings out of step (rule 7):\n  " + "\n  ".join(gaps)


def test_not_settings_allow_list_names_only_keys_the_code_reads():
    reads = env_reads()
    stale = sorted(k for k in NOT_SETTINGS if k not in reads)
    assert not stale, f"remove from NOT_SETTINGS, the code no longer reads them: {stale}"
