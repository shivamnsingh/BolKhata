from decimal import Decimal

import pytest

from database import connect, connection_scope, database_path
from ledger import LedgerError, add_transaction, balance_paise, create_customer, dashboard_totals, reverse_transaction, rupees_to_paise


@pytest.fixture
def db():
    connection = connect(":memory:")
    yield connection
    connection.close()


def test_persists_balances_and_repayments(db):
    customer = create_customer(db, "Ramesh")
    add_transaction(db, customer.id, "70", "credit")
    add_transaction(db, customer.id, Decimal("20.50"), "repayment")
    assert balance_paise(db, customer.id) == 4950
    assert dashboard_totals(db) == {"credits": 7000, "repayments": 2050, "customers": 1}


def test_duplicate_submission_is_idempotent(db):
    customer = create_customer(db, "Meena")
    first = add_transaction(db, customer.id, 50, "credit", idempotency_key="voice-1")
    second = add_transaction(db, customer.id, 50, "credit", idempotency_key="voice-1")
    assert first.id == second.id
    assert balance_paise(db, customer.id) == 5000


def test_reversal_preserves_history_and_neutralizes_balance(db):
    customer = create_customer(db, "Asha")
    original = add_transaction(db, customer.id, 100, "credit")
    reversal = reverse_transaction(db, original.id, "Wrong customer")
    assert reversal.reversed_transaction_id == original.id
    assert balance_paise(db, customer.id) == 0


def test_invalid_amounts_are_rejected(db):
    customer = create_customer(db, "Irfan")
    with pytest.raises(LedgerError):
        add_transaction(db, customer.id, 0, "credit")
    with pytest.raises(LedgerError):
        add_transaction(db, customer.id, -1, "credit")


def test_database_path_is_absolute_and_stable(tmp_path):
    assert database_path(tmp_path / "ledger.sqlite3").is_absolute()


def test_connection_scope_closes_connection_on_error(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with pytest.raises(RuntimeError):
        with connection_scope(path) as connection:
            connection.execute("INSERT INTO customers (name) VALUES (?)", ("Rollback",))
            raise RuntimeError("forced failure")
    with connection_scope(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0


def test_paise_conversion_rounds_to_two_decimal_places():
    assert rupees_to_paise("12.345") == 1235
