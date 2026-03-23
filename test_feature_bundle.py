import time
import unittest

from app import app


class FeatureBundleTests(unittest.TestCase):
    def setUp(self):
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

    def test_realtime_summary(self):
        res = self.client.get("/realtime/summary")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("totals", data)
        self.assertIn("slots", data["totals"])

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
        top_up = self.client.post("/top-up", headers=self.headers, json={"amount": 200})
        self.assertEqual(top_up.status_code, 200)

        slots_res = self.client.get("/slots")
        self.assertEqual(slots_res.status_code, 200)
        free_slots = [slot for slot in slots_res.get_json() if slot["status"] == "free"]
        self.assertTrue(free_slots)

        slot = free_slots[0]
        booked = self.client.post(
            "/book",
            headers=self.headers,
            json={"id": slot["id"], "hours": 1},
        )
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


if __name__ == "__main__":
    unittest.main()
