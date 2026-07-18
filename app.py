import os, secrets
import json
import logging
import datetime
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from database import SessionLocal
from models import User, Habit, Log, Chat, Nudge, init_db

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "rohi-recovery-garden-secret-key-999")

@app.before_request
def csrf_protect():
    """Validates session-based CSRF tokens on all state-changing requests."""
    # Ensure session token is initialized on GET requests
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
        
    # Skip checks in testing environments
    if app.config.get("TESTING"):
        return
        
    # Enforce checks on all mutating methods
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        
        # Check JSON payloads if header is missing
        if not token and request.is_json:
            try:
                token = request.get_json().get("csrf_token")
            except Exception:
                pass
                
        if not token or token != session.get("csrf_token"):
            logger.warning(f"CSRF authentication failure for path: {request.path}")
            return jsonify({"error": "Security validation failed. CSRF token missing or invalid."}), 400

@app.after_request
def add_security_headers(response):
    """Enforces secure security HTTP headers on all outgoing responses."""
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "script-src 'self' 'unsafe-inline';"
    )
    return response

# Initialize database tables
try:
    logger.info("Initializing database schema...")
    init_db()
    logger.info("Database schema initialized successfully.")
except Exception as e:
    logger.error(f"Error during schema initialization: {e}")

# Helper for AI model execution
def run_ai_generation(prompt, response_type="text"):
    """Orchestrates Gemini API with standard REST fallback to Groq API."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    
    # Primary: Gemini
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        
        models_to_try = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash-latest"]
        for model_name in models_to_try:
            try:
                logger.info(f"Attempting primary AI generation using Gemini ({model_name})...")
                model = genai.GenerativeModel(model_name)
                
                gen_config = {}
                if response_type == "json":
                    gen_config["response_mime_type"] = "application/json"
                    
                response = model.generate_content(prompt, generation_config=gen_config)
                if response.text:
                    logger.info(f"Gemini ({model_name}) succeeded.")
                    return response.text.strip(), "gemini"
            except Exception as e:
                logger.warning(f"Gemini {model_name} failed: {e}")
                
    # Fallback: Groq REST API (bypassing python-groq SDK to avoid proxy argument conflicts)
    if groq_key and groq_key != "your_groq_api_key_here":
        try:
            logger.info("Attempting fallback AI generation using Groq API via direct REST HTTP request...")
            headers = {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "llama-3.3-70b-specdec",
                "messages": [
                    {"role": "system", "content": "You are an assistant. If JSON is requested, you must output strictly valid JSON conforming to the requested schema."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7
            }
            
            if response_type == "json":
                payload["response_format"] = {"type": "json_object"}
                
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            res_json = response.json()
            content = res_json["choices"][0]["message"]["content"]
            if content:
                logger.info("Groq API succeeded.")
                return content.strip(), "groq"
        except Exception as e:
            logger.error(f"Groq API fallback also failed: {e}")
            
    raise ValueError("AI generation failed on both primary (Gemini) and fallback (Groq) models. Check API keys.")

# ----------------- UI Routes -----------------

@app.route("/")
def index():
    """Profile selection landing page."""
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    """Handles new user profile registration with password hashing."""
    if request.method == "GET":
        return render_template("register.html")
        
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    
    if not username or not password:
        flash("Username and password are required.")
        return redirect(url_for("register"))
        
    if password != confirm_password:
        flash("Passwords do not match.")
        return redirect(url_for("register"))
        
    db = SessionLocal()
    try:
        # Check if username is taken
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            flash("Username is already taken. Please choose another.")
            return redirect(url_for("register"))
            
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password_hash=hashed_password)
        db.add(new_user)
        db.commit()
        
        session["user_id"] = new_user.id
        session["username"] = new_user.username
        logger.info(f"Registered new secure user: {username}")
        return redirect(url_for("dashboard"))
    except Exception as e:
        db.rollback()
        logger.error(f"Failed registering user: {e}")
        flash("A server error occurred during registration. Please try again.")
        return redirect(url_for("register"))
    finally:
        db.close()

@app.route("/login", methods=["GET", "POST"])
def login():
    """Handles secure user profile login, with legacy migration for passwordless test users."""
    if request.method == "GET":
        return render_template("login.html")
        
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    
    if not username or not password:
        flash("Please enter both username and password.")
        return redirect(url_for("login"))
        
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            flash("Invalid username or password.")
            return redirect(url_for("login"))
            
        # Legacy check: automatically migrate passwordless testing user profile
        if user.password_hash is None:
            user.password_hash = generate_password_hash(password)
            db.commit()
            logger.info(f"Migrated legacy user '{username}' with new password hash.")
        elif not check_password_hash(user.password_hash, password):
            flash("Invalid username or password.")
            return redirect(url_for("login"))
            
        session["user_id"] = user.id
        session["username"] = user.username
        logger.info(f"Secure login successful for user: {username}")
        return redirect(url_for("dashboard"))
    except Exception as e:
        logger.error(f"Failed login verification: {e}")
        flash("A server error occurred during login. Please try again.")
        return redirect(url_for("login"))
    finally:
        db.close()

@app.route("/logout")
def logout():
    """Clears user session."""
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    """Main application dashboard."""
    if "user_id" not in session:
        return redirect(url_for("index"))
        
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == session["user_id"]).first()
        if not user:
            session.clear()
            return redirect(url_for("index"))
            
        habits = db.query(Habit).filter(Habit.user_id == user.id).all()
        
        # Calculate daily status flags (e.g. checks if they slipped today)
        today = datetime.date.today()
        habit_statuses = {}
        for h in habits:
            log_today = db.query(Log).filter(
                Log.habit_id == h.id,
                Log.created_at >= datetime.datetime.combine(today, datetime.time.min),
                Log.created_at <= datetime.datetime.combine(today, datetime.time.max)
            ).order_by(Log.created_at.desc()).first()
            
            if log_today:
                habit_statuses[h.id] = {
                    "logged": True,
                    "value": log_today.logged_value,
                    "severity": log_today.severity
                }
            else:
                habit_statuses[h.id] = {
                    "logged": False,
                    "value": 0,
                    "severity": "Unknown"
                }

        # Retrieve or generate today's dynamic nudge
        nudge = db.query(Nudge).filter(
            Nudge.user_id == user.id,
            Nudge.created_at >= datetime.datetime.combine(today, datetime.time.min),
            Nudge.created_at <= datetime.datetime.combine(today, datetime.time.max)
        ).first()

        nudge_content = nudge.content if nudge else None

        return render_template(
            "dashboard.html",
            user=user,
            habits=habits,
            habit_statuses=habit_statuses,
            nudge_content=nudge_content
        )
    finally:
        db.close()

@app.route("/coach")
def coach():
    """CBT Adaptive AI coach page."""
    if "user_id" not in session:
        return redirect(url_for("index"))
        
    db = SessionLocal()
    try:
        chats = db.query(Chat).filter(Chat.user_id == session["user_id"]).order_by(Chat.created_at).all()
        return render_template("coach.html", chats=chats)
    finally:
        db.close()

@app.route("/emergency")
def emergency():
    """Urge Surfing emergency assistance portal."""
    if "user_id" not in session:
        return redirect(url_for("index"))
    
    db = SessionLocal()
    try:
        habits = db.query(Habit).filter(Habit.user_id == session["user_id"]).all()
        return render_template("emergency.html", habits=habits)
    finally:
        db.close()

# ----------------- API Endpoints -----------------

@app.route("/api/habit/create", methods=["POST"])
def create_habit():
    """Adds a new habit to the user's recovery garden."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json()
    name = data.get("name", "").strip()
    unit = data.get("unit", "").strip()
    daily_limit = data.get("daily_limit")

    if not name or not unit or daily_limit is None:
        return jsonify({"error": "Missing parameters."}), 400
        
    try:
        daily_limit = int(daily_limit)
        if daily_limit < 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "Limit must be a non-negative integer."}), 400

    db = SessionLocal()
    try:
        # Prevent duplicate habits
        exists = db.query(Habit).filter(Habit.user_id == session["user_id"], Habit.name == name).first()
        if exists:
            return jsonify({"error": "You are already tracking this habit."}), 400

        habit = Habit(
            user_id=session["user_id"],
            name=name,
            unit=unit,
            daily_limit=daily_limit,
            successful_days=0,
            last_success_date=None
        )
        db.add(habit)
        db.commit()
        return jsonify(habit.to_dict()), 201
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating habit: {e}")
        return jsonify({"error": "Failed to create habit."}), 500
    finally:
        db.close()

