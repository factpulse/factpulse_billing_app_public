# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [1.0.0] — 2026-03-11

### Fonctionnalités

- **Facturation** : création, validation, transmission et annulation de factures (cycle de vie complet)
- **Clients** : gestion des clients avec lookup SIRENE pour pré-remplissage automatique
- **Produits** : catalogue de produits avec TVA configurable
- **Fournisseurs** : gestion multi-fournisseurs avec numérotation automatique des factures
- **PDF** : génération de factures PDF via WeasyPrint
- **API REST** : API complète avec OpenAPI/Swagger (drf-spectacular)
- **Interface web** : frontend HTMX/Alpine.js avec tableaux de bord
- **MCP Server** : serveur Model Context Protocol intégré (streamable-http) pour Claude Desktop et Claude Code
- **OAuth 2.1** : provider complet avec PKCE et Dynamic Client Registration (RFC 7591)
- **Clés API** : authentification par clés API longue durée (`fp_...`)
- **Webhooks** : notifications sortantes avec signature HMAC
- **Multi-tenant** : isolation par organisation avec rôles (owner, member, viewer)
- **Paiements** : intégration Stripe et GoCardless (optionnel)
- **EN 16931** : données structurées conformes à la norme européenne de facturation
- **i18n** : français et anglais
- **Déploiement** : Docker (dev/prod), self-hosted avec Caddy SSL automatique, local SQLite
