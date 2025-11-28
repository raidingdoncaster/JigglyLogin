import os
import copy
import hashlib
import json
import requests
import uuid
import re
from collections import Counter, defaultdict
from flask import Flask, render_template, abort, request, redirect, url_for, session, flash, send_from_directory, jsonify, g, make_response, current_app
from werkzeug.utils import secure_filename
from PIL import Image
from pywebpush import webpush, WebPushException
import pytesseract
from datetime import datetime, date, timezone, timedelta
from dateutil import parser
import io, base64, time
from markupsafe import Markup
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, quote_plus
import bleach
from bleach.linkifier import DEFAULT_CALLBACKS
from typing import Any, Optional
from sqlalchemy import or_

from rdab.trainer_detection import extract_trainer_name
from extensions import db
from advent import create_advent_blueprint, create_player_advent_blueprint
from advent.service import load_advent_config
from city_perks import (
    city_perks_api_blueprint,
    create_city_perks_admin_blueprint,
    ensure_city_perks_cache,
)
from models import CityPerk
from content_filter import ContentFilter

# ====== Feature toggle ======
def _env_flag(name: str, default: bool) -> bool:
    """Parse truthy feature-toggle values from the environment."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


USE_SUPABASE = _env_flag("USE_SUPABASE", True)  # ✅ Supabase for stamps/meetups
MAINTENANCE_MODE = _env_flag("MAINTENANCE_MODE", False)  # ⛔️ Change to True to enable maintenance mode
USE_GEOCACHE_QUEST = _env_flag("USE_GEOCACHE_QUEST", False)  # 🧭 Toggle Geocache quest endpoints

# ====== Trainer metadata ======
TEAM_CONFIG = {
    "valor": {"label": "Team Valor", "icon": "passport/themes/valoricon.png"},
    "mystic": {"label": "Team Mystic", "icon": "passport/themes/mysticicon.png"},
    "instinct": {"label": "Team Instinct", "icon": "passport/themes/instincticon.png"},
}
MAX_TRAINER_LEVEL = 80

# ====== Auth security settings ======
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 1800

# ====== Admin dashboard security ======
HARD_CODED_ADMIN_DASHBOARD_PASSWORD = "shinypsyduck"

ADMIN_DASHBOARD_PASSWORD = os.environ.get("ADMIN_DASHBOARD_PASSWORD")
if ADMIN_DASHBOARD_PASSWORD is not None:
    ADMIN_DASHBOARD_PASSWORD = ADMIN_DASHBOARD_PASSWORD.strip() or None
if not ADMIN_DASHBOARD_PASSWORD:
    ADMIN_DASHBOARD_PASSWORD = HARD_CODED_ADMIN_DASHBOARD_PASSWORD

ADMIN_DASHBOARD_PASSWORD_HASH = os.environ.get("ADMIN_DASHBOARD_PASSWORD_HASH")
if ADMIN_DASHBOARD_PASSWORD_HASH is not None:
    ADMIN_DASHBOARD_PASSWORD_HASH = ADMIN_DASHBOARD_PASSWORD_HASH.strip() or None

ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS = 5
_gate_attempts_env = os.environ.get("ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS")
if _gate_attempts_env:
    try:
        ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS = max(1, int(_gate_attempts_env))
    except ValueError:
        print(f"⚠️ Invalid ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS value: {_gate_attempts_env!r}. Using default {ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS}.")

ADMIN_DASHBOARD_GATE_LOCK_SECONDS = 900
_gate_lock_env = os.environ.get("ADMIN_DASHBOARD_GATE_LOCK_SECONDS")
if _gate_lock_env:
    try:
        ADMIN_DASHBOARD_GATE_LOCK_SECONDS = max(30, int(_gate_lock_env))
    except ValueError:
        print(f"⚠️ Invalid ADMIN_DASHBOARD_GATE_LOCK_SECONDS value: {_gate_lock_env!r}. Using default {ADMIN_DASHBOARD_GATE_LOCK_SECONDS}.")

ADMIN_DASHBOARD_GATE_TTL_SECONDS = 4200
_gate_ttl_env = os.environ.get("ADMIN_DASHBOARD_GATE_TTL_SECONDS")
if _gate_ttl_env:
    try:
        ADMIN_DASHBOARD_GATE_TTL_SECONDS = max(0, int(_gate_ttl_env))
    except ValueError:
        print(f"⚠️ Invalid ADMIN_DASHBOARD_GATE_TTL_SECONDS value: {_gate_ttl_env!r}. Using default {ADMIN_DASHBOARD_GATE_TTL_SECONDS}.")

# ====== Dashboard feature visibility toggles ======
SHOW_CATALOG_APP = True
SHOW_CITY_PERKS_APP = True
SHOW_CITY_GUIDES_APP = False
SHOW_LEAGUES_APP = False

ABOUT_EMBED_URL = "https://www.canva.com/design/DAGxH_O6jbA/-X2WI4vn30ls9-KMaQ0ecQ/view?embed"
ABOUT_SOURCE_URL = (
    "https://www.canva.com/design/DAGxH_O6jbA/-X2WI4vn30ls9-KMaQ0ecQ/"
    "view?utm_content=DAGxH_O6jbA&utm_campaign=designshare&utm_medium=embeds&utm_source=link"
)
ABOUT_CREDIT_TITLE = "Raiding Doncaster and Beyond"
ABOUT_CREDIT_AUTHOR = "admin"

# Try to import Supabase client
try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client, Client = None, None

# ====== Flask setup ======
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.permanent_session_lifetime = timedelta(days=365)
app.config.setdefault("USE_SUPABASE", USE_SUPABASE)
app.config.setdefault("USE_GEOCACHE_QUEST", USE_GEOCACHE_QUEST)

CONTENT_FILTER = ContentFilter()

@app.errorhandler(404)
@app.errorhandler(500)
def show_custom_error_page(err):
    status_code = getattr(err, "code", 500) or 500
    return render_template("error.html"), status_code

@app.before_request
def check_maintenance_mode():
    from flask import request, render_template

    endpoint = request.endpoint or ""

    allowed_when_locked = {
        "static",
        "manifest",
        "service_worker",
        "maintenance",
        "home",
    }
    if endpoint in allowed_when_locked:
        return

    # If maintenance mode is on, show maintenance page
    if MAINTENANCE_MODE:
        return render_template("maintenance.html"), 503

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_CLASSIC_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif"}
CLASSIC_SUBMISSION_STATUSES = {"PENDING", "AWARDED", "REJECTED"}

# ====== Passport theme settings ======
def _slugify_theme_value(raw: str) -> str:
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


PASSPORT_THEMES = [
    {
        "slug": "passport_light",
        "label": "Passport Light",
        "description": "Bright parchment finish with airy highlights.",
        "preview_colors": ["#fef3c7", "#2563eb"],
        "icon": None,
        "flag": None,
        "summary": None,
        "glow": "#fef3c7",
    },
    {
        "slug": "passport_dark",
        "label": "Passport Dark",
        "description": "Moody midnight styling with deep blues.",
        "preview_colors": ["#0f172a", "#3b82f6"],
        "icon": None,
        "flag": None,
        "summary": None,
        "glow": "#3b82f6",
    },
    {
        "slug": "team_valor",
        "label": "Team Valor",
        "description": "Bold ember tones inspired by Valor pride.",
        "preview_colors": ["#7f1d1d", "#f87171"],
        "icon": "passport/themes/valoricon.png",
        "flag": "passport/themes/vflag.png",
        "summary": "Team Valor relies on strength in battle. Valor's members believe that Pokémon are stronger and more warmhearted than humans and are interested in enhancing their natural power.",
        "glow": "#f87171",
    },
    {
        "slug": "team_mystic",
        "label": "Team Mystic",
        "description": "Cool aurora wash for Mystic strategists.",
        "preview_colors": ["#0f172a", "#60a5fa"],
        "icon": "passport/themes/mysticicon.png",
        "flag": "passport/themes/mflag.png",
        "summary": "Team Mystic relies on analyzing every situation. Mystic's members believe that Pokémon have immeasurable wisdom and are interested in learning more about why Pokémon experience evolution.",
        "glow": "#a5d8ff",
    },
    {
        "slug": "team_instinct",
        "label": "Team Instinct",
        "description": "Vibrant electric hues for Instinct explorers.",
        "preview_colors": ["#78350f", "#facc15"],
        "icon": "passport/themes/instincticon.png",
        "flag": "passport/themes/iflag.png",
        "summary": "Team Instinct relies on a Trainer's instincts. Instinct's members believe that Pokémon have excellent intuition and are interested in learning more about its connection to the egg hatching process.",
        "glow": "#facc15",
    },
]

PASSPORT_THEME_BY_SLUG = {theme["slug"]: theme for theme in PASSPORT_THEMES}
DEFAULT_PASSPORT_THEME = PASSPORT_THEMES[0]["slug"]


def normalize_passport_theme(raw_value: Optional[str]) -> str:
    """Return a valid passport theme slug that matches available themes."""

    if not raw_value:
        return DEFAULT_PASSPORT_THEME

    normalized = _slugify_theme_value(raw_value)
    if not normalized:
        return DEFAULT_PASSPORT_THEME

    for theme in PASSPORT_THEMES:
        theme_keys = {
            _slugify_theme_value(theme["slug"]),
            _slugify_theme_value(theme["label"]),
        }
        if normalized in theme_keys:
            return theme["slug"]

    return DEFAULT_PASSPORT_THEME


def get_passport_theme_data(slug: Optional[str] = None) -> dict:
    """Return the structured metadata for a passport theme slug."""

    normalized = normalize_passport_theme(slug)
    return PASSPORT_THEME_BY_SLUG.get(normalized, PASSPORT_THEME_BY_SLUG[DEFAULT_PASSPORT_THEME])

# ====== RDAB Community Bulletin ======

COMMUNITY_BULLETIN_POSTS: list[dict] = []
MAX_BULLETIN_COMMENT_DEPTH = 2  # depth 0 = root, depth 2 = deepest nested reply


def get_community_bulletin_posts(include_sections: bool = False) -> list[dict]:
    """Return newest-first RDAB Community Bulletin entries."""

    posts = fetch_bulletin_posts_from_supabase(include_sections=include_sections)
    if not posts:
        posts = copy.deepcopy(COMMUNITY_BULLETIN_POSTS)
    posts.sort(key=lambda post: parse_dt_safe(post.get("published_at")), reverse=True)
    return posts


def get_community_bulletin_post(slug: str) -> Optional[dict]:
    """Return a single bulletin post by slug, if present."""

    supa_post = fetch_bulletin_post_from_supabase(slug, include_sections=True)
    if supa_post:
        return supa_post
    for post in get_community_bulletin_posts():
        if (post.get("slug") or "").lower() == slug.lower():
            return post
    return None


def _bulletin_supabase_enabled() -> bool:
    return bool(USE_SUPABASE and supabase)


def _bulletin_author_from_row(row: dict) -> tuple[str, dict]:
    author = row.get("author") or {}
    display_name = author.get("display_name") or row.get("author_name") or "RDAB Staff"
    mapped = {
        "author": display_name,
        "author_avatar": author.get("avatar_url") or row.get("author_avatar"),
        "author_bio": author.get("bio") or row.get("author_bio"),
        "author_link": author.get("link_url") or row.get("author_link"),
        "social_handles": author.get("social_handles") or row.get("social_handles") or [],
    }
    return display_name, mapped


def _normalize_supabase_sections(section_rows: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for row in sorted(section_rows, key=lambda r: r.get("section_order", 0)):
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        payload_copy = copy.deepcopy(payload)
        section = {
            "id": row.get("id"),
            "title": row.get("heading") or payload.get("title"),
            "type": row.get("section_type"),
            "section_type": row.get("section_type"),
            "payload": payload_copy,
        }
        # expose payload keys (body, details, etc.) at the top level for templates
        for key, value in payload_copy.items():
            section.setdefault(key, value)
        normalized.append(section)
    return normalized


def _normalize_supabase_post(row: dict, include_sections: bool = False) -> dict:
    post = {
        "id": row.get("id"),
        "slug": row.get("slug"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "hero_image": row.get("hero_image"),
        "header_image": row.get("header_image"),
        "category": row.get("category"),
        "tag": row.get("tag"),
        "read_time": row.get("read_time"),
        "is_featured": row.get("is_featured"),
        "published_at": row.get("published_at"),
        "like_count": row.get("like_count") or 0,
        "status": row.get("status"),
        "author_id": row.get("author_id"),
        "scheduled_publish_at": row.get("scheduled_publish_at"),
    }
    _, author_meta = _bulletin_author_from_row(row)
    post.update(author_meta)
    tags = row.get("tags") or []
    if tags and isinstance(tags[0], dict):
        post["tags"] = [t.get("tag") for t in tags if t.get("tag")]
    else:
        post["tags"] = tags
    if include_sections:
        post["content_sections"] = _normalize_supabase_sections(row.get("sections") or [])
    else:
        post["content_sections"] = []
    return post


def fetch_bulletin_posts_from_supabase(include_sections: bool = False, status: Optional[str] = "published") -> list[dict]:
    if not _bulletin_supabase_enabled():
        return []
    select_cols = [
        "*",
        "author:bulletin_authors(*)",
        "tags:bulletin_post_tags(tag)",
    ]
    if include_sections:
        select_cols.append("sections:bulletin_post_sections(*)")
    query = supabase.table("bulletin_posts").select(",".join(select_cols)).order("published_at", desc=True)
    if status:
        query = query.eq("status", status)
    try:
        data = query.execute().data or []
    except Exception as exc:
        print("⚠️ Supabase bulletin posts fetch failed:", exc)
        return []
    return [_normalize_supabase_post(row, include_sections=include_sections) for row in data]


def fetch_bulletin_post_from_supabase(slug: str, include_sections: bool = True) -> Optional[dict]:
    if not _bulletin_supabase_enabled():
        return None
    select_cols = [
        "*",
        "author:bulletin_authors(*)",
        "tags:bulletin_post_tags(tag)",
    ]
    if include_sections:
        select_cols.append("sections:bulletin_post_sections(*)")
    try:
        resp = (supabase.table("bulletin_posts")
                .select(",".join(select_cols))
                .eq("slug", slug)
                .limit(1)
                .execute())
        rows = resp.data or []
    except Exception as exc:
        print("⚠️ Supabase bulletin post fetch failed:", exc)
        return None
    if not rows:
        return None
    return _normalize_supabase_post(rows[0], include_sections=include_sections)


def _memory_section_identifier(post: dict, section: dict, index: int) -> str:
    """Return a deterministic identifier for a bulletin section."""
    slug = (post.get("slug") or "post").replace(" ", "-")
    section_id = section.get("id")
    if section_id:
        return str(section_id)
    return f"{slug}-section-{index + 1}"


def _build_memory_album(post: dict, sections: Optional[list[dict]] = None) -> Optional[dict]:
    """Collate carousel slides and media blocks into a single memory album payload."""

    sections = sections if sections is not None else (post.get("content_sections") or [])
    slides: list[dict] = []
    section_offsets: dict[str, int] = {}

    for idx, section in enumerate(sections):
        section_type = (section.get("section_type") or section.get("type") or "").lower()
        section_id = _memory_section_identifier(post, section, idx)
        added_any = False

        if section_type == "carousel":
            for slide in section.get("slides") or []:
                image = slide.get("image")
                if not image:
                    continue
                resolved_image = bulletin_media(image)
                if not added_any:
                    section_offsets[section_id] = len(slides)
                    added_any = True
                slides.append({
                    "image": resolved_image,
                    "caption": slide.get("caption"),
                    "alt": slide.get("alt"),
                    "section_id": section_id,
                    "source": "carousel",
                })

        elif section_type == "media-block":
            image = section.get("image")
            if image:
                resolved_image = bulletin_media(image)
                section_offsets.setdefault(section_id, len(slides))
                slides.append({
                    "image": resolved_image,
                    "caption": section.get("caption") or section.get("title"),
                    "alt": section.get("alt"),
                    "section_id": section_id,
                    "source": "media",
                })

    if not slides:
        return None

    cover_image = next((slide["image"] for slide in slides if slide.get("image")), None)
    if not cover_image:
        cover_image = bulletin_media(post.get("header_image") or post.get("hero_image"))

    album_id = (post.get("slug") or f"post-{post.get('id') or 'memory'}") + "-memory"
    return {
        "id": album_id,
        "post_slug": post.get("slug"),
        "title": post.get("title") or "Memory Album",
        "summary": post.get("summary"),
        "tag": post.get("tag") or (post.get("category") or "Memory"),
        "cover_image": cover_image,
        "slide_count": len(slides),
        "slides": slides,
        "published_at": post.get("published_at"),
        "author": post.get("author") or "RDAB Staff",
        "section_offsets": section_offsets,
    }


def _build_memory_albums(posts: list[dict]) -> list[dict]:
    """Return memory albums for the provided bulletin posts."""
    albums: list[dict] = []
    for post in posts:
        album = _build_memory_album(post)
        if album:
            albums.append(album)
    return albums


def _trainer_avatar_fallback(trainer: str) -> str:
    avatars = [
        "avatars/avatar1.png",
        "avatars/avatar2.png",
        "avatars/avatar3.png",
        "avatars/avatar4.png",
        "avatars/avatar5.png",
        "avatars/avatar6.png",
        "avatars/avatar7.png",
        "avatars/avatar8.png",
        "avatars/avatar9.png",
        "avatars/avatar10.png",
    ]
    if not trainer:
        return avatars[0]
    return avatars[hash(trainer) % len(avatars)]


def _team_metadata(value: Optional[str]) -> Optional[dict]:
    if not value:
        return None
    slug = value.strip().lower()
    config = TEAM_CONFIG.get(slug)
    if not config:
        return None
    icon_path = config["icon"]
    try:
        icon_url = url_for("static", filename=icon_path)
    except RuntimeError:
        icon_url = f"/static/{icon_path}"
    return {
        "value": slug,
        "label": config["label"],
        "icon": icon_url,
    }


def _team_options_for_template() -> list[dict]:
    options = []
    for slug, meta in TEAM_CONFIG.items():
        try:
            icon_url = url_for("static", filename=meta["icon"])
        except RuntimeError:
            icon_url = f"/static/{meta['icon']}"
        options.append({
            "value": slug,
            "label": meta["label"],
            "icon": icon_url,
        })
    return options


def _decode_featured_stamp(value) -> Optional[dict]:
    if not value:
        return None
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    icon = data.get("icon")
    label = data.get("label")
    if not icon:
        return None
    return {
        "icon": icon,
        "label": label or "Featured stamp",
        "token": data.get("token"),
    }


def _record_featured_stamps(record: dict) -> list[dict]:
    featured: list[dict] = []
    for key in ("fstamp1", "fstamp2", "fstamp3"):
        parsed = _decode_featured_stamp(record.get(key))
        if parsed:
            featured.append(parsed)
    return featured


def _encode_featured_stamp_value(icon: str, label: str, token: Optional[str] = None) -> str:
    payload = {
        "icon": icon,
        "label": label or "Featured stamp",
    }
    if token:
        payload["token"] = token
    return json.dumps(payload)


def _featured_stamp_token(trainer: str, stamp: dict) -> str:
    raw = "|".join([
        trainer or "",
        stamp.get("icon") or "",
        stamp.get("name") or stamp.get("label") or "",
        stamp.get("awarded_at_iso") or "",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _write_featured_stamps(username: str, stamps: list[dict]) -> None:
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    update_payload = {}
    for idx in range(3):
        key = f"fstamp{idx + 1}"
        if idx < len(stamps):
            stamp = stamps[idx]
            update_payload[key] = _encode_featured_stamp_value(
                stamp.get("icon") or "",
                stamp.get("label") or "",
                stamp.get("token"),
            )
        else:
            update_payload[key] = None
    supabase.table("sheet1").update(update_payload).eq("trainer_username", username).execute()


def fetch_bulletin_comments_from_supabase(post_id: str) -> list[dict]:
    if not _bulletin_supabase_enabled():
        return []
    try:
        rows = (supabase.table("bulletin_post_comments")
                .select("*")
                .eq("post_id", post_id)
                .order("created_at", desc=False)
                .execute().data or [])
    except Exception as exc:
        print("⚠️ Supabase bulletin comments fetch failed:", exc)
        return []
    by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for row in rows:
        cid = row.get("id")
        trainer_username = row.get("trainer_username")
        profile_url = None
        if trainer_username:
            try:
                profile_url = url_for("api_public_trainer_profile", username=trainer_username)
            except RuntimeError:
                profile_url = f"/api/trainers/{quote_plus(trainer_username)}/profile"
        comment = {
            "id": cid,
            "author": trainer_username or "Trainer",
            "avatar": _trainer_avatar_fallback(row.get("trainer_username")),
            "timestamp": row.get("created_at"),
            "body": row.get("body"),
            "replies": [],
            "trainer_username": trainer_username,
            "trainer_profile_url": profile_url,
            "depth": 0,
        }
        by_id[cid] = comment
        parent_id = row.get("parent_comment_id")
        if parent_id and parent_id in by_id:
            parent = by_id[parent_id]
            parent_depth = parent.get("depth") or 0
            comment["depth"] = parent_depth + 1
            parent["replies"].append(comment)
        else:
            roots.append(comment)
    return roots


def _hydrate_comment_media_urls(comments: list[dict]) -> None:
    for comment in comments or []:
        avatar = comment.get("avatar") or "avatars/avatar1.png"
        if avatar.startswith(("http://", "https://", "/")):
            comment["avatar"] = avatar
        else:
            comment["avatar"] = url_for("static", filename=avatar)
        if comment.get("replies"):
            _hydrate_comment_media_urls(comment["replies"])


def _send_comment_reply_notification(post: dict | None,
                                     slug: str,
                                     parent_comment: dict,
                                     reply_comment: dict | None,
                                     replier_username: str) -> None:
    """Notify the original commenter when someone replies to their thread."""
    if not supabase:
        return
    parent_username = (parent_comment.get("trainer_username") or "").strip()
    if not parent_username:
        return
    reply_username = (replier_username or "").strip()
    if reply_username and parent_username.lower() == reply_username.lower():
        return

    post_title = (post or {}).get("title") or "the community bulletin"
    params: dict[str, str] = {}
    parent_id = parent_comment.get("id")
    if parent_id:
        params["comment"] = str(parent_id)
    reply_id = reply_comment.get("id") if reply_comment else None
    if reply_id:
        params["reply"] = str(reply_id)

    try:
        base_url = url_for("community_bulletin_post", slug=slug)
    except RuntimeError:
        base_url = f"/community-bulletin/{slug}"
    thread_url = f"{base_url}#comments"
    if params:
        thread_url = f"{base_url}?{urlencode(params)}#comments"

    metadata = {
        "url": thread_url,
        "cta_label": "Open comment thread",
        "post_title": post_title,
        "post_slug": slug,
        "comment_reply": {
            "parent": {
                "id": parent_id,
                "body": parent_comment.get("body"),
                "author": parent_username,
                "timestamp": parent_comment.get("created_at"),
            },
            "reply": {
                "id": reply_id,
                "body": (reply_comment or {}).get("body"),
                "author": ((reply_comment or {}).get("trainer_username") or reply_username or "Trainer"),
                "timestamp": (reply_comment or {}).get("created_at"),
            },
        },
    }

    subject = f"Your comment is getting some traction on {post_title}"
    message = (
        f"Another trainer replied to your comment on {post_title}. "
        "Open the comments pane to keep the conversation going."
    )
    send_notification(parent_username, subject, message, notif_type="system", metadata=metadata)


def _count_comment_nodes(comments: list[dict]) -> int:
    total = 0
    for comment in comments or []:
        total += 1
        total += _count_comment_nodes(comment.get("replies") or [])
    return total


def _bulletin_comment_depth(post_id: str, comment_id: str) -> Optional[int]:
    """Return the nesting depth (0-based) for a comment id, or None if invalid."""

    if not comment_id or not _bulletin_supabase_enabled():
        return None
    depth = 0
    current_id = comment_id
    visited: set[str] = set()
    while current_id:
        if current_id in visited:
            break
        visited.add(current_id)
        try:
            resp = (supabase.table("bulletin_post_comments")
                    .select("id,parent_comment_id,post_id")
                    .eq("id", current_id)
                    .limit(1)
                    .execute())
        except Exception as exc:
            print("⚠️ Supabase comment depth lookup failed:", exc)
            return None
        row = (resp.data or [None])[0]
        if not row:
            return None
        if str(row.get("post_id")) != str(post_id):
            return None
        parent_id = row.get("parent_comment_id")
        if parent_id:
            depth += 1
            current_id = parent_id
        else:
            break
    return depth


def _bulletin_widget_preview(posts: Optional[list[dict]] = None) -> Optional[dict]:
    posts = posts or get_community_bulletin_posts()
    if not posts:
        return None
    candidate = next((p for p in posts if p.get("is_featured")), posts[0])
    hero_image = candidate.get("hero_image") or candidate.get("header_image")
    return {
        "title": candidate.get("title"),
        "summary": candidate.get("summary"),
        "tag": candidate.get("tag"),
        "published_at": candidate.get("published_at"),
        "hero_image": hero_image,
        "slug": candidate.get("slug"),
    }


def _bulletin_admin_fetch_authors() -> list[dict]:
    if not _bulletin_supabase_enabled():
        return []
    try:
        resp = (supabase.table("bulletin_authors")
                .select("*")
                .order("display_name", desc=False)
                .execute())
        return resp.data or []
    except Exception as exc:
        print("⚠️ Supabase bulletin authors fetch failed:", exc)
        return []


def _bulletin_admin_fetch_comments(limit: int = 10) -> list[dict]:
    if not _bulletin_supabase_enabled():
        return []
    try:
        resp = (supabase.table("bulletin_post_comments")
                .select("*, post:bulletin_posts(slug,title)")
                .order("created_at", desc=True)
                .limit(limit)
                .execute())
        comments = resp.data or []
        for comment in comments:
            avatar = comment.get("trainer_avatar") or _trainer_avatar_fallback(comment.get("trainer_username"))
            if avatar.startswith(("http://", "https://", "/")):
                comment["avatar"] = avatar
            else:
                comment["avatar"] = url_for("static", filename=avatar)
            post_ref = comment.get("post") or {}
            comment["post_title"] = post_ref.get("title")
            comment["post_slug"] = post_ref.get("slug")
        return comments
    except Exception as exc:
        print("⚠️ Supabase bulletin admin comments fetch failed:", exc)
        return []


def _bulletin_admin_comment_total() -> int:
    if not _bulletin_supabase_enabled():
        return 0
    try:
        resp = supabase.table("bulletin_post_comments").select("id", count="exact").execute()
        return getattr(resp, "count", None) or len(resp.data or [])
    except Exception as exc:
        print("⚠️ Supabase bulletin comment count failed:", exc)
        return 0


def _bulletin_admin_stats(posts: list[dict]) -> dict:
    total_posts = len(posts)
    published_posts = len([p for p in posts if (p.get("status") or "").lower() == "published"])
    featured_posts = len([p for p in posts if p.get("is_featured")])
    total_likes = sum(p.get("like_count") or 0 for p in posts)
    total_comments = _bulletin_admin_comment_total()
    return {
        "total_posts": total_posts,
        "published_posts": published_posts,
        "featured_posts": featured_posts,
        "total_likes": total_likes,
        "total_comments": total_comments,
    }


def _parse_datetime_local(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = parser.isoparse(value)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _bulletin_admin_upsert_post(data: dict) -> tuple[bool, dict | str]:
    if not _bulletin_supabase_enabled():
        return False, "Bulletin service unavailable"
    post_id = data.get("id") or None
    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    status = (data.get("status") or "draft").lower()
    published_at = _parse_datetime_local(data.get("published_at"))
    if status == "published" and not published_at:
        published_at = now_iso
    post_record = {
        "title": data.get("title"),
        "slug": data.get("slug"),
        "summary": data.get("summary"),
        "author_id": data.get("author_id"),
        "category": data.get("category"),
        "tag": data.get("tag"),
        "read_time": data.get("read_time"),
        "hero_image": data.get("hero_image"),
        "header_image": data.get("header_image"),
        "status": status,
        "is_featured": str(data.get("is_featured")).lower() not in {"0", "false", "none", "null"},
        "published_at": published_at,
        "scheduled_publish_at": _parse_datetime_local(data.get("scheduled_publish_at")),
        "updated_at": now_iso,
    }
    post_record = {k: v for k, v in post_record.items() if v is not None}
    try:
        if post_id:
            supabase.table("bulletin_posts").update(post_record).eq("id", post_id).execute()
        else:
            post_record["created_at"] = now_iso
            resp = supabase.table("bulletin_posts").insert(post_record).execute()
            post_id = (resp.data or [{}])[0].get("id")
        if not post_id:
            return False, "Unable to determine post ID"

        sections = data.get("sections") or []
        supabase.table("bulletin_post_sections").delete().eq("post_id", post_id).execute()
        section_rows = []
        for order_idx, section in enumerate(sections, start=1):
            payload = section.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            section_rows.append({
                "post_id": post_id,
                "section_order": order_idx,
                "section_type": section.get("section_type"),
                "heading": section.get("heading"),
                "payload": payload or {},
            })
        if section_rows:
            supabase.table("bulletin_post_sections").insert(section_rows).execute()

        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",")]
        supabase.table("bulletin_post_tags").delete().eq("post_id", post_id).execute()
        tag_rows = [{"post_id": post_id, "tag": tag.strip()} for tag in tags if tag.strip()]
        if tag_rows:
            supabase.table("bulletin_post_tags").insert(tag_rows).execute()

        updated_post = fetch_bulletin_post_from_supabase(data.get("slug"), include_sections=True)
        return True, updated_post or {"id": post_id}
    except Exception as exc:
        print("⚠️ Supabase bulletin post save failed:", exc)
        return False, "Unable to save post"


def _bulletin_admin_toggle_feature(post_id: str, value: bool) -> bool:
    if not _bulletin_supabase_enabled():
        return False
    try:
        supabase.table("bulletin_posts").update({"is_featured": value}).eq("id", post_id).execute()
        return True
    except Exception as exc:
        print("⚠️ Supabase toggle feature failed:", exc)
        return False


def _bulletin_admin_delete_comment(comment_id: str) -> bool:
    if not _bulletin_supabase_enabled():
        return False
    try:
        supabase.table("bulletin_post_comments").delete().eq("id", comment_id).execute()
        return True
    except Exception as exc:
        print("⚠️ Supabase comment delete failed:", exc)
        return False


def _bulletin_parse_social_handles(raw: Any) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw]
    social_handles: list[dict] = []
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            for line in stripped.splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) == 3:
                    social_handles.append({"icon": parts[0], "label": parts[1], "url": parts[2]})
                elif len(parts) == 2:
                    social_handles.append({"label": parts[0], "url": parts[1]})
    return social_handles


def _bulletin_admin_delete_post(post_id: str) -> bool:
    if not _bulletin_supabase_enabled():
        return False
    try:
        supabase.table("bulletin_post_sections").delete().eq("post_id", post_id).execute()
        supabase.table("bulletin_post_tags").delete().eq("post_id", post_id).execute()
        supabase.table("bulletin_post_comments").delete().eq("post_id", post_id).execute()
        supabase.table("bulletin_post_likes").delete().eq("post_id", post_id).execute()
        supabase.table("bulletin_posts").delete().eq("id", post_id).execute()
        return True
    except Exception as exc:
        print("⚠️ Supabase bulletin post delete failed:", exc)
        return False


def _bulletin_admin_upsert_author(data: dict) -> tuple[bool, dict | str]:
    if not _bulletin_supabase_enabled():
        return False, "Bulletin service unavailable"
    display_name = (data.get("display_name") or "").strip()
    if not display_name:
        return False, "Display name is required"
    author_id = data.get("id") or data.get("author_id")
    record = {
        "display_name": display_name,
        "avatar_url": (data.get("avatar_url") or "").strip() or None,
        "link_url": (data.get("link_url") or "").strip() or None,
        "bio": data.get("bio") or None,
        "social_handles": _bulletin_parse_social_handles(data.get("social_handles")),
    }
    try:
        if author_id:
            supabase.table("bulletin_authors").update(record).eq("id", author_id).execute()
            resp = (supabase.table("bulletin_authors")
                    .select("*")
                    .eq("id", author_id)
                    .limit(1)
                    .execute())
            author = (resp.data or [{}])[0]
        else:
            resp = supabase.table("bulletin_authors").insert(record).execute()
            author = (resp.data or [{}])[0]
        return True, author
    except Exception as exc:
        print("⚠️ Supabase author create failed:", exc)
        return False, "Unable to create author"


def _bulletin_admin_delete_author(author_id: str) -> bool:
    if not _bulletin_supabase_enabled():
        return False
    try:
        supabase.table("bulletin_authors").delete().eq("id", author_id).execute()
        return True
    except Exception as exc:
        print("⚠️ Supabase author delete failed:", exc)
        return False

DATA_DIR = Path(app.root_path) / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CUSTOM_EVENTS_PATH = DATA_DIR / "custom_events.json"

SQLITE_PATH = DATA_DIR / "app.db"
app.config.setdefault("SQLALCHEMY_DATABASE_URI", f"sqlite:///{SQLITE_PATH}")
app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
db.init_app(app)
try:
    LONDON_TZ = ZoneInfo("Europe/London")
except Exception:
    LONDON_TZ = timezone.utc

def parse_dt_safe(dt_str):
    if not dt_str:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = parser.isoparse(dt_str)
        if dt.tzinfo is None:
            # make naive -> UTC aware
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)

def load_custom_events():
    if not CUSTOM_EVENTS_PATH.exists():
        return []
    try:
        with CUSTOM_EVENTS_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except Exception as exc:
        print("⚠️ Failed to load custom calendar events:", exc)
    return []

def save_custom_events(events: list[dict]) -> None:
    try:
        with CUSTOM_EVENTS_PATH.open("w", encoding="utf-8") as fh:
            json.dump(events, fh, indent=2)
    except Exception as exc:
        print("⚠️ Failed to save custom calendar events:", exc)


def build_league_content():
    """Return static league mode copy used by the admin and player hubs."""
    league_modes = [
        {
            "key": "cdl",
            "title": "CDL",
            "summary": "Centralise CDL records, leaderboards, and scorecards so trainers can track progress season over season.",
            "features": [
                "Ingest live data from the CDL Google Sheet with search across active and historical standings.",
                "Auto-create a CDL scorecard for each trainer with win-loss, rankings, and highlight stats.",
                "Archive seasonal tables so admins and trainers can browse past cups at any time.",
            ],
            "future": [
                "Automate CDL stamp awarding based on scorecard milestones and final placements.",
            ],
        },
        {
            "key": "pvp",
            "title": "PvP",
            "summary": "Host seasonal leagues, track match results, and operate live brackets directly in the app.",
            "features": [
                "List previous seasonal leagues with standings, rosters, and match histories.",
                "Spin up a new league with rules, eligible Pokemon, and round structures for meetups like Sinistea.",
                "Switch a league into \"live\" mode mid-meetup so players can enter results in real time.",
            ],
            "future": [
                "Launch a Hall of Fame view to spotlight top performers across seasons.",
            ],
        },
        {
            "key": "limited",
            "title": "Limited-Time Events",
            "summary": "Run time-bound activities like quizzes, bingo cards, and scavenger hunts with themed UIs.",
            "features": [
                "Toggle active vs inactive state to show either a sleeping Rotom message or a live event hub.",
                "Configure event theming starting with the Sinistea Halloween party and reuse for future pop-ups.",
                "Create live event activities such as quizzes, bingo cards, and scavenger hunts with admin controls.",
            ],
            "future": [
                "Add advanced verifications for stamps via QR, NFC, or media uploads once tooling is ready.",
            ],
        },
    ]

    live_event_settings = {
        "active": False,
        "theme": "Sinistea Halloween Party",
        "activities": [
            {
                "type": "quiz",
                "title": "Live Quiz Tool",
                "details": [
                    "Admin controls to start, pause, or advance quiz questions remotely.",
                    "Player view shows one question at a time with four answer options.",
                    "Real-time leaderboard with streak bonuses and stamp rewards based on final points.",
                ],
            },
            {
                "type": "bingo",
                "title": "Bingo Tool",
                "details": [
                    "Pre-build bingo cards so players can tap squares during events and sync progress.",
                    "Auto-stamp completed cards with celebratory animations once all squares are marked.",
                    "Future: require verification like QR scans, NFC taps, or screenshot upload before stamping.",
                ],
            },
        ],
        "inactive_copy": "No live events right now - the rotoms are sleeping",
    }

    return league_modes, live_event_settings


def _trainer_uuid_from_name(trainer_name: str) -> str:
    """Derive a stable UUID for trainers for PvP tables until auth IDs are wired in."""
    cleaned = (trainer_name or "").strip().lower()
    if not cleaned:
        cleaned = f"unknown-{uuid.uuid4().hex}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"trainer:{cleaned}"))


def _ensure_uuid(value: str | None, fallback_name: str | None = None) -> str | None:
    if value:
        try:
            return str(uuid.UUID(str(value)))
        except Exception:
            pass
    if fallback_name:
        return _trainer_uuid_from_name(fallback_name)
    return None


def _supabase_execute(query, fallback=None):
    """Execute a Supabase query and swallow errors with logging."""
    if not supabase:
        return fallback
    try:
        resp = query.execute()
        return getattr(resp, "data", fallback) or fallback
    except Exception as exc:
        print("⚠️ Supabase query failed:", exc)
        try:
            g.supabase_last_error = str(exc)
        except RuntimeError:
            pass
        return fallback


def fetch_pvp_tournaments(statuses: list[str] | None = None, limit: int | None = None):
    """Return tournaments filtered by status (default: upcoming & live)."""
    if statuses is None:
        statuses = ["REGISTRATION", "LIVE"]
    rows = _supabase_execute(
        supabase.table("pvp_tournament_summary")
        .select("*")
        .in_("status", statuses)
        .order("start_at", desc=False)
    , [])
    tournaments: list[dict] = []
    for row in rows:
        start_at = parse_dt_safe(row.get("start_at"))
        reg_open = parse_dt_safe(row.get("registration_open_at"))
        reg_close = parse_dt_safe(row.get("registration_close_at"))
        tournaments.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "status": row.get("status"),
            "bracket_type": row.get("bracket_type"),
            "start_at": start_at if start_at.year > 1900 else None,
            "registration_open_at": reg_open if reg_open.year > 1900 else None,
            "registration_close_at": reg_close if reg_close.year > 1900 else None,
            "registrant_count": row.get("registrant_count", 0),
        })
    if limit is not None:
        tournaments = tournaments[:limit]
    return tournaments


def fetch_pvp_tournament_archive(limit: int = 10):
    """Return recently completed tournaments for archive listings."""
    return fetch_pvp_tournaments(statuses=["COMPLETED", "ARCHIVED"], limit=limit)


def fetch_pvp_tournaments_for_admin():
    """Fetch all tournaments for admin dashboard."""
    rows = _supabase_execute(
        supabase.table("pvp_tournaments")
        .select("*")
        .order("created_at", desc=True)
    , [])
    tournaments: list[dict] = []
    for row in rows:
        record = dict(row)
        for field in ("start_at", "registration_open_at", "registration_close_at", "conclude_at", "created_at", "updated_at"):
            dt_val = parse_dt_safe(record.get(field))
            record[field] = dt_val if dt_val.year > 1900 else None
        tournaments.append(record)
    return tournaments


def fetch_pvp_tournament_detail(tournament_id: str):
    """Load tournament detail including rules, prizes, registrations, and matches."""
    if not (supabase and tournament_id):
        return None

    tournament_rows = _supabase_execute(
        supabase.table("pvp_tournaments")
        .select("*")
        .eq("id", tournament_id)
        .limit(1)
    , [])
    if not tournament_rows:
        return None
    tournament = tournament_rows[0]
    for field in ("start_at", "registration_open_at", "registration_close_at", "conclude_at"):
        dt_val = parse_dt_safe(tournament.get(field))
        tournament[field] = dt_val if dt_val.year > 1900 else None

    rules = _supabase_execute(
        supabase.table("pvp_rules")
        .select("*")
        .eq("tournament_id", tournament_id)
        .order("rule_order", desc=False)
    , [])
    prizes = _supabase_execute(
        supabase.table("pvp_prizes")
        .select("*")
        .eq("tournament_id", tournament_id)
        .order("placement", desc=False)
    , [])
    registrations = _supabase_execute(
        supabase.table("pvp_registrations")
        .select("*")
        .eq("tournament_id", tournament_id)
        .order("created_at", desc=True)
    , [])

    teams_map: dict[str, list[dict]] = {}
    team_rows = _supabase_execute(
        supabase.table("pvp_teams")
        .select("*")
        .in_("registration_id", [r["id"] for r in registrations] or ["00000000-0000-0000-0000-000000000000"])
        .order("pokemon_slot", desc=False)
    , [])
    for row in team_rows or []:
        teams_map.setdefault(row["registration_id"], []).append({
            "slot": row.get("pokemon_slot"),
            "species_name": row.get("species_name"),
            "species_form": row.get("species_form") or "",
            "moves": row.get("moves") or {},
        })

    matches = _supabase_execute(
        supabase.table("pvp_matches")
        .select("*")
        .eq("tournament_id", tournament_id)
        .order("round_no", desc=False)
    , [])
    leaderboard = _supabase_execute(
        supabase.table("pvp_leaderboards")
        .select("*")
        .eq("tournament_id", tournament_id)
        .order("rank", desc=False)
    , [])

    detail = {
        "tournament": tournament,
        "rules": rules or [],
        "prizes": prizes or [],
        "registrations": [],
        "matches": matches or [],
        "leaderboard": leaderboard or [],
    }

    for reg in registrations or []:
        detail["registrations"].append({
            **reg,
            "teams": teams_map.get(reg["id"], []),
        })
    return detail


@app.route("/leagues/pvp/<tournament_id>/archive", methods=["GET"])
def leagues_pvp_archive_detail(tournament_id):
    if "trainer" not in session:
        return jsonify({"error": "auth required"}), 403

    detail = fetch_pvp_tournament_detail(tournament_id)
    if not detail:
        return jsonify({"error": "tournament not found"}), 404

    tournament = detail.get("tournament") or {}
    leaderboard = detail.get("leaderboard") or []
    standings: list[dict] = []
    for idx, row in enumerate(leaderboard, start=1):
        trainer_label = (
            row.get("trainer_name")
            or row.get("notes")
            or row.get("registration_label")
            or (row.get("registration_id")[:8] if row.get("registration_id") else "")
        )
        standings.append({
            "rank": row.get("rank") or idx,
            "trainer": trainer_label,
            "points": row.get("points") or 0,
            "wins": row.get("wins") or 0,
            "losses": row.get("losses") or 0,
            "draws": row.get("draws") or 0,
        })

    payload = {
        "tournament": {
            "id": tournament.get("id"),
            "name": tournament.get("name"),
            "status": tournament.get("status"),
            "bracket_type": tournament.get("bracket_type"),
            "start_at": tournament.get("start_at").isoformat() if isinstance(tournament.get("start_at"), datetime) else None,
            "conclude_at": tournament.get("conclude_at").isoformat() if isinstance(tournament.get("conclude_at"), datetime) else None,
        },
        "leaderboard": standings,
    }
    return jsonify(payload)


def find_pvp_registration(tournament_id: str, trainer_name: str):
    """Lookup an existing registration handle for a trainer."""
    if not (supabase and tournament_id and trainer_name):
        return None
    trainer_uuid = _trainer_uuid_from_name(trainer_name)
    rows = _supabase_execute(
        supabase.table("pvp_registrations")
        .select("*")
        .eq("tournament_id", tournament_id)
        .eq("trainer_id", trainer_uuid)
        .limit(1)
    , [])
    if rows:
        reg = rows[0]
        team_rows = _supabase_execute(
            supabase.table("pvp_teams")
            .select("*")
            .eq("registration_id", reg["id"])
            .order("pokemon_slot", desc=False)
        , [])
        reg["teams"] = [
            {
                "slot": row.get("pokemon_slot"),
                "species_name": row.get("species_name"),
                "species_form": row.get("species_form") or "",
                "moves": row.get("moves") or {},
            }
            for row in team_rows or []
        ]
        return reg
    return None


def register_trainer_for_pvp(tournament_id: str, trainer_name: str, notes: str | None = None):
    """Create or confirm a PvP registration."""
    if not (supabase and tournament_id and trainer_name):
        return None, "Supabase not available."

    trainer_uuid = _trainer_uuid_from_name(trainer_name)
    payload = {
        "tournament_id": tournament_id,
        "trainer_id": trainer_uuid,
        "status": "CONFIRMED",
        "confirmed_at": datetime.utcnow().isoformat(),
        "notes": notes or trainer_name,
    }
    try:
        resp = supabase.table("pvp_registrations").upsert(payload, on_conflict="tournament_id,trainer_id").execute()
        data = getattr(resp, "data", None) or []
        if data:
            return data[0], None
    except Exception as exc:
        print("⚠️ PvP registration upsert failed:", exc)
        try:
            g.supabase_last_error = str(exc)
        except RuntimeError:
            pass
        return None, "Unable to register for tournament right now."
    return None, "Registration data not returned."


def save_pvp_team(registration_id: str, team_slots: list[dict]):
    """Replace the team for a registration."""
    if not (supabase and registration_id):
        return False, "Supabase not available."
    try:
        supabase.table("pvp_teams").delete().eq("registration_id", registration_id).execute()
    except Exception as exc:
        print("⚠️ PvP team clear failed:", exc)
        return False, "Could not reset previous team."

    entries = []
    for idx, slot in enumerate(team_slots, start=1):
        species = (slot.get("species_name") or "").strip()
        if not species:
            continue
        entry = {
            "registration_id": registration_id,
            "pokemon_slot": idx,
            "species_name": species,
        }
        form_value = (slot.get("species_form") or "").strip()
        if form_value:
            entry["species_form"] = form_value
        moves = slot.get("moves") or {}
        if moves:
            entry["moves"] = moves
        entries.append(entry)

    if not entries:
        return False, "Please provide at least one Pokemon."

    try:
        resp = supabase.table("pvp_teams").insert(entries, returning="representation").execute()
        if not getattr(resp, "data", None):
            print("⚠️ PvP team save returned no rows:", getattr(resp, "data", None))
        supabase.table("pvp_registrations").update({
            "team_locked_at": datetime.utcnow().isoformat(),
        }).eq("id", registration_id).execute()
        return True, None
    except Exception as exc:
        print("⚠️ PvP team save failed:", exc)
        try:
            g.supabase_last_error = str(exc)
        except RuntimeError:
            pass
        return False, "Unable to save team right now."


def _slugify_tournament_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not cleaned:
        cleaned = f"tournament-{uuid.uuid4().hex[:8]}"
    return cleaned[:60]


def save_pvp_rules(tournament_id: str, rules_lines: list[str]):
    if not supabase:
        return
    try:
        supabase.table("pvp_rules").delete().eq("tournament_id", tournament_id).execute()
    except Exception as exc:
        print("⚠️ PvP rules delete failed:", exc)
        return
    entries = []
    for idx, line in enumerate(rules_lines, start=1):
        text = (line or "").strip()
        if not text:
            continue
        title = ""
        body = text
        if ": " in text:
            title, body = text.split(": ", 1)
        entries.append({
            "tournament_id": tournament_id,
            "rule_order": idx,
            "title": title,
            "body": body,
        })
    if entries:
        try:
            supabase.table("pvp_rules").insert(entries).execute()
        except Exception as exc:
            print("⚠️ PvP rules insert failed:", exc)


def save_pvp_prizes(tournament_id: str, prize_lines: list[str]):
    if not supabase:
        return
    try:
        supabase.table("pvp_prizes").delete().eq("tournament_id", tournament_id).execute()
    except Exception as exc:
        print("⚠️ PvP prizes delete failed:", exc)
        return
    entries = []
    for idx, line in enumerate(prize_lines, start=1):
        text = (line or "").strip()
        if not text:
            continue
        entries.append({
            "tournament_id": tournament_id,
            "placement": idx,
            "description": text,
        })
    if entries:
        try:
            supabase.table("pvp_prizes").insert(entries).execute()
        except Exception as exc:
            print("⚠️ PvP prizes insert failed:", exc)


def upsert_pvp_tournament(data: dict, actor: str):
    """Create or update a tournament from admin form data."""
    if not supabase:
        return None, "Supabase not available."

    tournament_id = data.get("tournament_id") or ""
    name = (data.get("name") or "").strip()
    if not name:
        return None, "Tournament name is required."
    bracket_type = (data.get("bracket_type") or "SWISS").upper()
    if bracket_type not in {"SWISS", "ROUND_ROBIN", "SINGLE_ELIMINATION"}:
        return None, "Unsupported bracket type."

    slug_value = data.get("slug")
    if not slug_value:
        slug_value = _slugify_tournament_name(name)

    payload = {
        "name": name,
        "slug": slug_value,
        "description": (data.get("description") or "").strip(),
        "bracket_type": bracket_type,
        "status": data.get("status") or "DRAFT",
        "location_text": (data.get("location_text") or "").strip(),
        "meta_notes": (data.get("meta_notes") or "").strip(),
        "prize_summary": (data.get("prize_summary") or "").strip(),
    }

    def _parse_dt(field_name):
        raw = (data.get(field_name) or "").strip()
        if not raw:
            return None
        try:
            dt = parser.parse(raw)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return None

    payload["start_at"] = _parse_dt("start_at")
    payload["registration_open_at"] = _parse_dt("registration_open_at")
    payload["registration_close_at"] = _parse_dt("registration_close_at")
    payload["conclude_at"] = _parse_dt("conclude_at")
    meetup_id = (data.get("meetup_id") or "").strip()
    if meetup_id:
        payload["meetup_id"] = meetup_id

    try:
        if tournament_id:
            supabase.table("pvp_tournaments").update(payload).eq("id", tournament_id).execute()
            saved_id = tournament_id
        else:
            payload["created_by"] = _ensure_uuid(data.get("created_by"), actor)
            resp = supabase.table("pvp_tournaments").insert(payload).execute()
            rows = getattr(resp, "data", None)
            saved_id = None
            if rows and isinstance(rows, list) and rows:
                saved_id = rows[0].get("id")
            if not saved_id:
                lookup = _supabase_execute(
                    supabase.table("pvp_tournaments")
                    .select("id")
                    .eq("slug", slug_value)
                    .order("created_at", desc=True)
                    .limit(1),
                    [],
                )
                if lookup:
                    saved_id = lookup[0]["id"]
            if not saved_id:
                return None, "Failed to create tournament."
    except Exception as exc:
        print("⚠️ PvP tournament save failed:", exc)
        return None, "Unable to save tournament."

    rules_lines = data.get("rules_block", "").splitlines()
    prizes_lines = data.get("prizes_block", "").splitlines()
    try:
        save_pvp_rules(saved_id, rules_lines)
        save_pvp_prizes(saved_id, prizes_lines)
    except Exception as exc:
        print("⚠️ PvP ancillary save failed:", exc)

    return saved_id, None


def update_pvp_tournament_status(tournament_id: str, status: str):
    if not (supabase and tournament_id and status):
        return False
    try:
        supabase.table("pvp_tournaments").update({
            "status": status,
        }).eq("id", tournament_id).execute()
        return True
    except Exception as exc:
        print("⚠️ PvP status update failed:", exc)
        return False



def fetch_upcoming_events(limit: int | None = None):
    """Fetch upcoming meetup events ordered by start time in London timezone."""
    if not (USE_SUPABASE and supabase):
        upcoming = []
    else:
        upcoming = []

    now_local = datetime.now(LONDON_TZ)
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    sentinel = datetime.min.replace(tzinfo=timezone.utc)

    if USE_SUPABASE and supabase:
        try:
            resp = (supabase.table("events")
                    .select("id,event_id,name,start_time,end_time,location,url,cover_photo_url")
                    .order("start_time", desc=False)
                    .execute())
            rows = resp.data or []
        except Exception as exc:
            print("⚠️ Supabase upcoming events fetch failed:", exc)
            rows = []

        for row in rows:
            record_id = str(row.get("id") or "").strip()
            start_dt = parse_dt_safe(row.get("start_time"))
            if start_dt <= sentinel:
                continue
            end_dt = parse_dt_safe(row.get("end_time"))
            if end_dt <= sentinel:
                end_dt = start_dt + timedelta(hours=2)

            start_local = start_dt.astimezone(LONDON_TZ)
            if start_local < now_local:
                continue

            end_utc = end_dt.astimezone(timezone.utc)
            if end_utc <= now_utc:
                continue

            event_id = str(row.get("event_id") or "").strip()
            if not event_id:
                event_id = record_id or f"evt-{uuid.uuid4().hex}"

            upcoming.append({
                "event_id": event_id,
                "record_id": record_id,
                "name": row.get("name") or "Unnamed Meetup",
                "location": row.get("location") or "",
                "campfire_url": row.get("url") or "",
                "cover_photo_url": row.get("cover_photo_url") or "",
                "start": start_dt,
                "end": end_dt,
                "start_local": start_local,
                "end_local": end_dt.astimezone(LONDON_TZ),
                "source": "supabase",
            })

    # Add locally managed events
    local_events = load_custom_events()
    for row in local_events:
        start_dt = parse_dt_safe(row.get("start_time"))
        if start_dt <= sentinel:
            continue
        end_dt = parse_dt_safe(row.get("end_time"))
        if end_dt <= sentinel:
            end_dt = start_dt + timedelta(hours=2)

        start_local = start_dt.astimezone(LONDON_TZ)
        if start_local < now_local:
            continue

        end_utc = end_dt.astimezone(timezone.utc)
        if end_utc <= now_utc:
            continue

        event_id = str(row.get("event_id") or "").strip()
        if not event_id:
            event_id = f"local-{uuid.uuid4().hex}"

        upcoming.append({
            "event_id": event_id,
            "record_id": row.get("record_id") or "",
            "name": row.get("name") or "Community Meetup",
            "location": row.get("location") or "",
            "campfire_url": row.get("url") or "",
            "cover_photo_url": row.get("cover_photo_url") or "",
            "start": start_dt,
            "end": end_dt,
            "start_local": start_local,
            "end_local": end_dt.astimezone(LONDON_TZ),
            "source": "local",
        })

    upcoming.sort(key=lambda ev: ev["start"])
    if limit is not None:
        upcoming = upcoming[:limit]
    return upcoming

def build_google_calendar_link(name: str, location: str, start_dt: datetime, end_dt: datetime, campfire_url: str) -> str:
    """Return a Google Calendar deep link for the provided event."""
    start_fmt = start_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end_fmt = end_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    details = []
    if campfire_url:
        details.append(f"Campfire RSVP: {campfire_url}")
    if location:
        details.append(f"Location: {location}")

    params = {
        "action": "TEMPLATE",
        "text": name,
        "dates": f"{start_fmt}/{end_fmt}",
    }
    if location:
        params["location"] = location
    if details:
        params["details"] = "\n".join(details)

    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote_plus)

def serialize_calendar_events(events):
    """Convert upcoming event objects into a template-friendly payload."""
    calendar_events = []
    for ev in events:
        google_link = build_google_calendar_link(ev["name"], ev["location"], ev["start"], ev["end"], ev["campfire_url"])
        calendar_events.append({
            "id": ev["event_id"],
            "event_id": ev["event_id"],
            "title": ev["name"],
            "location": ev["location"],
            "campfire_url": ev["campfire_url"],
            "cover_photo_url": ev["cover_photo_url"],
            "start": ev["start"].astimezone(timezone.utc).isoformat(),
            "end": ev["end"].astimezone(timezone.utc).isoformat(),
            "start_local_date": ev["start_local"].strftime("%A %d %B %Y"),
            "start_local_time": ev["start_local"].strftime("%H:%M"),
            "end_local_time": ev["end_local"].strftime("%H:%M"),
            "google_calendar_url": google_link,
            "ics_url": url_for("event_ics_file", event_id=ev["event_id"]),
            "date_label": ev["start_local"].strftime("%d %b %Y"),
        })
    return calendar_events

# ====== Supabase setup ======
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = None
if USE_SUPABASE and create_client and SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("⚠️ Could not init Supabase client:", e)
        supabase = None
app.config["SUPABASE_CLIENT"] = supabase


def _clear_supabase_error():
    try:
        if hasattr(g, "supabase_last_error"):
            del g.supabase_last_error
    except RuntimeError:
        pass


def _supabase_rest_insert(table: str, payload: dict) -> bool:
    """Fallback insert using Supabase REST API when the Python client misbehaves."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        try:
            g.supabase_last_error = "Missing Supabase credentials"
        except RuntimeError:
            pass
        return False

    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code >= 400:
            print(
                "❌ Supabase REST insert failed:",
                resp.status_code,
                resp.text[:300],
            )
            try:
                g.supabase_last_error = resp.text
            except RuntimeError:
                pass
            return False
        _clear_supabase_error()
        return True
    except Exception as exc:
        print("❌ Supabase REST insert exception:", exc)
        try:
            g.supabase_last_error = str(exc)
        except RuntimeError:
            pass
        return False


