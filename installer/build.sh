#!/bin/sh
# Build dist/setup-matinee.sh: the installer header + this app as a base64 tarball after __PAYLOAD_BELOW__.
set -eu
cd "$(dirname "$0")/.."
STAGE=$(mktemp -d /tmp/matinee-stage.XXXXXX 2>/dev/null || mktemp -d -t matinee-stage.XXXXXX 2>/dev/null || mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p dist "$STAGE/matinee"
cp -r app.py ask.py settings.py common.py db.py clients.py images.py people.py browse.py export.py wishlist.py lists.py watched_days.py youtube.py fandango.py play.py recommend.py mcp_server.py mcp_http.py mcp_remote.py requirements-mcp.txt README.md .env.example ops static tests "$STAGE/matinee/"

# COMMIT: what this build is, for /healthz, the installer's upgrade line and matinee-mcp. Line 1 is the short hash
# (the only part /healthz shows), line 2 owner/name of a GitHub origin (else empty), line 3 the commit date, and line 4
# "dirty" when tracked files have uncommitted changes. Outside git there is no file and the app says commit null.
if HASH=$(git rev-parse --short HEAD 2>/dev/null); then
  REPO=$(git config --get remote.origin.url 2>/dev/null | sed -n 's#^.*github\.com[:/]\([^/]*/[^/]*\)$#\1#p' | sed 's/\.git$//' | grep -E '^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$' || true)
  {
    echo "$HASH"
    echo "$REPO"
    git log -1 --format=%cs
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      echo dirty
      echo "warning: building from a tree with uncommitted changes: the payload's COMMIT says dirty" >&2
    fi
  } > "$STAGE/matinee/COMMIT"
else
  echo "warning: not a git checkout: no COMMIT in the payload, so /healthz will say commit null" >&2
fi

# Clean cache directories portably (avoid find -exec + which BusyBox find can reject)
find "$STAGE" \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} \; 2>/dev/null || true

# Check for GNU tar options (--owner/--group) which BusyBox tar does not support
TAR_EXTRA=""
if tar --owner=0 --group=0 --help >/dev/null 2>&1; then
  TAR_EXTRA="--owner=0 --group=0"
fi

OUT=dist/setup-matinee.sh
{
  cat installer/setup-head.sh
  echo "__PAYLOAD_BELOW__"
  # shellcheck disable=SC2086  # TAR_EXTRA is empty or two flags: it must word-split
  ( cd "$STAGE" && tar $TAR_EXTRA -czf - matinee ) | base64
} > "$OUT"
chmod +x "$OUT"
sh -n "$OUT"
echo "Built $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
