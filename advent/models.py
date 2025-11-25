"""Database models for the Advent Calendar; update config/advent_2025.json for content."""

from datetime import datetime

from extensions import db


class AdventClaim(db.Model):
    """Tracks which day a given user has opened (unique per user/day)."""

    __tablename__ = "advent_claims"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True, nullable=False)
    day = db.Column(db.Integer, nullable=False)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "day", name="uq_advent_user_day"),
    )

