import os
import sqlite3
import psycopg2
from psycopg2.extras import DictCursor

# 1. Read the Neon Connection String from Vercel's environment variables
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    """
    Dynamically connects to Neon (PostgreSQL) if DATABASE_URL is available.
    Falls back to a local SQLite file during local development if needed.
    """
    if DATABASE_URL:
        # Connect to Neon DB
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
        # Monkey patch the connection to make it look and act like SQLite's interface
        return PostgresToSQLiteAdapter(conn)
    else:
        # Fallback to local fallback for isolated offline testing
        conn = sqlite3.connect("local_fallback.db")
        conn.row_factory = sqlite3.Row
        return conn

class PostgresToSQLiteAdapter:
    """
    A lightweight wrapper that translates SQLite-style python calls (? placeholders, 
    execute().fetchone(), commit()) into PostgreSQL-compatible execution.
    """
    def __init__(self, pg_conn):
        self.pg_conn = pg_conn
        self._cursor = None

    def execute(self, query, params=None):
        # Convert SQLite style "?" placeholders to PostgreSQL "%s" placeholders
        query = query.replace("?", "%s")
        
        self._cursor = self.pg_conn.cursor()
        self._cursor.execute(query, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        # Allow dictionary access like row['id'] or index access like row[0]
        return row

    def fetchall(self):
        return self._cursor.fetchall()

    def commit(self):
        self.pg_conn.commit()

    def close(self):
        if self._cursor:
            self._cursor.close()
        self.pg_conn.close()