import os
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, jsonify, request
from sqlalchemy import DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

app = Flask(__name__)


def _build_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///parking.db")
    if url.startswith("postgres://"):
        # Railway/Postgres URLs can use the deprecated postgres:// scheme.
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _build_database_url()


class Base(DeclarativeBase):
    pass


class ParkingSlot(Base):
    __tablename__ = "parking_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    booked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _slot_to_dict(slot: ParkingSlot) -> dict:
    return {
        "id": slot.id,
        "location": slot.location,
        "status": slot.status,
        "booked_at": slot.booked_at.isoformat() if slot.booked_at else None,
    }


def init_db() -> None:
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        existing = session.query(ParkingSlot).count()
        if existing == 0:
            session.add_all(
                [
                    ParkingSlot(location="Legon Gate", status="free"),
                    ParkingSlot(location="Accra Mall", status="occupied", booked_at=datetime.now(timezone.utc)),
                    ParkingSlot(location="Circle", status="free"),
                ]
            )
            session.commit()


init_db()


@app.get("/")
def home():
    return jsonify(
        {
            "message": "Smart Parking System API is running",
            "routes": [
                "GET /slots",
                "POST /book",
                "POST /add-slot",
                "POST /release-slot",
            ],
        }
    )


# Get all slots
@app.get("/slots")
def get_slots():
    with SessionLocal() as session:
        slots = session.query(ParkingSlot).order_by(ParkingSlot.id.asc()).all()
        return jsonify([_slot_to_dict(slot) for slot in slots])


# Book a slot
@app.post("/book")
def book_slot():
    data = request.get_json(silent=True) or {}
    slot_id = data.get("id")

    if slot_id is None:
        return jsonify({"message": "Slot id is required"}), 400

    with SessionLocal() as session:
        slot = session.get(ParkingSlot, slot_id)
        if slot and slot.status == "free":
            slot.status = "occupied"
            slot.booked_at = datetime.now(timezone.utc)
            session.commit()
            return jsonify({"message": "Slot booked successfully"})

    return jsonify({"message": "Slot not available"}), 400


@app.post("/add-slot")
def add_slot():
    data = request.get_json(silent=True) or {}
    location = str(data.get("location", "")).strip()
    status = str(data.get("status", "free")).strip().lower()

    if not location:
        return jsonify({"message": "location is required"}), 400

    if status not in {"free", "occupied"}:
        return jsonify({"message": "status must be 'free' or 'occupied'"}), 400

    booked_at = datetime.now(timezone.utc) if status == "occupied" else None

    with SessionLocal() as session:
        slot = ParkingSlot(location=location, status=status, booked_at=booked_at)
        session.add(slot)
        session.commit()
        session.refresh(slot)
        return jsonify({"message": "Slot added successfully", "slot": _slot_to_dict(slot)}), 201


@app.post("/release-slot")
def release_slot():
    data = request.get_json(silent=True) or {}
    slot_id = data.get("id")

    if slot_id is None:
        return jsonify({"message": "Slot id is required"}), 400

    with SessionLocal() as session:
        slot = session.get(ParkingSlot, slot_id)
        if not slot:
            return jsonify({"message": "Slot not found"}), 404

        if slot.status == "free":
            return jsonify({"message": "Slot is already free"}), 400

        slot.status = "free"
        slot.booked_at = None
        session.commit()
        return jsonify({"message": "Slot released successfully", "slot": _slot_to_dict(slot)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
