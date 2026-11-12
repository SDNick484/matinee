"""The local image cache: TMDB poster and backdrop file names and URLs, and custom covers (sniffed by magic
bytes, stored flat in IMAGE_DIR). The downloads (save_image, fetch_images) stay in app.py, where they log through
the Flask app's logger. Split out of app.py."""
import urllib.parse

from settings import IMAGE_DIR


# ======================================================================= image cache
IMAGES = (("poster_path", "w342", "p342"), ("poster_path", "w780", "p780"), ("backdrop_path", "w780", "b780"))


def image_name(tmdb_id, tag):
    return f"{tmdb_id}-{tag}.jpg"


def delete_images(tmdb_id):
    for _col, _size, tag in IMAGES:
        (IMAGE_DIR / image_name(tmdb_id, tag)).unlink(missing_ok=True)


def image_url(name, fetched_at):
    return f"/images/{name}?v={urllib.parse.quote(fetched_at or '')}" if name else None


# Custom covers: stored flat in IMAGE_DIR as <tmdb_id>-custom-<sha1[:10]>.<ext>, so a new upload
# gets a new URL and the TMDB files beside it are untouched. Sniffed by magic bytes, not extension.
MAX_POSTER_BYTES = 8 * 1024 * 1024
POSTER_TYPES = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"))


def sniff_image(data):
    for magic, ext in POSTER_TYPES:
        if data.startswith(magic):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def poster_file_of(t):
    """The poster to show: an uploaded cover wins over the cached TMDB one."""
    return t["custom_poster_file"] or t["poster_file"]


def delete_custom_poster(name):
    if name and "-custom-" in name and "/" not in name:
        (IMAGE_DIR / name).unlink(missing_ok=True)
