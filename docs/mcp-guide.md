# Guide MCP — Utiliser Claude comme assistant de facturation

FactPulse expose ses outils de facturation via le protocole MCP (Model Context Protocol), ce qui permet d'utiliser Claude comme assistant de facturation intelligent.

## Prérequis

- Un compte FactPulse

## Option A : Claude Desktop (recommandé)

L'authentification se fait via OAuth — pas besoin de clé API.

1. Ouvrir Claude Desktop → **Settings** → **Connectors**
2. **Add custom connector**
3. Entrer l'URL : `https://your-domain.example.com/mcp/mcp`
4. Claude Desktop ouvre votre navigateur pour vous connecter à FactPulse
5. Cliquer **Autoriser** sur la page de consentement
6. Les 26 outils sont disponibles

> Claude Desktop découvre automatiquement les endpoints OAuth via les métadonnées `.well-known`.

## Option B : Claude Code (CLI)

Claude Code utilise une clé API pour l'authentification.

### 1. Créer une clé API

Depuis l'interface FactPulse : menu **Clés API** dans la barre latérale.

1. Cliquez sur **Créer la clé**
2. Donnez un nom (ex : « Claude Code »)
3. **Copiez la clé immédiatement** (elle commence par `fp_` et ne sera plus visible ensuite)

### 2. Configurer Claude Code

Créez un fichier `.mcp.json` à la racine de votre projet :

```json
{
  "mcpServers": {
    "factpulse": {
      "type": "http",
      "url": "https://your-domain.example.com/mcp/mcp",
      "headers": {
        "Authorization": "Bearer fp_votre_cle_api"
      }
    }
  }
}
```

Relancez Claude Code. Les outils FactPulse sont disponibles.

> **Important :** Ajoutez `.mcp.json` à votre `.gitignore` — il contient votre clé API.

#### Alternative : via la CLI

```bash
claude mcp add --transport http --scope project factpulse \
  "https://your-domain.example.com/mcp/mcp" \
  --header "Authorization: Bearer fp_votre_cle_api"
```

## Utiliser

```
Toi : "Montre-moi les factures en retard"

Toi : "Crée une facture pour ACME Corp : 10 licences logiciel à 500€"

Toi : "Cherche l'entreprise Dupont dans SIRENE et crée le client"

Toi : "Quel est mon CA du trimestre ?"

Toi : "Valide toutes les factures en brouillon"
```

Claude gère automatiquement les étapes intermédiaires (recherche client, lookup SIRENE, création si nécessaire) sans intervention de votre part.

## Outils disponibles (26)

| Catégorie | Outils |
|-----------|--------|
| **Clients** | `list_customers`, `get_customer`, `create_customer`, `update_customer`, `archive_customer` |
| **Factures** | `list_invoices`, `get_invoice`, `create_draft_invoice`, `update_draft_invoice`, `validate_invoice`, `transmit_invoice`, `cancel_invoice`, `mark_paid`, `download_pdf` |
| **Produits** | `list_products`, `get_product`, `create_product`, `update_product`, `archive_product` |
| **Fournisseurs** | `list_suppliers`, `get_supplier`, `create_supplier`, `update_supplier`, `archive_supplier` |
| **Recherche** | `lookup_sirene` (registre SIRENE par nom ou numéro) |
| **Statistiques** | `get_dashboard_stats` |

## Authentification — détails techniques

### OAuth 2.1 (Claude Desktop)

Le serveur MCP implémente OAuth 2.1 Authorization Code + PKCE (RFC 7636) avec :

- **Protected Resource Metadata** (RFC 9728) : `/.well-known/oauth-protected-resource`
- **Authorization Server Metadata** (RFC 8414) : `/.well-known/oauth-authorization-server`
- **Dynamic Client Registration** (RFC 7591) : `POST /oauth/register/`
- **Authorization** : `GET /oauth/authorize/` (login + consentement)
- **Token** : `POST /oauth/token/` (code → access token, refresh)
- **Révocation** : `POST /oauth/revoke_token/`

Les tokens OAuth ont une durée de vie de 1h (access) / 7j (refresh) avec rotation automatique.

### Clé API (Claude Code, scripts)

- Les clés sont stockées hashées (SHA-256) — elles ne sont visibles qu'à la création
- Chaque clé est liée à un utilisateur et une organisation
- Révocation : depuis l'interface (Clés API → Révoquer) ou via `DELETE /api/v1/auth/api-keys/<uuid>/`
- Le préfixe `fp_XXXXXXXX…` permet d'identifier visuellement quelle clé est utilisée
- La dernière date d'utilisation est visible dans l'interface

## Vérifier la connexion

```bash
curl -s -X POST https://your-domain.example.com/mcp/mcp \
  -H "Authorization: Bearer fp_votre_cle_api" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

Vous devriez recevoir une réponse JSON-RPC avec `serverInfo.name: "FactPulse Billing"`.
