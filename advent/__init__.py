"""Advent Calendar package scaffolding — extend this to expose player routes later."""

from .routes import (
    create_advent_blueprint,
    create_player_advent_blueprint,
)

__all__ = ["create_advent_blueprint", "create_player_advent_blueprint"]
