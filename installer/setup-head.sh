#!/bin/sh
# Matinee (family movie collection) installer for Alpine Linux (OpenRC).
# Re-runnable: upgrades the app in place; never touches existing data, and only *adds*
# missing settings to existing config files.
#
#   sh setup-matinee.sh               install / upgrade
#   MATINEE_PORT=8771 sh setup-matinee.sh
#   sh setup-matinee.sh --uninstall   remove app + service (keeps /var/lib/matinee and backups)
set -eu
APP_DIR=/opt/matinee
DATA_DIR=/var/lib/matinee
CONF=/etc/conf.d/matinee
OPS_CONF="${OPS_CONF:-/etc/conf.d/ops}"
SVC_USER=matinee
MCP_USER=matinee-mcp
MCP_VENV=/opt/matinee-mcp/venv
PORT="${MATINEE_PORT:-8771}"

if [ "$(id -u)" != 0 ]; then echo "Run as root"; exit 1; fi
command -v apk >/dev/null || { echo "This installer is for Alpine (apk)."; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  rc-service matinee-mcp stop 2>/dev/null || true
  rc-update del matinee-mcp default 2>/dev/null || true
  rc-service matinee stop 2>/dev/null || true
  rc-update del matinee default 2>/dev/null || true
  rm -f /etc/init.d/matinee /etc/init.d/matinee-mcp /etc/periodic/daily/matinee-backup /etc/periodic/daily/matinee-refresh /etc/periodic/weekly/matinee-restore-test
  rm -rf "$APP_DIR" /opt/matinee-mcp
  echo "Removed app, service and backup, refresh and restore-test jobs. Data kept in $DATA_DIR, config in $CONF, backups in /var/backups/matinee."
  exit 0
fi

# an existing install keeps its port
[ -f "$CONF" ] && PORT=$(sed -n 's/^PORT=//p' "$CONF" | head -n1) && PORT=${PORT:-8771}

echo "==> Installing packages"
apk add --no-cache python3 py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite >/dev/null

[ -x /etc/init.d/matinee-mcp ] && rc-service matinee-mcp stop >/dev/null 2>&1 || true
[ -x /etc/init.d/matinee ] && rc-service matinee stop >/dev/null 2>&1 || true
IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)
IP=${IP:-127.0.0.1}
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
# The MCP HTTP service gets its own user: no database access, no home.
grep -q "^$MCP_USER:" /etc/group || addgroup -S "$MCP_USER"
id "$MCP_USER" >/dev/null 2>&1 || adduser -S -D -H -h /nonexistent -s /sbin/nologin -G "$MCP_USER" "$MCP_USER"
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
gen_key() {  # 43 URL-safe characters (256 bits) for a new API key
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
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
add_setting "$CONF" LAN_HOSTS "" "Host names LAN editing may use besides IPs and localhost, e.g. matinee.lan (comma separated)"
add_setting "$CONF" PUBLIC_VIEW 1 "1 = anyone can browse the collection at /; 0 = everything behind /admin"
add_setting "$CONF" TMDB_READ_TOKEN "" "TMDB API Read Access Token (themoviedb.org -> Settings -> API); adding movies needs it"
add_setting "$CONF" OMDB_API_KEY "" "Optional OMDb API key (omdbapi.com/apikey.aspx) for IMDb, Rotten Tomatoes & Metacritic scores"
add_setting "$CONF" MATINEE_API_KEY "" "Optional API key for headless automation and MCP server (Authorization: Bearer <key>)"
add_setting "$CONF" MATINEE_READ_KEY "$(gen_key)" "Key that may only GET /admin/api (watch notes included); read-only matinee-mcp uses it. Keep it different from MATINEE_API_KEY"
add_setting "$CONF" ANTHROPIC_SEARCH_KEY "" "Anthropic API key for Ask (AI search); empty = Ask is off"
add_setting "$CONF" ANTHROPIC_SEARCH_MODEL "claude-haiku-5-5" "Model Ask uses"
add_setting "$CONF" ASK_DAILY_LIMIT "200" "Ask questions per day across all editors"
# Remote MCP (matinee-mcp). MCP_HTTP_BIND must be this host's LAN address because cloudflared runs in Docker.
add_setting "$CONF" MATINEE_MCP_READONLY 1 "1 = the Claude app can only read; 0 = it may also rate, log watches and add titles"
add_setting "$CONF" MCP_HTTP_BIND "$IP" "Address matinee-mcp listens on (cloudflared reaches it here)"
add_setting "$CONF" MCP_HTTP_PORT 8719 "Port matinee-mcp listens on"
add_setting "$CONF" MCP_HTTP_PATH /matinee/mcp "URL path of the MCP endpoint (the tunnel route is ^/matinee/)"
add_setting "$CONF" MCP_PUBLIC_HOSTS "" "Public hostname(s) of the tunnel, e.g. mcp.example.com (required)"
add_setting "$CONF" CF_ACCESS_AUD "" "AUD tag of the Access application that fronts mcp.<your-domain> (required)"
add_setting "$CONF" MCP_ALLOWED_EMAILS "" "Only these Access emails may use the MCP tools (comma separated); empty = anyone Access lets in"
# Holds API keys, so root only: OpenRC and the cron jobs run as root; services get what they need from the environment.
chmod 600 "$CONF"; chown root:root "$CONF"

# health checks: add Matinee and Matinee MCP to HEALTH_CHECKS if the ops config exists
add_health_check() {  # add_health_check "Name|url|service" service  (sets HC_ADDED / HC_MANUAL)
  [ -f "$OPS_CONF" ] || return 0
  # already there; a longer service name that merely starts with this one (matinee-mcp) doesn't count
  grep -Eq "\|$2([^-A-Za-z0-9_]|\$)" "$OPS_CONF" && return 0
  if ! grep -q '^HEALTH_CHECKS=' "$OPS_CONF"; then
    cat >> "$OPS_CONF" <<EOF

# Apps checked every 15 min (name|url|service); added by setup-matinee.sh
HEALTH_CHECKS="$1"
EOF
    HC_ADDED="${HC_ADDED:-} $2"
    return 0
  fi
  # append inside the existing (usually multi-line) HEALTH_CHECKS="..." value, before its closing quote
  awk -v add="$1" '
    !done && /^HEALTH_CHECKS="/ { inb = 1; if ($0 == "HEALTH_CHECKS=\"") { print; next } }
    inb && /"[ \t]*$/ { sub(/"[ \t]*$/, ""); print; print add "\""; inb = 0; done = 1; next }
    { print }' "$OPS_CONF" > "$OPS_CONF.new"
  if grep -Eq "\|$2([^-A-Za-z0-9_]|\$)" "$OPS_CONF.new" && sh -n "$OPS_CONF.new" 2>/dev/null; then
    cat "$OPS_CONF.new" > "$OPS_CONF"; HC_ADDED="${HC_ADDED:-} $2"
  else
    HC_MANUAL="${HC_MANUAL:-}
      $1"
  fi
  rm -f "$OPS_CONF.new"
}
HC_LINE="Matinee|http://127.0.0.1:$PORT/healthz|matinee"
add_health_check "$HC_LINE" matinee

