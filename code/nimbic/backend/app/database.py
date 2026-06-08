# app/database.py
# DEPRECATED: Please import database engine, sessions, or get_db from app.db.session instead.
# Exposing delegates here to ensure backward compatibility.

from app.db.session import engine, async_session_local, get_db

__all__ = ["engine", "async_session_local", "get_db"]