@app.route("/api/log/create", methods=["POST"])
def create_log():
    """Logs daily progress, evaluating slips and updating virtual recovery garden growth."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json()
    habit_id = data.get("habit_id")
    logged_value = data.get("logged_value")
    emotional_state = data.get("emotional_state", "").strip()
    trigger_context = data.get("trigger_context", "").strip()

    if habit_id is None or logged_value is None:
        return jsonify({"error": "Missing parameters."}), 400

    try:
        habit_id = int(habit_id)
        logged_value = int(logged_value)
        if logged_value < 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "Log value must be a non-negative integer."}), 400

    db = SessionLocal()
    try:
        habit = db.query(Habit).filter(
            Habit.id == habit_id,
            Habit.user_id == session["user_id"]
        ).first()
        
        if not habit:
            return jsonify({"error": "Habit not found."}), 404

        # Determine severity based on daily limits
        # "Success" = Strictly within limit
        # "Struggle" = Borderline limit
        # "Slip" = Exceeded limit
        if logged_value == 0:
            severity = "Success"
        elif logged_value <= habit.daily_limit:
            severity = "Success" if logged_value < habit.daily_limit else "Struggle"
        else:
            severity = "Slip"

        log = Log(
            user_id=session["user_id"],
            habit_id=habit_id,
            logged_value=logged_value,
            emotional_state=emotional_state,
            trigger_context=trigger_context,
            severity=severity
        )
        db.add(log)

        # Virtual Recovery Garden: Increment successful days if log is "Success" or "Struggle"
        # and has not already been incremented today.
        today = datetime.date.today()
        if severity in ["Success", "Struggle"]:
            if habit.last_success_date != today:
                habit.successful_days += 1
                habit.last_success_date = today
                logger.info(f"Tree for habit '{habit.name}' grew! Successful days: {habit.successful_days}")
        else:
            # Bad day (Slip): Pauses growth. Success count is unaffected, last_success_date is NOT updated.
            logger.info(f"Tree for habit '{habit.name}' paused at {habit.successful_days} days due to Slip.")

        db.commit()
        return jsonify({
            "log": log.to_dict(),
            "habit": habit.to_dict()
        }), 201
    except Exception as e:
        db.rollback()
        logger.error(f"Error logging progress: {e}")
        return jsonify({"error": "Failed to record log."}), 500
    finally:
        db.close()

@app.route("/api/chat", methods=["POST"])
def chat_coaching():
    """Converses with CBT AI therapist, adapting tone dynamically based on logging history."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Message is empty."}), 400

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == session["user_id"]).first()
        
        # Save user message to database
        user_chat = Chat(user_id=user.id, sender="user", message=user_message)
        db.add(user_chat)
        db.commit()

        # Retrieve recent logs to provide context for AI adaptive tone adjustment
        from sqlalchemy.orm import joinedload
        recent_logs = db.query(Log).options(joinedload(Log.habit)).filter(Log.user_id == user.id).order_by(Log.created_at.desc()).limit(5).all()
        log_summary = []
        emotional_states = []
        slips_logged = 0
        for l in recent_logs:
            habit_name = l.habit.name if l.habit else "Unknown"
            log_summary.append(f"- {habit_name}: logged {l.logged_value} {l.habit.unit if l.habit else ''} ({l.severity})")
            if l.emotional_state:
                emotional_states.append(l.emotional_state)
            if l.severity == "Slip":
                slips_logged += 1

        # Adjust tone dynamically
        tone = "nurturing, patient, and highly encouraging"
        if slips_logged >= 2:
            tone = "highly empathetic, focused on relapse prevention, analyzing triggers, and non-judgmental CBT redirection"
        elif "Stressed" in emotional_states or "Anxious" in emotional_states:
            tone = "calming, therapeutic, reassuring, and focused on mindfulness/breathing exercises"
        elif len(recent_logs) > 0 and slips_logged == 0:
            tone = "celebratory, warm, motivational, and affirming the user's hard work"

        # Load recent chat conversation context (last 10 entries)
        past_chats = db.query(Chat).filter(Chat.user_id == user.id).order_by(Chat.created_at.desc()).limit(10).all()
        past_chats.reverse()
        conversation_context = []
        for c in past_chats:
            conversation_context.append(f"{c.sender.capitalize()}: {c.message}")
        
        conversation_string = "\n".join(conversation_context)

        # Build prompt
        prompt = f"""You are 'Rohi', a virtual Cognitive Behavioral Therapy (CBT) recovery companion.
Your user is '{user.username}'.
Their recent logs:
{json.dumps(log_summary)}

Your coaching tone should adapt dynamically to the user. Right now, it should be: {tone}.

Conversation history:
{conversation_string}

Respond to the user's latest statement empathically and constructively. Focus on cognitive reframing, trigger tracking, or celebrating successes.
Keep the response within 2-3 short, readable paragraphs. You can use markdown bullet points if giving advice.
Do not output JSON. Do not include model intro/outro conversation. Just output the chatbot's direct reply.
"""

        # Call AI
        ai_reply, provider = run_ai_generation(prompt, response_type="text")

        # Save AI reply to database
        coach_chat = Chat(user_id=user.id, sender="coach", message=ai_reply, detected_sentiment=tone)
        db.add(coach_chat)
        db.commit()

        return jsonify({
            "message": ai_reply,
            "provider": provider
        })
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        return jsonify({"error": f"Failed to get response from AI coach: {str(e)}"}), 500
    finally:
        db.close()

