import os
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

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ==== Google Sheets setup ====
SERVICE_ACCOUNT_FILE = "pogo-passport-key.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
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
    """Pulls the trainer name from a profile screenshot."""
    img = Image.open(image_path)
    text = pytesseract.image_to_string(img)

    # Debug: print everything it reads
    print("OCR Extracted Text:", text)

    # Pick the second non-empty line as trainer name
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines[1]  # the line with the trainer name
    return None

# ==== Routes ====
@app.route("/")
def home():
    return render_template("login.html")

# ==== Sign Up ====
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        pin = request.form["pin"].strip()
        memorable = request.form["memorable"].strip()
        file = request.files.get("screenshot")

        if not (pin and memorable and file and file.filename):
            flash("All fields required!")
            return redirect(url_for("signup"))

        # Save screenshot temporarily
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(file.filename))
        file.save(filepath)

        # Extract trainer name via OCR
        trainer_name = extract_trainer_name(filepath)

        # Delete screenshot after processing
        try:
            os.remove(filepath)
        except OSError:
            pass

        if not trainer_name:
            flash("Could not detect trainer name from screenshot. Please try again.")
            return redirect(url_for("signup"))

        # Send user to confirmation page
        return render_template("detectname.html", trainer_name=trainer_name, pin=pin, memorable=memorable)

    return render_template("signup.html")

# ==== Detects Name =====
@app.route("/detectname", methods=["GET", "POST"])
def detectname():
    if request.method == "POST":
        trainer_name = request.form["trainer_name"]
        pin = request.form["pin"]
        memorable = request.form["memorable"]

        # Check if trainer already exists
        row, existing = find_user(trainer_name)
        if existing:
            flash("Trainer already registered!")
            return redirect(url_for("signup"))

        # Save row in Google Sheets
        sheet.append_row([
            trainer_name,
            hash_value(pin),
            memorable
        ])

        flash(f"Signup successful! Welcome, {trainer_name}. Please log in.")
        return redirect(url_for("home"))

    # GET request just bounces back
    return redirect(url_for("signup"))

# ==== Login ====
@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"].strip()
    pin = request.form["pin"].strip()

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
        username = request.form["username"].strip()
        memorable = request.form["memorable"].strip()
        new_pin = request.form["new_pin"].strip()

        row, user = find_user(username)
        if not user:
            flash("No trainer found with that username.")
            return redirect(url_for("recover"))

        if user.get("Memorable Password") != memorable:
            flash("Memorable password does not match.")
            return redirect(url_for("recover"))

        # Update PIN hash
        sheet.update_cell(row, 2, hash_value(new_pin))  # col 2 = PIN Hash

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

if __name__ == "__main__":
    app.run(debug=True)