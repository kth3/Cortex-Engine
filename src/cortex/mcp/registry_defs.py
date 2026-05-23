"""MCP tool registry composition."""
from __future__ import annotations

from .registry_common import *  # noqa: F401,F403
from .registry_mutation import MUTATION_TOOLS
from .registry_readonly import READONLY_TOOLS

TOOLS = [*READONLY_TOOLS, *MUTATION_TOOLS]


def list_tools():
    return TOOLS