# ---------------------------------------------------------------- service
cat > /etc/init.d/matinee <<'EOF'
#!/sbin/openrc-run
name="matinee"
description="Matinee movie collection"
: ${PORT:=8771}
: ${DATA_DIR:=/var/lib/matinee}
export HOME=/var/lib/matinee  # newer gunicorn keeps a small control socket under $HOME
export PORT DATA_DIR CF_TEAM_DOMAIN CF_AUD ADMIN_EMAILS LAN_EDIT LAN_HOSTS PUBLIC_VIEW TMDB_READ_TOKEN MATINEE_API_KEY MATINEE_READ_KEY OMDB_API_KEY ANTHROPIC_SEARCH_KEY ANTHROPIC_SEARCH_MODEL ASK_DAILY_LIMIT
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

cat > /etc/init.d/matinee-mcp <<'EOF'
#!/sbin/openrc-run
name="matinee-mcp"
description="Matinee MCP server (Streamable HTTP, behind Cloudflare Access)"
# OpenRC runs this as root, so it can read the root-only config; the service user never reads it.
[ -r /etc/conf.d/matinee ] && . /etc/conf.d/matinee
: ${PORT:=8771}
: ${MATINEE_URL:=http://127.0.0.1:${PORT}}
# Only what this service needs: TMDB_READ_TOKEN and OMDB_API_KEY deliberately stay out of its environment,
# and it gets one Matinee key: the GET-only MATINEE_READ_KEY while read-only (mirrors mcp_http.readonly()),
# MATINEE_API_KEY only when MATINEE_MCP_READONLY is 0/false/no/off.
case "$(printf '%s' "${MATINEE_MCP_READONLY:-}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')" in
  0|false|no|off) unset MATINEE_READ_KEY; export MATINEE_API_KEY ;;
  *) unset MATINEE_API_KEY; export MATINEE_READ_KEY ;;
