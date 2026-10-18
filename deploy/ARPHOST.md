# Installing mikeryan.ie on the arphost server

A runbook for the first install on arphost, where Docker and Traefik are
already running and serving other apps. The site starts with an empty
database. Do the steps in order; each one ends with a check you can run.

[README.md](README.md) in this folder is the general reference (what is
stored where, updating, backups). This file is the arphost-specific checklist.

Paths used below (change them if you like, but keep them consistent):

| What | Path on arphost |
|---|---|
| This folder | `/opt/docker/mrie` |
| Shared secrets | `/opt/docker/shared/secrets` |
| Shared config | `/opt/docker/shared/common.env` |

---

## 1. Publish the image (on your dev machine, not the server)

The server pulls `mikeryan56/mikeryanie:latest` from Docker Hub, and nothing
in CI builds it.

```sh
make docker-login      # once per machine
make docker-push       # builds and pushes :<version> and :latest
```

**Check:** the new tag is listed at
https://hub.docker.com/r/mikeryan56/mikeryanie/tags. If the repository is
private, run `docker login` on arphost as well, or the pull in step 8 fails.

---

## 2. Confirm Traefik matches what the compose file expects

`docker-compose.yml` refers to four names that must already exist in the
Traefik setup on arphost. They were copied from an earlier setup, so
confirm them before starting anything:

| Name in docker-compose.yml | What it is |
|---|---|
| `web` (network) | external Docker network Traefik is attached to |
| `web` (entrypoint) | Traefik's port-80 entrypoint |
| `web-secured` (entrypoint) | Traefik's port-443 entrypoint |
| `redirect@file` | http→https middleware from Traefik's file provider |
| `mytlschallenge` | Let's Encrypt certificate resolver |

```sh
docker network ls | grep -w web
docker ps --format '{{.Names}}' | grep -i traefik       # find the container
docker inspect <traefik-container> --format '{{join .Args "\n"}}' \
  | grep -Ei 'entrypoints|certificatesresolvers|providers.file'
```

If that prints nothing, Traefik is configured from a static file instead
(`traefik.yml` or `traefik.toml`, usually mounted into the container; see
`docker inspect <traefik-container> --format '{{json .Mounts}}'`). Look for
`entryPoints`, `certificatesResolvers` and `providers.file` there.

Then check the file provider's directory for the redirect middleware:

```sh
grep -rn 'redirect' <file-provider-directory>
```

**Check:** all five names exist. If any is spelled differently (e.g. the
entrypoint is `websecure`, or the resolver is `letsencrypt`), edit the
matching labels in `docker-compose.yml` to match. Another app's compose file
on the same server is the quickest reference.

---

## 3. Point DNS at arphost

Both names need an A record (and AAAA, if you use IPv6) with arphost's IP:

- `mikeryan.ie`
- `www.mikeryan.ie` (Traefik redirects it to `mikeryan.ie`)

**Check:** both return the arphost IP:

```sh
dig +short mikeryan.ie
dig +short www.mikeryan.ie
```

Let's Encrypt can only issue the certificate once DNS resolves here, so do
this before step 8.

---

## 4. Copy this folder to the server

From the repo root on your dev machine:

```sh
ssh <you>@arphost 'sudo mkdir -p /opt/docker/mrie && sudo chown $USER /opt/docker/mrie'
scp -r deploy/. <you>@arphost:/opt/docker/mrie/
```

`deploy/.` (with the dot) copies the hidden `.env.pre-prd` too.

**Check:** on arphost:

```sh
ls -A /opt/docker/mrie
# .env.pre-prd  ARPHOST.md  README.md  common.env.sample  db-backup.sh  docker-compose.yml
```

---

## 5. Create the secrets

The app reads `phrase`, `salt` and `openai_key` from one file each.

```sh
sudo mkdir -p /opt/docker/shared/secrets
ls -l /opt/docker/shared/secrets
```

**If `phrase` and `salt` already exist** (another app on the server uses
them), decide before going on:

- **Share them** (the default): mrie reads the same files. Then nobody may
  ever change them, for either app. Changing them makes every mrie note
  unreadable.
- **Keep mrie's separate:** create e.g. `/opt/docker/mrie-secrets` with all
  three files, and add `SHARED_SECRETS_DIR=/opt/docker/mrie-secrets` to the
  `.env` in step 7. Compose reads it from there.

**If they don't exist yet**, create them. The site starts empty, so these
can be brand-new random values:

```sh
cd /opt/docker/shared/secrets
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" | sudo tee phrase >/dev/null
python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" | sudo tee salt   >/dev/null
printf '%s' 'sk-...your OpenAI key...'                                 | sudo tee openai_key >/dev/null
sudo chmod 0444 phrase salt openai_key
```

> **Save `phrase` and `salt` somewhere off the server now** (a password
> manager). They are the encryption key for every note. If you lose them, a
> database backup can't be read: there is no recovery.

**Check:** the container runs as uid 10001, which isn't the owner, so all
three files must be world-readable:

```sh
stat -c '%a %n' /opt/docker/shared/secrets/*    # 444 for phrase, salt, openai_key
```

---

## 6. Shared config (optional)

If `/opt/docker/shared/common.env` already exists for the other apps, leave
it alone. mrie uses it as-is. If it doesn't exist:

```sh
sudo cp /opt/docker/mrie/common.env.sample /opt/docker/shared/common.env
```

It holds the log level, log retention and timezone. mrie's own `.env` can
override any of them.

