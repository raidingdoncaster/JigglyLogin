"""Helpers for loading Advent config (edit advent/config/advent_2025.json to tweak stamps/messages)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from flask import current_app, has_app_context

from extensions import db
from advent.models import AdventClaim

DEFAULT_CONFIG_BASENAME = "advent_2025.json"
_CONFIG_CACHE: Dict[str, object] = {"data": None, "mtime": None, "path": None}


def load_advent_config(force_refresh: bool = False) -> Dict[int, dict]:
    """Load and cache Advent day metadata as a dict keyed by day."""
    config_path = _resolve_config_path()

    mtime = config_path.stat().st_mtime
    cached = _CONFIG_CACHE.get("data")
    cached_mtime = _CONFIG_CACHE.get("mtime")
    cached_path = _CONFIG_CACHE.get("path")
    if not force_refresh and cached and cached_mtime == mtime and cached_path == config_path:
        return cached  # type: ignore[return-value]

    with config_path.open("r", encoding="utf-8") as handle:
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
    _CONFIG_CACHE["path"] = config_path
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


def _resolve_config_path() -> Path:
    """Return the first Advent config path that exists across multiple fallbacks."""
    candidates: List[Path] = []
    env_override = os.environ.get("ADVENT_CONFIG_PATH")
    if env_override:
        candidates.append(Path(env_override).expanduser())

    module_dir = Path(__file__).resolve().parent
    candidates.append(module_dir / "config" / DEFAULT_CONFIG_BASENAME)

    if has_app_context():
        root_path = Path(current_app.root_path)
        candidates.extend(
            [
                root_path / "advent" / "config" / DEFAULT_CONFIG_BASENAME,
                root_path / "config" / DEFAULT_CONFIG_BASENAME,
                root_path / "app" / "config" / DEFAULT_CONFIG_BASENAME,
            ]
        )

    seen: List[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.append(path)
        if path.exists():
            return path

    checked = ", ".join(str(path) for path in seen)
    raise FileNotFoundError(f"Advent config missing. Checked: {checked}")
