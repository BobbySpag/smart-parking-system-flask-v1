from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, url_for
from authlib.integrations.flask_client import OAuth
from sqlalchemy import create_engine, Column, Integer, String, Float, text
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
import datetime
import jwt
import os
import re
import requests as http_requests
from urllib.parse import urlencode
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-in-production")
bcrypt = Bcrypt(app)
oauth = OAuth(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")

# IMPORTANT FIX: SQLAlchemy expects postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

PAYSTACK_SECRET = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_API = "https://api.paystack.co"
RESERVATION_EXPIRY_MINUTES = 30
FRONTEND_APP_URL = os.getenv("FRONTEND_APP_URL", "/app")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:5000/auth/google/callback")
APPLE_REDIRECT_URI = os.getenv("APPLE_REDIRECT_URI", "http://localhost:5000/auth/apple/callback")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID", "")
APPLE_CLIENT_SECRET = os.getenv("APPLE_CLIENT_SECRET", "")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = scoped_session(sessionmaker(bind=engine))
session = Session

Base = declarative_base()


@app.teardown_appcontext
def shutdown_session(_exception=None):
    Session.remove()


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
    email = Column(String, nullable=True)
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


def _ensure_user_email_column():
    with engine.connect() as conn:
        if DATABASE_URL.startswith("sqlite"):
            cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
            names = [row[1] for row in cols]
            if "email" not in names:
                conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR"))
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
            if "email" not in names:
                conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR"))
                conn.commit()


_ensure_user_email_column()


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
    {"location": "UPSA Campus, East Legon",     "lat": 5.6396, "lng": -0.1735, "price": 4.00},
    {"location": "Pent Hall Hostel, Legon",     "lat": 5.6517, "lng": -0.1861, "price": 3.50},
    {"location": "Evandy Hostel, East Legon",   "lat": 5.6418, "lng": -0.1497, "price": 4.50},
    {"location": "Accra International Conference Centre", "lat": 5.5608, "lng": -0.1969, "price": 9.00},
    {"location": "National Theatre Event Centre", "lat": 5.5586, "lng": -0.1962, "price": 8.50},
    {"location": "Makola Market, Accra",        "lat": 5.5482, "lng": -0.2110, "price": 3.00},
    {"location": "Madina Market",               "lat": 5.6839, "lng": -0.1645, "price": 3.50},
]

_LIVE_AVAILABILITY_CATEGORIES = {
    "hostels": {
        "label": "Hostels",
        "keywords": ["hostel", "hall", "lodge"],
    },
    "universities": {
        "label": "Accra Universities",
        "keywords": ["university", "upsa", "campus"],
    },
    "event_centers": {
        "label": "Event Centers",
        "keywords": ["event", "conference", "theatre", "center", "centre"],
    },
    "market_places": {
        "label": "Market Places",
        "keywords": ["market", "station"],
    },
}


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


if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

if APPLE_CLIENT_ID and APPLE_CLIENT_SECRET:
    oauth.register(
        name="apple",
        client_id=APPLE_CLIENT_ID,
        client_secret=APPLE_CLIENT_SECRET,
        access_token_url="https://appleid.apple.com/auth/token",
        authorize_url="https://appleid.apple.com/auth/authorize",
        api_base_url="https://appleid.apple.com/",
        jwks_uri="https://appleid.apple.com/auth/keys",
        client_kwargs={"scope": "name email"},
    )


def _parse_iso_datetime(value):
    if not value:
        return None

    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def _utc_now():
    return datetime.datetime.now(datetime.UTC)


def _utc_now_naive():
    return _utc_now().replace(tzinfo=None)


def _release_expired_bookings():
    now = _utc_now_naive()
    expiry_cutoff = now - datetime.timedelta(minutes=RESERVATION_EXPIRY_MINUTES)
    active_bookings = session.query(Booking).filter_by(status="active").all()
    changed = False

    for booking in active_bookings:
        if booking is None:
            continue
        booked_at = _parse_iso_datetime(booking.booked_at)
        if booked_at is None or booked_at > expiry_cutoff:
            continue

        booking.status = "expired"
        slot = session.get(ParkingSlot, booking.slot_id)
        if slot and slot.status != "free":
            slot.status = "free"
        changed = True

    if changed:
        session.commit()

    return changed


def _reservation_expires_at(booking):
    booked_at = _parse_iso_datetime(booking.booked_at)
    if booked_at is None:
        return None

    expires_at = booked_at + datetime.timedelta(minutes=RESERVATION_EXPIRY_MINUTES)
    return f"{expires_at.isoformat()}Z"


