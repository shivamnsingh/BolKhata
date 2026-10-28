from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


class LedgerError(ValueError):
    """A user-correctable ledger validation error."""


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str = ""


@dataclass(frozen=True)
class Transaction:
    id: int
    customer_id: int
    customer_name: str
    amount_paise: int
    transaction_type: str
    description: str
    transcript: str
    created_at: str
    reversed_transaction_id: int | None = None

    @property
    def amount_rupees(self) -> Decimal:
        return Decimal(self.amount_paise) / Decimal(100)


def rupees_to_paise(value: str | int | float | Decimal) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise LedgerError("Enter a valid amount.") from None
    if amount <= 0:
        raise LedgerError("Amount must be greater than zero.")
    return int(amount * 100)


def _customer(row: sqlite3.Row) -> Customer:
    return Customer(int(row["id"]), row["name"], row["phone"] or "")


def _transaction(row: sqlite3.Row) -> Transaction:
    return Transaction(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]),
        customer_name=row["customer_name"],
        amount_paise=int(row["amount_paise"]),
        transaction_type=row["transaction_type"],
        description=row["description"],
        transcript=row["transcript"],
        created_at=row["created_at"],
        reversed_transaction_id=row["reversed_transaction_id"],
    )


def create_customer(connection: sqlite3.Connection, name: str, phone: str = "") -> Customer:
    clean_name = " ".join(name.split()).strip()
    if not clean_name:
        raise LedgerError("Customer name is required.")
    if len(clean_name) > 120:
        raise LedgerError("Customer name is too long.")
    try:
        cursor = connection.execute(
            "INSERT INTO customers (name, phone) VALUES (?, ?)",
            (clean_name, phone.strip()),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        raise LedgerError("A customer with this name already exists.") from None
    return Customer(cursor.lastrowid, clean_name, phone.strip())


def get_or_create_customer(connection: sqlite3.Connection, name: str) -> Customer:
    row = connection.execute("SELECT id, name, phone FROM customers WHERE name = ?", (name.strip(),)).fetchone()
    return _customer(row) if row else create_customer(connection, name)


def list_customers(connection: sqlite3.Connection, search: str = "") -> list[Customer]:
    term = f"%{search.strip()}%"
    rows = connection.execute(
        "SELECT id, name, phone FROM customers WHERE name LIKE ? ORDER BY name COLLATE NOCASE",
        (term,),
    ).fetchall()
    return [_customer(row) for row in rows]


def add_transaction(
    connection: sqlite3.Connection,
    customer_id: int,
    amount: str | int | float | Decimal,
    transaction_type: str,
    description: str = "",
    transcript: str = "",
    idempotency_key: str | None = None,
) -> Transaction:
    if transaction_type not in {"credit", "repayment"}:
        raise LedgerError("Transaction must be credit or repayment.")
    amount_paise = rupees_to_paise(amount)
    key = idempotency_key or str(uuid.uuid4())
    try:
        cursor = connection.execute(
            """INSERT INTO transactions
               (customer_id, amount_paise, transaction_type, description, transcript, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (customer_id, amount_paise, transaction_type, description.strip(), transcript.strip(), key),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        if "idempotency_key" in str(exc):
            row = connection.execute(
                """SELECT t.*, c.name AS customer_name FROM transactions t
                   JOIN customers c ON c.id = t.customer_id WHERE t.idempotency_key = ?""",
                (key,),
            ).fetchone()
            return _transaction(row)
        raise LedgerError("Could not save this transaction.") from exc
    return get_transaction(connection, cursor.lastrowid)


def get_transaction(connection: sqlite3.Connection, transaction_id: int) -> Transaction:
    row = connection.execute(
        """SELECT t.*, c.name AS customer_name FROM transactions t
           JOIN customers c ON c.id = t.customer_id WHERE t.id = ?""",
        (transaction_id,),
    ).fetchone()
    if not row:
        raise LedgerError("Transaction not found.")
    return _transaction(row)


def list_transactions(
    connection: sqlite3.Connection,
    customer_id: int | None = None,
    search: str = "",
    transaction_type: str | None = None,
) -> list[Transaction]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    if customer_id is not None:
        clauses.append("t.customer_id = ?")
        params.append(customer_id)
    if search.strip():
        clauses.append("(c.name LIKE ? OR t.description LIKE ? OR t.transcript LIKE ?)")
        term = f"%{search.strip()}%"
        params.extend([term, term, term])
    if transaction_type in {"credit", "repayment"}:
        clauses.append("t.transaction_type = ?")
        params.append(transaction_type)
    rows = connection.execute(
        f"""SELECT t.*, c.name AS customer_name FROM transactions t
            JOIN customers c ON c.id = t.customer_id
            WHERE {' AND '.join(clauses)} ORDER BY t.created_at DESC, t.id DESC""",
        params,
    ).fetchall()
    return [_transaction(row) for row in rows]


def balance_paise(connection: sqlite3.Connection, customer_id: int) -> int:
    row = connection.execute(
        """SELECT COALESCE(SUM(CASE WHEN transaction_type = 'credit'
               THEN amount_paise ELSE -amount_paise END), 0) AS balance
           FROM transactions WHERE customer_id = ? AND reversed_transaction_id IS NULL""",
        (customer_id,),
    ).fetchone()
    return int(row["balance"])


def dashboard_totals(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        """SELECT
           COALESCE(SUM(CASE WHEN transaction_type = 'credit' AND reversed_transaction_id IS NULL THEN amount_paise ELSE 0 END), 0) AS credits,
           COALESCE(SUM(CASE WHEN transaction_type = 'repayment' AND reversed_transaction_id IS NULL THEN amount_paise ELSE 0 END), 0) AS repayments,
           COUNT(DISTINCT customer_id) AS customers
           FROM transactions"""
    ).fetchone()
    return {"credits": int(row["credits"]), "repayments": int(row["repayments"]), "customers": int(row["customers"])}


def reverse_transaction(connection: sqlite3.Connection, transaction_id: int, reason: str) -> Transaction:
    original = get_transaction(connection, transaction_id)
    if original.reversed_transaction_id is not None:
        raise LedgerError("This transaction has already been reversed.")
    reason = " ".join(reason.split()).strip()
    if not reason:
        raise LedgerError("A correction reason is required.")
    cursor = connection.execute(
        """INSERT INTO transactions
           (customer_id, amount_paise, transaction_type, description, transcript, idempotency_key)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (original.customer_id, original.amount_paise, original.transaction_type,
         f"REVERSAL of #{original.id}: {reason}", original.transcript, str(uuid.uuid4())),
    )
    connection.execute(
        "UPDATE transactions SET reversed_transaction_id = ? WHERE id = ?",
        (cursor.lastrowid, original.id),
    )
    connection.execute(
        "UPDATE transactions SET reversed_transaction_id = ? WHERE id = ?",
        (original.id, cursor.lastrowid),
    )
    connection.commit()
    return get_transaction(connection, cursor.lastrowid)


def format_paise(amount_paise: int) -> str:
    return f"₹{Decimal(amount_paise) / Decimal(100):,.2f}"
