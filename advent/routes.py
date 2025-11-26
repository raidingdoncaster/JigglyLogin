"""Admin-only Advent Blueprint (drop the admin guard when moving to player dashboard)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional, Union

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from advent.service import (
    get_advent_state_for_user,
    load_advent_config,
    open_advent_day,
)

AdminProvider = Callable[[], Optional[dict]]


def create_advent_blueprint(current_admin_provider: AdminProvider) -> Blueprint:
    """Factory so the main app can inject its session-based admin lookup."""

    bp = Blueprint("admin_advent", __name__, url_prefix="/admin")

    def _require_admin() -> Union[dict, object]:
        user = current_admin_provider()
        if user:
            return user
        flash("Admin access required to test the Advent Calendar.", "error")
        return redirect(url_for("admin_login"))

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
        admin_user = _require_admin()
        if not isinstance(admin_user, dict):
            return admin_user

        day_override = request.args.get("day_override")
        today_day, day_error = _resolve_day(day_override)
        if day_error:
            flash(day_error, "warning")
            day_override = None

        user_id = _extract_user_id(admin_user)
        if not user_id:
            flash("Admin user is missing an ID — cannot load Advent state.", "error")
            return redirect(url_for("admin_dashboard"))

        try:
            config = load_advent_config()
        except FileNotFoundError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin_dashboard"))

        state = get_advent_state_for_user(user_id, today_day)

        return render_template(
            "admin/advent_calendar.html",
            config=config,
            effective_day=today_day,
            day_override=day_override,
            state=state,
        )

    def _wants_json() -> bool:
        accepts = request.accept_mimetypes
        return (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or accepts["application/json"] >= accepts["text/html"]
        )

    @bp.post("/advent/open/<int:day>")
    def open_day(day: int):
        admin_user = _require_admin()
        if not isinstance(admin_user, dict):
            return admin_user

        json_mode = _wants_json()
        raw_override = request.form.get("day_override") or request.args.get("day_override")
        today_day, day_error = _resolve_day(raw_override)
        if day_error:
            flash(day_error, "warning")
            raw_override = None

        user_id = _extract_user_id(admin_user)
        if not user_id:
            msg = "Admin user is missing an ID — cannot open Advent day."
            if json_mode:
                return jsonify({"status": "error", "reason": msg}), 403
            flash(msg, "error")
            return redirect(url_for("admin_advent.view_calendar"))

        try:
            config = load_advent_config()
        except FileNotFoundError as exc:
            if json_mode:
                return jsonify({"status": "error", "reason": str(exc)}), 500
            flash(str(exc), "error")
            return _redirect_back(raw_override)

        state = get_advent_state_for_user(user_id, today_day)
        openable_day = state.get("openable_day")

        if openable_day != day:
            msg = "You can only open the next available Advent day."
            if json_mode:
                return jsonify({"status": "error", "reason": msg}), 400
            flash(msg, "error")
            return _redirect_back(raw_override)

        if open_advent_day(user_id, day):
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
            flash(f"Day {day} unlocked!", "success")
        else:
            msg = "Day was already opened or could not be saved."
            if json_mode:
                return jsonify({"status": "error", "reason": msg}), 400
            flash(msg, "warning")

        return _redirect_back(raw_override)

    def _redirect_back(day_override: Optional[str]):
        target = url_for("admin_advent.view_calendar")
        if day_override:
            target = f"{target}?day_override={day_override}"
        return redirect(target)

    return bp