def _slot_live_category(location):
    normalized = (location or "").lower()
    for key, config in _LIVE_AVAILABILITY_CATEGORIES.items():
        if any(keyword in normalized for keyword in config["keywords"]):
            return key
    return None


def _build_live_availability_summary():
    grouped = {
        key: {
            "key": key,
            "label": config["label"],
            "available": 0,
            "total": 0,
            "spots": [],
        }
        for key, config in _LIVE_AVAILABILITY_CATEGORIES.items()
    }

    for slot in session.query(ParkingSlot).all():
        category = _slot_live_category(slot.location)
        if not category:
            continue

        bucket = grouped[category]
        bucket["total"] += 1
        if slot.status == "free":
            bucket["available"] += 1
        bucket["spots"].append({
            "id": slot.id,
            "location": slot.location,
            "status": slot.status,
        })

    return [grouped[key] for key in _LIVE_AVAILABILITY_CATEGORIES]


def _issue_token(user):
    return jwt.encode(
        {
            "user_id": user.id,
            "username": user.username,
            "role": user.role or "user",
            "exp": _utc_now() + datetime.timedelta(hours=2),
        },
        app.config["SECRET_KEY"],
        algorithm="HS256",
    )


def _frontend_redirect_with_auth(user):
    query = urlencode({
        "auth_token": _issue_token(user),
        "auth_role": user.role or "user",
        "auth_username": user.username,
        "auth_email": user.email or "",
    })
    return redirect(f"{FRONTEND_APP_URL}?{query}")


def _frontend_redirect_error(message):
    return redirect(f"{FRONTEND_APP_URL}?{urlencode({'oauth_error': message})}")


def _oauth_client(name):
    return oauth.create_client(name)


def _slugify_username(value):
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in (value or "user"))
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return collapsed[:24] or "user"


def _unique_username(base_username):
    candidate = _slugify_username(base_username)
    suffix = 1
    while session.query(User).filter_by(username=candidate).first():
        candidate = f"{_slugify_username(base_username)}-{suffix}"
        suffix += 1
    return candidate


def _find_or_create_oauth_user(provider, email, display_name):
    existing = session.query(User).filter_by(email=email.lower()).first() if email else None
    if existing:
        return existing

    base_username = email.split("@")[0] if email else display_name or f"{provider}-user"
    username = _unique_username(base_username)
    existing_admin = session.query(User).filter_by(role="admin").first()
    role = "admin" if existing_admin is None else "user"
    user = User(
        username=username,
        email=email.lower() if email else None,
        password_hash="",
        role=role,
    )
    session.add(user)
    session.commit()
    return user


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


def _current_user():
    payload, err = _decode_token()
    if err:
        return None, payload, err

    user = session.get(User, payload.get("user_id"))
    if not user:
        return None, payload, (jsonify({"message": "User not found"}), 404)

    return user, payload, None


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
@limiter.limit("10 per minute")
def register_user():
    data = request.json or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))

    if not username or not password:
        return jsonify({"message": "username and password are required"}), 400
    if len(username) < 3 or len(username) > 40:
        return jsonify({"message": "Username must be 3–40 characters"}), 400
    if len(password) < 6 or len(password) > 128:
        return jsonify({"message": "Password must be 6–128 characters"}), 400

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
@limiter.limit("10 per minute")
def login_user():
    data = request.json or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))

    if not username or not password:
        return jsonify({"message": "Invalid credentials"}), 401

    user = session.query(User).filter_by(username=username).first()

    if user and bcrypt.check_password_hash(user.password_hash, password):
        token = _issue_token(user)
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
        "email": user.email,
        "role": user.role or "user",
        "balance": round(user.balance or 0.0, 2),
    })


@app.route("/auth/google/start", methods=["GET"])
def auth_google_start():
    client = _oauth_client("google")
    if not client:
        return _frontend_redirect_error("Google OAuth is not configured on the server.")

    return client.authorize_redirect(GOOGLE_REDIRECT_URI)