@app.route("/api/nudge", methods=["GET"])
def get_nudge():
    """Generates today's personalized AI recovery nudge, analyzing habits and logged triggers."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    db = SessionLocal()
    try:
        today = datetime.date.today()
        # Look for existing nudge
        nudge = db.query(Nudge).filter(
            Nudge.user_id == session["user_id"],
            Nudge.created_at >= datetime.datetime.combine(today, datetime.time.min),
            Nudge.created_at <= datetime.datetime.combine(today, datetime.time.max)
        ).first()

        if nudge:
            return jsonify({"nudge": nudge.content, "cached": True})

        # Generate a new nudge using historical logs
        user = db.query(User).filter(User.id == session["user_id"]).first()
        from sqlalchemy.orm import joinedload
        logs = db.query(Log).options(joinedload(Log.habit)).filter(Log.user_id == user.id).order_by(Log.created_at.desc()).limit(15).all()
        
        log_data = []
        for l in logs:
            log_data.append({
                "habit": l.habit.name if l.habit else "Unknown",
                "severity": l.severity,
                "emotion": l.emotional_state,
                "context": l.trigger_context,
                "date": l.created_at.strftime("%A, %I %p")
            })

        prompt = f"""You are Rohi, a CBT recovery companion.
User: {user.username}
Log history for context analysis:
{json.dumps(log_data)}

