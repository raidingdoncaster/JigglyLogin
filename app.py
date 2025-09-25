import os
import json
import hashlib
import gspread
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g
from google.oauth2.service_account import Credentials
from werkzeug.utils import secure_filename
from PIL import Image
import pytesseract
from datetime import datetime
import io, base64, time

# ====== Feature toggle ======
USE_SUPABASE = True  # ✅ Supabase for stamps/meetups (Sheets fallback). Flip to False to force Sheets only.

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

# ====== Helpers ======
def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def get_all_users():
    """Cache Sheet1 records per-request so we only fetch once."""
    if not hasattr(g, "user_records"):
        g.user_records = sheet.get_all_records()
    return g.user_records

def find_user(username):
    """Find a trainer in Sheet1 (case-insensitive) and return (row_index, record)."""
    records = get_all_users()
    for i, record in enumerate(records, start=2):  # header row = 1
        if record.get("Trainer Username", "").lower() == str(username).lower():
            record.setdefault("Avatar Icon", "avatar1.png")
            record.setdefault("Trainer Card Background", "default.png")
            return i, record
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

# ====== Data: stamps & meetups ======
def get_passport_stamps(username: str, campfire_username: str | None = None):
    if USE_SUPABASE and supabase:
        try:
            q = supabase.table("lugia_ledger").select("*").eq("trainer", username)
            resp = q.execute()
            records = resp.data or []
            if (not records) and campfire_username:
                resp = supabase.table("lugia_ledger").select("*").eq("campfire", campfire_username).execute()
                records = resp.data or []

            ev_rows = supabase.table("events").select("event_id, cover_photo_url").execute().data or []
            event_map = {str(e.get("event_id", "")).strip().lower(): (e.get("cover_photo_url") or "") for e in ev_rows}

            stamps, total_count = [], 0
            for r in records:
                reason = (r.get("reason") or "").strip()
                count = int(r.get("count") or 1)
                total_count += count
                event_id = str(r.get("eventid") or "").strip().lower()

                rl = reason.lower()
                if rl == "signup bonus":
                    icon = url_for("static", filename="icons/signup.png")
                elif "cdl" in rl:
                    icon = url_for("static", filename="icons/cdl.png")
                elif "win" in rl:
                    icon = url_for("static", filename="icons/win.png")
                elif event_id and event_id in event_map and event_map[event_id]:
                    icon = event_map[event_id]
                else:
                    icon = url_for("static", filename="icons/tickstamp.png")

                stamps.append({"name": reason, "count": count, "icon": icon})

            most_recent = stamps[-1] if stamps else None
            return total_count, stamps, most_recent
        except Exception as e:
            print("⚠️ Supabase get_passport_stamps failed:", e)

    # Sheets fallback
    ledger_ws = gclient.open("POGO Passport Sign-Ins").worksheet("Lugia_Ledger")
    events_ws = gclient.open("POGO Passport Sign-Ins").worksheet("events")
    ledger_records = ledger_ws.get_all_records()
    event_records = events_ws.get_all_records()

    event_map = {
        str(r.get("event_id", "")).strip().lower(): str(r.get("cover_photo_url", "")).strip()
        for r in event_records if r.get("event_id")
    }

    stamps, total_count, most_recent = [], 0, None
    u = username.strip().lower()
    c = (campfire_username or "").strip().lower()

    for r in ledger_records:
        trainer = str(r.get("Trainer", "")).strip().lower()
        campfire = str(r.get("Campfire", "")).strip().lower()
        if not (trainer == u or (c and campfire == c)):
            continue

        reason = str(r.get("Reason", "")).strip()
        count = int(r.get("Count", 1))
        total_count += count
        event_id = str(r.get("EventID", "")).strip().lower()

        rl = reason.lower()
        if rl == "signup bonus":
            icon = url_for("static", filename="icons/signup.png")
        elif "cdl" in rl:
            icon = url_for("static", filename="icons/cdl.png")
        elif "win" in rl:
            icon = url_for("static", filename="icons/win.png")
        elif event_id in event_map and event_map[event_id]:
            icon = event_map[event_id]
        else:
            icon = url_for("static", filename="icons/tickstamp.png")

        stamp = {"name": reason, "count": count, "icon": icon}
        stamps.append(stamp)
        most_recent = stamp

    return total_count, stamps, most_recent

