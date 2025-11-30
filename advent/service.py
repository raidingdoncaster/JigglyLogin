"""Helpers for loading Advent config (edit advent/config/advent_2025.json to tweak stamps/messages)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from flask import current_app, has_app_context

from extensions import db
from advent.models import AdventClaim

DEFAULT_CONFIG_BASENAME = "advent_2025.json"
SUPABASE_TABLE = "advent_claims"
ADVENT_AWARD_ACTOR = "Advent Calendar"
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
    client = _get_supabase_client()
    if client:
        try:
            resp = (
                client.table(SUPABASE_TABLE)
                .select("day")
                .eq("trainer_id", user_id)
                .order("day", desc=False)
                .execute()
            )
            data = resp.data or []
            days = sorted(
                {
                    day
                    for day in (
                        _coerce_day(entry.get("day"))
                        for entry in data
                    )
                    if day is not None
                }
            )
            return days
        except Exception as exc:  # pragma: no cover - external service dependency
            _log_supabase_warning("fetching advent claims", exc)

    return _get_user_opened_days_sql(user_id)


def get_advent_state_for_user(
    user_id: int,
    today_day: int,
    allow_previous_day: bool = False,
) -> Dict[str, Optional[int] | List[int]]:
    """Compute which day(s) the user can open today alongside opened/locked lists."""
    today_day = _clamp_day(today_day)
    opened_days = get_user_opened_days(user_id)
    opened_lookup = set(opened_days)

    if allow_previous_day:
        candidates = sorted({today_day, max(1, today_day - 1)})
    else:
        candidates = list(range(1, today_day + 1))

    openable_days = [day for day in candidates if day not in opened_lookup]
    openable_day: Optional[int] = openable_days[0] if openable_days else None

    locked_days = [
        day
        for day in range(1, 26)
        if day not in opened_lookup and day not in openable_days
    ]

    return {
        "opened_days": opened_days,
        "openable_day": openable_day,
        "openable_days": openable_days,
        "locked_days": locked_days,
    }


def open_advent_day(user_id: int, day: int, username: Optional[str] = None) -> bool:
    """Persist the newly opened day; returns False if already claimed or invalid."""
    if not 1 <= day <= 25:
        return False

    client = _get_supabase_client()
    if client:
        payload = {
            "trainer_id": user_id,
            "day": day,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        if username:
            payload["trainer_username"] = username
        try:
            client.table(SUPABASE_TABLE).insert(payload, returning="minimal").execute()
            return True
        except Exception as exc:  # pragma: no cover - external service dependency
            if _is_supabase_conflict(exc):
                return False
            _log_supabase_warning("inserting advent claim", exc)
            return False

    return _open_advent_day_sql(user_id, day)


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


def _get_supabase_client():
    if not has_app_context():
        return None
    if not current_app.config.get("USE_SUPABASE"):
        return None
    client = current_app.config.get("SUPABASE_CLIENT")
    return client if client else None


def _get_user_opened_days_sql(user_id: int) -> List[int]:
    rows: List[AdventClaim] = (
        AdventClaim.query.filter_by(user_id=user_id)
        .order_by(AdventClaim.day.asc())
        .all()
    )
    return [row.day for row in rows]


def _open_advent_day_sql(user_id: int, day: int) -> bool:
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


def _coerce_day(raw_day) -> Optional[int]:
    try:
        value = int(raw_day)
    except (TypeError, ValueError):
        return None
    if 1 <= value <= 25:
        return value
    return None


def _is_supabase_conflict(exc: Exception) -> bool:
    message = str(exc).lower()
    return "duplicate key value" in message or "unique constraint" in message


def _log_supabase_warning(action: str, exc: Exception) -> None:
    if not has_app_context():
        return
    logger = getattr(current_app, "logger", None)
    if logger:
        logger.warning("Advent Supabase error while %s: %s", action, exc)


def award_advent_passport_stamp(trainer_username: Optional[str], day: int) -> Tuple[bool, Optional[str]]:
    """Award a single Advent stamp to the trainer via Supabase."""
    if not trainer_username:
        return False, "Trainer username missing for Advent stamp award."

    client = _get_supabase_client()
    if not client:
        return False, "Supabase client unavailable."

    reason = f"Advent {day:02d}"
    payload = {
        "p_trainer": trainer_username,
        "p_delta": 1,
        "p_reason": reason,
        "p_awardedby": ADVENT_AWARD_ACTOR,
    }
    try:
        client.rpc("lugia_admin_adjust", payload).execute()
        return True, None
    except Exception as exc:  # pragma: no cover - external dependency
        _log_supabase_warning("awarding advent passport stamp", exc)
        return False, str(exc)
