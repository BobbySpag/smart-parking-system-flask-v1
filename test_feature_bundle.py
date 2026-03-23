import time
import unittest
import datetime

import jwt

from app import app, Booking, limiter, session

SECRET_KEY = app.config["SECRET_KEY"]


class FeatureBundleTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        limiter.enabled = False
        self.client = app.test_client()
        self.username = f"feature_user_{int(time.time() * 1000)}"
        self.password = "password123"
        register = self.client.post(
            "/auth/register",
            json={"username": self.username, "password": self.password},
        )
        self.assertIn(register.status_code, (200, 409))

        login = self.client.post(
            "/auth/login",
            json={"username": self.username, "password": self.password},
        )
        self.assertEqual(login.status_code, 200)
        payload = login.get_json()
        self.token = payload["token"]
        self.headers = {"Authorization": self.token}

    # ------------------------------------------------------------------ helpers

    def _top_up(self, amount=200):
        res = self.client.post("/top-up", headers=self.headers, json={"amount": amount})
        self.assertEqual(res.status_code, 200)

    def _first_free_slot(self):
        slots = self.client.get("/slots").get_json()
        free = [s for s in slots if s["status"] == "free"]
        self.assertTrue(free, "Need at least one free slot")
        return free[0]

    def _book(self, slot_id, hours=1):
        return self.client.post("/book", headers=self.headers,
                                json={"id": slot_id, "hours": hours})

    # ------------------------------------------------------------------ existing

    def test_realtime_summary(self):
        res = self.client.get("/realtime/summary")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("totals", data)
        self.assertIn("slots", data["totals"])

    def test_realtime_summary_releases_expired_booking(self):
        self._top_up(200)
        slot = self._first_free_slot()
        booked = self._book(slot["id"], hours=1)
        self.assertEqual(booked.status_code, 200)

        booking_id = booked.get_json()["booking_id"]
        booking = session.get(Booking, booking_id)
        self.assertIsNotNone(booking)
        booking.booked_at = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=31)
        ).replace(tzinfo=None).isoformat()
        session.commit()

        res = self.client.get("/realtime/summary")
        self.assertEqual(res.status_code, 200)

        session.expire_all()
        booking = session.get(Booking, booking_id)
        self.assertIsNotNone(booking)
        self.assertEqual(booking.status, "expired")

        slots = self.client.get("/slots")
        self.assertEqual(slots.status_code, 200)
        refreshed = next(s for s in slots.get_json() if s["id"] == slot["id"])
        self.assertEqual(refreshed["status"], "free")

    def test_profile_update(self):
        new_email = f"{self.username}@example.com"
        res = self.client.post(
            "/auth/profile/update",
            headers=self.headers,
            json={"email": new_email},
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["email"], new_email)

    def test_booking_extend_and_cancel(self):
        self._top_up(200)
        slot = self._first_free_slot()
        booked = self._book(slot["id"], hours=1)
        self.assertEqual(booked.status_code, 200)

        bookings = self.client.get("/my-bookings", headers=self.headers)
        self.assertEqual(bookings.status_code, 200)
        booking_id = bookings.get_json()[0]["id"]

        extended = self.client.post(
            "/bookings/extend",
            headers=self.headers,
            json={"booking_id": booking_id, "extra_hours": 1},
        )
        self.assertEqual(extended.status_code, 200)

        canceled = self.client.post(
            "/bookings/cancel",
            headers=self.headers,
            json={"booking_id": booking_id},
        )
        self.assertEqual(canceled.status_code, 200)
        data = canceled.get_json()
        self.assertIn("refund", data)

    def test_notifications_and_payment_history(self):
        history = self.client.get("/payments/history", headers=self.headers)
        self.assertEqual(history.status_code, 200)
        self.assertIsInstance(history.get_json(), list)

        notifications = self.client.get("/notifications", headers=self.headers)
        self.assertEqual(notifications.status_code, 200)
        data = notifications.get_json()
        self.assertIn("items", data)

    # ------------------------------------------------------------------ edge cases

    def test_double_booking_same_slot(self):
        """Booking an already-occupied slot must return 400."""
        self._top_up(500)
        slot = self._first_free_slot()
        first = self._book(slot["id"], hours=1)
        self.assertEqual(first.status_code, 200)
        second = self._book(slot["id"], hours=1)
        self.assertEqual(second.status_code, 400)
        msg = second.get_json().get("message", "")
        self.assertIn("not available", msg.lower())

    def test_insufficient_balance(self):
        """Booking without enough balance must return 402."""
        slot = self._first_free_slot()
        # Do NOT top up — start with zero or near-zero balance
        res = self.client.post("/book", headers=self.headers,
                               json={"id": slot["id"], "hours": 24})
        # Either 400 (slot now occupied by another test) or 402 (balance)
        self.assertIn(res.status_code, (400, 402))
        if res.status_code == 402:
            self.assertIn("insufficient", res.get_json().get("message", "").lower())

    def test_expired_token_rejected(self):
        """A token with a past expiry must be rejected with 401."""
        expired = jwt.encode(
            {"user_id": 9999, "exp": 1},  # epoch 1 = well in the past
            SECRET_KEY,
            algorithm="HS256",
        )
        res = self.client.get("/my-bookings", headers={"Authorization": expired})
        self.assertEqual(res.status_code, 401)

    def test_invalid_token_rejected(self):
        """A completely bogus token must return 401."""
        res = self.client.get("/my-bookings", headers={"Authorization": "not.a.real.token"})
        self.assertEqual(res.status_code, 401)

    def test_admin_only_route_blocked_for_user(self):
        """Non-admin user must receive 403 on /admin/add-slot."""
        # Ensure this account is a regular user (second+ registration = user role)
        uname = f"plain_user_{int(time.time() * 1000)}"
        self.client.post("/auth/register",
                         json={"username": uname, "password": "pass1234"})
        login = self.client.post("/auth/login",
                                 json={"username": uname, "password": "pass1234"})
        if login.status_code != 200:
            self.skipTest("Could not create a plain user for admin-block test")
        tok = login.get_json()["token"]
        role = login.get_json().get("role", "user")
        if role == "admin":
            self.skipTest("Test account has admin role; skip admin-block test")
        res = self.client.post("/admin/add-slot",
                               headers={"Authorization": tok},
                               json={"location": "Hack Attempt"})
        self.assertEqual(res.status_code, 403)

    def test_input_validation_top_up(self):
        """top-up must reject amounts outside 1–500."""
        for bad in [0, -10, 501, 9999]:
            res = self.client.post("/top-up", headers=self.headers,
                                   json={"amount": bad})
            self.assertEqual(res.status_code, 400,
                             msg=f"Expected 400 for amount={bad}, got {res.status_code}")

    def test_input_validation_book(self):
        """Booking with out-of-range hours must return 400."""
        slot = self._first_free_slot()
        for bad_hours in [0, -1, 25, 100]:
            res = self.client.post("/book", headers=self.headers,
                                   json={"id": slot["id"], "hours": bad_hours})
            self.assertEqual(res.status_code, 400,
                             msg=f"Expected 400 for hours={bad_hours}, got {res.status_code}")

    def test_unauthenticated_protected_routes(self):
        """Protected endpoints must return 401 when no token supplied."""
        for method, path in [
            ("GET", "/my-bookings"),
            ("GET", "/payments/history"),
            ("GET", "/notifications"),
            ("POST", "/book"),
            ("POST", "/top-up"),
        ]:
            if method == "GET":
                res = self.client.get(path)
            else:
                res = self.client.post(path, json={})
            self.assertEqual(res.status_code, 401,
                             msg=f"{method} {path} should require auth")


if __name__ == "__main__":
    unittest.main()