esac
export MATINEE_URL MATINEE_MCP_READONLY MCP_HTTP_BIND MCP_HTTP_PORT MCP_HTTP_PATH MCP_PUBLIC_HOSTS CF_ACCESS_TEAM_DOMAIN CF_ACCESS_AUD MCP_ALLOWED_EMAILS CF_TEAM_DOMAIN
supervisor="supervise-daemon"
respawn_delay=5
respawn_max=10
respawn_period=600
command="/opt/matinee-mcp/venv/bin/python"
command_args="/opt/matinee/mcp_http.py"
command_user="matinee-mcp:matinee-mcp"
directory="/opt/matinee"
output_log="/var/log/matinee-mcp.log"
error_log="/var/log/matinee-mcp.log"
depend() { need net matinee; after firewall; }
start_pre() { checkpath -f -m 0640 -o matinee-mcp:matinee-mcp /var/log/matinee-mcp.log; }
EOF
chmod +x /etc/init.d/matinee-mcp

# The MCP stack is not packaged for Alpine (no py3-mcp), so this one service gets a pip venv.
# A pip or network failure only skips matinee-mcp; the main app still installs and starts.
echo "==> Installing MCP HTTP service (pip venv)"
# shellcheck disable=SC2015  # deliberate: any failure in the chain (including MCP_OK=1) falls through to the warning branch
apk add --no-cache py3-pip >/dev/null 2>&1 && python3 -m venv "$MCP_VENV" && "$MCP_VENV/bin/pip" install --no-cache-dir -q -r "$APP_DIR/requirements-mcp.txt" && MCP_OK=1 || { MCP_OK=0; echo "    WARNING: could not set up the MCP venv (needs PyPI access); matinee-mcp will not start. Re-run this installer to retry."; }

# ---------------------------------------------------------------- backups & maintenance
echo "==> Installing nightly backup and refresh jobs and the weekly restore test"
mkdir -p /etc/periodic/daily /etc/periodic/weekly
install -m 755 "$APP_DIR/ops/matinee-backup" /etc/periodic/daily/matinee-backup
install -m 755 "$APP_DIR/ops/matinee-refresh" /etc/periodic/daily/matinee-refresh
install -m 755 "$APP_DIR/ops/matinee-restore-test" /etc/periodic/weekly/matinee-restore-test
rc-update add crond default >/dev/null 2>&1 || true
rc-service crond start >/dev/null 2>&1 || true

