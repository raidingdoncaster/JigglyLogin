"""Shared Flask extensions used by the Advent Calendar feature and future modules."""

from flask_sqlalchemy import SQLAlchemy

# SQLAlchemy instance initialized in app.py so blueprints/services can import `db`.
db = SQLAlchemy()