def supabase_insert_row(table: str, payload: dict) -> bool:
    """Insert helper that retries via REST if the Supabase client errors."""
    last_error = None
    if supabase:
        try:
            supabase.table(table).insert(payload).execute()
            _clear_supabase_error()
            return True
        except Exception as exc:
            msg = str(exc)
            print(f"⚠️ Supabase client insert failed: {msg}")
            last_error = msg
            try:
                g.supabase_last_error = msg
            except RuntimeError:
                pass
    ok = _supabase_rest_insert(table, payload)
    if not ok and last_error:
        print(f"❌ Supabase insert ultimately failed after client+REST attempts: {last_error}")
    if ok:
        _clear_supabase_error()
    return ok

# ====== Policy registry ======
POLICY_PAGES = [
    {
        "slug": "community-standards",
        "title": "Community Standards (Arceus Law)",
        "description": "Expectations for respectful play, meetup conduct, and moderator actions across RDAB.",
        "template": "policies/community-standards.html",
    },
    {
        "slug": "terms-of-service",
        "title": "Terms of Service – RDAB App",
        "description": "What you agree to when using the RDAB app to track stamps, rewards, and events.",
        "template": "policies/terms-of-service.html",
    },
    {
        "slug": "promise-to-parents",
        "title": "Our Promise to Parents",
        "description": "How we keep young trainers safe online and at meetups, plus what parents can expect from us.",
        "template": "policies/promise-to-parents.html",
    },
    {
        "slug": "privacy-policy",
        "title": "Privacy Policy",
        "description": "The data we collect, how it is used, and your GDPR rights within the RDAB community.",
        "template": "policies/privacy-policy.html",
    },
    {
        "slug": "safeguarding-policy",
        "title": "Safeguarding Policy – RDAB App & Community",
        "description": "Our safeguarding commitments for children, young people, and vulnerable adults.",
        "template": "policies/safeguarding-policy.html",
    },
    {
        "slug": "branding-fair-use",
        "title": "Branding and Fair Use Policy",
        "description": "How RDAB handles Pokémon intellectual property and responds to rights holder requests.",
        "template": "policies/branding-fair-use.html",
    },
    {
        "slug": "children-parental-consent",
        "title": "Children and Parental Consent Policy",
        "description": "Parental consent requirements, data handling, and usage rules for under-13 trainers.",
        "template": "policies/children-parental-consent.html",
    },
    {
        "slug": "user-appeals-process",
        "title": "User Appeals Process – RDAB App & Community",
        "description": "How suspended members can submit appeals and how RDAB reviews them.",
        "template": "policies/user-appeals-process.html",
    },
]

# ===== Detect mobile - force app =====
@app.route("/pwa-flag", methods=["POST"])
def pwa_flag():
    session["is_pwa"] = True
    return ("", 204)  # no content

@app.route("/")
def home():
    """Smart home redirect — PWA-safe."""
    ua = request.user_agent.string.lower()
    is_mobile = bool(re.search("iphone|ipad|android|mobile", ua))
    is_pwa = session.get("is_pwa", False)

    # 🔹 If user already logged in → dashboard
    if "trainer" in session:
        return redirect(url_for("dashboard"))

    # 🔹 If running as PWA → go straight to login
    if is_pwa or "wv" in ua or "pwa" in ua:
        return redirect(url_for("login"))

    # 🔹 Otherwise show normal landing page (for browsers only)
    if not is_pwa:
        return render_template("landing.html", show_back=False)

    # Default fallback
    return redirect(url_for("login"))


@app.route("/session-check")
def session_check():
    """Quick JSON endpoint for PWA reload logic."""
    return jsonify({"logged_in": "trainer" in session})


@app.route("/about")
def about_rdab():
    """Public-facing About page embedding Canva overview content."""
    return render_template(
        "about.html",
        title="About RDAB",
        header_back_action={
            "href": url_for("home"),
            "label": "Back to RDAB",
        },
        about_embed_url=ABOUT_EMBED_URL,
        about_source_url=ABOUT_SOURCE_URL,
        about_credit_title=ABOUT_CREDIT_TITLE,
        about_credit_author=ABOUT_CREDIT_AUTHOR,
        show_back=False,
    )


# ===== Policies =====
def _policy_back_action(is_pwa: bool, source: str | None):
    if source == "login":
        return {
            "label": "⬅ Back to login",
            "href": url_for("login"),
        }, True
    if is_pwa:
        return {
            "label": "⬅ Go back to the dashboard",
            "href": url_for("dashboard"),
        }, True
    return None, False


@app.route("/policies")
def policies_index():
    source = request.args.get("source")
    is_pwa = session.get("is_pwa", False) or request.args.get("pwa") == "1"
    back_action, force_pwa = _policy_back_action(is_pwa, source)
    effective_is_pwa = is_pwa or force_pwa
    return render_template(
        "policies/index.html",
        policies=POLICY_PAGES,
        is_pwa=effective_is_pwa,
        back_action=back_action,
        show_back=False,
    )


@app.route("/policies/<slug>")
def policy_page(slug: str):
    policy = next((p for p in POLICY_PAGES if p["slug"] == slug), None)
    if not policy:
        abort(404)

    source = request.args.get("source")
    is_pwa = session.get("is_pwa", False) or request.args.get("pwa") == "1"
    back_action, force_pwa = _policy_back_action(is_pwa, source)
    effective_is_pwa = is_pwa or force_pwa

    template_name = policy.get("template") or f"policies/{slug}.html"
    return render_template(
        template_name,
        policy=policy,
        is_pwa=effective_is_pwa,
        back_action=back_action,
        show_back=False,
    )

# ===== Catalog Receipt Helper =====
# --- put near your other imports ---
import mimetypes
import io
import uuid

def _upload_to_supabase(file_storage, folder="catalog", bucket="catalog"):
    """
    Uploads a file to the Supabase 'catalog' bucket and returns its public URL.
    Compatible with supabase-py >= 2.0.
    """
    if not supabase:
        print("❌ Supabase client not initialized.")
        return None
    if not file_storage or not getattr(file_storage, "filename", ""):
        print("❌ No file supplied to upload.")
        return None

    try:
        # Build unique key
        fname = secure_filename(file_storage.filename)
        root, ext = os.path.splitext(fname)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        fname = f"{root}_{stamp}{ext}"
        object_key = f"{folder}/{fname}" if folder else fname

        # Read file bytes
        file_storage.stream.seek(0)
        file_bytes = file_storage.read()

        storage = supabase.storage.from_(bucket)

        # 🔑 Upload — v2 client just takes path + bytes
        res = storage.upload(object_key, file_bytes)
        print("➡️ Upload result:", res)

        # Return public URL
        public_url = storage.get_public_url(object_key)
        print("✅ Uploaded file URL:", public_url)
        return public_url
    except Exception as e:
        err_txt = str(e)
        try:
            if hasattr(e, "args") and e.args and isinstance(e.args[0], dict):
                err_txt = json.dumps(e.args[0])
        except Exception:
            pass
        print(f"❌ Supabase upload failed: {err_txt}")
        return None

def _is_allowed_image_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_CLASSIC_IMAGE_EXTENSIONS

def absolute_url(path: str) -> str:
    root = request.url_root.rstrip('/')
    if not path.startswith('/'):
        path = '/' + path
    return f"{root}{path}"


def _bulletin_is_absolute_media(path: Optional[str]) -> bool:
    if not path:
        return False
    lowered = path.lower()
    return lowered.startswith(("http://", "https://", "//", "data:", "blob:"))


def _clean_static_path(path: str) -> str:
    cleaned = path.lstrip("/")
    if cleaned.startswith("static/"):
        cleaned = cleaned.split("/", 1)[1]
    return cleaned


@app.template_filter("bulletin_media")
def bulletin_media(value: Optional[str]) -> str:
    """Return a usable URL for bulletin assets stored locally or on Supabase."""
    if not value:
        return ""
    if _bulletin_is_absolute_media(value):
        return value
    if value.startswith("/"):
        return value
    return url_for("static", filename=_clean_static_path(value))

ALLOWED_INBOX_TAGS = {"a", "br", "strong", "em", "b", "i", "u", "p", "ul", "ol", "li", "blockquote", "code"}
ALLOWED_INBOX_ATTRS = {
    "a": ["href", "title", "target", "rel"],
}


def _linkify_target_blank(attrs, new=False):
    href = attrs.get("href")
    if not href:
        return attrs

    attrs["target"] = "_blank"
    rel_values = set(filter(None, (attrs.get("rel") or "").split()))
    rel_values.update({"noopener", "noreferrer"})
    attrs["rel"] = " ".join(sorted(rel_values))
    return attrs


LINKIFY_CALLBACKS = list(DEFAULT_CALLBACKS) + [_linkify_target_blank]

@app.template_filter("nl2br")
def nl2br(text):
    if text is None:
        return Markup("")

    cleaned = bleach.clean(
        text,
        tags=ALLOWED_INBOX_TAGS,
        attributes=ALLOWED_INBOX_ATTRS,
        strip=True,
    )
    cleaned = cleaned.replace("\r\n", "\n").replace("\n", "<br>")
    linked = bleach.linkify(
        cleaned,
        callbacks=LINKIFY_CALLBACKS,
        skip_tags=["a", "code"],
    )
    return Markup(linked)

# ====== VAPID setup ======
_DEFAULT_VAPID_PUBLIC = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEAbWEvTQ7pDPa0Q-O8drCVnHmfnzVpn7W7UkclKUd1A-yGIee_ehqUjRgMp_HxSBPMylN_H83ffaE2eDIybrTVA"
_DEFAULT_VAPID_PRIVATE = "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgDJL244WZuoVzLqj3NvdTZ_fY-DtZqDQUakJdKV73myihRANCAAQBtYS9NDukM9rRD47x2sJWceZ-fNWmftbtSRyUpR3UD7IYh5796GpSNGAyn8fFIE8zKU38fzd99oTZ4MjJutNU"
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", _DEFAULT_VAPID_PUBLIC)
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", _DEFAULT_VAPID_PRIVATE)
VAPID_CLAIMS = {"sub": "mailto:raidingdoncaster@gmail.com"}

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

@app.route("/service-worker.js")
def service_worker():
    response = make_response(send_from_directory("static", "service-worker.js"))
    response.headers["Cache-Control"] = "no-cache"
    return response

# ===== Header =====
@app.context_processor
def inject_header_data():
    """Inject stamp count and inbox preview into every template automatically."""
    trainer = session.get("trainer")
    current_stamps = 0
    inbox_preview = []

    if trainer and supabase:
        # Stamp count from Supabase.sheet1
        try:
            r = (supabase.table("sheet1")
                 .select("stamps")
                 .eq("trainer_username", trainer)
                 .limit(1)
                 .execute())
            if r.data:
                current_stamps = int(r.data[0].get("stamps") or 0)
        except Exception as e:
            print("⚠️ header stamps fetch failed:", e)

        # Latest inbox messages (subject + created_at)
        try:
            inbox_preview = get_inbox_preview(trainer)
        except Exception as e:
            print("⚠️ header inbox preview failed:", e)

    return dict(current_stamps=current_stamps, inbox_preview=inbox_preview)


@app.context_processor
def inject_trainer_team_options():
    try:
        options = _team_options_for_template()
    except Exception:
        options = []
    return dict(trainer_team_options=options)


# ====== Helpers ======
def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_account_type(value: str | None) -> str:
    """Return canonical account_type strings for downstream logic."""
    if not value:
        return "Standard"
    cleaned = value.strip()
    lowered = cleaned.lower()
    if lowered == "standard":
        return "Standard"
    if lowered == "kids account":
        return "Kids Account"
    if lowered == "admin":
        return "Admin"
    return cleaned


def find_user(username):
    """Find a trainer in Supabase.sheet1 (case-insensitive)."""
    if not supabase:
        return None, None

    try:
        resp = supabase.table("sheet1") \
            .select("*") \
            .ilike("trainer_username", username) \
            .limit(1) \
            .execute()
        records = resp.data or []
        if not records:
            return None, None

        record = records[0]
        record.setdefault("avatar_icon", "avatar1.png")
        record.setdefault("trainer_card_background", "standard.png")
        record.setdefault("fstamp1", "")
        record.setdefault("fstamp2", "")
        record.setdefault("fstamp3", "")
        record.setdefault("team", "")
        record.setdefault("tlevel", None)
        record.setdefault("trainerbio", "")
        record["account_type"] = normalize_account_type(record.get("account_type"))
        return None, record
    except Exception as e:
        print("⚠️ Supabase find_user failed:", e)
        return None, None


