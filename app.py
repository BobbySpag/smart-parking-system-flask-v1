import os
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, jsonify, request
from sqlalchemy import DateTime, Integer, String, create_engine, pool, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

app = Flask(__name__)


def _build_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///parking.db")
    if url.startswith("postgres://"):
        # Railway/Postgres URLs can use the deprecated postgres:// scheme.
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _build_database_url()


def _build_engine():
    """Create engine with appropriate pooling for SQLite or Postgres."""
    if DATABASE_URL.startswith("postgresql://"):
        # Postgres: use connection pooling for production
        return create_engine(
            DATABASE_URL,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=3600,  # Recycle connections every hour
        )
    else:
        # SQLite: use StaticPool for local development
        return create_engine(
            DATABASE_URL,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=pool.StaticPool,
        )


class Base(DeclarativeBase):
    pass


class ParkingSlot(Base):
    __tablename__ = "parking_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    booked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


engine = _build_engine()
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


@app.get("/health")
def health_check():
    """
    Health check endpoint that verifies database connectivity.
    Returns 200 if healthy, 503 if database is unreachable.
    """
    health_status = {
        "status": "ok",
        "database": "unknown",
        "database_url": DATABASE_URL.split("@")[0] if "@" in DATABASE_URL else "sqlite",
    }

    try:
        with SessionLocal() as session:
            # Simple query to verify DB connectivity
            count = session.query(ParkingSlot).count()
            health_status["database"] = "connected"
            health_status["total_slots"] = count
            return jsonify(health_status), 200
    except Exception as e:
        health_status["status"] = "degraded"
        health_status["database"] = "disconnected"
        health_status["error"] = str(e)
        return jsonify(health_status), 503


@app.get("/metrics")
def metrics():
    """
    Advanced monitoring endpoint with database pool stats and performance metrics.
    """
    metrics_data = {
        "service": "smart-parking-system",
        "database_type": "postgresql" if DATABASE_URL.startswith("postgresql://") else "sqlite",
        "db_pool": {},
        "db_version": "unknown",
        "slots_summary": {},
    }

    try:
        # Connection pool stats (if using pooled connection)
        if hasattr(engine.pool, "checkedout"):
            metrics_data["db_pool"] = {
                "checked_out": engine.pool.checkedout(),
                "total_size": engine.pool.size(),
                "overflow": engine.pool.overflow(),
            }

        with SessionLocal() as session:
            # Get total slot counts and status breakdown
            total = session.query(ParkingSlot).count()
            free = session.query(ParkingSlot).filter_by(status="free").count()
            occupied = session.query(ParkingSlot).filter_by(status="occupied").count()

            metrics_data["slots_summary"] = {
                "total": total,
                "free": free,
                "occupied": occupied,
                "availability_percentage": round((free / total * 100), 2) if total > 0 else 0,
            }

            # Get database version if Postgres
            if DATABASE_URL.startswith("postgresql://"):
                version = session.execute(text("SELECT version()")).scalar()
                metrics_data["db_version"] = version

        return jsonify(metrics_data), 200

    except Exception as e:
        metrics_data["status"] = "error"
        metrics_data["error"] = str(e)
        return jsonify(metrics_data), 503


@app.get("/")
def home():
    return jsonify(
        {
            "message": "Smart Parking System API is running",
            "routes": [
                "GET /health",
                "GET /metrics",
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
