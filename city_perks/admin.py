"""Admin blueprint for creating, editing, and listing City Perks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import or_

from extensions import db
from models import CityPerk
from .sync import ensure_city_perks_cache, mark_city_perks_cache_stale

AdminProvider = Callable[[], Optional[dict]]
AdminGuard = Callable[[Callable], Callable]
UploadHelper = Callable[..., Optional[str]]

STATUS_CHOICES = [
    ("all", "All"),
    ("live", "Live"),
    ("scheduled", "Scheduled"),
    ("expired", "Expired"),
    ("inactive", "Inactive"),
]
STATUS_KEYS = {choice[0] for choice in STATUS_CHOICES}

PERK_MODES = ["in_store", "online", "hybrid"]

BOOLEAN_FIELDS = ("is_active", "show_on_map")

CITY_PERKS_FOLDER = "city-perks"


def create_city_perks_admin_blueprint(
    admin_required: AdminGuard,
    admin_user_provider: AdminProvider,
    upload_helper: UploadHelper,
    supabase_client,
) -> Blueprint:
    """Factory so we can re-use app.py's admin gate decorator."""

    if not callable(upload_helper):
        raise ValueError("upload_helper must be provided for City Perks media uploads.")

    bp = Blueprint("admin_city_perks", __name__, url_prefix="/admin/city-perks")

    @bp.route("", methods=["GET"])
    @bp.route("/", methods=["GET"])
    @admin_required
    def list_city_perks():
        ensure_city_perks_cache()
        now = _now()
        status_filter = _normalize_status(request.args.get("status", "all"))
        area_filter = _clean_or_none(request.args.get("area"))
        category_filter = _clean_or_none(request.args.get("category"))
        search_term = (request.args.get("q") or "").strip()

        query = CityPerk.query
        query = _apply_status_filter(query, status_filter, now)
        if area_filter:
            query = query.filter(CityPerk.area == area_filter)
        if category_filter:
            query = query.filter(CityPerk.category == category_filter)
        if search_term:
            like = f"%{search_term.lower()}%"
            query = query.filter(
                or_(
                    db.func.lower(CityPerk.name).like(like),
                    db.func.lower(CityPerk.partner_name).like(like),
                )
            )

        perks = query.order_by(
            CityPerk.start_date.desc(),
            CityPerk.name.asc(),
        ).all()

        areas = _distinct_values(CityPerk.area)
        categories = _distinct_values(CityPerk.category)

        return render_template(
            "admin/city_perks_list.html",
            perks=perks,
            now=now,
            status_filter=status_filter,
            area_filter=area_filter,
            category_filter=category_filter,
            search_term=search_term,
            status_choices=STATUS_CHOICES,
            areas=areas,
            categories=categories,
            perk_modes=CityPerk.MODE_LABELS,
        )

    @bp.route("/new", methods=["GET", "POST"])
    @admin_required
    def create_city_perk():
        form_values = _empty_form_values()
        errors: list[str] = []
        if request.method == "POST":
            form_values = _form_values_from_request()
            logo_file = request.files.get("logo_file")
            cover_file = request.files.get("cover_file")
            payload, errors = _validate_and_normalize(form_values)
            if not errors:
                errors.extend(
                    _apply_media_uploads(payload, logo_file, cover_file, upload_helper)
                )
            if not errors:
                perk = CityPerk(**payload)
                admin_user = admin_user_provider()
                if admin_user:
                    try:
                        perk.created_by_admin_id = int(admin_user.get("id"))
                    except (TypeError, ValueError):
                        perk.created_by_admin_id = None
                db.session.add(perk)
                if _commit_session("creating city perk"):
                    db.session.refresh(perk)
                    _sync_city_perk_to_supabase(perk, supabase_client)
                    mark_city_perks_cache_stale()
                    flash("City perk created.", "success")
                    return redirect(url_for("admin_city_perks.list_city_perks"))
                errors.append("Could not save the city perk. Please try again.")
        return render_template(
            "admin/city_perks_form.html",
            form_values=form_values,
            errors=errors,
            perk=None,
            is_edit=False,
            perk_modes=PERK_MODES,
        )

    @bp.route("/<int:perk_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_city_perk(perk_id: int):
        perk = CityPerk.query.get_or_404(perk_id)
        form_values = _form_values_from_perk(perk)
        errors: list[str] = []

        if request.method == "POST":
            form_values = _form_values_from_request()
            logo_file = request.files.get("logo_file")
            cover_file = request.files.get("cover_file")
            payload, errors = _validate_and_normalize(form_values, existing=perk)
            if not errors:
                errors.extend(
                    _apply_media_uploads(payload, logo_file, cover_file, upload_helper)
                )
            if not errors:
                for key, value in payload.items():
                    setattr(perk, key, value)
                if _commit_session(f"updating city perk {perk_id}"):
                    db.session.refresh(perk)
                    _sync_city_perk_to_supabase(perk, supabase_client)
                    mark_city_perks_cache_stale()
                    flash("City perk updated.", "success")
                    return redirect(url_for("admin_city_perks.list_city_perks"))
                errors.append("Could not save your changes. Please try again.")

        return render_template(
            "admin/city_perks_form.html",
            form_values=form_values,
            errors=errors,
            perk=perk,
            is_edit=True,
            perk_modes=PERK_MODES,
        )

    return bp


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _apply_status_filter(query, status: str, now: datetime):
    if status == "live":
        query = query.filter(
        CityPerk.is_active.is_(True),
        CityPerk.start_date <= now,
        or_(CityPerk.end_date.is_(None), CityPerk.end_date >= now),
        )
    elif status == "scheduled":
        query = query.filter(
            CityPerk.is_active.is_(True),
            CityPerk.start_date > now,
        )
    elif status == "expired":
        query = query.filter(
            CityPerk.end_date.is_not(None),
            CityPerk.end_date < now,
        )
    elif status == "inactive":
        query = query.filter(CityPerk.is_active.is_(False))
    return query


def _distinct_values(column):
    rows = (
        db.session.query(column)
        .filter(column.is_not(None))
        .distinct()
        .order_by(column.asc())
        .all()
    )
    return [value for (value,) in rows if value]


def _normalize_status(value: Optional[str]) -> str:
    value = (value or "all").lower()
    if value not in STATUS_KEYS:
        return "all"
    return value


def _clean_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _empty_form_values() -> dict:
    return {
        "name": "",
        "partner_name": "",
        "category": "",
        "area": "",
        "short_tagline": "",
        "description_long": "",
        "perk_mode": "in_store",
        "address": "",
        "latitude": "",
        "longitude": "",
        "google_maps_link": "",
        "apple_maps_link": "",
        "website_url": "",
        "offer_type": "",
        "offer_text": "",
        "start_date": "",
        "end_date": "",
        "is_active": True,
        "show_on_map": True,
        "logo_url": "",
        "cover_image_url": "",
        "notes_internal": "",
    }


def _form_values_from_perk(perk: CityPerk) -> dict:
    values = _empty_form_values()
    values.update(
        {
            "name": perk.name or "",
            "partner_name": perk.partner_name or "",
            "category": perk.category or "",
            "area": perk.area or "",
            "short_tagline": perk.short_tagline or "",
            "description_long": perk.description_long or "",
            "perk_mode": perk.perk_mode or "in_store",
            "address": perk.address or "",
            "latitude": _format_float(perk.latitude),
            "longitude": _format_float(perk.longitude),
            "google_maps_link": perk.google_maps_link or "",
            "apple_maps_link": perk.apple_maps_link or "",
            "website_url": perk.website_url or "",
            "offer_type": perk.offer_type or "",
            "offer_text": perk.offer_text or "",
            "start_date": _format_datetime_input(perk.start_date),
            "end_date": _format_datetime_input(perk.end_date),
            "is_active": bool(perk.is_active),
            "show_on_map": bool(perk.show_on_map),
            "logo_url": perk.logo_url or "",
            "cover_image_url": perk.cover_image_url or "",
            "notes_internal": perk.notes_internal or "",
        }
    )
    return values


def _form_values_from_request() -> dict:
    values = _empty_form_values()
    for key in values:
        if key in BOOLEAN_FIELDS:
            continue
        values[key] = request.form.get(key, "").strip()
    for key in BOOLEAN_FIELDS:
        values[key] = bool(request.form.get(key))
    return values


def _validate_and_normalize(form_values: dict, existing: Optional[CityPerk] = None):
    errors: list[str] = []

    name = form_values["name"].strip()
    partner_name = form_values["partner_name"].strip()
    category = form_values["category"].strip()
    perk_mode = form_values["perk_mode"].strip() or "in_store"
    start_date_raw = form_values["start_date"]
    end_date_raw = form_values["end_date"]

    if not name:
        errors.append("Name is required.")
    if not partner_name:
        errors.append("Partner name is required.")
    if not category:
        errors.append("Category is required.")
    if perk_mode not in PERK_MODES:
        errors.append("Perk mode must be one of: in_store, online, hybrid.")

    start_dt = _parse_datetime_field(start_date_raw, "Start date", required=True, errors=errors)
    end_dt = _parse_datetime_field(end_date_raw, "End date", required=False, errors=errors)
    if start_dt and end_dt and end_dt < start_dt:
        errors.append("End date cannot be before the start date.")

    website_url = form_values["website_url"].strip()
    address = form_values["address"].strip()

    if perk_mode in {"online", "hybrid"} and not website_url:
        errors.append("Website URL is required for online or hybrid perks.")
    if perk_mode in {"in_store", "hybrid"} and not address:
        errors.append("Address is required for in-store or hybrid perks.")

    latitude = _parse_float(form_values["latitude"], "Latitude", errors)
    longitude = _parse_float(form_values["longitude"], "Longitude", errors)
    show_on_map = bool(form_values["show_on_map"])
    if show_on_map and (latitude is None or longitude is None):
        errors.append("Latitude and longitude are required when Show on map is enabled.")

    payload = {
        "name": name,
        "partner_name": partner_name,
        "category": category,
        "area": form_values["area"].strip() or None,
        "short_tagline": form_values["short_tagline"].strip() or None,
        "description_long": form_values["description_long"].strip() or None,
        "perk_mode": perk_mode,
        "address": address or None,
        "latitude": latitude,
        "longitude": longitude,
        "google_maps_link": form_values["google_maps_link"].strip() or None,
        "apple_maps_link": form_values["apple_maps_link"].strip() or None,
        "website_url": website_url or None,
        "offer_type": form_values["offer_type"].strip() or None,
        "offer_text": form_values["offer_text"].strip() or None,
        "start_date": start_dt,
        "end_date": end_dt,
        "is_active": bool(form_values["is_active"]),
        "show_on_map": show_on_map,
        "logo_url": form_values["logo_url"].strip() or None,
        "cover_image_url": form_values["cover_image_url"].strip() or None,
        "notes_internal": form_values["notes_internal"].strip() or None,
    }

    return payload, errors


def _parse_datetime_field(raw_value: str, label: str, required: bool, errors: list[str]):
    if not raw_value:
        if required:
            errors.append(f"{label} is required.")
        return None
    try:
        candidate = datetime.fromisoformat(raw_value)
    except ValueError:
        errors.append(f"{label} must be a valid ISO date/time.")
        return None
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    else:
        candidate = candidate.astimezone(timezone.utc)
    return candidate


def _format_datetime_input(value: Optional[datetime]) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(timezone.utc)
    return local.strftime("%Y-%m-%dT%H:%M")


def _format_float(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _parse_float(raw: str, label: str, errors: list[str]) -> Optional[float]:
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        errors.append(f"{label} must be a number.")
        return None


def _commit_session(context_message: str) -> bool:
    try:
        db.session.commit()
        return True
    except Exception as exc:  # pragma: no cover - admin convenience
        db.session.rollback()
        current_app.logger.error("CityPerks admin error while %s: %s", context_message, exc)
        return False


def _apply_media_uploads(payload: dict, logo_file, cover_file, upload_helper: UploadHelper) -> list[str]:
    """Upload optional media files and update payload with Supabase URLs."""
    errors: list[str] = []
    uploads = [
        (logo_file, "logos", "logo", "logo_url"),
        (cover_file, "covers", "cover image", "cover_image_url"),
    ]
    for file_storage, subfolder, label, payload_key in uploads:
        if not file_storage or not getattr(file_storage, "filename", ""):
            continue
        folder = f"{CITY_PERKS_FOLDER}/{subfolder}"
        uploaded_url = upload_helper(file_storage, folder=folder)
        if uploaded_url:
            payload[payload_key] = uploaded_url
        else:
            errors.append(f"Failed to upload {label}. Please try again.")
    return errors


def _sync_city_perk_to_supabase(perk: CityPerk, supabase_client) -> None:
    """Mirror the SQLAlchemy record into Supabase."""
    if not supabase_client:
        return
    payload = _city_perk_to_supabase_row(perk)
    try:
        supabase_client.table("city_perks").upsert(payload).execute()
    except Exception as exc:  # pragma: no cover - remote sync best effort
        current_app.logger.error("CityPerks Supabase sync failed for %s: %s", perk.id, exc)


def _city_perk_to_supabase_row(perk: CityPerk) -> dict:
    def _iso(dt):
        if not dt:
            return None
        value = dt
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    return {
        "id": perk.id,
        "name": perk.name,
        "partner_name": perk.partner_name,
        "category": perk.category,
        "area": perk.area,
        "short_tagline": perk.short_tagline,
        "description_long": perk.description_long,
        "perk_mode": perk.perk_mode,
        "address": perk.address,
        "latitude": perk.latitude,
        "longitude": perk.longitude,
        "google_maps_link": perk.google_maps_link,
        "apple_maps_link": perk.apple_maps_link,
        "website_url": perk.website_url,
        "offer_type": perk.offer_type,
        "offer_text": perk.offer_text,
        "start_date": _iso(perk.start_date),
        "end_date": _iso(perk.end_date),
        "is_active": perk.is_active,
        "show_on_map": perk.show_on_map,
        "logo_url": perk.logo_url,
        "cover_image_url": perk.cover_image_url,
        "created_at": _iso(perk.created_at),
        "updated_at": _iso(perk.updated_at),
        "created_by_admin_id": perk.created_by_admin_id,
        "notes_internal": perk.notes_internal,
    }
