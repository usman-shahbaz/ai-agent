
"""Validate and execute read-only DuckDB queries."""

import pandas as pd
import duckdb
import sqlglot
from sqlglot import exp


class QueryError(ValueError):
    """Raised when SQL validation or execution fails."""


FORBIDDEN_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_text",
    "read_blob",
    "glob",
    "query",
    "query_table",
    "query_table_function",
    "parquet_scan",
    "csv_scan",
    "sqlite_scan",
    "postgres_scan",
    "mysql_scan",
    "httpfs",
    "current_setting",
    "set_config",
}

FORBIDDEN_NODE_TYPES = {
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "Command",
    "Copy",
    "Transaction",
    "Merge",
    "Grant",
    "Revoke",
    "Pragma",
    "Attach",
    "Detach",
    "Use",
}


def validate_sql(sql: str) -> str:
    """
    Validate a single read-only SELECT query.

    Only the uploaded_data table and CTEs declared by the query
    are permitted. An output limit is added if one is absent.
    """

    if not sql or not sql.strip():
        raise QueryError("SQL cannot be empty.")

    if len(sql) > 10_000:
        raise QueryError("SQL exceeds the 10,000-character limit.")

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:
        raise QueryError("SQL syntax could not be validated.") from exc

    statements = [statement for statement in statements if statement]

    if len(statements) != 1:
        raise QueryError("Only one SQL statement is allowed.")

    tree = statements[0]

    # SQLGlot represents a SELECT with a WITH clause as a Select node.
    if not isinstance(tree, exp.Select):
        raise QueryError("Only SELECT queries and read-only CTEs are allowed.")

    cte_names = {
        cte.alias_or_name.casefold()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }

    for node in tree.walk():
        if node.__class__.__name__ in FORBIDDEN_NODE_TYPES:
            raise QueryError(
                "The query contains a forbidden SQL operation."
            )

        if isinstance(node, exp.Table):
            table_name = (node.name or "").casefold()

            if node.db or node.catalog:
                raise QueryError(
                    "Qualified or external table names are not allowed."
                )

            if table_name != "uploaded_data" and table_name not in cte_names:
                raise QueryError(
                    f"Access to table '{node.name}' is not allowed."
                )

        if isinstance(node, exp.Func):
            function_names = {
                str(getattr(node, "name", "") or "").casefold(),
                str(node.sql_name() or "").casefold(),
            }

            for function_name in function_names:
                if (
                    function_name in FORBIDDEN_FUNCTIONS
                    or function_name.startswith("read_")
                    or function_name.endswith("_scan")
                ):
                    raise QueryError(
                        f"Function '{function_name}' is not allowed."
                    )

    # Add a maximum result size when the model omitted LIMIT.
    if tree.args.get("limit") is None:
        tree = tree.limit(1000)

    return tree.sql(dialect="duckdb")


class QueryEngine:
    """Execute validated SQL against a DataFrame in memory."""

    def __init__(
        self,
        df: pd.DataFrame,
        timeout_seconds: int = 15,
    ) -> None:
        if df is None or df.empty:
            raise ValueError("A non-empty DataFrame is required.")

        if timeout_seconds < 1:
            raise ValueError("Query timeout must be at least 1 second.")

        self.df = df
        self.timeout_seconds = timeout_seconds

    def run(self, sql: str) -> pd.DataFrame:
        """Validate SQL and return the resulting DataFrame."""

        safe_sql = validate_sql(sql)

        connection = duckdb.connect(
            ":memory:",
            config={"enable_external_access": False},
        )

        try:
            connection.register("uploaded_data", self.df)

            # Set a DuckDB execution-time limit for this query.
            connection.execute(
                f"SET statement_timeout = '{self.timeout_seconds}s'"
            )

            result = connection.execute(safe_sql).fetchdf()

            return result

        except QueryError:
            raise

        except Exception as exc:
            raise QueryError(
                "Query execution failed. Check the SQL, column names, "
                "data types, and configured resource limits."
            ) from exc

        finally:
            connection.close()
