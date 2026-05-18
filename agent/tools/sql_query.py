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
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        sslmode=os.environ.get("POSTGRES_SSLMODE", "prefer"),
    )


def execute_sql_query(query: str, chart_attributes: Optional[Dict[str, Any]] = None) -> str:
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