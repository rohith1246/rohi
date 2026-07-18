import datetime
import logging
from typing import Dict, Any
from sqlalchemy import Column, Integer, String, Text, DateTime, Date, ForeignKey, Boolean, text
from sqlalchemy.orm import relationship
from database import Base, engine

logger = logging.getLogger(__name__)

class User(Base):
    """
    SQLAlchemy database model representing an authenticated user profile.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    habits = relationship("Habit", back_populates="user", cascade="all, delete-orphan")
    logs = relationship("Log", back_populates="user", cascade="all, delete-orphan")
    chats = relationship("Chat", back_populates="user", cascade="all, delete-orphan")
    nudges = relationship("Nudge", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the User model fields into a Python dictionary."""
        return {
            "id": self.id,
            "username": self.username,
            "created_at": self.created_at.isoformat()
        }

class Habit(Base):
    """
    SQLAlchemy database model representing a habit to track and grow.
    """
    __tablename__ = "habits"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    unit = Column(String(50), nullable=False, default="minutes")  # e.g. "minutes", "cigarettes", "drinks"
    daily_limit = Column(Integer, nullable=False)  # e.g. 60 (minutes limit)
    
    # Recovery Garden Fields
    successful_days = Column(Integer, nullable=False, default=0)
    last_success_date = Column(Date, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="habits")
    logs = relationship("Log", back_populates="habit", cascade="all, delete-orphan")

    def get_growth_stage(self) -> int:
        """Calculates growth stage from 1 to 6 based on successful days count."""
        days = self.successful_days
        if days == 0:
            return 1  # Seed
        elif days <= 3:
            return 2  # Sprout
        elif days <= 7:
            return 3  # Sapling
        elif days <= 14:
            return 4  # Young Tree
        elif days <= 30:
            return 5  # Mature Tree
        else:
            return 6  # Blooming Tree

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the Habit model fields into a Python dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "unit": self.unit,
            "daily_limit": self.daily_limit,
            "successful_days": self.successful_days,
            "last_success_date": self.last_success_date.isoformat() if self.last_success_date else None,
            "growth_stage": self.get_growth_stage(),
            "created_at": self.created_at.isoformat()
        }

class Log(Base):
    """
    SQLAlchemy database model representing a daily tracked value for a specific habit.
    """
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    habit_id = Column(Integer, ForeignKey("habits.id", ondelete="CASCADE"), nullable=False, index=True)
    logged_value = Column(Integer, nullable=False)
    emotional_state = Column(String(100), nullable=True)
    trigger_context = Column(Text, nullable=True)
    severity = Column(String(50), nullable=False)  # "Success", "Struggle", "Slip"
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="logs")
    habit = relationship("Habit", back_populates="logs")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the Log model fields into a Python dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "habit_id": self.habit_id,
            "logged_value": self.logged_value,
            "emotional_state": self.emotional_state,
            "trigger_context": self.trigger_context,
            "severity": self.severity,
            "created_at": self.created_at.isoformat()
        }

class Chat(Base):
    """
    SQLAlchemy database model representing a message in the CBT Coach conversation logs.
    """
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sender = Column(String(50), nullable=False)  # "user" or "coach"
    message = Column(Text, nullable=False)
    detected_sentiment = Column(String(100), nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="chats")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the Chat message model fields into a Python dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "sender": self.sender,
            "message": self.message,
            "detected_sentiment": self.detected_sentiment,
            "created_at": self.created_at.isoformat()
        }

class Nudge(Base):
    """
    SQLAlchemy database model representing a daily customized pattern nudge banner.
    """
    __tablename__ = "nudges"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="nudges")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the Nudge model fields into a Python dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "content": self.content,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat()
        }

def init_db() -> None:
    """
    Initializes the database schema and performs safe migrations to ensure
    needed columns (e.g. password_hash) are present on live databases.
    """
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        try:
            dialect_name = engine.name
            if dialect_name == "postgresql":
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(200);"))
                conn.commit()
            elif dialect_name == "sqlite":
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(200);"))
                    conn.commit()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Database migration warning: {e}")
