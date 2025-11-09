from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import current_app


PIN_SALT = os.getenv("PIN_SALT", "static-fallback-salt")


class GeocacheServiceError(Exception):
    """Raised when a geocache service operation fails."""

    def __init__(self, message: str, status_code: int = 400, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {"error": message}


def _supabase():
    client = current_app.config.get("SUPABASE_CLIENT")
    if not client:
        raise GeocacheServiceError("Supabase unavailable", status_code=503, payload={"error": "supabase_unavailable"})
    return client


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_trainer(value: str) -> str:
    return (value or "").strip()


def _normalize_campfire(name: Optional[str], opt_out: bool) -> Optional[str]:
    if opt_out:
        return "Not on Campfire"
    if not name:
        return None
    cleaned = name.strip()
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    return cleaned or None


def _hash_pin(pin: str) -> str:
    return hashlib.sha256((pin or "").encode("utf-8")).hexdigest()


def _hash_pin_salted(pin: str, username: str) -> str:
    basis = f"{PIN_SALT}:{(username or '').strip()}:{pin}".encode("utf-8")
    return hashlib.sha256(basis).hexdigest()


def _pin_matches(account_row: Dict[str, Any], trainer_username: str, pin: str) -> bool:
    if not pin:
        return False
    stored_hash = (account_row.get("pin_hash") or account_row.get("PIN Hash") or "").strip()
    if stored_hash:
        candidate_plain = _hash_pin(pin)
        if stored_hash.lower() == candidate_plain.lower():
            return True
        candidate_salted = _hash_pin_salted(pin, account_row.get("trainer_username") or trainer_username)
        if stored_hash.lower() == candidate_salted.lower():
            return True
    stored_pin = (account_row.get("pin") or account_row.get("PIN") or "").strip()
    if stored_pin:
        return stored_pin == pin
    return False


def _fetch_sheet1_account(trainer_username: str) -> Optional[Dict[str, Any]]:
    client = _supabase()
    try:
        resp = (
            client.table("sheet1")
            .select("*")
            .ilike("trainer_username", trainer_username)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase sheet1 lookup failed: %s", exc)
        raise GeocacheServiceError("Failed to query accounts", status_code=502, payload={"error": "account_lookup_failed"}) from exc

    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _update_sheet1_account(account_row: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    if not updates:
        return account_row

    client = _supabase()
    key_value = account_row.get("id")
    try:
        query = client.table("sheet1").update(updates)
        if key_value:
            query = query.eq("id", key_value)
        else:
            query = query.eq("trainer_username", account_row.get("trainer_username"))
        query.execute()
    except Exception as exc:
        current_app.logger.exception("Supabase sheet1 update failed: %s", exc)
        raise GeocacheServiceError("Failed to update account", status_code=502, payload={"error": "account_update_failed"}) from exc
    return _fetch_sheet1_account(account_row.get("trainer_username") or "")


def _ensure_trainer_account(trainer_username: str, pin: str, campfire_name: Optional[str], campfire_opt_out: bool) -> Tuple[Dict[str, Any], bool]:
    trainer_norm = trainer_username.lower()
    account = _fetch_sheet1_account(trainer_norm)
    campfire_value = _normalize_campfire(campfire_name, campfire_opt_out)
    hashed_pin = _hash_pin(pin)

    if account:
        if not _pin_matches(account, trainer_norm, pin):
            raise GeocacheServiceError("Incorrect PIN for existing account", status_code=401, payload={"error": "invalid_pin"})
        updates: Dict[str, Any] = {}
        if account.get("pin_hash") != hashed_pin:
            updates["pin_hash"] = hashed_pin
        if campfire_value != account.get("campfire_username"):
            updates["campfire_username"] = campfire_value
        updates["last_login"] = _now_iso()
        refreshed = _update_sheet1_account(account, updates)
        return refreshed or account, False

    payload = {
        "trainer_username": trainer_username,
        "pin_hash": hashed_pin,
        "memorable_password": "GEOCACHE-QUEST",
        "last_login": _now_iso(),
        "campfire_username": campfire_value or "Not on Campfire",
        "stamps": 0,
        "avatar_icon": "avatar1.png",
        "trainer_card_background": "default.png",
        "account_type": "Standard",
    }
    client = _supabase()
    try:
        resp = client.table("sheet1").insert(payload, returning="representation").execute()
    except Exception as exc:
        current_app.logger.exception("Supabase sheet1 insert failed: %s", exc)
        raise GeocacheServiceError("Failed to create account", status_code=502, payload={"error": "account_create_failed"}) from exc

    rows = getattr(resp, "data", None) or []
    created_account = rows[0] if rows else _fetch_sheet1_account(trainer_norm)
    if not created_account:
        raise GeocacheServiceError("Account creation failed", status_code=502, payload={"error": "account_create_failed"})
    return created_account, True


def _parse_uuid(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _fetch_profile_by_trainer(trainer_username: str) -> Optional[Dict[str, Any]]:
    client = _supabase()
    trainer_lc = trainer_username.lower()
    try:
        resp = (
            client.table("geocache_profiles")
            .select("*")
            .eq("trainer_name_lc", trainer_lc)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase geocache_profiles lookup failed: %s", exc)
        raise GeocacheServiceError("Failed to query quest profiles", status_code=502, payload={"error": "profile_lookup_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _fetch_profile_by_id(profile_id: str) -> Optional[Dict[str, Any]]:
    client = _supabase()
    try:
        resp = (
            client.table("geocache_profiles")
            .select("*")
            .eq("id", profile_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase geocache_profiles id lookup failed: %s", exc)
        raise GeocacheServiceError("Failed to query quest profiles", status_code=502, payload={"error": "profile_lookup_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _fetch_artifact(slug: str) -> Optional[Dict[str, Any]]:
    if not slug:
        return None
    spec = _get_artifact_spec(slug)
    if spec:
        return spec

    # Fallback to Supabase if configured (legacy support)
    client = _supabase()
    try:
        resp = (
            client.table("geocache_artifacts")
            .select("*")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase geocache_artifacts lookup failed: %s", exc)
        raise GeocacheServiceError("Failed to query artifacts", status_code=502, payload={"error": "artifact_lookup_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _merge_metadata(existing: Any, extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = existing.copy() if isinstance(existing, dict) else {}
    if extra:
        for key, value in extra.items():
            if value is not None:
                base[key] = value
    return base


def _merge_dict(base: Optional[Dict[str, Any]], updates: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not base:
        base = {}
    result = dict(base)
    if not updates:
        return result
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _ensure_geocache_profile(
    trainer_username: str,
    pin: str,
    campfire_name: Optional[str],
    campfire_opt_out: bool,
    account_row: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    trainer_norm = trainer_username.strip()
    profile = _fetch_profile_by_trainer(trainer_norm)
    campfire_value = _normalize_campfire(campfire_name, campfire_opt_out)
    hashed_pin = _hash_pin(pin)
    merged_metadata = _merge_metadata(
        profile.get("metadata") if profile else {},
        {
            "campfire_opt_out": campfire_opt_out,
            "last_login_at": _now_iso(),
            **(metadata or {}),
        },
    )

    update_payload: Dict[str, Any] = {}
    rdab_user_uuid = _parse_uuid(account_row.get("id"))

    if profile:
        if profile.get("pin_hash") != hashed_pin:
            update_payload["pin_hash"] = hashed_pin
        if campfire_value != profile.get("campfire_name"):
            update_payload["campfire_name"] = campfire_value
        if rdab_user_uuid and not profile.get("rdab_user_id"):
            update_payload["rdab_user_id"] = rdab_user_uuid
        update_payload["metadata"] = merged_metadata
        if update_payload:
            client = _supabase()
            try:
                resp = (
                    client.table("geocache_profiles")
                    .update(update_payload, returning="representation")
                    .eq("id", profile["id"])
                    .execute()
                )
            except Exception as exc:
                current_app.logger.exception("Supabase geocache_profiles update failed: %s", exc)
                raise GeocacheServiceError("Failed to update quest profile", status_code=502, payload={"error": "profile_update_failed"}) from exc
            rows = getattr(resp, "data", None) or []
            return (rows[0] if rows else _fetch_profile_by_trainer(trainer_norm)) or profile, False
        return profile, False

    insert_payload = {
        "trainer_name": trainer_username,
        "campfire_name": campfire_value,
        "pin_hash": hashed_pin,
        "metadata": merged_metadata,
    }
    if rdab_user_uuid:
        insert_payload["rdab_user_id"] = rdab_user_uuid

    client = _supabase()
    try:
        resp = (
            client.table("geocache_profiles")
            .insert(insert_payload, returning="representation")
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase geocache_profiles insert failed: %s", exc)
        raise GeocacheServiceError("Failed to create quest profile", status_code=502, payload={"error": "profile_create_failed"}) from exc

    rows = getattr(resp, "data", None) or []
    created_profile = rows[0] if rows else _fetch_profile_by_trainer(trainer_norm)
    if not created_profile:
        raise GeocacheServiceError("Quest profile creation failed", status_code=502, payload={"error": "profile_create_failed"})
    return created_profile, True


SESSION_FIELDS = {
    "current_act",
    "last_scene",
    "branch",
    "choices",
    "inventory",
    "progress_flags",
    "ending_choice",
    "ended_at",
}

REQUIRED_FLAGS_BY_ACT = {
    2: {"compass_found", "compass_repaired"},
    3: {
        "miners_riddle_solved",
        "sigil_dawn_recovered",
        "focus_test_passed",
        "sigil_roots_recovered",
        "oracle_mood_profiled",
    },
    6: {
        "market_returned",
        "pink_bike_check",
        "illusion_battle_won",
        "sir_nigel_check_in",
        "sigil_might_recovered",
        "order_defeated",
    },
}


def _authorize_profile(profile_id: str, pin: str) -> Dict[str, Any]:
    profile = _fetch_profile_by_id(profile_id)
    if not profile:
        raise GeocacheServiceError("Profile not found", status_code=404, payload={"error": "profile_not_found"})

    pin_clean = (pin or "").strip()
    if not pin_clean:
        raise GeocacheServiceError("PIN required", status_code=400, payload={"error": "missing_pin"})

    hashed_pin = _hash_pin(pin_clean)
    if profile.get("pin_hash") != hashed_pin:
        raise GeocacheServiceError("Incorrect PIN", status_code=401, payload={"error": "invalid_pin"})

    return profile


def _hash_location_token(lat: float, lng: float, precision: int = 4) -> str:
    rounded_lat = round(lat, precision)
    rounded_lng = round(lng, precision)
    payload = f"{rounded_lat:.{precision}f}:{rounded_lng:.{precision}f}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _distance_m(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """Return distance in metres using haversine formula."""
    radius = 6371000  # metres
    d_lat = radians(lat_b - lat_a)
    d_lng = radians(lng_b - lng_a)
    lat1 = radians(lat_a)
    lat2 = radians(lat_b)
    a = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return radius * c


_DEFAULT_STORY = {"title": "Whispers of the Wild Court", "version": 1, "acts": [], "scenes": {}}
_location_cache: Dict[str, Any] = {"path": None, "mtime": 0, "locations": {}}
_artifact_cache: Dict[str, Any] = {"path": None, "mtime": 0, "artifacts": {}}


def _resolve_config_path(key: str, default_filename: Optional[str] = None) -> Path:
    value = current_app.config.get(key)
    if not value:
        if default_filename:
            return Path(current_app.root_path) / "data" / default_filename
        raise GeocacheServiceError(f"{key} is not configured", status_code=500, payload={"error": "config_missing"})
    path = Path(value)
    if not path.is_absolute():
        path = Path(current_app.root_path) / path
    if not path.exists():
        fallback = Path(__file__).resolve().parent.parent / "data" / Path(path.name)
        if fallback.exists():
            return fallback
    return path


def get_story_path() -> Path:
    return _resolve_config_path("GEOCACHE_STORY_PATH", "geocache_story.json")


def get_assets_path() -> Path:
    return _resolve_config_path("GEOCACHE_ASSETS_PATH", "geocache_assets.json")


def _load_story_locations() -> Dict[str, Dict[str, float]]:
    story_path = get_assets_path()
    try:
        mtime = story_path.stat().st_mtime
    except FileNotFoundError:
        current_app.logger.warning("Geocache assets file missing at %s", story_path)
        _location_cache.update({"path": str(story_path), "mtime": 0, "locations": {}})
        return {}
    cache_path = _location_cache.get("path")
    if cache_path != str(story_path) or _location_cache.get("mtime", 0) < mtime:
        try:
            with story_path.open("r", encoding="utf-8") as handle:
                assets = json.load(handle)
        except Exception as exc:
            current_app.logger.warning("Unable to load geocache assets for locations: %s", exc)
            _location_cache.update({"path": str(story_path), "mtime": mtime, "locations": {}})
            return {}

        locations: Dict[str, Dict[str, float]] = {}
        for entry in assets.get("locations", []):
            location_id = entry.get("id") or entry.get("location_id")
            if not location_id:
                continue
            try:
                lat_f = float(entry.get("latitude"))
                lng_f = float(entry.get("longitude"))
            except (TypeError, ValueError):
                continue
            radius = float(entry.get("radius_m") or 75)
            precision = int(entry.get("precision") or 4)
            locations[str(location_id)] = {
                "latitude": lat_f,
                "longitude": lng_f,
                "radius_m": radius,
                "precision": precision,
                "scene_id": entry.get("scene_id"),
            }
        _location_cache.update({"path": str(story_path), "mtime": mtime, "locations": locations})
    return _location_cache.get("locations", {})


def _get_location_spec(location_id: str) -> Optional[Dict[str, float]]:
    if not location_id:
        return None
    locations = _load_story_locations()
    return locations.get(str(location_id))


def _load_story_artifacts() -> Dict[str, Dict[str, Any]]:
    story_path = get_assets_path()
    try:
        mtime = story_path.stat().st_mtime
    except FileNotFoundError:
        current_app.logger.warning("Geocache assets file missing at %s", story_path)
        _artifact_cache.update({"path": str(story_path), "mtime": 0, "artifacts": {}})
        return {}
    cache_path = _artifact_cache.get("path")
    if cache_path != str(story_path) or _artifact_cache.get("mtime", 0) < mtime:
        try:
            with story_path.open("r", encoding="utf-8") as handle:
                assets = json.load(handle)
        except Exception as exc:
            current_app.logger.warning("Unable to load geocache assets for artifacts: %s", exc)
            _artifact_cache.update({"path": str(story_path), "mtime": mtime, "artifacts": {}})
            return {}

        artifacts: Dict[str, Dict[str, Any]] = {}
        for entry in assets.get("artifacts", []):
            slug = str(entry.get("slug") or entry.get("id") or "")
            if not slug:
                continue
            code = entry.get("code")
            nfc_uid = entry.get("nfc_uid")
            artifacts[slug] = {
                "id": slug,
                "slug": slug,
                "display_name": entry.get("display_name") or slug.replace("-", " ").title(),
                "code": str(code).strip() if code is not None else None,
                "nfc_uid": str(nfc_uid).lower() if nfc_uid else None,
                "location_hint": entry.get("hint"),
                "scene_id": entry.get("scene_id"),
            }
        _artifact_cache.update({"path": str(story_path), "mtime": mtime, "artifacts": artifacts})
    return _artifact_cache.get("artifacts", {})


def _get_artifact_spec(slug: str) -> Optional[Dict[str, Any]]:
    if not slug:
        return None
    artifacts = _load_story_artifacts()
    return artifacts.get(str(slug))


def get_asset_locations() -> Dict[str, Dict[str, Any]]:
    return _load_story_locations()


def get_asset_artifacts() -> Dict[str, Dict[str, Any]]:
    return _load_story_artifacts()


def load_story(include_assets: bool = True) -> Dict[str, Any]:
    try:
        story_path = get_story_path()
        with story_path.open("r", encoding="utf-8") as handle:
            story = json.load(handle)
    except Exception as exc:
        current_app.logger.warning("Using default geocache story (%s)", exc)
        story = dict(_DEFAULT_STORY)

    if include_assets:
        try:
            story = enrich_story_with_assets(story)
        except GeocacheServiceError as exc:
            current_app.logger.warning("Geocache assets unavailable: %s", exc)
    return story


def enrich_story_with_assets(story: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the story with location/asset data merged in."""
    story_copy = json.loads(json.dumps(story))
    scenes = story_copy.get("scenes", {})
    locations = get_asset_locations()
    artifacts = get_asset_artifacts()

    for scene_id, scene in scenes.items():
        minigame = scene.get("minigame")
        if not minigame:
            continue
        kind = minigame.get("kind")
        if kind == "location":
            loc_key = minigame.get("location_id") or scene_id
            spec = locations.get(str(loc_key))
            if spec:
                minigame["location_id"] = spec.get("id") or spec.get("scene_id") or loc_key
                minigame["latitude"] = spec.get("latitude")
                minigame["longitude"] = spec.get("longitude")
                minigame["radius_m"] = spec.get("radius_m")
        elif kind == "artifact_scan":
            slug = minigame.get("artifact_slug") or scene_id
            spec = artifacts.get(str(slug))
            if spec:
                minigame["artifact_slug"] = spec.get("slug") or slug
                minigame["code"] = spec.get("code")
                minigame["nfc_uid"] = spec.get("nfc_uid")
                if spec.get("display_name") and not minigame.get("display_name"):
                    minigame["display_name"] = spec["display_name"]
                if spec.get("hint") and not minigame.get("code_hint"):
                    minigame["code_hint"] = spec["hint"]
    return story_copy


def _filter_session_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not state:
        return {}
    filtered: Dict[str, Any] = {}
    for key in SESSION_FIELDS:
        if key in state:
            filtered[key] = state[key]
    return filtered


def _get_latest_session(profile_id: str) -> Optional[Dict[str, Any]]:
    client = _supabase()
    try:
        resp = (
            client.table("geocache_sessions")
            .select("*")
            .eq("profile_id", profile_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase geocache_sessions lookup failed: %s", exc)
        raise GeocacheServiceError("Failed to query quest sessions", status_code=502, payload={"error": "session_lookup_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _create_session(profile_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    client = _supabase()
    payload = {
        "profile_id": profile_id,
        **state,
    }
    payload.setdefault("current_act", 1)
    payload.setdefault("choices", state.get("choices") or {})
    payload.setdefault("inventory", state.get("inventory") or {})
    payload.setdefault("progress_flags", state.get("progress_flags") or {})
    try:
        resp = (
            client.table("geocache_sessions")
            .insert(payload, returning="representation")
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase geocache_sessions insert failed: %s", exc)
        raise GeocacheServiceError("Failed to create quest session", status_code=502, payload={"error": "session_create_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    created = rows[0] if rows else _get_latest_session(profile_id)
    if not created:
        raise GeocacheServiceError("Quest session creation failed", status_code=502, payload={"error": "session_create_failed"})
    return created


def _update_session(existing_session: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    if not state:
        return existing_session
    client = _supabase()
    update_payload = dict(state)
    if "progress_flags" in state:
        update_payload["progress_flags"] = _merge_dict(
            existing_session.get("progress_flags"),
            state.get("progress_flags"),
        )
    if "inventory" in state:
        update_payload["inventory"] = _merge_dict(
            existing_session.get("inventory"),
            state.get("inventory"),
        )
    if "choices" in state:
        update_payload["choices"] = _merge_dict(
            existing_session.get("choices"),
            state.get("choices"),
        )

    try:
        resp = (
            client.table("geocache_sessions")
            .update(update_payload, returning="representation")
            .eq("id", existing_session["id"])
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase geocache_sessions update failed: %s", exc)
        raise GeocacheServiceError("Failed to update quest session", status_code=502, payload={"error": "session_update_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    if rows:
        return rows[0]
    return {**existing_session, **update_payload}


def _append_session_event(session_id: str, event: Optional[Dict[str, Any]]) -> None:
    if not event:
        return
    client = _supabase()
    payload = {
        "session_id": session_id,
        "event_type": event.get("event_type") or "update",
        "payload": event.get("payload") or {},
    }
    try:
        client.table("geocache_session_events").insert(payload).execute()
    except Exception as exc:
        # Log but do not raise — event logging should not break gameplay.
        current_app.logger.warning("Supabase geocache_session_events insert failed: %s", exc)


def _serialize_profile(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not profile:
        return None
    return {
        "id": profile.get("id"),
        "trainer_name": profile.get("trainer_name"),
        "campfire_name": profile.get("campfire_name"),
        "metadata": profile.get("metadata") or {},
        "rdab_user_id": profile.get("rdab_user_id"),
        "created_at": profile.get("created_at"),
        "updated_at": profile.get("updated_at"),
    }


def _serialize_session(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not session:
        return None
    return {
        "id": session.get("id"),
        "profile_id": session.get("profile_id"),
        "current_act": session.get("current_act"),
        "last_scene": session.get("last_scene"),
        "branch": session.get("branch"),
        "choices": session.get("choices") or {},
        "inventory": session.get("inventory") or {},
        "progress_flags": session.get("progress_flags") or {},
        "ending_choice": session.get("ending_choice"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "ended_at": session.get("ended_at"),
    }


def create_or_login_profile(
    trainer_name: str,
    pin: str,
    *,
    campfire_name: Optional[str] = None,
    campfire_opt_out: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trainer_clean = _normalize_trainer(trainer_name)
    if not trainer_clean:
        raise GeocacheServiceError("Trainer name required", status_code=400, payload={"error": "missing_trainer"})
    pin_clean = (pin or "").strip()
    if not pin_clean or not pin_clean.isdigit() or len(pin_clean) != 4:
        raise GeocacheServiceError("PIN must be exactly 4 digits", status_code=400, payload={"error": "invalid_pin_format"})

    account, account_created = _ensure_trainer_account(trainer_clean, pin_clean, campfire_name, campfire_opt_out)
    profile, profile_created = _ensure_geocache_profile(trainer_clean, pin_clean, campfire_name, campfire_opt_out, account, metadata)
    session = _get_latest_session(profile["id"])

    return {
        "account_created": account_created,
        "profile_created": profile_created,
        "profile": _serialize_profile(profile),
        "session": _serialize_session(session),
    }


def get_session_state(profile_id: str, pin: str) -> Dict[str, Any]:
    profile = _authorize_profile(profile_id, pin)
    session = _get_latest_session(profile["id"])
    return {
        "profile": _serialize_profile(profile),
        "session": _serialize_session(session),
    }


def save_session_state(
    profile_id: str,
    pin: str,
    state_updates: Optional[Dict[str, Any]],
    *,
    event: Optional[Dict[str, Any]] = None,
    reset: bool = False,
) -> Dict[str, Any]:
    profile = _authorize_profile(profile_id, pin)

    filtered_state = _filter_session_state(state_updates)
    session_created = False
    session_reset = False
    existing_session = None if reset else _get_latest_session(profile["id"])

    if filtered_state:
        existing_progress = existing_session.get("progress_flags") if existing_session else {}
        combined_progress = _merge_dict(existing_progress, filtered_state.get("progress_flags"))

        if "current_act" in filtered_state:
            try:
                target_act = int(filtered_state["current_act"])
            except (TypeError, ValueError):
                raise GeocacheServiceError("Invalid act value", status_code=400, payload={"error": "invalid_act"})

            current_act = int(existing_session.get("current_act") or 1) if existing_session else 1
            if target_act > current_act + 1:
                raise GeocacheServiceError(
                    "Act progression out of order",
                    status_code=409,
                    payload={"error": "act_out_of_sequence"},
                )
            required_flags = REQUIRED_FLAGS_BY_ACT.get(target_act)
            if required_flags:
                missing = [flag for flag in required_flags if not combined_progress.get(flag)]
                if missing:
                    raise GeocacheServiceError(
                        "Quest requirements not met",
                        status_code=409,
                        payload={"error": "requirements_missing", "missing": missing},
                    )

    if reset:
        session = _create_session(profile["id"], filtered_state)
        session_created = True
        session_reset = True
    else:
        if existing_session:
            if filtered_state:
                session = _update_session(existing_session, filtered_state)
            else:
                session = existing_session
        else:
            session = _create_session(profile["id"], filtered_state)
            session_created = True

    _append_session_event(session["id"], event)

    # Update profile metadata with last session reference
    metadata = _merge_metadata(profile.get("metadata"), {"last_session_id": session["id"]})
    client = _supabase()
    try:
        client.table("geocache_profiles").update({"metadata": metadata}, returning="minimal").eq("id", profile["id"]).execute()
        profile["metadata"] = metadata
    except Exception as exc:
        current_app.logger.warning("Supabase geocache_profiles metadata update failed: %s", exc)

    return {
        "profile": _serialize_profile(profile),
        "session": _serialize_session(session),
        "session_created": session_created,
        "session_reset": session_reset,
    }


def complete_artifact_scan(
    profile_id: str,
    pin: str,
    artifact_slug: str,
    *,
    success_flag: Optional[str] = None,
    code: Optional[str] = None,
    nfc_uid: Optional[str] = None,
    scene_id: Optional[str] = None,
) -> Dict[str, Any]:
    profile = _authorize_profile(profile_id, pin)
    slug = (artifact_slug or "").strip()
    if not slug:
        raise GeocacheServiceError("Artifact slug required", status_code=400, payload={"error": "missing_artifact"})

    artifact = _fetch_artifact(slug)
    if not artifact:
        raise GeocacheServiceError("Artifact not found", status_code=404, payload={"error": "artifact_not_found"})

    provided_code = (code or "").strip()
    provided_nfc = (nfc_uid or "").strip()
    validated = False
    method = None

    artifact_code = (artifact.get("code") or "").strip()
    artifact_nfc = (artifact.get("nfc_uid") or "").strip()

    if artifact_code:
        if provided_code and provided_code == artifact_code:
            validated = True
            method = "code"

    if not validated and artifact_nfc:
        if provided_nfc and provided_nfc.lower() == artifact_nfc.lower():
            validated = True
            method = "nfc"

    if not validated and not (artifact_code or artifact_nfc):
        # Artifact uses passive validation; accept if a code/nfc is not required.
        validated = True
        method = "passive"

    if not validated:
        raise GeocacheServiceError(
            "Artifact validation failed",
            status_code=401,
            payload={"error": "artifact_validation_failed"},
        )

    flag_key = (success_flag or slug).strip()
    if not flag_key:
        flag_key = slug

    entry = {
        "status": "found",
        "artifact_slug": slug,
        "artifact_id": artifact.get("id"),
        "display_name": artifact.get("display_name"),
        "validated_at": _now_iso(),
        "method": method,
    }
    if method == "code":
        entry["code"] = provided_code
    elif method == "nfc":
        entry["nfc_uid"] = provided_nfc

    state: Dict[str, Any] = {
        "progress_flags": {
            flag_key: entry,
        },
    }
    if scene_id:
        state["last_scene"] = scene_id

    event = {
        "event_type": "artifact_scan",
        "payload": {
            "artifact_slug": slug,
            "artifact_id": artifact.get("id"),
            "display_name": artifact.get("display_name"),
            "flag": flag_key,
            "method": method,
        },
    }

    return save_session_state(
        profile_id,
        pin,
        state,
        event=event,
    )


def complete_mosaic_puzzle(
    profile_id: str,
    pin: str,
    puzzle_id: str,
    *,
    success_flag: Optional[str] = None,
    scene_id: Optional[str] = None,
    success_token: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> Dict[str, Any]:
    profile = _authorize_profile(profile_id, pin)
    puzzle_key = (puzzle_id or "").strip()
    if not puzzle_key:
        raise GeocacheServiceError("Puzzle identifier required", status_code=400, payload={"error": "missing_puzzle"})

    flag_key = (success_flag or puzzle_key).strip()
    if not flag_key:
        flag_key = puzzle_key

    entry = {
        "status": "completed",
        "puzzle_id": puzzle_key,
        "validated_at": _now_iso(),
    }
    if success_token:
        entry["token"] = success_token
    if duration_ms is not None:
        try:
            entry["duration_ms"] = int(duration_ms)
        except (TypeError, ValueError):
            pass

    state: Dict[str, Any] = {
        "progress_flags": {
            flag_key: entry,
        },
    }
    if scene_id:
        state["last_scene"] = scene_id

    event = {
        "event_type": "puzzle_complete",
        "payload": {
            "puzzle_id": puzzle_key,
            "flag": flag_key,
            "duration_ms": entry.get("duration_ms"),
        },
    }

    return save_session_state(
        profile_id,
        pin,
        state,
        event=event,
    )


def complete_location_check(
    profile_id: str,
    pin: str,
    location_id: str,
    *,
    success_flag: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    accuracy_m: Optional[float] = None,
    precision: int = 4,
    scene_id: Optional[str] = None,
) -> Dict[str, Any]:
    _authorize_profile(profile_id, pin)
    location_key = (location_id or "").strip()
    if not location_key:
        raise GeocacheServiceError("Location identifier required", status_code=400, payload={"error": "missing_location"})

    if latitude is None or longitude is None:
        raise GeocacheServiceError("Latitude and longitude required", status_code=400, payload={"error": "missing_coordinates"})

    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        raise GeocacheServiceError("Invalid coordinate values", status_code=400, payload={"error": "invalid_coordinates"})

    accuracy_val = None
    if accuracy_m is not None:
        try:
            accuracy_val = float(accuracy_m)
        except (TypeError, ValueError):
            accuracy_val = None

    spec = _get_location_spec(location_key)
    if spec and "latitude" in spec and "longitude" in spec:
        radius_m = float(spec.get("radius_m") or 75)
        distance = _distance_m(lat, lng, spec["latitude"], spec["longitude"])
        if distance > radius_m:
            raise GeocacheServiceError(
                "You appear to be outside the quest radius.",
                status_code=409,
                payload={
                    "error": "location_out_of_range",
                    "distance_m": round(distance, 1),
                    "radius_m": radius_m,
                },
            )
        precision = int(spec.get("precision") or 4)
    else:
        radius_m = None
        precision = max(3, min(6, int(precision or 4)))

    rounded_lat = round(lat, precision)
    rounded_lng = round(lng, precision)
    location_hash = _hash_location_token(rounded_lat, rounded_lng, precision)

    flag_key = (success_flag or location_key).strip()
    if not flag_key:
        flag_key = location_key

    entry = {
        "status": "check_in",
        "location_id": location_key,
        "validated_at": _now_iso(),
        "lat": rounded_lat,
        "lng": rounded_lng,
        "precision": precision,
        "hash": location_hash,
    }
    if accuracy_val is not None:
        entry["accuracy_m"] = accuracy_val
    if spec and radius_m is not None:
        entry["radius_m"] = radius_m
        entry["target_lat"] = spec["latitude"]
        entry["target_lng"] = spec["longitude"]

    state: Dict[str, Any] = {
        "progress_flags": {
            flag_key: entry,
        },
    }
    if scene_id:
        state["last_scene"] = scene_id

    event_payload = {
        "location_id": location_key,
        "flag": flag_key,
        "precision": precision,
        "accuracy_m": accuracy_val,
    }
    if spec:
        event_payload.update(
            {
                "target_lat": spec.get("latitude"),
                "target_lng": spec.get("longitude"),
                "radius_m": spec.get("radius_m"),
            }
        )

    event = {
        "event_type": "location_check",
        "payload": event_payload,
    }

    return save_session_state(
        profile_id,
        pin,
        state,
        event=event,
    )
