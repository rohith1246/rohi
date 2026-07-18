import os
from typing import Generator
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Load environment variables
load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///habit_breaker_fallback.db")

# Resolve Render legacy 'postgres://' schema
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Create engine
# For Neon/PostgreSQL, we ensure pool_pre_ping is active to handle idle connection drops
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=3600,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    Context manager database session generator dependency.
    Yields:
        Session: SQLAlchemy active database connection session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
