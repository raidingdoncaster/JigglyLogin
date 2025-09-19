import os
import json
import hashlib
import gspread
from flask import Flask, render_template, request, redirect, url_for, session, flash
from google.oauth2.service_account import Credentials
from werkzeug.utils import secure_filename
from PIL import Image
import pytesseract

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
    """Find a trainer in the sheet and return their row + record safely."""
    records = sheet.get_all_records()
    for i, record in enumerate(records, start=2):  # row 1 = header
        if record.get("Trainer Username") == username:
            return i, record
    return None, None


def extract_trainer_name(image_path):
    """Extract trainer name from uploaded screenshot using OCR."""
    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        print(f"🔍 OCR text: {text}")  # helpful for debugging
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2:
            return lines[1]  # second line usually contains trainer name
        return None
    except Exception as e:
        print(f"❌ OCR failed: {e}")
        return None


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
        file = request.files.get("profile_screenshot")  # fixed name

        if not (pin and memorable and file):
            flash("All fields required!")
            return redirect(url_for("signup"))

        # Save uploaded screenshot temporarily
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(file.filename))
        file.save(filepath)

        # OCR to detect trainer name
        trainer_name = extract_trainer_name(filepath)

        # Delete screenshot after OCR
        os.remove(filepath)

        if not trainer_name:
            flash("Could not detect trainer name from screenshot. Please try again.")
            return redirect(url_for("signup"))

        # Store details in session temporarily until user confirms
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
        return redirect(url_for("signup"))

    if request.method == "POST":
        choice = request.form.get("choice")
        if choice == "yes":
            # Save to Google Sheets
            sheet.append_row([
                details["trainer_name"],
                hash_value(details["pin"]),
                details["memorable"]
            ])
            session.pop("signup_details", None)
            flash("Signup successful! Please log in.")
            return redirect(url_for("home"))
        else:
            flash("Please upload a clearer screenshot with your trainer name visible.")
            session.pop("signup_details", None)
            return redirect(url_for("signup"))

    return render_template("detectname.html", trainer_name=details["trainer_name"])


# ==== Login ====
@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"]
    pin = request.form["pin"]

    row, user = find_user(username)
    if not user:
        flash("No trainer found!")
        return redirect(url_for("home"))

    if user.get("PIN Hash") == hash_value(pin):
        session["trainer"] = username
        return redirect(url_for("dashboard"))
    else:
        flash("Incorrect PIN!")
        return redirect(url_for("home"))


# ==== Recover ====
@app.route("/recover", methods=["GET", "POST"])
def recover():
    if request.method == "POST":
        username = request.form["username"]
        memorable = request.form["memorable"]
        new_pin = request.form["new_pin"]

        row, user = find_user(username)
        if not user:
            flash("No trainer found with that username.")
            return redirect(url_for("recover"))

        if user["Memorable Password"] != memorable:
            flash("Memorable password does not match.")
            return redirect(url_for("recover"))

        # Update PIN hash
        sheet.update_cell(row, 2, hash_value(new_pin))  # 2 = PIN Hash column

        flash("PIN successfully reset! Please log in.")
        return redirect(url_for("home"))

    return render_template("recover.html")


# ==== Dashboard ====
@app.route("/dashboard")
def dashboard():
    if "trainer" not in session:
        return redirect(url_for("home"))
    return render_template("dashboard.html", trainer=session["trainer"])


# ==== Logout ====
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# ==== Passport Progress ====
@app.route("/passport")
def passport():
    if "trainer" not in session:
        return redirect(url_for("home"))

    # Mock data (later: pull from Google Sheets)
    data = {
        "stamps": 5,
        "total": 10,
        "rewards": ["Sticker Pack", "Discount Band"]
    }
    return render_template("passport.html", trainer=session["trainer"], data=data)


# ==== Event Check-ins ====
@app.route("/checkins")
def checkins():
    if "trainer" not in session:
        return redirect(url_for("home"))

    # Mock data (later: pull from Google Sheets)
    events = [
        {"name": "Max Finale: Eternatus", "date": "2025-07-23"},
        {"name": "Wild Area Community Day", "date": "2025-08-15"},
    ]
    return render_template("checkins.html", trainer=session["trainer"], events=events)


# ==== Recently Claimed Prizes ====
@app.route("/prizes")
def prizes():
    if "trainer" not in session:
        return redirect(url_for("home"))

    # Mock data (later: pull from Google Sheets)
    prizes = [
        {"item": "GO Fest T-shirt", "date": "2025-07-23"},
        {"item": "Festival Wristband", "date": "2025-08-15"},
    ]
    return render_template("prizes.html", trainer=session["trainer"], prizes=prizes)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)