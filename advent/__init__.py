"""Advent Calendar package scaffolding — extend this to expose player routes later."""

from .routes import create_advent_blueprint  # re-export factory for convenience

__all__ = ["create_advent_blueprint"]

