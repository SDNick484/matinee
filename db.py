"""The SQLite schema, migrate() from every earlier version, connections (one per request, or one standalone
connection outside a request) and init_db(). Also the copy-format and storefront catalogs, which the app checks
(not the schema), so a new format or service needs no table rebuild. Split out of app.py."""
import sqlite3

from flask import g, has_app_context

from settings import DATA_DIR, IMAGE_DIR, DB_PATH


# =============================================================================== db
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS titles (
  id INTEGER PRIMARY KEY,
  tmdb_id INTEGER NOT NULL UNIQUE,
  imdb_id TEXT,
  title TEXT NOT NULL,
  sort_title TEXT NOT NULL,
  search_text TEXT NOT NULL,
  year INTEGER, release_date TEXT, runtime INTEGER, overview TEXT,
  certification TEXT,
  collection_tmdb_id INTEGER, collection_name TEXT,
  tmdb_vote REAL, tmdb_votes INTEGER,
  poster_path TEXT, backdrop_path TEXT,
  poster_file TEXT, backdrop_file TEXT,
  custom_poster_file TEXT,
  certification_override TEXT,
  fetched_at TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS titles_collection ON titles(collection_tmdb_id);
CREATE TABLE IF NOT EXISTS genres (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS title_genres (
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genres(id),
  PRIMARY KEY (title_id, genre_id)
);
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS locations_parent ON locations(parent_id);

CREATE TABLE IF NOT EXISTS copies (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('physical','digital')),
  format TEXT NOT NULL,
  service TEXT,
  location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
  added_by TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  -- v10 copy details, last so a migrated table has the same columns in the same order (COPY_DETAIL_COLUMNS)
  edition TEXT CHECK (edition IS NULL OR length(edition) <= 60),
  code_status TEXT NOT NULL DEFAULT 'none' CHECK (code_status IN ('none', 'unredeemed', 'redeemed', 'expired')),
  purchased_on TEXT,
  price_cents INTEGER CHECK (price_cents IS NULL OR price_cents >= 0)
);
CREATE INDEX IF NOT EXISTS copies_title ON copies(title_id);
CREATE TABLE IF NOT EXISTS upc_cache (
  upc TEXT PRIMARY KEY,
  raw_title TEXT,
  brand TEXT,
  data TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS upc_cache_created ON upc_cache(created_at);

CREATE TABLE IF NOT EXISTS user_ratings (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
  rating REAL NOT NULL CHECK (rating >= 0.0 AND rating <= 10.0),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS user_ratings_title ON user_ratings(title_id);
CREATE INDEX IF NOT EXISTS user_ratings_score ON user_ratings(rating);

CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  watched_date TEXT,
  notes TEXT,
  date_bucket TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS watches_title_date ON watches(title_id, watched_date DESC);
CREATE INDEX IF NOT EXISTS watches_date ON watches(watched_date DESC);

CREATE TABLE IF NOT EXISTS omdb_ratings (
  title_id INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
  imdb_id TEXT NOT NULL,
  imdb_rating REAL,
  imdb_votes INTEGER,
  rotten_tomatoes_score INTEGER,
  metacritic_score INTEGER,
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS omdb_ratings_fetched ON omdb_ratings(fetched_at);
"""
SCHEMA_VERSION = "11"

# v10 copy details. The limits mirror the CHECKs on `copies`; the app validates first and answers 400 with a sentence.
CODE_STATUSES = ("none", "unredeemed", "redeemed", "expired")   # a physical copy's digital code; digital copies stay "none"
EDITION_MAX = 60                                                # characters
NOTE_MAX = 140                                                  # v11: characters in a want's note; mirrors the CHECK on `wants`
PRICE_MAX_CENTS = 10_000_000                                    # $100,000.00; stops a huge integer reaching SQLite
COPY_DETAIL_COLUMNS = (
    ("edition", "TEXT CHECK (edition IS NULL OR length(edition) <= 60)"),
    ("code_status", "TEXT NOT NULL DEFAULT 'none' CHECK (code_status IN ('none', 'unredeemed', 'redeemed', 'expired'))"),
    ("purchased_on", "TEXT"),
    ("price_cents", "INTEGER CHECK (price_cents IS NULL OR price_cents >= 0)"),
)

# Checked in the app (not the schema) so a new format or service needs no table rebuild.
FORMATS = {"physical": ["4K UHD", "Blu-ray", "3D Blu-ray", "DVD", "VHS"],
           "digital": ["Digital 4K", "Digital HD", "Digital SD"]}
SERVICES = ["Movies Anywhere", "Apple TV", "Fandango at Home", "Amazon", "Google Play", "Plex/Jellyfin", "Other"]
SERVICE_ALIASES = {
    "vudu": "Fandango at Home",
    "fandango": "Fandango at Home",
    "itunes": "Apple TV",
    "prime": "Amazon",
    "prime video": "Amazon",
}


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


_standalone_db = None


def db():
    if has_app_context():
        if "db" not in g:
            g.db = connect()
        return g.db
    global _standalone_db
    if _standalone_db is None:
        _standalone_db = connect()
    return _standalone_db


def migrate(con):
    """Add tables and columns that newer versions expect to an older database."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS locations (
      id INTEGER PRIMARY KEY,
      parent_id INTEGER REFERENCES locations(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      sort_order INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS locations_parent ON locations(parent_id);

    CREATE TABLE IF NOT EXISTS user_ratings (
      id INTEGER PRIMARY KEY,
      title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
      rating REAL NOT NULL CHECK (rating >= 0.0 AND rating <= 10.0),
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS user_ratings_title ON user_ratings(title_id);
    CREATE INDEX IF NOT EXISTS user_ratings_score ON user_ratings(rating);

    CREATE TABLE IF NOT EXISTS watches (
      id INTEGER PRIMARY KEY,
      title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
      watched_date TEXT,
      notes TEXT,
      date_bucket TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS watches_title_date ON watches(title_id, watched_date DESC);
    CREATE INDEX IF NOT EXISTS watches_date ON watches(watched_date DESC);

    CREATE TABLE IF NOT EXISTS omdb_ratings (
      title_id INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
      imdb_id TEXT NOT NULL,
      imdb_rating REAL,
      imdb_votes INTEGER,
      rotten_tomatoes_score INTEGER,
      metacritic_score INTEGER,
      fetched_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS omdb_ratings_fetched ON omdb_ratings(fetched_at);
    """)

    # Check copies table for location_id
    has_copies = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='copies'").fetchone()
    if has_copies:
        copy_cols = [r[1] for r in con.execute("PRAGMA table_info(copies)").fetchall()]
        if "location_id" not in copy_cols:
            con.execute("PRAGMA foreign_keys=OFF")
            con.executescript("""
            CREATE TABLE copies_new (
              id INTEGER PRIMARY KEY,
              title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
              kind TEXT NOT NULL CHECK (kind IN ('physical','digital')),
              format TEXT NOT NULL,
              service TEXT,
              location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
              added_by TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO copies_new (id, title_id, kind, format, service, added_by, created_at, updated_at)
              SELECT id, title_id, kind, format, service, added_by, created_at, updated_at FROM copies;
            DROP TABLE copies;
            ALTER TABLE copies_new RENAME TO copies;
            CREATE INDEX IF NOT EXISTS copies_title ON copies(title_id);
            CREATE INDEX IF NOT EXISTS copies_location ON copies(location_id);
            """)
            con.execute("PRAGMA foreign_keys=ON")
        else:
            con.execute("CREATE INDEX IF NOT EXISTS copies_location ON copies(location_id)")

    has_watches = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watches'").fetchone()
    if has_watches:
        cols = [r[1] for r in con.execute("PRAGMA table_info(watches)").fetchall()]
        if "notes" not in cols:
            con.execute("ALTER TABLE watches ADD COLUMN notes TEXT")
        if "date_bucket" not in cols:
            con.execute("ALTER TABLE watches ADD COLUMN date_bucket TEXT")

        # If watched_date is NOT NULL, recreate table to allow NULL
        watches_info = con.execute("PRAGMA table_info(watches)").fetchall()
        needs_recreation = False
        for col in watches_info:
            if col[1] == "watched_date" and col[3] == 1:
                needs_recreation = True
                break
        if needs_recreation:
            con.execute("PRAGMA foreign_keys=OFF")
            con.executescript("""
            CREATE TABLE watches_new (
              id INTEGER PRIMARY KEY,
              title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
              watched_date TEXT,
              notes TEXT,
              date_bucket TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO watches_new (id, title_id, watched_date, notes, date_bucket, created_at)
              SELECT id, title_id, watched_date, notes, date_bucket, created_at FROM watches;
            DROP TABLE watches;
            ALTER TABLE watches_new RENAME TO watches;
            CREATE INDEX IF NOT EXISTS watches_title_date ON watches(title_id, watched_date DESC);
            CREATE INDEX IF NOT EXISTS watches_date ON watches(watched_date DESC);
            """)
            con.execute("PRAGMA foreign_keys=ON")

    # v6: an editor-uploaded cover that overrides the TMDB poster
    title_cols = [r[1] for r in con.execute("PRAGMA table_info(titles)").fetchall()]
    if "custom_poster_file" not in title_cols:
        con.execute("ALTER TABLE titles ADD COLUMN custom_poster_file TEXT")

    # v7: editor override for the US certification (survives TMDB refresh; not in TITLE_COLS)
    if "certification_override" not in title_cols:
        con.execute("ALTER TABLE titles ADD COLUMN certification_override TEXT")

    # v8: Ask usage log (no question text)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS ask_log (
      id INTEGER PRIMARY KEY,
      ts REAL NOT NULL,                 -- unix time
      editor TEXT,                      -- g.editor: Access email or 'lan'
      rounds INTEGER NOT NULL,
      input_tokens INTEGER NOT NULL DEFAULT 0,
      output_tokens INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL              -- ok | capped | api_error | timeout | error
    );
    CREATE INDEX IF NOT EXISTS ask_log_ts ON ask_log(ts);
    """)

    # v9: people and watch attendance (no rows written)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS people (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL COLLATE NOCASE UNIQUE,
      kind TEXT NOT NULL CHECK (kind IN ('adult', 'kid')),
      color TEXT NOT NULL,                       -- palette key: green | blue | red | pink | purple
      sort_order INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS watch_people (
      watch_id  INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
      person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
      PRIMARY KEY (watch_id, person_id)
    ) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS watch_people_person ON watch_people(person_id);
    """)

    # v10: copy details. After the copies rebuild above, so every earlier version ends with the same table.
    copy_cols = {r[1] for r in con.execute("PRAGMA table_info(copies)")}
    for col, ddl in COPY_DETAIL_COLUMNS:
        if copy_cols and col not in copy_cols:
            con.execute(f"ALTER TABLE copies ADD COLUMN {col} {ddl}")

    # v11: the wish list. A want is one row per title, independent of copies (a title can be wanted on 4K UHD while
    # owned on Digital HD). wanted_format is NULL ("any copy") or one of FORMATS, checked in the app like copies.format.
    # wish_items remembers what was pushed to the family Wish Lists app and survives the want being cleared.
    con.executescript("""
    CREATE TABLE IF NOT EXISTS wants (
      title_id      INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
      wanted_format TEXT,
      note          TEXT CHECK (note IS NULL OR length(note) <= 140),
      created_at    TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS wish_items (
      id            INTEGER PRIMARY KEY,
      title_id      INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
      person_id     INTEGER,
      person_name   TEXT,
      item_id       INTEGER,
      wanted_format TEXT,
      created_at    TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS wish_items_title ON wish_items(title_id);
    """)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    con.executescript(SCHEMA)
    migrate(con)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
    con.commit()
    con.close()
    for p in IMAGE_DIR.glob("*.part"):  # downloads interrupted by a restart
        p.unlink(missing_ok=True)
