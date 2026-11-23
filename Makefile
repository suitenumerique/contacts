# Note to developers:
#
# While editing this file, please respect the following statements:
#
# 1. Every variable should be defined in the ad hoc VARIABLES section with a
#    relevant subsection
# 2. Every new rule should be defined in the ad hoc RULES section with a
#    relevant subsection depending on the targeted service
# 3. Rules should be sorted alphabetically within their section
# 4. When a rule has multiple dependencies, you should:
#    - duplicate the rule name to add the help string (if required)
#    - write one dependency per line to increase readability and diffs
# 5. .PHONY rule statement should be written after the corresponding rule
# ==============================================================================
# VARIABLES

BOLD := \033[1m
RESET := \033[0m
GREEN := \033[1;32m
BLUE := \033[1;34m

# -- Docker
# Get the current user ID to use for docker run and docker exec commands
DOCKER_UID          = $(shell id -u)
DOCKER_GID          = $(shell id -g)
DOCKER_USER         = $(DOCKER_UID):$(DOCKER_GID)
COMPOSE             = DOCKER_USER=$(DOCKER_USER) DOCKER_UID=$(DOCKER_UID) docker compose
COMPOSE_EXEC        = $(COMPOSE) exec
COMPOSE_EXEC_APP    = $(COMPOSE_EXEC) backend-dev
COMPOSE_RUN         = $(COMPOSE) run --rm
COMPOSE_RUN_APP     = $(COMPOSE_RUN) backend-dev
COMPOSE_RUN_APP_NO_DEPS = $(COMPOSE_RUN) --no-deps backend-dev

# -- Backend
MANAGE              = $(COMPOSE_RUN_APP) python manage.py
MANAGE_EXEC         = $(COMPOSE_EXEC_APP) python manage.py
UV_IMAGE            = ghcr.io/astral-sh/uv:0.12.13-python3.14-alpine@sha256:eb47c391d3a252d9d912270dd2b5e234af5493e92039fedb483f1dd1cb5659ce

# ==============================================================================
# RULES

default: help

data/media:
	@mkdir -p data/media

data/static:
	@mkdir -p data/static

# -- Project

create-env-files: ## Create empty .local env files for local development
create-env-files: \
	env.d/development/postgresql.local \
	env.d/development/keycloak.local \
	env.d/development/backend.local \
	env.d/development/frontend.local
.PHONY: create-env-files

env.d/development/%.local:
	@echo "# Local development overrides for $(notdir $*)" > $@
	@echo "# Add your local-specific environment variables below:" >> $@
	@echo "# Example: DJANGO_DEBUG=True" >> $@
	@echo "" >> $@

create-docker-network: ## create the docker network if it doesn't exist
	@docker network create lasuite-network || true
.PHONY: create-docker-network

bootstrap: ## Prepare the project for local development
bootstrap: \
	data/media \
	data/static \
	create-env-files \
	build \
	create-docker-network \
	migrate \
	start
.PHONY: bootstrap

update: ## Update the project with latest changes
	@$(MAKE) data/media
	@$(MAKE) data/static
	@$(MAKE) create-env-files
	@$(MAKE) build
	@$(MAKE) migrate
	@$(MAKE) install-frozen-front
.PHONY: update

# -- Docker/compose

build: cache ?=  # --no-cache
build: ## build the project containers
	@$(COMPOSE) build $(cache)
.PHONY: build

down: ## stop and remove containers and networks (the next start gets an empty database)
	@$(COMPOSE) down
.PHONY: down

logs: ## display all services logs (follow mode)
	@$(COMPOSE) logs -f
.PHONY: logs

start: ## start all development services
	@$(COMPOSE) up --force-recreate -d worker-dev frontend-dev backend-dev keycloak
.PHONY: start

start-back: ## start backend services only (for local frontend development)
	@$(COMPOSE) up --force-recreate -d worker-dev backend-dev keycloak
.PHONY: start-back

status: ## an alias for "docker compose ps"
	@$(COMPOSE) ps
.PHONY: status

stop: ## stop all development services
	@$(COMPOSE) stop
.PHONY: stop

restart: ## restart all development services
restart: \
	stop \
	start
.PHONY: restart

# -- Linters

lint: ## run all linters
lint: \
	lint-back \
	lint-front
.PHONY: lint

lint-back: ## run back-end linters (with auto-fix)
lint-back: \
	format-back \
	check-back \
	analyze-back
.PHONY: lint-back

format-back: ## format back-end python sources with ruff
	@$(COMPOSE_RUN_APP_NO_DEPS) ruff format .
.PHONY: format-back

check-back: ## check back-end python sources with ruff
	@$(COMPOSE_RUN_APP_NO_DEPS) ruff check . --fix
.PHONY: check-back

analyze-back: ## lint all back-end python sources with pylint
	@$(COMPOSE_RUN_APP_NO_DEPS) pylint .
.PHONY: analyze-back

deps-audit-back: ## audit back-end dependencies for known vulnerabilities
	@$(COMPOSE_RUN) --no-deps -e XDG_CACHE_HOME=/tmp/.cache backend-dev \
		pip-audit --progress-spinner off
.PHONY: deps-audit-back

deps-outdated-back: ## list back-end dependencies newer than the pinned ones
	@$(COMPOSE_RUN_APP_NO_DEPS) uv pip list --outdated
.PHONY: deps-outdated-back

lint-check-back: ## check back-end sources WITHOUT rewriting them (what CI runs)
	@$(COMPOSE_RUN_APP_NO_DEPS) ruff format . --diff
	@$(COMPOSE_RUN_APP_NO_DEPS) ruff check .
	@$(COMPOSE_RUN_APP_NO_DEPS) pylint .
.PHONY: lint-check-back

lint-front: ## format then run the frontend linter (oxfmt + oxlint)
	@$(COMPOSE) run --rm frontend-dev sh -c "pnpm run format && pnpm run lint"
.PHONY: lint-front

format-front: ## format front-end sources with oxfmt
	@$(COMPOSE) run --rm frontend-dev sh -c "pnpm run format"
.PHONY: format-front

typecheck-front: ## run the frontend type checker
	@$(COMPOSE) run --rm frontend-dev sh -c "pnpm run ts:check"
.PHONY: typecheck-front

analyze-front: ## analyze frontend bundle sizes (per-chunk + per-package breakdown)
	@$(COMPOSE) run --rm frontend-dev sh -c "pnpm run analyze"
.PHONY: analyze-front

# -- Tests

test: ## run all tests
test: \
	test-back-parallel \
	test-front
.PHONY: test

test-back: ## run back-end tests
	@echo "$(BOLD)Running tests...$(RESET)"
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest $${args:-${1}}
.PHONY: test-back

test-back-parallel: ## run all back-end tests in parallel
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	bin/pytest -n auto $${args:-${1}}
.PHONY: test-back-parallel

build-back-distroless: ## build the distroless backend image
	@docker build --target backend-distroless -t contacts-backend-distroless src/backend/
.PHONY: build-back-distroless

test-back-distroless: ## build and smoke-test the distroless backend image (no database)
test-back-distroless: build-back-distroless
	@bin/smoke-test-back-distroless contacts-backend-distroless
.PHONY: test-back-distroless

test-front: ## run the frontend tests
	@args="$(filter-out $@,$(MAKECMDGOALS))" && \
	$(COMPOSE) run --rm frontend-dev sh -c "pnpm test -- $${args:-${1}}"
.PHONY: test-front

build-front-production: ## build the frontend production image (Caddy + static bundle)
	@docker build --target frontend-production -t contacts-frontend-production src/frontend/
.PHONY: build-front-production

test-front-production: ## build and smoke-test the frontend production image (proxy, admin allowlist)
test-front-production: build-front-production
	@bin/smoke-test-front contacts-frontend-production
.PHONY: test-front-production

# -- Backend

makemigrations: ## run django makemigrations
	@echo "$(BOLD)Running makemigrations$(RESET)"
	@$(COMPOSE) up -d postgresql
	@$(MANAGE) makemigrations
.PHONY: makemigrations

migrate: ## run django migrations
	@echo "$(BOLD)Running migrations$(RESET)"
	@$(COMPOSE) up -d postgresql
	@$(MANAGE) migrate
.PHONY: migrate

migrations-check: ## fail if a model change has no migration (what CI runs)
	@echo "$(BOLD)Checking for missing migrations$(RESET)"
	@$(COMPOSE) up -d postgresql
	@$(MANAGE) makemigrations --check --dry-run
.PHONY: migrations-check

showmigrations: ## list migrations and whether they are applied
	@$(MANAGE) showmigrations
.PHONY: showmigrations

superuser: ## Create an admin superuser with password "admin"
	@echo "$(BOLD)Creating a Django superuser$(RESET)"
	@$(MANAGE) createsuperuser --email admin@admin.local --password admin
.PHONY: superuser

shell-back: ## open a shell in the backend container
	@$(COMPOSE) run --rm --build backend-dev /bin/sh
.PHONY: shell-back

exec-back: ## open a shell in the running backend-dev container
	@$(COMPOSE) exec backend-dev /bin/sh
.PHONY: exec-back

shell-back-django: ## connect to django shell
	@$(MANAGE) shell
.PHONY: shell-back-django

back-lock: ## regenerate the uv.lock file
	@echo "$(BOLD)Regenerating uv.lock$(RESET)"
	@docker run --rm -u $(DOCKER_USER) -e UV_CACHE_DIR=/tmp/uv-cache \
		-v $(PWD)/src/backend:/app -w /app $(UV_IMAGE) uv lock
.PHONY: back-lock

clean-back: ## reset the backend .venv volumes (run after a dep or Python bump)
# The venv lives in an anonymous volume, so a rebuilt image keeps being
# shadowed by the previous one until the volume goes. Symptom: the app
# dies on ModuleNotFoundError for a package that is installed.
	@$(COMPOSE) rm -sfv backend-dev worker-dev backend-test
.PHONY: clean-back

# -- Database

shell-db: ## connect to database shell
	@$(COMPOSE) exec backend-dev python manage.py dbshell
.PHONY: shell-db

reset-db: FLUSH_ARGS ?=
reset-db: build ## flush database and re-run migrations
	@echo "$(BOLD)Flush database$(RESET)"
	@$(MANAGE) flush $(FLUSH_ARGS)
	@$(MAKE) migrate
.PHONY: reset-db

demo: ## flush db then create a demo
# Wiping is the whole point of this target, so it does not stop to ask:
# without --no-input the flush prompt makes `make demo` unusable from a
# script or a CI job (it reads EOF and dies).
	@$(MAKE) reset-db FLUSH_ARGS=--no-input
	@$(MANAGE) create_demo
.PHONY: demo

# -- Frontend

install-front: ## install frontend deps and (re)generate pnpm-lock.yaml
	@$(COMPOSE) run --rm -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 node \
		sh -c "cd /app/src/frontend && corepack pnpm install"
.PHONY: install-front

install-frozen-front: ## install frontend deps strictly from the committed lockfile
	@$(COMPOSE) run --rm -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 node \
		sh -c "cd /app/src/frontend && corepack pnpm install --frozen-lockfile"
.PHONY: install-frozen-front

update-api-front: ## regenerate the orval client from core/api/openapi.json
# Runs in the `node` service, not `frontend-dev`: orval reads the backend
# schema through a relative path, and only `node` mounts the whole repo.
# Stops the dev server first: orval empties the output dir before writing,
# and vite's watcher racing that produces sporadic EACCES failures.
	@$(COMPOSE) stop frontend-dev >/dev/null 2>&1 || true
	@$(COMPOSE) run --rm -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 node \
		sh -c "cd /app/src/frontend && corepack pnpm run api:update"
	@$(COMPOSE) start frontend-dev >/dev/null 2>&1 || true
.PHONY: update-api-front

shell-front: ## open a shell in the frontend container
	@$(COMPOSE) run --rm frontend-dev /bin/sh
.PHONY: shell-front

clean-front: ## reset the frontend node_modules volumes (run after dep changes)
	@$(COMPOSE) rm -sfv frontend-dev
.PHONY: clean-front

# -- Release

release: ## create a new release (interactive: asks for version and kind)
	bin/release.py
.PHONY: release

# -- Misc

clean: ## restore repository state as it was freshly cloned
	git clean -idx
.PHONY: clean

clean-media: ## remove all media files
	rm -rf data/media/*
.PHONY: clean-media

help:
	@echo "$(BOLD)contacts Makefile"
	@echo "Please use 'make $(BOLD)target$(RESET)' where $(BOLD)target$(RESET) is one of:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-30s$(RESET) %s\n", $$1, $$2}'
.PHONY: help
