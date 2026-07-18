import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import json
import unittest
from unittest.mock import patch
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
            "daily_limit": -10  # Invalid negative limit
        }
        response = self.client.post(
            "/api/habit/create",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.get_data(as_text=True))
        self.assertIn("error", data)

    def test_log_creation_invalid_value(self):
        """Verify that negative logged values are rejected with 400 Bad Request."""
        # Create a valid habit first
        habit = Habit(user_id=self.test_user_id, name="Smoking", unit="cigs", daily_limit=5)
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": -1,  # Invalid negative value
            "emotional_state": "Stressed",
            "trigger_context": "At work"
        }
        response = self.client.post(
            "/api/log/create",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.get_data(as_text=True))
        self.assertIn("error", data)

    # 2. Virtual Recovery Garden Growth & Pause Logic
    def test_garden_tree_growth_on_success(self):
        """Verify successful day increments the tree successful_days counter."""
        habit = Habit(user_id=self.test_user_id, name="Social Media", unit="minutes", daily_limit=60, successful_days=0)
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": 45,  # Within the limit of 60
            "emotional_state": "Calm",
            "trigger_context": "Home"
        }
        response = self.client.post(
            "/api/log/create",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)

        # Re-fetch habit from DB after expiring cache
        self.db.expire_all()
        updated_habit = self.db.query(Habit).filter(Habit.id == habit.id).first()
        self.assertEqual(updated_habit.successful_days, 1)

    def test_garden_tree_paused_on_slip(self):
        """Verify that a slip logs correctly but leaves successful_days unchanged (pauses growth)."""
        habit = Habit(user_id=self.test_user_id, name="Social Media", unit="minutes", daily_limit=60, successful_days=5)
        self.db.add(habit)
        self.db.commit()

        payload = {
            "habit_id": habit.id,
            "logged_value": 90,  # Exceeds the limit of 60 (Slip)
            "emotional_state": "Bored",
            "trigger_context": "Night scrolling"
        }
        response = self.client.post(
            "/api/log/create",
            data=json.dumps(payload),
            content_type="application/json"
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
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "invalid_placeholder_to_force_failure",
            "GROQ_API_KEY": "mocked_groq_api_key_for_testing"
        }):
            # Run helper
            prompt = "Test fallback scenario prompt"
            result, provider = run_ai_generation(prompt, response_type="text")
            
            # Verify it fell back to Groq
            self.assertEqual(provider, "groq")
            self.assertEqual(result, "Take a walk and call a friend to distract yourself.")
            self.assertTrue(mock_groq_post.called)


if __name__ == "__main__":
    unittest.main()
