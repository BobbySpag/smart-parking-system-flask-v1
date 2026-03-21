from flask import Flask, jsonify, request, send_file, send_from_directory
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, text
from sqlalchemy.orm import sessionmaker, declarative_base
from flask_bcrypt import Bcrypt
from functools import wraps
import datetime
import jwt
import math
import os
import requests as http_requests

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-in-production")
bcrypt = Bcrypt(app)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")

# IMPORTANT FIX: SQLAlchemy expects postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

PAYSTACK_SECRET = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_API = "https://api.paystack.co"

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

Base = declarative_base()


class ParkingSlot(Base):
    __tablename__ = "parking_slots"

    id = Column(Integer, primary_key=True)
    location = Column(String)
    status = Column(String)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    price_per_hour = Column(Float, default=5.00)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    password_hash = Column(String)
    role = Column(String, default="user")
    balance = Column(Float, default=0.00)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    slot_id = Column(Integer)
    location = Column(String)
    hours = Column(Float)
    amount = Column(Float)
    booked_at = Column(String)  # ISO datetime string
    status = Column(String, default="active")  # active / completed


class Payment(Base):
    """Tracks processed Paystack payments — prevents double-crediting."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    reference = Column(String, unique=True)
    amount_ghs = Column(Float)
    paid_at = Column(String)  # ISO datetime string


Base.metadata.create_all(engine)


def _ensure_user_role_column():
    # Lightweight migration to support existing databases without the role column.
    with engine.connect() as conn:
        if DATABASE_URL.startswith("sqlite"):
            cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
            names = [row[1] for row in cols]
            if "role" not in names:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'user'"))
                conn.commit()
        else:
            cols = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'users'
                    """
                )
            ).fetchall()
            names = [row[0] for row in cols]
            if "role" not in names:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'user'"))
                conn.commit()


_ensure_user_role_column()


def _ensure_lat_lng_columns():
    with engine.connect() as conn:
        if DATABASE_URL.startswith("sqlite"):
            cols = conn.execute(text("PRAGMA table_info(parking_slots)")).fetchall()
            names = [row[1] for row in cols]
            if "lat" not in names:
                conn.execute(text("ALTER TABLE parking_slots ADD COLUMN lat FLOAT"))
                conn.execute(text("ALTER TABLE parking_slots ADD COLUMN lng FLOAT"))
                conn.commit()
        else:
            cols = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'parking_slots'")
            ).fetchall()
            names = [row[0] for row in cols]
            if "lat" not in names:
                conn.execute(text("ALTER TABLE parking_slots ADD COLUMN lat FLOAT"))
                conn.execute(text("ALTER TABLE parking_slots ADD COLUMN lng FLOAT"))
                conn.commit()


_ensure_lat_lng_columns()


def _ensure_money_columns():
    with engine.connect() as conn:
        is_sqlite = DATABASE_URL.startswith("sqlite")
        if is_sqlite:
            slot_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(parking_slots)")).fetchall()]
            user_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()]
            if "price_per_hour" not in slot_cols:
                conn.execute(text("ALTER TABLE parking_slots ADD COLUMN price_per_hour FLOAT DEFAULT 5.0"))
                conn.commit()
            if "balance" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN balance FLOAT DEFAULT 0.0"))
                conn.commit()
        else:
            slot_cols = [r[0] for r in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='parking_slots'")
            ).fetchall()]
            user_cols = [r[0] for r in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
            ).fetchall()]
            if "price_per_hour" not in slot_cols:
                conn.execute(text("ALTER TABLE parking_slots ADD COLUMN price_per_hour FLOAT DEFAULT 5.0"))
                conn.commit()
            if "balance" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN balance FLOAT DEFAULT 0.0"))
                conn.commit()


_ensure_money_columns()


_SEED_SLOTS = [
    {"location": "Accra Mall, Spintex",        "lat": 5.6360, "lng": -0.1669, "price": 8.00},
    {"location": "Junction Mall, Nungua",       "lat": 5.6195, "lng": -0.2080, "price": 6.00},
    {"location": "West Hills Mall, Weija",      "lat": 5.5892, "lng": -0.2731, "price": 5.00},
    {"location": "Kotoka International Airport","lat": 5.6052, "lng": -0.1718, "price": 15.00},
    {"location": "Osu Oxford Street",           "lat": 5.5600, "lng": -0.1858, "price": 7.00},
    {"location": "University of Ghana, Legon",  "lat": 5.6502, "lng": -0.1870, "price": 4.00},
    {"location": "Kaneshie Market",             "lat": 5.5714, "lng": -0.2328, "price": 3.00},
    {"location": "Labadi Beach",                "lat": 5.5565, "lng": -0.1467, "price": 5.00},
    {"location": "Tema Station, Accra Central", "lat": 5.5500, "lng": -0.2050, "price": 4.00},
    {"location": "A&C Mall, East Legon",        "lat": 5.6381, "lng": -0.1558, "price": 10.00},
]


