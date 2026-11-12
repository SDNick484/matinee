"""Matinee settings, read once at import from the environment; /etc/conf.d/matinee (or a local .env next to
app.py) fills in anything the environment leaves unset. Split out of app.py, which re-exports every name here:
patch app.<NAME>, which also rebinds it in this module (see _AppModule in app.py)."""
import os, re
from pathlib import Path


APP_NAME = "Matinee"
APP_DIR = Path(__file__).resolve().parent

# Auto-load config from /etc/conf.d/matinee or local .env if not already set in environment
def _load_env():
    for conf_file in [Path("/etc/conf.d/matinee"), APP_DIR / ".env"]:
        if conf_file.is_file():
            try:
                with open(conf_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        if line.startswith("export "):
                            line = line[7:].strip()
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if len(v) >= 2 and ((v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'"))):
                            v = v[1:-1]
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass

_load_env()

DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "matinee.db"
# bare host: tolerate a pasted "https://team.cloudflareaccess.com/" (same as Cookbook)
CF_TEAM_DOMAIN = re.sub(r"^https?://", "", os.environ.get("CF_TEAM_DOMAIN", "").strip().strip("'\"")).split("/")[0]
CF_AUD = os.environ.get("CF_AUD", "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
LAN_EDIT = os.environ.get("LAN_EDIT", "1") == "1"


def parse_hosts(raw):
    """'Matinee.LAN, movies.home.arpa' -> {'matinee.lan', 'movies.home.arpa'}"""
    return {h.strip().lower().rstrip(".") for h in (raw or "").split(",") if h.strip()}


# host names (besides IP literals and localhost) that a LAN_EDIT request may use; see lan_host()
LAN_HOSTS = parse_hosts(os.environ.get("LAN_HOSTS", ""))
PUBLIC_VIEW = os.environ.get("PUBLIC_VIEW", "1") == "1"
MATINEE_API_KEY = os.environ.get("MATINEE_API_KEY", "").strip()


OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "").strip()
# Home Assistant webhook, POSTed when a watch is logged for today; empty = off. A SECRET: the webhook id in the URL
# is its only credential, so it is never logged (the host only), never returned by a route, never put in a payload.
HA_WEBHOOK_URL = os.environ.get("HA_WEBHOOK_URL", "").strip()
HA_WEBHOOK_TIMEOUT = 3  # seconds per socket operation; the request runs on a daemon thread
TMDB_READ_TOKEN = os.environ.get("TMDB_READ_TOKEN", "").strip()
TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p"
UA = "matinee/1.0 (movie collection tracker; https://github.com/SDNick484/matinee)"
