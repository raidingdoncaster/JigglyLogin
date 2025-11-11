from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, session

from . import services


geocache_bp = Blueprint(
    "geocache",
    __name__,
    url_prefix="/geocache",
)


@dataclass
class FeatureGate:
    flag_name: str = "USE_GEOCACHE_QUEST"

    def enabled(self) -> bool:
        return bool(current_app.config.get(self.flag_name, False))

    def guard(self) -> None:
        if not self.enabled():
            abort(404)


feature_gate = FeatureGate()


def _load_story_payload() -> dict:
    """Load the quest story skeleton from disk."""
    import json

    try:
        story_path = services.get_story_path()
    except services.GeocacheServiceError:
        return {"title": "Whispers of the Wild Court", "acts": [], "scenes": {}, "version": 1}

    try:
        with story_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {"title": "Whispers of the Wild Court", "acts": [], "scenes": {}, "version": 1}
    except json.JSONDecodeError as exc:
        current_app.logger.error("Unable to parse geocache story JSON: %s", exc)
        return {"title": "Whispers of the Wild Court", "acts": [], "scenes": {}, "version": 1}


@geocache_bp.get("/")
def quest_shell():
    feature_gate.guard()

    initial_state = {
        "title": "Whispers of the Wild Court",
        "message": "Loading the Wild Court...",
        "enabled": feature_gate.enabled(),
        "use_supabase": bool(current_app.config.get("USE_SUPABASE", False)),
        "story": services.load_story(include_assets=True),
        "view": "landing",
        "session_trainer": (session.get("trainer") or "").strip(),
    }
    return render_template("geocache/base.html", initial_state=initial_state)


@geocache_bp.get("/status")
def quest_status():
    enabled = feature_gate.enabled()
    story = services.load_story(include_assets=False)
    return jsonify(
        {
            "enabled": enabled,
            "supabase": bool(current_app.config.get("USE_SUPABASE", False)),
            "story_version": story.get("version"),
        }
    )


@geocache_bp.get("/story")
def quest_story():
    feature_gate.guard()
    story = services.load_story(include_assets=True)
    return jsonify(story)


@geocache_bp.get("/manifest")
def quest_manifest():
    feature_gate.guard()
    story = services.load_story(include_assets=True)
    return jsonify({"story": story, "version": story.get("version")})


@geocache_bp.get("/start")
def quest_start():
    feature_gate.guard()
    initial_state = {
        "title": "Whispers of the Wild Court",
        "message": "Summoning Act I…",
        "enabled": feature_gate.enabled(),
        "use_supabase": bool(current_app.config.get("USE_SUPABASE", False)),
        "story": services.load_story(include_assets=True),
        "view": "signin",
        "session_trainer": (session.get("trainer") or "").strip(),
    }
    return render_template("geocache/base.html", initial_state=initial_state)


@geocache_bp.post("/profile")
def create_or_lookup_profile():
    feature_gate.guard()
    payload = request.get_json(silent=True) or {}
    trainer_name = (payload.get("trainer_name") or "").strip()
    pin = (payload.get("pin") or "").strip()
    campfire_name = payload.get("campfire_name")
    campfire_opt_out = bool(payload.get("campfire_opt_out"))
    metadata = payload.get("metadata")
    create_if_missing = payload.get("create_if_missing", True)
    use_session_auth = bool(payload.get("use_session_auth"))
    if metadata is not None and not isinstance(metadata, dict):
        metadata = None

    if not use_session_auth and (not trainer_name or not pin):
        return (
            jsonify({"error": "missing_fields", "detail": "trainer_name and pin are required"}),
            400,
        )
    if use_session_auth and not trainer_name:
        trainer_name = (session.get("trainer") or "").strip()
        if not trainer_name:
            return jsonify({"error": "session_not_authorised"}), 401

    try:
        result = services.create_or_login_profile(
            trainer_name,
            pin,
            campfire_name=campfire_name,
            campfire_opt_out=campfire_opt_out,
            metadata=metadata,
            create_if_missing=bool(create_if_missing),
            use_session_auth=use_session_auth,
        )
    except services.GeocacheServiceError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@geocache_bp.post("/signup/detect")
