"""FactPulse MCP HTTP server — multi-tenant, JWT-authenticated.

Exposes billing tools via MCP streamable-http transport.
Mounted at /mcp/ in the ASGI config.
"""

import asyncio
import contextvars
import inspect
import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from apps.assistant.tools import TOOL_REGISTRY
from apps.assistant.tools.registry import ParamType
from apps.assistant.tools.urls import enrich_result

# ── Context var for per-request organization ──────────────────────────

current_org: contextvars.ContextVar = contextvars.ContextVar("current_org")

# ── Type mapping ──────────────────────────────────────────────────────

_PARAM_TYPE_TO_PYTHON = {
    ParamType.STRING: str,
    ParamType.INTEGER: int,
    ParamType.NUMBER: float,
    ParamType.BOOLEAN: bool,
    ParamType.ARRAY: list,
}

# ── Build MCP server ─────────────────────────────────────────────────

_allowed_hosts = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
_site_url = os.environ.get("SITE_URL", "http://localhost:8000")

mcp = FastMCP(
    "FactPulse Billing",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h if ":" in h else f"{h}:*" for h in _allowed_hosts]
        + _allowed_hosts,
    ),
    instructions=(
        "Outils de facturation FactPulse. "
        "Tous les identifiants sont des UUID. "
        "Le fournisseur par défaut est utilisé automatiquement pour les factures. "
        "Avant de créer un client, vérifier d'abord s'il existe avec list_customers, "
        "puis chercher dans SIRENE avec lookup_sirene pour pré-remplir les infos. "
        "Toujours proposer le lien vers la page UI après une création ou consultation. "
        f"L'URL de base de l'application est {_site_url} — "
        "les pages UI sont : "
        f"{_site_url}/invoices/<uuid>/ (détail facture), "
        f"{_site_url}/customers/<uuid>/edit/ (détail client), "
        f"{_site_url}/products/<uuid>/edit/ (détail produit), "
        f"{_site_url}/suppliers/<uuid>/edit/ (détail fournisseur)."
    ),
)


def _make_handler(tool_def):
    """Create a closure that reads org from contextvar."""

    async def handler(**kwargs):
        org = current_org.get()
        result = await asyncio.to_thread(tool_def.handler, org=org, **kwargs)
        if isinstance(result, (dict, list)):
            enrich_result(tool_def.name, result)
            return json.dumps(result, ensure_ascii=False, default=str)
        return str(result)

    params = []
    for p in tool_def.params:
        py_type = _PARAM_TYPE_TO_PYTHON.get(p.type, str)
        default = inspect.Parameter.empty if p.required else None
        params.append(
            inspect.Parameter(
                p.name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=py_type,
            )
        )
    handler.__signature__ = inspect.Signature(params)
    handler.__name__ = tool_def.name
    handler.__doc__ = tool_def.description
    return handler


for _tool_def in TOOL_REGISTRY.values():
    mcp.tool(name=_tool_def.name, description=_tool_def.description)(
        _make_handler(_tool_def)
    )
