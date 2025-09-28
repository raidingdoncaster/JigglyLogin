import os
import json
import hashlib
import gspread
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify, g
from google.oauth2.service_account import Credentials
from werkzeug.utils import secure_filename
from PIL import Image
from pywebpush import webpush, WebPushException
import pytesseract
from datetime import datetime
import io, base64, time

# ====== Feature toggle ======
USE_SUPABASE = True  # ✅ Supabase for stamps/meetups

# Try to import Supabase client
try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client, Client = None, None

# ====== Flask setup ======
app = Flask(__name__)
app.secret_key = os.urandom(24)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

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
    """Fetch up to N most recent notifications for nav bar preview."""
    if not supabase:
        return []
    try:
        resp = (supabase.table("notifications")
                .select("subject, message, sent_at, read_by")
                .eq("audience", trainer)               # align with your /inbox() query
                .order("sent_at", desc=True)
                .limit(limit)
                .execute())
        return resp.data or []
    except Exception as e:
        print("⚠️ Supabase inbox preview fetch failed:", e)
        return []

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

# ===== Placeholder Admin Pages =====
@app.route("/admin/catalog")
def admin_catalog():
    return render_template("admin_placeholder.html", title="Catalog Manager")

@app.route("/admin/meetups")
def admin_meetups():
    return render_template("admin_placeholder.html", title="Catalog Meetup Manager")

@app.route("/admin/redemptions")
def admin_redemptions():
    return render_template("admin_placeholder.html", title="Redemptions Manager")

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
@app.route("/admin/stats")
def admin_stats():
    return render_template("admin_placeholder.html", title="RDAB Stats")

@app.route("/admin/notifications")
def admin_notifications():
    return render_template("admin_placeholder.html", title="Notifications Center")

# ====== Routes ======
@app.route("/")
def home():
    return render_template("login.html")

# ==== Login ====
@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"]
    pin = request.form["pin"]

    _, user = find_user(username)
    if not user:
        flash("No trainer found!", "error")
        return redirect(url_for("home"))

    if user.get("pin_hash") == hash_value(pin):
        session["trainer"] = user.get("trainer_username")
        # update last_login in Supabase
        try:
            supabase.table("sheet1") \
                .update({"last_login": datetime.utcnow().isoformat()}) \
                .eq("trainer_username", user.get("trainer_username")) \
                .execute()
        except Exception as e:
            print("⚠️ Supabase last_login update failed:", e)

        flash(f"Welcome back, {user.get('trainer_username')}!", "success")
        return redirect(url_for("dashboard"))
    else:
        flash("Incorrect PIN!", "error")
        return redirect(url_for("home"))

# ====== Sign Up ======
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

    if request.method == "POST":
        choice = request.form.get("choice")
        if choice == "yes":
            # ✅ Prevent duplicate usernames
            all_users = sheet.get_all_records()
            for record in all_users:
                if record.get("Trainer Username", "").lower() == details["trainer_name"].lower():
                    flash("This trainer name is already registered. Please log in instead.", "error")
                    session.pop("signup_details", None)
                    return redirect(url_for("home"))
            return redirect(url_for("age"))
        else:
            flash("Please upload a clearer screenshot with your trainer name visible.", "warning")
            session.pop("signup_details", None)
            return redirect(url_for("signup"))

    return render_template("detectname.html", trainer_name=details["trainer_name"])


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
                if r.get("Trainer Username", "").lower() == details["trainer_name"].lower():
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

# ====== Inbox ======
@app.route("/inbox")
def inbox():
    if "trainer" not in session:
        flash("Please log in to view your inbox.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]
    sort_by = request.args.get("sort", "newest")
    messages = []

    if USE_SUPABASE and supabase:
        try:
            query = supabase.table("notifications") \
                .select("*") \
                .eq("audience", trainer)

            # Apply filters
            if sort_by == "unread":
                query = query.not_.contains("read_by", [trainer])
            elif sort_by == "read":
                query = query.contains("read_by", [trainer])
            elif sort_by == "type":
                query = query.order("type", desc=False)

            # Default sort order
            if sort_by == "newest":
                query = query.order("sent_at", desc=True)
            elif sort_by == "oldest":
                query = query.order("sent_at", desc=False)

            resp = query.execute()
            messages = resp.data or []
        except Exception as e:
            print("⚠️ Supabase inbox fetch failed:", e)

    # fallback: no messages
    if not messages:
        messages = [{
            "subject": "📭 No messages yet",
            "message": "Your inbox is empty. You’ll see updates, receipts, and announcements here.",
            "sent_at": datetime.utcnow().isoformat(),
            "type": "info",
            "read_by": []
        }]

    return render_template(
        "inbox.html",
        trainer=trainer,
        inbox=messages,
        sort_by=sort_by,
        show_back=True
    )

# ====== Mark message as read ======
@app.route("/inbox/read/<message_id>")
def read_message(message_id):
    if "trainer" not in session:
        flash("Please log in to view your inbox.", "warning")
        return redirect(url_for("home"))

    trainer = session["trainer"]

    try:
        # Fetch the current message
        resp = supabase.table("notifications").select("*").eq("id", message_id).limit(1).execute()
        if resp.data:
            msg = resp.data[0]
            read_by = msg.get("read_by") or []

            # If trainer not already marked as read, append
            if trainer not in read_by:
                read_by.append(trainer)
                supabase.table("notifications").update({"read_by": read_by}).eq("id", message_id).execute()
    except Exception as e:
        print("⚠️ Failed to mark message read:", e)

    # Redirect back to inbox
    return redirect(url_for("inbox"))

# ====== Logout ======
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))

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
        return {"inbox_preview": get_inbox_preview(trainer)}
    return {"inbox_preview": []}

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