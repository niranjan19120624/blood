import os
import sqlite3
import random
from flask import Flask, render_template, request, jsonify
from twilio.rest import Client

app = Flask(__name__)

# ─── CONFIGURATION (all secrets from Render environment variables) ──────────
# Set these in Render Dashboard → Environment tab — NEVER hardcode them here.
ACCOUNT_SID = "ACa21ed3f32fc56aea0eae571481add246" 
AUTH_TOKEN = "8b1edc1064eff4bb4bbe262e3907ed78"
TWILIO_PHONE = "+19149282650"


# ─── DATABASE PATH ───────────────────────────────────────────────────────────
# Render free tier has an ephemeral filesystem; /tmp is writable but resets on
# redeploy. For permanent storage across deploys, attach a Render Disk (paid)
# and set the env var  DB_PATH=/data/database.db  pointing to the mount path.
DB_PATH = os.environ.get("DB_PATH", "/tmp/database.db")


def get_db():
    """Return a sqlite3 connection to the configured DB path."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS donors (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT    NOT NULL,
                blood TEXT    NOT NULL,
                phone TEXT    NOT NULL,
                city  TEXT,
                lat   REAL,
                lon   REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                patient    TEXT,
                blood      TEXT,
                hospital   TEXT,
                phone      TEXT,
                city       TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

init_db()


# ─── TWILIO HELPER ───────────────────────────────────────────────────────────
def send_sms(to_phone, message):
    if not all([ACCOUNT_SID, AUTH_TOKEN, TWILIO_PHONE]):
        print("⚠️  Twilio credentials not set — skipping SMS.")
        return False
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        client.messages.create(body=message, from_=TWILIO_PHONE, to=to_phone)
        print(f"✅ SMS sent to {to_phone}")
        return True
    except Exception as e:
        print(f"❌ Twilio Error: {e}")
        return False


# ─── FALLBACK CITY COORDINATES ───────────────────────────────────────────────
# Used only when the browser did NOT share GPS (permission denied / desktop)
CITY_COORDS = {
    "CHENNAI":     [13.0827, 80.2707],
    "MUMBAI":      [19.0760, 72.8777],
    "DELHI":       [28.6139, 77.2090],
    "BANGALORE":   [12.9716, 77.5946],
    "HYDERABAD":   [17.3850, 78.4867],
    "KOLKATA":     [22.5726, 88.3639],
    "PUDUCHERRY":  [11.9416, 79.8083],
    "PONDICHERRY": [11.9416, 79.8083],
    "COIMBATORE":  [11.0168, 76.9558],
    "MADURAI":     [ 9.9252, 78.1198],
    "TIRUNELVELI": [ 8.7139, 77.7567],
    "TRICHY":      [10.7905, 78.7047],
    "SALEM":       [11.6643, 78.1460],
}


# ─── PAGE ROUTES ─────────────────────────────────────────────────────────────

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/dashboard')
def home():
    return render_template('index.html')

@app.route('/donor_page')
def donor_page():
    return render_template('donor.html')

@app.route('/receiver_page')
def receiver_page():
    return render_template('receiver.html')

@app.route('/map_page')
def map_page():
    return render_template('map.html')


# ─── API: REGISTER DONOR ─────────────────────────────────────────────────────

@app.route('/api/register_donor', methods=['POST'])
def reg_donor():
    data = request.json or {}
    city = data.get('city', '').strip()

    lat = data.get('lat')
    lon = data.get('lon')

    if lat is None or lon is None:
        base = CITY_COORDS.get(city.upper(), [20.5937, 78.9629])
        lat  = base[0] + random.uniform(-0.05, 0.05)
        lon  = base[1] + random.uniform(-0.05, 0.05)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO donors (name, blood, phone, city, lat, lon) VALUES (?,?,?,?,?,?)",
            (
                data.get('name', '').strip(),
                data.get('blood', '').upper().strip(),
                data.get('phone', '').strip(),
                city,
                lat,
                lon
            )
        )
    return jsonify({"status": "success"})


# ─── API: REQUEST BLOOD + SMS BROADCAST ──────────────────────────────────────

@app.route('/api/request_blood', methods=['POST'])
def reg_request():
    data         = request.json or {}
    blood_needed = data.get('blood', '').upper().strip()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO requests (patient, blood, hospital, phone, city) VALUES (?,?,?,?,?)",
            (data.get('patient'), blood_needed, data.get('hospital'), data.get('phone'), data.get('city'))
        )

    with get_db() as conn:
        donors = conn.execute(
            "SELECT phone FROM donors WHERE blood = ?", (blood_needed,)
        ).fetchall()

    count = 0
    for donor in donors:
        msg = (
            f"🚨 URGENT: {blood_needed} blood needed for {data.get('patient')} "
            f"at {data.get('hospital')}, {data.get('city')}. "
            f"Emergency contact: {data.get('phone')}"
        )
        if send_sms(donor['phone'], msg):
            count += 1

    return jsonify({"status": "success", "donors_notified": count})


# ─── API: GET DONORS FOR MAP (filtered) ──────────────────────────────────────

@app.route('/api/get_donors')
def get_donors():
    blood = request.args.get("blood", "").upper().strip()
    city  = request.args.get("city",  "").strip()

    if not blood:
        return jsonify([])

    with get_db() as conn:
        if city:
            rows = conn.execute(
                "SELECT id, name, blood, phone, city, lat, lon "
                "FROM donors WHERE blood = ? AND UPPER(city) = UPPER(?)",
                (blood, city)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, blood, phone, city, lat, lon "
                "FROM donors WHERE blood = ?",
                (blood,)
            ).fetchall()

    return jsonify([dict(r) for r in rows])


# ─── API: ALL DONORS (default map view + dashboard counter) ──────────────────

@app.route('/api/all_donors')
def all_donors():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, blood, phone, city, lat, lon FROM donors"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ─── HEALTH CHECK (Render pings this to confirm the service is up) ───────────

@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200


# ─── ENTRYPOINT ──────────────────────────────────────────────────────────────
# Gunicorn is the production server on Render.
# The `if __name__` block is only used for local dev: `python app.py`
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
