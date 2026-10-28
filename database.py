from __future__ import annotations

import sqlite3
from pathlib import Path


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


def connect(path: str | Path = "bolkhata.sqlite3") -> sqlite3.Connection:
    """Open and initialize the local ledger database."""
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection
