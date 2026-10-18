# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mrie` (mikeryan.ie) is a single-user personal site: a private journal
("Notes", with mood/AI analysis) and a link-bookmarking tool ("Links",
with AI summary + screenshot capture). FastAPI + Jinja2 + htmx,
server-rendered — no SPA framework, no build step for the frontend. See
`PROJECT_STATUS.md` for build history and the current roadmap.

## Commands

Dependencies install straight into the container Python — there is no
virtualenv (`pip3 install -r requirements/dev.txt`), and that's
deliberate, not an oversight.

```bash
make install-dev     # pip install requirements/dev.txt (includes prd.txt)
make install-optional  # pip install requirements/optional.txt (selenium, silly, tqdm)
make run-dev          # uvicorn with --reload, port 5000
make test             # pre-commit run -a && pytest -n auto --maxprocesses=8 (parallel; + coverage badge)
make cleanup          # same as `make ruff`
make ruff             # ruff check --fix, then ruff format, on src/ and tests/
make cache            # clean __pycache__ / .pytest_cache
make alembic-migrate  # alembic upgrade head on the SQLite file DB (see Data layer below)
make db-backup        # consistent snapshot of the prod DB from the running container (deploy/db-backup.sh) into deploy/backups/
make alembic-rev      # alembic revision --autogenerate, prompts for a name
make alembic-downgrade  # alembic downgrade -1
make docker-push      # build (dockerfile, lowercase - needs -f) and push mikeryan56/mikeryanie:<version> and :latest
make release          # make test, bumpcalver, commit ALL uncommitted changes as "bump to <version>", tag, build, push commit+tag, push image
make help             # list all targets with descriptions
```

Local dev defaults to `DB_DRIVER=sqlite`, a file at
`sqlite_db/mrie.db` (`DB_DIRECTORY`/`DB_NAME`), matching production; the
devcontainer's `postCreateCommand` runs `alembic upgrade head` against it
on container creation. Set `DB_DRIVER=memory` in `.env` instead for a
quick, disposable in-memory DB (this is what the automated test suite
always uses, regardless of `.env` — see `tests/conftest.py`).

Run a single test directly (skips pre-commit and coverage/badge generation):

```bash
PYTHONPATH=. pytest tests/test_main.py::test_read_root -v
```

`make help` shows every active target; a few (flake8, pyright, granian)
are commented out in the `makefile` with a reason each — they're not wired
up (packages not installed), not accidentally broken. The docker targets
(`docker-login`/`docker-build`/`docker-push`) are live: nothing in CI builds
the image, so `make release` (or `make docker-push` without bumping) is the release step.

## Architecture

### Request flow / app assembly
`src/main.py` builds the `FastAPI` app and owns the `/` route only —
everything else lives in `src/app_routes.py`/`src/endpoints/`.
`src/app_routes.py::create_routes()` mounts static files, registers the
feature routers (see below), the `/error/{code}` HTML error page,
health endpoints, and — importantly — a global `StarletteHTTPException`
handler. That handler **content-negotiates**: if the request sent
`Accept: application/json` it returns a plain JSON `{"detail": ...}` body
(status preserved), otherwise it redirects to `/error/{code}` with
`303 See Other` specifically (not the default 307 — a 307 would preserve
the original request's method, and since `/error/{code}` is GET-only, a
failed POST would redirect into a 405 into another redirect, forever).
Any new endpoint meant to be called via `fetch()`/htmx from JS should send
that `Accept` header if it wants JSON errors back instead of an HTML
redirect. A 401 specifically is a separate special case even when JSON
isn't requested: `check_login` rejecting a missing/expired/dangling
session (e.g. the session's `user_identifier` no longer exists after the
in-memory dev DB reset) clears the session and redirects to
`/users/login` — landing on a bare "401 Unauthorized" page would
otherwise be a dead end with no way forward. An htmx fragment request
(`HX-Request: true`, e.g. `hx-get="/notes/pagination"`) hitting that same
401 gets an `HX-Redirect` response header instead of the raw 303 — htmx
turns that into a full browser navigation, rather than swapping the
entire login page's HTML into whatever small `hx-target` issued the
request. `src/app_middleware.py` adds `SessionMiddleware` (cookie-based
session, `https_only`/`same_site`/`max_age` from settings) and a
request-logging middleware.

### Two layout roots — don't mix them up
- `templates/base.html` — the public, logged-out landing page
  (`templates/index.html`). No nav, no auth-aware chrome.
