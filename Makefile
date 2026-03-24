.PHONY: help dev prod build stop restart logs shell dbshell migrate makemigrations \
       seed static test messages compilemessages clean \
       local-install local-run local-migrate local-seed local-test \
       local-provision-clients provision-clients \
       lint format precommit-install \
       selfhost selfhost-logs selfhost-stop

DC       = docker compose
DC_OVER  = $(if $(wildcard docker-compose.override.yml),-f docker-compose.override.yml)
DC_DEV   = $(DC) -f docker-compose.yml -f docker-compose.dev.yml $(DC_OVER)
DC_PROD  = $(DC) -f docker-compose.yml -f docker-compose.prod.yml $(DC_OVER)
DC_SELF  = $(DC) -f docker-compose.selfhosted.yml $(DC_OVER)
DC_EXEC  = $(DC_DEV) exec billing-web uv run
DC_RUN   = $(DC_DEV) run --rm billing-web

DJANGO_LOCAL = DJANGO_SETTINGS_MODULE=config.settings.dev

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------- Environment ----------

env: ## Copy .env.example to .env (won't overwrite)
	cp -n .env.example .env

# ---------- Dev (Docker) ----------

dev: env ## Start dev stack (runserver + hot-reload)
	$(DC_DEV) up --build -d

dev-up: env ## Start dev stack in foreground (with logs)
	$(DC_DEV) up --build

# ---------- Prod (Docker) ----------

prod: ## Start production stack (gunicorn + WhiteNoise)
	$(DC_PROD) up --build -d

prod-logs: ## Tail production logs
	$(DC_PROD) logs -f

prod-stop: ## Stop production stack
	$(DC_PROD) down

# ---------- Local (sans Docker, SQLite) ----------

local-install: ## Install dependencies locally (uv)
	uv sync

local-run: ## Start dev server locally (SQLite)
	$(DJANGO_LOCAL) uv run python manage.py runserver

local-migrate: ## Run migrations locally (SQLite)
	$(DJANGO_LOCAL) uv run python manage.py migrate

local-seed: ## Seed demo data locally
	$(DJANGO_LOCAL) uv run python manage.py seed_demo

local-test: ## Run tests locally
	$(DJANGO_LOCAL) uv run pytest

local-coverage: ## Run tests with coverage locally
	$(DJANGO_LOCAL) uv run pytest --cov --cov-report=term-missing --cov-report=html

local-provision-clients: ## Provision FactPulse clients locally
	$(DJANGO_LOCAL) uv run python manage.py provision_factpulse_clients

# ---------- Common ----------

build: ## Rebuild images without starting
	$(DC_DEV) build

stop: ## Stop all containers
	$(DC_DEV) down

restart: ## Restart all containers
	$(DC_DEV) restart

logs: ## Tail logs (all services)
	$(DC_DEV) logs -f

logs-web: ## Tail web logs only
	$(DC_DEV) logs -f billing-web

# ---------- Django ----------

shell: ## Django shell
	$(DC_EXEC) python manage.py shell

dbshell: ## Database shell (psql)
	$(DC_EXEC) python manage.py dbshell

migrate: ## Run migrations
	$(DC_EXEC) python manage.py migrate

makemigrations: ## Generate migrations
	$(DC_EXEC) python manage.py makemigrations

seed: ## Seed demo data
	$(DC_EXEC) python manage.py seed_demo

provision-clients: ## Provision FactPulse clients
	$(DC_EXEC) python manage.py provision_factpulse_clients

static: ## Collect static files
	$(DC_EXEC) python manage.py collectstatic --noinput

createsuperuser: ## Create a superuser
	$(DC_EXEC) python manage.py createsuperuser

manage: ## Run any manage.py command – usage: make manage CMD="check"
	$(DC_EXEC) python manage.py $(CMD)

# ---------- Tests ----------

test: ## Run tests
	$(DC_EXEC) pytest

test-v: ## Run tests (verbose)
	$(DC_EXEC) pytest -v

coverage: ## Run tests with coverage (Docker)
	$(DC_EXEC) pytest --cov --cov-report=term-missing --cov-report=html

# ---------- i18n ----------

messages: ## Generate translation files (fr + en)
	$(DC_EXEC) python manage.py makemessages -l fr -l en

compilemessages: ## Compile translation files
	$(DC_EXEC) python manage.py compilemessages

# ---------- Lint ----------

lint: ## Run ruff linter + formatter check
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-fix lint + format
	uv run ruff check --fix .
	uv run ruff format .

precommit-install: ## Install pre-commit hooks
	uv run pre-commit install

# ---------- Dependencies ----------

lock: ## Update uv.lock (resolve latest compatible versions)
	uv lock --upgrade

# ---------- Self-hosted (Caddy + SSL) ----------

selfhost: env ## Start self-hosted stack (Caddy + SSL)
	$(DC_SELF) up --build -d

selfhost-logs: ## Tail self-hosted stack logs
	$(DC_SELF) logs -f

selfhost-stop: ## Stop self-hosted stack
	$(DC_SELF) down

# ---------- Cleanup ----------

clean: ## Stop containers and remove volumes
	$(DC_DEV) down -v
