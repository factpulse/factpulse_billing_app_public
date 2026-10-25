# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [1.3.3] — 2026-10-25

### Modifié

- Dépendances : correctifs de sécurité (Pillow 13.0, certifi 2026.10.23, brotli 1.2.1, filelock 4.2, virtualenv 21.14.13), aucune vulnérabilité connue restante
- Fournisseurs : le logo envoyé depuis l'interface est désormais validé comme dans l'API (image valide, formats JPG/PNG/WebP, 2 Mo max)

## [1.3.2] — 2026-10-12

### Modifié

- Dépendances : correctifs de sécurité (Django 5.2.18, cryptography 50, PyJWT 2.15, Pillow 12.3, WeasyPrint 70, oauthlib 4, starlette, urllib3, requests…), aucune vulnérabilité connue restante
- Image Docker : HarfBuzz-Subset installé (bientôt requis par WeasyPrint)

## [1.3.1] — 2026-10-12

### Modifié

- Statuts du cycle de vie : seules les organisations ayant une facture en cours de cycle sont interrogées (beaucoup moins d'appels à l'API)
- Après l'envoi, vérification ciblée de la facture (20 s, 1 min, 3 min) : « Transmission en cours » passe à « Transmise » en quelques secondes

## [1.3.0] — 2026-10-12

### Ajouté

- **Vue d'ensemble** (Réglages) : toute la configuration sur une page — entreprise, plateforme agréée, parcours des factures par source (arrivée, validation, transmission), intégrations — avec la liste des points à régler
- **Cycle de vie** : résumé des statuts CDAR en tête de la fiche facture (Déposée → Reçue → Mise à disposition → Encaissée), refus et rejets en rouge
- **Historique facture** : entrées dépliables avec leurs détails (erreur, date du statut, émetteur, origine du paiement)
- **SuperPDP** : connexion en un clic (OAuth) et déconnexion depuis la page Plateforme agréée
- **Liste des factures** : filtre « Transmise à la plateforme »

### Modifié

- Refonte visuelle : thème sombre épuré, navigation par sections, affichage mobile, montants au format français
- Historique : statuts reçus ensemble affichés dans l'ordre du cycle de vie, « émis » ou « reçu » selon la direction du flux, statut 212 envoyé et son retour par la plateforme fusionnés, heure des statuts sans fuseau correctement interprétée
- Statuts CDAR : direction, identifiant du flux et du message enregistrés

## [1.2.0] — 2026-10-10

### Ajouté

- **Paiement par carte** : moyen de paiement `CARTE` (code UNTDID 4461 n° 48), déduit des factures Stripe payées par carte
- **Franchise en base sans numéro de TVA** : le SIREN est transmis comme identifiant fiscal du vendeur (BT-32), accepté par BR-E-02
- **Signaux** `invoice_validated` et `invoice_failed` (génération, transmission, refus client et rejet PA) pour les modules complémentaires
- **SuperPDP** : `superpdp_connect_url()` et `get_superpdp_status()` dans le client FactPulse
- **Inscription** : paramètre `?next=` conservé jusqu'au lien de vérification, qui connecte l'utilisateur et le ramène sur la page d'origine

### Modifié

- L'IBAN n'est plus exigé que pour les factures payées par virement (BR-50/BR-61)
- CI : smoke test sans MinIO

## [1.1.0] — 2026-10-10

### Ajouté

- **Stripe** : pages de synchronisation (clients, produits, factures) et import manuel des factures Stripe
- **Stripe** : bouton « Resynchroniser depuis Stripe » sur les brouillons importés
- **Stripe** : la numérotation Stripe est conservée (commande `backfill_stripe_numbers` pour les imports antérieurs)
- **Notifications** : e-mail aux propriétaires de l'organisation quand une facture est importée automatiquement
- **Fournisseurs** : exonération de TVA par défaut (motif BT-120 et code VATEX BT-121), appliquée aux lignes à 0 %
- Commande `copy_media_from_s3` pour migrer les fichiers d'un bucket S3/MinIO vers le volume local

### Modifié

- **Stockage** : les fichiers (PDF Factur-X, logos) sont stockés par défaut dans le volume Docker `media_data`. MinIO n'est plus fourni (ses images ne sont plus téléchargeables). S3 reste disponible via les variables `AWS_*` — voir « Migrating from the former bundled MinIO » dans `docs/deployment.md`
- **Stripe** : le destinataire est repris de la fiche client FactPulse ; le moyen de paiement est déduit de la facture Stripe (carte, prélèvement SEPA, virement)

## [1.0.1] — 2026-03-24

### Modifié

- chore: upgrade pinned dependencies (uv lock)

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
