"""SIRENE lookup tool — search French company registry."""

from apps.assistant.tools.registry import ParamType, ToolParam, tool


@tool(
    name="lookup_sirene",
    description=(
        "Recherche une entreprise dans le registre SIRENE par nom, "
        "numéro SIREN (9 chiffres) ou SIRET (14 chiffres). "
        "Retourne le nom, SIREN, SIRET, TVA et adresse. "
        "Utile avant de créer un client pour pré-remplir les informations."
    ),
    params=[
        ToolParam(
            "query",
            ParamType.STRING,
            "Nom d'entreprise, numéro SIREN (9 chiffres) ou SIRET (14 chiffres)",
        ),
    ],
)
def lookup_sirene(org, query, **kw):
    from apps.billing.services.sirene_client import SireneError, search

    try:
        results = search(query)
        if len(results) == 1:
            return results[0]
        return results
    except SireneError as exc:
        return {"error": str(exc)}