def _seed_slots():
    existing_locations = {s.location for s in session.query(ParkingSlot.location).all()}
    added = False
    for s in _SEED_SLOTS:
        if s["location"] not in existing_locations:
            session.add(ParkingSlot(
                location=s["location"], status="free",
                lat=s["lat"], lng=s["lng"],
                price_per_hour=s.get("price", 5.00)
            ))
            added = True
    if added:
        session.commit()


_seed_slots()


def _decode_token():
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "", 1).strip() if auth_header else ""

    if not token:
        return None, (jsonify({"message": "Token missing"}), 401)

    try:
        payload = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        return payload, None
    except Exception:
        return None, (jsonify({"message": "Invalid token"}), 401)


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        _, err = _decode_token()
        if err:
            return err

        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        payload, err = _decode_token()
        if err:
            return err

        user = session.get(User, payload.get("user_id"))
        if not user or user.role != "admin":
            return jsonify({"message": "Admin access required"}), 403

        return f(*args, **kwargs)

    return decorated


@app.route("/auth/register", methods=["POST"])
def register_user():
    data = request.json or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))

    if not username or not password:
        return jsonify({"message": "username and password are required"}), 400

    existing = session.query(User).filter_by(username=username).first()
    if existing:
        return jsonify({"message": "username already exists"}), 409

    hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")
    # First account becomes admin so the dashboard can be used immediately.
    existing_admin = session.query(User).filter_by(role="admin").first()
    role = "admin" if existing_admin is None else "user"

    user = User(username=username, password_hash=hashed_pw, role=role)

    session.add(user)
    session.commit()

    return jsonify({"message": "User registered successfully", "role": role})


@app.route("/auth/login", methods=["POST"])
def login_user():
    data = request.json or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))

    user = session.query(User).filter_by(username=username).first()

    if user and bcrypt.check_password_hash(user.password_hash, password):
        token = jwt.encode(
            {
                "user_id": user.id,
                "username": user.username,
                "role": user.role or "user",
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=2),
            },
            app.config["SECRET_KEY"],
            algorithm="HS256",
        )
        return jsonify({"token": token, "role": user.role or "user", "username": user.username})

    return jsonify({"message": "Invalid credentials"}), 401


@app.route("/auth/me", methods=["GET"])
@token_required
def auth_me():
    payload, err = _decode_token()
    if err:
        return err

    user = session.get(User, payload.get("user_id"))
    if not user:
        return jsonify({"message": "User not found"}), 404

    return jsonify({
        "id": user.id,
        "username": user.username,
        "role": user.role or "user",
        "balance": round(user.balance or 0.0, 2),
    })


@app.route("/slots", methods=["GET"])
def get_slots():
    slots = session.query(ParkingSlot).all()
    result = [
        {
            "id": s.id,
            "location": s.location,
            "status": s.status,
            "lat": s.lat,
            "lng": s.lng,
            "price_per_hour": s.price_per_hour or 5.0,
        }
        for s in slots
    ]
    return jsonify(result)


@app.route("/add-slot", methods=["POST"])
@token_required
def add_slot():
    data = request.json or {}
    if "location" not in data:
        return jsonify({"message": "location is required"}), 400

    new_slot = ParkingSlot(
        location=data["location"],
        status="free",
        lat=data.get("lat"),
        lng=data.get("lng"),
    )
    session.add(new_slot)
    session.commit()
    return jsonify({"message": "Slot added", "id": new_slot.id}), 201


@app.route("/admin/add-slot", methods=["POST"])
@admin_required
def admin_add_slot():
    data = request.json or {}
    if "location" not in data:
        return jsonify({"message": "location is required"}), 400

    new_slot = ParkingSlot(
        location=data["location"],
        status="free",
        lat=data.get("lat"),
        lng=data.get("lng"),
    )
    session.add(new_slot)
    session.commit()
    return jsonify({"message": "Slot created", "id": new_slot.id}), 201


@app.route("/admin/delete-slot", methods=["POST"])
@admin_required
def admin_delete_slot():
    data = request.json or {}
    slot_id = data.get("id")
    if slot_id is None:
        return jsonify({"message": "id is required"}), 400

    slot = session.get(ParkingSlot, slot_id)
    if slot:
        session.delete(slot)
        session.commit()
        return jsonify({"message": "Slot deleted"})

    return jsonify({"message": "Slot not found"}), 404


