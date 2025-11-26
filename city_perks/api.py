"""Public JSON API for City Perks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, jsonify, request
from sqlalchemy import or_

from models import CityPerk

city_perks_api_blueprint = Blueprint(
    "city_perks_api",
    __name__,
    url_prefix="/api/city-perks",
)


@city_perks_api_blueprint.get("")
def list_live_city_perks():
    query = _live_perks_query()

    area = _clean_or_none(request.args.get("area"))
    category = _clean_or_none(request.args.get("category"))
    featured = _parse_bool_param(request.args.get("featured"))

    if area:
        query = query.filter(CityPerk.area == area)
    if category:
        query = query.filter(CityPerk.category == category)
    if featured is True:
        query = query.filter(CityPerk.is_featured.is_(True))
    elif featured is False:
        query = query.filter(CityPerk.is_featured.is_(False))

    perks = query.order_by(
        CityPerk.is_featured.desc(),
        CityPerk.start_date.asc(),
        CityPerk.name.asc(),
    ).all()

    return jsonify([perk.to_public_dict() for perk in perks])


@city_perks_api_blueprint.get("/<int:perk_id>")
def get_city_perk(perk_id: int):
    perk = _live_perks_query().filter(CityPerk.id == perk_id).first()
    if not perk:
        return _json_not_found()
    return jsonify(perk.to_public_dict())


def _live_perks_query():
    now = datetime.now(timezone.utc)
    return CityPerk.query.filter(
        CityPerk.is_active.is_(True),
        CityPerk.start_date <= now,
        or_(CityPerk.end_date.is_(None), CityPerk.end_date >= now),
    )


def _json_not_found():
    return jsonify({"error": "not_found", "message": "City perk not found"}), 404


def _clean_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _parse_bool_param(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None
