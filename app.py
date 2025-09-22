import os
import json
import hashlib
import gspread
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash
from google.oauth2.service_account import Credentials
from werkzeug.utils import secure_filename
from PIL import Image
import pytesseract
from datetime import datetime
import io, base64

# ==== Flask setup ====
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Uploads folder setup (temporary screenshots for OCR)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ==== Google Sheets setup ====
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Load service account creds from environment variable
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]),
    scopes=SCOPES
)

client = gspread.authorize(creds)
sheet = client.open("POGO Passport Sign-Ins").sheet1

# ==== Helpers ====
def hash_value(value: str) -> str:
    """Hash sensitive values securely with SHA256."""
    return hashlib.sha256(value.encode()).hexdigest()

def find_user(username):
    """Find a trainer in the sheet (case-insensitive) and return their row + record."""
    records = sheet.get_all_records()
    for i, record in enumerate(records, start=2):  # row 1 = header
        if record.get("Trainer Username", "").lower() == username.lower():
            # Ensure Avatar field always exists
            if "Avatar" not in record or not record["Avatar"]:
                record["Avatar"] = "avatar1.png"  # default avatar
            return i, record
    return None, None

def extract_trainer_name(image_path):
    """Extract trainer name from uploaded screenshot using OCR with cropping."""
    try:
        img = Image.open(image_path)
        width, height = img.size

        # Crop only the trainer name band
        top = int(height * 0.15)
        bottom = int(height * 0.25)
        left = int(width * 0.05)
        right = int(width * 0.90)
        cropped = img.crop((left, top, right, bottom))

        # OCR on cropped region
        text = pytesseract.image_to_string(cropped)
        print(f"🔍 OCR text (cropped): {text}")

        # Split into lines, keep only first non-empty
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return lines[0]  # trainer name always first
        return None
    except Exception as e:
        print(f"❌ OCR failed: {e}")
        return None

def trigger_lugia_refresh():
    """Call Lugia refresh Apps Script after signup."""
    url = "https://script.google.com/macros/s/AKfycbwx33Twu9HGwW4bsSJb7vwHoaBS56gCldNlqiNjxGBJEhckVDAnv520MN4ZQWxI1U9D/exec"
    params = {"action": "lugiaRefresh"}
    try:
        r = requests.get(url, params=params, timeout=10)
        print("Lugia response:", r.text)
    except Exception as e:
        print("Error calling Lugia:", e)

def get_passport_stamps(username):
    stamps = []

    # Pull ledger records
    ledger = client.open("POGO Passport Sign-Ins").worksheet("Lugia_Ledger")
    events = client.open("POGO Passport Sign-Ins").worksheet("events")

    ledger_records = ledger.get_all_records()
    event_records = events.get_all_records()

    # Event mapping: {name: cover_photo_url}
    event_map = {r["Name"]: r["cover_photo_url"] for r in event_records}

    for record in ledger_records:
        if record["Trainer Username"].lower() == username.lower():
            reason = record["Reason"]
            count = record["Count"]

            if reason.lower() == "signup bonus":
                icon = url_for("static", filename="avatars/avatar1.png")
            else:
                icon = event_map.get(reason, url_for("static", filename="avatars/avatar1.png"))

            stamps.append({"icon": icon, "name": reason, "count": count})

    return stamps

# ==== Routes ====
@app.route("/")
def home():
    return render_template("login.html")

# ==== Sign Up ====
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
            "memorable": memorable
        }

        return redirect(url_for("detectname"))

    return render_template("signup.html")

# ==== Confirm Detected Name ====
@app.route("/detectname", methods=["GET", "POST"])
def detectname():
    details = session.get("signup_details")
    if not details:
        flash("Session expired. Please try signing up again.", "warning")
        return redirect(url_for("signup"))

    if request.method == "POST":
        choice = request.form.get("choice")
        if choice == "yes":
            # ✅ Prevent duplicate usernames (case-insensitive)
            all_users = sheet.get_all_records()
            for record in all_users:
                if record.get("Trainer Username", "").lower() == details["trainer_name"].lower():
                    flash("This trainer name is already registered. Please log in instead.", "error")
                    session.pop("signup_details", None)
                    return redirect(url_for("home"))

            # Go to Age step next
            return redirect(url_for("age"))
        else:
            flash("Please upload a clearer screenshot with your trainer name visible.", "warning")
            session.pop("signup_details", None)
            return redirect(url_for("signup"))

    return render_template("detectname.html", trainer_name=details["trainer_name"])

