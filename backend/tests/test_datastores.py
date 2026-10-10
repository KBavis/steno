"""Database connection URLs name a DataStore: vendor, host, and database (credentials dropped)."""

import pytest

from steno.extraction.facts import database_of


@pytest.mark.parametrize(
    ("url", "store"),
    [
        ("jdbc:postgresql://app:secret@db:5432/orders?ssl=1", ("postgresql", "db:5432", "orders")),
        ("jdbc:mysql://db/shop", ("mysql", "db", "shop")),
        ("jdbc:oracle:thin:@//db:1521/ORCL", ("oracle", "db:1521", "ORCL")),
        ("jdbc:oracle:thin:@db:1521:ORCL", ("oracle", "db:1521", "ORCL")),
        ("jdbc:sqlserver://db:1433;databaseName=pay", ("sqlserver", "db:1433", "pay")),
        ("jdbc:h2:mem:testdb", ("h2", "embedded", "testdb")),
        ("r2dbc:postgresql://db/x", ("postgresql", "db", "x")),
        ("postgresql+psycopg://db/steno", ("postgresql", "db", "steno")),
    ],
)
def test_database_urls(url, store):
    found = database_of(url)
    assert found is not None
    assert (found["vendor"], found["host"], found["database"]) == store


def test_not_a_database_url():
    assert database_of("not a url") is None
    assert database_of("<unresolved>") is None
