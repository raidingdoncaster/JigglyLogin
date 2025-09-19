import os
import json
import hashlib
import gspread
from flask import Flask, render_template, request, redirect, url_for, session, flash
from google.oauth2.service_account import Credentials
from werkzeug.utils import secure_filename
from PIL import Image
import pytesseract
from datetime import datetime
from flask import send_file
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
        width, height = img.size

        # Crop region: only the band where the trainer name usually appears
        top = int(height * 0.25)   # skip top menus
        bottom = int(height * 0.50)  # stop before avatar/stats
        left = int(width * 0.10)   # leave a little margin
        right = int(width * 0.90)

        cropped = img.crop((left, top, right, bottom))

        # OCR on cropped area
        text = pytesseract.image_to_string(cropped)
        print(f"🔍 Cropped OCR text: {text}")  # debug in logs

        # Find candidate lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return lines[0]  # first valid line should be trainer name
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
            sheet.append_row([
                details["trainer_name"],
                hash_value(details["pin"]),
                details["memorable"],
                datetime.utcnow().isoformat()  # store signup time as last login
            ])
            session.pop("signup_details", None)
            flash("Signup successful! Please log in.", "success")
            return redirect(url_for("home"))
        else:
            flash("Please upload a clearer screenshot with your trainer name visible.", "warning")
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
        flash("No trainer found!", "error")
        return redirect(url_for("home"))

    if user.get("PIN Hash") == hash_value(pin):
        session["trainer"] = username
        sheet.update_cell(row, 4, datetime.utcnow().isoformat())
        flash(f"Welcome back, {username}!", "success")
        return redirect(url_for("dashboard"))
    else:
        flash("Incorrect PIN!", "error")
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
            flash("No trainer found with that username.", "error")
            return redirect(url_for("recover"))

        if user["Memorable Password"] != memorable:
            flash("Memorable password does not match.", "error")
            return redirect(url_for("recover"))

        sheet.update_cell(row, 2, hash_value(new_pin))
        flash("PIN successfully reset! Please log in.", "success")
        return redirect(url_for("home"))

    return render_template("recover.html")


# ==== Dashboard ====
@app.route("/dashboard")
def dashboard():
    if "trainer" not in session:
        flash("You must be logged in to view the dashboard.", "warning")
        return redirect(url_for("home"))

    row, user = find_user(session["trainer"])
    last_login = user.get("Last Login") if user else None

    return render_template(
        "dashboard.html",
        trainer=session["trainer"],
        last_login=last_login
    )


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

    if confirm_name != session["trainer"]:
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

    data = {"stamps": 5, "total": 10, "rewards": ["Sticker Pack", "Discount Band"]}
    return render_template("passport.html", trainer=session["trainer"], data=data, show_back=True)


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
            top = int(height * 0.10)      # start higher up
            bottom = int(height * 0.25)   # end before the middle
            left = int(width * 0.15)      # a little narrower from the left
            right = int(width * 0.85)     # a little narrower from the right
            cropped = img.crop((left, top, right, bottom))

            # OCR the cropped area
            text = pytesseract.image_to_string(cropped)
            print(f"🔍 OCR TEST OUTPUT: {text}")

            # Encode cropped image to base64 for inline display
            import io, base64
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)