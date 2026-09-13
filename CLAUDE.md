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
make run-dev          # uvicorn with --reload, port 5000
make test             # pre-commit run -a && PYTHONPATH=. pytest (+ coverage badge)
make cleanup          # autoflake + ruff + isort, in that order
make ruff             # ruff check --fix on src/ and tests/
make black            # black on src/ and tests/
make cache            # clean __pycache__ / .pytest_cache
make help             # list all targets with descriptions
```

Run a single test directly (skips pre-commit and coverage/badge generation):

```bash
PYTHONPATH=. pytest tests/test_main.py::test_read_root -v
```

`make help` shows every active target; several targets (alembic, docker,
flake8, pyright, granian) are commented out in the `makefile` with a
reason each — they're not wired up (packages not installed / no
migrations exist yet), not accidentally broken.

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
redirect. `src/app_middleware.py` adds `SessionMiddleware` (cookie-based
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
  export, auto-detected by header), dashboard/insights.
- `web_links.py` — bookmark CRUD, AI title/summary, Selenium screenshot
  capture, CSV import.
- `about.py` — singleton public About page (`AboutPage` DB row, always
  read/created via `_get_or_create_about()`). `GET /about` is public;
  `GET`/`POST /about/edit` and `POST /about/upload-image` require login.
  Uploaded images land on the filesystem (`static/uploads/about/`,
  gitignored), not the DB-blob-plus-base64 pattern `WebLinks` uses for its
  preview thumbnail — that pattern fits one auto-fetched image per link,
  not a rich-text editor that can accumulate many free-form embedded images
  over time. `about/view.html` extends `base.html` (public shell);
  `about/edit.html` extends `base_app.html` (admin shell) — the one place
  in the app where a single feature spans both layout roots.

All of `notes.py`/`web_links.py`/the write endpoints in `about.py` require
`Depends(check_login)`
(`src/functions/login_required.py`) except the login/register ceremony
endpoints themselves. `check_login` only inspects session shape — it's
agnostic to how the session was populated.

### Data layer
- `src/db_tables.py` — SQLAlchemy models (`Users`, `Notes`, `NoteMetrics`,
  `WebLinks`, `Categories`, `Notifications`, `WebAuthnCredentials`). Notes'
  `_note`/`_summary` columns are Fernet-encrypted at rest
  (`src/functions/encrypt.py`, key derived from `PHRASE`/`SALT`).
- `src/db_init.py` builds the async engine from `settings.db_driver`
  (`DatabaseDriverEnum` in `src/settings.py`: `postgres` / `sqlite` /
  `memory`, the last being in-memory sqlite used for all local dev).
  `src/resources.py::startup_event()` calls `create_tables()`
  unconditionally on boot (idempotent — only creates missing tables; no
  Alembic migrations exist, so it can't alter existing ones) and seeds
  demo data via `functions/demo_data.py` whenever the driver isn't
  postgres and `users` is empty.
- All queries go through `db_ops` (`devsetgo-lib`'s `DatabaseOperations`,
  instantiated once in `src/resources.py`). **Gotcha**:
  `db_ops.count_query(query)` wraps whatever `Select` you pass it in its
  own `SELECT count(*) FROM (<your query>)` — pass a plain
  `Select(SomeTable)...`, never `Select(func.count(SomeTable.pkid))` (the
  latter silently always returns `1`).

### Optional-dependency pattern
`openai`, `selenium`/`webdriver_manager`, `nameparser`, `silly`, `tqdm`,
`unsync` are all commented out of `requirements/prd.txt` by default (see
`PROJECT_STATUS.md` for why) but every module that uses them guards the
import — `src/functions/_optional_deps.py` provides no-op fallbacks for
`tqdm`/`unsync`; `ai.py`, `youtube_helper.py`, `link_preview.py`,
`demo_data.py` each wrap their optional import in `try/except
ImportError` and either no-op or raise a clear `RuntimeError` at
*call time*. The app must always boot and serve pages with none of these
installed — when adding a new optional integration, follow this same
pattern rather than a hard top-level import.

### Settings
`src/settings.py` (pydantic-settings, reads `.env`). Notable fields:
`db_driver`, `webauthn_rp_id`/`webauthn_origin` (must match how the app is
actually accessed — see gotcha below), `registration_bootstrap_token`,
`https_only`/`same_site`/`max_age` (session cookie behavior),
`admin_user`. `.env.sample` documents every var with a placeholder and
must be kept in sync when adding new settings.

`dev_fake_login_allowed` (a property, not a raw field) is the pattern to
follow for any other dev-only bypass: it requires **both** an explicit
opt-in flag and `release_env` matching an explicit allowlist of known
non-prod values, so a single misconfigured var can't accidentally expose
it — see `GET /users/dev-login` in `src/endpoints/users.py` for the
consuming side (returns 404, not a refusal, when disallowed).

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