# ==== Age Selection ====
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
            # Save user with Kids Account
            sheet.append_row([
                details["trainer_name"],
                hash_value(details["pin"]),
                details["memorable"],
                datetime.utcnow().isoformat(),
                "Kids Account",   # Column E
                "0",               # Column F (leave empty if unused)
                "avatar1.png"     # Column G (default avatar)
            ])
            trigger_lugia_refresh()
            session.pop("signup_details", None)
            flash("👶 Kids Account created successfully!", "success")
            return redirect(url_for("home"))
        else:
            flash("Please select an option.", "warning")

    return render_template("age.html")

# ==== Campfire Username Step ====
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

        # Save full signup to Google Sheet
        sheet.append_row([
            details["trainer_name"],
            hash_value(details["pin"]),
            details["memorable"],
            datetime.utcnow().isoformat(),
            campfire_username,  # Column E
            "",                 # Column F (leave empty if unused)
            "avatar1.png"       # Column G (default avatar)
        ])
        trigger_lugia_refresh()
        session.pop("signup_details", None)
        flash("Signup successful! Please log in.", "success")
        return redirect(url_for("home"))

    return render_template("campfire.html")

# ==== Login ====
@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"]
    pin = request.form["pin"]

    row, user = find_user(username)
    if not user:
        flash("No trainer found!", "error")
        return redirect(url_for("home"))

    if user.get("PIN Hash") == hash_value(pin):
        session["trainer"] = user.get("Trainer Username")  # preserve original case
        sheet.update_cell(row, 4, datetime.utcnow().isoformat())
        flash(f"Welcome back, {user.get('Trainer Username')}!", "success")
        return redirect(url_for("dashboard"))
    else:
        flash("Incorrect PIN!", "error")
        return redirect(url_for("home"))

# ==== Recover ====
@app.route("/recover", methods=["GET", "POST"])
def recover():
    if request.method == "POST":
        username = request.form.get("username")
        memorable = request.form.get("memorable")
        new_pin = request.form.get("new_pin")

        row, user = find_user(username)
        if not user:
            flash("❌ No trainer found with that name. Please check your spelling.", "error")
            return redirect(url_for("recover"))

        if user["Memorable Password"] != memorable:
            flash("⚠️ Memorable password does not match. Try again.", "error")
            return redirect(url_for("recover"))

        # Update PIN hash
        sheet.update_cell(row, 2, hash_value(new_pin))
        flash("✅ PIN successfully reset! You can now log in with your new PIN.", "success")
        return redirect(url_for("home"))

    return render_template("recover.html")

# ==== Dashboard ====
@app.route("/dashboard")
def dashboard():
    if "trainer" not in session:
        flash("You must be logged in to view the dashboard.", "warning")
        return redirect(url_for("home"))

    # ✅ Always reload fresh from Sheets
    row, user = find_user(session["trainer"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("home"))

    last_login = user.get("Last Login")
    campfire_username = user.get("Campfire Username", "")
    avatar = user.get("Avatar", "avatar1.png")  # Column G

    # Detect account type
    if campfire_username == "Kids Account":
        account_type = "Kids Account"
    else:
        account_type = "Standard Account"

    return render_template(
        "dashboard.html",
        trainer=session["trainer"],
        last_login=last_login,
        account_type=account_type,
        campfire_username=campfire_username,
        avatar=avatar,
        data=data,
        events=events,
        inbox=inbox
    )

# ==== Inbox ====
@app.route("/inbox")
def inbox():
    if "trainer" not in session:
        flash("Please log in to view your inbox.", "warning")
        return redirect(url_for("home"))

    # Placeholder inbox messages
    inbox_messages = [
        {"subject": "🎉 You earned a new stamp!", "date": "2025-09-20", "content": "Congrats on checking in at Wild Area. You’ve been awarded a stamp."},
        {"subject": "📅 New meetup near you", "date": "2025-09-19", "content": "Join us for the Doncaster meetup this weekend. RSVP now to secure your spot."},
        {"subject": "🎁 Claim your reward", "date": "2025-09-18", "content": "You’ve unlocked a T-shirt from your Passport progress! Collect at the next event."},
    ]

    return render_template("inbox.html", trainer=session["trainer"], inbox=inbox_messages, show_back=True)

# ==== Logout ====
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))

