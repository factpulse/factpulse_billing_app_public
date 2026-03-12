import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

django_app = get_asgi_application()

_mcp_app = None


def _get_mcp_app():
    global _mcp_app
    if _mcp_app is None:
        from apps.mcp.asgi import get_mcp_app

        _mcp_app = get_mcp_app()
    return _mcp_app


async def application(scope, receive, send):
    """Route /mcp/ to the MCP server, everything else to Django."""
    if scope["type"] == "lifespan":
        # Forward lifespan to the MCP Starlette app so it initializes its task group
        mcp_app = _get_mcp_app()
        await mcp_app(scope, receive, send)
        return

    if scope["type"] == "http" and scope["path"].startswith("/mcp/"):
        mcp_app = _get_mcp_app()
        # Strip /mcp prefix — the MCP Starlette app has its route at /mcp
        scope = dict(scope)
        scope["path"] = scope["path"][4:]  # /mcp/mcp → /mcp
        await mcp_app(scope, receive, send)
    else:
        await django_app(scope, receive, send)