@app.route("/book", methods=["POST"])
@token_required
def book_slot():
    payload, _ = _decode_token()
    data = request.json or {}
    slot_id = data.get("id")
    hours = float(data.get("hours", 1))

    if slot_id is None:
        return jsonify({"message": "Slot id is required"}), 400
    if hours <= 0 or hours > 24:
        return jsonify({"message": "Hours must be between 1 and 24"}), 400

    slot = session.get(ParkingSlot, slot_id)
    if not slot or slot.status != "free":
        return jsonify({"message": "Slot not available"}), 400

    user = session.get(User, payload.get("user_id"))
    price = (slot.price_per_hour or 5.0) * hours
    balance = user.balance or 0.0

    if balance < price:
        return jsonify({
            "message": f"Insufficient balance. Need GHS {price:.2f}, have GHS {balance:.2f}. Please top up."
        }), 402

    user.balance = round(balance - price, 2)
    slot.status = "occupied"

    booking = Booking(
        user_id=user.id,
        slot_id=slot.id,
        location=slot.location,
        hours=hours,
        amount=round(price, 2),
        booked_at=datetime.datetime.utcnow().isoformat(),
        status="active",
    )
    session.add(booking)
    session.commit()

    return jsonify({
        "message": f"Slot booked for {hours}h at GHS {price:.2f}",
        "balance_remaining": round(user.balance, 2),
        "booking_id": booking.id,
    })


@app.route("/release-slot", methods=["POST"])
@admin_required
def release_slot():
    data = request.json or {}
    slot_id = data.get("id")
    if slot_id is None:
        return jsonify({"message": "Slot id is required"}), 400

    slot = session.get(ParkingSlot, slot_id)

    if slot:
        slot.status = "free"
        # Mark active bookings for this slot as completed
        active = session.query(Booking).filter_by(slot_id=slot_id, status="active").all()
        for b in active:
            b.status = "completed"
        session.commit()
        return jsonify({"message": "Slot released"})

    return jsonify({"message": "Slot not found"}), 404


@app.route("/top-up", methods=["POST"])
@token_required
def top_up():
    payload, _ = _decode_token()
    data = request.json or {}
    amount = float(data.get("amount", 0))

    if amount <= 0 or amount > 500:
        return jsonify({"message": "Amount must be between 1 and 500 GHS"}), 400

    user = session.get(User, payload.get("user_id"))
    user.balance = round((user.balance or 0.0) + amount, 2)
    session.commit()

    return jsonify({
        "message": f"Wallet topped up by GHS {amount:.2f}",
        "balance": round(user.balance, 2),
    })


@app.route("/my-bookings", methods=["GET"])
@token_required
def my_bookings():
    payload, _ = _decode_token()
    bookings = session.query(Booking).filter_by(user_id=payload.get("user_id")).order_by(Booking.id.desc()).all()
    return jsonify([
        {
            "id": b.id,
            "location": b.location,
            "hours": b.hours,
            "amount": b.amount,
            "booked_at": b.booked_at,
            "status": b.status,
        }
        for b in bookings
    ])


@app.route("/admin/revenue", methods=["GET"])
@admin_required
def admin_revenue():
    bookings = session.query(Booking).all()
    total = round(sum(b.amount for b in bookings), 2)
    return jsonify({
        "total_revenue_ghs": total,
        "total_bookings": len(bookings),
        "active_bookings": sum(1 for b in bookings if b.status == "active"),
        "recent": [
            {"id": b.id, "location": b.location, "amount": b.amount, "booked_at": b.booked_at, "status": b.status}
            for b in sorted(bookings, key=lambda x: x.id, reverse=True)[:10]
        ],
    })


@app.route("/slots/<int:slot_id>", methods=["DELETE"])
@admin_required
def delete_slot(slot_id):
    slot = session.get(ParkingSlot, slot_id)
    if not slot:
        return jsonify({"message": "Slot not found"}), 404

    session.delete(slot)
    session.commit()
    return jsonify({"message": "Slot deleted"})


# ---------------------------------------------------------------------------
# Paystack Mobile Money Payment Routes
# ---------------------------------------------------------------------------

def _paystack_headers():
    return {"Authorization": f"Bearer {PAYSTACK_SECRET}", "Content-Type": "application/json"}