def get_most_recent_meetup(username: str, campfire_username: str | None = None):
    if USE_SUPABASE and supabase:
        try:
            rec = None
            r1 = supabase.table("lugia_summary").select("*").eq("trainer_username", username).limit(1).execute().data
            if r1:
                rec = r1[0]
            elif campfire_username:
                r2 = supabase.table("lugia_summary").select("*").eq("campfire_username", campfire_username).limit(1).execute().data
                if r2:
                    rec = r2[0]

            if rec:
                title = rec.get("most_recent_event", "") or rec.get("Most Recent Event", "")
                date = rec.get("most_recent_event_date", "") or rec.get("Most Recent Event Date", "")
                eid = (rec.get("most_recent_event_id") or "").strip()
                if not eid:
                    ev_ids = str(rec.get("event_ids", "")).strip()
                    if ev_ids:
                        eid = ev_ids.split(",")[-1].strip()
                eid_l = eid.lower()

                ev_rows = supabase.table("events").select("event_id, cover_photo_url").execute().data or []
                event_map = {str(e.get("event_id", "")).lower(): (e.get("cover_photo_url") or "") for e in ev_rows}
                return {"title": title, "date": date, "icon": event_map.get(eid_l, ""), "event_id": eid_l}
        except Exception as e:
            print("⚠️ Supabase get_most_recent_meetup failed:", e)

    # Sheets fallback
    summary_ws = gclient.open("POGO Passport Sign-Ins").worksheet("Lugia_Summary")
    events_ws = gclient.open("POGO Passport Sign-Ins").worksheet("events")
    s_rows = summary_ws.get_all_records()
    e_rows = events_ws.get_all_records()
    e_map = {str(e.get("event_id", "")).strip().lower(): str(e.get("cover_photo_url", "")).strip() for e in e_rows}

    u = username.strip().lower()
    c = (campfire_username or "").strip().lower()
    for r in s_rows:
        ru = str(r.get("Trainer Username", "")).strip().lower()
        rc = str(r.get("Campfire Username", "")).strip().lower()
        if not (ru == u or (c and rc == c)):
            continue
        title = r.get("Most Recent Event", "")
        date = r.get("Most Recent Event Date", "")
        eid = str(r.get("Most Recent Event ID", "") or "").strip().lower()
        return {"title": title, "date": date, "icon": e_map.get(eid, ""), "event_id": eid}

    return {"title": "", "date": "", "icon": "", "event_id": ""}

# ====== Routes ======
@app.route("/")
def home():
    return render_template("login.html")


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

        session["signup_details"] = {"trainer_name": trainer_name, "pin": pin, "memorable": memorable}
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
            # Prevent duplicates
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
        trainer_name = details["trainer_name"].strip()

        if choice == "13plus":
            flash("✅ Great! You’re signing up as 13 or older.", "success")
            return redirect(url_for("campfire"))

        elif choice == "under13":
            exists = False
            if supabase:
                resp = supabase.table("sheet1").select("trainer_username").ilike("trainer_username", trainer_name).execute()
                if resp.data:
                    exists = True

            records = sheet.get_all_records()
            for record in records:
                if record.get("Trainer Username", "").lower() == trainer_name.lower():
                    exists = True
                    break

            if exists:
                flash("This trainer name is already registered. Please log in instead.", "error")
                session.pop("signup_details", None)
                return redirect(url_for("home"))

            try:
                if supabase:
                    supabase.table("sheet1").insert({
                        "trainer_username": trainer_name,
                        "pin_hash": hash_value(details["pin"]),
                        "memorable_password": details["memorable"],
                        "last_login": datetime.utcnow().isoformat(),
                        "campfire_username": "Kids Account",
                        "stamps": 0,
                        "avatar_icon": "avatar1.png",
                        "trainer_card_background": "default.png",
                        "account_type": "Kids Account",
                    }).execute()
            except Exception as e:
                print("⚠️ Supabase insert failed:", e)

            try:
                sheet.append_row([
                    trainer_name,
                    hash_value(details["pin"]),
                    details["memorable"],
                    datetime.utcnow().isoformat(),
                    "Kids Account",   # Column E
                    "0",              # Column F
                    "avatar1.png",    # Column G
                    "default.png",    # Column H
                    "Kids Account"    # Column I
                ])
            except Exception as e:
                print("⚠️ Sheets insert failed:", e)

            trigger_lugia_refresh()
            session.pop("signup_details", None)
            flash("👶 Kids Account created successfully!", "success")
            return redirect(url_for("home"))

        else:
            flash("Please select an option.", "warning")

    return render_template("age.html")


