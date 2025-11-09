import os
import copy
import hashlib
import json
import requests
import uuid
import re
from collections import Counter, defaultdict
from flask import Flask, render_template, abort, request, redirect, url_for, session, flash, send_from_directory, jsonify, g, make_response
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
from typing import Any

from geocache import geocache_bp, services
from geocache.services import REQUIRED_FLAGS_BY_ACT
from rdab.trainer_detection import extract_trainer_name

# ====== Feature toggle ======
USE_SUPABASE = True  # ✅ Supabase for stamps/meetups
MAINTENANCE_MODE = False  # ⛔️ Change to True to enable maintenance mode
USE_GEOCACHE_QUEST = os.getenv("USE_GEOCACHE_QUEST", "1").strip().lower() in {"1", "true", "yes", "on"}

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

# ====== GOWA secret event toggle ======
GOWA_ENABLED = True  # 🌿 Flip to True to unlock the Doncaster GO Wild Area experience
GOWA_STATIC_PREFIX = "gowa"
GOWA_BANNER_ASSET = f"{GOWA_STATIC_PREFIX}/banner.png"
GOWA_LOGO_ASSET = f"{GOWA_STATIC_PREFIX}/logo.png"
GOWA_EXTERNAL_URL = "https://rdab.app/gowa"

# ====== Live events hub ======
LIVE_EVENTS_ENABLED = True  # Toggle to enable the live events microsite
LIVE_EVENTS_EXTERNAL_URL = "https://rdab.app/live-events"

# ====== Dashboard feature visibility toggles ======
SHOW_CATALOG_APP = True
SHOW_CITY_PERKS_APP = False
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
app.register_blueprint(geocache_bp)
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
        "live_events_page",
        "gowa_portal",
        "gowa_alias_redirect",
        "gowa_legacy_redirect",
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

DATA_DIR = Path(app.root_path) / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CUSTOM_EVENTS_PATH = DATA_DIR / "custom_events.json"
GOWA_CONTENT_PATH = DATA_DIR / "gowa_page.json"
LIVE_EVENTS_CONTENT_PATH = DATA_DIR / "live_events_page.json"
GEOCACHE_STORY_PATH = DATA_DIR / "geocache_story.json"
GEOCACHE_ASSETS_PATH = DATA_DIR / "geocache_assets.json"
app.config.setdefault("GEOCACHE_STORY_PATH", GEOCACHE_STORY_PATH)
app.config.setdefault("GEOCACHE_ASSETS_PATH", GEOCACHE_ASSETS_PATH)
GOWA_DEFAULT_CONTENT = {
    "title": "Doncaster GO Wild Area 2025",
    "description": (
        "Whispered among the trees of the wild area, a new chapter awaits brave Trainers. "
        "Follow the shimmering lights and discover encounters spun from folklore and stardust."
    ),
    "logo_alt": "GO Wild Area emblem",
    "meta": {
        "canonical": GOWA_EXTERNAL_URL,
    },
    "buttons": [
        {
            "label": "back to RDAB",
            "endpoint": "home",
            "variant": "outline",
        },
        {
            "label": "View details on Pokemon GO",
            "href": "https://pokemongolive.com/events",
            "variant": "primary",
            "new_tab": True,
        },
    ],
    "social": {
        "label": "Stay Connected",
        "links": [
            {
                "label": "RDAB Instagram",
                "href": "https://www.instagram.com/raidingdoncaster",
                "new_tab": True,
            },
            {
                "label": "RDAB TikTok",
                "href": "https://www.tiktok.com/@raidingdoncaster",
                "new_tab": True,
            },
        ],
    },
    "events": {
        "heading": "2 Days. 1 Wild Adventure.",
        "body": (
            "Gather your friends, your team, and your courage. The Wild Area calls! "
            "From a local meet-up to a grand community celebration, every moment counts."
        ),
        "cards": [
            {
                "title": "GO Wild Area: Doncaster Meet-up",
                "date": "15 November · 10:00 – 18:00",
                "location": "Elmfield Park, Doncaster",
                "logo": None,
                "logo_alt": "GO Wild Area: Doncaster Meet-up logo",
                "button": {
                    "label": "RSVP",
                    "href": "",
                    "variant": "primary",
                    "new_tab": True,
                },
            },
            {
                "title": "Pokémon GO: Wild Area Community Celebration – Doncaster",
                "date": "16 November · 10:00 – 18:00",
                "location": "Doncaster Market",
                "logo": None,
                "logo_alt": "Wild Area Community Celebration logo",
                "button": {
                    "label": "Find out more",
                    "href": "#wild-area-details",
                    "variant": "outline",
                    "new_tab": False,
                },
            },
        ],
    },
    "narrative": {
        "heading": "Wait… an Official Pokémon GO Event?!",
        "lede": (
            "Yes, Niantic are delivering an in-person Community Celebration event — "
            "right here in Doncaster."
        ),
        "paragraphs": [
            "Raiding Doncaster and Beyond and Community Ambassador teams from across the north of England have teamed up to help deliver Europe's first ever official Pokémon GO: Community Celebration event.",
            "This is where community passion meets official gameplay. Expect a true in-person experience both on the ground in the City of Doncaster and in-game. From official route tours and raid trains, to geocache adventures and competitive challenges, to live activities and celebrations across the city.",
            "On Sunday, 16th November, the Wild Area comes alive at Doncaster Market and Corn Exchange from 10am till 6pm.",
            "This is not just an event. It's a milestone — where the communities of the North unite to deliver something truly world-class. Join us. Be a part of Pokémon GO history.",
        ],
        "breaker": {
            "image": "gowa/section-breaker.png",
            "alt": "Golden sparkle divider",
        },
        "button": {
            "label": "Learn More about Pokémon GO: Wild Area 2025",
            "href": "",
            "variant": "primary",
            "new_tab": True,
        },
    },
    "expectations": {
        "heading": "What to Expect?",
        "quote": "“The Wild Awakens”",
        "disclaimer": (
            "The Wild Awakens is a community-run creative experience designed, developed and organised by Community Ambassadors. "
            "This experience is not sponsored by or affiliated with Niantic, Scopely, The Pokémon Company, Game Freak or The Pokémon Company International."
        ),
        "paragraphs": [
            "The ancient Fairy Council has stirred from their slumber, and a mysterious energy is spreading across Doncaster.",
            "In its wake rises a protest movement calling themselves Team NO Wild — a group claiming to “protect” nature by driving Trainers from the city. But their motives may not be as pure as they seem.",
            "This original storyline has been developed by the Raiding Doncaster and Beyond Community Ambassadors as an independent, fan-made experience, created to celebrate the spirit of exploration and community that Pokémon GO inspires.",
            "On 16 November, during the Doncaster Community Celebrations, Trainers can take part in activities inspired by this story in live in-person activities exclusive to the day.",
            "Will you side with the Fairy Council, restoring balance to the Wild? Or align with Team NO Wild, seeking to control its growing power?",
        ],
    },
    "factions": {
        "heading": "Choose Your Side",
        "intro": "On November 16th, your choices will shape the fate of Doncaster’s wilds.",
        "cards": [
            {
                "title": "The Fairy Council",
                "body": "Guardians of harmony and ancient nature magic. The Fairy Council seeks to calm the rising chaos and restore balance.",
            },
            {
                "title": "Team NO Wild",
                "body": "A radical movement claiming to protect the “Natural Order” by driving Trainers out of the city. But their true motives remain hidden in the shadows.",
            },
        ],
    },
    "activities": {
        "heading": "Core Activities",
        "disclaimer": (
            "The Wild Awakens is a community-run creative experience designed, developed and organised by Community Ambassadors. "
            "This experience is not sponsored by or affiliated with Niantic, Scopely, The Pokémon Company, Game Freak or The Pokémon Company International."
        ),
        "intro": "These four feature events run throughout the day led by your Community Ambassadors, and shape the fate of Doncaster’s wilds.",
        "cards": [
            {
                "title": "Join a City Route Tour",
                "body": "Embark on Route Tours led by local guides! Explore Doncaster’s landmarks, tackle raids, battles, and uncover Wild secrets.",
                "image": "gowa/activity-route-tour.png",
                "image_alt": "Community Ambassadors leading a city route tour",
            },
            {
                "title": "Digital Geocache Quest",
                "body": "Join the Fairy Council in a secret investigation to uncover Team NO Wild’s true plans. Complete puzzles and uncover hidden clues to gain power, then use your power for good — or bad!",
                "image": "gowa/activity-geocache.png",
                "image_alt": "Digital geocache quest illustration",
            },
            {
                "title": "Take on Team NO Wild",
                "body": "Team NO Wild Grunts are scattered across the city. Find them, battle them, and earn your stamps — or… help them spread their message.",
                "image": "gowa/activity-team-nowild.png",
                "image_alt": "Team NO Wild battle scene",
            },
            {
                "title": "GOWA Doncaster Community Day League (10AM–5PM)",
                "body": "Battle alongside your chosen faction! Every match helps your side control more of the city. Compete, climb the ranks, and prove your strength.",
                "image": "gowa/activity-league.png",
                "image_alt": "Competitive Pokémon GO league battles",
            },
        ],
    },
    "agenda": {
        "heading": "Community Celebration: Doncaster Agenda",
        "note": "Route tours will be available throughout the day. Timings will be shared closer to the event.",
        "events": [
            {
                "time": "10:00",
                "label": "Community Celebration Opening Ceremony @ Doncaster Corn Exchange",
            },
            {
                "time": "13:00 – 14:00",
                "label": "Trading Post @ Doncaster Markets",
            },
            {
                "time": "17:50",
                "label": "Community Celebration Closing Ceremony @ Doncaster Corn Exchange",
            },
            {
                "time": "18:00 onwards",
                "label": "Social Events @ The Market Square — \"Get ready to throw it down! There ain’t no party like an RDAB party!!!\"",
            },
        ],
    },
    "footer": {
        "heading": "Pokémon GO: Community Celebration – Doncaster is brought to you by Community Ambassadors from the following communities",
        "banner": {
            "image": "gowa/footer-banner.png",
            "alt": "Community Ambassador collective banner",
        },
        "disclaimer": (
            "The Wild Awakens storyline and community programming are independently created by Community Ambassadors. "
            "They are not sponsored by or affiliated with Niantic, Scopely, The Pokémon Company, Game Freak, or The Pokémon Company International."
        ),
    },
}

