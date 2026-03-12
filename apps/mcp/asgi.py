"""ASGI app for the MCP endpoint — mounted at /mcp/ in config/asgi.py."""

from apps.mcp.middleware import MCPAuthMiddleware
from apps.mcp.server import mcp


def get_mcp_app():
    """Return the MCP Starlette app wrapped with auth middleware."""
    starlette_app = mcp.streamable_http_app()
    return MCPAuthMiddleware(starlette_app)
