# Use an official Python runtime as a parent image
FROM python:3.14-slim-bookworm

# Set the working directory in the container to /app
WORKDIR /app

# Add metadata to the image
LABEL maintainer='Mike Ryan'
LABEL github_repo='https://github.com/devsetgo/mrie'

# Install dependencies before copying the source, so a code-only change
# reuses this (slow) layer from the build cache instead of reinstalling
# every package.
COPY requirements/prd.txt /app/requirements/prd.txt
RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir -r requirements/prd.txt

# Copy the current directory contents into the container at /app
COPY . /app

# Create a user and set file permissions. sqlite_db/ is created here (and
# owned by that user) so the named volume docker-compose.yml mounts over it
# inherits that ownership on first use - otherwise Docker creates the volume
# root-owned and the app can't create its database file.
RUN useradd -m -r -u 10001 mrieUser \
    && mkdir -p /app/sqlite_db /app/logs \
    && chown -R mrieUser /app

# Switch to the new user
USER 10001

# uvicorn reads both of these itself when the matching CLI flag is absent.
# WEB_CONCURRENCY: worker processes. They share the one SQLite file: WAL mode
# lets reads run in parallel across workers while writes take turns (see
# db_init.py), so 2 is plenty for a single-user site; more workers only add
# contention for the write lock. FORWARDED_ALLOW_IPS: which peers may set
# X-Forwarded-Proto/For. Behind a reverse proxy (Traefik, see
# docker-compose.yml) it must include the proxy's address, otherwise the app
# sees every request as plain http and url_for() emits http:// asset URLs
# that browsers block as mixed content on the https site.
ENV WEB_CONCURRENCY=2 \
    FORWARDED_ALLOW_IPS=127.0.0.1 \
    PYTHONUNBUFFERED=1

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Python rather than curl - the slim base image doesn't ship curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health/status', timeout=4)"]

# Apply any pending migrations, then start the ASGI server. Safe to run on
# every start: there is only ever one container, so nothing else can be
# migrating the same file at once, and `upgrade head` is a no-op when the
# schema is current. `exec` hands PID 1 to uvicorn so `docker stop`'s SIGTERM
# reaches it directly and the shutdown hook (PRAGMA optimize, clean WAL
# checkpoint on close) gets to run.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn src.main:app --host 0.0.0.0 --port 5000 --proxy-headers"]
