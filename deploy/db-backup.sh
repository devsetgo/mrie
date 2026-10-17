#!/usr/bin/env sh
# Consistent snapshot of the live SQLite database into ./backups/.
# Uses SQLite's online backup API inside the running container - never `cp`
# the file off the volume: with WAL, the -wal side file holds committed
# writes the main file doesn't have yet. Run from anywhere; it works in the
# folder holding docker-compose.yml.
set -eu
cd "$(dirname "$0")"

stamp=$(date +'%y-%m-%d-%H%M')
mkdir -p backups

# Path built from the container's own DB_DIRECTORY/DB_NAME (same defaults as
# src/settings.py), so a non-default DB_NAME in .env is backed up too.
# A missing file is an error, not a fresh empty database (mode=rw).
docker compose exec -T mikeryan python -c "
import os, sqlite3
path = os.path.join(os.environ.get('DB_DIRECTORY', 'sqlite_db'), os.environ.get('DB_NAME', 'mrie') + '.db')
src = sqlite3.connect(f'file:{path}?mode=rw', uri=True)
dst = sqlite3.connect('/tmp/mrie-backup.db')
src.backup(dst)
dst.close()
src.close()
print(f'backed up {path}')
"
docker compose cp mikeryan:/tmp/mrie-backup.db "backups/mrie-${stamp}.db"
docker compose exec -T mikeryan rm -f /tmp/mrie-backup.db

echo "Wrote backups/mrie-${stamp}.db"