def get_public_trainer_profile(username: str) -> Optional[dict]:
    """
    Fetch a sanitized profile payload that can be shared publicly.
    Returns basic trainer card fields only (avatar, background, stamps, etc.).
    """
    if not username:
        return None
    _, record = find_user(username)
    if not record:
        return None
    avatar_file = record.get("avatar_icon") or "avatar1.png"
    background_file = record.get("trainer_card_background") or "standard.png"
    bio = (record.get("trainerbio") or "").strip()
    if len(bio) > 200:
        bio = bio[:200]
    try:
        avatar_url = url_for("static", filename=f"avatars/{avatar_file}")
        background_url = url_for("static", filename=f"backgrounds/{background_file}")
    except RuntimeError:
        avatar_url = f"/static/avatars/{avatar_file}"
        background_url = f"/static/backgrounds/{background_file}"

    team_info = _team_metadata(record.get("team"))
    tlevel_raw = record.get("tlevel")
    try:
        trainer_level = int(tlevel_raw)
    except (TypeError, ValueError):
        trainer_level = None

    return {
        "username": record.get("trainer_username") or username,
        "account_type": record.get("account_type") or "Trainer",
        "campfire_username": record.get("campfire_username"),
        "stamps": record.get("stamps") or 0,
        "avatar_url": avatar_url,
        "background_url": background_url,
        "joined_at": record.get("created_at"),
        "trainer_title": record.get("trainer_title"),
        "trainer_team": record.get("trainer_team"),
        "bio": bio,
        "featured_stamps": _record_featured_stamps(record),
        "team": team_info,
        "trainer_level": trainer_level,
        "is_max_level": bool(trainer_level and trainer_level >= MAX_TRAINER_LEVEL),
    }


def search_trainers(query, limit=10):
    """Fuzzy search trainers by username for admin tooling."""
    if not (supabase and query):
        return []
    pattern = f"%{query.strip()}%"
    try:
        resp = (
            supabase.table("sheet1")
            .select("id, trainer_username, trainer_name, account_type")
            .ilike("trainer_username", pattern)
            .order("trainer_username", desc=False)
            .limit(limit)
            .execute()
        )
        rows = resp.data or []
        results = []
        for row in rows:
            results.append(
                {
                    "username": row.get("trainer_username") or "",
                    "display_name": row.get("trainer_name") or row.get("trainer_username") or "",
                    "account_type": normalize_account_type(row.get("account_type")),
                }
            )
        return results
    except Exception as exc:
        print("⚠️ Trainer search failed:", exc)
        try:
            g.supabase_last_error = str(exc)
        except RuntimeError:
            pass
        return []

def trigger_lugia_refresh():
    url = "https://script.google.com/macros/s/AKfycbwx33Twu9HGwW4bsSJb7vwHoaBS56gCldNlqiNjxGBJEhckVDAnv520MN4ZQWxI1U9D/exec"
    try:
        requests.get(url, params={"action": "lugiaRefresh"}, timeout=10)
    except Exception as e:
        print("⚠️ Lugia refresh error:", e)

import os, requests
LUGIA_URL = os.getenv("LUGIA_WEBAPP_URL")

def adjust_stamps(trainer_username: str, count: int, reason: str, action: str, actor: str = "Admin"):
    if not supabase:
        return False, "Supabase client not initialized on server"

    # validate number
    try:
        n = int(count)
        if n <= 0:
            return False, "Count must be a positive number"
    except Exception:
        return False, "Invalid count"

    delta = n if action == "award" else -n

    try:
        resp = supabase.rpc(
            "lugia_admin_adjust",
            {
                "p_trainer": trainer_username,
                "p_delta": delta,
                "p_reason": reason or "",
                "p_awardedby": actor or "Admin",
            },
        ).execute()
        data = getattr(resp, "data", None) or {}
        new_total = data.get("new_total")
        return True, f"✅ Updated {trainer_username}. New total: {new_total}"
    except Exception as e:
        return False, f"❌ Failed to update: {e}"

