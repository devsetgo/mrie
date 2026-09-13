# Shell
SHELL := /bin/bash
# Variables
application_name = mikeryanie
__version__ = 2024-11-17-001

PYTHON = python3
PIP = $(PYTHON) -m pip
PYTEST = $(PYTHON) -m pytest

SERVICE_PATH = src
TESTS_PATH = tests
LOG_PATH = log

DEV_SERVER = uvicorn ${SERVICE_PATH}.main:app
PROD_SERVER = uvicorn ${SERVICE_PATH}.main:app
PORT = 5000
WORKERS = 4
THREADS = 2

VENV_PATH = _venv
REQUIREMENTS_PATH = requirements.txt
DEV_REQUIREMENTS_PATH = requirements/dev.txt

TIMESTAMP := $(shell date +'%y-%m-%d-%H%M')
LOG_LEVEL := $(shell grep LOGGING_LEVEL .env | cut -d '=' -f2 | tr '[:upper:]' '[:lower:]')

.PHONY: autoflake black bump cache cleanup help install install-dev isort kill ruff run-dev run-dev-workers run-prd reset-dev-workers-db test

# Necessary to develop, lint, test, and run the app day to day.
# Everything below is commented out while evaluating a possible move to
# Robyn / granian - uncomment as needed for local feature work.

autoflake:  # Remove unused imports and variables
	autoflake --in-place --remove-all-unused-imports -r $(SERVICE_PATH)

black:  # Format code using black
	black $(SERVICE_PATH)
	black $(TESTS_PATH)

bump:  # Bump the version number
	bumpcalver --build

cache:  # Clean pycache
	find . -name '__pycache__' -exec rm -rf {} +
	find . -name '.pytest_cache' -exec rm -rf {} +

cleanup: autoflake ruff isort  # Run isort, ruff, and autoflake

help:  # Display available targets
	@awk 'BEGIN {FS = ":  # "} /^[a-zA-Z_-]+:  # / {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  # Install required dependencies
	$(PIP) install -r $(REQUIREMENTS_PATH)

install-dev:  # Install development dependencies
	$(PIP) install -r $(DEV_REQUIREMENTS_PATH)

isort:  # Sort imports using isort
	isort $(SERVICE_PATH)
	isort $(TESTS_PATH)

kill: ## Kill the server
	kill -9 $(lsof -t -i:5000)

ruff: ## Format Python code with Ruff
	ruff check --fix --exit-non-zero-on-fix --show-fixes $(SERVICE_PATH)
	ruff check --fix --exit-non-zero-on-fix --show-fixes $(TESTS_PATH)

run-dev:  # Run the FastAPI application in development mode with hot-reloading
	uvicorn ${SERVICE_PATH}.main:app --port ${PORT} --reload --log-level ${LOG_LEVEL}

run-prd:  # Run the FastAPI application in production mode
	uvicorn ${SERVICE_PATH}.main:app --port 5000 --workers 4 --log-level debug

run-dev-workers:  # Run with 6 workers against a real file-based SQLite DB, for concurrency/load testing (e.g. bulk note import) - no --reload, that's single-process only
	mkdir -p sqlite_db
	DB_DRIVER=sqlite DB_NAME=loadtest uvicorn ${SERVICE_PATH}.main:app --port ${PORT} --workers 6 --log-level ${LOG_LEVEL}

reset-dev-workers-db:  # Delete the SQLite file run-dev-workers created, for a clean re-test
	rm -f sqlite_db/loadtest.db

test:  # Run tests and generate coverage report
	pre-commit run -a
	PYTHONPATH=. pytest
	sed -i 's|<source>/workspaces/mrie</source>|<source>/github/workspace/mrie</source>|' /workspaces/mrie/coverage.xml
	genbadge coverage -i /workspaces/mrie/coverage.xml

tests: test  # Alias for test


# alembic-init: # Initialize Alembic — no migrations set up yet, alembic isn't even installed
# 	alembic init alembic

# alembic-migrate: # Migrate database using Alembic — no migrations set up yet
# 	alembic upgrade head

# alembic-rev: # Create a new revision file — no migrations set up yet
# 	cp env-files/.env.test .env && \
# 	./scripts/env.sh && \
# 	export DATABASE_URL=`cat /tmp/db_url.txt` && \
# 	echo "In Makefile, DATABASE_URL is: $$DATABASE_URL"
# 	@read -p "Enter revision name: " name; \
# 	alembic revision --autogenerate -m "$$name"

# alembic-downgrade: # Downgrade database using Alembic — no migrations set up yet
# 	@read -p "Enter revision name: " name; \
# 	alembic downgrade $$name

# compile:  # Compile http_request.c into a shared library — http_request.c isn't in the repo
# 	gcc -shared -o http_request.so http_request.c -lcurl -fPIC

# docker-login:  # Login to docker hub
# 	docker login

# docker-run:  # Run docker container
# 	docker run -p 5000:5000 mikeryanie:$(__version__)

# docker-build:  # Build docker image
# 	docker build --no-cache -t $(application_name):$(__version__) .

# docker-push:  # Push beta test image to docker hub
# 	docker tag $(application_name):$(__version__) mikeryan56/$(application_name):$(__version__)
# 	docker push mikeryan56/$(application_name):$(__version__)

# docker-all: docker-build docker-push

# flake8:  # Run flake8 and output report — flake8 isn't installed; ruff replaces it
# 	flake8 --tee . > _flake8Report.txt

# pyright:  # Run pyright — pyright isn't installed
# 	pyright

# run-gdev:  # Run the FastAPI application in development mode with hot-reloading using granian — granian isn't installed, uvicorn is the server actually used
# 	granian --interface asgi ${SERVICE_PATH}.main:app --port ${PORT} --reload --log-level ${LOG_LEVEL}

# run-gprd:  # Run the FastAPI application in production mode using granian — granian isn't installed, uvicorn is the server actually used
# 	granian --interface asgi ${SERVICE_PATH}.main:app --port ${PORT} --workers ${WORKERS} --log-level ${LOG_LEVEL}
