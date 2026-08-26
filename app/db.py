"""
Database setup. SQLite on purpose — zero config, nothing to keep alive,
won't break a demo. Swap DATABASE_URL for a Postgres connection string
later if you want a "production-ready" story for the writeup.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./kyc.db"

# check_same_thread=False is needed because FastAPI can use a request from
# a different thread than the one that created the SQLite connection.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session per request, closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