# ==== Manage Account: Change PIN ====
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

# ==== Manage Account: Change Memorable Password ====
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

# ==== Manage Account: Log Out Everywhere ====
@app.route("/logout_everywhere", methods=["POST"])
def logout_everywhere():
    if "trainer" not in session:
        return redirect(url_for("home"))

    session.clear()
    flash("You have been logged out everywhere.", "success")
    return redirect(url_for("home"))

# ==== Manage Account: Delete Account ====
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

# ==== Passport Progress ====
@app.route("/passport")
def passport():
    if "trainer" not in session:
        flash("Please log in to view your passport progress.", "warning")
        return redirect(url_for("home"))

    username = session["trainer"]

    # 🔍 Collect stamps from Lugia_Ledger
    records = client.open("POGO Passport Sign-Ins").worksheet("Lugia_Ledger").get_all_records()
    events = client.open("POGO Passport Sign-Ins").worksheet("events").get_all_records()

    stamps = []
    for record in records:
        if record.get("Trainer Username", "").lower() == username.lower():
            reason = record.get("Reason")
            count = record.get("Count", 1)

            # Default icon if not matched
            icon = url_for("static", filename="avatars/avatar1.png")

            if reason and reason.lower() != "signup bonus":
                # Try to match against events tab
                for ev in events:
                    if ev.get("Name", "").lower() == reason.lower():
                        icon = ev.get("cover_photo_url", icon)
                        break

            stamps.append({
                "name": reason,
                "count": count,
                "icon": icon
            })

    return render_template(
        "passport.html",
        trainer=username,
        stamps=stamps,
        show_back=True
    )

# ==== Event Check-ins ====
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

# ==== Recently Claimed Prizes ====
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

# ==== OCR Test (debug route) ====
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
            width, height = img.size

            # Crop to trainer name area (higher region, near top)
            top = int(height * 0.15)
            bottom = int(height * 0.25)
            left = int(width * 0.05)
            right = int(width * 0.90)
            cropped = img.crop((left, top, right, bottom))

            text = pytesseract.image_to_string(cropped)
            print(f"🔍 OCR TEST OUTPUT: {text}")

            buf = io.BytesIO()
            cropped.save(buf, format="PNG")
            base64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

            return f"""
            <h2>OCR Test Result</h2>
            <p><b>Detected Text:</b> {text}</p>
            <h3>Cropped Region:</h3>
            <img src="data:image/png;base64,{base64_img}" style="max-width:100%;border:1px solid #ccc;" />
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

from flask import jsonify

# ==== Change Avatar ====
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
        if not avatar_choice:
            flash("Please select an avatar.", "warning")
            return redirect(url_for("change_avatar"))

        # Only allow valid avatars
        valid_avatars = [f"avatar{i}.png" for i in range(1, 13)]
        if avatar_choice not in valid_avatars:
            flash("Invalid avatar choice.", "error")
            return redirect(url_for("change_avatar"))

        # Update in Google Sheet
        sheet.update_cell(row, 7, avatar_choice)

        # If AJAX, return JSON instead of redirect
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "avatar": avatar_choice})

        flash("✅ Avatar updated successfully!", "success")
        return redirect(url_for("dashboard"))

    avatars = [f"avatar{i}.png" for i in range(1, 13)]
    current_avatar = user.get("Avatar", "avatar1.png")
    return render_template("change_avatar.html", avatars=avatars, current_avatar=current_avatar)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)