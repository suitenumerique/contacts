# Django contacts

# ---- base image to inherit from ----
FROM python:3.14.10-alpine@sha256:5862d971d1f0ae26b258cefa4e893556b1d3aba81bd2ff30888704ad378bab67 AS base

# Upgrade system packages to install security updates
RUN apk update && \
  apk upgrade && \
  rm -rf /var/cache/apk/*

# ---- Back-end builder image ----
FROM base AS back-builder

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image.
ENV UV_PYTHON_DOWNLOADS=0

# install uv (same version as the Makefile's UV_IMAGE)
COPY --from=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /uvx /bin/

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY . /app
# The application runs as an unprivileged user: don't depend on the build
# host's file modes.
RUN chmod -R a+rX /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---- static link collector ----
FROM base AS link-collector
ARG CONTACTS_STATIC_ROOT=/data/static

RUN apk add rdfind

WORKDIR /app

# Copy the application from the builder
COPY --from=back-builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

# collectstatic
RUN DJANGO_CONFIGURATION=Build \
    python manage.py collectstatic --noinput

# Replace duplicated file by a symlink to decrease the overall size of the
# final image
RUN rdfind -makesymlinks true -followsymlinks true -makeresultsfile false ${CONTACTS_STATIC_ROOT}

# ---- Core application image ----
FROM base AS core

ENV PYTHONUNBUFFERED=1

# Give the "root" group the same permissions as the "root" user on /etc/passwd
# to allow a user belonging to the root group to add new users; typically the
# docker user (see entrypoint).
RUN chmod g=u /etc/passwd

# Copy the application from the builder
COPY --from=back-builder /app /app

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

# We wrap commands run in this container by the following entrypoint that
# creates a user on-the-fly with the container user ID (see USER) and root group
# ID.
ENTRYPOINT [ "/app/entrypoint" ]

# ---- Development image ----
FROM core AS backend-development

# Switch back to the root user to install development dependencies
USER root:root

# Install psql
RUN apk add postgresql-client

# Install uv for development dependencies
COPY --from=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /uvx /bin/

# Install development dependencies (ensure venv is created and used)
RUN uv sync --all-extras --locked

# Ensure venv is accessible and PATH is set
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

# Restore the un-privileged user running the application
ARG DOCKER_USER
USER ${DOCKER_USER}

# Target database host (e.g. database engine following docker compose services
# name) & port
ENV DB_HOST=postgresql \
    DB_PORT=5432

# Run django development server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---- Production image ----
FROM core AS backend-production

ARG CONTACTS_STATIC_ROOT=/data/static

# Un-privileged user running the application. Group 0 lets the entrypoint
# register it in /etc/passwd.
ARG DOCKER_USER=10000:0
USER ${DOCKER_USER}

# Copy statics
COPY --from=link-collector ${CONTACTS_STATIC_ROOT} ${CONTACTS_STATIC_ROOT}

# The default command runs gunicorn WSGI server in contacts' main module
CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "contacts.wsgi:application"]

# The probe sends the container IP as Host: Production.ALLOWED_HOSTS always
# holds it.
HEALTHCHECK --interval=30s --timeout=2s --start-period=30s \
  CMD ["python", "-c", "import socket; from urllib.request import Request, urlopen; urlopen(Request('http://localhost:8000/__heartbeat__/', headers={'Host': socket.gethostbyname(socket.gethostname())}), timeout=2)"]
