import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.db import Base

# Mounted to a persistent TrueNAS dataset path in docker-compose, e.g. /data/minecontrol.db
DB_PATH = os.environ.get("MINECONTROL_DB_PATH", "/data/minecontrol.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
