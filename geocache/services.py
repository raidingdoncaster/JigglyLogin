from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import current_app

import requests

from rdab.trainer_detection import extract_trainer_name


PIN_SALT = os.getenv("PIN_SALT", "static-fallback-salt")
LUGIA_REFRESH_URL = os.getenv(
    "LUGIA_WEBAPP_URL",
    "https://script.google.com/macros/s/AKfycbwx33Twu9HGwW4bsSJb7vwHoaBS56gCldNlqiNjxGBJEhckVDAnv520MN4ZQWxI1U9D/exec",
)


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


def _trigger_lugia_refresh() -> None:
    if not LUGIA_REFRESH_URL:
        return
    try:
        requests.get(LUGIA_REFRESH_URL, params={"action": "lugiaRefresh"}, timeout=10)
    except Exception as exc:  # pragma: no cover - best-effort
        current_app.logger.warning("Lugia refresh call failed: %s", exc)


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


def detect_trainer_name_from_upload(file_storage) -> str:
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise GeocacheServiceError(
            "Screenshot is required",
            status_code=400,
            payload={"error": "missing_screenshot"},
        )

    suffix = Path(file_storage.filename).suffix or ".png"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name
        detected = extract_trainer_name(tmp_path)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if not detected:
        raise GeocacheServiceError(
            "We could not read the trainer name from that screenshot.",
            status_code=422,
            payload={"error": "ocr_failed"},
        )
    return detected.strip()


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


def _trainer_exists(trainer_username: str) -> bool:
    cleaned = (trainer_username or "").strip().lower()
    if not cleaned:
        return False
    return _fetch_sheet1_account(cleaned) is not None


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


def _ensure_trainer_account(
    trainer_username: str,
    pin: str,
    campfire_name: Optional[str],
    campfire_opt_out: bool,
    *,
    create_if_missing: bool = True,
) -> Tuple[Dict[str, Any], bool]:
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

    if not create_if_missing:
        raise GeocacheServiceError(
            "Trainer not found. Please create a quest pass first.",
            status_code=404,
            payload={"error": "trainer_not_found"},
        )

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