@app.route("/auth/google/callback", methods=["GET"])
def auth_google_callback():
    client = _oauth_client("google")
    if not client:
        return _frontend_redirect_error("Google OAuth is not configured on the server.")

    try:
        token = client.authorize_access_token()
        user_info = token.get("userinfo") or client.get("userinfo").json()
        email = (user_info.get("email") or "").strip().lower()
        if not email:
            return _frontend_redirect_error("Google account did not provide an email address.")

        user = _find_or_create_oauth_user("google", email, user_info.get("name"))
        return _frontend_redirect_with_auth(user)
    except Exception as exc:
        return _frontend_redirect_error(f"Google login failed: {exc}")


@app.route("/auth/apple/start", methods=["GET"])
def auth_apple_start():
    client = _oauth_client("apple")
    if not client:
        return _frontend_redirect_error("Apple OAuth is not configured on the server.")

    return client.authorize_redirect(APPLE_REDIRECT_URI, response_mode="form_post")


@app.route("/auth/apple/callback", methods=["GET", "POST"])
def auth_apple_callback():
    client = _oauth_client("apple")
    if not client:
        return _frontend_redirect_error("Apple OAuth is not configured on the server.")

    try:
        token = client.authorize_access_token()
        claims = client.parse_id_token(token)
        email = (claims.get("email") or "").strip().lower()
        if not email:
            return _frontend_redirect_error("Apple account did not provide an email address.")

        user = _find_or_create_oauth_user("apple", email, claims.get("email"))
        return _frontend_redirect_with_auth(user)
    except Exception as exc:
        return _frontend_redirect_error(f"Apple login failed: {exc}")


@app.route("/slots", methods=["GET"])
def get_slots():
    _release_expired_bookings()
    slots = session.query(ParkingSlot).all()
    active_bookings = {
        booking.slot_id: booking
        for booking in session.query(Booking).filter_by(status="active").all()
    }
    result = [
        {
            "id": s.id,
            "location": s.location,
            "status": s.status,
            "lat": s.lat,
            "lng": s.lng,
            "price_per_hour": s.price_per_hour or 5.0,
            "reserved_until": _reservation_expires_at(active_bookings[s.id]) if s.id in active_bookings else None,
        }
        for s in slots
    ]
    return jsonify(result)


@app.route("/live-availability", methods=["GET"])
def live_availability():
    _release_expired_bookings()
    return jsonify({
        "city": "Accra",
        "updated_at": f"{_utc_now().isoformat()}",
        "categories": _build_live_availability_summary(),
    })


@app.route("/realtime/summary", methods=["GET"])
def realtime_summary():
    _release_expired_bookings()
    slots = session.query(ParkingSlot).all()
    bookings = session.query(Booking).filter_by(status="active").all()
    return jsonify({
        "server_time": _utc_now().isoformat(),
        "totals": {
            "slots": len(slots),
            "free": sum(1 for slot in slots if slot.status == "free"),
            "occupied": sum(1 for slot in slots if slot.status != "free"),
            "active_bookings": len(bookings),
        },
    })


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
@limiter.limit("30 per minute")
def book_slot():
    _release_expired_bookings()
    payload, _ = _decode_token()
    data = request.json or {}
    slot_id = data.get("id")
    if slot_id is None:
        return jsonify({"message": "Slot id is required"}), 400
    try:
        slot_id = int(slot_id)
        hours = float(data.get("hours", 1))
    except (TypeError, ValueError):
        return jsonify({"message": "Invalid slot id or hours"}), 400
    if hours < 0.5 or hours > 24:
        return jsonify({"message": "Hours must be between 0.5 and 24"}), 400

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
        booked_at=_utc_now_naive().isoformat(),
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
    _release_expired_bookings()
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
@limiter.limit("20 per minute")
def top_up():
    payload, _ = _decode_token()
    data = request.json or {}
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"message": "Invalid amount"}), 400

    if amount < 1 or amount > 500:
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
    _release_expired_bookings()
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
            "expires_at": _reservation_expires_at(b) if b.status == "active" else None,
            "reference": f"BOOK-{b.id}",
        }
        for b in bookings
    ])


