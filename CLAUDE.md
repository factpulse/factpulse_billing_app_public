# FactPulse Billing App

## Quick Start

```bash
# Local dev (SQLite, sans Docker)
make local-install
make local-migrate
make local-run

# Docker dev (Postgres, hot-reload)
make dev

# Production (gunicorn + uvicorn ASGI, derrière un reverse proxy)
make prod
```

## Key Commands

```bash
# Docker dev
make migrate          # Run migrations
make seed             # Seed demo data
make test             # Run tests
make messages         # Generate translations
make compilemessages  # Compile translations

# Local dev (SQLite)
make local-migrate    # Run migrations
make local-seed       # Seed demo data
make local-test       # Run tests
```

## Architecture

- **config/**: Django project settings (base/dev/prod split)
- **apps/core/**: Organization, JWT auth (simplejwt), API keys, middleware, permissions
- **apps/billing/**: Invoice, Supplier, Customer, Product models + services
- **apps/webhooks/**: Outbound webhook endpoints + delivery
- **apps/factpulse/**: FactPulse API client + Celery tasks
- **apps/ui/**: HTMX/Alpine.js frontend views
- **apps/assistant/**: Tool registry (shared with MCP server)
- **apps/mcp/**: MCP HTTP server (streamable-http, OAuth + API key auth, multi-tenant)
- **apps/oauth/**: OAuth 2.1 provider (PKCE, Dynamic Client Registration) pour Claude Desktop

## MCP Server

Endpoint intégré à Django via ASGI : `POST /mcp/mcp`
- Transport : streamable-http (stateless)
- Multi-tenant : org résolue depuis le token
- Code : `apps/mcp/` (server.py, middleware.py, asgi.py) + `config/asgi.py`
- Nécessite ASGI : `uvicorn config.asgi:application` (pas compatible `runserver`)
- Voir `docs/mcp-guide.md` pour le guide complet

### Auth MCP (2 méthodes)

| Méthode | Usage | Format |
|---------|-------|--------|
| **OAuth 2.1** | Claude Desktop | Auto-découverte via `.well-known`, PKCE |
| **API Key** | Claude Code, scripts | `Bearer fp_...` |

**Claude Desktop** — ajouter comme connecteur personnalisé (Settings > Connectors) :
- URL : `https://app.factpulse.fr/mcp/mcp`
- L'auth OAuth se fait automatiquement (login navigateur + consentement)

**Claude Code** — fichier `.mcp.json` à la racine du projet (gitignored) :
```json
{
  "mcpServers": {
    "factpulse": {
      "type": "http",
      "url": "https://app.factpulse.fr/mcp/mcp",
      "headers": {
        "Authorization": "Bearer fp_votre_cle_api"
      }
    }
  }
}
```

### OAuth endpoints (`apps/oauth/`)

- `GET /.well-known/oauth-protected-resource` — RFC 9728
- `GET /.well-known/oauth-authorization-server` — RFC 8414
- `POST /oauth/register/` — Dynamic Client Registration (RFC 7591)
- `GET /oauth/authorize/` — Authorization Code + PKCE
- `POST /oauth/token/` — Token exchange
- `POST /oauth/revoke_token/` — Révocation

### Tools disponibles (26)

Clients : `list_customers`, `get_customer`, `create_customer`, `update_customer`, `archive_customer`
Factures : `list_invoices`, `get_invoice`, `create_draft_invoice`, `update_draft_invoice`, `validate_invoice`, `transmit_invoice`, `cancel_invoice`, `mark_paid`, `download_pdf`
Produits : `list_products`, `get_product`, `create_product`, `update_product`, `archive_product`
Fournisseurs : `list_suppliers`, `get_supplier`, `create_supplier`, `update_supplier`, `archive_supplier`
Autres : `lookup_sirene`, `get_dashboard_stats`

## Conventions

- UUIDs are the public identifiers for all models (never expose PK)
- `en16931_data` is a JSONField passthrough — no EN16931 model duplication
- Business logic lives in `apps/billing/services/` (shared by API, UI, MCP)
- Service layer enrichment: `enrich_en16931_data()` for invoices, `enrich_customer_data()` for customers
- French is the default language (i18n via Django's `gettext`)
- API endpoints have no i18n prefix, UI routes use `i18n_patterns`
- Production uses gunicorn + uvicorn worker (ASGI), WhiteNoise (statics), behind a reverse proxy
- Password hashing: Argon2 (fallback PBKDF2)
- Package management: uv (deps in `pyproject.toml`, lockfile `uv.lock`)
