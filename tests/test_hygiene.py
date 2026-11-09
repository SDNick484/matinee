"""No personal LAN addresses, hostnames or container IDs in the published tree.

Like Cookbook-Collection's test_hygiene.py. Design history (docs/superpowers/, openspec/, .superpowers/)
records what was true at the time and is exempt. Docs and examples use 192.168.1.100, mcp.example.com
and "the Matinee host" instead.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXEMPT = ("docs/superpowers/", "openspec/", ".superpowers/")
# Built from fragments so this file does not match its own pattern.
PATTERN = re.compile("|".join((r"192\.168\.1\.1" + "74", "nick" + "andrade", r"CT ?2" + "10")))
SKIP_DIRS = {".git", "dist", "data", "__pycache__", ".pytest_cache", ".venv", "node_modules"}


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
        return [ROOT / p for p in out.stdout.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):     # no git (e.g. a source tarball): walk the tree
        return [p for p in ROOT.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


def test_no_personal_identifiers():
    hits = []
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(EXEMPT) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:                      # fonts, images
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not hits, "personal identifiers found:\n" + "\n".join(hits)