@app.route("/payment/initiate", methods=["POST"])
@token_required
def payment_initiate():
    """Step 1: charge the user's phone via Paystack MoMo."""
    if not PAYSTACK_SECRET:
        return jsonify({"message": "Payment gateway not configured — use Demo top-up below"}), 503

    payload, _ = _decode_token()
    data = request.json or {}
    amount = float(data.get("amount", 0))
    phone = str(data.get("phone", "")).strip()
    provider = str(data.get("provider", "mtn")).lower()  # mtn | vod | tgo

    if amount < 1 or amount > 1000:
        return jsonify({"message": "Amount must be 1–1000 GHS"}), 400
    if not phone or len(phone) < 10:
        return jsonify({"message": "Valid 10-digit phone number required"}), 400

    user = session.get(User, payload.get("user_id"))
    email = f"{user.username}@parkaccra.app"  # synthetic email for Paystack

    try:
        resp = http_requests.post(
            f"{PAYSTACK_API}/charge",
            headers=_paystack_headers(),
            json={
                "email": email,
                "amount": int(amount * 100),  # pesewas
                "currency": "GHS",
                "mobile_money": {"phone": phone, "provider": provider},
            },
            timeout=20,
        )
        result = resp.json()
        data_block = result.get("data", {})
        return jsonify({
            "status": data_block.get("status"),
            "reference": data_block.get("reference"),
            "message": result.get("message", ""),
        })
    except Exception as e:
        return jsonify({"message": f"Payment initiation failed: {e}"}), 502


@app.route("/payment/submit-otp", methods=["POST"])
@token_required
def payment_submit_otp():
    """Step 2: submit the OTP sent to the user's phone."""
    if not PAYSTACK_SECRET:
        return jsonify({"message": "Payment gateway not configured"}), 503

    payload, _ = _decode_token()
    data = request.json or {}
    otp = str(data.get("otp", "")).strip()
    reference = str(data.get("reference", "")).strip()

    if not otp or not reference:
        return jsonify({"message": "OTP and reference are required"}), 400

    try:
        resp = http_requests.post(
            f"{PAYSTACK_API}/charge/submit_otp",
            headers=_paystack_headers(),
            json={"otp": otp, "reference": reference},
            timeout=20,
        )
        result = resp.json()
        status = (result.get("data") or {}).get("status", "")

        if status == "success":
            amount_ghs = (result["data"]["amount"]) / 100
            # Idempotency: only credit once per reference
            existing = session.query(Payment).filter_by(reference=reference).first()
            if not existing:
                user = session.get(User, payload.get("user_id"))
                user.balance = round((user.balance or 0.0) + amount_ghs, 2)
                session.add(Payment(
                    user_id=user.id, reference=reference,
                    amount_ghs=amount_ghs,
                    paid_at=datetime.datetime.utcnow().isoformat(),
                ))
                session.commit()
                return jsonify({"status": "success", "balance": user.balance,
                                "message": f"GHS {amount_ghs:.2f} added to wallet ✓"})
            return jsonify({"status": "success", "message": "Already processed"})

        return jsonify({"status": status, "message": "Pending or failed — check your phone"})
    except Exception as e:
        return jsonify({"message": f"OTP error: {e}"}), 502


@app.route("/payment/verify", methods=["POST"])
@token_required
def payment_verify():
    """Manual fallback: verify a Paystack reference and credit wallet."""
    if not PAYSTACK_SECRET:
        return jsonify({"message": "Payment gateway not configured"}), 503

    payload, _ = _decode_token()
    data = request.json or {}
    reference = str(data.get("reference", "")).strip()

    if not reference:
        return jsonify({"message": "Reference required"}), 400

    existing = session.query(Payment).filter_by(reference=reference).first()
    if existing:
        return jsonify({"status": "already_processed",
                        "message": "This payment was already credited to your wallet"})

    try:
        resp = http_requests.get(
            f"{PAYSTACK_API}/transaction/verify/{reference}",
            headers=_paystack_headers(), timeout=20,
        )
        result = resp.json()
        tx = result.get("data", {})
        if tx.get("status") == "success" and tx.get("currency") == "GHS":
            amount_ghs = tx["amount"] / 100
            user = session.get(User, payload.get("user_id"))
            user.balance = round((user.balance or 0.0) + amount_ghs, 2)
            session.add(Payment(
                user_id=user.id, reference=reference,
                amount_ghs=amount_ghs,
                paid_at=datetime.datetime.utcnow().isoformat(),
            ))
            session.commit()
            return jsonify({"status": "success", "balance": user.balance,
                            "message": f"GHS {amount_ghs:.2f} added to wallet ✓"})
        return jsonify({"status": "failed", "message": "Payment not confirmed by Paystack"}), 400
    except Exception as e:
        return jsonify({"message": f"Verify error: {e}"}), 502


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/app", methods=["GET"])
def frontend():
    return send_file("index.html")


@app.route("/styles.css", methods=["GET"])
def styles():
    return send_from_directory(".", "styles.css")


@app.route("/", methods=["GET"])
def home():
    return send_file("landing.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
