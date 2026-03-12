# Import tool modules to trigger registration via @tool decorator.
from apps.assistant.tools import (  # noqa: F401
    customers,
    dashboard,
    invoices,
    products,
    sirene,
    suppliers,
)
from apps.assistant.tools.registry import TOOL_REGISTRY, tool  # noqa: F401
