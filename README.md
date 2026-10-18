# mrie

Source for [mikeryan.ie](https://mikeryan.ie): a personal site with a public
About page, plus a private journal (Notes) and bookmark manager (Links)
behind passkey login. FastAPI, Jinja2 and htmx, with SQLite.

- **Develop:** open in the devcontainer, `cp .env.sample .env`, fill it in,
  then `make run-dev` (http://localhost:5000). `make test` runs the suite.
  See [CLAUDE.md](CLAUDE.md) for architecture and gotchas.
- **Release:** `make docker-push` builds and pushes
  `mikeryan56/mikeryanie` (version tag and `latest`).
- **Deploy:** copy [`deploy/`](deploy/) to the server and follow
  [deploy/README.md](deploy/README.md).
- **History and roadmap:** [PROJECT_STATUS.md](PROJECT_STATUS.md).
