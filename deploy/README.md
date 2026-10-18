# Deploying mikeryan.ie

This folder is everything the server needs. The image is pulled from
Docker Hub (`mikeryan56/mikeryanie:latest`). There's nothing to build here.

| File | Purpose |
|---|---|
| `docker-compose.yml` | The one service, Traefik labels, volumes, secrets |
| `.env.pre-prd` | Template for the app's `.env` (copy and fill in) |
| `common.env.sample` | Template for the shared `/opt/docker/shared/common.env` |
| `db-backup.sh` | Consistent snapshot of the live DB into `./backups/` |
| `ARPHOST.md` | Step-by-step first install on the arphost server |

## What persists where

| Data | Location | Survives image updates |
|---|---|---|
| Notes, links (incl. preview thumbnails), About page + its uploaded images | SQLite file on volume `mrie-db` (`/app/sqlite_db/mrie.db`) | Yes |
| Log files | Volume `mrie-logs` (`/app/logs`) | Yes |
| Site images (favicon, headshot, background), CSS, JS | Baked into the image (`static/`) | Replaced by each new image |

## Publishing an image

From the repo (not the server): `make docker-login` once, then
`make docker-push`. That builds `mikeryan56/mikeryanie` and pushes both the
version tag and `latest`, which is what the server pulls.

## First-time setup

0. **Old data:** run `docker volume ls`. The volumes here are named `mrie-db`
   and `mrie-logs`; an earlier deployment's `<folder>_db-volume` won't be
   picked up automatically. Back it up, then copy its `mrie.db` into
   `mrie-db` before the first start.

1. **Shared secrets** (one file per value, no quotes; skip any that already exist):

   ```sh
   sudo mkdir -p /opt/docker/shared/secrets && cd /opt/docker/shared/secrets
   printf '%s' 'your-phrase'     | sudo tee phrase     >/dev/null
   printf '%s' 'your-salt'       | sudo tee salt       >/dev/null
   printf '%s' 'sk-...'          | sudo tee openai_key >/dev/null
   sudo chmod 0444 phrase salt openai_key
   ```

   `phrase` and `salt` must be exactly the values the notes were encrypted
   with. If they're wrong, every note becomes unreadable.

2. **Shared config:** copy `common.env.sample` to `/opt/docker/shared/common.env`.
   This is optional; without it the app's defaults apply.

3. **App settings:** in this folder:

   ```sh
   cp .env.pre-prd .env && chmod 600 .env
   ```

   Replace every `__FILL_ME__` value.

4. **Network:** the external `web` network must exist (`docker network create web`)
   and Traefik must provide the `redirect@file` middleware and the
   `mytlschallenge` cert resolver.

5. **Start:**

   ```sh
   docker compose up -d
   docker compose logs -f mikeryan
   ```

   On start, the container applies database migrations (`alembic upgrade head`)
   and then starts the app.

6. **First passkey:** open `https://mikeryan.ie/users/register` and use the
   `REGISTRATION_BOOTSTRAP_TOKEN`. Afterwards, blank that value in `.env` and
   run `docker compose up -d` again.

## Updating

After a `make docker-push`:

```sh
./db-backup.sh         # before any release that adds a migration
docker compose up -d   # pull_policy: always fetches the newest :latest
```

## Backups

```sh
./db-backup.sh         # writes backups/mrie-YY-MM-DD-HHMM.db
```

This uses SQLite's backup API inside the running container, on the file
named by the container's `DB_NAME`. Never `cp` the
`.db` file off the volume while the app is running: recent writes still in the
`-wal` file would be missing from the copy.

## Rules

- Run exactly one container. Don't scale this service.
- Keep the volumes on local disk (not NFS/SMB): SQLite's WAL mode needs it.
- Never put `PHRASE`/`SALT`/`OPENAI_KEY` in `.env`: a `.env` value overrides
  the secret file.