LIVE_EVENTS_DEFAULT_CONTENT = {
    "meta": {
        "title": "Community Celebrations Live",
        "description": (
            "Your colourful hub for live events, story moments, and community surprises across Doncaster."
        ),
        "canonical": LIVE_EVENTS_EXTERNAL_URL,
    },
    "navigation": [
        {"view": "agenda", "label": "Agenda", "icon": "📅"},
        {"view": "activities", "label": "Activities", "icon": "🎯"},
        {"view": "map", "label": "Map", "icon": "🗺️"},
        {"view": "more", "label": "More", "icon": "✨"},
    ],
    "agenda": {
        "banner": {
            "image": "gowa/banner.png",
            "alt": "Community celebrations banner with pastel confetti",
        },
        "welcome": {
            "eyebrow": "Welcome to Doncaster",
            "title": "Community Celebrations Hub",
            "subtitle": "Drop into the hub, plan your day, and discover the key moments we have lined up.",
            "image": "gowa/communitycel.png",
            "alt": "Trainers celebrating together in Doncaster",
        },
        "highlights_heading": "Agenda highlights",
        "cards": [
            {
                "image": "gowa/activity-route-tour.png",
                "time": "10:00 AM",
                "title": "Event hub opens",
                "body": "Collect your wristband, grab a welcome drink, and meet the celebrations team.",
            },
            {
                "image": "gowa/activity-geocache.png",
                "time": "1:00 – 2:00 PM",
                "title": "Trading Post & market",
                "body": "Swap stickers, pins, and postcards while catching featured spawns around the market.",
            },
            {
                "image": "gowa/activity-league.png",
                "time": "5:50 PM",
                "title": "Closing ceremony",
                "body": "Gather by the main stage for the big wrap-up, shout-outs, and prize reveals.",
            },
            {
                "image": "gowa/footer-banner.png",
                "time": "6:00 PM →",
                "title": "After-hours social",
                "body": "RSVP to keep the celebrations going with food, music, and late-night vibes.",
                "cta": {
                    "label": "Social RSVP",
                    "href": "#",
                    "new_tab": False,
                },
            },
        ],
        "route_tours": {
            "heading": "Route tours",
            "description": (
                "Join guided walks that uncover Doncaster lore, raid hotspots, and hidden challenges. "
                "Arrive five minutes early at the welcome arch to secure your spot."
            ),
            "times_left": ["10:30 AM", "11:00 AM", "11:30 AM", "12:00 PM"],
            "times_right": ["1:00 PM", "2:00 PM", "3:00 PM", "4:00 PM"],
        },
    },
    "activities": {
        "banner": {
            "image": "gowa/section-breaker.png",
            "alt": "Pastel ribbon divider",
        },
        "story": {
            "eyebrow": "The Wild Awakens",
            "title": "Story brief",
            "body": (
                "A mysterious energy is rippling through Doncaster. The Fairy Council needs allies, but "
                "Team NO Wild have their own plans."
            ),
            "image": "gowa/activity-team-nowild.png",
            "alt": "Illustration of Team NO Wild characters",
        },
        "mission": {
            "title": "Your mission",
            "body": (
                "Choose your path, complete featured activities, and collect stamps to unlock celebratory rewards."
            ),
        },
        "prizes_intro": "You can earn up to two prizes today.",
        "rewards": [
            {
                "title": "Earn 4 stamps",
                "image": "gowa/activity-league.png",
                "description": "Play through the headline activities to claim an exclusive celebrations patch.",
                "info": "Stamp cards available from the welcome desk.",
            },
            {
                "title": "Complete the geocache",
                "image": "gowa/activity-geocache.png",
                "description": "Crack the riddles around town to unlock the holographic postcard set.",
                "info": "Check in with the Fairy Council booth for your first clue.",
            },
        ],
        "prizes_showcase": {
            "heading": "Prizes include…",
            "image": "gowa/logo.png",
            "alt": "Community celebrations prize bundle",
            "caption": "Collect prizes at the Community Desk beside the Corn Exchange entrance.",
        },
        "cta_grid": {
            "heading": "Get started",
            "tiles": [
                {
                    "image": "gowa/activity-route-tour.png",
                    "label": "City tours",
                    "href": "#",
                },
                {
                    "image": "gowa/activity-league.png",
                    "label": "CDL Arena",
                    "href": "#",
                },
                {
                    "image": "gowa/activity-team-nowild.png",
                    "label": "Team NO Wild",
                    "href": "#",
                },
                {
                    "image": "gowa/activity-geocache.png",
                    "label": "Geocache",
                    "href": "#",
                },
            ],
        },
    },
    "map": {
        "banner": {
            "image": "gowa/banner.png",
            "alt": "Doncaster skyline banner",
        },
        "heading": "Map & logistics",
        "body": (
            "Drop a static map, Google My Maps embed, or quick directions here. Use this placeholder to plan the layout."
        ),
        "notes": [
            "Venue hub: Doncaster Market Square",
            "Registration: Welcome arch beside Corn Exchange",
            "Rest zones: Corn Exchange mezzanine & Elmfield Park",
        ],
    },
    "more": {
        "banner": {
            "image": "gowa/section-breaker.png",
            "alt": "Sparkle banner",
        },
        "heading": "More to explore",
        "body": (
            "Use this space for FAQ links, volunteer sign-ups, accessibility info, or anything else you need on the day."
        ),
        "links": [
            {"label": "Volunteer briefing", "href": "#"},
            {"label": "Emergency contact sheet", "href": "#"},
            {"label": "Social media toolkit", "href": "#"},
        ],
    },
}


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


