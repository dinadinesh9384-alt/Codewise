"""Deterministic repository structure analyzer; contains no LLM integration."""

from .definition import create_definition, register

__all__ = ["create_definition", "register"]
