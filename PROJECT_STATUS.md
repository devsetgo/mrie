# mrie: project status and roadmap

Canonical reference for where this project is and where it's headed, so a
future Claude Code session (or Mike) can pick it up without re-deriving
decisions from chat history. Supersedes the earlier `NOTES_LINKS_PLAN.md`
(folded in below as Phase 1) — that file's scope (just the Notes+Links
build) outgrew its name once the work expanded into a broader UI/UX pass.

## What mrie is

Mike's personal site: a single-user private journal ("Notes", with
mood/AI analysis) and a link-bookmarking tool ("Links", with AI
summary + screenshot capture). FastAPI + Jinja2 + htmx, server-rendered,
no SPA framework. Single admin identity, WebAuthn/passkey-only login — see
Auth below.

## Phase 1 — Notes + Links feature build (prior session)

### Database
- mrie owns its own database/schema independently (`src/db_tables.py`:
  `Users`, `Notes`, `NoteMetrics`, `WebLinks`, `Categories`,
  `Notifications`) — originated as a port of a sibling project's (dsg)
  table shapes but now evolves independently.
- `src/resources.py`'s `startup_event` calls `async_db.create_tables()`
  unconditionally on boot — safe/idempotent (only creates missing tables).
- No Alembic migrations yet — add when a real schema change is needed, not
  before.
- Local dev: `DB_DRIVER=memory` (in-memory sqlite). `demo_data.py` seeds an
  admin user + demo notes/weblinks whenever the driver isn't postgres and
  `users` is empty at boot. Never runs against real Postgres.
- **Production DB: staying on Postgres — see Decisions below.**

### Encryption
- Notes encrypted at rest (`_note`/`_summary` columns, Fernet key derived
  from `PHRASE`/`SALT` via PBKDF2 — `src/functions/encrypt.py`). mrie uses
  its own independent `PHRASE`/`SALT`, not shared with dsg.

### Auth — WebAuthn/passkey only
- No password, no OAuth provider. `src/endpoints/users.py` +
  `static/js/webauthn.js` (hand-rolled vanilla JS, not a CDN library —
  deliberate, since this is security-sensitive code worth keeping
  auditable). Registration is discoverable/resident-key (no username typed
  at login). Credentials live in `WebAuthnCredentials`, one admin `Users`
  row can hold several (one per device).
- **Bootstrap problem and how it's closed**: nothing can create a
  credential except a successful registration, but registration must close
  once one exists. `registration_open()` in `users.py` is true when either
  the caller is already logged in as admin (adding a device) or zero
  credentials exist anywhere yet (first-ever registration).
  `registration_authorized()` gates the *actual* register calls further:
  the already-logged-in-admin path is unchanged, but the first-ever path
  additionally requires an `X-Bootstrap-Token` header matching
  `settings.registration_bootstrap_token` (`REGISTRATION_BOOTSTRAP_TOKEN`
  env var) — without it, first-time registration is closed to everyone.
  This exists because the original "first registration wins" rule had no
  identity check at all: whoever hit `/users/register` first — not
  necessarily Mike — would have permanently become the admin. The token
  should be rotated/removed after the first passkey is registered; it has
  no further purpose once a credential exists.
- `check_login`/`login_required.py` unchanged — agnostic to how the
  session got populated.

### Notes import
- `/notes/bulk` accepts two CSV formats, auto-detected by header
  (`src/functions/note_import.py`): a simple format (queued for AI
  processing) and dsg's full export format (10 columns, imported as-is, no
  AI calls — mood/tags/summary come straight from the CSV).

### AI + Links features
- OpenAI-backed mood/tag/summary analysis for newly created notes, OpenAI
  title/summary for weblinks, Selenium/headless-Chrome screenshot capture
  for link previews. All three are **optional-guarded** (see Devcontainer
  below) — the app boots and runs fine without any of them installed; only
  the specific feature fails at call time if its package is missing.

### Devcontainer / dependencies (prior session)
- Python bumped 3.12 → 3.14 everywhere. The devcontainer base image must
  stay pinned to a `-bookworm` variant — the bare `3-3.14` tag resolves to
  Debian trixie, which dropped `apt-key`, breaking the Chrome install step.
- Removed dead `node`/`vue-cli` devcontainer Features (no JS anywhere in
  this repo) — they were also breaking the build via an untrusted Yarn apt
  key.
- `requirements/prd.txt` trimmed to boot-essential only
  (`cryptography`, `devsetgo-lib`, `fastapi[all]`, `pydantic[email]`,
  `webauthn`, plus `pytz`/`python-dateutil` — see Phase 2). Everything else
  (`openai`, `selenium`, `nameparser`, `silly`, `tqdm`, `unsync`,
  `webdriver-manager`, `alembic`, `granian`) is commented out with a
  one-word reason per line, not deleted. This was in anticipation of a
  possible future framework move (see Decisions) and to keep local dev
  lean — restoring any feature is just uncommenting its line, no code
  changes, since every optional import is guarded
  (`src/functions/_optional_deps.py` + per-module `try/except ImportError`
  patterns in `ai.py`, `link_preview.py`, `demo_data.py`, etc.).

