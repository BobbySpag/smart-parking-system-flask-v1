from flask import Flask, jsonify, request
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-in-production")

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")

# IMPORTANT FIX: SQLAlchemy expects postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

Base = declarative_base()
serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])


# Table model
class ParkingSlot(Base):
    __tablename__ = "parking_slots"

    id = Column(Integer, primary_key=True)
    location = Column(String)
    status = Column(String)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)


# Create tables
Base.metadata.create_all(engine)


def _create_token(user_id):
    return serializer.dumps({"user_id": user_id})


def _verify_token(token):
    try:
        data = serializer.loads(token, max_age=60 * 60 * 24)
        return data.get("user_id")
    except (BadSignature, SignatureExpired):
        return None


def _get_bearer_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def _require_auth_user_id():
    token = _get_bearer_token()
    if not token:
        return None, (jsonify({"message": "Missing bearer token"}), 401)

    user_id = _verify_token(token)
    if not user_id:
        return None, (jsonify({"message": "Invalid or expired token"}), 401)

    return user_id, None


@app.route("/auth/register", methods=["POST"])
def register_user():
    data = request.json or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))

    if not username or not password:
        return jsonify({"message": "username and password are required"}), 400

    existing = session.query(User).filter(User.username == username).first()
    if existing:
        return jsonify({"message": "username already exists"}), 409

    user = User(username=username, password_hash=generate_password_hash(password))
    session.add(user)
    session.commit()

    return jsonify({"message": "user registered successfully"}), 201


@app.route("/auth/login", methods=["POST"])
def login_user():
    data = request.json or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))

    if not username or not password:
        return jsonify({"message": "username and password are required"}), 400

    user = session.query(User).filter(User.username == username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"message": "invalid credentials"}), 401

    token = _create_token(user.id)
    return jsonify({"access_token": token, "token_type": "Bearer"})


@app.route("/slots", methods=["GET"])
def get_slots():
    slots = session.query(ParkingSlot).all()
    result = [{"id": s.id, "location": s.location, "status": s.status} for s in slots]
    return jsonify(result)


@app.route("/add-slot", methods=["GET", "POST"])
def add_slot():
    if request.method == "GET":
        return jsonify(
            {
                "message": "Use POST /add-slot with JSON body",
                "example": {"location": "Accra Mall"},
                "auth": "Authorization: Bearer <token>",
            }
        )

    _, auth_error = _require_auth_user_id()
    if auth_error:
        return auth_error

    data = request.json or {}
    if "location" not in data:
        return jsonify({"message": "location is required"}), 400

    new_slot = ParkingSlot(location=data["location"], status="free")
    session.add(new_slot)
    session.commit()
    return jsonify({"message": "Slot added"}), 201


@app.route("/book", methods=["GET", "POST"])
def book_slot():
    if request.method == "GET":
        return jsonify(
            {
                "message": "Use POST /book with JSON body",
                "example": {"id": 1},
                "auth": "Authorization: Bearer <token>",
            }
        )

    _, auth_error = _require_auth_user_id()
    if auth_error:
        return auth_error

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


@app.route("/release-slot", methods=["GET", "POST"])
def release_slot():
    if request.method == "GET":
        return jsonify(
            {
                "message": "Use POST /release-slot with JSON body",
                "example": {"id": 1},
                "auth": "Authorization: Bearer <token>",
            }
        )

    _, auth_error = _require_auth_user_id()
    if auth_error:
        return auth_error

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


@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "message": "Smart Parking System API",
            "routes": {
                "GET": ["/", "/slots", "/add-slot", "/book", "/release-slot"],
                "POST": ["/auth/register", "/auth/login", "/add-slot", "/book", "/release-slot"],
            },
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