@app.route("/admin/trainers/<username>/adjust-stamps", methods=["POST"], endpoint="admin_adjust_stamps_v2")
@app.route("/admin/trainers/<username>/adjust_stamps", methods=["POST"], endpoint="admin_adjust_stamps_legacy")
def admin_adjust_stamps_route(username):
    count = request.form.get("count", "0")
    action = request.form.get("action", "award")
    reason = request.form.get("reason", "")

    actor = (
        session.get("trainer_username")
        or session.get("username")
        or session.get("admin_username")
        or "Admin"
    )

    ok, msg = adjust_stamps(username, count, reason, action, actor)  # ← pass actor
    if _is_trainer_panel_ajax():
        return _panel_action_response(ok, msg, username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_trainer_detail", username=username))

@app.route("/admin/trainers/mass-stamp", methods=["POST"])
def admin_mass_stamp():
    if "trainer" not in session:
        return jsonify({"success": False, "error": "Please log in."}), 401

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        return jsonify({"success": False, "error": "Admins only."}), 403

    payload = request.get_json(silent=True) or {}
    usernames = payload.get("usernames") or []
    amount = payload.get("amount")
    reason = (payload.get("reason") or "").strip()

    if not usernames:
        return jsonify({"success": False, "error": "Select at least one trainer."}), 400

    if not reason:
        return jsonify({"success": False, "error": "Provide a reason for the stamp."}), 400

    try:
        count = int(amount)
        if count <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Amount must be a positive integer."}), 400

    actor = (
        session.get("trainer_username")
        or session.get("username")
        or session.get("admin_username")
        or "Admin"
    )

    successes = []
    failures = []

    for username in dict.fromkeys(usernames):  # dedupe while preserving order
        ok, message = adjust_stamps(username, count, reason, "award", actor)
        entry = {"username": username, "message": message}
        if ok:
            successes.append(entry)
        else:
            failures.append(entry)

    response = {
        "success": not failures,
        "awarded": successes,
        "failed": failures,
        "summary": {
            "total_requested": len(usernames),
            "awarded": len(successes),
            "failed": len(failures),
        },
    }

    if not failures:
        response["message"] = f"✅ Awarded {count} stamp{'s' if count != 1 else ''} to {len(successes)} trainer{'s' if len(successes) != 1 else ''}."
    else:
        response["message"] = "⚠️ Some awards failed. Check details."

    status = 200 if not failures else 207  # multi-status style response
    return jsonify(response), status

def get_classic_submissions_for_trainer(trainer_username: str) -> list[dict]:
    if not (supabase and trainer_username):
        return []
    try:
        resp = (
            supabase.table("classic_passport_submissions")
            .select("*")
            .ilike("trainer_username", trainer_username)
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        print("⚠️ Failed to load classic submissions for trainer:", exc)
        return []

def get_classic_submission(submission_id: str) -> dict | None:
    if not (supabase and submission_id):
        return None
    try:
        resp = (
            supabase.table("classic_passport_submissions")
            .select("*")
            .eq("id", submission_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception as exc:
        print("⚠️ Failed to fetch classic submission:", exc)
        return None

def list_classic_submissions(status: str | None = None) -> list[dict]:
    if not supabase:
        return []
    try:
        query = supabase.table("classic_passport_submissions").select("*").order("created_at", desc=True)
        if status and status.upper() != "ALL":
            query = query.eq("status", status.upper())
        resp = query.execute()
        return resp.data or []
    except Exception as exc:
        print("⚠️ Failed to list classic submissions:", exc)
        return []

# ====== Data: stamps, inbox & meetups ======
def _advent_day_from_reason(reason: str) -> Optional[int]:
    if not reason:
        return None
    match = re.search(r"advent\s+(\d{1,2})", reason, re.IGNORECASE)
    if not match:
        return None
    try:
        day = int(match.group(1))
    except ValueError:
        return None
    if 1 <= day <= 25:
        return day
    return None


def _advent_stamp_icon(day: int) -> Optional[str]:
    try:
        config = load_advent_config()
    except FileNotFoundError:
        return None
    entry = config.get(day) if isinstance(config, dict) else None
    if not entry:
        return None
    stamp_png = (entry.get("stamp_png") or "").strip()
    if not stamp_png:
        return None
    return url_for("static", filename=f"advent/{stamp_png}")


def _advent_stamp_message(day: int) -> Optional[str]:
    try:
        config = load_advent_config()
    except FileNotFoundError:
        return None
    entry = config.get(day) if isinstance(config, dict) else None
    if not entry:
        return None
    text = (entry.get("message") or "").strip()
    return text or None


def _passport_advent_icon(reason: str) -> Optional[str]:
    day = _advent_day_from_reason(reason or "")
    if day is None:
        return None
    return _advent_stamp_icon(day)


def _describe_passport_reason(reason: str, event_name: str | None = None) -> tuple[str, str]:
    """Return a short label + explanation for how a stamp was earned."""
    stamp_name = (reason or "").strip() or "Passport stamp"
    event_title = (event_name or "").strip()
    rl = stamp_name.lower()

    advent_day = _advent_day_from_reason(stamp_name)
    if advent_day:
        quote = _advent_stamp_message(advent_day) or "Advent Calendar stamp unlocked during the 2025 quest."
        return f"Advent Day {advent_day}", quote

    if event_title and event_title.lower() == rl:
        return "Meetup check-in", f"You checked in at {event_title} to receive this stamp."

    reason_map = [
        ("win", "Win", "This stamp is for winning a competition in the community. Well Done!"),
        ("cdl", "CDL", "Stamps for your participation and leaderboard scoring in the CDL."),
        ("classic", "Classic", "Classic passport stamps converted to digital stamps."),
        ("owed", "Owed", "Owed stamps added by the admin team."),
        ("normal", "Normal", "A strange stamp unlocked by strange means. Congrats!"),
        ("test", "Test", "This stamp was a test! Bzzzzt!"),
        ("beta", "Beta", "This stamp was awarded as a thanks for you helping us BETA test the RDAB App, we appreicate you!"),
        ("lccgowa", "lccgowa", "You unlocked this stamp for attending the GO Wild Area 2025: Community Celebration live event in the City of Doncaster alongside thousands of other trainers. Thanks for helping us get our city and community on the map!"),
        ("signup", "Signup", "This stamp was awarded to you for signing up to the RDAB app. Welcome!"),
    ]

    for needle, label, description in reason_map:
        if needle in rl:
            return label, description

    return stamp_name, "Passport stamp unlocked for checking in at a RDAB community meetup."


def _format_passport_awarded_at(raw_ts: str | None) -> tuple[str, str, str]:
    """Return ISO + readable date & time strings for stamp timestamps."""
    if not raw_ts:
        return "", "", ""
    try:
        dt = parser.isoparse(raw_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(LONDON_TZ)
        iso_value = local_dt.isoformat()
        display_date = local_dt.strftime("%d %b %Y")
        display_time = local_dt.strftime("%H:%M")
        return iso_value, display_date, display_time
    except Exception:
        return raw_ts or "", raw_ts or "", ""


def _passport_record_sort_key(record: dict[str, Any]) -> datetime:
    """Best-effort parse of the Supabase timestamp for ordering."""
    raw_ts = record.get("timestamp") or record.get("created_at")
    if not raw_ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = parser.isoparse(raw_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def get_passport_stamps(username: str, campfire_username: str | None = None):
    try:
        # 🔑 Pull all ledger rows where trainer OR campfire matches
        if campfire_username:
            resp = supabase.table("lugia_ledger").select("*") \
                .or_(f"trainer.ilike.{username},campfire.ilike.{campfire_username}") \
                .execute()
        else:
            resp = supabase.table("lugia_ledger").select("*") \
                .ilike("trainer", username) \
                .execute()

        records = resp.data or []
        records.sort(key=_passport_record_sort_key, reverse=True)

        # Fetch event cover photos
        ev_rows = supabase.table("events").select("event_id, cover_photo_url").execute().data or []
        event_map = {
            str(e.get("event_id", "")).strip().lower(): (e.get("cover_photo_url") or "")
            for e in ev_rows
        }

        stamps, total_count = [], 0
        for r in records:
            reason = (r.get("reason") or "").strip()
            event_name = (r.get("eventname") or r.get("event_name") or "").strip()
            try:
                count = int(r.get("count") or 1)
            except (ValueError, TypeError):
                count = 1

            if count <= 0:
                # Negative ledger entries represent stamp removals; skip showing them.
                continue

            total_count += count

            # Handle both eventid and event_id
            event_id = str(r.get("eventid") or r.get("event_id") or "").strip().lower()

            rl = reason.lower()
            advent_icon = _passport_advent_icon(reason)
            if advent_icon:
                icon = advent_icon
            elif rl == "signup bonus":
                icon = url_for("static", filename="icons/signup.png")
            elif "cdl" in rl:
                icon = url_for("static", filename="icons/cdl.png")
            elif "win" in rl:
                icon = url_for("static", filename="icons/win.png")
            elif "normal" in rl:
                icon = url_for("static", filename="icons/normal.png")
            elif "owed" in rl:
                icon = url_for("static", filename="icons/owed.png")
            elif "classic" in rl:
                icon = url_for("static", filename="icons/classic.png")
            elif "lccgowa" in rl:
                icon = url_for("static", filename="icons/gowa.png")
            elif "beta" in rl:
                icon = url_for("static", filename="icons/beta.png")
            
            elif event_id and event_id in event_map and event_map[event_id]:
                icon = event_map[event_id]
            else:
                icon = url_for("static", filename="icons/tickstamp.png")

            reason_label, reason_description = _describe_passport_reason(reason, event_name)
            timestamp_value = r.get("timestamp") or r.get("created_at")
            awarded_iso, awarded_display, awarded_time = _format_passport_awarded_at(timestamp_value)
            stamp_name = reason or event_name or "Passport stamp"
            stamps.append(
                {
                    "name": stamp_name,
                    "count": count,
                    "icon": icon,
                    "reason_label": reason_label,
                    "reason_description": reason_description,
                    "awarded_at_iso": awarded_iso,
                    "awarded_at": awarded_display,
                    "awarded_at_time": awarded_time,
                }
            )

        most_recent = stamps[-1] if stamps else None
        return total_count, stamps, most_recent

    except Exception as e:
        print("⚠️ Supabase get_passport_stamps failed:", e)
        return 0, [], None


def fetch_passport_ledger(username: str, campfire_username: str | None = None, limit: int = 200):
    """Return recent Lugia ledger rows for a trainer sorted by newest first."""
    empty_summary = {"total_entries": 0, "total_awarded": 0, "total_removed": 0, "net_total": 0}
    if not supabase:
        return [], empty_summary

    trainer_lookup = (username or "").strip()
    campfire_lookup = (campfire_username or "").strip()
    if not trainer_lookup and not campfire_lookup:
        return [], empty_summary

    try:
        query = supabase.table("lugia_ledger").select(
            "id, created_at, trainer, campfire, reason, count, awardedby, eventname, eventid"
        )
        if trainer_lookup and campfire_lookup:
            query = query.or_(f"trainer.ilike.{trainer_lookup},campfire.ilike.{campfire_lookup}")
        elif campfire_lookup:
            query = query.ilike("campfire", campfire_lookup)
        else:
            query = query.ilike("trainer", trainer_lookup)

        query = query.order("created_at", desc=True)
        if limit:
            query = query.limit(limit)

        rows = query.execute().data or []

        formatted = []
        total_awarded = 0
        total_removed = 0
        for row in rows:
            try:
                delta = int(row.get("count") or 0)
            except (ValueError, TypeError):
                delta = 0

            if delta > 0:
                total_awarded += delta
            elif delta < 0:
                total_removed += abs(delta)

            created_at = row.get("created_at") or ""
            created_date = ""
            created_time = ""
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    created_date = dt.strftime("%d %b %Y")
                    created_time = dt.strftime("%H:%M")
                except Exception:
                    created_date = created_at

            formatted.append({
                "id": row.get("id"),
                "reason": (row.get("reason") or row.get("eventname") or "").strip() or "Passport stamp",
                "event_name": (row.get("eventname") or "").strip(),
                "count": delta,
                "trainer": row.get("trainer") or "",
                "campfire": row.get("campfire") or "",
                "awarded_by": row.get("awardedby") or row.get("awarded_by") or "",
                "event_id": row.get("eventid") or row.get("event_id") or "",
                "created_at": created_at,
                "created_date": created_date,
                "created_time": created_time,
            })

        summary = {
            "total_entries": len(formatted),
            "total_awarded": total_awarded,
            "total_removed": total_removed,
            "net_total": total_awarded - total_removed,
        }
        return formatted, summary
    except Exception as e:
        print("⚠️ fetch_passport_ledger failed:", e)
        return [], empty_summary

def get_most_recent_meetup(username: str, campfire_username: str | None = None):
    """
    Returns {title, date, icon, event_id} for the user's most recent meetup.
    - Icon is resolved by event_id if present; otherwise by matching events.name to the title.
    """
    try:
        rec = None
        r1 = supabase.table("lugia_summary").select("*").eq("trainer_username", username).limit(1).execute().data
        if r1:
            rec = r1[0]
        elif campfire_username:
            r2 = supabase.table("lugia_summary").select("*").eq("campfire_username", campfire_username).limit(1).execute().data
            if r2:
                rec = r2[0]

        if not rec:
            # return default but truthy so template shows "no data" gracefully if needed
            return {"title": "", "date": "", "icon": url_for("static", filename="icons/tickstamp.png"), "event_id": ""}

        title = (rec.get("most_recent_event") or "").strip()
        date  = (rec.get("most_recent_event_date") or "").strip()
        eid   = (rec.get("most_recent_event_id") or "").strip().lower()

        icon = url_for("static", filename="icons/tickstamp.png")

        # 1) Try by event_id
        if eid:
            ev = supabase.table("events").select("event_id, cover_photo_url").eq("event_id", eid).limit(1).execute().data or []
            if ev and (ev[0].get("cover_photo_url") or ""):
                icon = ev[0]["cover_photo_url"]
        # 2) Fallback: resolve by name
        if not icon or icon.endswith("tickstamp.png"):
            by_name = cover_from_event_name(title)
            if by_name:
                icon = by_name

        return {"title": title, "date": date, "icon": icon, "event_id": eid}
    except Exception as e:
        print("⚠️ Supabase get_most_recent_meetup failed:", e)
        return {"title": "", "date": "", "icon": url_for("static", filename="icons/tickstamp.png"), "event_id": ""}

def get_meetup_history(username: str, campfire_username: str | None = None):
    """Build full meet-up history using attendance with CHECKED_IN filter."""
    if USE_SUPABASE and supabase:
        try:
            # Step 1: Get attendance rows by campfire or display_name
            records = []
            if campfire_username:
                records = supabase.table("attendance") \
                    .select("event_id, rsvp_status") \
                    .ilike("campfire_username", campfire_username) \
                    .execute().data or []

            if not records:
                records = supabase.table("attendance") \
                    .select("event_id, rsvp_status") \
                    .ilike("display_name", username) \
                    .execute().data or []

            if not records:
                return [], 0

            # Step 2: Only keep CHECKED_IN events (case-insensitive)
            raw_id_lookup: dict[str, Any] = {}
            checked_in_ids: list[str] = []
            for record in records:
                status = str(record.get("rsvp_status", "")).upper()
                raw_event_id = record.get("event_id")
                normalized = str(raw_event_id or "").strip().lower()
                if not normalized or status != "CHECKED_IN":
                    continue
                raw_id_lookup.setdefault(normalized, raw_event_id)
                checked_in_ids.append(normalized)

            if not checked_in_ids:
                return [], 0

            # Step 3: Fetch events table
            ev_rows = supabase.table("events") \
                .select("event_id, name, start_time, cover_photo_url") \
                .execute().data or []

            ev_map = {str(e.get("event_id", "")).strip().lower(): e for e in ev_rows}

            # Step 3b: Fetch check-in counts for each event
            checkins_by_event: dict[str, int] = defaultdict(int)
            unique_ids = sorted(set(checked_in_ids))
            chunk_size = 90
            for i in range(0, len(unique_ids), chunk_size):
                chunk_norm = unique_ids[i:i + chunk_size]
                chunk_raw = [raw_id_lookup.get(nid) for nid in chunk_norm if raw_id_lookup.get(nid)]
                if not chunk_raw:
                    continue
                rows = supabase.table("attendance") \
                    .select("event_id, rsvp_status") \
                    .in_("event_id", chunk_raw) \
                    .execute().data or []
                for row in rows:
                    eid_norm = str(row.get("event_id", "")).strip().lower()
                    if not eid_norm:
                        continue
                    if str(row.get("rsvp_status", "")).upper() == "CHECKED_IN":
                        checkins_by_event[eid_norm] += 1

            # Step 4: Build meetups list
            meetups = []
            for eid in checked_in_ids:
                ev = ev_map.get(eid)
                if ev:
                    meetups.append({
                        "event_id": ev.get("event_id") or eid,
                        "title": ev.get("name", "Unknown Event"),
                        "date": ev.get("start_time", ""),
                        "photo": ev.get("cover_photo_url", ""),
                        "checkins": checkins_by_event.get(eid, 0),
                    })

            # Step 5: Sort newest first
            meetups.sort(key=lambda m: m["date"], reverse=True)
            return meetups, len(meetups)

        except Exception as e:
            print("⚠️ Supabase get_meetup_history failed:", e)

    return [], 0

def cover_from_event_name(event_name: str) -> str:
    """
    Find an event cover photo by matching the events.name field to event_name.
    Tries exact (case-insensitive) first, then a wildcard match.
    Returns cover_photo_url or "".
    """
    if not (supabase and event_name):
        return ""
    try:
        # exact (case-insensitive)
        exact = supabase.table("events") \
            .select("name, cover_photo_url") \
            .ilike("name", event_name) \
            .limit(1).execute().data or []
        if exact:
            return exact[0].get("cover_photo_url") or ""

        # wildcard
        like = supabase.table("events") \
            .select("name, cover_photo_url") \
            .ilike("name", f"%{event_name}%") \
            .limit(1).execute().data or []
        if like:
            return like[0].get("cover_photo_url") or ""
    except Exception as e:
        print("⚠️ cover_from_event_name failed:", e)
    return ""

def get_inbox_preview(trainer: str, limit: int = 3):
    """Fetch recent notifications + unread count."""
    if not supabase:
        return []
    try:
        # Fetch ALL + user-targeted
        resp = (supabase.table("notifications")
                .select("id, subject, message, sent_at, read_by")
                .or_(f"audience.eq.{trainer},audience.eq.ALL")
                .order("sent_at", desc=True)
                .limit(limit)
                .execute())
        preview = resp.data or []

        # Unread count
        unread_resp = (supabase.table("notifications")
                       .select("id, read_by")
                       .or_(f"audience.eq.{trainer},audience.eq.ALL")
                       .execute())
        unread_count = sum(1 for n in unread_resp.data or [] if trainer not in (n.get("read_by") or []))

        return {"preview": preview, "unread_count": unread_count}
    except Exception as e:
        print("⚠️ Supabase inbox preview fetch failed:", e)
        return {"preview": [], "unread_count": 0}

NOTIFICATION_ALLOWED_TAGS = [
    "a",
    "b",
    "br",
    "em",
    "i",
    "li",
    "p",
    "strong",
    "u",
    "ul",
    "ol",
]
NOTIFICATION_ALLOWED_ATTRS = {
    "a": ["href", "target", "rel", "title"],
}

DIGITAL_CODE_TABLE = os.environ.get("DIGITAL_CODE_TABLE", "digital_reward_codes")
DIGITAL_CODE_STATUS_AVAILABLE = "AVAILABLE"
DIGITAL_CODE_STATUS_REDEEMED = "REDEEMED"
try:
    DIGITAL_CODE_UPLOAD_LIMIT = max(1, int(os.environ.get("DIGITAL_CODE_UPLOAD_LIMIT", 400)))
except (TypeError, ValueError):
    DIGITAL_CODE_UPLOAD_LIMIT = 400

DEFAULT_DIGITAL_CODE_SUBJECT = "Here’s your digital code"
DIGITAL_CODE_DEFAULT_CATEGORY = os.environ.get("DIGITAL_CODE_DEFAULT_CATEGORY", "General")
DIGITAL_CODE_CATEGORY_SUGGESTIONS = [
    value.strip()
    for value in os.environ.get("DIGITAL_CODE_CATEGORIES", "").split(",")
    if value.strip()
]
DIGITAL_CODE_SOURCE_DEFAULT = os.environ.get("DIGITAL_CODE_SOURCE_DEFAULT", "admin_dashboard_manual")
DIGITAL_CODE_SOURCE_SUGGESTIONS = [
    value.strip()
    for value in os.environ.get("DIGITAL_CODE_SOURCES", "admin_dashboard_manual,catalog_redemption,advent_event").split(",")
    if value.strip()
]
MAX_DIGITAL_CODE_CATEGORY_LENGTH = 80
MAX_DIGITAL_CODE_SOURCE_LENGTH = 80


def sanitize_notification_html(body: str | None) -> str:
    if not body:
        return ""
    normalized = body.replace("\r\n", "\n")
    normalized = normalized.replace("\n", "<br>")
    cleaned = bleach.clean(
        normalized,
        tags=NOTIFICATION_ALLOWED_TAGS,
        attributes=NOTIFICATION_ALLOWED_ATTRS,
        strip=True,
    )
    return cleaned


def send_notification(audience, subject, message, notif_type="system", metadata=None, *, returning: bool = False):
    """Send an inbox notification. When returning=True, return the inserted row (if available)."""
    if not supabase:
        print("⚠️ Failed to send notification: Supabase is unavailable.")
        return None

    message_html = sanitize_notification_html(message)
    payload = {
        "type": notif_type,
        "audience": audience,
        "subject": subject,
        "message": message_html,
        "metadata": metadata or {},
        "sent_at": datetime.utcnow().isoformat(),
        "read_by": [],
    }
    try:
        insert_kwargs = {"returning": "representation"} if returning else {}
        resp = supabase.table("notifications").insert(payload, **insert_kwargs).execute()
        if returning:
            rows = resp.data or []
            return rows[0] if rows else payload
    except Exception as e:
        print("⚠️ Failed to send notification:", e)
        return None
    return None

# ====== Digital reward codes ======
def _digital_codes_supported() -> bool:
    return bool(USE_SUPABASE and supabase and DIGITAL_CODE_TABLE)


def _format_admin_timestamp(raw_ts: str | None) -> str:
    if not raw_ts:
        return ""
    try:
        dt = parser.isoparse(raw_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LONDON_TZ).strftime("%d %b %Y · %H:%M")
    except Exception:
        return raw_ts or ""


def _prepare_category_value(raw: str | None, *, required: bool = True) -> str | None:
    value = (raw or "").strip()
    if not value:
        return DIGITAL_CODE_DEFAULT_CATEGORY if required else None
    return value[:MAX_DIGITAL_CODE_CATEGORY_LENGTH]


def _prepare_source_value(raw: str | None, *, default_to_admin: bool = True) -> str:
    value = (raw or "").strip()
    if not value:
        return DIGITAL_CODE_SOURCE_DEFAULT if default_to_admin else "system"
    return value[:MAX_DIGITAL_CODE_SOURCE_LENGTH]


def _infer_assigner_metadata(actor: str | None, actor_type_hint: str | None = None) -> tuple[str, str]:
    """
    Returns (assigner_type, assigner_label) where type is one of admin|feature|system.
    """
    label = (actor or "").strip() or "System"
    normalized_hint = (actor_type_hint or "").strip().lower()
    if normalized_hint in {"admin", "feature", "system"}:
        assigner_type = normalized_hint
    else:
        assigner_type = "feature" if label and label != "System" else "system"

    if assigner_type == "admin":
        return assigner_type, label

    _, record = find_user(label)
    if record and record.get("account_type") == "Admin":
        return "admin", record.get("trainer_username") or label

    return assigner_type, label


def _describe_assignment_origin(source: str | None, assigned_by_type: str, assigned_by_label: str | None) -> str:
    key = (source or "").strip().lower()
    if "catalog" in key:
        return "You redeemed this via the RDAB Catalog."
    if "advent" in key:
        return "Unlocked during the Advent Calendar 2025 event."
    if assigned_by_type == "admin":
        ambassador = (assigned_by_label or "A Community Ambassador").strip()
        return f"{ambassador} (Community Ambassador) gifted this reward to you."
    return "You received this digital reward from RDAB."


def _reward_summary_for_category(category: str | None, custom_text: str | None) -> str:
    slug = (category or "").strip().lower()
    if slug == "digital":
        return "Redeem this code for 1 Premium Battle Pass, 1 Star Piece, 1 Incubator, and 1 Lure Module."
    if slug == "ca":
        return "Redeem this code for 2 Premium Battle Passes and 1 Star Piece."
    custom = (custom_text or "").strip()
    if custom:
        return f"Redeem this code for {custom}."
    return "Redeem this code for exclusive RDAB rewards."


def _build_digital_code_message(code_value: str, category: str | None, assignment_source: str | None, assigned_by_type: str, assigned_by_label: str | None, custom_description: str | None) -> tuple[str, str]:
    origin_sentence = _describe_assignment_origin(assignment_source, assigned_by_type, assigned_by_label)
    reward_sentence = _reward_summary_for_category(category, custom_description)
    redeem_url = f"https://store.pokemongo.com/offer-redemption?passcode={quote_plus(code_value)}"
    message = (
        "<p><strong>Here’s your digital code.</strong></p>"
        f"<p>{origin_sentence}</p>"
        f"<p><a href=\"{redeem_url}\" target=\"_blank\" rel=\"noopener\">{code_value}</a></p>"
        f"<p>{reward_sentence}</p>"
        "<p><strong>How to redeem:</strong></p>"
        "<ol>"
        "<li><strong>Step 1.</strong> Tap the code above to open the Pokémon GO offer redemption site.</li>"
        "<li><strong>Step 2.</strong> Sign in with the Pokémon GO account you use with RDAB.</li>"
        "<li><strong>Step 3.</strong> Redeem your code and enjoy the items!</li>"
        "</ol>"
        "<p>If you have any issues, please reach out to us via RDAB Support.</p>"
    )
    return message, reward_sentence


def _parse_code_batch(raw_codes: str) -> list[str]:
    if not raw_codes:
        return []
    cleaned = raw_codes.replace("\r\n", "\n")
    tokens = re.split(r"[,\n]", cleaned)
    codes = []
    for token in tokens:
        value = token.strip()
        if value:
            codes.append(value)
    return codes


def _chunked(seq: list[str], size: int = 80):
    for idx in range(0, len(seq), size):
        yield seq[idx: idx + size]


def _reset_digital_code(code_id: str):
    if not _digital_codes_supported() or not code_id:
        return
    try:
        supabase.table(DIGITAL_CODE_TABLE).update({
            "status": DIGITAL_CODE_STATUS_AVAILABLE,
            "assigned_to": None,
            "assigned_by": None,
            "assigned_at": None,
            "notification_id": None,
            "notification_subject": None,
        }).eq("id", code_id).execute()
    except Exception as exc:
        print("⚠️ digital code reset failed:", exc)


def add_digital_codes_from_payload(raw_codes: str, actor: str, batch_label: str | None = None, category: str | None = None):
    summary = {
        "total": 0,
        "inserted": 0,
        "duplicates": 0,
        "existing": 0,
        "unique": 0,
    }
    if not _digital_codes_supported():
        return False, "Digital codes require Supabase to be enabled.", summary

    category_value = _prepare_category_value(category, required=True)
    parsed = _parse_code_batch(raw_codes)
    summary["total"] = len(parsed)
    if not parsed:
        return False, "Paste at least one code separated by commas.", summary
    if len(parsed) > DIGITAL_CODE_UPLOAD_LIMIT:
        return False, f"Upload up to {DIGITAL_CODE_UPLOAD_LIMIT} codes at once.", summary

    deduped: list[str] = []
    seen = set()
    for code in parsed:
        key = code.lower()
        if key in seen:
            summary["duplicates"] += 1
            continue
        seen.add(key)
        deduped.append(code)
    summary["unique"] = len(deduped)
    if not deduped:
        return False, "All provided codes were duplicates.", summary

    existing_lookup: set[str] = set()
    try:
        for chunk in _chunked(deduped, 90):
            resp = (
                supabase.table(DIGITAL_CODE_TABLE)
                .select("code")
                .in_("code", chunk)
                .execute()
            )
            for row in resp.data or []:
                value = (row.get("code") or "").strip().lower()
                if value:
                    existing_lookup.add(value)
    except Exception as exc:
        print("⚠️ digital code lookup failed:", exc)
        return False, "Could not verify existing codes. Try again.", summary

    fresh_codes = [code for code in deduped if code.lower() not in existing_lookup]
    summary["existing"] = len(deduped) - len(fresh_codes)
    if not fresh_codes:
        return False, "Every code you entered already exists in Supabase.", summary

    payloads = []
    for code in fresh_codes:
        payloads.append({
            "code": code,
            "status": DIGITAL_CODE_STATUS_AVAILABLE,
            "batch_label": (batch_label or None),
             "category": category_value,
            "uploaded_by": actor,
            "assigned_to": None,
            "assigned_by": None,
            "assigned_by_type": None,
            "assigned_source": None,
            "redeemed_at": None,
        })
    try:
        supabase.table(DIGITAL_CODE_TABLE).insert(payloads).execute()
    except Exception as exc:
        print("⚠️ Failed to insert digital codes:", exc)
        return False, "Failed to store the codes in Supabase.", summary

    summary["inserted"] = len(payloads)
    fragment = "code" if summary["inserted"] == 1 else "codes"
    message_bits = [f"✅ Added {summary['inserted']} {fragment}."]
    if summary["existing"]:
        message_bits.append(f"Skipped {summary['existing']} already in Supabase.")
    if summary["duplicates"]:
        dup_fragment = "duplicate" if summary["duplicates"] == 1 else "duplicates"
        message_bits.append(f"Ignored {summary['duplicates']} {dup_fragment} in this upload.")
    return True, " ".join(message_bits), summary


def fetch_digital_code_summary(limit_available: int = 12, limit_history: int = 8) -> dict:
    summary = {
        "supported": _digital_codes_supported(),
        "available_count": 0,
        "assigned_count": 0,
        "available_codes": [],
        "assigned_recent": [],
        "upload_limit": DIGITAL_CODE_UPLOAD_LIMIT,
        "error": None,
        "available_buckets": [],
        "available_bucket_totals": [],
        "category_options": sorted({DIGITAL_CODE_DEFAULT_CATEGORY, *DIGITAL_CODE_CATEGORY_SUGGESTIONS}),
        "source_suggestions": sorted(
            set(DIGITAL_CODE_SOURCE_SUGGESTIONS or [DIGITAL_CODE_SOURCE_DEFAULT])
        ),
        "default_category": DIGITAL_CODE_DEFAULT_CATEGORY,
    }
    if not summary["supported"]:
        return summary

    try:
        available_resp = (
            supabase.table(DIGITAL_CODE_TABLE)
            .select("id, code, created_at, batch_label, category", count="exact")
            .eq("status", DIGITAL_CODE_STATUS_AVAILABLE)
            .order("created_at", desc=True)
            .limit(limit_available)
            .execute()
        )
        summary["available_count"] = available_resp.count or 0
        bucket_map: dict[str, dict] = {}
        available_rows = []
        for row in available_resp.data or []:
            category_label = _prepare_category_value(row.get("category"), required=True)
            record = {
                "id": row.get("id"),
                "code": row.get("code"),
                "created_at": row.get("created_at"),
                "created_display": _format_admin_timestamp(row.get("created_at")),
                "batch_label": row.get("batch_label"),
                "category": category_label,
            }
            available_rows.append(record)
            bucket_key = (category_label or DIGITAL_CODE_DEFAULT_CATEGORY).lower()
            bucket = bucket_map.setdefault(bucket_key, {
                "category": category_label,
                "count": 0,
                "codes": [],
            })
            bucket["count"] += 1
            bucket["codes"].append(record)
        summary["available_codes"] = available_rows
        summary["available_buckets"] = sorted(bucket_map.values(), key=lambda item: (item["category"] or ""))
        summary["category_options"].extend([bucket["category"] for bucket in summary["available_buckets"]])
    except Exception as exc:
        print("⚠️ digital code summary (available) failed:", exc)
        summary["error"] = "Failed to load digital code inventory."
        return summary

    try:
        totals_resp = (
            supabase.table(DIGITAL_CODE_TABLE)
            .select("category, count:id")
            .eq("status", DIGITAL_CODE_STATUS_AVAILABLE)
            .group("category")
            .execute()
        )
        totals_rows = []
        for row in totals_resp.data or []:
            category_label = _prepare_category_value(row.get("category"), required=True)
            counts = row.get("count") or row.get("count:id") or 0
            try:
                counts = int(counts)
            except (TypeError, ValueError):
                counts = 0
            totals_rows.append({
                "category": category_label,
                "count": counts,
            })
            summary["category_options"].append(category_label)
        summary["available_bucket_totals"] = sorted(totals_rows, key=lambda item: (item["category"] or ""))
    except Exception as exc:
        print("⚠️ digital code bucket totals failed:", exc)

    try:
        assigned_resp = (
            supabase.table(DIGITAL_CODE_TABLE)
            .select(
                "id, code, assigned_to, assigned_by, assigned_by_type, assigned_source, redeemed_at, batch_label, notification_subject, category"
            )
            .eq("status", DIGITAL_CODE_STATUS_REDEEMED)
            .order("redeemed_at", desc=True)
            .limit(limit_history)
            .execute()
        )
        summary["assigned_count"] = assigned_resp.count or 0
        summary["assigned_recent"] = [
            {
                "id": row.get("id"),
                "code": row.get("code"),
                "assigned_to": row.get("assigned_to"),
                "assigned_by": row.get("assigned_by"),
                "assigned_by_type": row.get("assigned_by_type"),
                "assigned_source": row.get("assigned_source"),
                "redeemed_at": row.get("redeemed_at"),
                "redeemed_display": _format_admin_timestamp(row.get("redeemed_at")),
                "batch_label": row.get("batch_label"),
                "subject": row.get("notification_subject"),
                "category": _prepare_category_value(row.get("category"), required=True),
            }
            for row in assigned_resp.data or []
        ]
    except Exception as exc:
        print("⚠️ digital code summary (history) failed:", exc)
        summary["error"] = summary["error"] or "Failed to load delivery history."

    try:
        distinct_resp = (
            supabase.table(DIGITAL_CODE_TABLE)
            .select("category", distinct=True)
            .execute()
        )
        for row in distinct_resp.data or []:
            label = _prepare_category_value(row.get("category"), required=True)
            if label:
                summary["category_options"].append(label)
    except Exception as exc:
        print("⚠️ digital code categories fetch failed:", exc)

    summary["category_options"] = sorted(set(filter(None, summary["category_options"])))

    return summary


def assign_digital_code_to_trainer(
    trainer_username: str,
    actor: str,
    code_id: str | None,
    subject: str | None,
    assignment_source: str | None = None,
    preferred_category: str | None = None,
    actor_type_hint: str | None = None,
    reward_description: str | None = None,
):
    if not _digital_codes_supported():
        return False, "Digital codes require Supabase."

    trainer_value = (trainer_username or "").strip()
    if not trainer_value:
        return False, "Enter a trainer username."

    _, trainer_record = find_user(trainer_value)
    if not trainer_record:
        return False, f"Trainer “{trainer_value}” was not found."
    resolved_trainer = trainer_record.get("trainer_username") or trainer_value

    preferred_category_value = _prepare_category_value(preferred_category, required=False)

    try:
        query = (
            supabase.table(DIGITAL_CODE_TABLE)
            .select("id, code, batch_label, category")
            .eq("status", DIGITAL_CODE_STATUS_AVAILABLE)
        )
        if code_id:
            query = query.eq("id", code_id)
        elif preferred_category_value:
            query = query.eq("category", preferred_category_value)
        else:
            query = query.order("created_at")
        resp = query.limit(1).execute()
    except Exception as exc:
        print("⚠️ digital code fetch failed:", exc)
        return False, "Failed to load an available code."

    rows = resp.data or []
    if not rows:
        if code_id:
            return False, "Selected code is no longer available."
        if preferred_category_value:
            return False, f"No available codes remain in the “{preferred_category_value}” bucket. Upload more codes first."
        return False, "No available codes remain. Upload more codes first."

    code_row = rows[0]
    selected_id = code_row.get("id")
    code_value = (code_row.get("code") or "").strip()
    if not selected_id or not code_value:
        return False, "The selected code is invalid."

    preferred_category_label = preferred_category_value or code_row.get("category")
    assigned_by_type, assigned_by_label = _infer_assigner_metadata(actor, actor_type_hint)
    assignment_source_value = _prepare_source_value(
        assignment_source,
        default_to_admin=(assigned_by_type == "admin"),
    )
    redeemed_at_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    reserve_payload = {
        "status": DIGITAL_CODE_STATUS_REDEEMED,
        "assigned_to": resolved_trainer,
        "assigned_by": assigned_by_label,
        "assigned_by_type": assigned_by_type,
        "assigned_source": assignment_source_value,
        "redeemed_at": redeemed_at_iso,
    }
    try:
        reserve_resp = (
            supabase.table(DIGITAL_CODE_TABLE)
            .update(reserve_payload, returning="representation")
            .eq("id", selected_id)
            .eq("status", DIGITAL_CODE_STATUS_AVAILABLE)
            .execute()
        )
        updated_rows = reserve_resp.data or []
    except Exception as exc:
        print("⚠️ digital code reserve failed:", exc)
        return False, "Failed to reserve the code. Try again."
    if not updated_rows:
        return False, "That code was already redeemed. Refresh the panel."

    subject_value = (subject or DEFAULT_DIGITAL_CODE_SUBJECT).strip() or DEFAULT_DIGITAL_CODE_SUBJECT
    message_with_code, reward_summary = _build_digital_code_message(
        code_value,
        preferred_category_label,
        assignment_source_value,
        assigned_by_type,
        assigned_by_label,
        reward_description,
    )
    metadata = {
        "reward_code": code_value,
        "reward_code_id": selected_id,
        "reward_code_batch": code_row.get("batch_label"),
        "reward_code_category": _prepare_category_value(code_row.get("category"), required=True),
        "digital_code_assigned_by": assigned_by_label,
        "digital_code_assigned_type": assigned_by_type,
        "digital_code_assigned_source": assignment_source_value,
        "reward_summary": reward_summary,
    }
    notification_row = send_notification(
        resolved_trainer,
        subject_value,
        message_with_code,
        notif_type="prize",
        metadata=metadata,
        returning=True,
    )
    if not notification_row:
        _reset_digital_code(selected_id)
        return False, "Could not send the inbox notification. The code was returned to the pool."

    follow_up = {
        "notification_id": notification_row.get("id"),
        "notification_subject": subject_value,
    }
    try:
        supabase.table(DIGITAL_CODE_TABLE).update(follow_up).eq("id", selected_id).execute()
    except Exception as exc:
        print("⚠️ digital code follow-up update failed:", exc)

    return True, f"🎉 Sent code {code_value} to {resolved_trainer}."

# ====== Admin Panel ======
from functools import wraps
from flask import session, redirect, url_for, flash

# --- Admin utilities: change account type & reset PIN ---

import os, re, hashlib
from flask import request, redirect, url_for, flash, session, abort

ALLOWED_ACCOUNT_TYPES = {
    "standard": "Standard",
    "kids account": "Kids Account",
    "admin": "Admin",
}

def _current_actor():
    return (
        session.get("trainer")
        or session.get("trainer_username")
        or session.get("username")
        or session.get("admin_username")
        or "Admin"
    )

def change_account_type(trainer_username: str, new_type: str, actor: str = "Admin"):
    if not new_type:
        return False, "Please choose an account type."

    norm = new_type.strip().lower()
    if norm not in ALLOWED_ACCOUNT_TYPES:
        return False, f"Invalid account type: {new_type}"

    label = ALLOWED_ACCOUNT_TYPES[norm]
    try:
        resp = supabase.table("sheet1").update({"account_type": label}) \
            .eq("trainer_username", trainer_username).execute()
        data = getattr(resp, "data", None)
        if not data:
            return False, f"Trainer not found: {trainer_username}"
        return True, f"✅ {trainer_username} is now “{label}”."
    except Exception as e:
        return False, f"❌ Failed to change account type: {e}"

PIN_SALT = os.getenv("PIN_SALT", "static-fallback-salt")  # set a real secret in prod!

def _hash_pin(pin: str, username: str) -> str:
    # Simple salted SHA256: adequate for a 4-digit PIN admin reset flow
    s = f"{PIN_SALT}:{username}:{pin}".encode("utf-8")
    return hashlib.sha256(s).hexdigest()

def _pin_hash_value(username: str | None, pin: str) -> str:
    username = (username or "").strip()
    if username:
        return _hash_pin(pin, username)
    return hash_value(pin)

def _pin_matches(user_record: dict | None, candidate_pin: str) -> bool:
    if not user_record or not candidate_pin:
        return False
    trainer_username = (
        user_record.get("trainer_username")
        or user_record.get("Trainer Username")
        or ""
    )
    stored_hash = (
        user_record.get("pin_hash")
        or user_record.get("PIN Hash")
        or ""
    )
    trainer_username = (trainer_username or "").strip()
    stored_hash = (stored_hash or "").strip()
    if not stored_hash:
        return False
    if trainer_username and stored_hash == _hash_pin(candidate_pin, trainer_username):
        return True
    # Legacy fallback for older unsalted hashes
    return stored_hash == hash_value(candidate_pin)

def reset_pin(trainer_username: str, new_pin: str, actor: str = "Admin"):
    if not re.fullmatch(r"\d{4}", new_pin or ""):
        return False, "PIN must be exactly 4 digits."

    # First try a hashed column if your schema has one (pin_hash)
    try:
        hashed = _hash_pin(new_pin, trainer_username)
        resp = supabase.table("sheet1").update({"pin_hash": hashed}) \
            .eq("trainer_username", trainer_username).execute()
        data = getattr(resp, "data", None)
        if data:
            return True, "✅ PIN reset."
    except Exception:
        # If the column doesn't exist or update fails, fall back to plaintext 'pin'
        pass

    # Fallback to plaintext column 'pin' (if that's how your current schema stores it)
    try:
        resp = supabase.table("sheet1").update({"pin": new_pin}) \
            .eq("trainer_username", trainer_username).execute()
        data = getattr(resp, "data", None)
        if data:
            return True, "✅ PIN reset."
        return False, f"Trainer not found: {trainer_username}"
    except Exception as e:
        return False, f"❌ Failed to reset PIN: {e}"

def _admin_dashboard_gate_enabled() -> bool:
    return bool(ADMIN_DASHBOARD_PASSWORD_HASH or ADMIN_DASHBOARD_PASSWORD)


def _admin_dashboard_gate_verified() -> bool:
    gate_state = session.get("admin_dashboard_gate") or {}
    verified_at = gate_state.get("verified_at")
    if verified_at is None:
        return False
    try:
        verified_ts = float(verified_at)
    except (TypeError, ValueError):
        session.pop("admin_dashboard_gate", None)
        return False

    ttl = ADMIN_DASHBOARD_GATE_TTL_SECONDS
    if ttl <= 0:
        return True

    if time.time() - verified_ts < ttl:
        return True

    session.pop("admin_dashboard_gate", None)
    return False


def _admin_dashboard_gate_check(password: str) -> bool:
    if not password:
        return False
    if ADMIN_DASHBOARD_PASSWORD_HASH:
        return hash_value(password) == ADMIN_DASHBOARD_PASSWORD_HASH
    if ADMIN_DASHBOARD_PASSWORD:
        return password == ADMIN_DASHBOARD_PASSWORD
    return False


def _load_admin_gate_security_state() -> dict:
    state = session.get("admin_dashboard_security")
    if not isinstance(state, dict):
        state = {}
    return state


def _save_admin_gate_security_state(state: dict) -> None:
    session["admin_dashboard_security"] = state


def _build_admin_gate_status_payload() -> dict:
    gate_required = _admin_dashboard_gate_enabled()
    verified = _admin_dashboard_gate_verified()
    security_state = _load_admin_gate_security_state()

    try:
        remaining = int(security_state.get("remaining", ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS))
    except (TypeError, ValueError):
        remaining = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
    remaining = max(0, min(remaining, ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS))

    lockout_seconds = None
    locked = False
    raw_lock_until = security_state.get("lock_until")
    lock_until_ts = None
    if raw_lock_until is not None:
        try:
            lock_until_ts = float(raw_lock_until)
        except (TypeError, ValueError):
            lock_until_ts = None
    if lock_until_ts:
        now_ts = time.time()
        if now_ts < lock_until_ts:
            locked = True
            lockout_seconds = max(int(lock_until_ts - now_ts), 1)
        else:
            security_state["lock_until"] = None
            _save_admin_gate_security_state(security_state)

    return {
        "enabled": gate_required,
        "verified": bool(verified or not gate_required),
        "locked": locked,
        "lockout_seconds": lockout_seconds,
        "remaining_attempts": remaining,
        "ttl_seconds": ADMIN_DASHBOARD_GATE_TTL_SECONDS,
    }


@app.context_processor
def inject_admin_gate_flags():
    return {
        "admin_gate_enabled": _admin_dashboard_gate_enabled(),
        "admin_gate_verified": _admin_dashboard_gate_verified(),
    }


def _is_trainer_panel_ajax() -> bool:
    """Detect if the current request came from the trainer panel modal."""
    try:
        header_value = request.headers.get("X-Requested-With", "")
    except RuntimeError:
        return False
    return header_value.lower() == "xmlhttprequest"


def _panel_action_response(success: bool, message: str, username: str, *, status_code: int | None = None, extra: dict | None = None):
    payload = {
        "success": bool(success),
        "message": message,
        "redirect_username": username,
        "panel_url": url_for("admin_trainer_panel", username=username),
        "passport_url": url_for("admin_trainer_passport", username=username),
    }
    if extra:
        payload.update(extra)
    status = status_code if status_code is not None else (200 if success else 400)
    return jsonify(payload), status


def _deny_admin_request(message: str = "Admins only."):
    """Abort with JSON when possible so the modal can handle errors gracefully."""
    if _is_trainer_panel_ajax():
        abort(make_response(jsonify({"success": False, "message": message}), 403))
    abort(403)


def _require_admin():
    trainer_username = session.get("trainer")
    if not trainer_username:
        _deny_admin_request()
    _, admin_user = find_user(trainer_username)
    if not admin_user or (admin_user.get("account_type") or "").lower() != "admin":
        _deny_admin_request()
    if _admin_dashboard_gate_enabled() and not _admin_dashboard_gate_verified():
        _deny_admin_request("Admin quick menu is locked.")
    session["account_type"] = "Admin"


def _current_admin_user_record():
    trainer_username = session.get("trainer")
    if not trainer_username:
        return None
    _, admin_user = find_user(trainer_username)
    if not admin_user or (admin_user.get("account_type") or "").lower() != "admin":
        return None
    return admin_user


@app.route("/admin/gate/status", methods=["GET"])
def admin_gate_status():
    trainer_username = session.get("trainer")
    if not trainer_username:
        return jsonify({"success": False, "message": "Please log in."}), 401
    admin_user = _current_admin_user_record()
    if not admin_user:
        return jsonify({"success": False, "message": "Admins only."}), 403
    return jsonify(_build_admin_gate_status_payload())


@app.route("/admin/gate/unlock", methods=["POST"])
def admin_gate_unlock():
    trainer_username = session.get("trainer")
    if not trainer_username:
        return jsonify({"success": False, "message": "Please log in."}), 401
    admin_user = _current_admin_user_record()
    if not admin_user:
        return jsonify({"success": False, "message": "Admins only."}), 403

    if not _admin_dashboard_gate_enabled():
        session["admin_dashboard_gate"] = {"verified_at": time.time()}
        session.pop("admin_dashboard_security", None)
        return jsonify({
            "success": True,
            "message": "Admin tools unlocked.",
            "status": _build_admin_gate_status_payload(),
        })

    payload = request.get_json(silent=True) or {}
    submitted_password = payload.get("admin_password")
    if submitted_password is None:
        submitted_password = request.form.get("admin_password")
    submitted_password = (submitted_password or "").strip()
    if not submitted_password:
        return jsonify({"success": False, "message": "Enter the admin password."}), 400

    security_state = _load_admin_gate_security_state()
    try:
        remaining = int(security_state.get("remaining", ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS))
    except (TypeError, ValueError):
        remaining = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
    remaining = max(0, remaining)

    now_ts = time.time()
    lock_until_raw = security_state.get("lock_until")
    lock_until_ts = None
    if lock_until_raw is not None:
        try:
            lock_until_ts = float(lock_until_raw)
        except (TypeError, ValueError):
            lock_until_ts = None

    if lock_until_ts and now_ts < lock_until_ts:
        wait_seconds = max(int(lock_until_ts - now_ts), 1)
        security_state["remaining"] = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
        _save_admin_gate_security_state(security_state)
        status_payload = _build_admin_gate_status_payload()
        return jsonify({
            "success": False,
            "message": f"Too many incorrect attempts. Try again in {wait_seconds} seconds.",
            "status": status_payload,
        }), 423

    if _admin_dashboard_gate_check(submitted_password):
        session["admin_dashboard_gate"] = {"verified_at": time.time()}
        security_state["remaining"] = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
        security_state["lock_until"] = None
        _save_admin_gate_security_state(security_state)
        return jsonify({
            "success": True,
            "message": "Admin tools unlocked.",
            "status": _build_admin_gate_status_payload(),
        })

    remaining = max(remaining - 1, 0)
    security_state["remaining"] = remaining
    status_code = 403
    if remaining <= 0:
        security_state["lock_until"] = now_ts + ADMIN_DASHBOARD_GATE_LOCK_SECONDS
        security_state["remaining"] = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
        status_code = 423
        error_message = f"Too many incorrect attempts. Try again in {ADMIN_DASHBOARD_GATE_LOCK_SECONDS} seconds."
    else:
        security_state["lock_until"] = None
        attempt_word = "attempt" if remaining == 1 else "attempts"
        error_message = f"Incorrect admin password. {remaining} {attempt_word} remaining."

    _save_admin_gate_security_state(security_state)
    return jsonify({
        "success": False,
        "message": error_message,
        "status": _build_admin_gate_status_payload(),
    }), status_code

def update_trainer_username(current_username: str, new_username: str, actor: str = "Admin"):
    desired = (new_username or "").strip()
    if not desired:
        return False, "Please provide a trainer username.", current_username
    if desired.lower() == (current_username or "").strip().lower():
        return False, "Trainer username is unchanged.", current_username
    if not supabase:
        return False, "Supabase is unavailable right now.", current_username

    # Ensure target username not already taken
    _, existing = find_user(desired)
    if existing:
        return False, f"Username “{desired}” is already in use.", current_username

    try:
        resp = (supabase.table("sheet1")
                .update({"trainer_username": desired})
                .eq("trainer_username", current_username)
                .execute())
        data = getattr(resp, "data", None)
        if not data:
            return False, f"Trainer “{current_username}” was not found.", current_username
        return True, f"✅ Trainer username updated to {desired}.", desired
    except Exception as e:
        print("⚠️ update_trainer_username failed:", e)
        return False, "Failed to update trainer username.", current_username

def update_campfire_username(trainer_username: str, campfire_username: str | None, actor: str = "Admin"):
    if not supabase:
        return False, "Supabase is unavailable."
    value = (campfire_username or "").strip()
    if "@" in value:
        value = value.replace("@", "")
    try:
        resp = (supabase.table("sheet1")
                .update({"campfire_username": value or None})
                .eq("trainer_username", trainer_username)
                .execute())
        data = getattr(resp, "data", None)
        if not data:
            return False, f"Trainer “{trainer_username}” was not found."
        label = value or "cleared"
        return True, f"✅ Campfire username updated ({label})."
    except Exception as e:
        print("⚠️ update_campfire_username failed:", e)
        return False, "Failed to update Campfire username."

def update_memorable_password(trainer_username: str, new_memorable: str, actor: str = "Admin"):
    new_value = (new_memorable or "").strip()
    if not new_value:
        return False, "Please enter a memorable password."
    if not supabase:
        return False, "Supabase is unavailable."
    try:
        resp = (supabase.table("sheet1")
                .update({"memorable_password": new_value})
                .eq("trainer_username", trainer_username)
                .execute())
        data = getattr(resp, "data", None)
        if not data:
            return False, f"Trainer “{trainer_username}” was not found."
        return True, "✅ Memorable password updated."
    except Exception as e:
        print("⚠️ update_memorable_password failed:", e)
        return False, "Failed to update memorable password."

# --- ADMIN: Change account type (supports underscore & hyphen URLs) ---
@app.route("/admin/trainers/<username>/change-account-type", methods=["POST"], endpoint="admin_change_account_type_v2")
@app.route("/admin/trainers/<username>/change_account_type", methods=["POST"], endpoint="admin_change_account_type_legacy")
def admin_change_account_type_route(username):
    _require_admin()
    new_type = request.form.get("account_type", "")
    actor = _current_actor()
    ok, msg = change_account_type(username, new_type, actor)
    if _is_trainer_panel_ajax():
        return _panel_action_response(ok, msg, username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_trainer_detail", username=username))

# --- ADMIN: Reset PIN (supports underscore & hyphen URLs) ---
@app.route("/admin/trainers/<username>/reset-pin", methods=["POST"], endpoint="admin_reset_pin_v2")
@app.route("/admin/trainers/<username>/reset_pin", methods=["POST"], endpoint="admin_reset_pin_legacy")
def admin_reset_pin_route(username):
    _require_admin()  
    new_pin = request.form.get("new_pin", "")
    actor = _current_actor()
    ok, msg = reset_pin(username, new_pin, actor)
    if _is_trainer_panel_ajax():
        return _panel_action_response(ok, msg, username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_trainer_detail", username=username))

# --- ADMIN: Change trainer username ---
@app.route("/admin/trainers/<username>/change-username", methods=["POST"], endpoint="admin_change_trainer_username_v2")
@app.route("/admin/trainers/<username>/change_username", methods=["POST"], endpoint="admin_change_trainer_username_legacy")
def admin_change_trainer_username_route(username):
    _require_admin()
    desired = request.form.get("new_trainer_username", "")
    actor = _current_actor()
    ok, msg, final_username = update_trainer_username(username, desired, actor)
    redirect_username = final_username if ok else username
    if _is_trainer_panel_ajax():
        return _panel_action_response(ok, msg, redirect_username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_trainer_detail", username=redirect_username))

# --- ADMIN: Change Campfire username ---
@app.route("/admin/trainers/<username>/change-campfire", methods=["POST"], endpoint="admin_change_campfire_username_v2")
@app.route("/admin/trainers/<username>/change_campfire", methods=["POST"], endpoint="admin_change_campfire_username_legacy")
def admin_change_campfire_username_route(username):
    _require_admin()
    new_campfire = request.form.get("new_campfire_username", "")
    actor = _current_actor()
    ok, msg = update_campfire_username(username, new_campfire, actor)
    if _is_trainer_panel_ajax():
        return _panel_action_response(ok, msg, username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_trainer_detail", username=username))

# --- ADMIN: Change memorable password ---
@app.route("/admin/trainers/<username>/change-memorable", methods=["POST"], endpoint="admin_change_memorable_password_v2")
@app.route("/admin/trainers/<username>/change_memorable", methods=["POST"], endpoint="admin_change_memorable_password_legacy")
def admin_change_memorable_password_route(username):
    _require_admin()
    new_memorable = request.form.get("new_memorable_password", "")
    actor = _current_actor()
    ok, msg = update_memorable_password(username, new_memorable, actor)
    if _is_trainer_panel_ajax():
        return _panel_action_response(ok, msg, username)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_trainer_detail", username=username))

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "trainer" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("home"))

        admin_user = _current_admin_user_record()
        if not admin_user:
            flash("Admins only!", "error")
            return redirect(url_for("dashboard"))

        if _admin_dashboard_gate_enabled() and not _admin_dashboard_gate_verified():
            flash("Unlock the admin quick menu to access this tool.", "warning")
            return redirect(url_for("dashboard"))

        session["account_type"] = "Admin"
        return f(*args, **kwargs)
    return wrapper

# ===== Admin Dashboard =====
@app.route("/admin/dashboard", methods=["GET"])
def admin_dashboard():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to access admin dashboard.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("⛔ Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    gate_required = _admin_dashboard_gate_enabled()
    if gate_required and not _admin_dashboard_gate_verified():
        flash("Unlock the admin quick menu to access the Admin Dashboard.", "warning")
        return redirect(url_for("dashboard"))

    if gate_required:
        session.pop("admin_dashboard_security", None)

    active_catalog_items = 0
    pending_redemptions = 0
    registered_trainers = 0

    geocache_quest_available = "geocache.quest_shell" in current_app.view_functions
    geocache_story_available = "admin_geocache_story" in current_app.view_functions

    try:
        # 📦 Active Catalog Items (where stock > 0 and active = true)
        result = supabase.table("catalog_items") \
            .select("id", count="exact") \
            .gt("stock", 0) \
            .eq("active", True) \
            .execute()
        active_catalog_items = result.count or 0

        # 🎁 Pending Redemptions
        result = supabase.table("redemptions") \
            .select("id", count="exact") \
            .eq("status", "PENDING") \
            .execute()
        pending_redemptions = result.count or 0

        # 👥 Registered Trainers (all trainers in sheet1)
        result = supabase.table("sheet1") \
            .select("id", count="exact") \
            .execute()
        registered_trainers = result.count or 0

    except Exception as e:
        print("⚠️ Error fetching admin stats:", e)

    digital_codes_summary = fetch_digital_code_summary()
    digital_code_roster = []
    if digital_codes_summary.get("supported"):
        roster, _acct = fetch_trainer_roster()
        digital_code_roster = roster[:250]

    return render_template(
        "admin_dashboard.html",
        active_catalog_items=active_catalog_items,
        pending_redemptions=pending_redemptions,
        registered_trainers=registered_trainers,
        show_catalog_app=SHOW_CATALOG_APP,
        show_city_perks_app=SHOW_CITY_PERKS_APP,
        show_city_guides_app=SHOW_CITY_GUIDES_APP,
        show_leagues_app=SHOW_LEAGUES_APP,
        geocache_quest_available=geocache_quest_available,
        geocache_story_available=geocache_story_available,
        digital_codes=digital_codes_summary,
        digital_code_roster=digital_code_roster,
        digital_code_subject_default=DEFAULT_DIGITAL_CODE_SUBJECT,
        digital_code_source_default=DIGITAL_CODE_SOURCE_DEFAULT,
    )


@app.route("/admin/digital-codes/upload", methods=["POST"])
@admin_required
def admin_digital_codes_upload():
    raw_codes = request.form.get("codes_csv") or request.form.get("codes_input") or ""
    batch_label = (request.form.get("batch_label") or "").strip()
    category_input = request.form.get("code_category") or request.form.get("category")
    actor = _current_actor()
    ok, message, _details = add_digital_codes_from_payload(raw_codes, actor, batch_label or None, category=category_input)
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/digital-codes/assign", methods=["POST"])
@admin_required
def admin_digital_codes_assign():
    trainer_username = request.form.get("trainer_username") or request.form.get("trainer") or ""
    code_id = (request.form.get("code_id") or "").strip() or None
    subject = request.form.get("subject") or request.form.get("notification_subject")
    assignment_source = request.form.get("assignment_source") or DIGITAL_CODE_SOURCE_DEFAULT
    category_filter = request.form.get("code_category") or request.form.get("preferred_category")
    reward_description = request.form.get("reward_contents") or request.form.get("reward_description")
    actor = _current_actor()
    ok, msg = assign_digital_code_to_trainer(
        trainer_username,
        actor,
        code_id,
        subject,
        assignment_source=assignment_source,
        preferred_category=category_filter,
        actor_type_hint="admin",
        reward_description=reward_description,
    )
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/testing-grounds")
def admin_testing_grounds():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to access Testing Grounds.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("⛔ Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    experiments = [
        {
            "name": "Login Concept Lab",
            "status": "Concept",
            "summary": "Review three refreshed splash explorations before we refresh the trainer login hero.",
            "cta_label": "Preview Login Concepts",
            "cta_url": url_for("admin_testing_login_concept"),
        },
        {
            "name": "Advent Calendar",
            "status": "Alpha",
            "summary": "Preview the 25-day stamp-and-quote experience before launching to trainers.",
            "cta_label": "Open Advent Calendar",
            "cta_url": url_for("admin_advent.view_calendar"),
        },
    ]

    return render_template(
        "admin_testing_grounds.html",
        experiments=experiments,
    )


@app.route("/admin/testing-grounds/login-concept")
@admin_required
def admin_testing_login_concept():
    session["last_page"] = request.path
    login_concepts = [
        {
            "slug": "aurora-passport",
            "title": "Aurora Passport",
            "status": "Frosted gradient + passport stamps",
            "summary": (
                "A sleeker take on today’s hero — glacier blues blend into a subtle"
                " aurora while oversized stamp icons hint at progress."
            ),
            "hero_copy": "Collective energy, but calmer and more premium.",
            "palette": ["#0f172a", "#1d4ed8", "#22d3ee", "#f472b6"],
            "highlights": [
                "Glassmorphism panel that mirrors the passport card UI.",
                "Animated aurora ribbon that loops slowly behind the hero image.",
                "Microcopy emphasises safety and togetherness for anxious trainers.",
            ],
            "device_caption": "Login hero framed by frosted glass + aurora ribbon",
        },
        {
            "slug": "midnight-arcade",
            "title": "Midnight Arcade",
            "status": "Neon raid hype",
            "summary": (
                "Hyper-modern noir palette with neon wireframes, inspired by city PVP nights."
            ),
            "hero_copy": "Loud energy for special pushes (like Leagues season).",
            "palette": ["#050914", "#8b5cf6", "#f43f5e", "#22d3ee"],
            "highlights": [
                "Retro grid + particle glow hints at AR scan beams.",
                "Hero copy swaps to ‘Coordinate. Charge. Win.’ messaging for urgency.",
                "CTA buttons adopt a pill outline that pulses softly on focus.",
            ],
            "device_caption": "Stretched neon banner over a blurred Doncaster skyline",
        },
        {
            "slug": "sunlit-field-notes",
            "title": "Sunlit Field Notes",
            "status": "Soft daylight + stationery textures",
            "summary": (
                "Warm parchment textures, pencil annotations, and pastel stickers for a more welcoming daytime feel."
            ),
            "hero_copy": "Grounded, analog energy for family-friendly beats.",
            "palette": ["#fef3c7", "#fb923c", "#065f46", "#312e81"],
            "highlights": [
                "Rounded photo frame stacks Polaroid moments from recent events.",
                "Handwritten-style eyebrow copy humanises the invite to log in.",
                "Background subtly animates with drifting paper grain to feel tactile.",
            ],
            "device_caption": "Passport stickers layered over soft daylight photo strip",
        },
    ]
    base_hero_content = {
        "eyebrow": "Raiding Doncaster and Beyond",
        "headline": "Where local Trainers level up together.",
        "body": (
            "Sign in to track your passport stamps, unlock rewards, and stay in sync with"
            " Doncaster’s Pokémon GO community."
        ),
        "image": url_for("static", filename="banner.jpg"),
    }
    return render_template(
        "admin_testing_login_concepts.html",
        concepts=login_concepts,
        base_hero=base_hero_content,
    )


@app.route("/admin/rdab-support")
@admin_required
def admin_rdab_support():
    session["last_page"] = request.path
    support_threads: list[dict] = []
    return render_template(
        "admin_rdab_support.html",
        support_threads=support_threads,
        has_threads=bool(support_threads),
    )


@app.route("/admin/leagues")
def admin_leagues():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to access Leagues.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    account_type = normalize_account_type(user.get("account_type"))
    if account_type != "Admin":
        flash("Leagues are under construction.", "info")
        return redirect(url_for("dashboard"))

    search_term = request.args.get("q", "").strip()
    search_results = search_trainers(search_term, limit=8) if search_term else []
    active_tournaments = fetch_pvp_tournaments(statuses=["REGISTRATION", "LIVE"])

    return render_template(
        "admin_leagues.html",
        search_term=search_term,
        search_results=search_results,
        active_tournaments=active_tournaments,
    )


@app.route("/admin/leagues/pvp", methods=["GET", "POST"])
def admin_leagues_pvp():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to access admin tools.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or normalize_account_type(user.get("account_type")) != "Admin":
        flash("⛔ Admin access required.", "error")
        return redirect(url_for("dashboard"))

    selected_id = request.args.get("tournament_id", "").strip()

    if request.method == "POST":
        action = request.form.get("action")
        next_focus = (request.form.get("next_focus") or "").lower()
        if next_focus not in {"overview", "editor", "snapshot"}:
            next_focus = None
        if action == "save":
            form_data = request.form.to_dict()
            saved_id, error = upsert_pvp_tournament(form_data, _current_actor())
            if error:
                flash(error, "error")
                error_detail = getattr(g, "supabase_last_error", "")
                if error_detail:
                    flash(error_detail, "error")
                selected_id = form_data.get("tournament_id", "")
            else:
                flash("Tournament saved.", "success")
                selected_id = saved_id
                return redirect(
                    url_for(
                        "admin_leagues_pvp",
                        tournament_id=saved_id,
                        focus=next_focus or "snapshot",
                    )
                )
        elif action == "status":
            tid = request.form.get("tournament_id")
            status_value = request.form.get("status_value")
            if tid and status_value:
                if update_pvp_tournament_status(tid, status_value):
                    flash(f"Tournament moved to {status_value.title()} status.", "success")
                else:
                    flash("Could not update tournament status.", "error")
                return redirect(
                    url_for(
                        "admin_leagues_pvp",
                        tournament_id=tid,
                        focus=next_focus or "snapshot",
                    )
                )

    tournaments = fetch_pvp_tournaments_for_admin()
    selected_detail = fetch_pvp_tournament_detail(selected_id) if selected_id else None

    rules_text = ""
    prizes_text = ""
    if selected_detail:
        rules_text = "\n".join(
            f"{(rule.get('title') + ': ') if rule.get('title') else ''}{rule.get('body', '')}"
            for rule in selected_detail.get("rules", [])
        )
        prizes_text = "\n".join(
            prize.get("description", "")
            for prize in selected_detail.get("prizes", [])
        )

    focus_param = (request.args.get("focus") or "").lower()
    allowed_focus = {"overview", "editor", "snapshot"}
    if focus_param not in allowed_focus:
        focus_param = "snapshot" if selected_detail else "overview"

    return render_template(
        "admin_leagues_pvp.html",
        tournaments=tournaments,
        selected=selected_detail,
        rules_text=rules_text,
        prizes_text=prizes_text,
        focus_tab=focus_param,
    )


@app.route("/leagues")
def leagues():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to explore leagues.", "warning")
        return redirect(url_for("home"))

    session_trainer = session["trainer"]
    _, viewer_user = find_user(session_trainer)
    if not viewer_user:
        flash("We could not load your trainer profile.", "error")
        return redirect(url_for("dashboard"))

    viewer_account_type = normalize_account_type(viewer_user.get("account_type"))

    target_trainer = request.args.get("trainer", "").strip()
    user = viewer_user
    trainer_name = session_trainer
    if target_trainer:
        if viewer_account_type == "Admin":
            _, target_user = find_user(target_trainer)
            if target_user:
                user = target_user
                trainer_name = target_user.get("trainer_username") or target_trainer
            else:
                flash(f"We couldn't find a trainer named {target_trainer}. Showing your profile instead.", "warning")
        else:
            flash("Only admins can inspect other trainer profiles.", "warning")

    league_modes, live_event_settings = build_league_content()
    avatar = user.get("avatar_icon", "avatar1.png")
    background = user.get("trainer_card_background", "default.png")

    league_card = {
        "rank": "Pending Launch",
        "cdl_points": 0,
        "pvp_record": "Coming Soon",
        "stamp_bonus": "Auto rewards in development",
        "season_highlight": "League scorecards unlock when the first season goes live.",
        "focus_items": [
            "Track CDL placements and seasonal badges once data syncs from the spreadsheet.",
            "Follow PvP brackets in real time when meetup leagues go live.",
            "Complete limited-time activities to earn bonus stamps during special events.",
        ],
    }

    if live_event_settings.get("active"):
        league_card["stamp_bonus"] = f"Earn event bonuses during {live_event_settings.get('theme')}"

    pvp_tournaments = fetch_pvp_tournaments()
    pvp_archive = fetch_pvp_tournament_archive()
    selected_pvp_id = request.args.get("pvp_id") or (pvp_tournaments[0]["id"] if pvp_tournaments else "")
    selected_pvp = fetch_pvp_tournament_detail(selected_pvp_id) if selected_pvp_id else None
    player_registration = find_pvp_registration(selected_pvp_id, trainer_name) if selected_pvp else None

    return render_template(
        "leagues.html",
        trainer=trainer_name,
        account_type=normalize_account_type(user.get("account_type")),
        league_modes=league_modes,
        live_event_settings=live_event_settings,
        avatar=avatar,
        background=background,
        league_card=league_card,
        pvp_tournaments=pvp_tournaments,
        pvp_selected=selected_pvp,
        pvp_selected_id=selected_pvp_id,
        pvp_registration=player_registration,
        pvp_archive=pvp_archive,
    )


@app.route("/leagues/pvp/<tournament_id>/join", methods=["GET", "POST"])
def leagues_pvp_join(tournament_id):
    if "trainer" not in session:
        flash("Please log in to continue.", "warning")
        return redirect(url_for("home"))

    detail = fetch_pvp_tournament_detail(tournament_id)
    if not detail:
        flash("Tournament not found.", "error")
        return redirect(url_for("leagues", pvp_id=""))

    tournament = detail["tournament"]
    if tournament.get("status") != "REGISTRATION":
        flash("Registration for this tournament is not open.", "info")
        return redirect(url_for("leagues", pvp_id=tournament.get("id")))

    trainer_name = session["trainer"]
    existing = find_pvp_registration(tournament_id, trainer_name)
    if existing:
        flash("You already registered for this tournament.", "info")
        return redirect(url_for("leagues_pvp_team", tournament_id=tournament_id))

    if request.method == "POST":
        if request.form.get("confirm_attendance") != "yes":
            flash("Please confirm that you will attend the meetup.", "warning")
        else:
            registration, error = register_trainer_for_pvp(tournament_id, trainer_name)
            if error:
                flash(error, "error")
            else:
                flash("🎉 You are registered! Let's build your team.", "success")
                return redirect(url_for("leagues_pvp_team", tournament_id=tournament_id))

    return render_template(
        "leagues_pvp_join.html",
        tournament=tournament,
        rules=detail.get("rules", []),
        prizes=detail.get("prizes", []),
    )


def _empty_team_slots():
    return [{"species_name": "", "species_form": "", "notes": ""} for _ in range(6)]


@app.route("/leagues/pvp/<tournament_id>/team", methods=["GET", "POST"])
def leagues_pvp_team(tournament_id):
    if "trainer" not in session:
        flash("Please log in to continue.", "warning")
        return redirect(url_for("home"))

    detail = fetch_pvp_tournament_detail(tournament_id)
    if not detail:
        flash("Tournament not found.", "error")
        return redirect(url_for("leagues"))
    tournament = detail["tournament"]

    trainer_name = session["trainer"]
    registration = find_pvp_registration(tournament_id, trainer_name)
    if not registration:
        flash("Please register before submitting a team.", "warning")
        return redirect(url_for("leagues_pvp_join", tournament_id=tournament_id))

    existing_team = registration.get("teams") or []
    team_slots = _empty_team_slots()
    for idx, slot in enumerate(existing_team):
        if idx >= len(team_slots):
            break
        team_slots[idx]["species_name"] = slot.get("species_name", "")
        team_slots[idx]["species_form"] = slot.get("species_form", "")
        team_slots[idx]["notes"] = (slot.get("moves") or {}).get("notes", "")

    if request.method == "POST":
        submitted = _empty_team_slots()
        payload = []
        for idx in range(6):
            name = request.form.get(f"pokemon_{idx + 1}", "").strip()
            form_label = request.form.get(f"pokemon_form_{idx + 1}", "").strip()
            extra = request.form.get(f"pokemon_notes_{idx + 1}", "").strip()

            submitted[idx]["species_name"] = name
            submitted[idx]["species_form"] = form_label
            submitted[idx]["notes"] = extra

            if not name:
                continue
            entry = {
                "species_name": name,
                "species_form": form_label,
                "notes": extra,
                "moves": {"notes": extra} if extra else {},
            }
            payload.append(entry)

        success, error = save_pvp_team(registration["id"], payload)
        if success:
            flash("Team saved! See you at the tournament.", "success")
            return redirect(url_for("leagues", pvp_id=tournament_id))
        if error:
            flash(error, "error")
        team_slots = submitted

    return render_template(
        "leagues_pvp_team.html",
        tournament=tournament,
        registration=registration,
        rules=detail.get("rules", []),
        prizes=detail.get("prizes", []),
        team_slots=team_slots,
    )

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("admin_login.html")

    username = request.form.get("username", "").strip()
    pin = request.form.get("pin", "").strip()

    # Look up account in Supabase
    try:
        r = supabase.table("sheet1").select("*").ilike("trainer_username", username).limit(1).execute()
    except Exception as e:
        print("⚠️ Supabase admin_login query failed:", e)
        flash("Database error — please try again later.", "error")
        return redirect(url_for("admin_login"))

    if not r.data:
        flash("Trainer not found.", "error")
        return redirect(url_for("admin_login"))

    user = r.data[0]
    # Compare hashed pin (supports legacy + salted)
    if not _pin_matches(user, pin):
        flash("Incorrect PIN.", "error")
        return redirect(url_for("admin_login"))

    # Check for admin privilege
    if (user.get("account_type") or "").lower() != "admin":
        flash("You are not an admin.", "error")
        return redirect(url_for("home"))

    # ✅ Success: Log them in
    session["trainer"] = user["trainer_username"]
    session["account_type"] = "Admin"
    session.permanent = True

    flash(f"Welcome back, Admin {user['trainer_username']}!", "success")
    return redirect(url_for("admin_dashboard"))

# ====== Catalog images folder ======
from werkzeug.utils import secure_filename
from datetime import datetime
CATALOG_IMG_DIR = os.path.join(app.root_path, "static", "catalog")
os.makedirs(CATALOG_IMG_DIR, exist_ok=True)

def get_current_trainer_user():
    """Return the logged-in trainer Supabase record or None."""
    trainer = session.get("trainer")
    if not trainer:
        return None
    _, user = find_user(trainer)
    return user


def _is_admin():
    return bool(get_current_admin_user())


def get_current_admin_user():
    """Return the logged-in admin Supabase record or None."""
    user = get_current_trainer_user()
    if not user or user.get("account_type") != "Admin":
        return None
    return user


app.register_blueprint(create_advent_blueprint(get_current_admin_user))
app.register_blueprint(create_player_advent_blueprint(get_current_trainer_user))
app.register_blueprint(
    create_city_perks_admin_blueprint(
        admin_required,
        get_current_admin_user,
        _upload_to_supabase,
        supabase,
    )
)
app.register_blueprint(city_perks_api_blueprint)

with app.app_context():
    db.create_all()
    try:
        ensure_city_perks_cache(force=True)
    except Exception as exc:
        app.logger.warning("Initial CityPerks sync skipped: %s", exc)

def _tags_csv_to_array(csv: str | None):
    if not csv:
        return []
    return [t.strip() for t in csv.split(",") if t.strip()]

def _save_catalog_image(file_storage):
    """Save uploaded image to /static/catalog and return its public URL path."""
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None
    fname = secure_filename(file_storage.filename)
    # make unique
    root, ext = os.path.splitext(fname)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    fname = f"{root}_{stamp}{ext}"
    path = os.path.join(CATALOG_IMG_DIR, fname)
    file_storage.save(path)
    return f"/static/catalog/{fname}"

# ====== Admin Catalog Manager ======
CATALOG_SORT_CHOICES = [
    ("newest", "Newest (recent first)"),
    ("oldest", "Oldest (oldest first)"),
    ("name_az", "Name A → Z"),
    ("name_za", "Name Z → A"),
    ("cost_low", "Cost: low → high"),
    ("cost_high", "Cost: high → low"),
    ("stock_high", "Stock: high → low"),
    ("stock_low", "Stock: low → high"),
]

CATALOG_STATUS_CHOICES = [
    ("all", "All items"),
    ("online", "Online only"),
    ("offline", "Offline only"),
]
CATALOG_STATUS_VALUES = {choice[0] for choice in CATALOG_STATUS_CHOICES}

CATALOG_SORT_CONFIG = {
    "newest": {"key": lambda it: parse_dt_safe(it.get("created_at")), "reverse": True},
    "oldest": {"key": lambda it: parse_dt_safe(it.get("created_at")), "reverse": False},
    "name_az": {"key": lambda it: (it.get("name") or "").lower(), "reverse": False},
    "name_za": {"key": lambda it: (it.get("name") or "").lower(), "reverse": True},
    "cost_low": {"key": lambda it: it.get("cost_stamps") or 0, "reverse": False},
    "cost_high": {"key": lambda it: it.get("cost_stamps") or 0, "reverse": True},
    "stock_high": {"key": lambda it: it.get("stock") or 0, "reverse": True},
    "stock_low": {"key": lambda it: it.get("stock") or 0, "reverse": False},
}

def wants_json_response() -> bool:
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    if not best:
        return False
    return best == "application/json" and request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]

def _catalog_json_error(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status

def _fetch_catalog_item(item_id: str):
    try:
        resp = supabase.table("catalog_items").select("*").eq("id", item_id).limit(1).execute()
        if resp.data:
            return resp.data[0]
    except Exception as exc:
        print("⚠️ Failed fetching catalog detail:", exc)
    return None

@app.route("/admin/catalog")
def admin_catalog():
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    sort_choice = request.args.get("sort", CATALOG_SORT_CHOICES[0][0])
    if sort_choice not in CATALOG_SORT_CONFIG:
        sort_choice = CATALOG_SORT_CHOICES[0][0]

    status_filter = request.args.get("status", CATALOG_STATUS_CHOICES[0][0])
    if status_filter not in CATALOG_STATUS_VALUES:
        status_filter = CATALOG_STATUS_CHOICES[0][0]

    view_mode = request.args.get("view", "grid")
    if view_mode not in {"grid", "list"}:
        view_mode = "grid"

    items = []
    try:
        resp = supabase.table("catalog_items").select("*").order("created_at", desc=True).execute()
        items = resp.data or []
    except Exception as e:
        print("⚠️ Failed fetching catalog items:", e)

    if status_filter == "online":
        items = [it for it in items if it.get("active")]
    elif status_filter == "offline":
        items = [it for it in items if not it.get("active")]

    sort_config = CATALOG_SORT_CONFIG.get(sort_choice)
    if sort_config:
        items = sorted(items, key=sort_config["key"], reverse=sort_config.get("reverse", False))

    return render_template(
        "admin_catalog.html",
        items=items,
        sort_choices=CATALOG_SORT_CHOICES,
        status_choices=CATALOG_STATUS_CHOICES,
        current_sort=sort_choice,
        current_status=status_filter,
        view_mode=view_mode,
    )


@app.route("/admin/catalog/<item_id>.json")
def admin_catalog_detail_json(item_id):
    if not _is_admin():
        return _catalog_json_error("Unauthorized", status=403)

    item = _fetch_catalog_item(item_id)
    if not item:
        return _catalog_json_error("Item not found", status=404)

    return jsonify({"success": True, "item": item})


@app.route("/admin/catalog/<item_id>")
def admin_catalog_detail(item_id):
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    item = _fetch_catalog_item(item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for("admin_catalog"))

    return render_template("admin_catalog_detail.html", item=item)

@app.route("/admin/catalog/<item_id>/update", methods=["POST"])
def admin_catalog_update(item_id):
    if "trainer" not in session:
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))

    data = {
        "name": request.form.get("name"),
        "cost_stamps": int(request.form.get("cost_stamps") or 0),
        "description": request.form.get("description"),
        "stock": int(request.form.get("stock") or 0),
        "tags": _tags_csv_to_array(request.form.get("tags")),
        "image_url": request.form.get("image_url"),
        "active": "active" in request.form,
        "updated_at": datetime.utcnow().isoformat()
    }

    # Supabase upload if provided
    file = request.files.get("image_file")
    if file and file.filename:
        saved_url = _upload_to_supabase(file)
        if saved_url:
            data["image_url"] = saved_url

    try:
        resp = supabase.table("catalog_items").update(data).eq("id", item_id).execute()
        updated_item = resp.data[0] if resp.data else _fetch_catalog_item(item_id)
        if wants_json_response():
            if not updated_item:
                return _catalog_json_error("Item updated but refreshed data missing. Reload the page.", status=500)
            return jsonify({"success": True, "item": updated_item})
        flash("✅ Item updated successfully!", "success")
    except Exception as e:
        print("⚠️ Catalog update failed:", e)
        if wants_json_response():
            return _catalog_json_error("Failed to update item.", status=500)
        flash("❌ Failed to update item.", "error")

    return redirect(url_for("admin_catalog_detail", item_id=item_id))

@app.route("/admin/catalog/<item_id>/delete", methods=["POST"])
def admin_catalog_delete(item_id):
    if "trainer" not in session:
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))

    try:
        supabase.table("catalog_items").delete().eq("id", item_id).execute()
        flash("🗑️ Item deleted.", "success")
    except Exception as e:
        print("⚠️ Catalog delete failed:", e)
        if wants_json_response():
            return _catalog_json_error("Failed to delete item.", status=500)
        flash("❌ Failed to delete item.", "error")

    if wants_json_response():
        return jsonify({"success": True, "item_id": item_id})

    return redirect(url_for("admin_catalog"))

@app.route("/admin/catalog/create", methods=["POST"])
def admin_catalog_create():
    if not _is_admin():
        flash("Admins only.", "error")
        return redirect(url_for("dashboard"))

    f = request.form
    name        = f.get("name", "").strip()
    description = f.get("description", "").strip()
    cost        = int(f.get("cost_stamps") or 0)
    stock       = int(f.get("stock") or 0)
    active      = (f.get("active") == "on")
    tags        = _tags_csv_to_array(f.get("tags"))
    image_url   = f.get("image_url", "").strip()

    # Supabase upload
    upload = request.files.get("image_file")
    if upload and upload.filename:
        saved_url = _upload_to_supabase(upload)
        if saved_url:
            image_url = saved_url

    if not name:
        flash("Name is required.", "warning")
        return redirect(url_for("admin_catalog"))

    try:
        supabase.table("catalog_items").insert({
            "name": name,
            "description": description,
            "cost_stamps": cost,
            "stock": stock,
            "active": active,
            "tags": tags,
            "image_url": image_url or None,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
        flash(f"✅ '{name}' created.", "success")
    except Exception as e:
        print("⚠️ admin_catalog_create failed:", e)
        flash("Failed to create item.", "error")

    return redirect(url_for("admin_catalog"))

@app.route("/admin/catalog/<item_id>/toggle", methods=["POST"])
def admin_catalog_toggle(item_id):
    if not _is_admin():
        flash("Admins only.", "error")
        return redirect(url_for("dashboard"))

    try:
        cur = supabase.table("catalog_items").select("active").eq("id", item_id).limit(1).execute().data
        new_state = not bool(cur and cur[0].get("active"))
        supabase.table("catalog_items").update({
            "active": new_state,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", item_id).execute()
        flash(("🟢 Online" if new_state else "⚫ Offline"), "success")
    except Exception as e:
        print("⚠️ admin_catalog_toggle failed:", e)
        flash("Failed to toggle active.", "error")

    return redirect(url_for("admin_catalog"))

from datetime import date
# ===== Admin Catalog Meetups =====
@app.route("/admin/meetups", methods=["GET", "POST"])
def admin_meetups():
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    # 🔄 Auto-disable expired meetups
    try:
        today = date.today().isoformat()
        supabase.table("meetups") \
            .update({"active": False}) \
            .lte("date", today) \
            .eq("active", True) \
            .execute()
    except Exception as e:
        print("⚠️ Failed auto-disable meetups:", e)

    meetups = []
    try:
        resp = supabase.table("meetups").select("*").order("date", desc=False).execute()
        meetups = resp.data or []
    except Exception as e:
        print("⚠️ Failed fetching meetups:", e)

    return render_template("admin_meetups.html", meetups=meetups)

@app.route("/admin/meetups/create", methods=["POST"])
def admin_meetups_create():
    if "trainer" not in session:
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))

    data = {
        "name": request.form.get("name"),
        "location": request.form.get("location"),
        "date": request.form.get("date"),
        "start_time": request.form.get("start_time"),
        "active": True,
        "created_at": datetime.utcnow().isoformat()
    }
    try:
        supabase.table("meetups").insert(data).execute()
        flash("✅ Meetup created!", "success")
    except Exception as e:
        print("⚠️ Failed creating meetup:", e)
        flash("❌ Could not create meetup.", "error")

    return redirect(url_for("admin_meetups"))

@app.route("/admin/meetups/<meetup_id>/update", methods=["POST"])
def admin_meetups_update(meetup_id):
    if "trainer" not in session:
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))

    data = {
        "name": request.form.get("name"),
        "location": request.form.get("location"),
        "date": request.form.get("date"),
        "start_time": request.form.get("start_time"),
        "active": "active" in request.form
    }
    try:
        supabase.table("meetups").update(data).eq("id", meetup_id).execute()
        flash("✅ Meetup updated!", "success")
    except Exception as e:
        print("⚠️ Failed updating meetup:", e)
        flash("❌ Could not update meetup.", "error")

    return redirect(url_for("admin_meetups"))

@app.route("/admin/meetups/<meetup_id>/delete", methods=["POST"])
def admin_meetups_delete(meetup_id):
    if "trainer" not in session:
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))

    try:
        supabase.table("meetups").delete().eq("id", meetup_id).execute()
        flash("🗑️ Meetup deleted.", "success")
    except Exception as e:
        print("⚠️ Failed deleting meetup:", e)
        flash("❌ Could not delete meetup.", "error")

    return redirect(url_for("admin_meetups"))

def _format_local(dt_val: datetime) -> str:
    sentinel = datetime.min.replace(tzinfo=timezone.utc)
    if not isinstance(dt_val, datetime) or dt_val <= sentinel:
        return "Unknown"
    return dt_val.astimezone(LONDON_TZ).strftime("%d %b %Y · %H:%M")


def _format_meetup_summary(meetup: dict | None) -> str:
    if not meetup:
        return ""

    name = (meetup.get("name") or "").strip() or "Meetup"
    details = [
        (meetup.get("date") or "").strip(),
        (meetup.get("start_time") or "").strip(),
        (meetup.get("location") or "").strip(),
    ]
    details = [d for d in details if d]
    if details:
        return f"{name} ({' · '.join(details)})"
    return name

@app.route("/admin/calendar-events", methods=["GET", "POST"])
@admin_required
def admin_calendar_events():
    session["last_page"] = request.path

    events = load_custom_events()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        campfire_url = (request.form.get("campfire_url") or "").strip()
        location = (request.form.get("location") or "").strip()
        cover_photo_url = (request.form.get("cover_photo_url") or "").strip()
        start_raw = (request.form.get("start_datetime") or "").strip()
        end_raw = (request.form.get("end_datetime") or "").strip()

        if not name or not start_raw:
            flash("Name and start date/time are required.", "error")
            return redirect(url_for("admin_calendar_events"))

        try:
            start_local = datetime.fromisoformat(start_raw)
        except ValueError:
            flash("Invalid start date/time.", "error")
            return redirect(url_for("admin_calendar_events"))
        if start_local.tzinfo is None:
            start_local = start_local.replace(tzinfo=LONDON_TZ)
        else:
            start_local = start_local.astimezone(LONDON_TZ)

        end_local = None
        if end_raw:
            try:
                end_local = datetime.fromisoformat(end_raw)
            except ValueError:
                flash("Invalid end date/time.", "error")
                return redirect(url_for("admin_calendar_events"))
            if end_local.tzinfo is None:
                end_local = end_local.replace(tzinfo=LONDON_TZ)
            else:
                end_local = end_local.astimezone(LONDON_TZ)

        if not end_local or end_local <= start_local:
            end_local = start_local + timedelta(hours=2)

        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        event = {
            "event_id": f"local-{uuid.uuid4().hex}",
            "name": name,
            "location": location,
            "url": campfire_url,
            "cover_photo_url": cover_photo_url,
            "start_time": start_local.astimezone(timezone.utc).isoformat(),
            "end_time": end_local.astimezone(timezone.utc).isoformat(),
            "created_at": now_utc.isoformat(),
            "updated_at": now_utc.isoformat(),
        }
        events.append(event)
        save_custom_events(events)
        flash("✅ Calendar event added.", "success")
        return redirect(url_for("admin_calendar_events"))

    # Prepare data for display
    display_events = []
    for ev in sorted(events, key=lambda item: item.get("start_time") or ""):
        start_dt = parse_dt_safe(ev.get("start_time"))
        end_dt = parse_dt_safe(ev.get("end_time"))
        display_events.append({
            "event_id": ev.get("event_id"),
            "name": ev.get("name"),
            "location": ev.get("location"),
            "campfire_url": ev.get("url"),
            "cover_photo_url": ev.get("cover_photo_url"),
            "start_display": _format_local(start_dt) if start_dt else "",
            "end_display": _format_local(end_dt) if end_dt else "",
            "created_at": ev.get("created_at"),
        })

    return render_template(
        "admin_calendar_events.html",
        custom_events=display_events,
        has_events=bool(display_events),
    )

@app.route("/admin/calendar-events/<event_id>/delete", methods=["POST"])
@admin_required
def admin_calendar_events_delete(event_id):
    events = load_custom_events()
    new_events = [ev for ev in events if str(ev.get("event_id") or "") != event_id]

    if len(new_events) == len(events):
        flash("Event not found.", "warning")
    else:
        save_custom_events(new_events)
        flash("🗑️ Calendar event removed.", "success")

    return redirect(url_for("admin_calendar_events"))

# ===== Admin Redemptions =====
@app.route("/admin/redemptions", methods=["GET"])
def admin_redemptions():
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Admins only!", "error")
        return redirect(url_for("dashboard"))

    view_mode = request.args.get("view", "list")
    if view_mode not in {"list", "prize-buckets"}:
        view_mode = "list"

    bucket_scope = (request.args.get("bucket_scope") or "active").lower()
    if bucket_scope not in {"active", "inactive", "all"}:
        bucket_scope = "active"

    status_filter = request.args.get("status", "ALL")
    search_user = request.args.get("search", "").strip()
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    per_page = 20

    stats = {"total": 0, "pending": 0, "fulfilled": 0, "cancelled": 0}
    redemptions: list[dict] = []
    filtered_redemptions: list[dict] = []
    prize_buckets: list[dict] = []
    bucket_scope_counts = {"active": 0, "inactive": 0}
    total_filtered = 0
    total_pages = 1
    has_more = False
    meetup_lookup: dict[str, dict] = {}
    all_meetups: list[dict] = []

    def build_tab_url(
        view_name: str,
        page_override: int | None = None,
        bucket_scope_override: str | None = None,
    ) -> str:
        params: dict[str, object] = {"view": view_name}
        if status_filter != "ALL":
            params["status"] = status_filter
        if search_user:
            params["search"] = search_user
        if page_override is not None:
            params["page"] = page_override
        elif view_name == "list":
            params["page"] = page
        if view_name == "prize-buckets":
            scope_value = (bucket_scope_override or bucket_scope or "active").lower()
            if scope_value != "active":
                params["bucket_scope"] = scope_value
        return url_for("admin_redemptions", **params)

    list_tab_url = build_tab_url("list")
    bucket_tab_url = build_tab_url("prize-buckets", page_override=1)
    bucket_scope_active_url = build_tab_url("prize-buckets", page_override=1, bucket_scope_override="active")
    bucket_scope_inactive_url = build_tab_url("prize-buckets", page_override=1, bucket_scope_override="inactive")

    def build_prize_buckets(
        meetups_list: list[dict], redemptions_list: list[dict], lookup: dict[str, dict]
    ) -> list[dict]:
        """Group filtered redemptions into meetup/prize buckets for the card view."""
        by_meetup: defaultdict[str, list[dict]] = defaultdict(list)
        for entry in redemptions_list:
            meetup_id = str(entry.get("meetup_id") or "").strip() or "NO_MEETUP"
            by_meetup[meetup_id].append(entry)

        buckets: list[dict] = []
        seen_ids: set[str] = set()

        def make_bucket(bucket_id: str, meetup_row: dict | None, entries: list[dict]) -> dict:
            meetup_row = meetup_row or lookup.get(bucket_id)
            is_unassigned = bucket_id == "NO_MEETUP" or meetup_row is None

            if meetup_row:
                title = (meetup_row.get("name") or "").strip() or "Meetup"
                details = [
                    (meetup_row.get("date") or "").strip(),
                    (meetup_row.get("start_time") or "").strip(),
                    (meetup_row.get("location") or "").strip(),
                ]
                subtitle = " · ".join([d for d in details if d])
            elif bucket_id == "NO_MEETUP":
                title = "No Meetup Assigned"
                subtitle = "Redemptions missing a pickup meetup."
            else:
                title = "Meetup"
                subtitle = ""

            status_counts = Counter((entry.get("status") or "").upper() for entry in entries)
            normalized_counts = {
                "PENDING": status_counts.get("PENDING", 0),
                "FULFILLED": status_counts.get("FULFILLED", 0),
                "CANCELLED": status_counts.get("CANCELLED", 0),
            }
            extra_status_counts = {
                key: value
                for key, value in status_counts.items()
                if key not in {"PENDING", "FULFILLED", "CANCELLED"} and key
            }

            items_by_name: defaultdict[str, list[dict]] = defaultdict(list)
            for entry in entries:
                snapshot = entry.get("item_snapshot") or {}
                item_name = (snapshot.get("name") or "").strip() or "Unknown Prize"
                items_by_name[item_name].append(entry)

            prize_blocks = []
            for prize_name in sorted(items_by_name.keys(), key=lambda name: name.lower()):
                records = sorted(
                    items_by_name[prize_name],
                    key=lambda row: row.get("created_at") or "",
                    reverse=True,
                )
                prize_blocks.append(
                    {
                        "name": prize_name,
                        "redemptions": records,
                    }
                )

            date_str = (meetup_row.get("date") or "").strip() if meetup_row else ""
            start_time_str = (meetup_row.get("start_time") or "").strip() if meetup_row else ""
            try:
                parsed_date = date.fromisoformat(date_str) if date_str else None
            except ValueError:
                parsed_date = None
            sort_date_value = parsed_date.toordinal() if parsed_date else 0

            if meetup_row:
                sort_weight = 0 if entries else 2
            else:
                sort_weight = 1 if entries else 3

            return {
                "id": bucket_id,
                "title": title,
                "subtitle": subtitle,
                "prizes": prize_blocks,
                "status_counts": normalized_counts,
                "extra_status_counts": extra_status_counts,
                "total_redemptions": len(entries),
                "pending_count": normalized_counts.get("PENDING", 0),
                "has_redemptions": bool(entries),
                "is_unassigned": is_unassigned,
                "is_active": bool((meetup_row or {}).get("active")),
                "sort_key": (
                    sort_weight,
                    -sort_date_value,
                    start_time_str or "",
                    title.lower(),
                ),
                "sort_weight": sort_weight,
                "meetup": meetup_row,
            }

        for meetup_row in meetups_list:
            bucket_id = str(meetup_row.get("id") or "").strip()
            if not bucket_id:
                continue
            seen_ids.add(bucket_id)
            buckets.append(make_bucket(bucket_id, meetup_row, by_meetup.get(bucket_id, [])))

        if "NO_MEETUP" in by_meetup:
            buckets.append(make_bucket("NO_MEETUP", None, by_meetup["NO_MEETUP"]))

        for bucket_id, entries in by_meetup.items():
            if bucket_id in seen_ids or bucket_id == "NO_MEETUP":
                continue
            buckets.append(make_bucket(bucket_id, lookup.get(bucket_id), entries))

        buckets.sort(key=lambda bucket: bucket["sort_key"])
        return buckets

    if USE_SUPABASE and supabase:
        try:
            all_resp = (
                supabase.table("redemptions")
                .select("id,trainer_username,status,item_snapshot,meetup_id,stamps_spent,created_at")
                .order("created_at", desc=True)
                .execute()
            )
            all_redemptions = list(all_resp.data or [])

            stats["total"] = len(all_redemptions)
            stats["pending"] = sum(1 for r in all_redemptions if r.get("status") == "PENDING")
            stats["fulfilled"] = sum(1 for r in all_redemptions if r.get("status") == "FULFILLED")
            stats["cancelled"] = sum(1 for r in all_redemptions if r.get("status") == "CANCELLED")

            filtered_redemptions = all_redemptions
            if status_filter != "ALL":
                target_status = status_filter.upper()
                filtered_redemptions = [
                    r for r in filtered_redemptions if (r.get("status") or "").upper() == target_status
                ]
            if search_user:
                needle = search_user.lower()
                filtered_redemptions = [
                    r for r in filtered_redemptions
                    if needle in (r.get("trainer_username") or "").lower()
                ]
            filtered_redemptions.sort(key=lambda r: r.get("created_at") or "", reverse=True)

            total_filtered = len(filtered_redemptions)
            total_pages = max(1, (total_filtered + per_page - 1) // per_page)

            start_index = (page - 1) * per_page
            end_index = start_index + per_page
            redemptions = filtered_redemptions[start_index:end_index]
            has_more = page < total_pages

            meetups_resp = (
                supabase.table("meetups")
                .select("id,name,location,date,start_time,active")
                .order("date", desc=False)
                .execute()
            )
            all_meetups = list(meetups_resp.data or [])
            for meetup_row in all_meetups:
                meetup_id = str(meetup_row.get("id") or "").strip()
                if meetup_id:
                    meetup_lookup[meetup_id] = meetup_row

            for record in redemptions:
                meetup_key = str(record.get("meetup_id") or "").strip()
                record["meetup_display"] = _format_meetup_summary(meetup_lookup.get(meetup_key))

            if view_mode == "prize-buckets":
                raw_buckets = build_prize_buckets(all_meetups, filtered_redemptions, meetup_lookup)
                def _bucket_matches_scope(bucket: dict) -> bool:
                    if bucket_scope == "inactive":
                        return (not bucket.get("is_unassigned")) and (not bucket.get("is_active"))
                    if bucket_scope == "all":
                        return True
                    # Default to active buckets; always include unassigned buckets for visibility.
                    return bucket.get("is_unassigned") or bucket.get("is_active")

                prize_buckets = [bucket for bucket in raw_buckets if _bucket_matches_scope(bucket)]
                bucket_scope_counts["active"] = sum(
                    1 for bucket in raw_buckets if bucket.get("is_unassigned") or bucket.get("is_active")
                )
                bucket_scope_counts["inactive"] = sum(
                    1 for bucket in raw_buckets if (not bucket.get("is_unassigned")) and (not bucket.get("is_active"))
                )
        except Exception as e:
            print("⚠️ Failed fetching redemptions:", e)
    else:
        print("⚠️ Supabase not configured; skipping redemptions fetch.")

    return render_template(
        "admin_redemptions.html",
        redemptions=redemptions,
        stats=stats,
        status_filter=status_filter,
        search_user=search_user,
        page=page,
        total_pages=total_pages,
        view_mode=view_mode,
        prize_buckets=prize_buckets,
        has_more=has_more,
        list_tab_url=list_tab_url,
        bucket_tab_url=bucket_tab_url,
        bucket_scope=bucket_scope,
        bucket_scope_counts=bucket_scope_counts,
        bucket_scope_active_url=bucket_scope_active_url,
        bucket_scope_inactive_url=bucket_scope_inactive_url,
    )


@app.route("/admin/redemptions/<rid>/update", methods=["POST"])
def admin_redemptions_update(rid):
    if "trainer" not in session:
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))

    new_status = request.form.get("status")
    if new_status not in ["FULFILLED", "CANCELLED"]:
        flash("Invalid status.", "error")
        return redirect(url_for("admin_redemptions"))

    try:
        supabase.table("redemptions").update({"status": new_status}).eq("id", rid).execute()
        flash(f"✅ Redemption marked as {new_status}", "success")
    except Exception as e:
        print("⚠️ Failed updating redemption:", e)
        flash("❌ Could not update redemption.", "error")

    return redirect(url_for("admin_redemptions"))

@app.route("/admin/redemptions/<uuid:redemption_id>/<action>", methods=["POST"])
def update_redemption_ajax(redemption_id, action):
    if "trainer" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 403
    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        return jsonify({"success": False, "error": "Admins only"}), 403

    if action not in ["fulfill", "cancel"]:
        return jsonify({"success": False, "error": "Invalid action"}), 400

    new_status = "FULFILLED" if action == "fulfill" else "CANCELLED"
    try:
        supabase.table("redemptions").update({"status": new_status}).eq("id", str(redemption_id)).execute()

        # Send notification to trainer
        redemption_resp = (
            supabase.table("redemptions")
            .select("*")
            .eq("id", str(redemption_id))
            .execute()
        )
        redemption_rows = redemption_resp.data or []
        if not redemption_rows:
            return jsonify({"success": False, "error": "Redemption not found"}), 404

        redemption = redemption_rows[0]
        trainer = redemption["trainer_username"]
        item_name = (redemption.get("item_snapshot") or {}).get("name", "a prize")

        if new_status == "CANCELLED":
            subject = "❌ Prize Redemption Cancelled"
            message = f"Hey {trainer}, your order for {item_name} has been cancelled. "
            message += "Our admin team has returned any stamps if necessary."
        else:
            subject = "✅ Prize Redemption Fulfilled"
            message = f"Thanks for picking up your {item_name}! "
            message += "We hope you like it — contact us if you have any issues."

        send_notification(trainer, subject, message, notif_type="system")

        stats_payload = None
        try:
            stats_resp = supabase.table("redemptions").select("status").execute()
            rows = stats_resp.data or []
            stats_payload = {
                "total": len(rows),
                "pending": sum(1 for row in rows if row.get("status") == "PENDING"),
                "fulfilled": sum(1 for row in rows if row.get("status") == "FULFILLED"),
                "cancelled": sum(1 for row in rows if row.get("status") == "CANCELLED"),
            }
        except Exception as stats_err:
            print("⚠️ Failed to refresh redemption stats:", stats_err)

        return jsonify({"success": True, "new_status": new_status, "stats": stats_payload})
    except Exception as e:
        print("⚠️ update_redemption_ajax failed:", e)
        return jsonify({"success": False, "error": "DB error"}), 500

# ====== Admin: Trainer Manager ======
@app.route("/admin/trainers", methods=["GET", "POST"])
def admin_trainers():
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    # ✅ Require Admin account_type
    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    trainer_data = None
    all_trainers = []
    account_types = set()

    # 🔎 Search
    if request.method == "POST":
        search_name = request.form.get("search_name", "").strip()
        if search_name:
            _, trainer_data = find_user(search_name)
            if not trainer_data:
                flash(f"No trainer found with username '{search_name}'", "warning")

    # 📋 Fetch all accounts from Supabase.sheet1
    trainer_columns = "id, trainer_username, campfire_username, account_type, stamps, avatar_icon, trainer_card_background"
    try:
        resp = None
        try:
            resp = supabase.table("sheet1").select(
                f"{trainer_columns}, created_at"
            ).execute()
        except Exception as column_err:
            print("⚠️ Trainer fetch with created_at failed, retrying without it:", column_err)
            resp = supabase.table("sheet1").select(trainer_columns).execute()

        all_trainers = (resp.data if resp else []) or []
        for entry in all_trainers:
            entry["account_type"] = normalize_account_type(entry.get("account_type"))
            entry.setdefault("avatar_icon", "avatar1.png")
            entry.setdefault("trainer_card_background", "default.png")
            if entry["account_type"]:
                account_types.add(entry["account_type"])
    except Exception as e:
        print("⚠️ Failed fetching all trainers:", e)

    return render_template(
        "admin_trainers.html",
        trainer_data=trainer_data,
        all_trainers=all_trainers,
        account_types=sorted(account_types),
    )

def _load_trainer_for_admin(username):
    """Common loader for trainer detail endpoints that enforces admin access."""
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return None, redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return None, redirect(url_for("dashboard"))

    _, trainer_data = find_user(username)
    if not trainer_data:
        flash(f"No trainer found with username '{username}'", "warning")
        return None, redirect(url_for("admin_trainers"))

    return trainer_data, None


@app.route("/admin/trainers/<username>")
def admin_trainer_detail(username):
    trainer_data, error_response = _load_trainer_for_admin(username)
    if error_response:
        return error_response

    return render_template(
        "admin_trainer_detail.html",
        trainer=trainer_data,
        show_back_link=True,
        modal_view=False,
    )


@app.route("/admin/trainers/<username>/panel")
def admin_trainer_panel(username):
    trainer_data, error_response = _load_trainer_for_admin(username)
    if error_response:
        return error_response

    return render_template(
        "partials/admin_trainer_panel.html",
        trainer=trainer_data,
        show_back_link=False,
        modal_view=True,
    )


@app.route("/admin/trainers/<username>/passport")
def admin_trainer_passport(username):
    trainer_data, error_response = _load_trainer_for_admin(username)
    if error_response:
        return error_response

    ledger_rows, ledger_summary = fetch_passport_ledger(
        trainer_data.get("trainer_username") or username,
        trainer_data.get("campfire_username")
    )
    total_stamps, stamp_entries, most_recent_stamp = get_passport_stamps(
        trainer_data.get("trainer_username") or username,
        trainer_data.get("campfire_username")
    )
    passport_pages = [stamp_entries[i:i + 12] for i in range(0, len(stamp_entries), 12)]

    return render_template(
        "partials/admin_trainer_passport.html",
        trainer=trainer_data,
        total_stamps=total_stamps,
        passport_pages=passport_pages,
        passport_stamps=stamp_entries,
        most_recent_stamp=most_recent_stamp,
        ledger=ledger_rows,
        ledger_summary=ledger_summary,
    )

@app.route("/admin/trainers/<username>/change_account_type", methods=["POST"])
def admin_change_account_type(username):
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    # ✅ Check admin rights via find_user
    _, admin_user = find_user(session["trainer"])
    if not admin_user or admin_user.get("account_type") != "Admin":
        flash("Unauthorized access.", "error")
        return redirect(url_for("dashboard"))

    requested_type = request.form.get("account_type")
    new_type = normalize_account_type(requested_type)
    if new_type not in ["Standard", "Kids Account", "Admin"]:
        flash("Invalid account type.", "error")
        return redirect(url_for("admin_trainer_detail", username=username))

    _, target_user = find_user(username)
    if not target_user:
        flash(f"No trainer found with username '{username}'", "warning")
        return redirect(url_for("admin_trainers"))

    if not supabase:
        flash("Supabase is unavailable. Please try again later.", "error")
        return redirect(url_for("admin_trainer_detail", username=username))

    trainer_username = target_user.get("trainer_username") or username

    try:
        supabase.table("sheet1") \
            .update({"account_type": new_type}) \
            .eq("trainer_username", trainer_username) \
            .execute()
        flash(f"✅ {trainer_username}'s account type updated to {new_type}", "success")
    except Exception as e:
        print("⚠️ Error updating account type:", e)
        flash("Failed to update account type.", "error")

    return redirect(url_for("admin_trainer_detail", username=username))

@app.route("/admin/trainers/<username>/reset_pin", methods=["POST"])
def admin_reset_pin(username):
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    # ✅ Check admin rights via find_user
    _, admin_user = find_user(session["trainer"])
    if not admin_user or admin_user.get("account_type") != "Admin":
        flash("Unauthorized access.", "error")
        return redirect(url_for("dashboard"))

    new_pin = request.form.get("new_pin")
    if not new_pin or len(new_pin) != 4 or not new_pin.isdigit():
        flash("PIN must be exactly 4 digits.", "error")
        return redirect(url_for("admin_trainer_detail", username=username))

    _, target_user = find_user(username)
    if not target_user:
        flash(f"No trainer found with username '{username}'", "warning")
        return redirect(url_for("admin_trainers"))

    if not supabase:
        flash("Supabase is unavailable. Please try again later.", "error")
        return redirect(url_for("admin_trainer_detail", username=username))

    trainer_username = target_user.get("trainer_username") or username
    hashed = _pin_hash_value(trainer_username, new_pin)

    try:
        supabase.table("sheet1") \
            .update({"pin_hash": hashed}) \
            .eq("trainer_username", trainer_username) \
            .execute()
        flash(f"✅ PIN for {trainer_username} has been reset.", "success")
    except Exception as e:
        print("⚠️ Error resetting PIN:", e)
        flash("Failed to reset PIN.", "error")

    return redirect(url_for("admin_trainer_detail", username=username))

# ====== Admin: RDAB Stats ======
from collections import Counter, defaultdict

STATS_RANGE_OPTIONS = [
    ("30d", "Last 30 days"),
    ("90d", "Last 90 days"),
    ("ytd", "Year to date"),
    ("365d", "Last 12 months"),
    ("all", "All time"),
]
STATS_GROUP_OPTIONS = [
    ("month", "Monthly"),
    ("quarter", "Quarterly"),
    ("year", "Yearly"),
]

def _load_supabase_table(table: str, columns: str = "*") -> list[dict]:
    """Return the raw rows for a Supabase table or an empty list on failure."""
    if not (USE_SUPABASE and supabase):
        return []
    try:
        response = supabase.table(table).select(columns).execute()
        return response.data or []
    except Exception as exc:
        print(f"⚠️ {table} fetch failed:", exc)
        return []

def _stats_time_bounds(range_key: str):
    now = datetime.now(timezone.utc)
    range_key = (range_key or "90d").lower()
    label_lookup = dict(STATS_RANGE_OPTIONS)
    label = label_lookup.get(range_key, label_lookup["90d"])
    start = None
    if range_key == "30d":
        start = now - timedelta(days=30)
    elif range_key == "90d":
        start = now - timedelta(days=90)
    elif range_key == "ytd":
        start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    elif range_key in {"365d", "1y"}:
        start = now - timedelta(days=365)
    elif range_key == "all":
        start = None
    else:
        start = now - timedelta(days=90)
        range_key = "90d"
        label = label_lookup["90d"]
    return range_key, start, now, label

def _parse_event_dt(value: str | None):
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        try:
            dt = parser.isoparse(value)
        except Exception:
            return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _group_bucket(dt: datetime, group_key: str):
    if not dt:
        return ((0,), "Unknown")
    group_key = group_key.lower()
    if group_key == "year":
        return ((dt.year,), str(dt.year))
    if group_key == "quarter":
        quarter = ((dt.month - 1) // 3) + 1
        return ((dt.year, quarter), f"Q{quarter} {dt.year}")
    # default: month
    return ((dt.year, dt.month), dt.strftime("%b %Y"))

def _normalize_status(value: str | None) -> str:
    return (value or "").upper().replace("-", "_").strip()

def _normalize_user(row: dict) -> str:
    return (row.get("campfire_username") or row.get("display_name") or "").strip().lower()

def _display_name(row: dict) -> str:
    return row.get("display_name") or row.get("campfire_username") or "Unknown Trainer"

@app.route("/admin/stats")
@admin_required
def admin_stats():
    range_key = request.args.get("range", "90d").lower()
    group_key = request.args.get("group", "month").lower()
    range_key, start_dt, end_dt, range_label = _stats_time_bounds(range_key)
    if group_key not in dict(STATS_GROUP_OPTIONS):
        group_key = "month"

    events = _load_supabase_table("events")
    attendance = _load_supabase_table("attendance")
    accounts = _load_supabase_table("sheet1", "trainer_username,account_type,stamps")

    ev_map: dict[str, dict] = {}
    filtered_event_ids: list[str] = []
    for e in events:
        eid = str(e.get("event_id") or "").strip().lower()
        dt = _parse_event_dt(e.get("start_time"))
        if not dt:
            continue
        ev_map[eid] = {
            "id": eid,
            "name": e.get("name") or "Unnamed meetup",
            "dt": dt,
            "date_iso": dt.isoformat(),
            "date_display": dt.strftime("%d %b %Y • %H:%M") if dt else "",
            "cover": e.get("cover_photo_url") or "",
            "location": e.get("location") or "",
        }
        if start_dt and dt < start_dt:
            continue
        if dt > end_dt:
            continue
        filtered_event_ids.append(eid)

    filtered_event_ids = sorted(filtered_event_ids, key=lambda eid: ev_map[eid]["dt"], reverse=True)
    filtered_set = set(filtered_event_ids)

    event_people: dict[str, dict] = defaultdict(dict)
    counts_by_event = Counter()
    counts_by_trainer = Counter()
    unique_attendees = set()

    for row in attendance:
        eid = str(row.get("event_id") or "").strip().lower()
        if not eid or eid not in filtered_set:
            continue
        user_key = _normalize_user(row)
        if not user_key:
            continue
        status = _normalize_status(row.get("rsvp_status"))
        entry = event_people[eid].setdefault(user_key, {
            "id": user_key,
            "display_name": _display_name(row),
            "campfire": (row.get("campfire_username") or "").strip(),
            "checked_in": False,
            "checked_in_at": None,
            "rsvp_status": None,
        })
        if status and status != "CHECKED_IN":
            entry["rsvp_status"] = status
        if status == "CHECKED_IN":
            entry["checked_in"] = True
            entry["checked_in_at"] = row.get("checked_in_at")
            unique_attendees.add(user_key)
            counts_by_trainer[entry["display_name"]] += 1
            counts_by_event[eid] += 1

    rsvp_totals = Counter({eid: len(roster) for eid, roster in event_people.items()})
    total_attendances = sum(counts_by_event.values())
    total_rsvps = sum(rsvp_totals.values())
    meetup_count = len(filtered_event_ids)
    meetings_with_checkins = sum(1 for eid in filtered_event_ids if counts_by_event[eid] > 0)
    avg_attendance = round(total_attendances / max(meetup_count, 1), 1)

    new_attendees = sum(1 for _, c in counts_by_trainer.items() if c == 1)
    unique_attendee_count = len(unique_attendees)
    returning_pct = round(
        100 * (1 - (new_attendees / max(unique_attendee_count, 1))),
        1,
    ) if unique_attendee_count else 0.0

    top_meetups = []
    for eid in sorted(filtered_event_ids,
                      key=lambda e: counts_by_event[e],
                      reverse=True)[:10]:
        meta = ev_map[eid]
        top_meetups.append({
            "event_id": eid,
            "name": meta["name"],
            "date": meta["date_iso"],
            "count": counts_by_event[eid],
            "rsvp": rsvp_totals.get(eid, 0),
        })

    top_trainers = [
        {"trainer": name, "count": count}
        for name, count in counts_by_trainer.most_common(10)
    ]

    grouped = {}
    for eid in filtered_event_ids:
        meta = ev_map[eid]
        key, label = _group_bucket(meta["dt"], group_key)
        bucket = grouped.setdefault(key, {"label": label, "events": 0, "attendance": 0})
        bucket["events"] += 1
    for eid, count in counts_by_event.items():
        meta = ev_map.get(eid)
        if not meta:
            continue
        key, label = _group_bucket(meta["dt"], group_key)
        bucket = grouped.setdefault(key, {"label": label, "events": 0, "attendance": 0})
        bucket["attendance"] += count
    growth_labels = []
    growth_events = []
    growth_attend = []
    for key in sorted(grouped.keys()):
        bucket = grouped[key]
        growth_labels.append(bucket["label"])
        growth_events.append(bucket["events"])
        growth_attend.append(bucket["attendance"])

    bins = {"0–4": 0, "5–9": 0, "10–19": 0, "20+": 0}
    for acct in accounts:
        try:
            stamps = int(acct.get("stamps") or 0)
        except Exception:
            stamps = 0
        if stamps <= 4:
            bins["0–4"] += 1
        elif stamps <= 9:
            bins["5–9"] += 1
        elif stamps <= 19:
            bins["10–19"] += 1
        else:
            bins["20+"] += 1
    stamp_labels = list(bins.keys())
    stamp_counts = list(bins.values())

    acct_counter = Counter(normalize_account_type(acct.get("account_type")) for acct in accounts)
    account_labels = list(acct_counter.keys())
    account_counts = list(acct_counter.values())

    meetup_browser = []
    for eid in filtered_event_ids:
        meta = ev_map[eid]
        roster = list(event_people.get(eid, {}).values())
        roster_sorted = sorted(roster, key=lambda a: a["display_name"].lower())
        checked = [p for p in roster_sorted if p["checked_in"]]
        rsvps_only = [p for p in roster_sorted if not p["checked_in"]]
        meetup_browser.append({
            "id": eid,
            "name": meta["name"],
            "date": meta["date_display"],
            "date_iso": meta["date_iso"],
            "cover": meta["cover"],
            "location": meta["location"],
            "rsvp_count": len(roster_sorted),
            "checkin_count": len(checked),
            "attendees": roster_sorted,
            "checked_in": checked,
            "rsvps": rsvps_only,
        })

    highlights = {
        "avg_attendance": avg_attendance,
        "unique_attendees": unique_attendee_count,
        "meetups_with_checkins": meetings_with_checkins,
        "returning_pct": returning_pct,
        "total_rsvps": total_rsvps,
        "timeframe_label": range_label,
    }

    range_options = [
        {"key": key, "label": label, "active": key == range_key}
        for key, label in STATS_RANGE_OPTIONS
    ]
    group_options = [
        {"key": key, "label": label, "active": key == group_key}
        for key, label in STATS_GROUP_OPTIONS
    ]

    timeframe_meta = {
        "label": range_label,
        "start": start_dt.isoformat() if start_dt else None,
        "end": end_dt.isoformat(),
        "event_count": meetup_count,
        "attendance_count": total_attendances,
        "rsvp_count": total_rsvps,
    }

    meetup_picker = {
        "total_events": meetup_count,
        "total_rsvps": total_rsvps,
        "total_checkins": total_attendances,
    }

    supabase_snapshot = {
        "events": events,
        "attendance": attendance,
        "events_count": len(events),
        "attendance_count": len(attendance),
    }

    return render_template(
        "admin_stats.html",
        range_options=range_options,
        group_options=group_options,
        selected_range=range_key,
        selected_group=group_key,
        highlights=highlights,
        total_attendances=total_attendances,
        top_meetups=top_meetups,
        top_trainers=top_trainers,
        growth_labels=growth_labels,
        growth_events=growth_events,
        growth_attend=growth_attend,
        stamp_labels=stamp_labels,
        stamp_counts=stamp_counts,
        account_labels=account_labels,
        account_counts=account_counts,
        meetup_browser=meetup_browser,
        meetup_picker=meetup_picker,
        timeframe_meta=timeframe_meta,
        supabase_snapshot=supabase_snapshot,
    )

@app.route("/admin/stats/raw.json")
@admin_required
def admin_stats_raw():
    events = _load_supabase_table("events")
    attendance = _load_supabase_table("attendance")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_count": len(events),
        "attendance_count": len(attendance),
        "events": events,
        "attendance": attendance,
    }
    return jsonify(payload)

@app.route("/toggle_maintenance")
def toggle_maintenance():
    if session.get("account_type") != "Admin":
        abort(403)
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    state = "ON" if MAINTENANCE_MODE else "OFF"
    flash(f"Maintenance mode is now {state}.", "warning")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/classic-stamps")
@admin_required
def admin_classic_stamps():
    status = (request.args.get("status", "PENDING") or "PENDING").upper()
    valid_statuses = CLASSIC_SUBMISSION_STATUSES | {"ALL"}
    if status not in valid_statuses:
        status = "PENDING"

    submissions_all = list_classic_submissions(None)
    counts = Counter()
    for entry in submissions_all:
        counts[(entry.get("status") or "PENDING").upper()] += 1
    counts["ALL"] = len(submissions_all)

    if status == "ALL":
        visible = submissions_all
    else:
        visible = [
            entry for entry in submissions_all
            if (entry.get("status") or "PENDING").upper() == status
        ]

    tabs = [
        {"code": "PENDING", "label": "Pending"},
        {"code": "AWARDED", "label": "Completed"},
        {"code": "REJECTED", "label": "Rejected"},
        {"code": "ALL", "label": "All"},
    ]

    return render_template(
        "admin_classic_stamps.html",
        submissions=visible,
        status=status,
        tabs=tabs,
        counts=dict(counts),
    )

def _redirect_to_classic_dashboard(fallback_status: str = "PENDING"):
    target = request.form.get("next") or url_for("admin_classic_stamps", status=fallback_status)
    if not target.startswith("/"):
        target = url_for("admin_classic_stamps", status=fallback_status)
    return redirect(target)

@app.route("/admin/classic-stamps/<submission_id>/award", methods=["POST"])
@admin_required
def admin_classic_stamps_award(submission_id):
    award_raw = (request.form.get("award_count") or "").strip()
    notes = (request.form.get("admin_notes") or "").strip()

    try:
        award_count = int(award_raw)
    except Exception:
        flash("Enter a whole number of stamps to award.", "warning")
        return _redirect_to_classic_dashboard()

    if award_count <= 0:
        flash("Stamp count must be at least 1.", "warning")
        return _redirect_to_classic_dashboard()

    submission = get_classic_submission(submission_id)
    if not submission:
        flash("Submission not found.", "error")
        return _redirect_to_classic_dashboard()

    status = (submission.get("status") or "PENDING").upper()
    trainer = submission.get("trainer_username")
    if not trainer:
        flash("Submission is missing a trainer username.", "error")
        return _redirect_to_classic_dashboard()

    if status == "AWARDED":
        flash("This submission has already been marked as completed.", "info")
        return _redirect_to_classic_dashboard("AWARDED")

    actor = _current_actor()
    ok, msg = adjust_stamps(trainer, award_count, "Classic", "award", actor)
    if not ok:
        flash(msg, "error")
        return _redirect_to_classic_dashboard()

    now = datetime.utcnow().isoformat()
    update_payload = {
        "status": "AWARDED",
        "awarded_count": award_count,
        "reviewed_by": actor,
        "reviewed_at": now,
        "admin_notes": notes,
        "updated_at": now,
    }

    try:
        supabase.table("classic_passport_submissions").update(update_payload).eq("id", submission_id).execute()
    except Exception as exc:
        print("⚠️ Failed to update classic submission after awarding:", exc)
        flash("Stamps were awarded, but we could not update the submission record. Please double-check manually.", "warning")
        return _redirect_to_classic_dashboard("AWARDED")

    subject = "Classic passport stamps awarded"
    message_lines = [
        f"Thanks for sharing your classic passports! We've added {award_count} stamp{'s' if award_count != 1 else ''} to your digital passport.",
        "You can recycle the paper cards or keep them as memorabilia — whichever you prefer.",
    ]
    if notes:
        message_lines.append("")
        message_lines.append(f"Admin note: {notes}")
    send_notification(trainer, subject, "\n".join(message_lines))

    flash(f"Awarded {award_count} stamp{'s' if award_count != 1 else ''} to {trainer}.", "success")
    return _redirect_to_classic_dashboard("AWARDED")

@app.route("/admin/classic-stamps/<submission_id>/reject", methods=["POST"])
@admin_required
def admin_classic_stamps_reject(submission_id):
    notes = (request.form.get("admin_notes") or "").strip()
    if not notes:
        flash("Please add a short note explaining why it was rejected.", "warning")
        return _redirect_to_classic_dashboard()

    submission = get_classic_submission(submission_id)
    if not submission:
        flash("Submission not found.", "error")
        return _redirect_to_classic_dashboard()

    status = (submission.get("status") or "PENDING").upper()
    if status == "AWARDED":
        flash("This submission has already been marked completed. You can leave additional notes from the award form instead.", "info")
        return _redirect_to_classic_dashboard("AWARDED")

    trainer = submission.get("trainer_username")
    if not trainer:
        flash("Submission is missing a trainer username.", "error")
        return _redirect_to_classic_dashboard()

    actor = _current_actor()
    now = datetime.utcnow().isoformat()
    update_payload = {
        "status": "REJECTED",
        "reviewed_by": actor,
        "reviewed_at": now,
        "admin_notes": notes,
        "updated_at": now,
    }

    try:
        supabase.table("classic_passport_submissions").update(update_payload).eq("id", submission_id).execute()
    except Exception as exc:
        print("⚠️ Failed to update classic submission on rejection:", exc)
        flash("We couldn't update the submission status. Please try again.", "error")
        return _redirect_to_classic_dashboard()

    subject = "Classic passport submission needs a tweak"
    message = (
        "Thanks for sending a photo of your classic passports. "
        "We couldn't approve it this time. "
        "Please review the note below and send a new photo when you're ready.\n\n"
        f"Admin note: {notes}"
    )
    send_notification(trainer, subject, message)

    flash(f"Marked the submission from {trainer} as rejected.", "success")
    return _redirect_to_classic_dashboard("REJECTED")

# ====== Admin: Notification Center ======
def fetch_trainer_roster():
    roster: list[dict] = []
    account_types: set[str] = set()
    if not supabase:
        return roster, []
    try:
        resp = supabase.table("sheet1").select("trainer_username, account_type").execute()
        for row in resp.data or []:
            username = (row.get("trainer_username") or "").strip()
            if not username:
                continue
            acct = normalize_account_type(row.get("account_type"))
            roster.append({
                "trainer_username": username,
                "account_type": acct,
            })
            if acct:
                account_types.add(acct)
        roster.sort(key=lambda item: item["trainer_username"].lower())
    except Exception as exc:
        print("⚠️ Failed fetching trainer roster for notifications:", exc)
    return roster, sorted(account_types)


NOTIFICATION_TYPE_CHOICES = [
    ("announcement", "Announcement"),
    ("system", "System"),
    ("prize", "Prize"),
]

def _notification_json_error(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status

def _fetch_notification(notification_id: str):
    try:
        resp = (
            supabase.table("notifications")
            .select("*")
            .eq("id", notification_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return rows[0]
    except Exception as exc:
        print("⚠️ Failed fetching notification:", exc)
    return None


@app.route("/admin/notifications", methods=["GET", "POST"])
def admin_notifications():
    if "trainer" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("⛔ Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    trainer_roster, account_types = fetch_trainer_roster()

    if request.method == "POST":
        notif_type = request.form.get("type", "announcement")
        subject = request.form.get("subject", "").strip()
        raw_message = request.form.get("message", "").strip()
        audience_mode = request.form.get("audience_mode", "all")
        selected = [a.strip() for a in request.form.getlist("audience_multi") if a.strip()]
        manual_entry = request.form.get("audience_manual", "")
        manual_list = [name.strip() for name in manual_entry.split(",") if name.strip()]

        if not subject or not raw_message:
            flash("Subject and message are required.", "warning")
            return redirect(url_for("admin_notifications"))

        recipients: list[str]
        if audience_mode == "all":
            recipients = ["ALL"]
        else:
            recipients = selected + manual_list
            recipients = [name for name in recipients if name]
            if any(name.upper() == "ALL" for name in recipients):
                recipients = ["ALL"]
            else:
                deduped = []
                seen = set()
                for name in recipients:
                    key = name.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(name)
                recipients = deduped

        if not recipients:
            flash("Select at least one trainer or choose All Trainers.", "warning")
            return redirect(url_for("admin_notifications"))

        sanitized_message = sanitize_notification_html(raw_message)
        payloads = []
        sent_at = datetime.utcnow().isoformat()
        for recipient in recipients:
            payloads.append({
                "type": notif_type,
                "audience": recipient,
                "subject": subject,
                "message": sanitized_message,
                "metadata": {},
                "sent_at": sent_at,
                "read_by": [],
            })

        try:
            supabase.table("notifications").insert(payloads).execute()
            if recipients == ["ALL"]:
                flash("✅ Notification sent to all trainers.", "success")
            else:
                total = len(recipients)
                flash(f"✅ Notification sent to {total} trainer{'s' if total != 1 else ''}.", "success")
        except Exception as e:
            print("⚠️ Failed sending notification:", e)
            flash("❌ Failed to send notification.", "error")

        return redirect(url_for("admin_notifications"))

    notifications = []
    try:
        resp = (supabase.table("notifications")
                .select("*")
                .order("sent_at", desc=True)
                .limit(100)
                .execute())
        notifications = resp.data or []
    except Exception as e:
        print("⚠️ Failed loading notifications:", e)

    type_lookup = {value: label for value, label in NOTIFICATION_TYPE_CHOICES}
    notification_categories = []
    for value, label in NOTIFICATION_TYPE_CHOICES:
        notification_categories.append({"value": value, "label": label})
    for entry in notifications:
        raw_type = (entry.get("type") or "").strip().lower()
        if not raw_type or raw_type in type_lookup:
            continue
        label = raw_type.title()
        type_lookup[raw_type] = label
        notification_categories.append({"value": raw_type, "label": label})

    return render_template(
        "admin_notifications.html",
        notifications=notifications,
        trainer_roster=trainer_roster,
        account_types=account_types,
        notification_types=NOTIFICATION_TYPE_CHOICES,
        notification_categories=notification_categories,
    )

@app.route("/admin/notifications/<notification_id>/update", methods=["POST"])
def admin_notifications_update(notification_id):
    if not _is_admin():
        return _notification_json_error("Admins only.", status=403)

    payload = request.get_json(silent=True) or {}
    subject = (payload.get("subject") or request.form.get("subject") or "").strip()
    notif_type = (payload.get("type") or request.form.get("type") or "announcement").strip().lower()
    message_raw = (payload.get("message") or request.form.get("message") or "").strip()

    if not subject or not message_raw:
        return _notification_json_error("Subject and message are required.", status=400)

    sanitized_message = sanitize_notification_html(message_raw)
    update_data = {
        "subject": subject,
        "type": notif_type,
        "message": sanitized_message,
        "updated_at": datetime.utcnow().isoformat(),
    }

    try:
        resp = supabase.table("notifications").update(update_data).eq("id", notification_id).execute()
        updated = resp.data[0] if resp.data else _fetch_notification(notification_id)
        if not updated:
            return _notification_json_error("Notification updated but not found. Refresh the page.", status=404)
        return jsonify({"success": True, "notification": updated})
    except Exception as exc:
        print("⚠️ Failed to update notification:", exc)
        return _notification_json_error("Failed to update notification.", status=500)

@app.route("/admin/notifications/<notification_id>/delete", methods=["POST"])
def admin_notifications_delete(notification_id):
    if not _is_admin():
        return _notification_json_error("Admins only.", status=403)

    try:
        supabase.table("notifications").delete().eq("id", notification_id).execute()
        return jsonify({"success": True, "notification_id": notification_id})
    except Exception as exc:
        print("⚠️ Failed to delete notification:", exc)
        return _notification_json_error("Failed to delete notification.", status=500)

# ==== Login ====
def _process_trainer_login(username: str, pin: str) -> dict:
    """Shared login handler for both full-page and inline logins."""
    username = (username or "").strip()
    pin = (pin or "").strip()
    security_state = session.get("login_security") or {
        "remaining": LOGIN_MAX_ATTEMPTS,
        "lock_until": None,
    }
    session["login_security"] = security_state

    now = time.time()
    lock_until = security_state.get("lock_until")
    if lock_until and now < lock_until:
        wait_seconds = max(int(lock_until - now), 1)
        return {
            "success": False,
            "error": f"Too many incorrect attempts. Try again in {wait_seconds} seconds.",
            "locked": True,
        }
    if not username or not pin:
        return {
            "success": False,
            "error": "Trainer name and PIN are required.",
            "locked": False,
        }

    _, user = find_user(username)
    if user and _pin_matches(user, pin):
        session["trainer"] = user.get("trainer_username")
        session["account_type"] = normalize_account_type(user.get("account_type"))
        session.permanent = True
        try:
            supabase.table("sheet1") \
                .update({"last_login": datetime.utcnow().isoformat()}) \
                .eq("trainer_username", user.get("trainer_username")) \
                .execute()
        except Exception as exc:
            print("⚠️ Supabase last_login update failed:", exc)

        security_state["remaining"] = LOGIN_MAX_ATTEMPTS
        security_state["lock_until"] = None
        session["login_security"] = security_state
        return {"success": True, "trainer": user.get("trainer_username"), "user": user}

    remaining = max(security_state.get("remaining", LOGIN_MAX_ATTEMPTS) - 1, 0)
    security_state["remaining"] = remaining
    if remaining <= 0:
        security_state["lock_until"] = now + LOGIN_LOCKOUT_SECONDS
        security_state["remaining"] = LOGIN_MAX_ATTEMPTS
        session["login_security"] = security_state
        return {
            "success": False,
            "error": f"Too many incorrect attempts. Try again in {LOGIN_LOCKOUT_SECONDS} seconds.",
            "locked": True,
        }

    security_state["lock_until"] = None
    attempt_word = "attempt" if remaining == 1 else "attempts"
    session["login_security"] = security_state
    return {
        "success": False,
        "error": f"Wrong PIN. {remaining} {attempt_word} remaining.",
        "locked": False,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    # 👇 NEW: If user already logged in, skip the login page entirely
    if "trainer" in session:
        # If they have a last_page stored, send them there
        last_page = session.get("last_page", "dashboard")
        try:
            return redirect(url_for(last_page))
        except:
            return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pin = request.form.get("pin", "")
        result = _process_trainer_login(username, pin)
        if result.get("success"):
            user = result.get("user") or {}
            flash(f"Welcome back, {user.get('trainer_username', 'Trainer')}!", "success")
            last_page = session.pop("last_page", None)
            if last_page:
                return redirect(last_page)
            return redirect(url_for("dashboard"))
        else:
            flash(result.get("error") or "Unable to log in.", "error")
            return redirect(url_for("login"))

    # GET request — just show login form
    return render_template("login.html")


@app.route("/api/session/login", methods=["POST"])
def api_session_login():
    payload = request.get_json(force=True, silent=True) or {}
    username = (payload.get("username") or "").strip()
    pin = (payload.get("pin") or "").strip()
    if not username or not pin:
        return jsonify({"success": False, "error": "Trainer name and PIN are required."}), 400
    result = _process_trainer_login(username, pin)
    if result.get("success"):
        return jsonify({"success": True, "trainer": result.get("trainer")})
    status_code = 429 if result.get("locked") else 401
    return jsonify({"success": False, "error": result.get("error") or "Unable to log in."}), status_code

# ====== Sign Up ======
def _trainer_exists(trainer_name: str) -> bool:
    """Return True if this trainer username already exists in Supabase."""
    trainer_lc = (trainer_name or "").strip().lower()
    if not trainer_lc:
        return False

    if not supabase:
        return False

    try:
        resp = (
            supabase.table("sheet1")
            .select("trainer_username")
            .ilike("trainer_username", trainer_lc)
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception as exc:
        print("⚠️ Supabase _trainer_exists lookup failed:", exc)
        return False


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        pin = request.form.get("pin")
        memorable = request.form.get("memorable")
        file = request.files.get("profile_screenshot")

        if not (pin and memorable and file):
            flash("All fields are required!", "warning")
            return redirect(url_for("signup"))

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(file.filename))
        file.save(filepath)
        trainer_name = extract_trainer_name(filepath)
        os.remove(filepath)

        if not trainer_name:
            flash("Could not detect trainer name from screenshot. Please try again.", "error")
            return redirect(url_for("signup"))

        session["signup_details"] = {
            "trainer_name": trainer_name,
            "pin": pin,
            "memorable": memorable,
        }
        return redirect(url_for("detectname"))

    return render_template("signup.html")

# ====== Confirm Detected Name ======
@app.route("/detectname", methods=["GET", "POST"])
def detectname():
    details = session.get("signup_details")
    if not details:
        flash("Session expired. Please try signing up again.", "warning")
        return redirect(url_for("signup"))
    if request.method == "GET":
        trainer_name = details.get("trainer_name", "")
        return render_template("detectname.html", trainer_name=trainer_name)

    action = request.form.get("action")
    if action == "confirm":
        edited_name = (request.form.get("trainer_name") or "").strip()
        if not edited_name:
            flash("Trainer name cannot be empty. Please double-check it.", "error")
            return redirect(url_for("detectname"))

        # Update the cached details so later steps use the edited name
        details["trainer_name"] = edited_name
        session["signup_details"] = details

        # ✅ Prevent duplicate usernames
        if _trainer_exists(details["trainer_name"]):
            flash("This trainer name is already registered. Please log in instead.", "error")
            session.pop("signup_details", None)
            return redirect(url_for("home"))
        return redirect(url_for("age"))

    if action == "retry":
        flash("Please upload a clearer screenshot with your trainer name visible.", "warning")
        session.pop("signup_details", None)
        return redirect(url_for("signup"))

    flash("Unexpected action. Please try again.", "warning")
    return redirect(url_for("detectname"))

# ====== Age Selection ======
@app.route("/age", methods=["GET", "POST"])
def age():
    details = session.get("signup_details")
    if not details:
        flash("Session expired. Please try signing up again.", "warning")
        return redirect(url_for("signup"))

    if request.method == "POST":
        choice = request.form.get("age_choice")
        if choice == "13plus":
            flash("✅ Great! You’re signing up as 13 or older.", "success")
            return redirect(url_for("campfire"))
        elif choice == "under13":
            # ✅ Backend guard to prevent duplicates
            if _trainer_exists(details["trainer_name"]):
                flash("This trainer already exists. Please log in.", "error")
                session.pop("signup_details", None)
                return redirect(url_for("home"))

            if not supabase:
                flash("Supabase is currently unavailable. Please try again later.", "error")
                return redirect(url_for("signup"))

            trainer_username = details["trainer_name"]
            payload = {
                "trainer_username": details["trainer_name"],
                "pin_hash": _pin_hash_value(trainer_username, details["pin"]),
                "memorable_password": details["memorable"],
                "last_login": datetime.utcnow().isoformat(),
                "campfire_username": "Kids Account",
                "stamps": 0,
                "avatar_icon": "avatar1.png",
                "trainer_card_background": "default.png",
                "account_type": "Kids Account",
            }
            if not supabase_insert_row("sheet1", payload):
                print("⚠️ Supabase kids signup insert failed (after retry)")
                error_text = ""
                try:
                    error_text = getattr(g, "supabase_last_error", "") or ""
                    if hasattr(g, "supabase_last_error"):
                        del g.supabase_last_error
                except RuntimeError:
                    pass
                if "duplicate key value" in error_text.lower():
                    flash("This trainer already exists. Please log in instead.", "error")
                else:
                    flash("Signup failed due to a server error. Please try again shortly.", "error")
                return redirect(url_for("signup"))

            trigger_lugia_refresh()
            session.pop("signup_details", None)
            flash("👶 Kids Account created successfully!", "success")
            return redirect(url_for("home"))
        else:
            flash("Please select an option.", "warning")

    return render_template("age.html")

# ====== Campfire Username Step (13+) ======
@app.route("/campfire", methods=["GET", "POST"])
def campfire():
    details = session.get("signup_details")
    if not details:
        flash("Session expired. Please try signing up again.", "warning")
        return redirect(url_for("signup"))

    if request.method == "POST":
        raw = (request.form.get("campfire_username") or "").strip()
        if "@" in raw:
            flash("Leave off the @ symbol from your Campfire username.", "warning")
            return redirect(url_for("campfire"))
        campfire_username = raw
        if not campfire_username:
            flash("Campfire username is required.", "warning")
            return redirect(url_for("campfire"))

        # ✅ Backend guard to prevent duplicates
        if _trainer_exists(details["trainer_name"]):
            flash("This trainer already exists. Please log in.", "error")
            session.pop("signup_details", None)
            return redirect(url_for("home"))

        if not supabase:
            flash("Supabase is currently unavailable. Please try again later.", "error")
            return redirect(url_for("signup"))

        trainer_username = details["trainer_name"]
        payload = {
            "trainer_username": details["trainer_name"],
            "pin_hash": _pin_hash_value(trainer_username, details["pin"]),
            "memorable_password": details["memorable"],
            "last_login": datetime.utcnow().isoformat(),
            "campfire_username": campfire_username,
            "stamps": 0,
            "avatar_icon": "avatar1.png",
            "trainer_card_background": "default.png",
            "account_type": "Standard",
        }
        if not supabase_insert_row("sheet1", payload):
            print("⚠️ Supabase signup insert failed (after retry)")
            error_text = ""
            try:
                error_text = getattr(g, "supabase_last_error", "") or ""
                if hasattr(g, "supabase_last_error"):
                    del g.supabase_last_error
            except RuntimeError:
                pass
            if "duplicate key value" in error_text.lower():
                flash("This trainer already exists. Please log in instead.", "error")
            else:
                flash("Signup failed due to a server error. Please try again shortly.", "error")
            return redirect(url_for("signup"))

        trigger_lugia_refresh()
        session.pop("signup_details", None)
        flash("Signup successful! Please log in.", "success")
        return redirect(url_for("home"))

    return render_template("campfire.html")

# ====== Recover (reset PIN by memorable) ======
@app.route("/recover", methods=["GET", "POST"])
def recover():
    if request.method == "POST":
        username = request.form.get("username")
        memorable = request.form.get("memorable")
        new_pin = request.form.get("new_pin")

        _, user = find_user(username)
        if not user:
            flash("❌ No trainer found with that name.", "error")
            return redirect(url_for("recover"))

        stored_memorable = user.get("memorable_password") or user.get("Memorable Password")
        if stored_memorable != memorable:
            flash("⚠️ Memorable password does not match.", "error")
            return redirect(url_for("recover"))

        trainer_username = user.get("trainer_username") or user.get("Trainer Username")
        if not trainer_username or not supabase:
            flash("Unable to reset PIN right now. Please contact support.", "error")
            return redirect(url_for("recover"))

        try:
            new_hash = _pin_hash_value(trainer_username, new_pin)
            supabase.table("sheet1").update({
                "pin_hash": new_hash,
                "last_login": datetime.utcnow().isoformat(),
            }).eq("trainer_username", trainer_username).execute()
        except Exception as exc:
            print("⚠️ Supabase PIN reset failed:", exc)
            flash("Unable to reset PIN right now. Please try again soon.", "error")
            return redirect(url_for("recover"))

        flash("✅ PIN reset! You can log in now.", "success")
        return redirect(url_for("home"))

    return render_template("recover.html")

# ====== Dashboard ======
@app.route("/dashboard")
def dashboard():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to access your dashboard.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]
    _, user = find_user(trainer)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("home"))

    campfire_username = user.get("campfire_username", "")

    total_stamps, stamps, most_recent_stamp = get_passport_stamps(trainer, campfire_username)
    current_stamps = int(user.get("stamps", 0) or 0)

    most_recent_meetup = get_most_recent_meetup(trainer, campfire_username)
    upcoming_widget_events = []
    for ev in fetch_upcoming_events(limit=1):
        start_local = ev["start_local"]
        location = ev["location"] or ""
        short_location = location.split(",")[0].strip() if location else ""
        upcoming_widget_events.append({
            "event_id": ev["event_id"],
            "name": ev["name"],
            "date_label": start_local.strftime("%a %d %b"),
            "time_label": start_local.strftime("%H:%M"),
            "location": short_location,
            "campfire_url": ev["campfire_url"],
            "cover_photo": ev["cover_photo_url"],
        })

    bulletin_posts = get_community_bulletin_posts()
    bulletin_preview = _bulletin_widget_preview(bulletin_posts)
    bulletin_latest_posts = bulletin_posts[:2]

    return render_template(
        "dashboard.html",
        trainer=trainer,
        stamps=stamps,
        total_stamps=total_stamps,
        current_stamps=current_stamps,
        avatar=user.get("avatar_icon", "avatar1.png"),
        background=user.get("trainer_card_background", "default.png"),
        campfire_username=campfire_username,
        most_recent_meetup=most_recent_meetup,
        account_type=normalize_account_type(user.get("account_type")),
        show_back=False,
        upcoming_meetups=upcoming_widget_events,
        calendar_url=url_for("calendar_view"),
        show_catalog_app=SHOW_CATALOG_APP,
        show_city_perks_app=SHOW_CITY_PERKS_APP,
        show_city_guides_app=SHOW_CITY_GUIDES_APP,
        show_leagues_app=SHOW_LEAGUES_APP,
        bulletin_preview=bulletin_preview,
        bulletin_latest_posts=bulletin_latest_posts,
    )

@app.route("/calendar")
def calendar_view():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view the meetup calendar.", "warning")
        return redirect(url_for("home"))

    events = fetch_upcoming_events()
    calendar_events = serialize_calendar_events(events)
    return render_template(
        "calendar.html",
        calendar_events=calendar_events,
        has_events=bool(calendar_events),
        show_back=False,
        public_view=False,
        login_url=url_for("login"),
        title="Meetup Calendar",
    )


@app.route("/city-perks")
def city_perks_page():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to explore City Perks.", "warning")
        return redirect(url_for("home"))

    ensure_city_perks_cache()

    def _live_query(now_utc):
        return CityPerk.query.filter(
            CityPerk.is_active.is_(True),
            CityPerk.start_date <= now_utc,
            or_(CityPerk.end_date.is_(None), CityPerk.end_date >= now_utc),
        )

    area = (request.args.get("area") or "").strip() or None
    category = (request.args.get("category") or "").strip() or None
    search_term = (request.args.get("q") or "").strip()

    now = datetime.now(timezone.utc)
    query = _live_query(now)
    if area:
        query = query.filter(CityPerk.area == area)
    if category:
        query = query.filter(CityPerk.category == category)
    if search_term:
        like = f"%{search_term.lower()}%"
        query = query.filter(
            or_(
                db.func.lower(CityPerk.name).like(like),
                db.func.lower(CityPerk.partner_name).like(like),
                db.func.lower(CityPerk.short_tagline).like(like),
            )
        )

    perks = query.order_by(
        CityPerk.start_date.asc(),
        CityPerk.name.asc(),
    ).all()

    map_ready_perks = [
        perk.to_public_dict()
        for perk in perks
        if perk.show_on_map and perk.latitude is not None and perk.longitude is not None
    ]

    areas = [
        value
        for (value,) in _live_query(now)
        .with_entities(CityPerk.area)
        .filter(CityPerk.area.isnot(None))
        .distinct()
        .order_by(CityPerk.area.asc())
        .all()
    ]
    categories = [
        value
        for (value,) in _live_query(now)
        .with_entities(CityPerk.category)
        .filter(CityPerk.category.isnot(None))
        .distinct()
        .order_by(CityPerk.category.asc())
        .all()
    ]

    filters_active = any([area, category, search_term])

    return render_template(
        "city_perks.html",
        perks=perks,
        areas=areas,
        categories=categories,
        selected_area=area,
        selected_category=category,
        search_term=search_term,
        filters_active=filters_active,
        perks_count=len(perks),
        featured_count=0,
        show_back=False,
        api_url=url_for("city_perks_api.list_live_city_perks"),
        map_perks=map_ready_perks,
    )


@app.route("/meetups")
def calendar_public():
    events = fetch_upcoming_events()
    calendar_events = serialize_calendar_events(events)
    return render_template(
        "calendar.html",
        calendar_events=calendar_events,
        has_events=bool(calendar_events),
        show_back=False,
        public_view=True,
        login_url=url_for("login"),
        title="Meetup Calendar",
    )

@app.route("/events/<event_id>.ics")
def event_ics_file(event_id):
    event_row = None
    if USE_SUPABASE and supabase:
        try:
            resp = (supabase.table("events")
                    .select("id,event_id,name,start_time,end_time,location,url")
                    .eq("event_id", event_id)
                    .limit(1)
                    .execute())
            data = resp.data or []
            if data:
                event_row = data[0]
            else:
                fallback = (supabase.table("events")
                            .select("id,event_id,name,start_time,end_time,location,url")
                            .eq("id", event_id)
                            .limit(1)
                            .execute())
                fallback_data = fallback.data or []
                if fallback_data:
                    event_row = fallback_data[0]
        except Exception as exc:
            print("⚠️ Supabase ICS fetch failed:", exc)

    if not event_row:
        for local in load_custom_events():
            if str(local.get("event_id") or "") == event_id:
                event_row = local
                break

    if not event_row:
        abort(404)

    start_dt = parse_dt_safe(event_row.get("start_time"))
    sentinel = datetime.min.replace(tzinfo=timezone.utc)
    if start_dt <= sentinel:
        abort(404)
    end_dt = parse_dt_safe(event_row.get("end_time"))
    if end_dt <= sentinel or end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=2)

    dtstamp = datetime.utcnow().replace(tzinfo=timezone.utc)

    def _ics_format(ts: datetime) -> str:
        return ts.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    uid = (event_row.get("event_id") or event_row.get("id") or event_id or f"evt-{uuid.uuid4().hex}")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//RDAB Community//Meetup Calendar//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}@rdab.app",
        f"DTSTAMP:{_ics_format(dtstamp)}",
        f"DTSTART:{_ics_format(start_dt)}",
        f"DTEND:{_ics_format(end_dt)}",
        f"SUMMARY:{(event_row.get('name') or 'RDAB Meetup').replace('\\n', ' ')}",
    ]

    location = event_row.get("location") or ""
    if location:
        lines.append(f"LOCATION:{location.replace('\\n', ' ')}")

    description_bits = []
    if event_row.get("url"):
        description_bits.append(f"Campfire RSVP: {event_row['url']}")
    description = "\\n".join(description_bits)
    if description:
        lines.append(f"DESCRIPTION:{description}")

    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    ics_body = "\r\n".join(lines)
    filename = secure_filename(f"{event_row.get('name') or 'meetup'}.ics") or "meetup.ics"

    response = make_response(ics_body)
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

# ====== Inbox, Notifications, Receipts ======
def _normalize_iso(dt_val):
    """Ensure datetime-like values always return UTC ISO string with tzinfo."""
    if not dt_val:
        return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

    # If it's already a datetime
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=timezone.utc)
        else:
            dt_val = dt_val.astimezone(timezone.utc)
        return dt_val.isoformat()

    # If it's a string (ISO-ish)
    try:
        from dateutil import parser
        parsed = parser.isoparse(str(dt_val))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat()
    except Exception:
        return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def _build_receipt_message(trainer, rec):
    """Turn a redemption record into an inbox-style 'message' dict."""
    item = rec.get("item_snapshot") or {}
    meetup = (rec.get("metadata") or {}).get("meetup") or {}
    item_name = item.get("name") or "Catalog Item"
    meetup_name = meetup.get("name") or "Unknown meetup"
    meetup_location = meetup.get("location") or ""
    meetup_date = meetup.get("date") or ""
    meetup_time = meetup.get("start_time") or ""
    meetup_stamp = " ".join(part for part in [meetup_name, meetup_location] if part).strip()
    meetup_when = " ".join(part for part in [meetup_date, meetup_time] if part).strip()

    return {
        "id": f"rec:{rec['id']}",
        "subject": f"🧾 Receipt: {item_name}",
        "message": (
            f"You redeemed {item_name} for {rec.get('stamps_spent', 0)} stamps"
            + (f" at {meetup_stamp}" if meetup_stamp else "")
            + (f" ({meetup_when})" if meetup_when else "")
            + "."
        ),
        "sent_at": _normalize_iso(rec.get("created_at")),
        "type": "receipt",
        "read_by": [],
        "metadata": {
            "url": f"/catalog/receipt/{rec['id']}",
            "cta_label": "View receipt",
            "status": rec.get("status"),
            "meetup": meetup if meetup else None,
            }
    }

@app.route("/inbox")
def inbox():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view your inbox.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]
    tab = request.args.get("tab", "all").lower()           # all | notifications | receipts
    sort_by = request.args.get("sort", "newest")           # newest | oldest | unread | read | type

    messages = []

    # --- Pull notifications (audience = trainer or ALL) ---
    notif_rows = []
    if USE_SUPABASE and supabase and tab in ("all", "notifications"):
        try:
            nq = (supabase.table("notifications")
                  .select("*")
                  .or_(f"audience.eq.{trainer},audience.eq.ALL"))

            if sort_by == "unread":
                nq = nq.not_.contains("read_by", [trainer])
            elif sort_by == "read":
                nq = nq.contains("read_by", [trainer])
            elif sort_by == "type":
                pass  # type sort handled after merge

            nq = nq.order("sent_at", desc=(sort_by != "oldest"))
            notif_rows = nq.execute().data or []

            if tab == "notifications":
                notif_rows = [n for n in notif_rows if (n.get("type") or "").lower() != "receipt"]
        except Exception as e:
            print("⚠️ Supabase notifications fetch failed:", e)

    # --- Pull receipts as message-like objects ---
    receipt_rows = []
    if USE_SUPABASE and supabase and tab in ("all", "receipts"):
        try:
            rq = (supabase.table("redemptions")
                  .select("*")
                  .eq("trainer_username", trainer)
                  .order("created_at", desc=(sort_by != "oldest")))
            raw = rq.execute().data or []
            receipt_rows = [_build_receipt_message(trainer, r) for r in raw]
        except Exception as e:
            print("⚠️ Supabase receipts fetch failed:", e)

    # --- Merge ---
    if tab == "notifications":
        messages = notif_rows
    elif tab == "receipts":
        messages = receipt_rows
    else:
        messages = (notif_rows or []) + (receipt_rows or [])

    # --- Sorting & filtering ---
    if sort_by == "type":
        messages.sort(
            key=lambda m: (
                (m.get("type") or "").lower(),
                -parse_dt_safe(m.get("sent_at")).timestamp()
            )
        )
    elif sort_by == "oldest":
        messages.sort(key=lambda m: parse_dt_safe(m.get("sent_at")))
    else:  # newest
        messages.sort(key=lambda m: parse_dt_safe(m.get("sent_at")), reverse=True)

    if not messages:
        messages = [{
            "subject": "📭 No messages yet",
            "message": "Your inbox is empty. You’ll see updates, receipts, and announcements here.",
            "sent_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
            "type": "info",
            "read_by": []
        }]

    bulletin_posts = get_community_bulletin_posts()
    latest_bulletin_post = bulletin_posts[0] if bulletin_posts else None

    return render_template(
        "inbox.html",
        trainer=trainer,
        inbox=messages,
        sort_by=sort_by,
        tab=tab,
        show_back=False,
        latest_bulletin_post=latest_bulletin_post
    )

@app.route("/inbox/message/<message_id>")
def inbox_message(message_id):
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view your inbox.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]
    if not (USE_SUPABASE and supabase):
        flash("Inbox messages are unavailable right now. Please try again later.", "error")
        return redirect(url_for("inbox"))

    # Receipt messages
    if message_id.startswith("rec:"):
        rec_id = message_id.split("rec:", 1)[1]
        try:
            r = (supabase.table("redemptions")
                 .select("*")
                 .eq("id", rec_id)
                 .limit(1)
                 .execute())
            if not r.data:
                abort(404)
            rec = r.data[0]
            if (rec.get("trainer_username") or "").lower() != trainer.lower():
                abort(403)
            msg = _build_receipt_message(trainer, rec)
        except Exception as e:
            print("⚠️ inbox_message (receipt) fetch failed:", e)
            abort(500)
        return render_template("inbox_message.html", msg=msg, show_back=False)

    # Normal notification
    try:
        r = (supabase.table("notifications")
             .select("*")
             .eq("id", message_id)
             .limit(1)
             .execute())
        if not r.data:
            abort(404)
        msg = r.data[0]

        # Mark as read
        read_by = msg.get("read_by") or []
        if trainer not in read_by:
            read_by.append(trainer)
            supabase.table("notifications").update({"read_by": read_by}).eq("id", message_id).execute()
        msg["read_by"] = read_by
    except Exception as e:
        print("⚠️ inbox_message (notification) failed:", e)
        abort(500)

    return render_template("inbox_message.html", msg=msg, show_back=False)


@app.route("/community-bulletin")
def community_bulletin():
    session["last_page"] = request.path

    posts_with_sections = get_community_bulletin_posts(include_sections=True)
    memory_albums = _build_memory_albums(posts_with_sections)

    posts: list[dict] = []
    for post in posts_with_sections:
        trimmed = dict(post)
        trimmed.pop("content_sections", None)
        posts.append(trimmed)

    latest_post = posts[0] if posts else None
    featured_posts = [p for p in posts if p.get("is_featured")] or posts[:2]
    featured_posts = featured_posts[:3]
    latest_feed = posts[:6]

    category_sections = []
    category_map: dict[str, list[dict]] = {}
    for post in posts:
        cat = (post.get("category") or "Updates").title()
        if cat not in category_map:
            category_map[cat] = []
            category_sections.append({
                "name": cat,
                "posts": category_map[cat],
            })
        category_map[cat].append(post)

    tag_filters = sorted({(p.get("tag") or "Update") for p in posts})

    return render_template(
        "community_bulletin.html",
        posts=posts,
        latest_post=latest_post,
        latest_feed=latest_feed,
        featured_posts=featured_posts,
        category_sections=category_sections,
        tag_filters=tag_filters,
        memory_albums=memory_albums,
        trainer=session.get("trainer"),
    )


@app.route("/community-bulletin/<slug>")
def community_bulletin_post(slug):
    session["last_page"] = request.path

    posts = get_community_bulletin_posts()
    post = get_community_bulletin_post(slug)
    if not post:
        # fallback to list search in case slug exists only in cached list
        post = next((p for p in posts if (p.get("slug") or "").lower() == slug.lower()), None)
    if not post:
        abort(404)

    formatted_sections = []
    for idx, section in enumerate(post.get("content_sections") or []):
        copied = copy.deepcopy(section)
        copied.setdefault("id", section.get("id") or f"section-{idx + 1}")
        formatted_sections.append(copied)
    memory_album = _build_memory_album(post, sections=formatted_sections)

    if post.get("id") and _bulletin_supabase_enabled():
        comment_threads = fetch_bulletin_comments_from_supabase(post["id"])
    else:
        comment_threads = post.get("comment_threads") or []
    _hydrate_comment_media_urls(comment_threads)
    post["comment_threads"] = comment_threads
    post["comment_count"] = _count_comment_nodes(comment_threads)

    liked_by_me = False
    if session.get("trainer") and post.get("id") and _bulletin_supabase_enabled():
        try:
            liked_existing = (supabase.table("bulletin_post_likes")
                              .select("trainer_username")
                              .eq("post_id", post["id"])
                              .eq("trainer_username", session["trainer"])
                              .limit(1)
                              .execute().data or [])
            liked_by_me = bool(liked_existing)
        except Exception as exc:
            print("⚠️ Supabase like lookup failed:", exc)
    post["liked_by_me"] = liked_by_me

    toc_sections = [sec for sec in formatted_sections if sec.get("title")]
    related_posts = [p for p in posts if p.get("slug") != post.get("slug")][:3]

    return render_template(
        "community_bulletin_post.html",
        post=post,
        sections=formatted_sections,
        toc_sections=toc_sections,
        related_posts=related_posts,
        trainer=session.get("trainer"),
        max_comment_depth=MAX_BULLETIN_COMMENT_DEPTH,
        is_pwa=session.get("is_pwa", False),
        memory_album=memory_album,
        memory_albums=[memory_album] if memory_album else [],
    )


def _bulletin_post_for_api(slug: str) -> tuple[Optional[dict], Optional[str]]:
    post = fetch_bulletin_post_from_supabase(slug, include_sections=False)
    if not post:
        return None, None
    return post, post.get("id")


@app.route("/api/bulletin/<slug>/like", methods=["POST"])
def api_bulletin_like(slug):
    if "trainer" not in session:
        return jsonify({"error": "Login required"}), 401
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Bulletin service unavailable"}), 503
    post, post_id = _bulletin_post_for_api(slug)
    if not post_id:
        return jsonify({"error": "Post not found"}), 404
    trainer = session["trainer"]
    liked = False
    like_count = post.get("like_count") or 0
    try:
        existing = (supabase.table("bulletin_post_likes")
                    .select("trainer_username")
                    .eq("post_id", post_id)
                    .eq("trainer_username", trainer)
                    .limit(1)
                    .execute().data or [])
        if existing:
            supabase.table("bulletin_post_likes").delete().eq("post_id", post_id).eq("trainer_username", trainer).execute()
            liked = False
        else:
            supabase.table("bulletin_post_likes").insert({
                "post_id": post_id,
                "trainer_username": trainer,
            }).execute()
            liked = True
        like_rows = (supabase.table("bulletin_post_likes")
                     .select("post_id")
                     .eq("post_id", post_id)
                     .execute().data or [])
        like_count = len(like_rows)
        supabase.table("bulletin_posts").update({"like_count": like_count}).eq("id", post_id).execute()
    except Exception as exc:
        print("⚠️ Supabase like toggle failed:", exc)
        return jsonify({"error": "Unable to toggle like"}), 500
    return jsonify({"liked": liked, "like_count": like_count})


@app.route("/api/bulletin/<slug>/comments", methods=["GET", "POST"])
def api_bulletin_comments(slug):
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Bulletin service unavailable"}), 503
    post, post_id = _bulletin_post_for_api(slug)
    if not post_id:
        return jsonify({"error": "Post not found"}), 404

    if request.method == "GET":
        comments = fetch_bulletin_comments_from_supabase(post_id)
        _hydrate_comment_media_urls(comments)
        return jsonify({"comments": comments, "count": _count_comment_nodes(comments)})

    if "trainer" not in session:
        return jsonify({"error": "Login required"}), 401
    trainer = session["trainer"]
    payload = request.get_json(force=True, silent=True) or {}
    body = (payload.get("body") or "").strip()
    parent_id = payload.get("parent_id")
    if not body:
        return jsonify({"error": "Comment cannot be empty"}), 400
    decision = CONTENT_FILTER.scan(body)
    if decision:
        return jsonify({
            "error": "Comment blocked by community filter.",
            "filter": decision.to_dict(),
        }), 422
    clean_body = bleach.clean(body, tags=[], attributes={}, strip=True)
    if not clean_body:
        return jsonify({"error": "Comment cannot be empty"}), 400
    parent_comment_row = None
    insert_payload = {
        "post_id": post_id,
        "trainer_username": trainer,
        "body": clean_body,
    }
    if parent_id:
        parent_depth = _bulletin_comment_depth(post_id, parent_id)
        if parent_depth is None:
            return jsonify({"error": "Parent comment not found"}), 400
        if parent_depth >= MAX_BULLETIN_COMMENT_DEPTH:
            return jsonify({"error": "Replies can only nest two levels deep"}), 400
        insert_payload["parent_comment_id"] = parent_id
        try:
            lookup = (supabase.table("bulletin_post_comments")
                      .select("id, post_id, trainer_username, body, created_at")
                      .eq("id", parent_id)
                      .limit(1)
                      .execute())
            parent_comment_row = (lookup.data or [None])[0]
        except Exception as exc:
            print("⚠️ Supabase parent comment lookup failed:", exc)
            return jsonify({"error": "Unable to post comment"}), 500
        if not parent_comment_row:
            return jsonify({"error": "Parent comment not found"}), 400
        if str(parent_comment_row.get("post_id")) != str(post_id):
            return jsonify({"error": "Parent comment mismatch"}), 400

    inserted_comment = None
    try:
        resp = supabase.table("bulletin_post_comments").insert(insert_payload).execute()
        inserted_comment = (resp.data or [None])[0]
    except Exception as exc:
        print("⚠️ Supabase comment insert failed:", exc)
        return jsonify({"error": "Unable to post comment"}), 500

    if parent_comment_row:
        reply_snapshot = inserted_comment or {
            "id": None,
            "body": clean_body,
            "trainer_username": trainer,
            "created_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        }
        try:
            _send_comment_reply_notification(post, slug, parent_comment_row, reply_snapshot, trainer)
        except Exception as exc:
            print("⚠️ Comment reply notification send failed:", exc)

    comments = fetch_bulletin_comments_from_supabase(post_id)
    _hydrate_comment_media_urls(comments)
    return jsonify({"comments": comments, "count": _count_comment_nodes(comments)})


@app.route("/api/trainers/<username>/profile", methods=["GET"])
def api_public_trainer_profile(username):
    username = (username or "").strip()
    if not username:
        return jsonify({"error": "Trainer username is required"}), 400
    profile = get_public_trainer_profile(username)
    if not profile:
        return jsonify({"error": "Trainer not found"}), 404
    return jsonify({"trainer": profile})


@app.route("/api/profile/bio", methods=["POST"])
def api_update_trainer_bio():
    if "trainer" not in session:
        return jsonify({"error": "Login required"}), 401
    if not supabase:
        return jsonify({"error": "Profile service unavailable"}), 503
    payload = request.get_json(force=True, silent=True) or {}
    bio = (payload.get("bio") or "").strip()
    if len(bio) > 200:
        bio = bio[:200]
    decision = CONTENT_FILTER.scan(bio)
    if decision:
        return jsonify({
            "error": "Trainer bio blocked by community filter.",
            "filter": decision.to_dict(),
        }), 422
    bio = bleach.clean(bio, tags=[], attributes={}, strip=True)
    username = session["trainer"]
    try:
        supabase.table("sheet1").update({"trainerbio": bio}).eq("trainer_username", username).execute()
    except Exception as exc:
        print("⚠️ Supabase trainer bio update failed:", exc)
        return jsonify({"error": "Unable to update trainer bio"}), 500
    profile = get_public_trainer_profile(username) or {"username": username, "bio": bio}
    return jsonify({"bio": bio, "trainer": profile})


@app.route("/api/profile/meta", methods=["POST"])
def api_update_profile_meta():
    if "trainer" not in session:
        return jsonify({"error": "Login required"}), 401
    if not supabase:
        return jsonify({"error": "Profile service unavailable"}), 503
    payload = request.get_json(force=True, silent=True) or {}
    updates = {}

    if "team" in payload:
        team_value = (payload.get("team") or "").strip().lower()
        if team_value and team_value not in TEAM_CONFIG:
            return jsonify({"error": "Invalid team selection"}), 400
        updates["team"] = team_value or None

    if "trainer_level" in payload or "level" in payload:
        level_value = payload.get("trainer_level", payload.get("level"))
        if level_value in (None, "", "null"):
            updates["tlevel"] = None
        else:
            try:
                level_int = int(level_value)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid trainer level"}), 400
            if not 1 <= level_int <= MAX_TRAINER_LEVEL:
                return jsonify({"error": f"Trainer level must be between 1 and {MAX_TRAINER_LEVEL}"}), 400
            updates["tlevel"] = level_int

    if not updates:
        return jsonify({"error": "No changes provided"}), 400

    username = session["trainer"]
    try:
        supabase.table("sheet1").update(updates).eq("trainer_username", username).execute()
    except Exception as exc:
        print("⚠️ Supabase trainer meta update failed:", exc)
        return jsonify({"error": "Unable to update profile"}), 500

    profile = get_public_trainer_profile(username)
    return jsonify({"trainer": profile})


@app.route("/api/profile/featured-stamps", methods=["GET", "POST"])
def api_featured_stamps():
    if "trainer" not in session:
        return jsonify({"error": "Login required"}), 401
    if not supabase:
        return jsonify({"error": "Profile service unavailable"}), 503
    _, record = find_user(session["trainer"])
    if not record:
        return jsonify({"error": "Trainer not found"}), 404
    trainer_username = record.get("trainer_username") or session["trainer"]
    campfire_username = record.get("campfire_username")

    if request.method == "GET":
        _, stamps, _ = get_passport_stamps(trainer_username, campfire_username)
        catalog: list[dict] = []
        seen_tokens: set[str] = set()
        for stamp in stamps:
            token = _featured_stamp_token(trainer_username, stamp)
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            catalog.append({
                "token": token,
                "icon": stamp.get("icon"),
                "label": stamp.get("name") or "Passport stamp",
                "awarded_at": stamp.get("awarded_at") or "",
                "awarded_at_iso": stamp.get("awarded_at_iso") or "",
            })
            if len(catalog) >= 60:
                break
        return jsonify({
            "stamps": catalog,
            "selected": _record_featured_stamps(record),
        })

    payload = request.get_json(force=True, silent=True) or {}
    tokens = payload.get("tokens") or []
    if not isinstance(tokens, list):
        return jsonify({"error": "Invalid payload"}), 400
    cleaned_tokens: list[str] = []
    for token in tokens:
        if not token:
            continue
        token_str = str(token)
        if token_str not in cleaned_tokens:
            cleaned_tokens.append(token_str)
        if len(cleaned_tokens) >= 3:
            break

    _, stamps, _ = get_passport_stamps(trainer_username, campfire_username)
    token_map = {}
    for stamp in stamps:
        token_map[_featured_stamp_token(trainer_username, stamp)] = stamp

    selected: list[dict] = []
    for token in cleaned_tokens:
        stamp = token_map.get(token)
        if not stamp:
            continue
        selected.append({
            "icon": stamp.get("icon") or "",
            "label": stamp.get("name") or "Passport stamp",
            "token": token,
        })
        if len(selected) >= 3:
            break

    try:
        _write_featured_stamps(trainer_username, selected)
    except Exception as exc:
        print("⚠️ Supabase featured stamp update failed:", exc)
        return jsonify({"error": "Unable to update featured stamps"}), 500

    profile = get_public_trainer_profile(trainer_username)
    return jsonify({"trainer": profile})


@app.route("/admin/community-bulletin", methods=["GET"])
@admin_required
def admin_bulletin():
    session["last_page"] = request.path
    if not _bulletin_supabase_enabled():
        flash("Community Bulletin data source unavailable. Check Supabase credentials.", "error")
        return render_template(
            "admin_bulletin.html",
            supabase_enabled=False,
        )

    posts = fetch_bulletin_posts_from_supabase(include_sections=True, status=None)
    authors = _bulletin_admin_fetch_authors()
    comments = _bulletin_admin_fetch_comments()
    stats = _bulletin_admin_stats(posts)

    return render_template(
        "admin_bulletin.html",
        supabase_enabled=True,
        posts=posts,
        authors=authors,
        latest_comments=comments,
        stats=stats,
    )


@app.route("/admin/community-bulletin/save", methods=["POST"])
@admin_required
def admin_bulletin_save():
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Bulletin service unavailable"}), 503
    payload = request.get_json(force=True, silent=True) or {}
    success, result = _bulletin_admin_upsert_post(payload)
    if not success:
        return jsonify({"error": result}), 400
    return jsonify({"post": result})


@app.route("/admin/community-bulletin/post/<post_id>/feature", methods=["POST"])
@admin_required
def admin_bulletin_toggle_feature_route(post_id):
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Bulletin service unavailable"}), 503
    payload = request.get_json(force=True, silent=True) or {}
    desired = payload.get("is_featured")
    value = bool(desired) and str(desired).lower() not in {"0", "false", "no"}
    if not _bulletin_admin_toggle_feature(post_id, value):
        return jsonify({"error": "Unable to update feature toggle"}), 400
    return jsonify({"is_featured": value})


@app.route("/admin/community-bulletin/post/<post_id>/delete", methods=["POST"])
@admin_required
def admin_bulletin_delete_post_route(post_id):
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Bulletin service unavailable"}), 503
    if not _bulletin_admin_delete_post(post_id):
        return jsonify({"error": "Unable to delete post"}), 400
    return jsonify({"deleted": True})


@app.route("/admin/community-bulletin/comment/<comment_id>/delete", methods=["POST"])
@admin_required
def admin_bulletin_delete_comment_route(comment_id):
    if not _bulletin_supabase_enabled():
        flash("Bulletin service unavailable.", "error")
        return redirect(url_for("admin_bulletin"))
    if _bulletin_admin_delete_comment(comment_id):
        flash("Comment removed.", "success")
    else:
        flash("Unable to remove comment right now.", "error")
    return redirect(url_for("admin_bulletin"))


@app.route("/admin/community-bulletin/authors", methods=["POST"])
@admin_required
def admin_bulletin_save_author_route():
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Bulletin service unavailable"}), 503
    payload = request.get_json(force=True, silent=True) or {}
    success, result = _bulletin_admin_upsert_author(payload)
    if not success:
        return jsonify({"error": result}), 400
    return jsonify({"author": result})


@app.route("/admin/community-bulletin/author/<author_id>/delete", methods=["POST"])
@admin_required
def admin_bulletin_delete_author_route(author_id):
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Bulletin service unavailable"}), 503
    if not _bulletin_admin_delete_author(author_id):
        return jsonify({"error": "Unable to delete author"}), 400
    return jsonify({"deleted": True})


def _bulletin_upload_folder(asset_type: str) -> str:
    asset_type = (asset_type or "").lower()
    mapping = {
        "hero": "community-bulletin/heroes",
        "header": "community-bulletin/heroes",
        "slide": "community-bulletin/slides",
        "album": "community-bulletin/slides",
        "author": "community-bulletin/authors",
        "media": "community-bulletin/attachments",
        "attachment": "community-bulletin/attachments",
    }
    return mapping.get(asset_type, "community-bulletin/attachments")


@app.route("/admin/community-bulletin/upload", methods=["POST"])
@admin_required
def admin_bulletin_upload_asset():
    if not _bulletin_supabase_enabled():
        return jsonify({"error": "Supabase unavailable"}), 503

    upload = request.files.get("asset")
    if not upload:
        return jsonify({"error": "No file uploaded"}), 400

    asset_type = request.args.get("type", "attachment")
    folder = _bulletin_upload_folder(asset_type)

    saved_url = _upload_to_supabase(upload, folder=folder, bucket="catalog")
    if not saved_url:
        error_text = getattr(g, "supabase_last_error", "Upload failed")
        return jsonify({"error": error_text}), 500

    return jsonify({"url": saved_url})

# ====== Logout ======
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))