## Phase 2 — Build fixes, security hardening, UI overhaul (this session)

### Devcontainer build fix
- The container failed to build: `pydantic==2.9.2`'s pinned
  `pydantic-core==2.23.4` has no prebuilt wheel for Python 3.14 (pyo3 0.22
  caps at 3.13), forcing a Rust source build that fails. Fixed by bumping
  `pydantic[email]` to `2.13.5` (`requirements/prd.txt`) — verified the
  full dependency tree now resolves to prebuilt cp314 wheels with zero
  compilation needed.
- Two more hard-required imports were missing from `requirements/prd.txt`
  entirely (not optional-guarded — genuine boot-time dependencies):
  `pytz` (`date_functions.py`) and `python-dateutil`
  (`note_import.py`). Added both.

### WebAuthn hardening
- Added the bootstrap-token gate described in Phase 1's Auth section.
- Fixed a real infinite-redirect bug in the global exception handler
  (`src/app_routes.py`): it redirected every `HTTPException` to
  `/error/{code}` with an implicit **307**, which preserves the original
  HTTP method. A failed POST (e.g. `/users/login/verify` erroring without
  the right `Accept` header) would get redirected via 307 *as a POST*,
  hit the GET-only `/error/{code}` route, get a 405, redirect again as
  POST... forever (`net::ERR_TOO_MANY_REDIRECTS`, surfaced to `fetch()` as
  a generic "Failed to fetch"). Fixed by forcing **303 See Other**
  (standard Post/Redirect/Get), which always downgrades to GET. The
  handler also now returns plain JSON instead of an HTML redirect when the
  caller sends `Accept: application/json` (WebAuthn's own fetch calls do
  this) — the WebAuthn JS (`webauthn.js`) reads the real `detail` field on
  error and logs full diagnostics (status, redirect chain, raw body) on
  any unexpected non-JSON response.
- `static/js/webauthn.js` is now loaded with a `?v=<mtime>` cache-busting
  query string (`static_version()` global in `src/resources.py`) — a
  stale cached copy of this file was the proximate cause of the redirect
  loop above actually being hit, and this closes that class of bug for any
  future edit to it.

### Routing / navigation
- `/` now redirects logged-in users straight to `/notes` instead of
  showing the logged-out landing page (`src/main.py`).
- `base_app.html`'s navbar was rebuilt: dropdowns for Notes (Dashboard /
  Notes / New Note / History / AI Issues / Bulk Import) and Links (All
  Links / New Link / Bulk Import), an "Add Passkey" link shown only when
  `is_admin`, and correct active-section highlighting.

### Bootstrap 4 → 5 migration
Chosen over Tailwind/Bulma/Pico specifically for lowest migration risk —
same component vocabulary as before, so this was a mechanical pass, not a
redesign. Covered every template: CDN swap (5.3.3, Popper now bundled),
`data-toggle`/`data-target`/`data-dismiss` → `data-bs-*`, `ml-`/`mr-` →
`ms-`/`me-`, `text-left`/`text-right` → `text-start`/`text-end`,
`.form-group` (removed in v5) → `mb-3` across 11 templates,
`.custom-control`/`.custom-switch` → `.form-check`/`.form-switch`,
`.input-group-append` wrapper removed, `<select class="form-control">` →
`form-select`, dropped two dead AdminLTE-only classes that had no CSS
backing them. jQuery kept (upgraded slim → full build) because Trumbowyg
(the notes rich-text editor) is a jQuery plugin and can't be dropped.

### Dashboard split
The combined dashboard (`/notes/`) mixed four AdminLTE-style tabs
(Dashboard/Notes/History/AI Issues) in one page — slow, especially on
mobile, since the Dashboard tab alone renders 7 Chart.js charts and that
markup shipped on every visit regardless of which tab you wanted. Split
into four real pages:
- `GET /notes/` — **Dashboard**: insights only (mood trend by month, mood
  stability score, writing streaks, tag trend rising/falling, activity by
  day/hour, top tags by mood, milestones). This already existed and
  worked — it just needed its own page. Directly answers the "mental
  health insights" ask from the roadmap conversation; no new feature work
  was needed there.
- `GET /notes/list` — **Notes** (new route/template): the search/browse
  view. Same card style throughout (mood, excerpt, tags, date +
  words/characters) — deliberately unchanged, this is the one visual
  pattern Mike explicitly wants kept.
