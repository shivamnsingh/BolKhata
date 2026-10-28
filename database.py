from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    phone TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('credit', 'repayment')),
    description TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT UNIQUE,
    reversed_transaction_id INTEGER REFERENCES transactions(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_customer ON transactions(customer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at DESC);
"""


def database_path(path: str | Path | None = None) -> Path:
    """Return a stable absolute path independent of Streamlit's working directory."""
    configured = path or __import__("os").environ.get("BOLKHATA_DB")
    if configured == ":memory:":
        return Path(":memory:")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parent / "bolkhata.sqlite3"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open and initialize the local ledger database."""
    resolved = database_path(path)
    is_memory = str(resolved) == ":memory:"
    if not is_memory:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    if not is_memory:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                connection.close()
                raise
    connection.executescript(SCHEMA)
    return connection


@contextmanager
def connection_scope(path: str | Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Yield one connection for one operation and always close it."""
    connection = connect(path)
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
