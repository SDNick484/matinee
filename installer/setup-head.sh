#!/bin/sh
# Matinee (family movie collection) installer for Alpine Linux (OpenRC).
# Re-runnable: upgrades the app in place; never touches existing data, and only *adds*
# missing settings to existing config files.
#
#   sh setup-matinee.sh               install / upgrade
#   MATINEE_PORT=8769 sh setup-matinee.sh
#   sh setup-matinee.sh --uninstall   remove app + service (keeps /var/lib/matinee and backups)
set -eu
APP_DIR=/opt/matinee
DATA_DIR=/var/lib/matinee
CONF=/etc/conf.d/matinee
OPS_CONF="${OPS_CONF:-/etc/conf.d/ops}"
SVC_USER=matinee
PORT="${MATINEE_PORT:-8769}"

if [ "$(id -u)" != 0 ]; then echo "Run as root"; exit 1; fi
command -v apk >/dev/null || { echo "This installer is for Alpine (apk)."; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  rc-service matinee stop 2>/dev/null || true
  rc-update del matinee default 2>/dev/null || true
  rm -f /etc/init.d/matinee /etc/periodic/daily/matinee-backup
  rm -rf "$APP_DIR"
  echo "Removed app, service and backup job. Data kept in $DATA_DIR, config in $CONF, backups in /var/backups/matinee."
  exit 0
fi

# an existing install keeps its port
[ -f "$CONF" ] && PORT=$(sed -n 's/^PORT=//p' "$CONF" | head -n1) && PORT=${PORT:-8769}

echo "==> Installing packages"
apk add --no-cache python3 py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite >/dev/null

[ -x /etc/init.d/matinee ] && rc-service matinee stop >/dev/null 2>&1 || true
if netstat -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$PORT\$"; then
  echo "Port $PORT is already in use by something else. Re-run with MATINEE_PORT=<free port>."; exit 1
fi

echo "==> Creating user and folders"
# BusyBox adduser -S puts a new user in "nogroup" unless told otherwise, so make the app's own group first.
grep -q "^$SVC_USER:" /etc/group || addgroup -S "$SVC_USER"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  adduser -S -D -H -h "$DATA_DIR" -s /sbin/nologin -G "$SVC_USER" "$SVC_USER"
elif ! id -nG "$SVC_USER" | tr ' ' '\n' | grep -qx "$SVC_USER"; then
  addgroup "$SVC_USER" "$SVC_USER"   # user from an older install: add it to its group
fi
mkdir -p "$APP_DIR" "$DATA_DIR/images" /var/backups/matinee

echo "==> Unpacking app"
TMP=$(mktemp -d /tmp/matinee-tmp.XXXXXX 2>/dev/null || mktemp -d -t matinee-tmp.XXXXXX 2>/dev/null || mktemp -d)
sed -n '/^__PAYLOAD_BELOW__$/,$p' "$0" | tail -n +2 | base64 -d | ( cd "$TMP" && tar -xzf - )
rm -rf "$APP_DIR.new" && mv "$TMP/matinee" "$APP_DIR.new"
rm -rf "$APP_DIR.old"; [ -d "$APP_DIR" ] && mv "$APP_DIR" "$APP_DIR.old"
mv "$APP_DIR.new" "$APP_DIR"; rm -rf "$APP_DIR.old" "$TMP"
chown -R root:root "$APP_DIR"
chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# ---------------------------------------------------------------- config
add_setting() {  # add_setting FILE KEY VALUE COMMENT — only if KEY isn't there yet
  grep -q "^$2=" "$1" 2>/dev/null && return 0
  printf '\n# %s\n%s=%s\n' "$4" "$2" "$3" >> "$1"
  ADDED="${ADDED:-} $2"
}

if [ ! -f "$CONF" ]; then
  echo "==> Writing $CONF"
  cat > "$CONF" <<EOF
# Matinee (movie collection) configuration — after editing: rc-service matinee restart
PORT=$PORT
DATA_DIR=$DATA_DIR
EOF
fi
add_setting "$CONF" CF_TEAM_DOMAIN "" "Cloudflare Access team domain, e.g. yourteam.cloudflareaccess.com"
add_setting "$CONF" CF_AUD "" "AUD tag of the Matinee Access application (path: admin)"
add_setting "$CONF" ADMIN_EMAILS "" "Only these emails may edit (comma separated); empty = anyone the Access policy lets in"
add_setting "$CONF" LAN_EDIT 1 "1 = direct LAN requests (not through Cloudflare) may edit"
add_setting "$CONF" PUBLIC_VIEW 1 "1 = anyone can browse the collection at /; 0 = everything behind /admin"
add_setting "$CONF" TMDB_READ_TOKEN "" "TMDB API Read Access Token (themoviedb.org -> Settings -> API); adding movies needs it"
add_setting "$CONF" MATINEE_API_KEY "" "Optional API key for headless automation and MCP server (Authorization: Bearer <key>)"
chmod 640 "$CONF"; chown root:"$SVC_USER" "$CONF"

# health checks: add Matinee to HEALTH_CHECKS if ops config exists
HC_LINE="Matinee|http://127.0.0.1:$PORT/healthz|matinee"
if [ -f "$OPS_CONF" ] && ! grep -q '^HEALTH_CHECKS=' "$OPS_CONF"; then
  cat >> "$OPS_CONF" <<EOF

# Apps checked every 15 min (name|url|service); added by setup-matinee.sh
HEALTH_CHECKS="$HC_LINE"
EOF
  HC=added
elif [ -f "$OPS_CONF" ] && ! grep -q '|matinee' "$OPS_CONF"; then
  # append inside the existing (usually multi-line) HEALTH_CHECKS="..." value, before its closing quote
  awk -v add="$HC_LINE" '
    !done && /^HEALTH_CHECKS="/ { inb = 1; if ($0 == "HEALTH_CHECKS=\"") { print; next } }
    inb && /"[ \t]*$/ { sub(/"[ \t]*$/, ""); print; print add "\""; inb = 0; done = 1; next }
    { print }' "$OPS_CONF" > "$OPS_CONF.new"
  if grep -q '|matinee' "$OPS_CONF.new" && sh -n "$OPS_CONF.new" 2>/dev/null; then
    cat "$OPS_CONF.new" > "$OPS_CONF"; HC=added
  else
    HC=manual
  fi
  rm -f "$OPS_CONF.new"