# ====== Campfire Username Step ======
@app.route("/campfire", methods=["GET", "POST"])
def campfire():
    details = session.get("signup_details")
    if not details:
        flash("Session expired. Please try signing up again.", "warning")
        return redirect(url_for("signup"))

    if request.method == "POST":
        campfire_username = request.form.get("campfire_username", "").strip()
        if not campfire_username:
            flash("Campfire username is required.", "warning")
            return redirect(url_for("campfire"))

        trainer_name = details["trainer_name"].strip()

        exists = False
        if supabase:
            resp = supabase.table("sheet1").select("trainer_username").ilike("trainer_username", trainer_name).execute()
            if resp.data:
                exists = True

        records = sheet.get_all_records()
        for record in records:
            if record.get("Trainer Username", "").lower() == trainer_name.lower():
                exists = True
                break

        if exists:
            flash("This trainer name is already registered. Please log in instead.", "error")
            session.pop("signup_details", None)
            return redirect(url_for("home"))

        try:
            if supabase:
                supabase.table("sheet1").insert({
                    "trainer_username": trainer_name,
                    "pin_hash": hash_value(details["pin"]),
                    "memorable_password": details["memorable"],
                    "last_login": datetime.utcnow().isoformat(),
                    "campfire_username": campfire_username,
                    "stamps": 0,
                    "avatar_icon": "avatar1.png",
                    "trainer_card_background": "default.png",
                    "account_type": "Standard",
                }).execute()
        except Exception as e:
            print("⚠️ Supabase insert failed:", e)

        try:
            sheet.append_row([
                trainer_name,
                hash_value(details["pin"]),
                details["memorable"],
                datetime.utcnow().isoformat(),
                campfire_username,  # Column E
                "",                 # Column F
                "avatar1.png",      # Column G
                "default.png",      # Column H
                "Standard"          # Column I
            ])
        except Exception as e:
            print("⚠️ Sheets insert failed:", e)

        trigger_lugia_refresh()
        session.pop("signup_details", None)
        flash("Signup successful! Please log in.", "success")
        return redirect(url_for("home"))

    return render_template("campfire.html")

# ====== Login ======
@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"].strip()
    pin = request.form["pin"].strip()

    if not supabase:
        flash("⚠️ Supabase not available. Please try again later.", "error")
        return redirect(url_for("home"))

    try:
        # Fetch user from Supabase (case-insensitive match)
        resp = supabase.table("sheet1").select("*").ilike("trainer_username", username).limit(1).execute()
        if not resp.data:
            flash("No trainer found!", "error")
            return redirect(url_for("home"))

        user = resp.data[0]

        # Verify PIN
        if user.get("pin_hash") != hash_value(pin):
            flash("Incorrect PIN!", "error")
            return redirect(url_for("home"))

        # ✅ Success: log them in
        session["trainer"] = user.get("trainer_username")

        # Update last_login
        supabase.table("sheet1").update({"last_login": datetime.utcnow().isoformat()}) \
            .eq("trainer_username", user.get("trainer_username")).execute()

        flash(f"Welcome back, {user.get('trainer_username')}!", "success")
        return redirect(url_for("dashboard"))

    except Exception as e:
        print("⚠️ Supabase login error:", e)
        flash("Login failed. Please try again.", "error")
        return redirect(url_for("home"))


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
    row, user = find_user(trainer)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("home"))

    campfire_username = user.get("Campfire Username", "")

    total_stamps, stamps, most_recent_stamp = get_passport_stamps(trainer, campfire_username)
    try:
        current_stamps = int(user.get("Stamps", 0) or 0)
    except Exception:
        current_stamps = 0

    most_recent_meetup = get_most_recent_meetup(trainer, campfire_username)

    return render_template(
        "dashboard.html",
        trainer=trainer,
        stamps=stamps,
        total_stamps=total_stamps,
        current_stamps=current_stamps,
        avatar=user.get("Avatar Icon", "avatar1.png"),
        background=user.get("Trainer Card Background", "default.png"),
        campfire_username=campfire_username,
        most_recent_meetup=most_recent_meetup,
        show_back=False,
    )