# ====== Manage Account: Change PIN ======
@app.route("/change_pin", methods=["POST"])
def change_pin():
    if "trainer" not in session:
        return redirect(url_for("home"))

    old_pin = request.form["old_pin"]
    memorable = request.form["memorable"]
    new_pin = request.form["new_pin"]

    _, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    if not _pin_matches(user, old_pin):
        flash("Old PIN is incorrect.", "error")
        return redirect(url_for("dashboard"))

    stored_memorable = user.get("memorable_password") or user.get("Memorable Password")
    if stored_memorable != memorable:
        flash("Memorable password is incorrect.", "error")
        return redirect(url_for("dashboard"))

    trainer_username = user.get("trainer_username") or session["trainer"]
    if not supabase:
        flash("Supabase is unavailable. Please try again later.", "error")
        return redirect(url_for("dashboard"))

    try:
        new_hash = _pin_hash_value(trainer_username, new_pin)
        supabase.table("sheet1").update({
            "pin_hash": new_hash,
        }).eq("trainer_username", trainer_username).execute()
    except Exception as exc:
        print("⚠️ Supabase change_pin failed:", exc)
        flash("Unable to update PIN right now. Please try again soon.", "error")
        return redirect(url_for("dashboard"))

    flash("PIN updated successfully.", "success")
    return redirect(url_for("dashboard"))

