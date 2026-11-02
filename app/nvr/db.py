from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from nvr.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, connect_args=connect_args)


engine = None
SessionLocal = None


def init_db() -> None:
    global engine, SessionLocal
    get_settings.cache_clear()
    engine = _engine()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    from nvr import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite(engine)


def _migrate_sqlite(eng) -> None:
    """Add columns introduced after first deploy (SQLite)."""
    if eng.dialect.name != "sqlite":
        return
    with eng.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(cameras)").fetchall()}
        if "dashboard_visible" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE cameras ADD COLUMN dashboard_visible BOOLEAN NOT NULL DEFAULT 1"
            )
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(cameras)").fetchall()}
        if "sort_order" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE cameras ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )
            # Preserve current alphabetical order as initial sort_order.
            rows = conn.exec_driver_sql(
                "SELECT id FROM cameras ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
            for idx, row in enumerate(rows):
                conn.exec_driver_sql(
                    "UPDATE cameras SET sort_order = ? WHERE id = ?",
                    (idx, row[0]),
                )


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        init_db()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
