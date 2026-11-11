# Restoring Matinee from a backup

Matinee keeps everything it cannot re-fetch in two places under `/var/lib/matinee` (`$DATA_DIR`):

| What | Where | Backed up by `ops/matinee-backup` |
|------|-------|-----------------------------------|
| The collection: titles, copies, shelves, watches, ratings, Ask usage | `matinee.db` (SQLite, WAL mode) | nightly, `/var/backups/matinee/<date>/matinee-matinee.db.gz`, kept 14 days (`KEEP_DAYS`) |
| Covers you uploaded | `images/<tmdb_id>-custom-<hex>.<ext>` | nightly, `/var/backups/matinee/<date>/matinee-custom-covers.tar.gz` |
| TMDB posters and backdrops | `images/<tmdb_id>-p342.jpg` etc. | copied to `$BACKUP_TARGET/files/matinee/images/` only if `BACKUP_TARGET` is set; otherwise re-fetched from TMDB |

If `BACKUP_TARGET` is set in `/etc/conf.d/ops`, the same database file is also at
`$BACKUP_TARGET/db/<date>/matinee-matinee.db.gz`. Use that copy if the host's own disk is the thing you lost.

Once a week (crond's `/etc/periodic/weekly` slot) `ops/matinee-restore-test` unpacks the newest backup into a temp file and
checks it, so you find out a backup is bad before you need it. Its result is the last `matinee-restore-test:` line in
`/var/log/ops.log`; a failure also goes to `ops-notify`.

## Restore the database

Run as root on the Matinee host. The example uses the 2026-11-10 backup; pick yours with `ls /var/backups/matinee`.

```sh
# 1. Choose the backup and prove it is good before touching anything
BK=/var/backups/matinee/2026-11-10
gunzip -c $BK/matinee-matinee.db.gz > /tmp/restore.db
sqlite3 /tmp/restore.db 'PRAGMA integrity_check;'                       # must print: ok
sqlite3 /tmp/restore.db 'SELECT COUNT(*) FROM titles; SELECT COUNT(*) FROM copies;'

# 2. Stop the app (matinee-mcp needs it, so OpenRC stops that too)
rc-service matinee stop

# 3. Keep what is there, including the WAL files: a leftover matinee.db-wal would be replayed into the restored database
mkdir -p /var/lib/matinee/restore-aside
mv /var/lib/matinee/matinee.db* /var/lib/matinee/restore-aside/ 2>/dev/null

# 4. Put the backup in place
cp /tmp/restore.db /var/lib/matinee/matinee.db
chown matinee:matinee /var/lib/matinee/matinee.db
rm /tmp/restore.db

# 5. Start again and check
rc-service matinee start
wget -qO- http://127.0.0.1:8771/healthz
rc-service matinee-mcp start          # only if it was running before
```

If the backup is from an older release, the app upgrades the schema itself on start (`migrate()`); there is no manual
step. Once you are happy, `rm -r /var/lib/matinee/restore-aside`.

Everything logged after the backup is gone: watches, ratings and titles added since that night. The 14-day window
means you can step back to an earlier date if the newest backup already contains the damage.

## Restore uploaded covers

```sh
BK=/var/backups/matinee/2026-11-10
tar -xzf $BK/matinee-custom-covers.tar.gz -C /var/lib/matinee/images
chown matinee:matinee /var/lib/matinee/images/*-custom-*
```

A title whose database row points at a custom cover that is not in `images/` has no cover to show, so restore the
covers from the same date as the database or later.

## Restore TMDB posters and backdrops

Either of these; neither needs the app stopped.

```sh
# a. from BACKUP_TARGET, if you use it
cp -a "$BACKUP_TARGET"/files/matinee/images/. /var/lib/matinee/images/
chown -R matinee:matinee /var/lib/matinee/images

# b. re-fetch everything from TMDB: one request per title, at least 0.3 s apart, so a big collection takes a while
set -a; . /etc/conf.d/matinee; set +a
python3 /opt/matinee/ops/refresh_titles.py --stale-days 0 --limit 5000 --delay 0.3
chown -R matinee:matinee /var/lib/matinee/images
```

Option b also refreshes certifications and franchise data. It keeps uploaded covers and certification overrides.

## Rebuild on a new host

1. Install Matinee with `sh setup-matinee.sh` and let it start once, so the user, folders and `/etc/conf.d/matinee` exist.
2. Copy `matinee-matinee.db.gz` (and the covers tarball) to the new host and follow the steps above.
3. Put your keys back in `/etc/conf.d/matinee` (`TMDB_READ_TOKEN`, `OMDB_API_KEY`, `CF_AUD`, ...). They are not in the
   backup, on purpose. Then `rc-service matinee restart`.

## Check the restore test by hand

```sh
/etc/periodic/weekly/matinee-restore-test; tail -n 3 /var/log/ops.log
```

A healthy line looks like `matinee-restore-test: ok 2026-11-10: integrity ok, 1480 titles, 1733 copies, 3 custom covers`.
