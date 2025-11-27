"""Helpers for keeping the local CityPerk table in sync with Supabase."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from dateutil import parser as date_parser
from flask import current_app

from extensions import db
from models import CityPerk

SYNC_STATE_KEY = "CITY_PERKS_LAST_SUPABASE_SYNC"


def ensure_city_perks_cache(force: bool = False) -> int:
    """Refresh the SQLite cache from Supabase when it is stale."""
    client = _supabase_client()
    if not client:
        return 0

    now = datetime.now(timezone.utc)
    max_age = _cache_ttl(current_app)
    last_sync: datetime | None = current_app.config.get(SYNC_STATE_KEY)

    if not force and last_sync and (now - last_sync) < timedelta(seconds=max_age):
        return 0

    rows = _fetch_supabase_rows(client)
    if rows is None:
        return 0

    remote_ids: set[int] = set()
    for row in rows:
        record_id = _coerce_int(row.get("id"))
        if record_id is None:
            continue
        remote_ids.add(record_id)
        perk = db.session.get(CityPerk, record_id) or CityPerk(id=record_id)
        _hydrate_perk_from_row(perk, row)
        db.session.add(perk)

    if remote_ids:
        (
            db.session.query(CityPerk)
            .filter(~CityPerk.id.in_(remote_ids))
            .delete(synchronize_session=False)
        )
    else:
        db.session.query(CityPerk).delete(synchronize_session=False)

    db.session.commit()
    current_app.config[SYNC_STATE_KEY] = now
    return len(rows)


def mark_city_perks_cache_stale() -> None:
    """Invalidate the cached sync timestamp so the next request refetches."""
    current_app.config.pop(SYNC_STATE_KEY, None)


def _hydrate_perk_from_row(perk: CityPerk, row: dict[str, Any]) -> None:
    perk.name = row.get("name") or ""
    perk.partner_name = row.get("partner_name") or ""
    perk.category = row.get("category") or ""
    perk.area = row.get("area")
    perk.short_tagline = row.get("short_tagline")
    perk.description_long = row.get("description_long")
    perk.perk_mode = row.get("perk_mode") or "in_store"
    perk.address = row.get("address")
    perk.latitude = _coerce_float(row.get("latitude"))
    perk.longitude = _coerce_float(row.get("longitude"))
    perk.google_maps_link = row.get("google_maps_link")
    perk.apple_maps_link = row.get("apple_maps_link")
    perk.website_url = row.get("website_url")
    perk.offer_type = row.get("offer_type")
    perk.offer_text = row.get("offer_text")
    perk.start_date = _parse_datetime(row.get("start_date"))
    perk.end_date = _parse_datetime(row.get("end_date"))
    perk.is_active = bool(row.get("is_active"))
    perk.show_on_map = bool(row.get("show_on_map"))
    perk.logo_url = row.get("logo_url")
    perk.cover_image_url = row.get("cover_image_url")
    created_at = _parse_datetime(row.get("created_at"))
    if created_at:
        perk.created_at = created_at
    elif not perk.created_at:
        perk.created_at = datetime.now(timezone.utc)
    updated_at = _parse_datetime(row.get("updated_at"))
    if updated_at:
        perk.updated_at = updated_at
    elif not perk.updated_at:
        perk.updated_at = datetime.now(timezone.utc)
    perk.created_by_admin_id = _coerce_int(row.get("created_by_admin_id"))
    perk.notes_internal = row.get("notes_internal")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = date_parser.isoparse(str(value))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_supabase_rows(client) -> list[dict[str, Any]] | None:
    try:
        resp = client.table("city_perks").select("*").execute()
        return resp.data or []
    except Exception as exc:
        current_app.logger.warning("CityPerks Supabase sync failed: %s", exc)
        return None


def _cache_ttl(app) -> int:
    try:
        value = int(app.config.get("CITY_PERKS_CACHE_MAX_AGE_SECONDS", 90))
    except (TypeError, ValueError):
        value = 90
    return max(15, value)


def _supabase_client():
    try:
        return current_app.config.get("SUPABASE_CLIENT")
    except RuntimeError:
        return None
