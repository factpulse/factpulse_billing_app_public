# Contributing to FactPulse Billing App

Thank you for your interest in contributing! This guide will help you get started.

## Development Setup

### With Docker (recommended)

```bash
cp .env.example .env
make dev
```

This starts PostgreSQL, Redis, MinIO, and the Django dev server with hot-reload.

### Without Docker (SQLite)

Requires [uv](https://docs.astral.sh/uv/) (Python package manager).

```bash
make local-install    # Install dependencies
make local-migrate    # Run migrations (SQLite)
make local-run        # Start dev server on localhost:8000
```

### Seed demo data

```bash
make seed         # Docker
make local-seed   # Local
```

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting (replaces Black, isort, flake8).

```bash
make lint    # Check lint + formatting
make format  # Auto-fix lint + format
```

Configuration is in `pyproject.toml` under `[tool.ruff]`.

Key rules:
- Line length: 88 characters
- Double quotes
- isort-compatible import ordering (first-party: `apps`, `config`)

### Pre-commit hooks

Install pre-commit hooks to run checks automatically on each commit:

```bash
make precommit-install
```

Hooks include Ruff, djLint (Django templates), trailing whitespace, YAML/JSON/TOML checks, Bandit (security), and detect-secrets.

## Tests

We use [pytest](https://docs.pytest.org/) with [pytest-django](https://pytest-django.readthedocs.io/).

```bash
make test             # Docker
make local-test       # Local (SQLite)
make local-coverage   # Local with coverage report
```

All new features and bug fixes should include tests. Aim to maintain or increase coverage.

## Pull Request Guidelines

1. **Fork the repo** and create a feature branch from `main`.
2. **Write clear commit messages** describing what and why.
3. **Add tests** for new functionality or bug fixes.
4. **Run the full check suite** before submitting:
   ```bash
   make local-test
   make lint
   uv run pre-commit run --all-files
   ```
5. **Keep PRs focused** — one feature or fix per PR.
6. **Update translations** if you add or change user-facing strings:
   ```bash
   make messages          # Generate .po files
   # Edit locale/fr/LC_MESSAGES/django.po and locale/en/LC_MESSAGES/django.po
   make compilemessages   # Compile .mo files
   ```

## Internationalization (i18n)

- French is the default language (`LANGUAGE_CODE = "fr"`).
- All user-facing strings must be wrapped with `gettext` / `gettext_lazy`.
- API endpoints have no i18n prefix; UI routes use `i18n_patterns`.
- After adding strings, run `make messages` and translate both `fr` and `en` `.po` files.

## Architecture Notes

- **UUIDs** are the public identifiers for all models (never expose database PKs).
- **Business logic** lives in `apps/billing/services/` (shared by API and UI).
- `en16931_data` is a JSONField passthrough — no EN16931 model duplication.
- See `CLAUDE.md` for a complete overview of conventions.

## License

By contributing, you agree that your contributions will be licensed under the [GNU AGPL-3.0](LICENSE) license.