# ====== Manage Account: Change Memorable Password ======
@app.route("/change_memorable", methods=["POST"])
def change_memorable():
    if "trainer" not in session:
        return redirect(url_for("home"))

    old_memorable = request.form["old_memorable"]
    new_memorable = request.form["new_memorable"]

    _, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    stored_memorable = user.get("memorable_password") or user.get("Memorable Password")
    if stored_memorable != old_memorable:
        flash("Old memorable password is incorrect.", "error")
        return redirect(url_for("dashboard"))

    trainer_username = user.get("trainer_username") or session["trainer"]
    if not supabase:
        flash("Supabase is unavailable. Please try again later.", "error")
        return redirect(url_for("dashboard"))

    try:
        supabase.table("sheet1").update({
            "memorable_password": new_memorable,
        }).eq("trainer_username", trainer_username).execute()
    except Exception as exc:
        print("⚠️ Supabase change_memorable failed:", exc)
        flash("Unable to update memorable password right now. Please try again soon.", "error")
        return redirect(url_for("dashboard"))

    flash("Memorable password updated successfully.", "success")
    return redirect(url_for("dashboard"))

# ====== Manage Account: Log Out Everywhere ======
@app.route("/logout_everywhere", methods=["POST"])
def logout_everywhere():
    if "trainer" not in session:
        return redirect(url_for("home"))

    session.clear()
    flash("You have been logged out everywhere.", "success")
    return redirect(url_for("home"))

