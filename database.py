"""Shared SQLite connection. Feature modules own additive schema migrations.

Historical tables from earlier builds are intentionally retained in the file;
they are no longer exposed through the application.
"""
from pathlib import Path
import sqlite3
DB_NAME=Path(__file__).resolve().parent/'union_arena.db'
def get_connection():
    connection=sqlite3.connect(DB_NAME,timeout=15)
    connection.row_factory=sqlite3.Row
    return connection