# ---------------------------------------------------------------- log rotation
# Optional: logrotate is not a required package, so this only acts when it is already installed. It also steps aside
# when any existing logrotate config (a hand-made rule, or the file an earlier run wrote) already names these logs.
# LOGROTATE_CONF and LOGROTATE_D exist so the tests can point it at a temp folder.
install_logrotate() {
  command -v logrotate >/dev/null 2>&1 || return 0
  lr_dir="${LOGROTATE_D:-/etc/logrotate.d}"
  [ -d "$lr_dir" ] || return 0
  if grep -qs -e '/var/log/matinee\.log' -e '/var/log/matinee-mcp\.log' "${LOGROTATE_CONF:-/etc/logrotate.conf}" "$lr_dir"/*; then
    return 0
  fi
  printf '%s\n' '/var/log/matinee.log /var/log/matinee-mcp.log {' \
    '    weekly' '    rotate 8' '    missingok' '    notifempty' '    compress' '    delaycompress' '    copytruncate' '}' \
    > "$lr_dir/matinee"
  echo "    Added $lr_dir/matinee (weekly, keep 8)"
}
echo "==> Checking log rotation"
install_logrotate

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

conf_val() { sed -n "s/^$1=//p" "$CONF" | head -n1 | tr -d "\"'"; }
mcp_key_name() {  # the key matinee-mcp needs in its configured mode (same rule as its init script)
  case "$(conf_val MATINEE_MCP_READONLY | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) echo MATINEE_API_KEY ;;
    *) echo MATINEE_READ_KEY ;;
  esac
}
MCP_STATE=off
if [ "${MCP_OK:-0}" = 1 ] && [ -n "$(conf_val CF_ACCESS_AUD)" ] && [ -n "$(conf_val MCP_PUBLIC_HOSTS)" ] && [ -n "$(conf_val "$(mcp_key_name)")" ]; then
  echo "==> Starting matinee-mcp"
  rc-service matinee-mcp start || true
  MCP_STATE=failed
  i=0
  while [ $i -lt 20 ]; do
    wget -qO- "http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT)/healthz" >/dev/null 2>&1 && { MCP_STATE=up; break; }
    i=$((i+1)); sleep 1
  done
  [ "$MCP_STATE" = up ] || { echo "    matinee-mcp didn't answer; see /var/log/matinee-mcp.log"; tail -n 10 /var/log/matinee-mcp.log 2>/dev/null || true; }
fi
# Only a service that is actually answering goes into the runlevel and the 15-minute health checks;
# an unconfigured one would otherwise be flagged (and respawned at boot) for nothing.
if [ "$MCP_STATE" = up ]; then
  rc-update add matinee-mcp default >/dev/null 2>&1 || true
  HC_MCP_LINE="Matinee MCP|http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT)/healthz|matinee-mcp"
  add_health_check "$HC_MCP_LINE" matinee-mcp
fi

echo
echo "Matinee is running:  http://$IP:$PORT/        (editing on the LAN: http://$IP:$PORT/admin)"
echo "Health: $(wget -qO- "http://127.0.0.1:$PORT/healthz")"
[ -n "${ADDED:-}" ] && echo "New settings added:${ADDED}"
grep -q '^TMDB_READ_TOKEN=$' "$CONF" && echo "  - TMDB is off until you set TMDB_READ_TOKEN in $CONF (then: rc-service matinee restart)"
grep -q '^CF_AUD=$' "$CONF" && echo "  - Set CF_AUD in $CONF after creating the Access app for <your-domain>/admin"
[ -n "${HC_ADDED:-}" ] && echo "  - Added to HEALTH_CHECKS in $OPS_CONF:${HC_ADDED}"
[ -n "${HC_MANUAL:-}" ] && echo "  - Couldn't edit HEALTH_CHECKS in $OPS_CONF: add these lines:${HC_MANUAL}"
[ -f "$OPS_CONF" ] || echo "  - No $OPS_CONF: backups still run nightly into /var/backups/matinee"
echo "  - Tunnel: <your-domain> -> http://$IP:$PORT"
case "$MCP_STATE" in
  up) echo "Matinee MCP is running (read-only unless MATINEE_MCP_READONLY=0): http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT)/healthz" ;;
  failed) echo "Matinee MCP did not come up: see /var/log/matinee-mcp.log" ;;
  *)
    echo "Matinee MCP (remote access from the Claude app) was NOT started:"
    [ "${MCP_OK:-0}" = 1 ] || echo "  - The MCP venv could not be built (see the warning above); re-run when PyPI is reachable"
    for k in MCP_PUBLIC_HOSTS CF_ACCESS_AUD "$(mcp_key_name)"; do
      [ -n "$(conf_val "$k")" ] || echo "  - Set $k in $CONF"
    done
    echo "  - Then start it:  rc-service matinee-mcp start && rc-update add matinee-mcp default"
    echo "  - (re-run this installer afterwards to add it to HEALTH_CHECKS)"
    ;;
esac
[ -n "$(conf_val MCP_ALLOWED_EMAILS)" ] || echo "  - MCP_ALLOWED_EMAILS is empty: anyone the Access policy admits can read the collection. Set it in $CONF, then rc-service matinee-mcp restart"
echo "  - MCP tunnel route: mcp.<your-domain>, path ^/matinee/ -> http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT) (leave the Host header override unset)"
echo "Config: $CONF   Data: $DATA_DIR   Logs: /var/log/matinee.log, /var/log/matinee-mcp.log, /var/log/ops.log"
exit 0
