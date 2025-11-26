"""Database models for the RDAB Flask app."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func

from extensions import db


class CityPerk(db.Model):
    """City perk / perk partner offer surfaced in the trainer dashboard."""

    __tablename__ = "city_perks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    partner_name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    area = db.Column(db.String(50), nullable=True)

    short_tagline = db.Column(db.String(200), nullable=True)
    description_long = db.Column(db.Text, nullable=True)

    perk_mode = db.Column(db.String(20), nullable=False)

    address = db.Column(db.String(255), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    google_maps_link = db.Column(db.String(500), nullable=True)
    apple_maps_link = db.Column(db.String(500), nullable=True)

    website_url = db.Column(db.String(500), nullable=True)

    offer_type = db.Column(db.String(50), nullable=True)
    offer_text = db.Column(db.Text, nullable=True)

    start_date = db.Column(db.DateTime(timezone=True), nullable=False)
    end_date = db.Column(db.DateTime(timezone=True), nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    show_on_map = db.Column(db.Boolean, default=True, nullable=False)

    logo_url = db.Column(db.String(500), nullable=True)
    cover_image_url = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    created_by_admin_id = db.Column(db.Integer, nullable=True)
    notes_internal = db.Column(db.Text, nullable=True)

    MODE_LABELS = {
        "in_store": "In-store perk",
        "online": "Online perk",
        "hybrid": "In-store + Online perk",
    }

    MODE_ICONS = {
        "in_store": "🏬",
        "online": "🌐",
        "hybrid": "🔁",
    }

    STATUS_LIVE = "Live"
    STATUS_SCHEDULED = "Scheduled"
    STATUS_EXPIRED = "Expired"
    STATUS_INACTIVE = "Inactive"

    @property
    def is_live(self) -> bool:
        """Return True when the perk is active for the current UTC moment."""
        return self.status() == self.STATUS_LIVE

    def status(self, reference: Optional[datetime] = None) -> str:
        """Return a friendly status label for admin dashboards."""
        ref = reference or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        start = _ensure_aware(self.start_date)
        end = _ensure_aware(self.end_date)

        if not self.is_active:
            return self.STATUS_INACTIVE
        if start and start > ref:
            return self.STATUS_SCHEDULED
        if end and end < ref:
            return self.STATUS_EXPIRED
        return self.STATUS_LIVE

    @property
    def perk_mode_label(self) -> str:
        return self.MODE_LABELS.get(self.perk_mode, "Unknown perk mode")

    @property
    def perk_mode_icon(self) -> str:
        return self.MODE_ICONS.get(self.perk_mode, "❔")

    def to_public_dict(self) -> dict:
        """Serialize the perk to the public API schema."""
        return {
            "id": self.id,
            "name": self.name,
            "partner_name": self.partner_name,
            "category": self.category,
            "area": self.area,
            "short_tagline": self.short_tagline,
            "description_long": self.description_long,
            "perk_mode": self.perk_mode,
            "location": {
                "address": self.address,
                "latitude": self.latitude,
                "longitude": self.longitude,
                "google_maps_link": self.google_maps_link,
                "apple_maps_link": self.apple_maps_link,
            },
            "online": {
                "website_url": self.website_url,
            },
            "offer": {
                "offer_type": self.offer_type,
                "offer_text": self.offer_text,
            },
            "timing": {
                "start_date": _isoformat_or_none(self.start_date),
                "end_date": _isoformat_or_none(self.end_date),
                "is_live": self.is_live,
            },
            "flags": {
                "is_active": self.is_active,
                "show_on_map": self.show_on_map,
            },
            "media": {
                "logo_url": self.logo_url,
                "cover_image_url": self.cover_image_url,
            },
        }

    def __repr__(self) -> str:  # pragma: no cover - helper for shell debugging
        return f"<CityPerk id={self.id} name={self.name!r} mode={self.perk_mode!r}>"


def _isoformat_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    aware = _ensure_aware(value)
    return aware.astimezone(timezone.utc).isoformat()


def _ensure_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