def _fetch_wotw_profile_by_trainer(trainer_username: str) -> Optional[Dict[str, Any]]:
    client = _supabase()
    trainer_lc = trainer_username.lower()
    try:
        resp = (
            client.table("wotw_geocache_profiles")
            .select("id, trainer_username, save_state")
            .ilike("trainer_username", trainer_lc)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase wotw_geocache_profiles lookup failed: %s", exc)
        raise GeocacheServiceError("Failed to query quest profiles", status_code=502, payload={"error": "profile_lookup_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _fetch_wotw_profile_by_id(profile_id: str) -> Optional[Dict[str, Any]]:
    client = _supabase()
    try:
        resp = (
            client.table("wotw_geocache_profiles")
            .select("id, trainer_username, save_state")
            .eq("id", profile_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase wotw_geocache_profiles id lookup failed: %s", exc)
        raise GeocacheServiceError("Failed to query quest profiles", status_code=502, payload={"error": "profile_lookup_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _ensure_wotw_profile(trainer_username: str) -> Tuple[Dict[str, Any], bool]:
    existing = _fetch_wotw_profile_by_trainer(trainer_username)
    if existing:
        return existing, False

    client = _supabase()
    payload = {
        "trainer_username": trainer_username,
        "save_state": {},
    }
    try:
        resp = (
            client.table("wotw_geocache_profiles")
            .insert(payload, returning="representation")
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase wotw_geocache_profiles insert failed: %s", exc)
        raise GeocacheServiceError("Failed to create quest profile", status_code=502, payload={"error": "profile_create_failed"}) from exc

    rows = getattr(resp, "data", None) or []
    created = rows[0] if rows else _fetch_wotw_profile_by_trainer(trainer_username)
    if not created:
        raise GeocacheServiceError("Quest profile creation failed", status_code=502, payload={"error": "profile_create_failed"})
    return created, True


def _save_wotw_profile(profile_id: str, trainer_username: str, save_state: Dict[str, Any]) -> Dict[str, Any]:
    client = _supabase()
    payload = {
        "id": profile_id,
        "trainer_username": trainer_username,
        "save_state": save_state or {},
    }
    try:
        resp = (
            client.table("wotw_geocache_profiles")
            .upsert(payload, returning="representation")
            .execute()
        )
    except Exception as exc:
        current_app.logger.exception("Supabase wotw_geocache_profiles upsert failed: %s", exc)
        raise GeocacheServiceError("Failed to save quest progress", status_code=502, payload={"error": "profile_save_failed"}) from exc
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else _fetch_wotw_profile_by_id(profile_id)


def _fetch_artifact(slug: str) -> Optional[Dict[str, Any]]:
    if not slug:
        return None
    return _get_artifact_spec(slug)


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

DEFAULT_SESSION_STATE = {
    "current_act": 1,
    "last_scene": None,
    "branch": None,
    "choices": {},
    "inventory": {},
    "progress_flags": {},
    "ending_choice": None,
    "ended_at": None,
    "created_at": None,
    "updated_at": None,
    "event_log": [],
}


def _normalize_session_state(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    state = {key: value for key, value in DEFAULT_SESSION_STATE.items()}
    if not isinstance(raw, dict):
        return state

    def _as_dict(candidate: Any) -> Dict[str, Any]:
        return dict(candidate) if isinstance(candidate, dict) else {}

    state["current_act"] = int(raw.get("current_act") or state["current_act"])
    state["last_scene"] = raw.get("last_scene") or state["last_scene"]
    state["branch"] = raw.get("branch") or state["branch"]
    state["choices"] = _as_dict(raw.get("choices"))
    state["inventory"] = _as_dict(raw.get("inventory"))
    state["progress_flags"] = _as_dict(raw.get("progress_flags"))
    state["ending_choice"] = raw.get("ending_choice") or state["ending_choice"]
    state["ended_at"] = raw.get("ended_at") or state["ended_at"]
    state["created_at"] = raw.get("created_at") or state["created_at"]
    state["updated_at"] = raw.get("updated_at") or state["updated_at"]
    if isinstance(raw.get("event_log"), list):
        state["event_log"] = list(raw["event_log"])
    return state


def _merge_session_state(current: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalize_session_state(current)
    if not updates:
        return merged

    if "current_act" in updates and updates["current_act"] is not None:
        merged["current_act"] = int(updates["current_act"])
    if "last_scene" in updates:
        merged["last_scene"] = updates["last_scene"]
    if "branch" in updates:
        merged["branch"] = updates["branch"]
    if "ending_choice" in updates:
        merged["ending_choice"] = updates["ending_choice"]
    if "ended_at" in updates:
        merged["ended_at"] = updates["ended_at"]
    if "choices" in updates:
        merged["choices"] = _merge_dict(merged.get("choices"), updates.get("choices"))
    if "inventory" in updates:
        merged["inventory"] = _merge_dict(merged.get("inventory"), updates.get("inventory"))
    if "progress_flags" in updates:
        merged["progress_flags"] = _merge_dict(merged.get("progress_flags"), updates.get("progress_flags"))

    merged["updated_at"] = _now_iso()
    if not merged.get("created_at"):
        merged["created_at"] = merged["updated_at"]
    return merged


def _authorize_wotw_profile(profile_id: str, pin: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    profile = _fetch_wotw_profile_by_id(profile_id)
    if not profile:
        raise GeocacheServiceError("Quest profile not found", status_code=404, payload={"error": "profile_not_found"})

    trainer_username = profile.get("trainer_username") or ""
    pin_clean = (pin or "").strip()
    if not pin_clean:
        raise GeocacheServiceError("PIN required", status_code=400, payload={"error": "missing_pin"})

    account = _fetch_sheet1_account(trainer_username.lower())
    if not account:
        raise GeocacheServiceError("Trainer account missing", status_code=404, payload={"error": "account_missing"})

    if not _pin_matches(account, trainer_username, pin_clean):
        raise GeocacheServiceError("Incorrect PIN", status_code=401, payload={"error": "invalid_pin"})

    return profile, account

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


_RESOURCE_DIR = Path(__file__).resolve().parent / "resources"


def _load_resource_json(filename: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        path = _RESOURCE_DIR / filename
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


_DEFAULT_STORY = _load_resource_json(
    "geocache_story.json",
    {"title": "Whispers of the Wild Court", "version": 1, "acts": [], "scenes": {}, "metadata": {}},
)
_DEFAULT_ASSETS = _load_resource_json("geocache_assets.json", {"locations": [], "artifacts": []})
_location_cache: Dict[str, Any] = {"path": None, "mtime": 0, "locations": {}}
_artifact_cache: Dict[str, Any] = {"path": None, "mtime": 0, "artifacts": {}}


def _ensure_json_file(path: Path, default_payload: Dict[str, Any]) -> Path:
    if path.exists():
        return path
    logger = None
    try:
        logger = current_app.logger
    except RuntimeError:
        logger = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default_payload, indent=2), encoding="utf-8")
        message = f"Scaffolded geocache config at {path}"
        if logger:
            logger.info(message)
        else:
            print(message)
    except Exception as exc:
        if logger:
            logger.warning("Unable to scaffold geocache file at %s: %s", path, exc)
        else:
            print(f"Unable to scaffold geocache file at {path}: {exc}")
    return path


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
    path = _resolve_config_path("GEOCACHE_STORY_PATH", "geocache_story.json")
    if not path.exists():
        ensured = _ensure_json_file(path, _DEFAULT_STORY)
        if ensured.exists():
            return ensured
        return _RESOURCE_DIR / "geocache_story.json"
    return path


def get_assets_path() -> Path:
    path = _resolve_config_path("GEOCACHE_ASSETS_PATH", "geocache_assets.json")
    if not path.exists():
        ensured = _ensure_json_file(path, _DEFAULT_ASSETS)
        if ensured.exists():
            return ensured
        return _RESOURCE_DIR / "geocache_assets.json"
    return path


def _load_story_locations() -> Dict[str, Dict[str, float]]:
    story_path = get_assets_path()
    try:
        mtime = story_path.stat().st_mtime
    except FileNotFoundError:
        current_app.logger.warning("Geocache assets file missing at %s", story_path)
        assets = dict(_DEFAULT_ASSETS)
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
        _location_cache.update({"path": str(story_path), "mtime": 0, "locations": locations})
        return locations
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
        assets = dict(_DEFAULT_ASSETS)
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
        _artifact_cache.update({"path": str(story_path), "mtime": 0, "artifacts": artifacts})
        return artifacts
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
        if key not in state:
            continue
        value = state[key]
        if key == "current_act":
            try:
                filtered[key] = int(value)
            except (TypeError, ValueError):
                raise GeocacheServiceError("Invalid act value", status_code=400, payload={"error": "invalid_act"})
        elif key in {"choices", "inventory", "progress_flags"}:
            filtered[key] = value if isinstance(value, dict) else {}
        else:
            filtered[key] = value
    return filtered


def _serialize_profile(profile_row: Dict[str, Any], account_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    account = account_row or {}
    trainer_username = (account.get("trainer_username") or profile_row.get("trainer_username") or "").strip()
    campfire_name = account.get("campfire_username") or "Not on Campfire"
    metadata: Dict[str, Any] = {
        "campfire_username": campfire_name,
    }
    if account.get("last_login"):
        metadata["last_login_at"] = account["last_login"]

    return {
        "id": profile_row.get("id"),
        "trainer_name": trainer_username,
        "trainer_username": trainer_username,
        "campfire_name": campfire_name,
        "metadata": metadata,
    }


def _serialize_session(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    state = _normalize_session_state(state)
    return {
        "current_act": state.get("current_act") or 1,
        "last_scene": state.get("last_scene"),
        "branch": state.get("branch"),
        "choices": state.get("choices") or {},
        "inventory": state.get("inventory") or {},
        "progress_flags": state.get("progress_flags") or {},
        "ending_choice": state.get("ending_choice"),
        "ended_at": state.get("ended_at"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
    }


def create_or_login_profile(
    trainer_name: str,
    pin: str,
    *,
    campfire_name: Optional[str] = None,
    campfire_opt_out: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    create_if_missing: bool = True,
) -> Dict[str, Any]:
    trainer_clean = _normalize_trainer(trainer_name)
    if not trainer_clean:
        raise GeocacheServiceError("Trainer name required", status_code=400, payload={"error": "missing_trainer"})
    pin_clean = (pin or "").strip()
    if not pin_clean or not pin_clean.isdigit() or len(pin_clean) != 4:
        raise GeocacheServiceError("PIN must be exactly 4 digits", status_code=400, payload={"error": "invalid_pin_format"})

    account, account_created = _ensure_trainer_account(
        trainer_clean,
        pin_clean,
        campfire_name,
        campfire_opt_out,
        create_if_missing=create_if_missing,
    )
    profile_row, profile_created = _ensure_wotw_profile(trainer_clean)

    save_state = _normalize_session_state(profile_row.get("save_state"))
    serialized_profile = _serialize_profile(profile_row, account)
    serialized_session = _serialize_session(save_state)

    return {
        "account_created": account_created,
        "profile_created": profile_created,
        "profile": serialized_profile,
        "session": serialized_session,
    }


def complete_signup(
    trainer_name: str,
    pin: str,
    memorable: str,
    *,
    age_band: str,
    campfire_name: Optional[str] = None,
    campfire_opt_out: bool = False,
) -> Dict[str, Any]:
    trainer_clean = _normalize_trainer(trainer_name)
    if not trainer_clean:
        raise GeocacheServiceError("Trainer name required", status_code=400, payload={"error": "missing_trainer"})

    pin_clean = (pin or "").strip()
    if not pin_clean or not pin_clean.isdigit() or len(pin_clean) != 4:
        raise GeocacheServiceError("PIN must be exactly 4 digits", status_code=400, payload={"error": "invalid_pin_format"})

    memorable_clean = (memorable or "").strip()
    if not memorable_clean:
        raise GeocacheServiceError("Memorable password required", status_code=400, payload={"error": "missing_memorable"})

    age_key = (age_band or "").strip().lower()
    is_under13 = age_key in {"under13", "kids", "u13", "under-13"}

    if _trainer_exists(trainer_clean):
        raise GeocacheServiceError(
            "This trainer already exists. Please log in instead.",
            status_code=409,
            payload={"error": "duplicate_trainer"},
        )

    effective_opt_out = campfire_opt_out
    campfire_value: Optional[str]
    account_type = "Standard"

    if is_under13:
        account_type = "Kids Account"
        campfire_value = "Kids Account"
        effective_opt_out = True
    else:
        campfire_clean = (campfire_name or "").strip()
        if not campfire_clean:
            effective_opt_out = True
        campfire_value = _normalize_campfire(campfire_clean, effective_opt_out)

    hashed_pin = _hash_pin(pin_clean)
    payload = {
        "trainer_username": trainer_clean,
        "pin_hash": hashed_pin,
        "memorable_password": memorable_clean,
        "last_login": _now_iso(),
        "campfire_username": campfire_value or "Not on Campfire",
        "stamps": 0,
        "avatar_icon": "avatar1.png",
        "trainer_card_background": "default.png",
        "account_type": account_type,
    }

    client = _supabase()
    try:
        resp = client.table("sheet1").insert(payload, returning="representation").execute()
    except Exception as exc:
        current_app.logger.exception("Supabase signup insert failed: %s", exc)
        message = str(exc).lower()
        if "duplicate" in message:
            raise GeocacheServiceError(
                "This trainer already exists. Please log in instead.",
                status_code=409,
                payload={"error": "duplicate_trainer"},
            ) from exc
        raise GeocacheServiceError("Signup failed due to a server error", status_code=502, payload={"error": "account_create_failed"}) from exc

    rows = getattr(resp, "data", None) or []
    account_row = rows[0] if rows else _fetch_sheet1_account(trainer_clean.lower())
    if not account_row:
        raise GeocacheServiceError("Signup failed due to a server error", status_code=502, payload={"error": "account_create_failed"})

    metadata = {
        "signup_source": "geocache",
        "signup_age_band": "under13" if is_under13 else "13plus",
    }
    if effective_opt_out:
        metadata["campfire_opt_out"] = True

    profile_row, profile_created = _ensure_wotw_profile(trainer_clean)
    save_state = _normalize_session_state(profile_row.get("save_state"))

    _trigger_lugia_refresh()

    return {
        "account_created": True,
        "profile_created": profile_created,
        "profile": _serialize_profile(profile_row, account_row),
        "session": _serialize_session(save_state),
    }


def get_session_state(profile_id: str, pin: str) -> Dict[str, Any]:
    profile_row, account_row = _authorize_wotw_profile(profile_id, pin)
    save_state = _normalize_session_state(profile_row.get("save_state"))
    return {
        "profile": _serialize_profile(profile_row, account_row),
        "session": _serialize_session(save_state),
    }


def save_session_state(
    profile_id: str,
    pin: str,
    state_updates: Optional[Dict[str, Any]],
    *,
    event: Optional[Dict[str, Any]] = None,
    reset: bool = False,
) -> Dict[str, Any]:
    profile_row, account_row = _authorize_wotw_profile(profile_id, pin)
    filtered_state = _filter_session_state(state_updates)

    raw_state = profile_row.get("save_state") or {}
    existing_state = _normalize_session_state(raw_state)
    session_created = not bool(raw_state)
    session_reset = False

    if reset:
        session_reset = True
        new_state = _normalize_session_state(filtered_state or {})
        new_state["updated_at"] = _now_iso()
        if not new_state.get("created_at"):
            new_state["created_at"] = new_state["updated_at"]
    else:
        if filtered_state:
            combined_progress = _merge_dict(existing_state.get("progress_flags"), filtered_state.get("progress_flags"))

            target_act = filtered_state.get("current_act")
            if target_act is not None:
                current_act = int(existing_state.get("current_act") or 1)
                if target_act > current_act + 1:
                    raise GeocacheServiceError(
                        "Act progression out of order",
                        status_code=409,
                        payload={"error": "act_out_of_sequence"},
                    )
                required_flags = REQUIRED_FLAGS_BY_ACT.get(int(target_act))
                if required_flags:
                    missing = [flag for flag in required_flags if not combined_progress.get(flag)]
                    if missing:
                        raise GeocacheServiceError(
                            "Quest requirements not met",
                            status_code=409,
                            payload={"error": "requirements_missing", "missing": missing},
                        )

        new_state = _merge_session_state(existing_state, filtered_state)

    # Persist event metadata inside the save state for a simple audit trail.
    if event:
        base_log_source = new_state if session_reset else existing_state
        event_log = list(base_log_source.get("event_log") or [])
        event_entry = {
            "recorded_at": _now_iso(),
            **event,
        }
        event_log.append(event_entry)
        new_state["event_log"] = event_log[-50:]  # cap to most recent 50 entries

    timestamp = _now_iso()
    new_state["updated_at"] = timestamp
    if not new_state.get("created_at"):
        new_state["created_at"] = timestamp

    persisted = _save_wotw_profile(profile_row["id"], profile_row.get("trainer_username") or "", new_state)
    profile_row.update(persisted or {})
    profile_row["save_state"] = new_state

    return {
        "profile": _serialize_profile(profile_row, account_row),
        "session": _serialize_session(new_state),
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
