"""Client HTTP pour l'API recherche-entreprises.api.gouv.fr (SIRENE)."""

import re

import requests

API_BASE_URL = "https://recherche-entreprises.api.gouv.fr"
TIMEOUT = 10


class SireneError(Exception):
    """Erreur générique lors de l'appel à l'API SIRENE."""


class SireneNotFoundError(SireneError):
    """Aucun établissement trouvé pour le numéro donné."""


def _is_siren_or_siret(query: str) -> bool:
    """Check if query looks like a SIREN (9 digits) or SIRET (14 digits)."""
    cleaned = re.sub(r"\s+", "", query)
    return bool(re.fullmatch(r"\d{9}|\d{14}", cleaned))


def _normalize_siren_siret(query: str) -> str:
    """Valide et nettoie un numéro SIREN (9 chiffres) ou SIRET (14 chiffres)."""
    cleaned = re.sub(r"\s+", "", query)
    if not re.fullmatch(r"\d{9}|\d{14}", cleaned):
        raise SireneError(
            "Veuillez saisir un numéro SIREN (9 chiffres) ou SIRET (14 chiffres)."
        )
    return cleaned


def _compute_vat_number(siren: str) -> str:
    """Calcule le numéro de TVA intracommunautaire FR à partir du SIREN."""
    key = (12 + 3 * (int(siren) % 97)) % 97
    return f"FR{key:02d}{siren}"


def _build_address_line(siege: dict) -> str:
    """Construit la ligne d'adresse depuis les composants SIRENE du siège."""
    parts = []
    numero = siege.get("numero_voie") or ""
    indice = siege.get("indice_repetition_voie") or ""
    type_voie = siege.get("type_voie") or ""
    libelle = siege.get("libelle_voie") or ""

    if numero:
        parts.append(numero)
    if indice:
        parts.append(indice)
    if type_voie:
        parts.append(type_voie)
    if libelle:
        parts.append(libelle)

    return " ".join(parts)


def _search_api(query: str) -> list:
    """Call the recherche-entreprises API and return the results list."""
    try:
        resp = requests.get(
            f"{API_BASE_URL}/search",
            params={"q": query},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise SireneError(
            "Impossible de contacter l'API SIRENE. Veuillez réessayer."
        ) from exc

    return resp.json().get("results") or []


def _format_result(entreprise: dict, siret_query: str = "") -> dict:
    """Format a single API result into a flat dict for forms."""
    siege = entreprise.get("siege") or {}
    siren = entreprise.get("siren", "")
    siret = siege.get("siret", "")

    if siret_query and len(siret_query) == 14:
        for etab in entreprise.get("matching_etablissements") or []:
            if etab.get("siret") == siret_query:
                siege = etab
                siret = siret_query
                break
        else:
            siret = siret_query

    return {
        "name": entreprise.get("nom_complet", ""),
        "siren": siren,
        "siret": siret,
        "vat_number": _compute_vat_number(siren) if siren else "",
        "address_line1": _build_address_line(siege),
        "address_postcode": siege.get("code_postal") or "",
        "address_city": siege.get("libelle_commune") or "",
        "address_country": "FR",
        "etat_administratif": entreprise.get("etat_administratif", ""),
    }


def lookup(query: str) -> dict:
    """Recherche une entreprise par SIREN ou SIRET.

    Retourne un dict plat avec les données utiles pour les formulaires
    client / fournisseur.
    """
    cleaned = _normalize_siren_siret(query)
    results = _search_api(cleaned)
    if not results:
        raise SireneNotFoundError("Aucune entreprise trouvée pour ce numéro.")

    return _format_result(results[0], siret_query=cleaned)


def search(query: str, limit: int = 5) -> list:
    """Recherche des entreprises par nom ou numéro SIREN/SIRET.

    Accepte un nom d'entreprise (texte libre) ou un numéro SIREN/SIRET.
    Retourne une liste de résultats (max `limit`).
    """
    query = query.strip()
    if not query:
        raise SireneError("Veuillez saisir un nom ou un numéro SIREN/SIRET.")

    # If it's a SIREN/SIRET, use the strict lookup
    if _is_siren_or_siret(query):
        try:
            return [lookup(query)]
        except SireneNotFoundError:
            return []

    results = _search_api(query)
    if not results:
        raise SireneNotFoundError(f"Aucune entreprise trouvée pour « {query} ».")

    return [_format_result(r) for r in results[:limit]]