---

## 7. Fill in the app's .env

```sh
cd /opt/docker/mrie
cp .env.pre-prd .env && chmod 600 .env
```

Generate the two random values:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # SESSION_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # REGISTRATION_BOOTSTRAP_TOKEN
```

Edit `.env` and replace every `__FILL_ME__`:

| Setting | Value |
|---|---|
| `SESSION_SECRET_KEY` | first value above (must be 32+ characters) |
| `ADMIN_USER` | the one username allowed to log in |
| `REGISTRATION_BOOTSTRAP_TOKEN` | second value above; keep it handy for step 9 |

Leave `WEBAUTHN_RP_ID=mikeryan.ie` and `WEBAUTHN_ORIGIN=https://mikeryan.ie`
as they are: the bare domain, no `www`, no trailing slash.

**Never** add `PHRASE`, `SALT` or `OPENAI_KEY` to this file: a value here
silently overrides the secret file.

**Check:**

```sh
grep -n __FILL_ME__ .env || echo "all filled in"
```

The app refuses to start while any `__FILL_ME__` is left, and says which one.

---

## 8. Start it

```sh
cd /opt/docker/mrie
docker compose config --quiet && echo "compose file OK"
docker compose up -d
docker compose logs -f mikeryan
```

In the logs you should see Alembic's `Running upgrade -> 93c7e052d8d6,
sqlite baseline` (creating the empty database), then uvicorn starting.
Press Ctrl-C to stop following; the container keeps running.

**Check:**

```sh
docker compose ps                         # STATUS shows (healthy) after ~20s
docker volume ls | grep mrie              # mrie-db and mrie-logs exist
curl -sI https://mikeryan.ie | head -1    # HTTP/2 200
curl -sI https://www.mikeryan.ie | grep -i '^location'   # -> https://mikeryan.ie/
curl -sI http://mikeryan.ie | grep -i '^location'        # -> https://...
```

The first https request can take a few seconds while Traefik gets the
certificate.

---

## 9. Register your passkey

1. Open **https://mikeryan.ie/users/register** (exactly that address).
2. Enter the `REGISTRATION_BOOTSTRAP_TOKEN` from step 7 and create the
   passkey.
3. Log in at https://mikeryan.ie/users/login.

Then close registration (once a passkey exists it's closed to logged-out
visitors anyway; this removes the token too):

```sh
cd /opt/docker/mrie
sed -i 's/^REGISTRATION_BOOTSTRAP_TOKEN=.*/REGISTRATION_BOOTSTRAP_TOKEN=/' .env
docker compose up -d
```

To add another device later, log in on the first one and register from
there. That doesn't need the token.

**Check:** write a test note and add a test link. Within a minute the note
should get a mood and summary and the link a title, which shows the OpenAI
key works. Delete both afterwards.

---

## 10. Schedule backups

`db-backup.sh` takes a consistent snapshot of the live database into
`/opt/docker/mrie/backups/`. Run it once by hand:

```sh
/opt/docker/mrie/db-backup.sh
```

Then add it to the crontab of a user in the `docker` group
(`crontab -e`): nightly at 03:00, keeping 30 days:

```cron
0 3 * * * /opt/docker/mrie/db-backup.sh >> /opt/docker/mrie/backups/backup.log 2>&1 && find /opt/docker/mrie/backups -name 'mrie-*.db' -mtime +30 -delete
```

Copy `backups/` off the server too (rsync, or whatever backs up the other
apps). A backup on the same disk doesn't survive losing the server, and it
can't be read without the `phrase`/`salt` you saved in step 5.

---

## Updating later

On your dev machine run `make docker-push`, then on arphost:

```sh
cd /opt/docker/mrie
./db-backup.sh
docker compose up -d     # pulls the new :latest and applies any migrations
docker compose logs --tail 50 mikeryan
```

---

## When something goes wrong

| Symptom | Cause and fix |
|---|---|
| Container exits, log says `Fill in X (still __FILL_ME__)` | Step 7: a value in `.env` wasn't replaced. |
| `SESSION_SECRET_KEY must be at least 32 characters` | Step 7: regenerate it with the command shown. |
| `Field required` for `phrase` or `salt` | Step 5: the secret file is missing or unreadable by uid 10001 (`chmod 0444`). |
| Traefik returns 404 for mikeryan.ie | Container isn't on the `web` network, or the labels don't match Traefik's names (step 2). Check with `docker compose ps` and the Traefik logs. |
| Browser shows a certificate warning | DNS didn't point at arphost when Traefik first asked (step 3). Fix DNS, then `docker compose restart mikeryan`. |
| Passkey: "origin" or "RP ID" error | You're on a different address than `WEBAUTHN_ORIGIN`. Use https://mikeryan.ie exactly. |
| Logged out after a restart | Expected if `SESSION_SECRET_KEY` changed; just log in again. |
| Notes or links never get a summary | `openai_key` secret missing or wrong: `docker compose logs mikeryan \| grep -i openai` (look for "OpenAI client unavailable"). |
| Page loads without styling | The browser blocked `http://` assets: `FORWARDED_ALLOW_IPS` must stay `"*"` in the compose file. |
| Link previews (screenshots) never appear | Expected: the image ships without Chrome, so previews are skipped in production. |

Logs: `docker compose logs mikeryan` (console output), or the rotated files
on the `mrie-logs` volume:

```sh
docker compose exec mikeryan sh -c 'tail -n 100 logs/*.log'
```
