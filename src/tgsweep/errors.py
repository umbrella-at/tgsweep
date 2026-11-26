"""Errors whose message is meant for the user."""

from __future__ import annotations


class ConfigError(Exception):
    """Configuration is missing or invalid."""