fi

# ---------------------------------------------------------------- service
cat > /etc/init.d/matinee <<'EOF'
#!/sbin/openrc-run
name="matinee"
description="Matinee movie collection"
: ${PORT:=8769}
: ${DATA_DIR:=/var/lib/matinee}
export HOME=/var/lib/matinee  # newer gunicorn keeps a small control socket under $HOME
export PORT DATA_DIR CF_TEAM_DOMAIN CF_AUD ADMIN_EMAILS LAN_EDIT PUBLIC_VIEW TMDB_READ_TOKEN MATINEE_API_KEY
supervisor="supervise-daemon"
respawn_delay=5
respawn_max=10
respawn_period=600
command="/usr/bin/gunicorn"
command_args="--preload --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:${PORT} --chdir /opt/matinee --access-logfile /var/log/matinee.log --error-logfile /var/log/matinee.log app:app"
command_user="matinee"
depend() { need net; after firewall; }
start_pre() { checkpath -f -o matinee /var/log/matinee.log; }
EOF
chmod +x /etc/init.d/matinee

# ---------------------------------------------------------------- backups
echo "==> Installing nightly backup"
mkdir -p /etc/periodic/daily
install -m 755 "$APP_DIR/ops/matinee-backup" /etc/periodic/daily/matinee-backup
rc-update add crond default >/dev/null 2>&1 || true
rc-service crond start >/dev/null 2>&1 || true

echo "==> Starting service"
rc-update add matinee default >/dev/null 2>&1 || true
rc-service matinee start
i=0
until wget -qO- "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; do
  i=$((i+1)); [ $i -gt 25 ] && { echo "Service didn't come up; see /var/log/matinee.log"; tail -n 20 /var/log/matinee.log; exit 1; }
  sleep 1
done

echo "==> Taking a first backup"
/etc/periodic/daily/matinee-backup || echo "    (backup reported a problem; see /var/log/ops.log)"

IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)
echo
echo "Matinee is running:  http://$IP:$PORT/        (editing on the LAN: http://$IP:$PORT/admin)"
echo "Health: $(wget -qO- http://127.0.0.1:$PORT/healthz)"
[ -n "${ADDED:-}" ] && echo "New settings added:${ADDED}"
grep -q '^TMDB_READ_TOKEN=$' "$CONF" && echo "  - TMDB is off until you set TMDB_READ_TOKEN in $CONF (then: rc-service matinee restart)"
grep -q '^CF_AUD=$' "$CONF" && echo "  - Set CF_AUD in $CONF after creating the Access app for <your-domain>/admin"
[ "${HC:-}" = added ] && echo "  - Added Matinee to HEALTH_CHECKS in $OPS_CONF"
[ "${HC:-}" = manual ] && echo "  - Couldn't edit HEALTH_CHECKS in $OPS_CONF: add  $HC_LINE"
[ -f "$OPS_CONF" ] || echo "  - No $OPS_CONF: backups still run nightly into /var/backups/matinee"
echo "  - Tunnel: <your-domain> -> http://$IP:$PORT"
echo "Config: $CONF   Data: $DATA_DIR   Logs: /var/log/matinee.log, /var/log/ops.log"
exit 0
