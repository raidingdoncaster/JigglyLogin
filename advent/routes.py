"""Admin-only Advent Blueprint (drop the admin guard when moving to player dashboard)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional, Union

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from advent.service import (
    get_advent_state_for_user,
    load_advent_config,
    open_advent_day,
)

AdminProvider = Callable[[], Optional[dict]]


def create_advent_blueprint(current_admin_provider: AdminProvider) -> Blueprint:
    """Factory so the main app can inject its session-based admin lookup."""

    return _create_shared_advent_blueprint(
        blueprint_name="admin_advent",
        url_prefix="/admin",
        template_name="advent/calendar.html",
        current_user_provider=current_admin_provider,
        unauthorized_message="Admin access required to test the Advent Calendar.",
        unauthorized_redirect_endpoint="admin_login",
        unauthorized_status_code=403,
        store_last_page=False,
        day_override_enabled=True,
        missing_id_load_message="Admin user is missing an ID — cannot load Advent state.",
        missing_id_open_message="Admin user is missing an ID — cannot open Advent day.",
        dashboard_endpoint="admin_dashboard",
        success_flash_template="Day {day} unlocked!",
    )


def create_player_advent_blueprint(current_trainer_provider: AdminProvider) -> Blueprint:
    """Expose the Advent calendar to logged-in trainers."""

    return _create_shared_advent_blueprint(
        blueprint_name="player_advent",
        url_prefix=None,
        template_name="advent/calendar.html",
        current_user_provider=current_trainer_provider,
        unauthorized_message="Please log in to open your Advent Calendar.",
        unauthorized_redirect_endpoint="login",
        unauthorized_status_code=401,
        store_last_page=True,
        day_override_enabled=False,
        missing_id_load_message="Trainer account is missing an ID — cannot load Advent state.",
        missing_id_open_message="Trainer account is missing an ID — cannot open Advent day.",
        dashboard_endpoint="dashboard",
        success_flash_template=None,
    )


def _create_shared_advent_blueprint(
    *,
    blueprint_name: str,
    url_prefix: str,
    template_name: str,
    current_user_provider: AdminProvider,
    unauthorized_message: str,
    unauthorized_redirect_endpoint: str,
    unauthorized_status_code: int,
    store_last_page: bool,
    day_override_enabled: bool,
    missing_id_load_message: str,
    missing_id_open_message: str,
    dashboard_endpoint: str,
    success_flash_template: Optional[str],
) -> Blueprint:
    bp = Blueprint(blueprint_name, __name__, url_prefix=url_prefix)

    def _require_user(json_mode: bool) -> Union[dict, object]:
        user = current_user_provider()
        if user:
            return user
        if json_mode:
            return (
                jsonify({"status": "error", "reason": unauthorized_message}),
                unauthorized_status_code,
            )
        if store_last_page:
            session["last_page"] = request.path
        flash(unauthorized_message, "error")
        return redirect(url_for(unauthorized_redirect_endpoint))

    def _resolve_day(raw_value: Optional[str]) -> tuple[int, Optional[str]]:
        if raw_value is None or raw_value == "":
            return _default_day(), None
        try:
            candidate = int(raw_value)
        except (TypeError, ValueError):
            return _default_day(), "Day override must be a number between 1 and 25."
        if not 1 <= candidate <= 25:
            return _default_day(), "Day override must be between 1 and 25."
        return candidate, None

    def _default_day() -> int:
        now = datetime.now(tz=timezone.utc)
        if now.month == 12:
            return max(1, min(now.day, 25))
        return 25

    def _extract_user_id(user: dict) -> Optional[int]:
        try:
            return int(user.get("id"))
        except (TypeError, ValueError):
            return None

    @bp.get("/advent")
    def view_calendar():
        user = _require_user(json_mode=False)
        if not isinstance(user, dict):
            return user

        day_override_raw = request.args.get("day_override") if day_override_enabled else None
        today_day, day_error = (
            _resolve_day(day_override_raw) if day_override_enabled else (_default_day(), None)
        )
        if day_error:
            flash(day_error, "warning")
            day_override_raw = None

        user_id = _extract_user_id(user)
        if not user_id:
            flash(missing_id_load_message, "error")
            return redirect(url_for(dashboard_endpoint))

        try:
            config = load_advent_config()
        except FileNotFoundError as exc:
            flash(str(exc), "error")
            return redirect(url_for(dashboard_endpoint))

        state = get_advent_state_for_user(user_id, today_day)

        return render_template(
            template_name,
            config=config,
            effective_day=today_day,
            day_override=day_override_raw if day_override_enabled else None,
            state=state,
            admin_mode=day_override_enabled,
            open_endpoint=f"{blueprint_name}.open_day",
        )

    def _wants_json() -> bool:
        accepts = request.accept_mimetypes
        return (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or accepts["application/json"] >= accepts["text/html"]
        )

    @bp.post("/advent/open/<int:day>")
    def open_day(day: int):
        json_mode = _wants_json()
        user = _require_user(json_mode=json_mode)
        if not isinstance(user, dict):
            return user

        raw_override = (
            request.form.get("day_override") or request.args.get("day_override")
            if day_override_enabled
            else None
        )
        today_day, day_error = (
            _resolve_day(raw_override) if day_override_enabled else (_default_day(), None)
        )
        if day_error:
            flash(day_error, "warning")
            raw_override = None

        user_id = _extract_user_id(user)
        if not user_id:
            if json_mode:
                return jsonify({"status": "error", "reason": missing_id_open_message}), 403
            flash(missing_id_open_message, "error")
            return redirect(url_for(f".view_calendar"))

        try:
            config = load_advent_config()
        except FileNotFoundError as exc:
            if json_mode:
                return jsonify({"status": "error", "reason": str(exc)}), 500
            flash(str(exc), "error")
            return _redirect_back(raw_override)

        state = get_advent_state_for_user(user_id, today_day)
        openable_day = state.get("openable_day")
        trainer_username = user.get("trainer_username") if isinstance(user, dict) else None

        if openable_day != day:
            msg = "You can only open the next available Advent day."
            if json_mode:
                return jsonify({"status": "error", "reason": msg}), 400
            flash(msg, "error")
            return _redirect_back(raw_override)

        if open_advent_day(user_id, day, trainer_username):
            state = get_advent_state_for_user(user_id, today_day)
            payload = {
                "status": "ok",
                "day": day,
                "message": (config.get(day) or {}).get("message", ""),
                "stamp_png": (config.get(day) or {}).get("stamp_png", ""),
                "next_openable_day": state.get("openable_day"),
            }
            if json_mode:
                return jsonify(payload)
            if success_flash_template:
                flash(success_flash_template.format(day=day), "success")
        else:
            msg = "Day was already opened or could not be saved."
            if json_mode:
                return jsonify({"status": "error", "reason": msg}), 400
            flash(msg, "warning")

        return _redirect_back(raw_override)

    def _redirect_back(day_override: Optional[str]):
        target = url_for(".view_calendar")
        if day_override and day_override_enabled:
            target = f"{target}?day_override={day_override}"
        return redirect(target)

    return bp
