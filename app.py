import os
import json
import hashlib
import gspread
import requests
import uuid
import re
from flask import Flask, render_template, abort, request, redirect, url_for, session, flash, send_from_directory, jsonify, g
from google.oauth2.service_account import Credentials
from werkzeug.utils import secure_filename
from PIL import Image
from pywebpush import webpush, WebPushException
import pytesseract
from datetime import datetime, date, timezone, timedelta
from dateutil import parser
import io, base64, time
from markupsafe import Markup, escape

# ====== Feature toggle ======
USE_SUPABASE = True  # ✅ Supabase for stamps/meetups
MAINTENANCE_MODE = False  # ⛔️ Change to True to enable maintenance mode

# Try to import Supabase client
try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client, Client = None, None

# ====== Flask setup ======
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.permanent_session_lifetime = timedelta(days=365)
@app.errorhandler(404)
@app.errorhandler(500)
def show_custom_error_page(err):
    status_code = getattr(err, "code", 500) or 500
    return render_template("error.html"), status_code

@app.before_request
def check_maintenance_mode():
    from flask import request, redirect, url_for, render_template, session

    # Allow admins through even during maintenance
    if session.get("account_type") == "Admin":
        return

    # Skip maintenance mode for static files, manifest, and login
    allowed_endpoints = ("static", "manifest", "service_worker", "maintenance", "admin_login")
    if app.view_functions.get(request.endpoint) and request.endpoint.startswith(allowed_endpoints):
        return

    # If maintenance mode is on, show maintenance page
    if MAINTENANCE_MODE:
        return render_template("maintenance.html"), 503

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

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

# ====== Google Sheets setup ======
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]),
    scopes=SCOPES
)
gclient = gspread.authorize(creds)
sheet = gclient.open("POGO Passport Sign-Ins").worksheet("Sheet1")

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
    if is_mobile:
        return render_template("landing.html", show_back=False)

    # Default fallback
    return redirect(url_for("login"))

@app.route("/session-check")
def session_check():
    """Quick JSON endpoint for PWA reload logic."""
    return jsonify({"logged_in": "trainer" in session})

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

def absolute_url(path: str) -> str:
    root = request.url_root.rstrip('/')
    if not path.startswith('/'):
        path = '/' + path
    return f"{root}{path}"

@app.template_filter("nl2br")
def nl2br(text):
    if text is None:
        return ""
    return Markup(escape(text).replace("\n", "<br>"))

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

def get_all_users():
    """Cache Sheet1 records per-request so we only fetch once."""
    if not hasattr(g, "user_records"):
        g.user_records = sheet.get_all_records()
    return g.user_records

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
        record.setdefault("trainer_card_background", "default.png")
        record.setdefault("account_type", "Standard")
        return None, record
    except Exception as e:
        print("⚠️ Supabase find_user failed:", e)
        return None, None

def extract_trainer_name(image_path):
    try:
        img = Image.open(image_path)
        w, h = img.size
        top, bottom = int(h * 0.15), int(h * 0.25)
        left, right = int(w * 0.05), int(w * 0.90)
        cropped = img.crop((left, top, right, bottom))
        text = pytesseract.image_to_string(cropped)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines[0] if lines else None
    except Exception as e:
        print("❌ OCR failed:", e)
        return None

def trigger_lugia_refresh():
    url = "https://script.google.com/macros/s/AKfycbwx33Twu9HGwW4bsSJb7vwHoaBS56gCldNlqiNjxGBJEhckVDAnv520MN4ZQWxI1U9D/exec"
    try:
        requests.get(url, params={"action": "lugiaRefresh"}, timeout=10)
    except Exception as e:
        print("⚠️ Lugia refresh error:", e)