@app.route("/bookings/cancel", methods=["POST"])
@token_required
def cancel_booking():
    _release_expired_bookings()
    user, payload, err = _current_user()
    if err:
        return err

    booking_id = (request.json or {}).get("booking_id")
    if not booking_id:
        return jsonify({"message": "booking_id is required"}), 400

    booking = session.get(Booking, int(booking_id))
    if not booking or booking.user_id != user.id:
        return jsonify({"message": "Booking not found"}), 404
    if booking.status != "active":
        return jsonify({"message": "Only active bookings can be canceled"}), 400

    slot = session.get(ParkingSlot, booking.slot_id)
    if slot:
        slot.status = "free"

    booked_at = _parse_iso_datetime(booking.booked_at)
    elapsed_minutes = 0
    if booked_at:
        elapsed_minutes = max(0, (_utc_now_naive() - booked_at).total_seconds() / 60)

    # Lightweight refund policy for fast delivery: 50% refund if canceled within 10 minutes.
    refund = 0.0
    if elapsed_minutes <= 10:
        refund = round((booking.amount or 0.0) * 0.5, 2)
        user.balance = round((user.balance or 0.0) + refund, 2)

    booking.status = "canceled"
    session.commit()

    return jsonify({
        "message": "Booking canceled",
        "refund": refund,
        "balance": round(user.balance or 0.0, 2),
    })


@app.route("/bookings/extend", methods=["POST"])
@token_required
def extend_booking():
    _release_expired_bookings()
    user, _, err = _current_user()
    if err:
        return err

    data = request.json or {}
    booking_id = data.get("booking_id")
    extra_hours = float(data.get("extra_hours", 1))

    if not booking_id:
        return jsonify({"message": "booking_id is required"}), 400
    if extra_hours <= 0 or extra_hours > 8:
        return jsonify({"message": "extra_hours must be between 1 and 8"}), 400

    booking = session.get(Booking, int(booking_id))
    if not booking or booking.user_id != user.id:
        return jsonify({"message": "Booking not found"}), 404
    if booking.status != "active":
        return jsonify({"message": "Only active bookings can be extended"}), 400

    slot = session.get(ParkingSlot, booking.slot_id)
    if not slot:
        return jsonify({"message": "Linked slot not found"}), 404

    price = (slot.price_per_hour or 5.0) * extra_hours
    if (user.balance or 0.0) < price:
        return jsonify({"message": f"Insufficient balance. Need GHS {price:.2f}"}), 402

    user.balance = round((user.balance or 0.0) - price, 2)
    booking.hours = round((booking.hours or 0.0) + extra_hours, 2)
    booking.amount = round((booking.amount or 0.0) + price, 2)
    session.commit()

    return jsonify({
        "message": f"Booking extended by {extra_hours}h",
        "hours": booking.hours,
        "amount": booking.amount,
        "balance": round(user.balance or 0.0, 2),
        "expires_at": _reservation_expires_at(booking),
    })


@app.route("/payments/history", methods=["GET"])
@token_required
def payment_history():
    user, _, err = _current_user()
    if err:
        return err

    payments = session.query(Payment).filter_by(user_id=user.id).order_by(Payment.id.desc()).all()
    return jsonify([
        {
            "id": p.id,
            "reference": p.reference,
            "amount_ghs": round(p.amount_ghs or 0.0, 2),
            "paid_at": p.paid_at,
            "type": "wallet_topup",
        }
        for p in payments
    ])


@app.route("/payments/receipt/<string:reference>", methods=["GET"])
@token_required
def payment_receipt(reference):
    user, _, err = _current_user()
    if err:
        return err

    payment = session.query(Payment).filter_by(reference=reference, user_id=user.id).first()
    if not payment:
        return jsonify({"message": "Receipt not found"}), 404

    return jsonify({
        "receipt": {
            "reference": payment.reference,
            "amount_ghs": round(payment.amount_ghs or 0.0, 2),
            "paid_at": payment.paid_at,
            "customer": user.username,
            "email": user.email,
            "status": "success",
        }
    })


@app.route("/auth/profile/update", methods=["POST"])
@token_required
def update_profile():
    user, _, err = _current_user()
    if err:
        return err

    data = request.json or {}
    next_email = str(data.get("email", "")).strip().lower()
    next_username = str(data.get("username", "")).strip().lower()

    if next_email:
        user.email = next_email

    if next_username and next_username != user.username:
        exists = session.query(User).filter_by(username=next_username).first()
        if exists:
            return jsonify({"message": "username already exists"}), 409
        user.username = next_username

    session.commit()

    return jsonify({
        "message": "Profile updated",
        "username": user.username,
        "email": user.email,
    })


