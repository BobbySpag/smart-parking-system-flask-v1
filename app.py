from flask import Flask, jsonify, request, send_file
from sqlalchemy import create_engine, Column, Integer, String, text
from sqlalchemy.orm import sessionmaker, declarative_base
from flask_bcrypt import Bcrypt
from functools import wraps
import datetime
import jwt
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-in-production")
bcrypt = Bcrypt(app)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")

# IMPORTANT FIX: SQLAlchemy expects postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

Base = declarative_base()


class ParkingSlot(Base):
    __tablename__ = "parking_slots"

    id = Column(Integer, primary_key=True)
    location = Column(String)
    status = Column(String)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    password_hash = Column(String)
    role = Column(String, default="user")


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

    return jsonify({"id": user.id, "username": user.username, "role": user.role or "user"})


@app.route("/slots", methods=["GET"])
def get_slots():
    slots = session.query(ParkingSlot).all()
    result = [{"id": s.id, "location": s.location, "status": s.status} for s in slots]
    return jsonify(result)


@app.route("/add-slot", methods=["POST"])
@admin_required
def add_slot():
    data = request.json or {}
    if "location" not in data:
        return jsonify({"message": "location is required"}), 400

    new_slot = ParkingSlot(location=data["location"], status="free")
    session.add(new_slot)
    session.commit()
    return jsonify({"message": "Slot added"}), 201


@app.route("/book", methods=["POST"])
@token_required
def book_slot():
    data = request.json or {}
    slot_id = data.get("id")
    if slot_id is None:
        return jsonify({"message": "Slot id is required"}), 400

    slot = session.get(ParkingSlot, slot_id)

    if slot and slot.status == "free":
        slot.status = "occupied"
        session.commit()
        return jsonify({"message": "Slot booked successfully"})

    return jsonify({"message": "Slot not available"}), 400


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
        session.commit()
        return jsonify({"message": "Slot released"})

    return jsonify({"message": "Slot not found"}), 404


@app.route("/slots/<int:slot_id>", methods=["DELETE"])
@admin_required
def delete_slot(slot_id):
    slot = session.get(ParkingSlot, slot_id)
    if not slot:
        return jsonify({"message": "Slot not found"}), 404

    session.delete(slot)
    session.commit()
    return jsonify({"message": "Slot deleted"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/app", methods=["GET"])
def frontend():
    return send_file("index.html")


@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "message": "Smart Parking System API",
            "status": "running",
            "version": "1.0",
            "routes": {
                "GET": ["/", "/slots", "/health", "/app", "/auth/me"],
                "POST": ["/auth/register", "/auth/login", "/add-slot", "/book", "/release-slot"],
                "DELETE": ["/slots/<slot_id>"],
            },
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
