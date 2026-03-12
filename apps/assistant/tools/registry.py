"""Tool registry — single source of truth for chatbot + MCP tools."""

import uuid as uuid_lib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


def is_uuid(value):
    """Check if a value is a valid UUID string."""
    try:
        uuid_lib.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


class ParamType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"


@dataclass
class ToolParam:
    name: str
    type: ParamType
    description: str
    required: bool = True
    enum: list[str] | None = None
    default: Any = None


@dataclass
class ToolDef:
    name: str
    description: str
    params: list[ToolParam]
    handler: Callable
    confirm: bool = False
    read_only: bool = True


TOOL_REGISTRY: dict[str, ToolDef] = {}


def tool(
    name: str,
    description: str,
    params: list[ToolParam] | None = None,
    confirm: bool = False,
    read_only: bool = True,
):
    """Register a tool in the global registry."""

    def decorator(fn: Callable) -> Callable:
        TOOL_REGISTRY[name] = ToolDef(
            name=name,
            description=description,
            params=params or [],
            handler=fn,
            confirm=confirm,
            read_only=read_only,
        )
        return fn

    return decorator
