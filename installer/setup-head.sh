#!/bin/sh
# Matinee (family movie collection) installer for Alpine Linux (OpenRC).
# Re-runnable: upgrades the app in place; never touches existing data, and only *adds*
# missing settings to existing config files.
#
# An upgrade does the slow work (unpack, venv refresh) and takes a "matinee-backup pre-upgrade" copy while the old
# version is still serving; only then does it stop the services, swap the directories (the old one stays in
# /opt/matinee.old until the next upgrade), start and poll /healthz. Any failure after the stop puts the old
# directory back and starts the services again.
#
#   sh setup-matinee.sh               install / upgrade
#   MATINEE_PORT=8771 sh setup-matinee.sh
#   sh setup-matinee.sh --uninstall   remove app, service, users, log rotation and health checks (keeps /var/lib/matinee and backups)
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

remove_health_check() {  # remove_health_check SERVICE — drop that service's entry from HEALTH_CHECKS in $OPS_CONF
  [ -f "$OPS_CONF" ] || return 0
  grep -Eq "\|$1([^-A-Za-z0-9_]|\$)" "$OPS_CONF" || return 0
  # an entry is name|url|service on a line of its own; the last one of the value also carries its closing quote
  awk -v svc="$1" '
    { line[NR] = $0 }
    END {
      m = 0
      for (i = 1; i <= NR; i++) {
        e = line[i]; pre = ""; post = ""
        if (e ~ /^HEALTH_CHECKS="/) { pre = "HEALTH_CHECKS=\""; e = substr(e, 16) }
        if (e ~ /"[ \t]*$/) { sub(/"[ \t]*$/, "", e); post = "\"" }
        t = e; gsub(/^[ \t]+|[ \t]+$/, "", t)
        n = split(t, f, "|")
        if (n == 3 && f[3] == svc) {
          if (pre != "") out[++m] = pre post              # keep the start of the value (and its closing quote)
          else if (post != "" && m > 0) out[m] = out[m] post   # the closing quote moves to the line before
        } else out[++m] = line[i]
      }
      for (i = 1; i <= m; i++) print out[i]
    }' "$OPS_CONF" > "$OPS_CONF.new"
  if ! grep -Eq "\|$1([^-A-Za-z0-9_]|\$)" "$OPS_CONF.new" && sh -n "$OPS_CONF.new" 2>/dev/null; then
    cat "$OPS_CONF.new" > "$OPS_CONF"
  else
    echo "    Couldn't edit HEALTH_CHECKS in $OPS_CONF: remove the $1 line yourself."
  fi
  rm -f "$OPS_CONF.new"
}

if [ "${1:-}" = "--uninstall" ]; then
  rc-service matinee-mcp stop 2>/dev/null || true
  rc-update del matinee-mcp default 2>/dev/null || true
  rc-service matinee stop 2>/dev/null || true
  rc-update del matinee default 2>/dev/null || true
  rm -f /etc/init.d/matinee /etc/init.d/matinee-mcp /etc/periodic/daily/matinee-backup /etc/periodic/daily/matinee-refresh /etc/periodic/weekly/matinee-restore-test
  rm -rf "$APP_DIR" "$APP_DIR.old" "$APP_DIR.new" "$APP_DIR.failed" /opt/matinee-mcp
  # the logrotate file this installer wrote (a hand-made one is left alone), the health checks it added, and its users
  lr="${LOGROTATE_D:-/etc/logrotate.d}/matinee"
  [ "$(head -n 1 "$lr" 2>/dev/null)" = '/var/log/matinee.log /var/log/matinee-mcp.log {' ] && rm -f "$lr"
  remove_health_check matinee
  remove_health_check matinee-mcp
  deluser "$SVC_USER" >/dev/null 2>&1 || true
  deluser "$MCP_USER" >/dev/null 2>&1 || true
  delgroup "$SVC_USER" >/dev/null 2>&1 || true
  delgroup "$MCP_USER" >/dev/null 2>&1 || true
  echo "Removed app, service, users, log rotation, health checks and the backup, refresh and restore-test jobs. Data kept in $DATA_DIR, config in $CONF, backups in /var/backups/matinee."
  exit 0
fi

