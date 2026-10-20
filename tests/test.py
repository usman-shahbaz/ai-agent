
"""Tests for SQL validation and DuckDB query execution."""

import pandas as pd
import pytest

from src.services.query_engine import (
    QueryEngine,
    QueryError,
    validate_sql,
)


def test_accepts_simple_select() -> None:
    sql = validate_sql(
        "SELECT name FROM uploaded_data"
    )

    assert "SELECT" in sql.upper()
    assert "uploaded_data" in sql


def test_adds_limit_when_missing() -> None:
    sql = validate_sql(
        "SELECT name FROM uploaded_data"
    )

    assert "LIMIT 1000" in sql.upper()


def test_preserves_existing_limit() -> None:
    sql = validate_sql(
        "SELECT name FROM uploaded_data LIMIT 5"
    )

    assert "LIMIT 5" in sql.upper()


def test_accepts_read_only_cte() -> None:
    sql = validate_sql(
        """
        WITH adults AS (
            SELECT name, age
            FROM uploaded_data
            WHERE age >= 18
        )
        SELECT name FROM adults
        """
    )

    assert "WITH" in sql.upper()
    assert "adults" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "DELETE FROM uploaded_data",
        "UPDATE uploaded_data SET age = 99",
        "DROP TABLE uploaded_data",
        "CREATE TABLE other_table (id INTEGER)",
        "SELECT * FROM another_table",
        "SELECT * FROM read_csv('private.csv')",
        "SELECT * FROM uploaded_data; SELECT 1",
        "SELECT * FROM another_schema.uploaded_data",
    ],
)
def test_rejects_invalid_or_unsafe_sql(sql: str) -> None:
    with pytest.raises(QueryError):
        validate_sql(sql)


def test_rejects_oversized_sql() -> None:
    sql = "SELECT " + (" " * 10_000) + "1"

    with pytest.raises(QueryError, match="10,000-character"):
        validate_sql(sql)


def test_query_engine_returns_expected_results() -> None:
    df = pd.DataFrame(
        {
            "name": ["Ali", "Sara", "Usman"],
            "age": [25, 30, 22],
        }
    )

    engine = QueryEngine(df, timeout_seconds=15)
    result = engine.run(
        "SELECT name, age FROM uploaded_data WHERE age >= 25"
    )

    assert len(result) == 2
    assert list(result.columns) == ["name", "age"]
    assert set(result["name"]) == {"Ali", "Sara"}


def test_query_engine_rejects_empty_dataframe() -> None:
    with pytest.raises(ValueError, match="non-empty DataFrame"):
        QueryEngine(pd.DataFrame())


def test_query_engine_rejects_invalid_timeout() -> None:
    df = pd.DataFrame({"value": [1]})

    with pytest.raises(ValueError, match="at least 1 second"):
        QueryEngine(df, timeout_seconds=0)