# ====== Manage Account: Delete Account ======
@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "trainer" not in session:
        return redirect(url_for("home"))

    confirm_name = request.form["confirm_name"]
    _, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    if confirm_name.lower() != session["trainer"].lower():
        flash("Trainer name does not match. Account not deleted.", "error")
        return redirect(url_for("dashboard"))

    trainer_username = user.get("trainer_username") or session["trainer"]
    if not supabase:
        flash("Supabase is unavailable. Please try again later.", "error")
        return redirect(url_for("dashboard"))

    try:
        supabase.table("sheet1").delete().eq("trainer_username", trainer_username).execute()
    except Exception as exc:
        print("⚠️ Supabase delete_account failed:", exc)
        flash("Unable to delete your account right now. Please try again soon.", "error")
        return redirect(url_for("dashboard"))

    session.clear()
    flash("Your account has been permanently deleted.", "success")
    return redirect(url_for("home"))

# ====== Passport ======
@app.route("/passport")
def passport():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view your passport progress.", "warning")
        return redirect(url_for("home"))

    username = session["trainer"]

    # === Get user from Supabase ===
    _, user = find_user(username)
    if not user:
        flash("User not found!", "error")
        return redirect(url_for("home"))

    campfire_username = user.get("campfire_username", "")
    passport_theme_slug = normalize_passport_theme(user.get("passport_theme"))

    # === Passport Stamps ===
    total_awarded, stamps, most_recent_stamp = get_passport_stamps(username, campfire_username)
    total_stamp_events = len(stamps)
    # nav bar: live balance
    current_stamps = int(user.get("stamps", 0) or 0)
    # page display: show all stamps from ledger
    passports = [stamps[i:i + 12] for i in range(0, len(stamps), 12)]

    # === Lugia Summary (Supabase only, fetch event cover photos properly) ===
    lugia_summary = {
        "total_attended": 0,
        "first_attended_event": "",
        "first_event_date": "",
        "first_event_icon": url_for("static", filename="icons/tickstamp.png"),
        "most_recent_event": "",
        "most_recent_event_date": "",
        "most_recent_icon": url_for("static", filename="icons/tickstamp.png"),
    }

    try:
        row = supabase.table("lugia_summary").select("*").eq("trainer_username", username).limit(1).execute().data
        if not row and campfire_username:
            row = supabase.table("lugia_summary").select("*").eq("campfire_username", campfire_username).limit(1).execute().data

        if row:
            r = row[0]
            lugia_summary["total_attended"] = r.get("total_attended", 0)
            lugia_summary["first_attended_event"] = r.get("first_attended_event", "")
            lugia_summary["first_event_date"] = r.get("first_event_date", "")
            lugia_summary["most_recent_event"] = r.get("most_recent_event", "")
            lugia_summary["most_recent_event_date"] = r.get("most_recent_event_date", "")

            ev_rows = supabase.table("events").select("event_id, cover_photo_url").execute().data or []
            ev_map = {str(e.get("event_id", "")).strip().lower(): e.get("cover_photo_url") for e in ev_rows}

            feid = (r.get("first_event_id") or "").strip().lower()
            ficon = ev_map.get(feid) if feid else None
            if not ficon:
                ficon = cover_from_event_name(r.get("first_attended_event", ""))
            lugia_summary["first_event_icon"] = ficon or lugia_summary["first_event_icon"]

            meid = (r.get("most_recent_event_id") or "").strip().lower()
            micon = ev_map.get(meid) if meid else None
            if not micon:
                micon = cover_from_event_name(r.get("most_recent_event", ""))
            lugia_summary["most_recent_icon"] = micon or lugia_summary["most_recent_icon"]

    except Exception as e:
        print("⚠️ Error loading Lugia Summary:", e)

    classic_submissions = get_classic_submissions_for_trainer(username)

    return render_template(
        "passport.html",
        trainer=username,
        stamps=stamps,
        passports=passports,
        total_stamps=total_stamp_events,
        total_stamps_awarded=total_awarded,
        current_stamps=current_stamps,
        most_recent_stamp=most_recent_stamp,
        lugia_summary=lugia_summary,
        classic_submissions=classic_submissions,
        passport_theme=passport_theme_slug,
        passport_theme_data=get_passport_theme_data(passport_theme_slug),
        show_back=False,
    )

