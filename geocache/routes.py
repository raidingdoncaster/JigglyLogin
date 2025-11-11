"""Blueprint for the rebuilt, self-contained geocache quest."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template

from .story import STORY

geocache_bp = Blueprint("geocache", __name__, url_prefix="/geocache")


@geocache_bp.get("/")
def quest_shell():
    """Serve the main quest shell."""
    initial_state = {
        "story": STORY,
    }
    return render_template("geocache/base.html", initial_state=initial_state)


@geocache_bp.get("/story.json")
def quest_story():
    """Expose the static story for debugging."""
    return jsonify(STORY)

