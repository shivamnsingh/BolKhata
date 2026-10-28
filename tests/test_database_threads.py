from concurrent.futures import ThreadPoolExecutor

from database import connection_scope
from ledger import add_transaction, balance_paise, create_customer


def test_each_thread_uses_its_own_connection(tmp_path):
    path = tmp_path / "threaded.sqlite3"

    def write_customer(index: int) -> int:
        with connection_scope(path) as connection:
            customer = create_customer(connection, f"Customer {index}")
            add_transaction(connection, customer.id, index + 1, "credit")
            return customer.id

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(write_customer, range(8)))

    with connection_scope(path) as connection:
        assert len(ids) == 8
        assert sum(balance_paise(connection, customer_id) for customer_id in ids) == 3600
