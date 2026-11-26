# Django contacts

# ---- base image to inherit from ----
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS base

# Upgrade system packages to install security updates. Python verifies TLS
# certificates against the system CA store.
RUN apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates && \
  rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv

WORKDIR /app

# ---- uv and Python ----
FROM base AS uv

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_PREFERENCE=only-managed
# uv installs a standalone Python. It links its own dependencies statically,
# so the distroless image needs no shared library other than the C runtime.
ENV UV_PYTHON_INSTALL_DIR=/opt/python
ENV UV_PROJECT_ENVIRONMENT=/venv

# uv knows the Python versions released before it. Upgrade it together with
# .python-version. Keep it the same as the Makefile's UV_IMAGE.
COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /uvx /bin/

RUN --mount=type=bind,source=.python-version,target=.python-version \
    uv python install

# ---- Back-end dependencies ----
FROM uv AS back-builder

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.python-version,target=.python-version \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev --exact

# ---- Back-end development dependencies ----
FROM back-builder AS back-builder-dev

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.python-version,target=.python-version \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --all-extras --exact

# ---- static link collector ----
FROM back-builder AS link-collector
ARG CONTACTS_STATIC_ROOT=/data/static

RUN apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends rdfind && \
  rm -rf /var/lib/apt/lists/*

COPY . /app

RUN DJANGO_CONFIGURATION=Build \
    python manage.py collectstatic --noinput

# Replace duplicated file by a symlink to decrease the overall size of the
# final image
RUN rdfind -makesymlinks true -followsymlinks true -makeresultsfile false ${CONTACTS_STATIC_ROOT}

# ---- Production Python ----
# Remove what the application does not use: pip, idle, tkinter, tcl/tk,
# headers and the test modules of the standard library. The virtual
# environment has its own site-packages, so the application does not read
# the site-packages of this Python.
FROM uv AS python-runtime

RUN PYDIR=$(dirname "$(dirname "$(uv python find --no-project)")") && \
  PYLIB=$(echo "$PYDIR"/lib/python3.*) && \
  rm -rf \
    "$PYDIR"/bin/idle* "$PYDIR"/bin/pip* "$PYDIR"/bin/pydoc* "$PYDIR"/bin/*-config \
    "$PYDIR"/include "$PYDIR"/share \
    "$PYDIR"/lib/pkgconfig "$PYDIR"/lib/itcl* "$PYDIR"/lib/libtcl* \
    "$PYDIR"/lib/tcl* "$PYDIR"/lib/tk* "$PYDIR"/lib/thread* \
    "$PYLIB"/idlelib "$PYLIB"/ensurepip "$PYLIB"/tkinter "$PYLIB"/turtledemo \
    "$PYLIB"/test "$PYLIB"/lib-dynload/_tkinter* "$PYLIB"/lib-dynload/_ctypes_test* \
    "$PYLIB"/lib-dynload/_test* "$PYLIB"/lib-dynload/xxlimited* \
    "$PYLIB"/site-packages/pip "$PYLIB"/site-packages/pip-*.dist-info \
    /opt/python/.gitignore /opt/python/.lock /opt/python/.temp

# ---- Production application source ----
FROM base AS app-prod

COPY . /app
RUN rm -rf \
    /app/core/tests \
    /app/carddav/tests \
    /app/core/factories.py \
    /app/Dockerfile \
    /app/.dockerignore \
    /app/.pylintrc \
    /app/uv.lock \
    /app/MANIFEST.in \
    /app/entrypoint
# The application runs as an unprivileged user: don't depend on the build
# host's file modes.
RUN chmod -R a+rX /app

# ---- Runtime with a shell ----
FROM base AS runtime-base

# Give the "root" group the same permissions as the "root" user on /etc/passwd
# to allow a user belonging to the root group to add new users; typically the
# docker user (see entrypoint).
RUN chmod g=u /etc/passwd

# We wrap commands run in this container by the following entrypoint that
# creates a user on-the-fly with the container user ID (see USER) and root group
# ID.
COPY --chmod=755 entrypoint /usr/local/bin/entrypoint
ENTRYPOINT [ "/usr/local/bin/entrypoint" ]

# ---- Development image ----
FROM runtime-base AS backend-development

RUN apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    postgresql-client && \
  rm -rf /var/lib/apt/lists/*

COPY --from=uv /bin/uv /bin/uvx /bin/
# The full Python, with pip and headers, for debugging.
COPY --from=uv /opt/python /opt/python
COPY --from=back-builder-dev /venv /venv

# The development compose services mount the sources on /app.

ARG DOCKER_USER
USER ${DOCKER_USER}

# Target database host (e.g. database engine following docker compose services
# name) & port
ENV DB_HOST=postgresql \
    DB_PORT=5432

# Run django development server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---- Production image with a shell ----
# The release workflow does not publish this stage.
FROM runtime-base AS backend-production

ARG CONTACTS_STATIC_ROOT=/data/static

COPY --from=python-runtime /opt/python /opt/python
COPY --from=back-builder /venv /venv
COPY --from=link-collector ${CONTACTS_STATIC_ROOT} ${CONTACTS_STATIC_ROOT}
COPY --from=app-prod /app /app

# Un-privileged user running the application. Group 0 lets the entrypoint
# register it in /etc/passwd.
ARG DOCKER_USER=10000:0
USER ${DOCKER_USER}

# The default command runs gunicorn WSGI server in contacts' main module
CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "contacts.wsgi:application"]

# The probe sends the container IP as Host: Production.ALLOWED_HOSTS always
# holds it.
HEALTHCHECK --interval=30s --timeout=2s --start-period=30s \
  CMD ["python", "-c", "import socket; from urllib.request import Request, urlopen; urlopen(Request('http://localhost:8000/__heartbeat__/', headers={'Host': socket.gethostbyname(socket.gethostname())}), timeout=2)"]

# ---- Distroless production image ----
# The release workflow publishes this stage. Keep it last: a build without
# --target produces the last stage.
# No shell and no package manager. The image has its own "nonroot" user, so
# it does not use the entrypoint.
# To debug, use "make shell-back-distroless". It adds the busybox shell of the
# debug-nonroot image to this image and starts the shell.
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:cb87acc574abe719b6e5c547384defa2f7b1f24af9605d3f618cf852495d8be2 AS backend-distroless

ARG CONTACTS_STATIC_ROOT=/data/static

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/venv/bin:$PATH"
ENV VIRTUAL_ENV=/venv

COPY --from=python-runtime /opt/python /opt/python
COPY --from=back-builder /venv /venv
COPY --from=link-collector ${CONTACTS_STATIC_ROOT} ${CONTACTS_STATIC_ROOT}
COPY --from=app-prod /app /app

WORKDIR /app

CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "contacts.wsgi:application"]

# The same probe as backend-production.
HEALTHCHECK --interval=30s --timeout=2s --start-period=30s \
  CMD ["python", "-c", "import socket; from urllib.request import Request, urlopen; urlopen(Request('http://localhost:8000/__heartbeat__/', headers={'Host': socket.gethostbyname(socket.gethostname())}), timeout=2)"]