@app.route("/admin/analytics", methods=["GET"])
@admin_required
def admin_analytics():
    _release_expired_bookings()
    bookings = session.query(Booking).all()
    slots = session.query(ParkingSlot).all()

    # Daily revenue for the last 7 days.
    day_totals = defaultdict(float)
    now = _utc_now_naive()
    for booking in bookings:
        booked_at = _parse_iso_datetime(booking.booked_at)
        if not booked_at:
            continue
        day_key = booked_at.strftime("%Y-%m-%d")
        day_totals[day_key] += booking.amount or 0.0

    revenue_series = []
    for i in range(6, -1, -1):
        day = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        revenue_series.append({"date": day, "revenue": round(day_totals.get(day, 0.0), 2)})

    free_slots = sum(1 for slot in slots if slot.status == "free")
    occupied_slots = max(0, len(slots) - free_slots)

    return jsonify({
        "totals": {
            "bookings": len(bookings),
            "revenue": round(sum(b.amount or 0.0 for b in bookings), 2),
            "free_slots": free_slots,
            "occupied_slots": occupied_slots,
        },
        "daily_revenue": revenue_series,
    })


@app.route("/notifications", methods=["GET"])
@token_required
def notifications():
    _release_expired_bookings()
    user, _, err = _current_user()
    if err:
        return err

    items = []

    user_bookings = session.query(Booking).filter_by(user_id=user.id).order_by(Booking.id.desc()).limit(10).all()
    for booking in user_bookings:
        if booking.status == "active":
            expires_at = _reservation_expires_at(booking)
            expires_date = _parse_iso_datetime(expires_at.replace("Z", "")) if expires_at else None
            if expires_date:
                minutes_left = int(max(0, (expires_date - _utc_now_naive()).total_seconds() // 60))
                if minutes_left <= 15:
                    items.append({
                        "kind": "booking_expiry",
                        "message": f"Booking #{booking.id} expires in about {minutes_left} min",
                        "created_at": booking.booked_at,
                        "severity": "warning",
                    })

        if booking.status in {"canceled", "expired"}:
            items.append({
                "kind": "booking_update",
                "message": f"Booking #{booking.id} is {booking.status}",
                "created_at": booking.booked_at,
                "severity": "info",
            })

    user_payments = session.query(Payment).filter_by(user_id=user.id).order_by(Payment.id.desc()).limit(5).all()
    for payment in user_payments:
        items.append({
            "kind": "payment",
            "message": f"Wallet top-up successful: GHS {round(payment.amount_ghs or 0.0, 2):.2f}",
            "created_at": payment.paid_at,
            "severity": "success",
            "reference": payment.reference,
        })

    return jsonify({"items": items[:20]})


@app.route("/admin/revenue", methods=["GET"])
@admin_required
def admin_revenue():
    _release_expired_bookings()
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
                    paid_at=_utc_now_naive().isoformat(),
                ))
                session.commit()
                return jsonify({"status": "success", "balance": user.balance,
                                "message": f"GHS {amount_ghs:.2f} added to wallet ✓"})
            return jsonify({"status": "success", "message": "Already processed"})

        return jsonify({"status": status, "message": "Pending or failed — check your phone"})
    except Exception as e:
        return jsonify({"message": f"OTP error: {e}"}), 502


_REFERENCE_RE = re.compile(r'^[A-Za-z0-9_\-]{4,100}$')


@app.route("/payment/verify", methods=["POST"])
@token_required
@limiter.limit("5 per minute")
def payment_verify():
    """Manual fallback: verify a Paystack reference and credit wallet."""
    if not PAYSTACK_SECRET:
        return jsonify({"message": "Payment gateway not configured"}), 503

    payload, _ = _decode_token()
    data = request.json or {}
    reference = str(data.get("reference", "")).strip()

    if not reference or not _REFERENCE_RE.match(reference):
        return jsonify({"message": "Invalid reference format"}), 400

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
                paid_at=_utc_now_naive().isoformat(),
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


@app.route("/index.html", methods=["GET"])
def frontend_index_alias():
    return send_file("index.html")


@app.route("/styles.css", methods=["GET"])
def styles():
    return send_from_directory(".", "styles.css")


@app.route("/", methods=["GET"])
def home():
    return send_file("landing.html")


@app.route("/landing.html", methods=["GET"])
def home_alias():
    return send_file("landing.html")


if __name__ == "__main__":
    import socket
    port = int(os.environ.get("PORT", 5000))
    # Check if port is already in use before starting
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        if _s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"ERROR: Port {port} is already in use. Kill the existing process first:", flush=True)
            print(f"  PowerShell: Stop-Process -Id (Get-NetTCPConnection -LocalPort {port} -State Listen).OwningProcess -Force", flush=True)
            raise SystemExit(1)
    app.run(host="0.0.0.0", port=port)
