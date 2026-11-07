#!/bin/sh
# Build dist/setup-matinee.sh: the installer header + this app as a base64 tarball after __PAYLOAD_BELOW__.
set -eu
cd "$(dirname "$0")/.."
STAGE=$(mktemp -d /tmp/matinee-stage.XXXXXX 2>/dev/null || mktemp -d -t matinee-stage.XXXXXX 2>/dev/null || mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p dist "$STAGE/matinee"
cp -r app.py ask.py recommend.py mcp_server.py README.md .env.example ops static tests "$STAGE/matinee/"

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
  ( cd "$STAGE" && tar $TAR_EXTRA -czf - matinee ) | base64
} > "$OUT"
chmod +x "$OUT"
sh -n "$OUT"
echo "Built $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