Analyze the user's emotional states, days, or time patterns from their logs. If there are slips or struggles, address them with a small alternative habit challenge. If there are no logs or slips, generate a motivational mindfulness exercise.
Generate a short, personal 'Intelligent Nudge' for their dashboard (under 160 characters).
Make it highly actionable and direct. Focus on INR/₹ if mentioning activities (e.g. buying a ₹50 cup of herbal tea instead of scrolling).
Output ONLY the nudge text. Do not wrap it in quotes. No conversation.
"""
        
        try:
            nudge_text, provider = run_ai_generation(prompt, response_type="text")
        except Exception as ai_err:
            logger.warning(f"Could not generate AI nudge: {ai_err}")
            nudge_text = "Take a deep breath. Focus on your recovery garden today—every small step is progress."

        # Save to DB
        new_nudge = Nudge(user_id=user.id, content=nudge_text)
        db.add(new_nudge)
        db.commit()

        return jsonify({"nudge": nudge_text, "cached": False})
    except Exception as e:
        logger.error(f"Error in nudge api: {e}")
        return jsonify({"nudge": "Water your garden with mindfulness today. You are doing great."})
    finally:
        db.close()

@app.route("/api/emergency/intervention", methods=["POST"])
def emergency_intervention():
    """Generates an immediate urge surfing mindfulness intervention plan tailored to user triggers."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    habit_id = data.get("habit_id")
    trigger_desc = data.get("trigger", "").strip()

    if not habit_id or not trigger_desc:
        return jsonify({"error": "Missing parameters."}), 400

    db = SessionLocal()
    try:
        habit = db.query(Habit).filter(Habit.id == habit_id, Habit.user_id == session["user_id"]).first()
        if not habit:
            return jsonify({"error": "Habit not found"}), 404

        prompt = f"""You are Rohi, the recovery companion.
The user is experiencing an intense, acute craving to relapse on their habit: '{habit.name}'.
The current trigger context: '{trigger_desc}'.

Using Cognitive Behavioral Therapy (CBT) and 'Urge Surfing' techniques, formulate an immediate, active response plan.
Include:
1. A physical redirect task (e.g. do 10 pushups, drink a glass of cold water).
2. A cognitive reframing phrase to say out loud.
3. A 2-minute urge surfing visualization (explaining how to view the craving as a wave to ride out rather than fight).

Keep the content clear, directive, and direct. Output ONLY the response plan in clean Markdown list items. Do not use JSON.
"""

        intervention_text, provider = run_ai_generation(prompt, response_type="text")
        return jsonify({
            "intervention": intervention_text,
            "provider": provider
        })
    except Exception as e:
        logger.error(f"Error generating intervention: {e}")
        return jsonify({"error": f"Mindfulness generator failed: {str(e)}"}), 500
    finally:
        db.close()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "True") == "True")