# ====== Inbox (placeholder) ======
@app.route("/inbox")
def inbox():
    if "trainer" not in session:
        flash("Please log in to view your inbox.", "warning")
        return redirect(url_for("home"))

    inbox_messages = [
        {"subject": "🎉 You earned a new stamp!", "date": "2025-09-20", "content": "Congrats on your check-in!"},
        {"subject": "📅 New meetup near you", "date": "2025-09-19", "content": "Join us this weekend."},
        {"subject": "🎁 Claim your reward", "date": "2025-09-18", "content": "You unlocked a reward!"},
    ]
    return render_template("inbox.html", trainer=session["trainer"], inbox=inbox_messages, show_back=True)


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

    # Pull Campfire Username from Sheet1
    user_rows = sheet.get_all_records()
    campfire_username = None
    for r in user_rows:
        if str(r.get("Trainer Username", "")).lower() == username.lower():
            campfire_username = r.get("Campfire Username", "")
            break

    total_stamps, stamps, most_recent_stamp = get_passport_stamps(username, campfire_username)
    current_stamps = len(stamps)
    passports = [stamps[i:i + 12] for i in range(0, len(stamps), 12)]

    return render_template(
        "passport.html",
        trainer=username,
        stamps=stamps,
        passports=passports,
        total_stamps=total_stamps,
        current_stamps=current_stamps,
        most_recent_stamp=most_recent_stamp,
        show_back=True,
    )


# ====== Check-ins (placeholder) ======
@app.route("/checkins")
def checkins():
    if "trainer" not in session:
        flash("Please log in to view your check-ins.", "warning")
        return redirect(url_for("home"))

    events = [
        {"name": "Max Finale: Eternatus", "date": "2025-07-23"},
        {"name": "Wild Area Community Day", "date": "2025-08-15"},
    ]
    return render_template("checkins.html", trainer=session["trainer"], events=events, show_back=True)


# ====== Prizes (placeholder) ======
@app.route("/prizes")
def prizes():
    if "trainer" not in session:
        flash("Please log in to view your prizes.", "warning")
        return redirect(url_for("home"))

    prizes = [
        {"item": "GO Fest T-shirt", "date": "2025-07-23"},
        {"item": "Festival Wristband", "date": "2025-08-15"},
    ]
    return render_template("prizes.html", trainer=session["trainer"], prizes=prizes, show_back=True)


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

    row, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        avatar_choice = request.form.get("avatar_choice")
        background_choice = request.form.get("background_choice")

        valid_avatars = [f"avatar{i}.png" for i in range(1, 13)]
        if avatar_choice not in valid_avatars:
            flash("Invalid avatar choice.", "error")
            return redirect(url_for("change_avatar"))

        # validate background from /static/backgrounds
        backgrounds_folder = os.path.join(app.root_path, "static", "backgrounds")
        valid_backgrounds = os.listdir(backgrounds_folder)
        if background_choice not in valid_backgrounds:
            flash("Invalid background choice.", "error")
            return redirect(url_for("change_avatar"))

        # Update in Sheet1
        sheet.update_cell(row, 7, avatar_choice)       # G = Avatar Icon
        sheet.update_cell(row, 8, background_choice)   # H = Trainer Card Background

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "avatar": avatar_choice, "background": background_choice})

        flash("✅ Appearance updated successfully!", "success")
        return redirect(url_for("dashboard"))

    avatars = [f"avatar{i}.png" for i in range(1, 13)]
    backgrounds_folder = os.path.join(app.root_path, "static", "backgrounds")
    backgrounds = os.listdir(backgrounds_folder)

    current_avatar = user.get("Avatar Icon", "avatar1.png")
    current_background = user.get("Trainer Card Background") or "default.png"

    return render_template(
        "change_avatar.html",
        avatars=avatars,
        backgrounds=backgrounds,
        current_avatar=current_avatar,
        current_background=current_background,
    )


# ====== Entrypoint ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)