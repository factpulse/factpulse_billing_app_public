"""Convert the tool registry to provider-specific formats."""

import inspect

from apps.assistant.tools.registry import TOOL_REGISTRY, ParamType, ToolDef

_PARAM_TYPE_TO_PYTHON = {
    ParamType.STRING: str,
    ParamType.INTEGER: int,
    ParamType.NUMBER: float,
    ParamType.BOOLEAN: bool,
    ParamType.ARRAY: list,
}

_JSON_TYPE_MAP = {
    ParamType.STRING: "string",
    ParamType.INTEGER: "integer",
    ParamType.NUMBER: "number",
    ParamType.BOOLEAN: "boolean",
    ParamType.ARRAY: "array",
}


def _param_to_json_schema(p):
    schema = {"type": _JSON_TYPE_MAP[p.type], "description": p.description}
    if p.enum:
        schema["enum"] = p.enum
    if p.type == ParamType.ARRAY:
        schema["items"] = {"type": "object"}
    return schema


def register_mcp_tools(mcp_server, org_resolver):
    """Register all tools from the registry into a FastMCP server.

    org_resolver: async callable that returns an Organization from MCP context.
    """
    for t in TOOL_REGISTRY.values():
        _register_one(mcp_server, t, org_resolver)


def _register_one(mcp_server, tool_def: ToolDef, org_resolver):
    async def handler(**kwargs):
        org = await org_resolver(kwargs)
        return tool_def.handler(org=org, **kwargs)

    # Build a proper signature so FastMCP can introspect parameters
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
    mcp_server.tool()(handler)