import os, requests
LUGIA_URL = os.getenv("LUGIA_WEBAPP_URL")
def adjust_stamps(trainer, count, reason, action):
    """Call the Lugia Google Apps Script to award/remove stamps."""
    payload = {
        "action": action,  # "award" or "remove"
        "trainer": trainer,
        "count": count,
        "reason": reason
    }
    resp = requests.post(LUGIA_URL, json=payload)
    if resp.status_code == 200:
        return resp.text
    else:
        return f"❌ Error {resp.status_code}: {resp.text}"

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
            count = int(r.get("count") or 1)
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
            elif "just being normal" in rl:
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
@app.route("/admin/dashboard")
def admin_dashboard():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to access admin dashboard.", "warning")
        return redirect(url_for("home"))

    _, user = find_user(session["trainer"])
    if not user or user.get("account_type") != "Admin":
        flash("⛔ Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))

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
        registered_trainers=registered_trainers
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

    # Query params
    status_filter = request.args.get("status", "ALL")
    search_user = request.args.get("search", "").strip()
    page = int(request.args.get("page", 1))
    per_page = 20  # show 20 redemptions per page

    redemptions = []
    stats = {"total": 0, "pending": 0, "fulfilled": 0, "cancelled": 0}
    total_filtered = 0

    try:
        # Build query with filters
        query = supabase.table("redemptions").select("*").order("created_at", desc=True)

        if status_filter != "ALL":
            query = query.eq("status", status_filter)

        if search_user:
            query = query.ilike("trainer_username", f"%{search_user}%")

        # Pagination: range = [from, to]
        from_row = (page - 1) * per_page
        to_row = from_row + per_page - 1
        resp = query.range(from_row, to_row).execute()
        redemptions = resp.data or []

        # Count for pagination
        total_filtered = len(
            supabase.table("redemptions").select("id", count="exact").execute().data or []
        )

        # Global counts (ignores filters)
        all_resp = supabase.table("redemptions").select("status").execute()
        stats["total"] = len(all_resp.data or [])
        stats["pending"] = sum(1 for r in all_resp.data if r["status"] == "PENDING")
        stats["fulfilled"] = sum(1 for r in all_resp.data if r["status"] == "FULFILLED")
        stats["cancelled"] = sum(1 for r in all_resp.data if r["status"] == "CANCELLED")

    except Exception as e:
        print("⚠️ Failed fetching redemptions:", e)

    # total pages for pagination UI
    total_pages = max(1, (total_filtered + per_page - 1) // per_page)

    return render_template(
        "admin_redemptions.html",
        redemptions=redemptions,
        stats=stats,
        status_filter=status_filter,
        search_user=search_user,
        page=page,
        total_pages=total_pages,
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
        redemption = supabase.table("redemptions").select("*").eq("id", str(redemption_id)).execute().data[0]
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

        return jsonify({"success": True, "new_status": new_status})
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
    except Exception as e:
        print("⚠️ Failed fetching all trainers:", e)

    return render_template(
        "admin_trainers.html",
        trainer_data=trainer_data,
        all_trainers=all_trainers
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

@app.route("/admin/trainers/<username>/adjust_stamps", methods=["POST"])
def admin_adjust_stamps(username):
    if "trainer" not in session:
        flash("Please log in.", "error")
        return redirect(url_for("home"))

    # Check admin status from Supabase
    _, current_user = find_user(session["trainer"])
    if not current_user or current_user.get("account_type") != "Admin":
        flash("Unauthorized access.", "error")
        return redirect(url_for("dashboard"))

    action = request.form.get("action")  # "award" or "remove"
    count = int(request.form.get("count", 0))
    reason = request.form.get("reason", "Admin Adjustment")

    result = adjust_stamps(username, count, reason, action)
    flash(result, "success" if "✅" in result else "error")
    return redirect(url_for("admin_trainer_detail", username=username))

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

    new_type = request.form.get("account_type")
    if new_type not in ["Standard", "Kids Account", "Admin"]:
        flash("Invalid account type.", "error")
        return redirect(url_for("admin_trainer_detail", username=username))

    try:
        # Update Google Sheet (Sheet1)
        all_users = sheet.get_all_records()
        row_index = None
        for i, record in enumerate(all_users, start=2):
            if record.get("Trainer Username", "").lower() == username.lower():
                row_index = i
                break
        if row_index:
            sheet.update_cell(row_index, 8, new_type)  # H = Account Type col

        # Update Supabase mirror if available
        if supabase:
            supabase.table("sheet1") \
                .update({"account_type": new_type}) \
                .ilike("trainer_username", username) \
                .execute()

        flash(f"✅ {username}'s account type updated to {new_type}", "success")
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

    try:
        hashed = hash_value(new_pin)

        # Update Google Sheet (Sheet1)
        all_users = sheet.get_all_records()
        row_index = None
        for i, record in enumerate(all_users, start=2):
            if record.get("Trainer Username", "").lower() == username.lower():
                row_index = i
                break
        if row_index:
            sheet.update_cell(row_index, 2, hashed)  # B = PIN Hash col

        # Update Supabase mirror if available
        if supabase:
            supabase.table("sheet1") \
                .update({"pin_hash": hashed}) \
                .ilike("trainer_username", username) \
                .execute()

        flash(f"✅ PIN for {username} has been reset.", "success")
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
    acct_counter = Counter((a.get("account_type") or "Standard") for a in accounts)
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
        username = request.form["username"]
        pin = request.form["pin"]

        _, user = find_user(username)
        if not user:
            flash("No trainer found!", "error")
            return redirect(url_for("home"))

        if user.get("pin_hash") == hash_value(pin):
            session["trainer"] = user.get("trainer_username")
            session.permanent = True
            try:
                supabase.table("sheet1") \
                    .update({"last_login": datetime.utcnow().isoformat()}) \
                    .eq("trainer_username", user.get("trainer_username")) \
                    .execute()
            except Exception as e:
                print("⚠️ Supabase last_login update failed:", e)

            flash(f"Welcome back, {user.get('trainer_username')}!", "success")
            last_page = session.pop("last_page", None)
            if last_page:
                return redirect(last_page)
            return redirect(url_for("dashboard"))
        else:
            flash("Incorrect PIN!", "error")
            return redirect(url_for("home"))

    # GET request — just show login form
    return render_template("login.html")

# ====== Sign Up ======
def _trainer_exists(trainer_name: str) -> bool:
    """Return True if this trainer username already exists in the signup sheet."""
    try:
        records = sheet.get_all_records()
    except Exception as exc:  # pragma: no cover - defensive logging for Sheets outages
        print("⚠️ Unable to read signup sheet; assuming trainer is new:", exc)
        return False

    trainer_lc = (trainer_name or "").strip().lower()
    for record in records:
        if record.get("Trainer Username", "").strip().lower() == trainer_lc:
            return True
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
    
    action = request.form.get("action") if request.method == "POST" else None
    if request.method == "POST":
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
        all_users = sheet.get_all_records()
        for record in all_users:
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
            records = sheet.get_all_records()
            for r in records:
                if _trainer_exists(details["trainer_name"]):
                    flash("This trainer already exists. Please log in.", "error")
                    session.pop("signup_details", None)
                    return redirect(url_for("home"))

            # Kids Account: store in Sheet1
            sheet.append_row([
                details["trainer_name"],
                hash_value(details["pin"]),
                details["memorable"],
                datetime.utcnow().isoformat(),
                "Kids Account",   # Column E
                "0",              # Column F
                "avatar1.png"     # Column G
            ])
            if supabase:
                supabase.table("sheet1").insert({
                    "trainer_username": details["trainer_name"],
                    "pin_hash": hash_value(details["pin"]),
                    "memorable_password": details["memorable"],
                    "last_login": datetime.utcnow().isoformat(),
                    "campfire_username": "Kids Account",
                    "stamps": 0,
                    "avatar_icon": "avatar1.png",
                    "trainer_card_background": "default.png",
                    "account_type": "Kids Account"
                }).execute()

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
        campfire_username = request.form.get("campfire_username")
        if not campfire_username:
            flash("Campfire username is required.", "warning")
            return redirect(url_for("campfire"))

        # ✅ Backend guard to prevent duplicates
        records = sheet.get_all_records()
        for r in records:
            if r.get("Trainer Username", "").lower() == details["trainer_name"].lower():
                flash("This trainer already exists. Please log in.", "error")
                session.pop("signup_details", None)
                return redirect(url_for("home"))

        # Save to Sheets
        sheet.append_row([
            details["trainer_name"],
            hash_value(details["pin"]),
            details["memorable"],
            datetime.utcnow().isoformat(),
            campfire_username,  # Column E
            "",                 # Column F
            "avatar1.png",       # Column G
            "Standard"          # Column H
        ])
        if supabase:
            supabase.table("sheet1").insert({
                "trainer_username": details["trainer_name"],
                "pin_hash": hash_value(details["pin"]),
                "memorable_password": details["memorable"],
                "last_login": datetime.utcnow().isoformat(),
                "campfire_username": campfire_username,
                "stamps": 0,
                "avatar_icon": "avatar1.png",
                "trainer_card_background": "default.png",
                "account_type": "Standard"
            }).execute()

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

        row, user = find_user(username)
        if not user:
            flash("❌ No trainer found with that name.", "error")
            return redirect(url_for("recover"))

        if user.get("Memorable Password") != memorable:
            flash("⚠️ Memorable password does not match.", "error")
            return redirect(url_for("recover"))

        sheet.update_cell(row, 2, hash_value(new_pin))
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
        account_type=user.get("account_type", "Standard"),
        show_back=False,
    )

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
    return {
        "id": f"rec:{rec['id']}",
        "subject": f"🧾 Receipt: {rec['item_snapshot']['name']}",
        "message": f"You redeemed {rec['item_snapshot']['name']} "
                   f"for {rec['stamps_spent']} stamps at {rec['metadata']['meetup']['name']}.",
        "sent_at": _normalize_iso(rec.get("created_at")),
        "type": "receipt",
        "read_by": [],
        "metadata": {
            "url": f"/catalog/receipt/{rec['id']}",
            "status": rec.get("status"),
            "meetup": rec.get("metadata", {}).get("meetup")
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

    row, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    if user.get("PIN Hash") != hash_value(old_pin):
        flash("Old PIN is incorrect.", "error")
        return redirect(url_for("dashboard"))

    if user.get("Memorable Password") != memorable:
        flash("Memorable password is incorrect.", "error")
        return redirect(url_for("dashboard"))

    sheet.update_cell(row, 2, hash_value(new_pin))
    flash("PIN updated successfully.", "success")
    return redirect(url_for("dashboard"))

# ====== Manage Account: Change Memorable Password ======
@app.route("/change_memorable", methods=["POST"])
def change_memorable():
    if "trainer" not in session:
        return redirect(url_for("home"))

    old_memorable = request.form["old_memorable"]
    new_memorable = request.form["new_memorable"]

    row, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    if user.get("Memorable Password") != old_memorable:
        flash("Old memorable password is incorrect.", "error")
        return redirect(url_for("dashboard"))

    sheet.update_cell(row, 3, new_memorable)
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
    row, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    if confirm_name.lower() != session["trainer"].lower():
        flash("Trainer name does not match. Account not deleted.", "error")
        return redirect(url_for("dashboard"))

    sheet.delete_rows(row)
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
    total_stamps, stamps, most_recent_stamp = get_passport_stamps(username, campfire_username)
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

    return render_template(
        "passport.html",
        trainer=username,
        stamps=stamps,
        passports=passports,
        total_stamps=total_stamps,
        current_stamps=current_stamps,
        most_recent_stamp=most_recent_stamp,
        lugia_summary=lugia_summary,
        show_back=False,
    )

# ====== Meet-up History ======
@app.route("/meetup_history")
def meetup_history():
    session["last_page"] = request.path
    if "trainer" not in session:
        flash("Please log in to view your meet-up history.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]
    row, user = find_user(trainer)
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

def _featured_slice(items, max_count=5):
    """Prefer items tagged 'featured'; else take most recent."""
    if not items:
        return []
    featured = [i for i in items if any((t or "").lower() == "featured" for t in _safe_list(i.get("tags")))]
    pool = featured or items
    return pool[:max_count]

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
    return ids

def _watchlist_add(trainer: str, item_id: str) -> None:
    """Add to watchlist both in Supabase (best effort) and session."""
    # Session mirror
    existing = set(_safe_list(session.get("watchlist")))
    existing.add(item_id)
    session["watchlist"] = list(existing)

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
    remaining = [i for i in _safe_list(session.get("watchlist")) if str(i) != str(item_id)]
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

    # Featured carousel (up to 5)
    featured_items = _featured_slice(items, 5)

    # Build categories → items map (reusing your constants)
    categories = {label: [] for label in CATEGORY_ORDER}
    for it in items:
        categories.setdefault(it["_cat"], []).append(it)

    # Watchlist state (badge + modal)
    trainer = session.get("trainer")
    watch_ids = _get_watchlist_ids(trainer) if trainer else []

    # Simple page description for the hero
    catalog_description = "Short description goes here"

    return render_template(
        "catalog.html",
        featured_items=featured_items,
        items=items,
        categories=categories,
        category_order=CATEGORY_ORDER,
        watch_ids=watch_ids,
        catalog_description=catalog_description,
        show_back=False
    )

# ========= Watchlist & Orders API=========

@app.post("/watchlist/toggle/<item_id>")
def watchlist_toggle(item_id):
    if "trainer" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 403
    trainer = session["trainer"]

    current = set(_get_watchlist_ids(trainer))
    if item_id in current:
        _watchlist_remove(trainer, item_id)
        return jsonify({"success": True, "watched": False})
    else:
        _watchlist_add(trainer, item_id)
        return jsonify({"success": True, "watched": True})

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

    return jsonify({
        "success": True,
        "count": len(rows),
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
        if not latest.get("active", False) or int(latest.get("stock") or 0) <= 0:
            flash("This prize just went out of stock or offline.", "warning")
            return redirect(url_for("catalog"))
    except Exception as e:
        print("⚠️ redeem: recheck failed:", e)

    # Deduct stamps via Lugia (ledger)
    cost = item["cost_stamps"]
    reason = f"Catalog Redemption: {item.get('name')}"
    lugia_msg = adjust_stamps(trainer, cost, reason, "remove")
    if "✅" not in lugia_msg:
        flash("Could not deduct stamps. Try again in a moment.", "error")
        return redirect(url_for("catalog_redeem", item_id=item_id))

    # Update balance mirror (best effort)
    try:
        new_balance = max(0, balance - cost)
        supabase.table("sheet1").update({"stamps": new_balance}).eq("trainer_username", trainer).execute()
    except Exception as e:
        print("⚠️ redeem: mirror stamp update failed:", e)

    # Decrement stock (only once)
    try:
        supabase.table("catalog_items") \
            .update({"stock": max(0, int(item["stock"]) - 1)}) \
            .eq("id", item_id) \
            .execute()
    except Exception as e:
        print("⚠️ redeem: stock update failed:", e)

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

        # === Update in Google Sheets ===
        try:
            # Find row in Sheet1
            all_users = sheet.get_all_records()
            row_index = None
            for i, record in enumerate(all_users, start=2):  # header row is 1
                if record.get("Trainer Username", "").lower() == session["trainer"].lower():
                    row_index = i
                    break
            if row_index:
                sheet.update_cell(row_index, 7, avatar_choice)       # G = Avatar Icon
                sheet.update_cell(row_index, 8, background_choice)   # H = Trainer Card Background
        except Exception as e:
            print("⚠️ Failed updating Google Sheets avatar/background:", e)

        # === Update in Supabase ===
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
            return {"account_type": user.get("account_type", "Standard")}
    return {"account_type": "Guest"}

# ====== Entrypoint ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)