- `templates/base_app.html` — the authenticated app shell for everything
  under `/notes` and `/weblinks` (sidebar + top navbar, breadcrumb, footer,
  Material Design via MDB UI Kit + jQuery + Chart.js + htmx). Every page
  under `notes/` and `weblinks/` extends this one, via the same block names
  (`page_title`, `page_head_title`, `page_breadcrumb`, `body`,
  `page_stylesheet`, `page_scripts`) regardless of shell changes.
  **Two JS bundles are loaded deliberately**: MDB core's JS (drives new shell
  markup via `data-mdb-*`) *and* Bootstrap 5.3.3's JS bundle (a bridge —
  every page's body content still uses `data-bs-toggle`/`data-bs-target` for
  tabs/collapses, and MDB's JS does not recognize that attribute namespace at
  all). Don't remove the Bootstrap bundle without first migrating every
  `data-bs-*` in `notes/`/`weblinks/` templates to `data-mdb-*`. MDB's own
  freebie admin template was used only as one-time layout inspiration, never
  vendored — see `PROJECT_STATUS.md` Phase 3 for why (an unversioned upstream
  template, same failure shape as the AdminLTE experience that motivated
  this).

Within `templates/notes/`, some templates are full pages
(`{% extends "base_app.html" %}`, safe to link to directly — e.g.
`dashboard.html`, `list.html`, `issues.html`, `today.html`) and some are
**bare htmx fragments with no wrapper** (`pagination.html`,
`page-controls.html`) meant only to be swapped into a container via
`hx-get`/`hx-target` — hitting one of those directly in a browser renders
without any nav/CSS. When adding a new notes/weblinks view, decide up
front which kind it is; nav links must only ever point at the full-page
kind.

### Routers (`src/endpoints/`)
- `users.py` — WebAuthn/passkey login + registration only. Single admin
  identity (`settings.admin_user`); `registration_open()` vs
  `registration_authorized()` gate whether a new passkey can ever be
  claimed (see `PROJECT_STATUS.md` Auth section for the full bootstrap
  logic and the `REGISTRATION_BOOTSTRAP_TOKEN` requirement).
- `notes.py` — journal CRUD, AI mood/tag/summary analysis (background
  task on create), CSV import (two formats — simple and dsg's full
  export, auto-detected by header; both bulk endpoints read the upload via
  `functions/csv_upload.py::read_csv_upload()`, which strips Excel's BOM
  and turns a missing, empty or non-UTF-8 file into a 400), dashboard/insights.
