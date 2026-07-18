import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import json
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from app import app, run_ai_generation
from database import Base, engine, SessionLocal
from models import User, Habit, Log


class RohiTestCase(unittest.TestCase):
    def setUp(self):
        # Configure app for testing
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

        # Initialize local test database tables
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()

        # Clear existing tables to ensure clean environment
        self.db.query(Log).delete()
        self.db.query(Habit).delete()
        self.db.query(User).delete()
        self.db.commit()

        # Create a mock user session
        with self.client.session_transaction() as sess:
            # We will create a test user first and store in session
            test_user = User(username="TestUser")
            self.db.add(test_user)
            self.db.commit()

            sess["user_id"] = test_user.id
            sess["username"] = test_user.username
            self.test_user_id = test_user.id

    def tearDown(self):
        self.db.close()
        # Drop all tables after test completes
        Base.metadata.drop_all(bind=engine)

    # 1. Input Field Bounds Checks
    def test_habit_creation_invalid_limit(self):
        """Verify that negative daily limits are rejected with 400 Bad Request."""
        payload = {
            "name": "Screen Time",
            "unit": "minutes",
            "daily_limit": -10,  # Invalid negative limit
        }
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.get_data(as_text=True))
        self.assertIn("error", data)

    def test_log_creation_invalid_value(self):
        """Verify that negative logged values are rejected with 400 Bad Request."""
        # Create a valid habit first
        habit = Habit(
            user_id=self.test_user_id, name="Smoking", unit="cigs", daily_limit=5
        )
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": -1,  # Invalid negative value
            "emotional_state": "Stressed",
            "trigger_context": "At work",
        }
        response = self.client.post(
            "/api/log/create", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.get_data(as_text=True))
        self.assertIn("error", data)

    # 2. Virtual Recovery Garden Growth & Pause Logic
    def test_garden_tree_growth_on_success(self):
        """Verify successful day increments the tree successful_days counter."""
        habit = Habit(
            user_id=self.test_user_id,
            name="Social Media",
            unit="minutes",
            daily_limit=60,
            successful_days=0,
        )
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": 45,  # Within the limit of 60
            "emotional_state": "Calm",
            "trigger_context": "Home",
        }
        response = self.client.post(
            "/api/log/create", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)

        # Re-fetch habit from DB after expiring cache
        self.db.expire_all()
        updated_habit = self.db.query(Habit).filter(Habit.id == habit.id).first()
        self.assertEqual(updated_habit.successful_days, 1)

    def test_garden_tree_paused_on_slip(self):
        """Verify that a slip logs correctly but leaves successful_days unchanged (pauses growth)."""
        habit = Habit(
            user_id=self.test_user_id,
            name="Social Media",
            unit="minutes",
            daily_limit=60,
            successful_days=5,
        )
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": 90,  # Exceeds the limit of 60 (Slip)
            "emotional_state": "Bored",
            "trigger_context": "Night scrolling",
        }
        response = self.client.post(
            "/api/log/create", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)

        # Re-fetch habit from DB after expiring cache
        self.db.expire_all()
        updated_habit = self.db.query(Habit).filter(Habit.id == habit.id).first()
        # Should remain 5 (paused, not reset, not incremented)
        self.assertEqual(updated_habit.successful_days, 5)

    # 3. AI Service Mocking & Fallback Checks
    @patch("app.requests.post")
    def test_ai_fallback_on_gemini_failure(self, mock_groq_post):
        """Tests that if Gemini fails, the app falls back to Groq REST API successfully."""
        # Mock Groq REST API response structure
        mock_response_json = {
            "choices": [
                {
                    "message": {
                        "content": "Take a walk and call a friend to distract yourself."
                    }
                }
            ]
        }
        mock_groq_post.return_value.status_code = 200
        mock_groq_post.return_value.json.return_value = mock_response_json

        # We temporarily disable the Gemini API Key and set a mock Groq API Key to force fallback execution
        with patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY": "invalid_placeholder_to_force_failure",
                "GROQ_API_KEY": "mocked_groq_api_key_for_testing",
            },
        ):
            # Run helper
            prompt = "Test fallback scenario prompt"
            result, provider = run_ai_generation(prompt, response_type="text")

            # Verify it fell back to Groq
            self.assertEqual(provider, "groq")
            self.assertEqual(
                result, "Take a walk and call a friend to distract yourself."
            )
            self.assertTrue(mock_groq_post.called)

    # 4. Authentication Verification Tests
    def test_user_registration_success(self):
        """Verify that a new user registration succeeds and redirects to dashboard."""
        payload = {
            "username": "NewSecureUser",
            "password": "securepassword123",
            "confirm_password": "securepassword123",
        }
        response = self.client.post("/register", data=payload)
        # Should redirect (302) to dashboard
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_user_login_success(self):
        """Verify login succeeds with correct credentials and fails with wrong credentials."""
        # Register a test user with a hashed password directly
        hashed = generate_password_hash("mypassword")
        user = User(username="SecureUser", password_hash=hashed)
        self.db.add(user)
        self.db.commit()

        # Login with correct password
        payload = {"username": "SecureUser", "password": "mypassword"}
        response = self.client.post("/login", data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

        # Login with wrong password
        payload = {"username": "SecureUser", "password": "wrongpassword"}
        response = self.client.post("/login", data=payload)
        # Should redirect back to login page on failure
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    # 5. Security & Headers Parameter Tests
    def test_security_headers_present(self):
        """Verify that essential security HTTP headers are present on responses."""
        response = self.client.get("/")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-XSS-Protection"), "1; mode=block")
        self.assertIn("Content-Security-Policy", response.headers)

    def test_csrf_validation_enforced(self):
        """Verify that mutations are blocked when CSRF token is missing and TESTING mode is disabled."""
        # Temporarily enable CSRF protection by toggling TESTING config flag
        app.config["TESTING"] = False
        try:
            payload = {"name": "Testing CSRF", "unit": "counts", "daily_limit": 10}
            # No X-CSRF-Token or csrf_token parameter in payload
            response = self.client.post(
                "/api/habit/create",
                data=json.dumps(payload),
                content_type="application/json",
            )
            # Should block with 400 Bad Request
            self.assertEqual(response.status_code, 400)
            data = json.loads(response.get_data(as_text=True))
            self.assertIn("error", data)
            self.assertIn("Security validation failed", data["error"])
        finally:
            # Restore testing mode state
            app.config["TESTING"] = True

    # 6. Authorization Guard Tests
    def test_dashboard_requires_login(self):
        """Verify dashboard redirects to index when not logged in."""
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.headers["Location"])

    def test_api_habit_create_requires_login(self):
        """Verify API returns 401 when session is not set."""
        with self.client.session_transaction() as sess:
            sess.clear()
        payload = {"name": "Test", "unit": "mins", "daily_limit": 10}
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_api_log_create_requires_login(self):
        """Verify log API returns 401 when session is not set."""
        with self.client.session_transaction() as sess:
            sess.clear()
        payload = {
            "habit_id": 1,
            "logged_value": 5,
            "emotional_state": "OK",
            "trigger_context": "test",
        }
        response = self.client.post(
            "/api/log/create", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 401)

    # 7. Duplicate Habit Prevention Test
    def test_duplicate_habit_rejected(self):
        """Verify the same habit cannot be planted twice by the same user."""
        habit = Habit(
            user_id=self.test_user_id, name="Smoking", unit="cigs", daily_limit=5
        )
        self.db.add(habit)
        self.db.commit()

        payload = {"name": "Smoking", "unit": "cigs", "daily_limit": 5}
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.get_data(as_text=True))
        self.assertIn("error", data)

    # 8. Empty / Missing Input Rejection
    def test_habit_missing_name_rejected(self):
        """Verify that creating a habit without a name returns 400."""
        payload = {"name": "", "unit": "minutes", "daily_limit": 30}
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_habit_missing_unit_rejected(self):
        """Verify that creating a habit without a unit returns 400."""
        payload = {"name": "Gaming", "unit": "", "daily_limit": 60}
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    # 9. Severity Classification Boundary Tests
    def test_severity_at_exact_limit_is_struggle(self):
        """Verify that logging exactly the daily limit results in Struggle severity."""
        habit = Habit(
            user_id=self.test_user_id,
            name="Coffee",
            unit="cups",
            daily_limit=3,
            successful_days=0,
        )
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": 3,  # Exactly at limit
            "emotional_state": "Neutral",
            "trigger_context": "Morning",
        }
        response = self.client.post(
            "/api/log/create", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(data["log"]["severity"], "Struggle")

    def test_severity_below_limit_is_success(self):
        """Verify that logging below the daily limit results in Success severity."""
        habit = Habit(
            user_id=self.test_user_id,
            name="Coffee",
            unit="cups",
            daily_limit=3,
            successful_days=0,
        )
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": 1,  # Below limit
            "emotional_state": "Happy",
            "trigger_context": "Breakfast",
        }
        response = self.client.post(
            "/api/log/create", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(data["log"]["severity"], "Success")

    def test_severity_above_limit_is_slip(self):
        """Verify that logging above the daily limit results in Slip severity."""
        habit = Habit(
            user_id=self.test_user_id,
            name="Coffee",
            unit="cups",
            daily_limit=3,
            successful_days=0,
        )
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": 7,  # Above limit
            "emotional_state": "Stressed",
            "trigger_context": "Office",
        }
        response = self.client.post(
            "/api/log/create", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(data["log"]["severity"], "Slip")

    # 10. Registration Validation Tests
    def test_registration_duplicate_username_rejected(self):
        """Verify that registering an existing username returns error redirect."""
        existing = User(
            username="ExistingUser", password_hash=generate_password_hash("pass123")
        )
        self.db.add(existing)
        self.db.commit()

        payload = {
            "username": "ExistingUser",
            "password": "newpass123",
            "confirm_password": "newpass123",
        }
        response = self.client.post("/register", data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/register", response.headers["Location"])

    def test_registration_missing_password_rejected(self):
        """Verify registration without password redirects back with error."""
        payload = {"username": "NoPassUser", "password": "", "confirm_password": ""}
        response = self.client.post("/register", data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/register", response.headers["Location"])

    # 11. Growth Stage Model Unit Test
    def test_growth_stage_calculation(self):
        """Verify get_growth_stage() returns correct stage at each milestone."""
        habit = Habit(
            user_id=self.test_user_id,
            name="Test",
            unit="x",
            daily_limit=1,
            successful_days=0,
        )
        self.db.add(habit)
        self.db.commit()
        self.assertEqual(habit.get_growth_stage(), 1)  # Seed

        habit.successful_days = 2
        self.assertEqual(habit.get_growth_stage(), 2)  # Sprout

        habit.successful_days = 5
        self.assertEqual(habit.get_growth_stage(), 3)  # Sapling

        habit.successful_days = 10
        self.assertEqual(habit.get_growth_stage(), 4)  # Young Tree

        habit.successful_days = 20
        self.assertEqual(habit.get_growth_stage(), 5)  # Mature Tree

        habit.successful_days = 35
        self.assertEqual(habit.get_growth_stage(), 6)  # Blooming Tree

    # 12. Logout Clears Session Test
    def test_logout_clears_session(self):
        """Verify that logout clears the user session and redirects to index."""
        response = self.client.get("/logout")
        self.assertEqual(response.status_code, 302)
        # After logout, dashboard should redirect to index
        dash_response = self.client.get("/dashboard")
        self.assertEqual(dash_response.status_code, 302)
        self.assertIn("/", dash_response.headers["Location"])

    # 13. AI Bad Habit Detection Validation Tests
    @patch("app.run_ai_generation")
    def test_habit_creation_positive_habit_rejected_by_ai(self, mock_run_ai):
        """Verify that positive routines are detected and blocked by the AI validation check."""
        mock_response_json = {
            "is_bad_habit": False,
            "message": "Reading is a positive routine. Try tracking Procrastination instead.",
        }
        mock_run_ai.return_value = (json.dumps(mock_response_json), "gemini")

        payload = {"name": "Reading books", "unit": "pages", "daily_limit": 20}
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.get_data(as_text=True))
        self.assertIn("error", data)
        self.assertIn("AI Validation", data["error"])

    @patch("app.run_ai_generation")
    def test_habit_creation_negative_habit_accepted_by_ai(self, mock_run_ai):
        """Verify that negative habits/addictions are approved by the AI validation check."""
        mock_response_json = {"is_bad_habit": True, "message": ""}
        mock_run_ai.return_value = (json.dumps(mock_response_json), "gemini")

        payload = {
            "name": "Social Media Scrolling",
            "unit": "minutes",
            "daily_limit": 45,
        }
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(data["name"], "Social Media Scrolling")


if __name__ == "__main__":
    unittest.main()