- `GET /notes/today` — **History**: converted from a bare fragment (no
  navbar/CSS when hit directly — this was actually broken before the fix)
  into a full page.
- `GET /notes/issues` — **AI Issues**: already a full page, untouched.

Measured: Notes list dropped to ~9.8KB and History to ~5.3KB standalone,
versus ~53KB for the old combined page on every visit.

### Search filter UX
`search-form.html` rebuilt: primary row (Text, Mood, Search, "More
Filters" toggle, Clear) always visible without scrolling; Tags + Date
range collapsed by default, auto-expanded if either is already active. An
active-filters chip bar (one chip per dimension: text/mood/date-range/each
tag) sits below the form, independently removable and auto-resubmitting.
`hx-push-url` + `/notes/list` reading its own query string on load makes
filtered views bookmarkable/shareable
(`/notes/list?mood=negative&tags=stress` reproduces that exact view).
Fixed a bug introduced by the pre-fill: native `form.reset()` would have
restored pre-filled values instead of clearing them — the Clear button now
blanks fields explicitly.

## Phase 3 — Material Design admin shell + dark mode

The logged-in app shell was committed to a real design direction: Material
Design, kept in an admin-portal layout, with dark mode as a hard requirement.
Compared MDBootstrap free admin, Tabler, CoreUI free dashboard, and Creative
Tim's Material Dashboard — the two genuinely Material-Design options
(MDBootstrap, Creative Tim) don't ship dark mode bundled with their free admin
package, while the non-Material options (Tabler, CoreUI) do. Went with
MDBootstrap anyway, since MDB documents its own dark-mode mechanism as a
well-supported, low-risk addition rather than a hack.

**Upgrade-resilience was the deciding design constraint**, per explicit
feedback from the AdminLTE experience ("upgrading was very time consuming").
Checked the actual freebie repo
([mdbootstrap/bootstrap-5-admin-template](https://github.com/mdbootstrap/bootstrap-5-admin-template)):
**no version tags or releases at all**, and its commit history shows long
gaps with occasional large, disruptive rewrites (the last substantive change,
Dec 2023, restructured the whole template when MDB's core library bumped to
v7) — the same failure shape as AdminLTE. So the freebie's own files are
**never vendored or hand-edited in this project**:

- `templates/base_app.html` depends only on **MDB's *core* UI Kit**
  (`mdb-ui-kit`, CDN, version pinned in the URL — currently `9.3.0`), which
  unlike the freebie genuinely is a versioned, actively maintained library.
  Bumping it later is a one-line version-string change, same as the
  Bootstrap 4→5 migration in Phase 2.
- The sidebar + top-navbar shell markup is hand-written directly in
  `base_app.html`, using MDB core's own documented classes and `data-mdb-*`
  attributes — ported as a *layout idea* from the freebie once, not a
  vendored file that could be restructured out from under the project.
- `static/css/admin.css` and `static/js/admin.js` are small, purpose-written
  files (not copies of the freebie's `admin.css`/`admin.js`), written against
  MDB core's CSS custom properties — `admin.css`'s header comment records
  which freebie state it took layout inspiration from, for future reference,
  without the app ever depending on that repo at runtime.
- **Verified MDB's JS only recognizes `data-mdb-*` attributes — zero
  `data-bs-*` support** (checked directly against the downloaded bundle).
  Since every page under `notes/`/`weblinks/` already used `data-bs-toggle`
  for tabs/collapses built in Phase 2, dropping Bootstrap's JS entirely would
  have silently broken all of them. Bootstrap's JS bundle (5.3.3) is kept
  loaded alongside MDB's specifically as a bridge — existing `data-bs-*`
  markup keeps working untouched. This is temporary: once those pages get
  their own MDB-style visual pass (follow-on work, not yet started), their
  `data-bs-*` can migrate to `data-mdb-*` and the Bootstrap JS bundle can be
  dropped. Confirmed separately that MDB's CSS is a genuine drop-in
  Bootstrap-5-compatible superset (`.card`, `.btn`, `.form-select`,
  `.dropdown-menu`, `.collapse`, etc. all present) — Bootstrap's own CSS was
  fully removed, not loaded alongside MDB's.
- **Dark mode**: `data-mdb-theme="light"|"dark"` on `<html>`, toggled via a
  switch in the top navbar (`#themeToggle`, wired in `static/js/admin.js`),
  persisted to `localStorage` (`mrie-theme` key — a per-viewer preference,
  not shared app state). A small inline script at the very top of `<head>`
  applies the saved preference (or `prefers-color-scheme` if none saved yet)
  before any CSS loads, avoiding a flash of the wrong theme.

**Explicitly out of scope for this pass** (deferred, follow-on work): visual
restyling of individual page content (dashboard tiles, note/weblink card
lists, forms) in MDB's component style — pages render inside the new shell
unchanged for now, via the same `{% block body %}`/`page_head_title`/
`page_breadcrumb`/`page_stylesheet`/`page_scripts` block names as before, so
zero changes were needed to any child template. `templates/base.html` /
`index.html` (public landing page) and the About page were untouched in
this pass — see Phase 4, done in a later session.

## Phase 4 — Public homepage redesign + About page

`templates/base.html` was rebuilt as a two-column shell shared by every
public (logged-out) page: a fixed dark sidebar (`static/img/headshot_2.jpg`
— a rounded-square crop, `object-position: center 20%` biased toward the
top so the head isn't cropped off, at 90% of the sidebar's width; name;
the same LinkedIn/X/GitHub/PyPI social links as before; nav to Home/About;
a session-aware Login-or-Logout link with the passkey icon, pinned above
the copyright line via `margin-top: auto`) plus a `{% block content %}`
main area for page-specific content. `index.html` supplies the Kilkee Bay
hero image in that main area (`{% block extra_head %}` for the
page-specific background CSS) — unchanged from before, just moved out of
the old single full-bleed layout.

The About page started (this session) as a static lorem-ipsum placeholder
served directly from `src/main.py`, then became the real, admin-editable
page described in "About page: DB-backed + rich-text editing" below —
`src/main.py` now owns only `/` again.

`tests/test_main.py::test_read_root` asserted on exact old copy ("Connect",
literal lowercase "mikeryan.ie", "Copyright &copy; Mike Ryan") that this
redesign intentionally changed — updated the assertions to match the new
page's real content instead of leaving them stale, and added
`test_read_about` for the new route.

## About page: DB-backed + rich-text editing

The About page is no longer static content — it's a singleton `AboutPage`
DB row (`src/db_tables.py`), publicly viewable at `GET /about`
(`src/endpoints/about.py`, its own router now, not part of `main.py`), and
editable at `/about/edit` when logged in via the same Trumbowyg rich-text
editor the notes editor uses, reachable from the admin shell's user-menu
dropdown ("Edit About Page") and from a small "Edit this page" link shown
on the public page itself when the viewer has a session. Seeded on first
access with Mike's actual professional summary + skills list (not
placeholder text) via `_get_or_create_about()`, which creates the row with
default content if none exists yet - same "provision on first read"
pattern `_get_admin_user()` in `users.py` already uses.

**Image uploads** (`POST /about/upload-image`) save to
`static/uploads/about/<uuid>.<ext>` on the filesystem (new directory,
gitignored) rather than following `WebLinks.image_preview_data`'s DB-blob +
base64-embed pattern - that pattern fits one auto-fetched preview thumbnail
per link; the About page's rich-text editor can accumulate many free-form
embedded images over time, and base64-inlining all of them into the
`content` text column would bloat it with no way to reuse an image across
edits. Validates content-type against an image allowlist (jpeg/png/gif/
webp), enforces a 5 MB cap, and always generates a random filename (never
trusts the client's filename) before writing - basic upload-endpoint
hygiene, not exercised anywhere else in this app yet. Wired to
[Trumbowyg's upload plugin](https://alex-d.github.io/Trumbowyg/documentation/plugins/),
which has its own JSON contract independent of this app's own
`{"detail": ...}` shape - `{"success": true, "url": "..."}` on success,
`{"success": false, "message": "..."}` on failure - so the endpoint returns
`JSONResponse` directly rather than raising `HTTPException` (which would
produce the wrong shape for the plugin to parse).

**Found and fixed a real gap in the test suite while adding
`test_read_about`**: `tests/test_main.py` built its `TestClient(app)` at
module level, outside a `with` block - which means FastAPI's lifespan
(`startup_event` → `create_tables()`) never actually ran. Every pre-existing
test happened to avoid touching the database at all, so this went
unnoticed; `test_read_about` was the first test to hit a DB-backed public
route, and it surfaced as "200 OK but the expected content is missing"
rather than an obvious error (the DB read/write failed silently, `content`
came back empty, but nothing raised). Fixed with a `client` fixture in the
new `tests/conftest.py` that uses `with TestClient(app) as c: yield c`, so
lifespan runs on setup/teardown for every test - any future test that
touches the database needs this fixture, not a bare `TestClient(app)`.

## Decisions

1. **Login mechanism**: Resolved — WebAuthn/passkey, see Phase 1.
2. **Production DB hosting**: **Resolved — staying on Postgres.** The
   dataset (2,720 notes / 225k words / 1.18M characters, expecting ~2,000
   more) is small enough that neither Postgres nor SQLite would strain
   under it even at 5,000+ notes — this was never really a performance
   question. Postgres already satisfies the multi-node-on-k3s goal
   natively (it's client-server). If cheaper hosting is still wanted
   later, that's a separate infra question (e.g. a cheaper Postgres host),
   not an engine change. `rqlite`/Litestream were considered and explicitly
   rejected for now — rqlite adds real write latency (Raft quorum per
   write) and would need new support built into `devsetgo-lib`; Litestream
   is single-writer only, so it wouldn't actually satisfy the multi-node
   goal anyway.
3. **Alembic migrations**: still not set up — add when a real schema
   change is needed.
4. **Web framework (FastAPI vs. Robyn)**: still explicitly unresolved.
   Don't assume a direction or start a migration unprompted.

## Roadmap — open work, in priority order

1. **Android "Share to" for quick link capture.** Current friction: adding
   a link requires opening the site and logging in first, so it's used far
   less than intended. Direction agreed: a PWA `manifest.json` with a
   `share_target` entry so Android's native share sheet lists mikeryan.ie
   directly (target device: Samsung Fold 8 Ultra). Security layer: the
   existing session/passkey auth, not a separate token system — sharing
   only needs to work from an already-logged-in device, and extending
   session length (already possible via `settings.max_age`) is the
   simplest way to keep that session valid long enough to matter. Not yet
   started — needs a manifest, icons, a capture route, and probably a
   quick confirm/edit-before-save step since share intents only hand over
   a raw URL/title/text.
2. **Admin → activity monitor.** Repurpose the unused multi-user admin
   surface (this app is single-user) into a security/activity view —
   failed login/registration attempts, registered devices, recent errors.
   Concept only, not scoped yet.

## Known pre-existing issues (found, not yet fixed — out of scope so far)

- `GET /weblinks/update/{id}` 500s with `AttributeError: 'NoneType' object
  has no attribute 'chat'` — an AI-assist code path calls the OpenAI
  client without checking whether it's `None` (e.g. when the key/package
  isn't configured). Unrelated to any Phase 2 work.
- The dashboard's `data-card-widget="collapse"` buttons on chart cards
  (`notes/charting.html`) are inert — that's an AdminLTE JS behavior, and
  AdminLTE's JS was never actually loaded (confirmed: only Bootstrap/
  jQuery/Chart.js/htmx are loaded). Cosmetic dead buttons, not wired to
  anything.

## Environment variables (mrie's `.env`)

Required to boot: `DB_USERNAME`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`,
`DB_NAME`, `PHRASE`, `SALT`.

Needed for features to work: `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`,
`WEBAUTHN_ORIGIN`, `ADMIN_USER`, `REGISTRATION_BOOTSTRAP_TOKEN` (only
needed until the first passkey is registered — see Auth), `OPENAI_KEY`,
`SESSION_SECRET_KEY`, `PLAUSIBLE_DOMAIN`, `PLAUSIBLE_SCRIPT_SRC`.

Dev-only, never set in production: `DEV_FAKE_LOGIN_ENABLED` — see "Dev
fake-login" below.

`.env.sample` is kept in sync with the real `.env` structure (placeholder
values only) — update it whenever a new required/optional var is added.

## Gotchas worth remembering

- **`DatabaseOperations.count_query(query)`** (from `dsg_lib`) wraps
  whatever `Select` you pass it in its own `SELECT count(*) FROM (<your
  query>)` — it expects a plain `Select(SomeTable)...`, never
  `Select(func.count(SomeTable.pkid))`. The latter silently always returns
  `1`. Correct usage is already established in `notes.py`/`web_links.py`/
  `users.py`'s `registration_open()`.
- **WebAuthn origin/rp_id matching is exact.** Access the app as
  `http://localhost:5000`, not `127.0.0.1` or a forwarded/tunneled URL,
  unless `WEBAUTHN_RP_ID`/`WEBAUTHN_ORIGIN` are overridden to match
  whatever host is actually being used.
- **`HTTPS_ONLY=True` in `.env` breaks passkey login over plain HTTP.**
  `SessionMiddleware(https_only=...)` marks the session cookie `Secure`,
  which a browser won't send/store over `http://`. The WebAuthn challenge
  lives in that session between the "begin" and "complete" calls, so
  registration/login fails with "No pending challenge" if this is
  mismatched with how the app is actually being accessed.
- **In-memory SQLite (`DB_DRIVER=memory`) resets on every restart**,
  including `uvicorn --reload` restarts triggered by file edits — this
  wipes the admin `Users` row *and* every `WebAuthnCredentials` row, so a
  session from before the restart now points at a user id that no longer
  exists. Landing on a page that requires login then hits a 401 - see the
  fix for that immediately below, and the dev fake-login section for how
  to stop re-registering a passkey after every restart.
- **A 401 from a page navigation clears the session and redirects to
  `/users/login`** (`src/app_routes.py`'s `http_exception_handler`),
  instead of dead-ending on a bare "401 Unauthorized" error page. This
  specifically covers the in-memory-DB-reset case above: the fix is just
  "log back in," and now the app gets you there itself.
- **Cleaning up accumulated Windows passkeys**: each in-memory DB reset
  during dev orphans the previous passkey credential (the app doesn't
  remember it registered one, since that row is gone too) - re-registering
  repeatedly during a dev session leaves the old ones stranded in whatever
  actually stores them on Windows. Where to find and delete them, depending
  on where the browser saved it:
  - **Windows Hello passkeys**: Settings → Accounts → Passkeys (Windows 11,
    newer builds) lists every site-issued passkey and lets you delete them
    individually.
  - **Edge's own passkey manager**: `edge://settings/passkeys` - shows
    passkeys regardless of whether they're Windows Hello- or
    account-backed.
  - **NordPass** (if used as the passkey provider): Items → Passkeys in the
    NordPass app or browser extension.
  - **Google Password Manager** (if signed into a Google/Chrome account
    that intercepted the save): `passwords.google.com/passkeys`.
  Using the dev fake-login below instead of repeatedly registering real
  passkeys avoids this pile-up entirely during normal local dev.
- **Static JS cache-busting**: any `<script src="{{ url_for('statics',
  ...) }}">` should go through `static_version()` (see
  `src/resources.py`) to avoid a repeat of the stale-cache redirect-loop
  bug above.

## Dev fake-login (WebAuthn bypass for local testing)

Added because the in-memory-DB-reset problem above made iterating on
anything behind `check_login` tedious - every restart meant a fresh
WebAuthn registration ceremony just to get back in.

- `GET /users/dev-login` (`src/endpoints/users.py`) logs in as the admin
  user directly, setting the exact same session shape a real passkey login
  does - no WebAuthn ceremony at all.
- Gated by `Settings.dev_fake_login_allowed` (`src/settings.py`), which is
  true only when **both** `DEV_FAKE_LOGIN_ENABLED=True` **and**
  `RELEASE_ENV` is in an explicit allowlist (`test`/`dev`/`local`/
  `development`). This is deliberately an allowlist, not a denylist -
  an unset or mistyped `RELEASE_ENV` (e.g. a typo like "produciton") fails
  closed rather than accidentally exposing the bypass. When not allowed,
  the route returns a plain 404 (not a 403) so its existence isn't
  discoverable outside dev.
- Visible in two places whenever it's active, so it's never silently on:
  a "Dev Login (fake)" button on `/users/login`, and a persistent yellow
  banner across every page in `base_app.html`.
- **To go back to exercising the real passkey flow** ("more formal
  testing"), set `DEV_FAKE_LOGIN_ENABLED=False` in `.env` and restart -
  the button/banner disappear and `/users/dev-login` 404s.
- Verified: returns 404 (well, a 303 redirect to `/error/404` - see the
  global exception handler's error-page behavior) under a
  `RELEASE_ENV=prd`-like config regardless of the enabled flag, and
  correctly logs in under the normal dev `.env`.

No changes have been pushed to a real server; everything above is
local/in-process verification against `DB_DRIVER=memory`.

## Test suite (started 2026-09-08, in progress)

Went from 6 tests / 53% coverage to 63 tests / 68% coverage in this pass,
then to 73% (still 63 tests) after removing confirmed-dead code from
`notes_metrics.py`, then to 82 tests / 76% after covering
`note_import.py`'s "simple" CSV format path, then to 86 tests / 77% after
closing the remaining easy `notes.py` gaps (edit-form GET, pagination
`tags=`/date-range filters), then to 104 tests / 79% after directly
unit-testing `db_tables.py`'s ORM event listeners and encryption error
paths, then to **116 tests / 80%** after closing the auth/bootstrap gaps a
PR-readiness check flagged (2026-09-12) - see below. Files:
`tests/conftest.py`, `tests/test_db_guards.py`, `tests/test_notes.py`,
`tests/test_weblinks.py`, `tests/test_users.py`, `tests/test_about.py`,
`tests/test_note_import.py`, `tests/test_db_tables.py`.

**Auth/bootstrap gaps closed (2026-09-12)**: `src/main.py`/`src/app_routes.py`
are now both 100% - added a test for the logged-in `/` redirect to `/notes`,
and a test that registers a throwaway route to feed the global exception
handler a valid-but-unregistered HTTP status code (419, with `detail` set
explicitly so `HTTPException.__init__` itself doesn't reject it first),
confirming the `error_code not in ALL_HTTP_CODES` fallback actually lands
on `/error/500`. `users.py` went 69% -> 91% (`tests/test_users.py`): the
`registration_authorized()` closed-registration guard now has a case for
"a credential already exists" (monkeypatched `registration_open`) and "the
bootstrap token setting itself isn't configured" (`settings.
registration_bootstrap_token = None`), not just "wrong token supplied";
`_get_admin_user()`'s failure path (lookup empty *and* the provisioning
insert fails) is covered by monkeypatching `db_ops.read_one_record`/
`execute_one` and hitting `/users/dev-login`; `login_verify()` now covers
the `InvalidAuthenticationResponse` branch and the "verified signature but
wrong/unknown user" branch (both via a `SimpleNamespace` fake stored
credential + monkeypatched `webauthn.verify_authentication_response`, no
real crypto needed); `register_verify()` now covers the missing-token,
malformed-body, `InvalidRegistrationResponse`, and failed-insert branches
the same way. Remaining `users.py` gaps (116, 218-236, 327-328) are all the
real WebAuthn success paths - see "Next steps" below, unchanged.

**Links (`web_links.py`/`link_import.py`/`link_preview.py`) is being
rewritten entirely** (per direct instruction, 2026-09-12) - do not invest
further test-writing effort there until the rewrite lands. Existing
weblinks tests stay as-is (they still pass and don't hurt anything) but
are not a basis to build on.

**Safety guarantee** (`tests/conftest.py`): forces `DB_DRIVER=memory`,
`RELEASE_ENV=test`, `DEV_FAKE_LOGIN_ENABLED=True`, `HTTPS_ONLY=False` via
`os.environ` *before* `src.main` is ever imported, plus an `assert` right
after that fails the whole session immediately if the driver isn't the
in-memory one. This must never be loosened - tests must never be able to
reach a real/shared database, regardless of what a developer's own `.env`
has configured. `HTTPS_ONLY=False` is required too, not just DB safety:
`TestClient` talks over plain `http://testserver`, and a `Secure`-flagged
session cookie silently never gets resent on the next request in the same
test, which looks like a broken login rather than a config issue.

Also fixed along the way: `.pre-commit-config.yaml` had `black` pinned to
`language_version: python3.12`, which isn't installed in this container
(only 3.11 and 3.14 are) - `make test` failed at the `pre-commit run -a`
step before any test ever ran. Now pinned to `python3.14` per direct
instruction ("we will be 3.14").

Each `client`/`logged_in_client` use gets a **fresh** in-memory DB (demo
data reseeded from scratch) - shutdown disposes the engine, which drops
the shared-cache `:memory:` SQLite once the last connection closes. Tests
must assert on specific content, never on exact row counts or an empty
table.

### What's covered now
- `db_guards.py` - 100%, pure unit tests.
- `db_tables.py` - **98%** (`tests/test_db_tables.py`, all direct unit
  tests, no DB/HTTP): `to_dict()` on every model that wasn't already
  covered elsewhere, `WebLinks.is_youtube`/`update_ai_fix`, and - most
  valuably - the ORM event listeners (`before_insert_listener`/
  `before_update_listener` for `WebLinks`, `note_on_change` for `Notes`)
  called directly with a constructed instance, since they only fire via a
  real `session.add()`+flush and nothing in this codebase still does that
  (everything writes through `db_ops.execute_one()` now - see the
  create_one -> execute_one migration notes). Kept rather than deleted
  like the `notes_metrics.py` dead code, since they're a legitimate safety
  net for any future ORM-session code, not something with literally no
  path to ever run. Also covers the `Notes.note`/`.summary` property
  getters'/setters' `DecryptionError`/`EncryptionError` catch branches
  (garbage Fernet bytes on read; a non-str value - `encrypt_text()` calls
  `.encode()` on it - on write). Only the two postgres-only branches (the
  driver `if`/`elif`/`else` at module level, and the postgres-only
  `Index`/schema `__table_args__` on `Notes`) are uncovered - genuinely
  unreachable under `DB_DRIVER=memory`, not worth forcing.
- `about.py` - 98%.
- `notes.py` - 85% (full CRUD including the edit-form GET, list/
  pagination - mood/search_term/tags/date-range filters all covered -
  tags/issues/bulk-import/ai-fix/ai-resubmit routes). Remaining gaps are
  mostly `is_db_error()` error branches (hard to force without breaking
  the DB) and a few early-return guards already covered by `check_login`
  upstream.
- `web_links.py` - 82% (CRUD, bulk import, pagination, comment edit -
  `ai.get_url_summary`/`get_url_title`/`get_html_title`,
  `link_preview.capture_full_page_screenshot`/`url_status` are all
  monkeypatched in `test_weblinks.py`'s `mock_weblink_externals` fixture,
  since none of those are installed/reachable in this environment and a
  real outbound request must never happen in a test).
- `users.py` - 91% (dev-login, logout, registration_open/authorized +
  bootstrap-token gating - including the "credential already exists" and
  "token not configured" denial branches, not just "wrong token" -
  login/register *options*, `_get_admin_user()`'s provisioning-failure
  path, and every verify-endpoint failure path that doesn't need real
  WebAuthn crypto - missing challenge, malformed body, unknown credential,
  failed signature verification, wrong/unknown user, invalid registration
  response, failed credential insert (the last several via monkeypatched
  `webauthn.verify_*_response`/`db_ops` calls, not real crypto). **Not
  covered**: the actual `login_verify`/`register_verify` *success* path -
  that needs a real or virtual authenticator to produce a valid signed
  attestation/assertion, which is out of scope for now.
- `link_import.py` - 94% via the weblinks bulk-import test (Links is
  being rewritten - see note above, not a priority to extend further).
- `note_import.py` - **99%** (`tests/test_note_import.py`): both CSV
  formats end-to-end via `/notes/bulk`, plus every pure helper
  (`detect_csv_format`, `parse_tags_field`, `parse_date`,
  `validate_csv_headers`) tested directly with no DB/HTTP needed.
  `ai.get_analysis` is monkeypatched for the "simple" format's
  AI-processing branch (`process_ai`/`process_note`), covering: the
  success path (summary/mood_analysis get real values, `ai_fix` clears),
  the mood-fallback-to-AI path (an invalid CSV mood value defers to the
  AI-derived mood), and the exception path (AI failure leaves the note's
  "processing" placeholder and `ai_fix=True` untouched, since
  `process_note`'s `except` only logs). One line left uncovered (a
  defensive `final_mood = "neutral"` fallback for a malformed/unrecognized
  AI-returned mood) - not worth chasing.

### Found and fixed while writing these tests (not the point of the
session, but worth recording)
- `HTTPS_ONLY=True` in the local `.env` was silently breaking every
  login-gated test until `conftest.py` forced it off (see above) - same
  root cause as the documented passkey-over-plain-HTTP gotcha.
- Any raised `HTTPException` in this app becomes a redirect (303 to
  `/error/{code}`, or straight to `/users/login` for 401 specifically) for
  a normal request - only `Accept: application/json` gets the raw status
  code + JSON body back. Every WebAuthn options/verify test now sends that
  header (`JSON_HEADERS` in `test_users.py`), matching what the real
  frontend JS (`static/js/webauthn.js`) actually sends.

### Dead code in `notes_metrics.py` - found, confirmed, removed (2026-09-12)
Re-verified with a repo-wide grep (all file types, not just `src/`) before
touching anything: `get_metrics`, `get_ai_fix_count`, `get_note_counts`
(the module function, not the same-named route in `notes.py`),
`mood_metrics`, `get_total_unique_tag_count`, `get_tag_count`,
`get_note_count_by_year`, `get_note_count_by_month`,
`get_note_count_by_week`, `mood_by_month`, `mood_trend_by_mean_month`,
`mood_analysis_trend_by_mean_month`, `mood_trend_by_median_month`,
`mood_trend_by_rolling_mean_month`, and `all_note_metrics` had zero
callers anywhere - not in `src/`, not in templates (the dashboard
template references match `_compute_metrics_bundle()`'s output dict
*keys*, which happen to share names with some of these functions - not
actual calls to them), no `__all__` export, no dynamic dispatch. All of
it predates `_compute_metrics_bundle()`, the single function that
actually computes every one of these stats today in one pass. Deleted all
15, plus the now-unused `tqdm` import. `notes_metrics.py` went from 359
statements/55% covered to 185/98% covered; overall project coverage went
68% -> 73% with zero new tests written. `make test` still passes clean.

### Next steps, in rough priority order
1. `youtube_helper.py` (31%) and `ai.py` (24%) - both need their own
   dedicated mocking strategy (YouTube oEmbed/transcript calls, OpenAI
   client) to test meaningfully; lower priority since they're thin
   wrappers around third-party calls rather than app logic.
2. `users.py` (91%) - only the real WebAuthn success path (`login_verify`/
   `register_verify` with a valid signed assertion) is left; needs a
   virtual authenticator; most expensive gap to close, highest confidence
   payoff.
3. Remaining `notes.py` gaps (85%) are mostly `is_db_error()` error
   branches - low value relative to effort (forcing a real DB failure in
   an in-memory SQLite test is awkward); not a priority.
4. Remaining `db_tables.py`/`web_links.py`/`about.py` gaps are all either
   postgres-only branches (unreachable under `DB_DRIVER=memory`) or
   `is_db_error()` error branches - same low-value/high-effort tradeoff
   as `notes.py` above.
5. **Skip for now**: anything under Links (`web_links.py`, `link_import.py`,
   `link_preview.py`) - that whole feature is being rewritten, so more
   tests there would just be thrown away.
6. Nothing in `main.py`/`app_routes.py`/`resources.py`/`settings.py` is a
   priority - all already 96%+.

Run `PYTHONPATH=. pytest tests/ --cov=src --cov-report=term-missing` to
see the current per-file gap list before picking up any of the above.
