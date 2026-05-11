"""Shared constants for the indexing pipeline."""

from cortex.parsers import registry as parser_registry

SUPPORTED_EXTENSIONS = parser_registry.parsers

__all__ = ["SUPPORTED_EXTENSIONS"]
