import json
import os
import re
from typing import Any, Dict, Optional
import pandas as pd
import psycopg2

FORBIDDEN_KEYWORDS = {"DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE"}
TABLE_NAME_ALIASES: Dict[str, str] = {
    "channel_metric": "channel_metrics",
    "input_type_metric": "input_type_metrics",
    "language_statistic": "language_statistics",
    "output_type_statistic": "output_type_statistics",
    "video_list": "video_list_data",
    "monthly_count_duration": "monthly_counts_duration",
    "monthly_count": "monthly_counts_duration",
}


def _get_connection() -> psycopg2.extensions.connection:
    """
    Open and return a new psycopg2 connection using environment variables.

    Expected env vars: ``POSTGRES_HOST``, ``POSTGRES_DB``, ``POSTGRES_USER``,
    ``POSTGRES_PASSWORD``, and optionally ``POSTGRES_PORT`` (default 5432)
    and ``POSTGRES_SSLMODE`` (default ``prefer``).

    Returns:
        An open psycopg2 connection object.
    """
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        sslmode=os.environ.get("POSTGRES_SSLMODE", "prefer"),
    )


def execute_sql_query(query: str, chart_attributes: Optional[Dict[str, Any]] = None) -> str:
    """
    Safely execute a read-only SQL query and return the results as JSON.

    Before execution the query is:
    1. Stripped of markdown fences (`` ```sql `` / `` ``` ``).
    2. Passed through ``TABLE_NAME_ALIASES`` to fix common LLM table-name mistakes.
    3. Validated against ``FORBIDDEN_KEYWORDS`` to block any write operations.

    On success, NaN values in the result are replaced with ``0``.

    Args:
        query:            A PostgreSQL SELECT query string.
        chart_attributes: Optional dict forwarded verbatim into the JSON payload
                          alongside the data rows (used downstream for chart
                          rendering). Defaults to ``{}``.

    Returns:
        A JSON string with the shape::

            {"data": [{...}, ...], "chart_attributes": {...}}

        On error (forbidden keyword, connection failure, or SQL error), returns::

            {"error": "<message>"}
    """
    if chart_attributes is None:
        chart_attributes = {}

    query = query.strip().strip("```sql").strip("```").strip()

    for wrong, correct in TABLE_NAME_ALIASES.items():
        query = re.sub(rf"\b{re.escape(wrong)}\b", correct, query, flags=re.IGNORECASE)
    query_upper = query.upper()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", query_upper):
            return json.dumps({"error": "Only read-only SELECT queries are allowed."})

    try:
        conn = _get_connection()
        df = pd.read_sql_query(query, conn)
        conn.close()

        df = df.fillna(0)

        payload = {
            "data": df.to_dict(orient="records"),
            "chart_attributes": chart_attributes,
        }
        return json.dumps(payload, default=str)

    except psycopg2.OperationalError as exc:
        return json.dumps({"error": f"Database connection error: {exc}"})
    except Exception as exc:
        return json.dumps({"error": f"SQL Execution Error: {exc}"})