@app.route("/passport/classic-upload", methods=["POST"])
def passport_classic_upload():
    session["last_page"] = url_for("passport")
    if "trainer" not in session:
        flash("Please log in to submit classic passports.", "warning")
        return redirect(url_for("home"))

    if not supabase:
        flash("Supabase is unavailable. Please try again later.", "error")
        return redirect(url_for("passport"))

    declared_count_raw = request.form.get("classic_count", "").strip()
    photo = request.files.get("classic_photo")

    if not declared_count_raw or not declared_count_raw.isdigit():
        flash("Please enter how many classic stamps are on your paper passports.", "warning")
        return redirect(url_for("passport"))

    declared_count = int(declared_count_raw)
    if declared_count <= 0:
        flash("Stamp count must be at least 1.", "warning")
        return redirect(url_for("passport"))

    if not photo or not getattr(photo, "filename", ""):
        flash("Please choose a photo showing your classic passports.", "warning")
        return redirect(url_for("passport"))

    if not _is_allowed_image_file(photo.filename):
        allowed_types = ", ".join(sorted(ALLOWED_CLASSIC_IMAGE_EXTENSIONS))
        flash(f"Please upload an image file ({allowed_types}).", "warning")
        return redirect(url_for("passport"))

    photo_url = _upload_to_supabase(photo, folder="classic-passports")
    if not photo_url:
        flash("We couldn't upload your photo. Please try again later.", "error")
        return redirect(url_for("passport"))

    _, user = find_user(session["trainer"])
    if not user:
        flash("Trainer account not found. Please contact support.", "error")
        return redirect(url_for("passport"))

    payload = {
        "trainer_username": user.get("trainer_username"),
        "campfire_username": user.get("campfire_username"),
        "declared_count": declared_count,
        "photo_url": photo_url,
        "status": "PENDING",
        "awarded_count": 0,
        "admin_notes": "",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    try:
        supabase.table("classic_passport_submissions").insert(payload).execute()
        flash("Thanks! We'll review your classic passports shortly.", "success")
    except Exception as exc:
        print("⚠️ Failed to insert classic passport submission:", exc)
        flash("We couldn't save your submission. Please try again.", "error")

    return redirect(url_for("passport"))

# ====== Meet-up History ======
@app.route("/meetup_history")
def meetup_history():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view your meet-up history.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]
    _, user = find_user(trainer)
    campfire_username = user.get("campfire_username", "")
    passport_theme_slug = normalize_passport_theme(user.get("passport_theme"))

    sort_by = request.args.get("sort", "date_desc")

    meetups, total_attended = get_meetup_history(trainer, campfire_username)

    # Sorting options
    if sort_by == "date_asc":
        meetups.sort(key=lambda m: m["date"])
    elif sort_by == "title":
        meetups.sort(key=lambda m: m["title"].lower())
    else:  # newest first
        meetups.sort(key=lambda m: m["date"], reverse=True)

    return render_template(
        "meetup_history.html",
        meetups=meetups,
        total_attended=total_attended,
        sort_by=sort_by,
        passport_theme=passport_theme_slug,
        passport_theme_data=get_passport_theme_data(passport_theme_slug),
    )

# ====== Date Filtering ======
from datetime import datetime
@app.template_filter("to_date")
def to_date_filter(value):
    """Format ISO date/time strings like '2025-09-23T17:00:00+00:00' into '23 Sep 2025'."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return value  # fallback: show raw if parsing fails

# ========= Catalog helpers & routes =========

def _safe_list(v):
    """Return a list no matter how Supabase stored the tags column."""
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, str):
        parts = [p.strip() for p in v.split(",")]
        return [p for p in parts if p]
    return []

def _featured_slide_paths() -> list[str]:
    """Return sorted static-relative paths for featured carousel slides."""
    static_root = Path(app.static_folder or "static")
    candidate_dirs = [
        static_root / "catalog" / "featured",
        static_root / "featured",
    ]
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    seen: set[str] = set()
    slides: list[str] = []
    for directory in candidate_dirs:
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            rel_path = path.relative_to(static_root).as_posix()
            if rel_path in seen:
                continue
            seen.add(rel_path)
            slides.append(rel_path)
    return sorted(slides)


def _featured_slide_alt(filename: str) -> str:
    """Generate a readable alt text from the slide filename."""
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    text = stem.strip().title()
    return text or "Featured slide"

def _category_label_for(item):
    """Return the first matching category label used in your page filters."""
    tags = [t.lower() for t in _safe_list(item.get("tags"))]
    for label, keys in CATEGORY_KEYS.items():
        keys_lc = set([label.lower(), *keys])
        if any(t in keys_lc for t in tags):
            return label
    return "Accessories"  # default bucket for 'misc'

def _get_watchlist_ids(trainer: str) -> list[str]:
    """Fetch watchlist item IDs for this trainer (Supabase table `watchlist`), falling back to session."""
    ids: list[str] = []
    if USE_SUPABASE and supabase:
        try:
            rows = supabase.table("watchlist") \
                .select("catalog_item_id") \
                .eq("trainer_username", trainer) \
                .execute().data or []
            ids = [str(r.get("catalog_item_id")) for r in rows if r.get("catalog_item_id")]
        except Exception as e:
            print("⚠️ watchlist fetch failed; falling back to session:", e)
    if not ids:
        ids = _safe_list(session.get("watchlist"))
    return ids[:WATCHLIST_LIMIT]

# Watchlist configuration
WATCHLIST_LIMIT = 6

def _watchlist_add(trainer: str, item_id: str) -> None:
    """Add to watchlist both in Supabase (best effort) and session."""
    existing = [str(x) for x in _safe_list(session.get("watchlist")) if x][:WATCHLIST_LIMIT]
    if item_id in existing:
        return
    if len(existing) >= WATCHLIST_LIMIT:
        return

    # Session mirror (preserve append order)
    existing.append(item_id)
    session["watchlist"] = existing

    # Supabase
    if USE_SUPABASE and supabase:
        try:
            supabase.table("watchlist").insert({
                "trainer_username": trainer,
                "catalog_item_id": item_id,
                "created_at": datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e:
            # ignore "duplicate key" errors etc.
            print("⚠️ watchlist add failed:", e)

def _watchlist_remove(trainer: str, item_id: str) -> None:
    """Remove from watchlist in Supabase (best effort) and session."""
    # Session mirror
    remaining = [i for i in _safe_list(session.get("watchlist")) if str(i) != str(item_id)][:WATCHLIST_LIMIT]
    session["watchlist"] = remaining

    # Supabase
    if USE_SUPABASE and supabase:
        try:
            supabase.table("watchlist") \
                .delete() \
                .eq("trainer_username", trainer) \
                .eq("catalog_item_id", item_id) \
                .execute()
        except Exception as e:
            print("⚠️ watchlist remove failed:", e)

@app.route("/catalog")
def catalog():
    session["last_page"] = request.path
    """Revamped catalog page matching the wireframe."""
    # Pull active catalog items
    items = []
    if supabase:
        try:
            resp = (supabase.table("catalog_items")
                    .select("*")
                    .eq("active", True)
                    .order("created_at", desc=True)
                    .execute())
            items = resp.data or []
        except Exception as e:
            print("⚠️ catalog items fetch failed:", e)

    # Normalize fields used by the template
    for it in items:
        it["id"] = it.get("id")
        it["name"] = it.get("name") or "Untitled"
        it["description"] = it.get("description") or ""
        it["image_url"] = it.get("image_url") or url_for("static", filename="icons/catalog-app.png")
        it["cost_stamps"] = int(it.get("cost_stamps") or 0)
        it["stock"] = int(it.get("stock") or 0)
        it["tags"] = _safe_list(it.get("tags"))
        it["_cat"] = _category_label_for(it)
        it["_created"] = it.get("created_at") or it.get("updated_at") or datetime.utcnow().isoformat()

    # Featured carousel slides from static folder
    slide_paths = _featured_slide_paths()
    featured_slides = []
    for rel_path in slide_paths:
        name = Path(rel_path).name
        featured_slides.append(
            {
                "url": url_for("static", filename=rel_path),
                "alt": _featured_slide_alt(name),
            }
        )

    # Build categories → items map (reusing your constants)
    categories = {label: [] for label in CATEGORY_ORDER}
    for it in items:
        categories.setdefault(it["_cat"], []).append(it)

    # Watchlist state (badge + modal)
    trainer = session.get("trainer")
    watch_ids = _get_watchlist_ids(trainer) if trainer else []

    # Simple page description for the hero
    catalog_description = "Short description goes here"

    context = {
        "featured_slides": featured_slides,
        "items": items,
        "categories": categories,
        "category_order": CATEGORY_ORDER,
        "watch_ids": watch_ids,
        "catalog_description": catalog_description,
        "show_back": False,
        "watchlist_limit": WATCHLIST_LIMIT,
    }

    return render_template("catalog.html", **context)

# ========= Watchlist & Orders API=========

@app.post("/watchlist/toggle/<item_id>")
def watchlist_toggle(item_id):
    if "trainer" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 403
    trainer = session["trainer"]

    current = _get_watchlist_ids(trainer)
    if item_id in current:
        _watchlist_remove(trainer, item_id)
        remaining = len(current) - 1
        return jsonify({
            "success": True,
            "watched": False,
            "count": max(remaining, 0),
            "limit": WATCHLIST_LIMIT,
        })
    else:
        if len(current) >= WATCHLIST_LIMIT:
            return jsonify({
                "success": False,
                "error": f"Watchlist is limited to {WATCHLIST_LIMIT} items.",
                "count": len(current),
                "limit": WATCHLIST_LIMIT,
            })
        _watchlist_add(trainer, item_id)
        return jsonify({
            "success": True,
            "watched": True,
            "count": len(current) + 1,
            "limit": WATCHLIST_LIMIT,
        })

@app.get("/watchlist")
def watchlist_data():
    if "trainer" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 403
    trainer = session["trainer"]
    ids = _get_watchlist_ids(trainer)
    rows = []
    if ids and supabase:
        try:
            # fetch items in one go
            rows = supabase.table("catalog_items") \
                .select("id,name,image_url,cost_stamps,stock,tags") \
                .in_("id", ids) \
                .execute().data or []
        except Exception as e:
            print("⚠️ watchlist items fetch failed:", e)

    # Keep order by most recently added (session order as fallback)
    id_pos = {i: p for p, i in enumerate(ids)}
    rows.sort(key=lambda r: id_pos.get(r.get("id"), 10**9))
    rows = rows[:WATCHLIST_LIMIT]

    return jsonify({
        "success": True,
        "count": len(rows),
        "limit": WATCHLIST_LIMIT,
        "items": [{
            "id": r.get("id"),
            "name": r.get("name"),
            "image_url": r.get("image_url") or url_for("static", filename="icons/catalog-app.png"),
            "cost_stamps": int(r.get("cost_stamps") or 0),
            "stock": int(r.get("stock") or 0),
            "tags": _safe_list(r.get("tags"))
        } for r in rows]
    })

@app.route("/orders")
def orders():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view your order history.", "warning")
        return redirect(url_for("home"))
    trainer = session["trainer"]

    redemptions = []
    if supabase:
        try:
            r = (supabase.table("redemptions")
                 .select("*")
                 .eq("trainer_username", trainer)
                 .order("created_at", desc=True)
                 .execute())
            redemptions = r.data or []
        except Exception as e:
            print("⚠️ orders: fetch failed:", e)

    return render_template("orders.html", redemptions=redemptions, show_back=True)

# ====== Catalog Items (User) ======
from flask import abort

# CATEGORY_KEYS control what tags put what items in what categories
CATEGORY_KEYS = {
    "Pins": ["pins", "pin"],
    "Plushies": ["plush", "plushie", "plushies"],
    "TCG": ["tcg", "cards", "booster"],
    "Keychains": ["keychain", "keychains", "keyring", "keyrings"],
    "Accessories": ["accessory", "accessories", "sticker", "badge", "apparel", "cap", "hat"],
    "Games": ["game", "games"],
    "Master Bundles": ["bundle", "bundles", "master bundle"],
}
CATEGORY_ORDER = list(CATEGORY_KEYS.keys())

def _item_matches_category(item_tags, category_label):
    """Case-insensitive tag/category matching."""
    tags = [t.lower() for t in (item_tags or [])]
    keys = set([category_label.lower(), *CATEGORY_KEYS.get(category_label, [])])
    return any(t in keys for t in tags)

def _pick_featured_item(items):
    """Prefer item with 'featured' tag; else highest stock; else newest."""
    if not items:
        return None
    # 1) tag 'featured'
    for it in items:
        if any((t or "").lower() == "featured" for t in (it.get("tags") or [])):
            return it
    # 2) highest stock
    items_sorted = sorted(items, key=lambda i: int(i.get("stock") or 0), reverse=True)
    if items_sorted:
        return items_sorted[0]
    # 3) newest
    return items[0]

@app.route("/catalog/item/<item_id>", endpoint="catalog_item")
def catalog_item(item_id):
    if not supabase:
        abort(404)
    try:
        r = (
            supabase.table("catalog_items")
            .select("*")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )
        if not r.data:
            abort(404)
        it = r.data[0]
        it["cost_stamps"] = int(it.get("cost_stamps") or 0)
        it["stock"] = int(it.get("stock") or 0)
        return render_template("catalog_item.html", item=it, show_back=False)
    except Exception as e:
        print("⚠️ catalog_item failed:", e)
        abort(500)

@app.route("/catalog/redeem/<item_id>", methods=["GET", "POST"])
def catalog_redeem(item_id):
    if "trainer" not in session:
        flash("Please log in to redeem.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]

    # Load user + balance
    _, user = find_user(trainer)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("catalog"))
    balance = int(user.get("stamps") or 0)

    # Load item (must be active and in stock)
    try:
        r = (
            supabase.table("catalog_items")
            .select("*")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )
        if not r.data:
            flash("Item not found.", "error")
            return redirect(url_for("catalog"))
        item = r.data[0]
        item["cost_stamps"] = int(item.get("cost_stamps") or 0)
        item["stock"] = int(item.get("stock") or 0)
    except Exception as e:
        print("⚠️ redeem: fetch item failed:", e)
        flash("Could not load item.", "error")
        return redirect(url_for("catalog"))

    if not item.get("active", False):
        flash("This prize is offline right now.", "warning")
        return redirect(url_for("catalog_item", item_id=item_id))
    if item["stock"] <= 0:
        flash("This prize is out of stock.", "warning")
        return redirect(url_for("catalog_item", item_id=item_id))

    # Meetups (active + upcoming)
    meetups = []
    try:
        today_iso = date.today().isoformat()
        m = (
            supabase.table("meetups")
            .select("*")
            .eq("active", True)
            .gte("date", today_iso)
            .order("date", desc=False)
            .order("start_time", desc=False)
            .execute()
        )
        meetups = m.data or []
    except Exception as e:
        print("⚠️ redeem: fetch meetups failed:", e)

    if request.method == "GET":
        return render_template(
            "catalog_redeem.html",
            item=item,
            balance=balance,
            meetups=meetups,
            show_back=False,
        )

    # ================================
    # SERVER-SIDE DOUBLE-CLICK LOCK
    # ================================
    last_redeem = session.get("last_redeem_time", 0)
    now = time.time()
    if now - last_redeem < 3:
        print("⚠️ Double redeem prevented for trainer:", trainer)
        flash("Slow down! That redemption was already processed.", "info")
        return redirect(url_for("catalog_item", item_id=item_id))
    session["last_redeem_time"] = now

    # POST — place order
    meetup_id = request.form.get("meetup_id")
    confirm = request.form.get("confirm") == "yes"

    if not meetup_id:
        flash("Please choose a meet-up.", "warning")
        return redirect(url_for("catalog_redeem", item_id=item_id))
    if not confirm:
        flash("Please confirm your order.", "warning")
        return redirect(url_for("catalog_redeem", item_id=item_id))
    if balance < item["cost_stamps"]:
        flash("You don't have enough stamps.", "error")
        return redirect(url_for("catalog_item", item_id=item_id))

    # Load meetup snapshot
    try:
        mr = (
            supabase.table("meetups")
            .select("*")
            .eq("id", meetup_id)
            .limit(1)
            .execute()
        )
        if not mr.data:
            flash("Meet-up not found.", "error")
            return redirect(url_for("catalog_redeem", item_id=item_id))
        meetup = mr.data[0]
    except Exception as e:
        print("⚠️ redeem: fetch meetup failed:", e)
        flash("Could not load meet-up.", "error")
        return redirect(url_for("catalog_redeem", item_id=item_id))

    # Double-check stock & activity just before we commit
    try:
        r2 = (
            supabase.table("catalog_items")
            .select("stock, active")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )
        latest = r2.data[0]
        latest_stock = int(latest.get("stock") or 0)
        if not latest.get("active", False) or latest_stock <= 0:
            flash("This prize just went out of stock or offline.", "warning")
            return redirect(url_for("catalog"))
    except Exception as e:
        print("⚠️ redeem: recheck failed:", e)
        latest_stock = int(item.get("stock") or 0)

    # Deduct stamps via Lugia (ledger)
    cost = item["cost_stamps"]
    reason = f"Catalog Redemption: {item.get('name')}"
    ok, lugia_msg = adjust_stamps(trainer, cost, reason, "remove")
    if not ok:
        flash("Could not deduct stamps. Try again in a moment.", "error")
        return redirect(url_for("catalog_redeem", item_id=item_id))

    # Update balance mirror (best effort) now that stock is confirmed
    try:
        new_balance = max(0, balance - cost)
        supabase.table("sheet1").update({"stamps": new_balance}).eq("trainer_username", trainer).execute()
    except Exception as e:
        print("⚠️ redeem: mirror stamp update failed:", e)

    # Create redemption record
    red_id = str(uuid.uuid4())
    item_snapshot = {
        "name": item.get("name"),
        "cost_stamps": item.get("cost_stamps"),
        "image_url": item.get("image_url"),
        "tags": item.get("tags") or [],
        "description": item.get("description") or "",
    }
    metadata = {
        "meetup": {
            "id": meetup.get("id"),
            "name": meetup.get("name"),
            "location": meetup.get("location"),
            "date": meetup.get("date"),
            "start_time": meetup.get("start_time"),
        }
    }
    try:
        supabase.table("redemptions").insert({
            "id": red_id,
            "trainer_username": trainer,
            "catalog_item_id": item_id,
            "meetup_id": meetup_id,
            "status": "PENDING",
            "stamps_spent": cost,
            "item_snapshot": item_snapshot,
            "metadata": metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print("⚠️ redeem: create redemption failed:", e)
        flash("Your order couldn't be created. Stamps were deducted, contact admin.", "error")
        return redirect(url_for("catalog"))

    # Send inbox message with receipt link (best effort)
    try:
        receipt_url = absolute_url(url_for("catalog_receipt", redemption_id=red_id))
        subj = f"Order received: {item_snapshot['name']}"
        msg = (
            f"Hey {trainer},\n\n"
            f"Thanks for your order! We’ve put aside **{item_snapshot['name']}**.\n"
            f"Pick-up at: {metadata['meetup']['name']} — {metadata['meetup']['location']} "
            f"on {metadata['meetup']['date']} at {metadata['meetup']['start_time']}.\n\n"
            f"Receipt: {receipt_url}\n"
            f"Status: PENDING"
        )
        send_notification(
            trainer,
            subj,
            msg,
            notif_type="prize",
            metadata={"url": receipt_url, "redemption_id": red_id},
        )
    except Exception as e:
        print("⚠️ redeem: inbox notify failed:", e)

    return redirect(url_for("catalog_receipt", redemption_id=red_id))

@app.route("/catalog/receipt/<redemption_id>")
def catalog_receipt(redemption_id):
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]

    try:
        r = (
            supabase.table("redemptions")
            .select("*")
            .eq("id", redemption_id)
            .limit(1)
            .execute()
        )
        if not r.data:
            abort(404)
        rec = r.data[0]
        if (rec.get("trainer_username") or "").lower() != trainer.lower():
            abort(403)
    except Exception as e:
        print("⚠️ receipt: fetch failed:", e)
        abort(500)

    return render_template("catalog_receipt.html", rec=rec, show_back=False)

# ====== OCR test (debug) ======
@app.route("/ocr_test", methods=["GET", "POST"])
def ocr_test():
    if request.method == "POST":
        file = request.files.get("screenshot")
        if not file:
            flash("Please upload a screenshot.", "error")
            return redirect(url_for("ocr_test"))

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(file.filename))
        file.save(filepath)
        try:
            img = Image.open(filepath)
            w, h = img.size
            top, bottom = int(h * 0.15), int(h * 0.25)
            left, right = int(w * 0.05), int(w * 0.90)
            cropped = img.crop((left, top, right, bottom))
            text = pytesseract.image_to_string(cropped)

            buf = io.BytesIO()
            cropped.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            return f"""
                <h2>OCR Test Result</h2>
                <p><b>Detected Text:</b> {text}</p>
                <h3>Cropped Region:</h3>
                <img src="data:image/png;base64,{b64}" style="max-width:100%;border:1px solid #ccc;" />
                <p><a href="/ocr_test">Try another</a></p>
            """
        finally:
            os.remove(filepath)

    return """
        <h2>OCR Test</h2>
        <form method="post" enctype="multipart/form-data">
            <p>Upload Trainer Screenshot:</p>
            <input type="file" name="screenshot" accept="image/*" required>
            <button type="submit">Run OCR</button>
        </form>
    """

# ====== Change Avatar / Background ======
@app.route("/change_avatar", methods=["GET", "POST"])
def change_avatar():
    session["last_page"] = request.path
    if "trainer" not in session:
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    current_avatar = user.get("avatar_icon", "avatar1.png")
    current_background = user.get("trainer_card_background") or "default.png"
    current_passport_theme = normalize_passport_theme(user.get("passport_theme"))
    current_passport_theme_data = get_passport_theme_data(current_passport_theme)

    avatars = [f"avatar{i}.png" for i in range(1, 20)]
    backgrounds_folder = os.path.join(app.root_path, "static", "backgrounds")
    try:
        backgrounds = sorted([
            name for name in os.listdir(backgrounds_folder)
            if not name.startswith(".") and os.path.isfile(os.path.join(backgrounds_folder, name))
        ])
    except FileNotFoundError:
        backgrounds = []

    if current_background and current_background not in backgrounds:
        backgrounds.append(current_background)

    if request.method == "POST":
        avatar_choice = request.form.get("avatar_choice") or current_avatar
        background_choice = request.form.get("background_choice") or current_background
        passport_theme_choice = request.form.get("passport_theme_choice") or current_passport_theme

        valid_avatars = set(avatars)
        if avatar_choice not in valid_avatars:
            message = "Invalid avatar choice."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": message}), 400
            flash(message, "error")
            return redirect(url_for("change_avatar"))

        valid_backgrounds = set(backgrounds)
        if background_choice not in valid_backgrounds:
            message = "Invalid background choice."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": message}), 400
            flash(message, "error")
            return redirect(url_for("change_avatar"))

        selected_passport_theme = normalize_passport_theme(passport_theme_choice)

        if not supabase:
            flash("Supabase is unavailable. Please try again later.", "error")
            return redirect(url_for("change_avatar"))

        try:
            supabase.table("sheet1") \
                .update({
                    "avatar_icon": avatar_choice,
                    "trainer_card_background": background_choice,
                    "passport_theme": selected_passport_theme,
                }) \
                .eq("trainer_username", session["trainer"]) \
                .execute()
        except Exception as e:
            print("⚠️ Failed updating Supabase avatar/background:", e)
            flash("Unable to update appearance right now. Please try again soon.", "error")
            return redirect(url_for("change_avatar"))

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": True,
                "avatar": avatar_choice,
                "background": background_choice,
                "passport_theme": selected_passport_theme,
                "passport_theme_label": get_passport_theme_data(selected_passport_theme)["label"],
            })

        flash("✅ Appearance updated successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "change_avatar.html",
        avatars=avatars,
        backgrounds=backgrounds,
        current_avatar=current_avatar,
        current_background=current_background,
        passport_themes=PASSPORT_THEMES,
        current_passport_theme=current_passport_theme,
        current_passport_theme_data=current_passport_theme_data,
    )

@app.context_processor
def inject_current_avatar():
    if "trainer" in session:
        _, user = find_user(session["trainer"])
        if user:
            return {"current_avatar": user.get("avatar_icon", "avatar1.png")}
    return {"current_avatar": "avatar1.png"}

# ====== Stamp Processor ======
@app.context_processor
def inject_nav_data():
    if "trainer" in session:
        _, user = find_user(session["trainer"])
        if user:
            return {"current_stamps": user.get("stamps", 0)}
    return {"current_stamps": 0}

# ====== Global Inbox Preview =====
@app.context_processor
def inject_inbox_preview():
    trainer = session.get("trainer")
    if trainer:
        data = get_inbox_preview(trainer)
        return {
            "inbox_preview": data["preview"],
            "inbox_unread": data["unread_count"]
        }
    return {"inbox_preview": [], "inbox_unread": 0}

# ====== Expose account_type globally ======
@app.context_processor
def inject_account_type():
    if "trainer" in session:
        _, user = find_user(session["trainer"])
        if user:
            return {"account_type": normalize_account_type(user.get("account_type"))}
    return {"account_type": "Guest"}

# ====== Entrypoint ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