- `web_links.py` — bookmark CRUD, AI title/summary, Selenium screenshot
  capture, CSV import. Title/summary come from the page itself:
  `functions/link_analysis.py` fetches it (`functions/page_fetch.py`, or
  YouTube's oEmbed metadata for videos) and makes one JSON-mode call
  (`ai.get_link_analysis`), as a background task, so the row holds the
  `WEBLINK_PROCESSING` placeholder meanwhile. Every server-side fetch of a
  user-supplied URL (page text, link status, Selenium) must go through
  `page_fetch.is_safe_public_url()`, per redirect hop — it blocks private,
  loopback, link-local and cluster addresses. Connect to the address it
  validated (`resolve_public_ip()` + `pinned_request()`), never let the
  HTTP client resolve the name again (DNS rebinding). Link URLs are
  validated on every write with `page_fetch.normalize_link_url()` (http/https
  only) and rendered with the `safe_href` filter.
- `about.py` — singleton public About page (`AboutPage` DB row, always
  read/created via `_get_or_create_about()`). `GET /about` is public;
  `GET`/`POST /about/edit` and `POST /about/upload-image` require login.
  `POST /about/generate-from-resume` (login) turns an uploaded résumé
  (`functions/resume_text.py`: PDF via `pypdf`, DOCX via stdlib zip/XML,
  or pasted text) into a draft via `ai.get_about_from_resume()`. The model
  returns structured JSON, which `about/generated.html` renders with
  autoescaping, so it never emits raw HTML. The draft is loaded into the editor and only
  stored when the user saves it.
  Uploaded images are stored one row each in `AboutImages` and served by
  the public `GET /about/images/{id}`; the stored HTML references that URL.
  They are not loose files on disk, so the one SQLite file is the whole
  site's state and a single `make db-backup` captures everything.
  They are also not base64-inlined like `WebLinks`' preview thumbnail,
  which would bloat `content` with every embedded image. Nothing in the app
  may write runtime state to local disk outside the database; it all goes in SQLite. `about/view.html` extends `base.html` (public shell);
  `about/edit.html` extends `base_app.html` (admin shell) — the one place
  in the app where a single feature spans both layout roots.

All of `notes.py`/`web_links.py`/the write endpoints in `about.py` require
`Depends(check_login)`
(`src/functions/login_required.py`) except the login/register ceremony
endpoints themselves. `check_login` only inspects session shape — it's
agnostic to how the session was populated.

### Data layer
- `src/db_tables.py` — SQLAlchemy models (`Users`, `Notes`, `NoteMetrics`,
  `WebLinks`, `Categories`, `Notifications`, `WebAuthnCredentials`,
  `AboutPage`, `AboutImages`). Notes'
  `_note`/`_summary` columns are Fernet-encrypted at rest
  (`src/functions/encrypt.py`, key derived from `PHRASE`/`SALT`).
- `src/db_init.py` builds the async engine from `settings.db_driver`
  (`DatabaseDriverEnum` in `src/settings.py`: `sqlite` / `memory` — SQLite
  is the only database; Postgres support was removed, see
  `PROJECT_STATUS.md`). A `connect` event listener applies the performance
  PRAGMAs to every pooled connection: WAL, `synchronous=NORMAL`,
  `busy_timeout`, `foreign_keys=ON`, in-memory temp store, a large page
  cache and mmap (sizes in `SQLITE_*` settings). Don't remove WAL: the
  uvicorn workers share one file, and WAL is what lets reads proceed while
  another worker writes.
- Schema for the SQLite file is managed by Alembic (see `alembic/`,
  `render_as_batch=True` because SQLite can't `ALTER` most column changes)
  — `alembic upgrade head` runs out-of-band (the Docker image's `CMD`
  before uvicorn starts, devcontainer `postCreateCommand`, CI, or
  `make alembic-migrate`), never from app boot, and `startup_event()`
  refuses to start if `alembic_version` is missing. Only the `memory`
  driver gets its schema from `create_tables()` on boot instead. Any model
  change needs an `alembic revision --autogenerate`; CI runs
  `alembic check` and fails if one is missing. Demo data
  (`functions/demo_data.py`) is seeded only when `release_env` is a dev
  value and `users` is empty — production is SQLite too, so the driver
  can't be the gate.
- `NoteMetrics` stores both its plain scalar aggregates (`word_count`,
  `note_count`, …) and the Python-computed `metrics` JSON blob, recomputed
  by `src/functions/notes_metrics.py::update_notes_metrics()`.
- All queries go through `db_ops` (`devsetgo-lib`'s `DatabaseOperations`,
  instantiated once in `src/resources.py`). **Gotcha**:
  `db_ops.count_query(query)` wraps whatever `Select` you pass it in its
  own `SELECT count(*) FROM (<your query>)` — pass a plain
  `Select(SomeTable)...`, never `Select(func.count(SomeTable.pkid))` (the
  latter silently always returns `1`).

### Rendering user content
Never emit a note body with `|safe`. Use the `sanitize_html` filter (nh3)
or `render_markdown` (markdown-it-py, raw HTML escaped, then sanitized);
both live in `src/functions/sanitize.py` and fail closed to escaping if
the package is missing. Anything that acts (re-analysis, AI fix, logout)
is a `POST` with a `303` redirect, never a `GET`: a cross-site top-level
`GET` carries `SameSite=Lax` cookies.

### Optional-dependency pattern
`selenium`/`webdriver_manager`, `silly` and `tqdm` are pinned in
`requirements/optional.txt`, not `prd.txt`, so neither the production image
nor `make install-dev` installs them (`make install-optional` does). They
used to be commented-out lines in `prd.txt`, but the dependency-update tool
that rewrites these files drops every comment, so keep explanations here and
in `PROJECT_STATUS.md`, never in the requirements files. `openai` and
`nameparser` are in `prd.txt` because note AI analysis and person-name
filtering depend on them. Every module that uses any
of these still guards the import — `src/functions/_optional_deps.py` provides
no-op fallbacks for `tqdm`; `ai.py`, `youtube_helper.py`,
`link_preview.py`, `demo_data.py` each wrap their optional import in `try/except
ImportError` and either no-op or raise a clear `RuntimeError` at
*call time* (for OpenAI, `get_client()`/`require_client()` in `ai.py` build the
client lazily and say separately whether the package or `OPENAI_KEY` is
missing). Screenshot capture needs `selenium` *and* a Chrome binary at
`/usr/bin/google-chrome`; the production image has neither, so previews
are simply skipped there (logged once, never an error). The app must
always boot and serve pages with none of these
installed — when adding a new optional integration, follow this same
pattern rather than a hard top-level import.

### Settings
`src/settings.py` (pydantic-settings, reads `.env`). Notable fields:
`db_driver`, `webauthn_rp_id`/`webauthn_origin` (must match how the app is
actually accessed — see gotcha below), `registration_bootstrap_token`,
`https_only`/`same_site`/`max_age` (session cookie behavior),
`admin_user`. `.env.sample` (local dev) documents every var with a
placeholder and must be kept in sync when adding new settings, as must
`deploy/.env.pre-prd` (production template) for anything production needs.
Outside a dev `release_env`, `require_production_secrets` refuses to start
when `session_secret_key` is missing or under 32 characters (uvicorn runs
several workers; a per-process random key would make them reject each
other's cookies) or when any setting still holds the template's
`__FILL_ME__` marker. `hide_input_in_errors` keeps a failed validation
from printing PHRASE/SALT/keys into the startup log. `echo` defaults to off since it logs every bound parameter.

`dev_fake_login_allowed` (a property, not a raw field) is the pattern to
follow for any other dev-only bypass: it requires **both** an explicit
opt-in flag and `release_env` matching an explicit allowlist of known
non-prod values, so a single misconfigured var can't accidentally expose
it — see `GET /users/dev-login` in `src/endpoints/users.py` for the
consuming side (returns 404, not a refusal, when disallowed).

### Deployment
Everything the server needs lives in `deploy/` (`docker-compose.yml`,
`.env.pre-prd`, `common.env.sample`, `db-backup.sh`, and a `README.md` with
the setup steps), which is copied to the server as one folder. Production is
one container on one server behind Traefik, with the SQLite file on the
`mrie-db` volume at `/app/sqlite_db` and logs on `mrie-logs` at `/app/logs`
(volume names pinned with `name:`, so they don't depend on the folder name).
`static/` is baked into the image, not a volume: About-page uploads are
already in the DB, and a volume over `static/img` would keep serving the
first image's files after every later build. The compose file pins
`RELEASE_ENV=prd`, `DEV_FAKE_LOGIN_ENABLED=False` and the DB/log paths in
`environment:`, which beats `.env`. Traefik redirects `www.` to the bare
domain, because WebAuthn verifies a single exact `WEBAUTHN_ORIGIN`. Never scale it past one container, and keep the volume
on local disk: WAL needs shared-memory locking that NFS/SMB don't provide.
The image's `CMD` runs `alembic upgrade head` then `exec`s uvicorn
(`WEB_CONCURRENCY=2` workers sharing the file). uvicorn must trust
Traefik's forwarded headers (`FORWARDED_ALLOW_IPS`, `*` in compose since
port 5000 is unpublished), or `url_for()` emits `http://` asset URLs on the
https site. The image tag is `latest` with `pull_policy: always`.
Secrets are Docker secrets, not `.env` values: `phrase`, `salt` and
`openai_key` are mounted from `SHARED_SECRETS_DIR` (default
`/opt/docker/shared/secrets`) at `/run/secrets/<name>`, and
`src/settings.py` reads any file there whose name matches a setting
(`secrets_dir`, only when the directory exists). A real env var or `.env`
value still wins, so local dev is unchanged. Shared non-secret config
(logging, timezone) comes from `SHARED_CONFIG_FILE` (see
`deploy/common.env.sample`) and is overridden by the app's own `.env`. `PHRASE`
and `SALT` must stay identical to what the notes were encrypted with. The
compose file is meant to be copied into a server repo: keep it
self-contained (no relative build context). Back up with `deploy/db-backup.sh` (`make db-backup`), which uses SQLite's online
backup API, never a plain `cp` of a live WAL database.

### Static assets
`src/resources.py::static_version(path)` is a Jinja global that returns a
file's mtime for cache-busting (`?v=...`). Any `<script src="{{
url_for('statics', ...) }}">` should go through it — a stale cached copy
of `static/js/webauthn.js` was the direct cause of a hard-to-diagnose bug
(see `PROJECT_STATUS.md`).

## Gotchas

- **WebAuthn origin/rp_id matching is exact.** Access the app as
  `http://localhost:5000`, not `127.0.0.1` or a forwarded/tunneled URL,
  unless `WEBAUTHN_RP_ID`/`WEBAUTHN_ORIGIN` are overridden to match.
- **`HTTPS_ONLY=True` in `.env` breaks passkey login over plain HTTP** —
  the session cookie gets marked `Secure` and a browser won't send it
  over `http://`, and the WebAuthn challenge lives in that session between
  the "begin" and "complete" calls.
- **In-memory SQLite resets on every restart**, including `--reload`
  restarts triggered by file edits — don't be surprised by having to
  re-register a passkey mid-session locally.