# an existing install keeps its port
[ -f "$CONF" ] && PORT=$(sed -n 's/^PORT=//p' "$CONF" | head -n1) && PORT=${PORT:-8771}

echo "==> Installing packages"
apk add --no-cache python3 py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite logrotate >/dev/null

# ---------------------------------------------------------------- safety net
# Nothing below changes the running install until GUARD=1. From then on any failure (set -e, a failed health check,
# Ctrl-C) runs rollback: put the previous app directory back if the swap happened, and start the services again.
GUARD=0 SWAP=0 HAD_APP=0 WAS_UP_APP=0 WAS_UP_MCP=0 UPGRADE=0 TMP=""
[ -d "$APP_DIR" ] && HAD_APP=1
# shellcheck disable=SC2329  # runs from the EXIT trap
rollback() {
  echo "==> The upgrade failed: putting the previous version back and starting the services" >&2
  rc-service matinee-mcp stop >/dev/null 2>&1 || true
  rc-service matinee stop >/dev/null 2>&1 || true
  if [ "$SWAP" = 1 ] && [ "$HAD_APP" = 1 ] && [ -d "$APP_DIR.old" ]; then
    rm -rf "$APP_DIR.failed"
    if [ -d "$APP_DIR" ]; then mv "$APP_DIR" "$APP_DIR.failed" || true; fi
    mv "$APP_DIR.old" "$APP_DIR" || true
  fi
  if [ "$WAS_UP_APP" = 1 ]; then
    rc-service matinee start >/dev/null 2>&1 || echo "    Could not start matinee: see /var/log/matinee.log" >&2
    ok=0; n=0
    while [ "$n" -lt 15 ]; do
      wget -qO- "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && { ok=1; break; }
      n=$((n+1)); sleep 1
    done
    if [ "$ok" = 1 ]; then echo "    The previous version is running again on port $PORT." >&2
    else echo "    The previous version did not answer /healthz either: see /var/log/matinee.log" >&2; fi
  fi
  if [ "$WAS_UP_MCP" = 1 ]; then rc-service matinee-mcp start >/dev/null 2>&1 || true; fi
  if [ "$UPGRADE" = 1 ]; then
    echo "    The database was not touched by the rollback. If it looks wrong, a pre-upgrade copy was taken first: docs/restore.md, 'Restore a pre-upgrade copy'." >&2
  fi
}
# shellcheck disable=SC2329  # the EXIT trap
on_exit() {
  rc=$?
  set +e
  trap - EXIT INT TERM HUP
  if [ -n "$TMP" ]; then rm -rf "$TMP"; fi
  if [ "$rc" != 0 ]; then
    rm -rf "$APP_DIR.new"
    if [ "$GUARD" = 1 ]; then rollback; fi
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 1' INT TERM HUP

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
mkdir -p "$DATA_DIR/images" /var/backups/matinee

# The slow work happens now, while the installed version is still serving: unpack the new app beside the old one
# and refresh the MCP venv from the new requirements. Downtime is then the swap and the restart only.
echo "==> Unpacking app"
TMP=$(mktemp -d /tmp/matinee-tmp.XXXXXX 2>/dev/null || mktemp -d -t matinee-tmp.XXXXXX 2>/dev/null || mktemp -d)
sed -n '/^__PAYLOAD_BELOW__$/,$p' "$0" | tail -n +2 | base64 -d | ( cd "$TMP" && tar -xzf - )
rm -rf "$APP_DIR.new" && mv "$TMP/matinee" "$APP_DIR.new"
rm -rf "$TMP"; TMP=""
commit_label() {  # commit_label DIR -> the commit DIR/COMMIT names (line 1, "-dirty" when line 4 says so), or unknown
  _c=$(sed -n 1p "$1/COMMIT" 2>/dev/null || true)
  if [ "$(sed -n 4p "$1/COMMIT" 2>/dev/null || true)" = dirty ]; then _c="$_c-dirty"; fi
  echo "${_c:-unknown}"
}
if [ "$HAD_APP" = 1 ]; then echo "    upgrading $(commit_label "$APP_DIR") → $(commit_label "$APP_DIR.new")"; fi
chown -R root:root "$APP_DIR.new"
chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# The MCP stack is not packaged for Alpine (no py3-mcp), so this one service gets a pip venv.
# A pip or network failure only skips matinee-mcp; the main app still installs and starts. An existing venv that
# cannot be refreshed (PyPI unreachable) is kept and still used. The venv is refreshed in place: pip does not
# disturb the running matinee-mcp, and this step is not undone by a rollback.
echo "==> Installing MCP HTTP service (pip venv)"
MCP_OK=0 MCP_REFRESH_FAILED=0 MCP_HAD_VENV=0
[ -x "$MCP_VENV/bin/pip" ] && MCP_HAD_VENV=1
# shellcheck disable=SC2015  # deliberate: any failure in the chain (including MCP_OK=1) falls through to the warning branch
apk add --no-cache py3-pip >/dev/null 2>&1 && { [ -x "$MCP_VENV/bin/python" ] || python3 -m venv "$MCP_VENV"; } && "$MCP_VENV/bin/pip" install --no-cache-dir --upgrade -q -r "$APP_DIR.new/requirements-mcp.txt" && MCP_OK=1 || MCP_REFRESH_FAILED=1
if [ "$MCP_REFRESH_FAILED" = 1 ]; then
  if [ "$MCP_HAD_VENV" = 1 ]; then
    MCP_OK=1
    echo "    WARNING: could not refresh the MCP venv (needs PyPI access); keeping the installed one. Re-run this installer to retry."
  else
    echo "    WARNING: could not set up the MCP venv (needs PyPI access); matinee-mcp will not start. Re-run this installer to retry."
  fi
fi

# An upgrade is any run that finds a database. Take the labelled copy now, before anything is stopped or changed,
# with the NEW matinee-backup from the payload: the script already installed on the host may be an older one that
# ignores the label and overwrites the day's scheduled copy. Any failure aborts, with the old version untouched.
DB_DIR=$(sed -n 's/^DATA_DIR=//p' "$CONF" 2>/dev/null | tail -n 1 | tr -d "\"'")
DB_DIR=${DB_DIR:-$DATA_DIR}
if [ -f "$DB_DIR/matinee.db" ]; then
  UPGRADE=1
  echo "==> Taking the pre-upgrade backup"
  if ! sh "$APP_DIR.new/ops/matinee-backup" pre-upgrade; then
    echo "The pre-upgrade backup failed, so nothing was changed and the running version was left alone."
    echo "See /var/log/ops.log, fix the cause (disk space? a damaged $DB_DIR/matinee.db?), then re-run this installer."
    exit 1
  fi
fi
latest_pre_upgrade() {  # path of the newest pre-upgrade copy, or nothing
  _ld=$(sed -n 's/^LOCAL_DIR=//p' "$OPS_CONF" 2>/dev/null | tail -n 1 | tr -d "\"'")
  # shellcheck disable=SC2012  # newest by mtime; the names are ours (matinee-matinee-pre-upgrade-<HHMMSS>.db.gz)
  ls -t "${_ld:-/var/backups/matinee}"/*/matinee-matinee-pre-upgrade-*.db.gz 2>/dev/null | head -n 1
}

# ---------------------------------------------------------------- the swap (downtime starts here)
# What was running is what a rollback starts again.
[ -x /etc/init.d/matinee-mcp ] && rc-service matinee-mcp status >/dev/null 2>&1 && WAS_UP_MCP=1 || true
[ -x /etc/init.d/matinee ] && rc-service matinee status >/dev/null 2>&1 && WAS_UP_APP=1 || true
GUARD=1
[ -x /etc/init.d/matinee-mcp ] && rc-service matinee-mcp stop >/dev/null 2>&1 || true
[ -x /etc/init.d/matinee ] && rc-service matinee stop >/dev/null 2>&1 || true
IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)
IP=${IP:-127.0.0.1}
if netstat -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$PORT\$"; then
  echo "Port $PORT is already in use by something else. Re-run with MATINEE_PORT=<free port>."; exit 1
fi

echo "==> Swapping in the new version"
SWAP=1
rm -rf "$APP_DIR.failed" "$APP_DIR.old"   # only now: until here the previous upgrade's copy was still there
if [ "$HAD_APP" = 1 ]; then mv "$APP_DIR" "$APP_DIR.old"; fi
mv "$APP_DIR.new" "$APP_DIR"

# ---------------------------------------------------------------- config
add_setting() {  # add_setting FILE KEY VALUE COMMENT — only if KEY isn't there yet
  grep -q "^$2=" "$1" 2>/dev/null && return 0
  printf '\n# %s\n%s=%s\n' "$4" "$2" "$3" >> "$1"
  ADDED="${ADDED:-} $2"
}
gen_key() {  # 43 URL-safe characters (256 bits) for a new API key
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
}
conf_val() { sed -n "s/^$1=//p" "$CONF" | head -n1 | tr -d "\"'"; }
mcp_level() {  # mcp_level READONLY WRITES -> none, log or all; mirrors mcp_http.write_level()
  _ro=$(printf '%s' "$1" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')
  _w=$(printf '%s' "$2" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')
  case "$_ro" in
    ""|0|false|no|off) ;;
    *) echo none; return 0 ;;
  esac
  case "$_w" in
    none|log|all) echo "$_w" ;;
    "") case "$_ro" in 0|false|no|off) echo all ;; *) echo none ;; esac ;;
    *) echo none ;;
  esac
}

if [ ! -f "$CONF" ]; then
  echo "==> Writing $CONF"
  # It will hold API keys, so root only from its first byte (the subshell keeps the umask to this one file).
  (
    umask 077
    cat > "$CONF" <<EOF
# Matinee (movie collection) configuration — after editing: rc-service matinee restart
PORT=$PORT
DATA_DIR=$DATA_DIR
EOF
  )
fi
# an existing file from an older run may be wider: close it before the generated read key is appended
chmod 600 "$CONF"; chown root:root "$CONF"
add_setting "$CONF" PORT "$PORT" "Port gunicorn listens on (each app on the host needs its own)"
add_setting "$CONF" DATA_DIR "$DATA_DIR" "Where matinee.db and images/ live"
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
add_setting "$CONF" HA_WEBHOOK_URL "" "Optional Home Assistant webhook URL, a secret: a watch logged for today is POSTed to it (docs/home-assistant.md); empty = off"
add_setting "$CONF" HA_PLAY_WEBHOOK_URL "" "Optional Home Assistant webhook URL, a secret: Play starts a film on the Family Room TV through it (docs/home-assistant.md); empty = off"
add_setting "$CONF" WISHLIST_URL "http://127.0.0.1:8766" "Family Wish Lists app on this host: 'Add to a wish list' pushes a wanted movie there; the button shows only while it answers"
add_setting "$CONF" WISHLIST_MOVIE_URL '"https://www.amazon.com/s?k={q}"' "Shopping search link on a pushed wish; {q} becomes the title, year and wanted format"
# Remote MCP (matinee-mcp). MCP_HTTP_BIND must be this host's LAN address because cloudflared runs in Docker.
add_setting "$CONF" MATINEE_MCP_READONLY 1 "1 = the Claude app can only read (MATINEE_MCP_WRITES is ignored); 0 = MATINEE_MCP_WRITES decides what it may write"
# A host that already runs with writes on (READONLY=0) keeps every tool; every other host starts at none.
MCP_WRITES_DEFAULT=$(mcp_level "$(conf_val MATINEE_MCP_READONLY)" "")
add_setting "$CONF" MATINEE_MCP_WRITES "$MCP_WRITES_DEFAULT" "none, log (adds rate_movie, log_watch and set_list_membership) or all (adds add_movie, want_movie and play_on_tv); only used while MATINEE_MCP_READONLY=0"
add_setting "$CONF" MCP_HTTP_BIND "$IP" "Address matinee-mcp listens on (cloudflared reaches it here)"
add_setting "$CONF" MCP_HTTP_PORT 8719 "Port matinee-mcp listens on"
add_setting "$CONF" MCP_HTTP_PATH /matinee/mcp "URL path of the MCP endpoint (the tunnel route is ^/matinee/)"
add_setting "$CONF" MCP_PUBLIC_HOSTS "" "Public hostname(s) of the tunnel, e.g. mcp.example.com (required)"
add_setting "$CONF" CF_ACCESS_AUD "" "AUD tag of the Access application that fronts mcp.<your-domain> (required)"
add_setting "$CONF" MCP_ALLOWED_EMAILS "" "Only these Access emails may use the MCP tools (comma separated); empty = anyone Access lets in"
add_setting "$CONF" MCP_ALLOWED_SERVICE_TOKENS "" "Access service-token client IDs (comma separated) that may use the read tools, e.g. a monitor; empty = none"
add_setting "$CONF" MATINEE_URL "" "Where matinee-mcp reaches the Matinee app; empty = http://127.0.0.1 on PORT"
add_setting "$CONF" MATINEE_DB_PATH "" "Path of matinee.db for matinee-mcp's direct read mode; empty = REST only (the remote service always uses REST)"
add_setting "$CONF" MCP_AUTOMATION_TIERS "read,power-off" "Tiers an Access service token may use at the MCP endpoint, comma separated; never costly"
add_setting "$CONF" MCP_COMMIT_FILE "" "File naming the running commit (line 1 commit, line 2 repo); empty = COMMIT beside the matinee-mcp venv"
# Root only (see above): OpenRC and the cron jobs run as root; services get what they need from the environment.

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
export PORT DATA_DIR CF_TEAM_DOMAIN CF_AUD ADMIN_EMAILS LAN_EDIT LAN_HOSTS PUBLIC_VIEW TMDB_READ_TOKEN MATINEE_API_KEY MATINEE_READ_KEY OMDB_API_KEY ANTHROPIC_SEARCH_KEY ANTHROPIC_SEARCH_MODEL ASK_DAILY_LIMIT HA_WEBHOOK_URL HA_PLAY_WEBHOOK_URL WISHLIST_URL WISHLIST_MOVIE_URL
supervisor="supervise-daemon"
respawn_delay=5
respawn_max=10
respawn_period=600
command="/usr/bin/gunicorn"
command_args="--preload --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:${PORT} --chdir /opt/matinee --access-logfile /var/log/matinee.log --error-logfile /var/log/matinee.log app:app"
command_user="matinee"
depend() { need net; after firewall; }
start_pre() { checkpath -f -m 0640 -o matinee:matinee /var/log/matinee.log; }
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
mcp_level() {  # mcp_level READONLY WRITES -> none, log or all; mirrors mcp_http.write_level()
  _ro=$(printf '%s' "$1" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')
  _w=$(printf '%s' "$2" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')
  case "$_ro" in
    ""|0|false|no|off) ;;
    *) echo none; return 0 ;;
  esac
  case "$_w" in
    none|log|all) echo "$_w" ;;
    "") case "$_ro" in 0|false|no|off) echo all ;; *) echo none ;; esac ;;
    *) echo none ;;
  esac
}
# Only what this service needs: TMDB_READ_TOKEN and OMDB_API_KEY deliberately stay out of its environment,
# and it gets one Matinee key: the GET-only MATINEE_READ_KEY at write level none (mirrors
# mcp_http.write_level()), MATINEE_API_KEY at log and all.
case "$(mcp_level "${MATINEE_MCP_READONLY:-}" "${MATINEE_MCP_WRITES:-}")" in
  none) unset MATINEE_API_KEY; export MATINEE_READ_KEY ;;
  *) unset MATINEE_READ_KEY; export MATINEE_API_KEY ;;
esac
export MATINEE_URL MATINEE_MCP_READONLY MATINEE_MCP_WRITES MCP_HTTP_BIND MCP_HTTP_PORT MCP_HTTP_PATH MCP_PUBLIC_HOSTS CF_ACCESS_TEAM_DOMAIN CF_ACCESS_AUD MCP_ALLOWED_EMAILS MCP_ALLOWED_SERVICE_TOKENS CF_TEAM_DOMAIN MATINEE_DB_PATH MCP_AUTOMATION_TIERS MCP_COMMIT_FILE
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

# ---------------------------------------------------------------- log rotation
# logrotate is installed with the other packages, so rotation is always set up. It steps aside only when an existing
# logrotate config (a hand-made rule, or the file an earlier run wrote) already names these logs.
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

echo "==> Starting service"
# 640 and owned by the service user from the first byte (the init script keeps it so at every start)
touch /var/log/matinee.log
chown "$SVC_USER:$SVC_USER" /var/log/matinee.log
chmod 640 /var/log/matinee.log
rc-update add matinee default >/dev/null 2>&1 || true
rc-service matinee start
i=0
until wget -qO- "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; do
  i=$((i+1)); [ $i -gt 25 ] && { echo "Service didn't come up (the previous version is put back); see /var/log/matinee.log"; tail -n 20 /var/log/matinee.log; exit 1; }
  sleep 1
done
# The new version answers: the upgrade is done and a later failure no longer rolls it back.
GUARD=0
# matinee-mcp reads the same COMMIT file from beside its venv (MCP_COMMIT_FILE empty). Copied only now, once the new
# version answers, so a rollback leaves the old copy in place; a payload without one clears a stale copy.
mkdir -p /opt/matinee-mcp
if [ -f "$APP_DIR/COMMIT" ]; then cp "$APP_DIR/COMMIT" /opt/matinee-mcp/COMMIT; else rm -f /opt/matinee-mcp/COMMIT; fi

# ---------------------------------------------------------------- backups & maintenance
echo "==> Installing nightly backup and refresh jobs and the weekly restore test"
mkdir -p /etc/periodic/daily /etc/periodic/weekly
install -m 755 "$APP_DIR/ops/matinee-backup" /etc/periodic/daily/matinee-backup
install -m 755 "$APP_DIR/ops/matinee-refresh" /etc/periodic/daily/matinee-refresh
install -m 755 "$APP_DIR/ops/matinee-restore-test" /etc/periodic/weekly/matinee-restore-test
rc-update add crond default >/dev/null 2>&1 || true
rc-service crond start >/dev/null 2>&1 || true

echo "==> Checking log rotation"
install_logrotate

# The nightly job's plain copy is only for a fresh install: after an upgrade it would overwrite tonight's copy with
# the migrated database, and the pre-upgrade copy above is the one to go back to.
if [ "$UPGRADE" = 1 ]; then
  echo "==> Pre-upgrade backup taken before the swap: $(latest_pre_upgrade)"
else
  echo "==> Taking a first backup"
  /etc/periodic/daily/matinee-backup || echo "    (backup reported a problem; see /var/log/ops.log)"
fi

mcp_key_name() {  # the key matinee-mcp needs at its write level (same rule as its init script)
  case "$(mcp_level "$(conf_val MATINEE_MCP_READONLY)" "$(conf_val MATINEE_MCP_WRITES)")" in
    none) echo MATINEE_READ_KEY ;;
    *) echo MATINEE_API_KEY ;;
  esac
}
MCP_LEVEL=$(mcp_level "$(conf_val MATINEE_MCP_READONLY)" "$(conf_val MATINEE_MCP_WRITES)")
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
  up) echo "Matinee MCP is running (write level: $MCP_LEVEL): http://$(conf_val MCP_HTTP_BIND):$(conf_val MCP_HTTP_PORT)/healthz" ;;
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
if [ "$HAD_APP" = 1 ] && [ -d "$APP_DIR.old" ]; then
  echo
  echo "The previous version is kept in $APP_DIR.old until the next upgrade. To go back to it:"
  echo "  rc-service matinee-mcp stop; rc-service matinee stop"
  echo "  mv $APP_DIR $APP_DIR.swap && mv $APP_DIR.old $APP_DIR && mv $APP_DIR.swap $APP_DIR.old"
  if [ "$UPGRADE" = 1 ] && [ -n "$(latest_pre_upgrade)" ]; then
    echo "  # only if the data looks wrong too, put the pre-upgrade copy back first (docs/restore.md, 'Restore a pre-upgrade copy'):"
    echo "  gunzip -c $(latest_pre_upgrade) > /tmp/restore.db && sqlite3 /tmp/restore.db 'PRAGMA integrity_check;'   # must print: ok"
    echo "  mkdir -p $DB_DIR/restore-aside && mv $DB_DIR/matinee.db* $DB_DIR/restore-aside/"
    echo "  cp /tmp/restore.db $DB_DIR/matinee.db && chown $SVC_USER:$SVC_USER $DB_DIR/matinee.db && rm /tmp/restore.db"
  fi
  echo "  rc-service matinee restart; rc-service matinee-mcp restart"
fi
echo "Config: $CONF   Data: $DATA_DIR   Logs: /var/log/matinee.log, /var/log/matinee-mcp.log, /var/log/ops.log"
exit 0