def _merge_nested_content(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_nested_content(result[key], value)
        elif isinstance(value, list) and isinstance(result.get(key), list):
            sanitized = [item for item in value if item is not None]
            if sanitized:
                result[key] = sanitized
        elif value is not None:
            result[key] = value
    return result


def load_live_events_content() -> dict:
    content = copy.deepcopy(LIVE_EVENTS_DEFAULT_CONTENT)
    if not LIVE_EVENTS_CONTENT_PATH.exists():
        return content
    try:
        with LIVE_EVENTS_CONTENT_PATH.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:
        print("⚠️ Failed to load live events content:", exc)
        return content
    if isinstance(raw, dict):
        return _merge_nested_content(content, raw)
    return content


def _ensure_live_events_enabled():
    if not LIVE_EVENTS_ENABLED:
        abort(404)


def _ensure_gowa_enabled():
    if not GOWA_ENABLED:
        abort(404)


def _gowa_banner():
    if not GOWA_ENABLED:
        return None
    try:
        return {
            "href": GOWA_EXTERNAL_URL,
            "image_url": url_for("static", filename=GOWA_BANNER_ASSET),
            "alt": "Doncaster GO Wild Area 2025",
        }
    except RuntimeError:
        # Called outside of a request context; treat as disabled.
        return None


def load_gowa_content() -> dict:
    content = copy.deepcopy(GOWA_DEFAULT_CONTENT)
    if not GOWA_CONTENT_PATH.exists():
        return content
    try:
        with GOWA_CONTENT_PATH.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:
        print("⚠️ Failed to load GOWA content:", exc)
        return content
    if not isinstance(raw, dict):
        return content

    for field in ("title", "description", "logo_alt"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            content[field] = value

    meta = raw.get("meta")
    if isinstance(meta, dict):
        content.setdefault("meta", {}).update({k: v for k, v in meta.items() if v})

    buttons = raw.get("buttons")
    if isinstance(buttons, list):
        filtered_buttons = [btn for btn in buttons if isinstance(btn, dict)]
        if filtered_buttons:
            content["buttons"] = filtered_buttons

    social = raw.get("social")
    if isinstance(social, dict):
        existing_social = content.get("social") or {}
        merged_social = {
            "label": social.get("label") or existing_social.get("label"),
            "links": existing_social.get("links", []),
        }
        links = social.get("links")
        if isinstance(links, list):
            merged_social["links"] = [link for link in links if isinstance(link, dict)]
        content["social"] = merged_social

    events = raw.get("events")
    if isinstance(events, dict):
        existing_events = content.get("events") or {}
        merged_events = {
            "heading": events.get("heading") or existing_events.get("heading"),
            "body": events.get("body") or existing_events.get("body"),
            "cards": existing_events.get("cards", []),
        }
        cards = events.get("cards")
        if isinstance(cards, list):
            merged_events["cards"] = [card for card in cards if isinstance(card, dict)]
        content["events"] = merged_events

    narrative = raw.get("narrative")
    if isinstance(narrative, dict):
        existing_narrative = content.get("narrative") or {}
        merged_narrative = {
            "heading": narrative.get("heading") or existing_narrative.get("heading"),
            "lede": narrative.get("lede") or existing_narrative.get("lede"),
            "paragraphs": existing_narrative.get("paragraphs", []),
            "breaker": narrative.get("breaker") or existing_narrative.get("breaker"),
            "button": narrative.get("button") or existing_narrative.get("button"),
        }
        paragraphs = narrative.get("paragraphs")
        if isinstance(paragraphs, list):
            merged_narrative["paragraphs"] = [p for p in paragraphs if isinstance(p, str)]
        content["narrative"] = merged_narrative

    expectations = raw.get("expectations")
    if isinstance(expectations, dict):
        existing_expectations = content.get("expectations") or {}
        merged_expectations = {
            "heading": expectations.get("heading") or existing_expectations.get("heading"),
            "quote": expectations.get("quote") or existing_expectations.get("quote"),
            "disclaimer": expectations.get("disclaimer") or existing_expectations.get("disclaimer"),
            "paragraphs": existing_expectations.get("paragraphs", []),
        }
        exp_paragraphs = expectations.get("paragraphs")
        if isinstance(exp_paragraphs, list):
            merged_expectations["paragraphs"] = [p for p in exp_paragraphs if isinstance(p, str)]
        content["expectations"] = merged_expectations

    factions = raw.get("factions")
    if isinstance(factions, dict):
        existing_factions = content.get("factions") or {}
        merged_factions = {
            "heading": factions.get("heading") or existing_factions.get("heading"),
            "intro": factions.get("intro") or existing_factions.get("intro"),
            "cards": existing_factions.get("cards", []),
        }
        faction_cards = factions.get("cards")
        if isinstance(faction_cards, list):
            merged_factions["cards"] = [card for card in faction_cards if isinstance(card, dict)]
        content["factions"] = merged_factions
    
    activities = raw.get("activities")
    if isinstance(activities, dict):
        existing_activities = content.get("activities") or {}
        merged_activities = {
            "heading": activities.get("heading") or existing_activities.get("heading"),
            "intro": activities.get("intro") or existing_activities.get("intro"),
            "disclaimer": activities.get("disclaimer") or existing_activities.get("disclaimer"),
            "cards": existing_activities.get("cards", []),
        }
        activity_cards = activities.get("cards")
        if isinstance(activity_cards, list):
            merged_activities["cards"] = [card for card in activity_cards if isinstance(card, dict)]
        content["activities"] = merged_activities

    agenda = raw.get("agenda")
    if isinstance(agenda, dict):
        existing_agenda = content.get("agenda") or {}
        merged_agenda = {
            "heading": agenda.get("heading") or existing_agenda.get("heading"),
            "note": agenda.get("note") or existing_agenda.get("note"),
            "events": existing_agenda.get("events", []),
        }
        agenda_events = agenda.get("events")
        if isinstance(agenda_events, list):
            merged_agenda["events"] = [item for item in agenda_events if isinstance(item, dict)]
        content["agenda"] = merged_agenda

    footer = raw.get("footer")
    if isinstance(footer, dict):
        existing_footer = content.get("footer") or {}
        merged_footer = {
            "heading": footer.get("heading") or existing_footer.get("heading"),
            "banner": footer.get("banner") or existing_footer.get("banner"),
            "disclaimer": footer.get("disclaimer") or existing_footer.get("disclaimer"),
        }
        content["footer"] = merged_footer

    return content


def _prepare_gowa_content() -> dict:
    content = copy.deepcopy(load_gowa_content())
    resolved_buttons: list[dict] = []
    for button in content.get("buttons", []):
        if not isinstance(button, dict):
            continue
        item = button.copy()
        endpoint = item.pop("endpoint", None)
        href = item.get("href")
        if endpoint:
            try:
                href = url_for(endpoint)
            except Exception as exc:
                print(f"⚠️ Invalid GOWA button endpoint '{endpoint}':", exc)
                href = None
        if not href:
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        item["label"] = label.strip()
        item["href"] = href
        if item.get("new_tab") is None:
            item["new_tab"] = bool(item.get("external") or str(href).startswith("http"))
        else:
            item["new_tab"] = bool(item.get("new_tab"))
        item["variant"] = item.get("variant") or "primary"
        resolved_buttons.append(item)
    content["buttons"] = resolved_buttons

    social = content.get("social") or {}
    links_resolved: list[dict] = []
    for link in social.get("links", []):
        if not isinstance(link, dict):
            continue
        item = link.copy()
        endpoint = item.pop("endpoint", None)
        href = item.get("href")
        if endpoint:
            try:
                href = url_for(endpoint)
            except Exception as exc:
                print(f"⚠️ Invalid GOWA social endpoint '{endpoint}':", exc)
                href = None
        if not href:
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        item["label"] = label.strip()
        item["href"] = href
        if item.get("new_tab") is None:
            item["new_tab"] = str(href).startswith("http")
        else:
            item["new_tab"] = bool(item.get("new_tab"))
        links_resolved.append(item)
    social["links"] = links_resolved
    content["social"] = social

    events = content.get("events") or {}
    cards_resolved: list[dict] = []
    for card in events.get("cards", []):
        if not isinstance(card, dict):
            continue
        item = card.copy()
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        item["title"] = title.strip()

        for key in ("date", "location", "body"):
            value = item.get(key)
            if isinstance(value, str):
                item[key] = value.strip()

        button_data = item.get("button")
        button_prepared: dict | None = None
        if isinstance(button_data, dict):
            btn = button_data.copy()
            endpoint = btn.pop("endpoint", None)
            href = btn.get("href")
            if endpoint:
                try:
                    href = url_for(endpoint)
                except Exception as exc:
                    print(f"⚠️ Invalid GOWA event button endpoint '{endpoint}':", exc)
                    href = None
            label = btn.get("label")
            if isinstance(label, str):
                label = label.strip()
            if href and label:
                btn["label"] = label
                btn["href"] = href
                if btn.get("new_tab") is None:
                    btn["new_tab"] = bool(btn.get("external") or str(href).startswith("http"))
                else:
                    btn["new_tab"] = bool(btn.get("new_tab"))
                btn["variant"] = btn.get("variant") or "primary"
                button_prepared = btn
        item["button"] = button_prepared

        logo = item.get("logo")
        logo_url = None
        if isinstance(logo, str):
            logo_path = logo.strip()
            if logo_path:
                if logo_path.startswith(("http://", "https://", "//")):
                    logo_url = logo_path
                else:
                    try:
                        logo_url = url_for("static", filename=logo_path.lstrip("/"))
                    except Exception as exc:
                        print(f"⚠️ Invalid GOWA event logo '{logo_path}':", exc)
                        logo_url = None
        item["logo_url"] = logo_url
        logo_alt = item.get("logo_alt")
        if not isinstance(logo_alt, str) or not logo_alt.strip():
            item["logo_alt"] = item["title"]
        else:
            item["logo_alt"] = logo_alt.strip()

        cards_resolved.append(item)
    events["cards"] = cards_resolved
    content["events"] = events
    
    narrative = content.get("narrative") or {}
    heading = narrative.get("heading")
    if isinstance(heading, str):
        narrative["heading"] = heading.strip()
    lede = narrative.get("lede")
    if isinstance(lede, str):
        narrative["lede"] = lede.strip()
    paragraphs_resolved: list[str] = []
    for para in narrative.get("paragraphs", []):
        if isinstance(para, str):
            clean_para = para.strip()
            if clean_para:
                paragraphs_resolved.append(clean_para)
    narrative["paragraphs"] = paragraphs_resolved
    breaker = narrative.get("breaker")
    if isinstance(breaker, dict):
        breaker_copy = breaker.copy()
        image = breaker_copy.get("image")
        breaker_url = None
        if isinstance(image, str):
            image_path = image.strip()
            if image_path:
                if image_path.startswith(("http://", "https://", "//")):
                    breaker_url = image_path
                else:
                    try:
                        breaker_url = url_for("static", filename=image_path.lstrip("/"))
                    except Exception as exc:
                        print(f"⚠️ Invalid GOWA breaker image '{image_path}':", exc)
                        breaker_url = None
        breaker_copy["image_url"] = breaker_url
        alt_text = breaker_copy.get("alt")
        if isinstance(alt_text, str) and alt_text.strip():
            breaker_copy["alt"] = alt_text.strip()
        else:
            breaker_copy["alt"] = "Section divider"
        narrative["breaker"] = breaker_copy
    button_data = narrative.get("button")
    narrative_button: dict | None = None
    if isinstance(button_data, dict):
        btn = button_data.copy()
        endpoint = btn.pop("endpoint", None)
        href = btn.get("href")
        if endpoint:
            try:
                href = url_for(endpoint)
            except Exception as exc:
                print(f"⚠️ Invalid GOWA narrative button endpoint '{endpoint}':", exc)
                href = None
        label = btn.get("label")
        if isinstance(label, str):
            label = label.strip()
        if href and label:
            btn["label"] = label
            btn["href"] = href
            if btn.get("new_tab") is None:
                btn["new_tab"] = bool(btn.get("external") or str(href).startswith("http"))
            else:
                btn["new_tab"] = bool(btn.get("new_tab"))
            btn["variant"] = btn.get("variant") or "primary"
            narrative_button = btn
    narrative["button"] = narrative_button
    content["narrative"] = narrative

    expectations = content.get("expectations") or {}
    heading = expectations.get("heading")
    if isinstance(heading, str):
        expectations["heading"] = heading.strip()
    quote = expectations.get("quote")
    if isinstance(quote, str):
        expectations["quote"] = quote.strip()
    disclaimer = expectations.get("disclaimer")
    if isinstance(disclaimer, str):
        expectations["disclaimer"] = disclaimer.strip()
    expectations_paragraphs: list[str] = []
    for para in expectations.get("paragraphs", []):
        if isinstance(para, str):
            clean_para = para.strip()
            if clean_para:
                expectations_paragraphs.append(clean_para)
    expectations["paragraphs"] = expectations_paragraphs
    content["expectations"] = expectations

    factions = content.get("factions") or {}
    heading = factions.get("heading")
    if isinstance(heading, str):
        factions["heading"] = heading.strip()
    intro = factions.get("intro")
    if isinstance(intro, str):
        factions["intro"] = intro.strip()
    faction_cards_resolved: list[dict] = []
    for card in factions.get("cards", []):
        if not isinstance(card, dict):
            continue
        item = card.copy()
        title = item.get("title")
        body = item.get("body")
        if not isinstance(title, str) or not title.strip():
            continue
        item["title"] = title.strip()
        if isinstance(body, str):
            item["body"] = body.strip()
        else:
            item["body"] = ""
        sigil = item.get("sigil")
        sigil_url = None
        if isinstance(sigil, str):
            sigil_path = sigil.strip()
            if sigil_path:
                if sigil_path.startswith(("http://", "https://", "//")):
                    sigil_url = sigil_path
                else:
                    try:
                        sigil_url = url_for("static", filename=sigil_path.lstrip("/"))
                    except Exception as exc:
                        print(f"⚠️ Invalid GOWA faction sigil '{sigil_path}':", exc)
                        sigil_url = None
        item["sigil_url"] = sigil_url
        sigil_alt = item.get("sigil_alt")
        if isinstance(sigil_alt, str) and sigil_alt.strip():
            item["sigil_alt"] = sigil_alt.strip()
        else:
            item["sigil_alt"] = item["title"]
        faction_cards_resolved.append(item)
    factions["cards"] = faction_cards_resolved
    content["factions"] = factions

    activities = content.get("activities") or {}
    heading = activities.get("heading")
    if isinstance(heading, str):
        activities["heading"] = heading.strip()
    intro = activities.get("intro")
    if isinstance(intro, str):
        activities["intro"] = intro.strip()
    disclaimer = activities.get("disclaimer")
    if isinstance(disclaimer, str):
        activities["disclaimer"] = disclaimer.strip()
    activities_cards_resolved: list[dict] = []
    for card in activities.get("cards", []):
        if not isinstance(card, dict):
            continue
        item = card.copy()
        title = item.get("title")
        body = item.get("body")
        if not isinstance(title, str) or not title.strip():
            continue
        item["title"] = title.strip()
        if isinstance(body, str):
            item["body"] = body.strip()
        else:
            item["body"] = ""
        image = item.get("image")
        image_url = None
        if isinstance(image, str):
            image_path = image.strip()
            if image_path:
                if image_path.startswith(("http://", "https://", "//")):
                    image_url = image_path
                else:
                    try:
                        image_url = url_for("static", filename=image_path.lstrip("/"))
                    except Exception as exc:
                        print(f"⚠️ Invalid GOWA activity image '{image_path}':", exc)
                        image_url = None
        item["image_url"] = image_url
        image_alt = item.get("image_alt")
        if isinstance(image_alt, str) and image_alt.strip():
            item["image_alt"] = image_alt.strip()
        else:
            item["image_alt"] = item["title"]
        activities_cards_resolved.append(item)
    activities["cards"] = activities_cards_resolved
    content["activities"] = activities

    agenda = content.get("agenda") or {}
    heading = agenda.get("heading")
    if isinstance(heading, str):
        agenda["heading"] = heading.strip()
    note = agenda.get("note")
    if isinstance(note, str):
        agenda["note"] = note.strip()
    agenda_events_resolved: list[dict] = []
    for item in agenda.get("events", []):
        if not isinstance(item, dict):
            continue
        event = item.copy()
        time_val = event.get("time")
        label = event.get("label")
        if isinstance(time_val, str):
            time_val = time_val.strip()
        else:
            time_val = ""
        if isinstance(label, str):
            label = label.strip()
        else:
            label = ""
        if not (time_val or label):
            continue
        event["time"] = time_val
        event["label"] = label
        agenda_events_resolved.append(event)
    agenda["events"] = agenda_events_resolved
    content["agenda"] = agenda

    footer = content.get("footer") or {}
    heading = footer.get("heading")
    if isinstance(heading, str):
        footer["heading"] = heading.strip()
    disclaimer = footer.get("disclaimer")
    if isinstance(disclaimer, str):
        footer["disclaimer"] = disclaimer.strip()
    banner = footer.get("banner")
    if isinstance(banner, dict):
        banner_copy = banner.copy()
        image = banner_copy.get("image")
        banner_url = None
        if isinstance(image, str):
            image_path = image.strip()
            if image_path:
                if image_path.startswith(("http://", "https://", "//")):
                    banner_url = image_path
                else:
                    try:
                        banner_url = url_for("static", filename=image_path.lstrip("/"))
                    except Exception as exc:
                        print(f"⚠️ Invalid GOWA footer banner '{image_path}':", exc)
                        banner_url = None
        banner_copy["image_url"] = banner_url
        alt_text = banner_copy.get("alt")
        if isinstance(alt_text, str) and alt_text.strip():
            banner_copy["alt"] = alt_text.strip()
        else:
            banner_copy["alt"] = "Community Ambassador banner"
        footer["banner"] = banner_copy
    content["footer"] = footer

    return content


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
    if MAINTENANCE_MODE:
        return redirect(url_for("gowa_portal"))

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
        return render_template("landing.html", show_back=False, gowa_banner=_gowa_banner())

    # Default fallback
    return redirect(url_for("login"))


@app.route("/live-events")
def live_events_page():
    _ensure_live_events_enabled()
    content = load_live_events_content()
    page_meta = content.get("meta") or {}
    canonical_url = page_meta.get("canonical") or LIVE_EVENTS_EXTERNAL_URL
    view = request.args.get("view", "agenda").lower()
    templates = {
        "agenda": "live_events/agenda.html",
        "activities": "live_events/activities.html",
        "map": "live_events/map.html",
        "more": "live_events/more.html",
    }
    if view not in templates:
        view = "agenda"
    return render_template(
        templates[view],
        content=content,
        page_meta=page_meta,
        canonical_url=canonical_url,
        active_view=view,
        navigation=content.get("navigation") or [],
    )


@app.route("/gowa")
def gowa_portal():
    _ensure_gowa_enabled()
    gowa_content = _prepare_gowa_content()
    canonical_url = (gowa_content.get("meta") or {}).get("canonical") or GOWA_EXTERNAL_URL
    return render_template(
        "gowa.html",
        gowa_logo_url=url_for("static", filename=GOWA_LOGO_ASSET),
        gowa_logo_alt=gowa_content.get("logo_alt", "GO Wild Area emblem"),
        gowa_content=gowa_content,
        canonical_url=canonical_url,
    )


@app.route("/gowild")
@app.route("/nowild")
def gowa_alias_redirect():
    _ensure_gowa_enabled()
    return redirect(url_for("gowa_portal"))


@app.route("/wild")
def gowa_legacy_redirect():
    _ensure_gowa_enabled()
    return redirect(url_for("gowa_portal"))

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

def _upload_to_supabase(file_storage, folder="catalog"):
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

        # 🔑 Upload — v2 client just takes path + bytes
        res = supabase.storage.from_("catalog").upload(object_key, file_bytes)
        print("➡️ Upload result:", res)

        # Return public URL
        public_url = supabase.storage.from_("catalog").get_public_url(object_key)
        print("✅ Uploaded file URL:", public_url)
        return public_url
    except Exception as e:
        print("❌ Supabase upload failed:", e)
        return None

    except Exception as e:
        # Supabase errors sometimes wrap useful fields on .args[0]
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
VAPID_PUBLIC_KEY = os.environ.get("MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEAbWEvTQ7pDPa0Q-O8drCVnHmfnzVpn7W7UkclKUd1A-yGIee_ehqUjRgMp_HxSBPMylN_H83ffaE2eDIybrTVA")
VAPID_PRIVATE_KEY = os.environ.get("MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgDJL244WZuoVzLqj3NvdTZ_fY-DtZqDQUakJdKV73myihRANCAAQBtYS9NDukM9rRD47x2sJWceZ-fNWmftbtSRyUpR3UD7IYh5796GpSNGAyn8fFIE8zKU38fzd99oTZ4MjJutNU")
VAPID_CLAIMS = {"sub": "mailto:raidingdoncaster@gmail.com"}

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

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
        record["account_type"] = normalize_account_type(record.get("account_type"))
        return None, record
    except Exception as e:
        print("⚠️ Supabase find_user failed:", e)
        return None, None


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

        # Fetch event cover photos
        ev_rows = supabase.table("events").select("event_id, cover_photo_url").execute().data or []
        event_map = {
            str(e.get("event_id", "")).strip().lower(): (e.get("cover_photo_url") or "")
            for e in ev_rows
        }

        stamps, total_count = [], 0
        for r in records:
            reason = (r.get("reason") or "").strip()
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
            if rl == "signup bonus":
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
            
            elif event_id and event_id in event_map and event_map[event_id]:
                icon = event_map[event_id]
            else:
                icon = url_for("static", filename="icons/tickstamp.png")

            stamps.append({"name": reason, "count": count, "icon": icon})

        most_recent = stamps[-1] if stamps else None
        return total_count, stamps, most_recent

    except Exception as e:
        print("⚠️ Supabase get_passport_stamps failed:", e)
        return 0, [], None

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
            checked_in_ids = [
                str(r.get("event_id", "")).strip().lower()
                for r in records if str(r.get("rsvp_status", "")).upper() == "CHECKED_IN"
            ]

            if not checked_in_ids:
                return [], 0

            # Step 3: Fetch events table
            ev_rows = supabase.table("events") \
                .select("event_id, name, start_time, cover_photo_url") \
                .execute().data or []

            ev_map = {str(e.get("event_id", "")).strip().lower(): e for e in ev_rows}

            # Step 4: Build meetups list
            meetups = []
            for eid in checked_in_ids:
                ev = ev_map.get(eid)
                if ev:
                    meetups.append({
                        "title": ev.get("name", "Unknown Event"),
                        "date": ev.get("start_time", ""),
                        "photo": ev.get("cover_photo_url", "")
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

def send_notification(audience, subject, message, notif_type="system"):
    try:
        supabase.table("notifications").insert({
            "type": notif_type,
            "audience": audience,
            "subject": subject,
            "message": message,
            "metadata": {},
            "sent_at": datetime.utcnow().isoformat(),
            "read_by": []
        }).execute()
    except Exception as e:
        print("⚠️ Failed to send notification:", e)

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


def _require_admin():
    trainer_username = session.get("trainer")
    if not trainer_username:
        abort(403)
    _, admin_user = find_user(trainer_username)
    if not admin_user or (admin_user.get("account_type") or "").lower() != "admin":
        abort(403)
    session["account_type"] = "Admin"

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
    flash(msg, "success" if ok else "error")
    redirect_username = final_username if ok else username
    return redirect(url_for("admin_trainer_detail", username=redirect_username))

# --- ADMIN: Change Campfire username ---
@app.route("/admin/trainers/<username>/change-campfire", methods=["POST"], endpoint="admin_change_campfire_username_v2")
@app.route("/admin/trainers/<username>/change_campfire", methods=["POST"], endpoint="admin_change_campfire_username_legacy")
def admin_change_campfire_username_route(username):
    _require_admin()
    new_campfire = request.form.get("new_campfire_username", "")
    actor = _current_actor()
    ok, msg = update_campfire_username(username, new_campfire, actor)
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
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_trainer_detail", username=username))

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "trainer" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("home"))

        # Load user from Sheet1 (or Supabase if you’ve switched)
        _, user = find_user(session["trainer"])
        account_type = user.get("account_type", "")

        if account_type != "Admin":
            flash("Admins only!", "error")
            return redirect(url_for("dashboard"))

        return f(*args, **kwargs)
    return wrapper

# ===== Admin Dashboard =====
@app.route("/admin/dashboard", methods=["GET", "POST"])
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
        security_state = session.get("admin_dashboard_security") or {}
        try:
            remaining = int(security_state.get("remaining", ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS))
        except (TypeError, ValueError):
            remaining = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
        remaining = max(0, remaining)

        now_ts = time.time()
        lock_until_ts = None
        lock_until_raw = security_state.get("lock_until")
        if lock_until_raw is not None:
            try:
                lock_until_ts = float(lock_until_raw)
            except (TypeError, ValueError):
                security_state["lock_until"] = None
                lock_until_ts = None

        if lock_until_ts and now_ts < lock_until_ts:
            wait_seconds = max(int(lock_until_ts - now_ts), 1)
            flash(f"Too many incorrect attempts. Try again in {wait_seconds} seconds.", "error")
            security_state["remaining"] = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
            session["admin_dashboard_security"] = security_state
            return (
                render_template(
                    "admin_dashboard_password.html",
                    remaining_attempts=ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS,
                    locked=True,
                    lockout_seconds=wait_seconds,
                ),
                403,
            )

        if request.method == "POST":
            submitted_password = request.form.get("admin_password", "")
            if _admin_dashboard_gate_check(submitted_password):
                session["admin_dashboard_gate"] = {"verified_at": time.time()}
                security_state["remaining"] = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
                security_state["lock_until"] = None
                session["admin_dashboard_security"] = security_state
                flash("Admin dashboard unlocked.", "success")
                return redirect(url_for("admin_dashboard"))

            remaining = max(remaining - 1, 0)
            security_state["remaining"] = remaining
            if remaining <= 0:
                security_state["lock_until"] = now_ts + ADMIN_DASHBOARD_GATE_LOCK_SECONDS
                security_state["remaining"] = ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS
                flash(f"Too many incorrect attempts. Try again in {ADMIN_DASHBOARD_GATE_LOCK_SECONDS} seconds.", "error")
            else:
                security_state["lock_until"] = None
                attempt_word = "attempt" if remaining == 1 else "attempts"
                flash(f"Incorrect admin password. {remaining} {attempt_word} remaining.", "error")

            session["admin_dashboard_security"] = security_state
        else:
            security_state["remaining"] = min(remaining, ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS)
            session["admin_dashboard_security"] = security_state

        return render_template(
            "admin_dashboard_password.html",
            remaining_attempts=security_state.get("remaining", ADMIN_DASHBOARD_GATE_MAX_ATTEMPTS),
            locked=False,
            lockout_seconds=None,
        )

    if gate_required:
        session.pop("admin_dashboard_security", None)

    active_catalog_items = 0
    pending_redemptions = 0
    registered_trainers = 0

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

    return render_template(
        "admin_dashboard.html",
        active_catalog_items=active_catalog_items,
        pending_redemptions=pending_redemptions,
        registered_trainers=registered_trainers,
        show_catalog_app=SHOW_CATALOG_APP,
        show_city_perks_app=SHOW_CITY_PERKS_APP,
        show_city_guides_app=SHOW_CITY_GUIDES_APP,
        show_leagues_app=SHOW_LEAGUES_APP,
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
    # Compare hashed pin
    if user.get("pin_hash") != hash_value(pin):
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

def _is_admin():
    if "trainer" not in session:
        return False
    _, u = find_user(session["trainer"])
    return bool(u and u.get("account_type") == "Admin")

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
@app.route("/admin/catalog")
def admin_catalog():
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    items = []
    try:
        resp = supabase.table("catalog_items").select("*").order("created_at", desc=True).execute()
        items = resp.data or []
    except Exception as e:
        print("⚠️ Failed fetching catalog items:", e)

    return render_template("admin_catalog.html", items=items)


@app.route("/admin/geocache/story")
def admin_geocache_story():
    _require_admin()
    try:
        story = services.load_story(include_assets=True)
    except Exception as exc:
        flash(f"Unable to load geocache story: {exc}", "error")
        return redirect(url_for("admin_dashboard"))

    scenes = story.get("scenes", {})
    acts_raw = story.get("acts", [])
    acts = []

    for act in acts_raw:
        act_id = act.get("id")
        act_number = None
        if isinstance(act_id, str) and act_id.lower().startswith("act"):
            try:
                act_number = int(act_id[3:])
            except ValueError:
                act_number = None
        required = REQUIRED_FLAGS_BY_ACT.get(act_number or act_id, set())
        act_scenes = act.get("scenes") or []
        acts.append(
            {
                "id": act_id,
                "title": act.get("title"),
                "intro": act.get("intro"),
                "objectives": act.get("objectives") or [],
                "scenes": [(scene_id, scenes.get(scene_id, {})) for scene_id in act_scenes],
                "next_act": act.get("next_act"),
                "required_flags": sorted(required),
            }
        )

    return render_template(
        "admin_geocache_story.html",
        story=story,
        acts=acts,
        scenes=scenes,
    )


@app.route("/admin/catalog/<item_id>")
def admin_catalog_detail(item_id):
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    try:
        resp = supabase.table("catalog_items").select("*").eq("id", item_id).limit(1).execute()
        if not resp.data:
            flash("Item not found.", "error")
            return redirect(url_for("admin_catalog"))
        item = resp.data[0]
    except Exception as e:
        print("⚠️ Failed fetching catalog detail:", e)
        flash("Error loading item.", "error")
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
        supabase.table("catalog_items").update(data).eq("id", item_id).execute()
        flash("✅ Item updated successfully!", "success")
    except Exception as e:
        print("⚠️ Catalog update failed:", e)
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
        flash("❌ Failed to delete item.", "error")

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
    total_filtered = 0
    total_pages = 1
    has_more = False
    meetup_lookup: dict[str, dict] = {}
    all_meetups: list[dict] = []

    def build_tab_url(view_name: str, page_override: int | None = None) -> str:
        params: dict[str, object] = {"view": view_name}
        if status_filter != "ALL":
            params["status"] = status_filter
        if search_user:
            params["search"] = search_user
        if page_override is not None:
            params["page"] = page_override
        elif view_name == "list":
            params["page"] = page
        return url_for("admin_redemptions", **params)

    list_tab_url = build_tab_url("list")
    bucket_tab_url = build_tab_url("prize-buckets", page_override=1)

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
                sort_date = date.fromisoformat(date_str) if date_str else date.max
            except ValueError:
                sort_date = date.max

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
                "sort_key": (sort_date, start_time_str, title.lower()),
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

        buckets.sort(key=lambda bucket: (bucket["sort_weight"], bucket["sort_key"]))
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
                prize_buckets = build_prize_buckets(all_meetups, filtered_redemptions, meetup_lookup)
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

        supabase.table("notifications").insert({
            "type": "system",
            "audience": trainer,
            "subject": subject,
            "message": message,
            "metadata": {},
            "sent_at": datetime.utcnow().isoformat()
        }).execute()

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
    try:
        resp = supabase.table("sheet1").select(
            "trainer_username, campfire_username, account_type, stamps, avatar_icon"
        ).execute()
        all_trainers = resp.data or []
        for entry in all_trainers:
            entry["account_type"] = normalize_account_type(entry.get("account_type"))
            entry.setdefault("avatar_icon", "avatar1.png")
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

@app.route("/admin/trainers/<username>")
def admin_trainer_detail(username):
    if "trainer" not in session:
        flash("Please log in.", "warning")
        return redirect(url_for("home"))

    # ✅ Require Admin account_type
    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    # 🔎 Find trainer
    _, trainer_data = find_user(username)
    if not trainer_data:
        flash(f"No trainer found with username '{username}'", "warning")
        return redirect(url_for("admin_trainers"))

    return render_template(
        "admin_trainer_detail.html",
        trainer=trainer_data
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
    hashed = hash_value(new_pin)

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
from datetime import datetime
@app.route("/admin/stats")
@admin_required
def admin_stats():
    # --- Pull data ---
    events = []
    attendance = []
    accounts = []

    try:
        events = (supabase.table("events")
                  .select("event_id,name,start_time,cover_photo_url")
                  .execute().data) or []
    except Exception as e:
        print("⚠️ events fetch failed:", e)

    try:
        attendance = (supabase.table("attendance")
                      .select("event_id,rsvp_status,campfire_username,display_name,checked_in_at")
                      .execute().data) or []
    except Exception as e:
        print("⚠️ attendance fetch failed:", e)

    try:
        accounts = (supabase.table("sheet1")
                    .select("trainer_username,account_type,stamps")
                    .execute().data) or []
    except Exception as e:
        print("⚠️ accounts fetch failed:", e)

    # --- Index events by id ---
    ev_map = {}
    month_of_event = {}
    for e in events:
        eid = str(e.get("event_id") or "").strip().lower()
        ev_map[eid] = {
            "name": e.get("name") or "Unknown",
            "date": e.get("start_time") or "",
            "cover": e.get("cover_photo_url") or ""
        }
        try:
            dt = datetime.fromisoformat((e.get("start_time") or "").replace("Z", "+00:00"))
            month_of_event[eid] = dt.strftime("%Y-%m")  # e.g. 2025-02
        except Exception:
            month_of_event[eid] = None

    # --- Keep only unique CHECKED_IN entries (event_id + user) ---
    def norm_status(s):
        return (s or "").upper().replace("-", "_").strip()

    def norm_user(row):
        return (row.get("campfire_username") or row.get("display_name") or "").strip().lower()

    unique_checkins = set()
    for r in attendance:
        if norm_status(r.get("rsvp_status")) != "CHECKED_IN":
            continue
        eid = str(r.get("event_id") or "").strip().lower()
        user = norm_user(r)
        if not eid or not user:
            continue
        unique_checkins.add((eid, user))

    # --- Aggregations ---
    total_attendances = len(unique_checkins)
    counts_by_event = Counter(eid for eid, _ in unique_checkins)
    counts_by_trainer = Counter(user for _, user in unique_checkins)

    # Top meetups (join with event info)
    top_meetups = []
    for eid, cnt in counts_by_event.most_common(10):
        meta = ev_map.get(eid, {})
        top_meetups.append({
            "event_id": eid,
            "name": meta.get("name", "Unknown"),
            "date": meta.get("date", ""),
            "count": cnt
        })

    # Top trainers
    top_trainers = [{"trainer": t or "unknown", "count": c}
                    for t, c in counts_by_trainer.most_common(10)]

    # Growth trends (per month)
    events_per_month = Counter()
    for eid, meta in ev_map.items():
        m = month_of_event.get(eid)
        if m: events_per_month[m] += 1

    attend_per_month = Counter()
    for eid, _ in unique_checkins:
        m = month_of_event.get(eid)
        if m: attend_per_month[m] += 1

    # Build a unified month axis (sorted)
    months = sorted(set(events_per_month.keys()) | set(attend_per_month.keys()))
    growth_labels = months
    growth_events = [events_per_month[m] for m in months]
    growth_attend = [attend_per_month[m] for m in months]

    # Stamp distribution
    bins = {"0–4": 0, "5–9": 0, "10–19": 0, "20+": 0}
    for a in accounts:
        try:
            s = int(a.get("stamps") or 0)
        except Exception:
            s = 0
        if s <= 4: bins["0–4"] += 1
        elif s <= 9: bins["5–9"] += 1
        elif s <= 19: bins["10–19"] += 1
        else: bins["20+"] += 1
    stamp_labels = list(bins.keys())
    stamp_counts = list(bins.values())

    # Account types pie
    acct_counter = Counter(normalize_account_type(a.get("account_type")) for a in accounts)
    account_labels = list(acct_counter.keys())
    account_counts = list(acct_counter.values())

    # Summary numbers
    total_meetups = len(events)                 # all events in table
    meetups_with_checkins = len(counts_by_event)  # events that had at least one check-in
    unique_attendees = len(counts_by_trainer)
    avg_attendance = round(total_attendances / max(meetups_with_checkins, 1), 1)

    # Returning vs new attendees
    new_only = sum(1 for _, c in counts_by_trainer.items() if c == 1)
    returning_pct = round(100 * (1 - (new_only / max(unique_attendees, 1))), 1)

    # Engagement highlights
    highlights = {
        "avg_attendance": avg_attendance,
        "unique_attendees": unique_attendees,
        "meetups_with_checkins": meetups_with_checkins,
        "returning_pct": returning_pct
    }

    return render_template(
        "admin_stats.html",
        # summary cards
        total_meetups=total_meetups,
        total_attendances=total_attendances,
        # top meetups
        top_meetups=top_meetups,  # [{name,date,count}]
        # top trainers
        top_trainers=top_trainers,  # [{trainer,count}]
        # growth
        growth_labels=growth_labels,
        growth_events=growth_events,
        growth_attend=growth_attend,
        # distributions
        stamp_labels=stamp_labels,
        stamp_counts=stamp_counts,
        account_labels=account_labels,
        account_counts=account_counts,
        # highlights
        highlights=highlights
    )

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
@app.route("/admin/notifications", methods=["GET", "POST"])
def admin_notifications():
    if "trainer" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("⛔ Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        notif_type = request.form.get("type", "announcement")
        audience   = request.form.get("audience", "ALL").strip()
        subject    = request.form.get("subject", "").strip()
        message    = request.form.get("message", "").strip()

        if not subject or not message:
            flash("Subject and message are required.", "warning")
            return redirect(url_for("admin_notifications"))

        try:
            # Insert into Supabase notifications
            supabase.table("notifications").insert({
                "type": notif_type,
                "audience": audience,   # could be "ALL" or a specific trainer_username
                "subject": subject,
                "message": message,
                "metadata": {},         # JSONB for future (attachments, deep links, etc.)
                "sent_at": datetime.utcnow().isoformat(),
                "read_by": []
            }).execute()

            # 📡 Future: send to Telegram here
            # if audience == "ALL":
            #     send_to_telegram(subject, message)

            flash("✅ Notification sent!", "success")
        except Exception as e:
            print("⚠️ Failed sending notification:", e)
            flash("❌ Failed to send notification.", "error")

        return redirect(url_for("admin_notifications"))

    # Show recent notifications
    notifications = []
    try:
        resp = supabase.table("notifications") \
            .select("*") \
            .order("sent_at", desc=True) \
            .limit(20) \
            .execute()
        notifications = resp.data or []
    except Exception as e:
        print("⚠️ Failed loading notifications:", e)

    return render_template("admin_notifications.html", notifications=notifications)

# ==== Login ====
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
        security_state = session.get("login_security") or {
            "remaining": LOGIN_MAX_ATTEMPTS,
            "lock_until": None,
        }
        session["login_security"] = security_state

        now = time.time()
        lock_until = security_state.get("lock_until")
        if lock_until and now < lock_until:
            wait_seconds = max(int(lock_until - now), 1)
            flash(f"Too many incorrect attempts. Try again in {wait_seconds} seconds.", "error")
            return redirect(url_for("login"))

        username = request.form.get("username", "").strip()
        pin = request.form.get("pin", "")

        _, user = find_user(username)
        if user and user.get("pin_hash") == hash_value(pin):
            session["trainer"] = user.get("trainer_username")
            session["account_type"] = normalize_account_type(user.get("account_type"))
            session.permanent = True
            try:
                supabase.table("sheet1") \
                    .update({"last_login": datetime.utcnow().isoformat()}) \
                    .eq("trainer_username", user.get("trainer_username")) \
                    .execute()
            except Exception as e:
                print("⚠️ Supabase last_login update failed:", e)

            security_state["remaining"] = LOGIN_MAX_ATTEMPTS
            security_state["lock_until"] = None
            session["login_security"] = security_state

            flash(f"Welcome back, {user.get('trainer_username')}!", "success")
            last_page = session.pop("last_page", None)
            if last_page:
                return redirect(last_page)
            return redirect(url_for("dashboard"))
        else:
            remaining = max(security_state.get("remaining", LOGIN_MAX_ATTEMPTS) - 1, 0)
            security_state["remaining"] = remaining

            if remaining <= 0:
                security_state["lock_until"] = now + LOGIN_LOCKOUT_SECONDS
                security_state["remaining"] = LOGIN_MAX_ATTEMPTS
                session["login_security"] = security_state
                flash(f"Too many incorrect attempts. Try again in {LOGIN_LOCKOUT_SECONDS} seconds.", "error")
            else:
                security_state["lock_until"] = None
                attempt_word = "attempt" if remaining == 1 else "attempts"
                session["login_security"] = security_state
                flash(f"Wrong PIN. {remaining} {attempt_word} remaining.", "error")

            return redirect(url_for("login"))

    # GET request — just show login form
    return render_template("login.html", gowa_banner=_gowa_banner())

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

            payload = {
                "trainer_username": details["trainer_name"],
                "pin_hash": hash_value(details["pin"]),
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

        payload = {
            "trainer_username": details["trainer_name"],
            "pin_hash": hash_value(details["pin"]),
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
            supabase.table("sheet1").update({
                "pin_hash": hash_value(new_pin),
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
        gowa_banner=_gowa_banner(),
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

    return render_template(
        "inbox.html",
        trainer=trainer,
        inbox=messages,
        sort_by=sort_by,
        tab=tab,
        show_back=False
    )

@app.route("/inbox/message/<message_id>")
def inbox_message(message_id):
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view your inbox.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]

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

    stored_pin_hash = user.get("pin_hash") or user.get("PIN Hash")
    if stored_pin_hash != hash_value(old_pin):
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
        supabase.table("sheet1").update({
            "pin_hash": hash_value(new_pin),
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
        sort_by=sort_by
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
        supabase.table("notifications").insert({
            "type": "prize",
            "audience": trainer,
            "subject": subj,
            "message": msg,
            "metadata": {"url": receipt_url, "redemption_id": red_id},
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "read_by": [],
        }).execute()
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

    if request.method == "POST":
        avatar_choice = request.form.get("avatar_choice")
        background_choice = request.form.get("background_choice")

        valid_avatars = [f"avatar{i}.png" for i in range(1, 20)]
        if avatar_choice not in valid_avatars:
            flash("Invalid avatar choice.", "error")
            return redirect(url_for("change_avatar"))

        # validate background from /static/backgrounds
        backgrounds_folder = os.path.join(app.root_path, "static", "backgrounds")
        valid_backgrounds = os.listdir(backgrounds_folder)
        if background_choice not in valid_backgrounds:
            flash("Invalid background choice.", "error")
            return redirect(url_for("change_avatar"))

        if not supabase:
            flash("Supabase is unavailable. Please try again later.", "error")
            return redirect(url_for("change_avatar"))

        try:
            supabase.table("sheet1") \
                .update({
                    "avatar_icon": avatar_choice,
                    "trainer_card_background": background_choice
                }) \
                .eq("trainer_username", session["trainer"]) \
                .execute()
        except Exception as e:
            print("⚠️ Failed updating Supabase avatar/background:", e)
            flash("Unable to update appearance right now. Please try again soon.", "error")
            return redirect(url_for("change_avatar"))

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "avatar": avatar_choice, "background": background_choice})

        flash("✅ Appearance updated successfully!", "success")
        return redirect(url_for("dashboard"))

    avatars = [f"avatar{i}.png" for i in range(1, 20)]
    backgrounds_folder = os.path.join(app.root_path, "static", "backgrounds")
    backgrounds = os.listdir(backgrounds_folder)

    current_avatar = user.get("avatar_icon", "avatar1.png")
    current_background = user.get("trainer_card_background") or "default.png"

    return render_template(
        "change_avatar.html",
        avatars=avatars,
        backgrounds=backgrounds,
        current_avatar=current_avatar,
        current_background=current_background,
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
