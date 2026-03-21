import os

from flask import Flask, jsonify, request

app = Flask(__name__)

# Temporary in-memory data
parking_slots = [
    {"id": 1, "location": "Legon Gate", "status": "free"},
    {"id": 2, "location": "Accra Mall", "status": "occupied"},
    {"id": 3, "location": "Circle", "status": "free"},
]


@app.get("/")
def home():
    return jsonify(
        {
            "message": "Smart Parking System API is running",
            "routes": ["GET /slots", "POST /book"],
        }
    )


# Get all slots
@app.get("/slots")
def get_slots():
    return jsonify(parking_slots)


# Book a slot
@app.post("/book")
def book_slot():
    data = request.get_json(silent=True) or {}
    slot_id = data.get("id")

    if slot_id is None:
        return jsonify({"message": "Slot id is required"}), 400

    for slot in parking_slots:
        if slot["id"] == slot_id and slot["status"] == "free":
            slot["status"] = "occupied"
            return jsonify({"message": "Slot booked successfully"})

    return jsonify({"message": "Slot not available"}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