def signup_detect():
    feature_gate.guard()
    file = request.files.get("profile_screenshot") or request.files.get("screenshot")
    try:
        trainer_name = services.detect_trainer_name_from_upload(file)
    except services.GeocacheServiceError as exc:
        return jsonify(exc.payload), exc.status_code
    return jsonify({"trainer_name": trainer_name})


@geocache_bp.post("/signup/complete")
def signup_complete():
    feature_gate.guard()
    payload = request.get_json(silent=True) or {}
    trainer_name = payload.get("trainer_name")
    pin = payload.get("pin")
    memorable = payload.get("memorable")
    age_band = payload.get("age_band")
    campfire_name = payload.get("campfire_name")
    campfire_opt_out = bool(payload.get("campfire_opt_out"))

    try:
        result = services.complete_signup(
            trainer_name,
            pin,
            memorable,
            age_band=age_band,
            campfire_name=campfire_name,
            campfire_opt_out=campfire_opt_out,
        )
    except services.GeocacheServiceError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@geocache_bp.post("/session")
def upsert_session_state():
    feature_gate.guard()
    payload = request.get_json(silent=True) or {}
    profile_id = payload.get("profile_id")
    pin = (payload.get("pin") or "").strip()
    if not profile_id:
        return jsonify({"error": "missing_profile_id"}), 400

    state_updates = payload.get("state")
    event = payload.get("event")
    reset = bool(payload.get("reset"))
    use_session_auth = bool(payload.get("use_session_auth"))

    try:
        if state_updates is None and not reset:
            result = services.get_session_state(profile_id, pin, use_session_auth=use_session_auth)
        else:
            result = services.save_session_state(
                profile_id,
                pin,
                state_updates,
                event=event,
                reset=reset,
                use_session_auth=use_session_auth,
            )
    except services.GeocacheServiceError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@geocache_bp.post("/minigame/artifact")
def complete_artifact():
    feature_gate.guard()
    payload = request.get_json(silent=True) or {}
    profile_id = payload.get("profile_id")
    pin = (payload.get("pin") or "").strip()
    artifact_slug = payload.get("artifact_slug")
    success_flag = payload.get("success_flag")
    scene_id = payload.get("scene_id")
    code = payload.get("code")
    nfc_uid = payload.get("nfc_uid")

    if not profile_id or not artifact_slug:
        return jsonify({"error": "missing_fields"}), 400

    try:
        result = services.complete_artifact_scan(
            profile_id,
            pin,
            artifact_slug,
            success_flag=success_flag,
            code=code,
            nfc_uid=nfc_uid,
            scene_id=scene_id,
        )
    except services.GeocacheServiceError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@geocache_bp.post("/minigame/mosaic")
def complete_mosaic():
    feature_gate.guard()
    payload = request.get_json(silent=True) or {}
    profile_id = payload.get("profile_id")
    pin = (payload.get("pin") or "").strip()
    puzzle_id = payload.get("puzzle_id")
    success_flag = payload.get("success_flag")
    scene_id = payload.get("scene_id")
    success_token = payload.get("success_token")
    duration_ms = payload.get("duration_ms")

    if not profile_id or not puzzle_id:
        return jsonify({"error": "missing_fields"}), 400

    try:
        result = services.complete_mosaic_puzzle(
            profile_id,
            pin,
            puzzle_id,
            success_flag=success_flag,
            scene_id=scene_id,
            success_token=success_token,
            duration_ms=duration_ms,
        )
    except services.GeocacheServiceError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)


@geocache_bp.post("/minigame/location")
def complete_location():
    feature_gate.guard()
    payload = request.get_json(silent=True) or {}
    profile_id = payload.get("profile_id")
    pin = (payload.get("pin") or "").strip()
    location_id = payload.get("location_id")
    success_flag = payload.get("success_flag")
    scene_id = payload.get("scene_id")
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    accuracy_m = payload.get("accuracy_m")
    precision = payload.get("precision")

    if not profile_id or not location_id:
        return jsonify({"error": "missing_fields"}), 400

    try:
        result = services.complete_location_check(
            profile_id,
            pin,
            location_id,
            success_flag=success_flag,
            latitude=latitude,
            longitude=longitude,
            accuracy_m=accuracy_m,
            precision=precision,
            scene_id=scene_id,
        )
    except services.GeocacheServiceError as exc:
        return jsonify(exc.payload), exc.status_code

    return jsonify(result)
