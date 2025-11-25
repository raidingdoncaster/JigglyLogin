"""Helpers for loading Advent config (edit advent/config/advent_2025.json to tweak stamps/messages)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from extensions import db
from advent.models import AdventClaim

ADVENT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "advent_2025.json"
_CONFIG_CACHE: Dict[str, object] = {"data": None, "mtime": None}


def load_advent_config(force_refresh: bool = False) -> Dict[int, dict]:
    """Load and cache Advent day metadata as a dict keyed by day."""
    if not ADVENT_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Advent config missing: {ADVENT_CONFIG_PATH}")

    mtime = ADVENT_CONFIG_PATH.stat().st_mtime
    cached = _CONFIG_CACHE.get("data")
    cached_mtime = _CONFIG_CACHE.get("mtime")
    if not force_refresh and cached and cached_mtime == mtime:
        return cached  # type: ignore[return-value]

    with ADVENT_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    config: Dict[int, dict] = {}
    for entry in payload:
        try:
            day = int(entry["day"])
        except (KeyError, ValueError, TypeError):
            continue
        if not 1 <= day <= 25:
            continue
        config[day] = {
            "day": day,
            "stamp_png": entry.get("stamp_png", ""),
            "message": entry.get("message", ""),
        }

    _CONFIG_CACHE["data"] = config
    _CONFIG_CACHE["mtime"] = mtime
    return config


def get_user_opened_days(user_id: int) -> List[int]:
    """Return sorted list of days the user has opened."""
    rows: List[AdventClaim] = (
        AdventClaim.query.filter_by(user_id=user_id)
        .order_by(AdventClaim.day.asc())
        .all()
    )
    return [row.day for row in rows]


def get_advent_state_for_user(user_id: int, today_day: int) -> Dict[str, Optional[int] | List[int]]:
    """Compute which day the user can open today alongside opened/locked lists."""
    today_day = _clamp_day(today_day)
    opened_days = get_user_opened_days(user_id)
    opened_lookup = set(opened_days)

    openable_day: Optional[int] = None
    for day in range(1, today_day + 1):
        if day not in opened_lookup:
            openable_day = day
            break

    locked_days = [
        day for day in range(1, 26) if day not in opened_lookup and day != openable_day
    ]

    return {
        "opened_days": opened_days,
        "openable_day": openable_day,
        "locked_days": locked_days,
    }


def open_advent_day(user_id: int, day: int) -> bool:
    """Persist the newly opened day; returns False if already claimed or invalid."""
    if not 1 <= day <= 25:
        return False

    existing = AdventClaim.query.filter_by(user_id=user_id, day=day).first()
    if existing:
        return False

    claim = AdventClaim(user_id=user_id, day=day)
    db.session.add(claim)

    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def _clamp_day(day: int) -> int:
    try:
        return max(1, min(int(day), 25))
    except (TypeError, ValueError):
        return 1

