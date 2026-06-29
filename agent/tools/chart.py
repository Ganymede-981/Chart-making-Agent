import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element, SubElement, indent, tostring

import pandas as pd
import psycopg2

FORBIDDEN_KEYWORDS = {"DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE"}


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


def _df_from_records(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Convert a list of row dicts (e.g. from ``execute_sql_query``) to a DataFrame.

    Args:
        records: List of dicts where each dict represents one database row.

    Returns:
        A pandas DataFrame with column names taken from the dict keys.
    """
    return pd.DataFrame(records)


def _resolve_y_cols(attrs: Dict, df: pd.DataFrame, x_col: str) -> List[str]:
    """
    Resolve which DataFrame columns to use as Y-axis series.

    Accepts a comma-separated string or list from ``attrs["y_axis"]``.  Filters
    out any column names not present in the DataFrame.  If nothing valid remains,
    falls back to the first numeric column that is not the X-axis column.

    Args:
        attrs:  Chart attribute dict produced by the LLM (may contain ``y_axis``).
        df:     The result DataFrame whose columns are considered valid.
        x_col:  The already-resolved X-axis column name (excluded from fallback).

    Returns:
        List of valid column name strings to use for Y-axis series.
    """
    raw_y = attrs.get("y_axis", "")
    if isinstance(raw_y, list):
        candidates = [c.strip() for c in raw_y if str(c).strip()]
    else:
        candidates = [c.strip() for c in str(raw_y).split(",") if c.strip()] if raw_y else []
    valid = [c for c in candidates if c in df.columns]

    if not valid:
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c != x_col]
        valid = [numeric[0]] if numeric else []

    return valid


def _build_dashboard_xml(
    df: pd.DataFrame,
    attrs: Dict[str, Any],
    title: str,
    chart_type: str,
    x_col: str,
    y_cols: List[str],
) -> str:
    """
    Build a dashboard XML string from a query result DataFrame.

    When the DataFrame contains only a single row, an additional KPI row is
    prepended above the chart row, displaying each column value as a KPI widget.
    Supports chart types: ``bar``, ``pie``, and ``line`` (unknown types default
    to ``bar``).

    Args:
        df:         Result DataFrame (already NaN-filled).
        attrs:      Chart attribute dict (used for colour-scheme decisions).
        title:      Human-readable chart title.
        chart_type: One of ``"bar"``, ``"pie"``, ``"line"`` (or variants with
                    ``"-chart"`` suffix).
        x_col:      Column name to use as the X-axis / category field.
        y_cols:     List of column names to use as Y-axis series.

    Returns:
        A well-formed XML string beginning with ``<?xml version="1.0" ...?>``.
    """
    today = date.today().isoformat()
    root = Element("dashboard", {"version": "1.0", "theme": "light", "cols": "12"})

    meta = SubElement(root, "meta")
    SubElement(meta, "title").text = title
    SubElement(meta, "description").text = f"Auto-generated analytics: {title}"
    SubElement(meta, "created").text = today

    layout = SubElement(root, "layout")

    widget_id = 1

    if len(df) == 1:
        row_kpi = SubElement(layout, "row", {"id": "r1", "label": "Summary"})
        cols_per_kpi = max(3, 12 // max(len(df.columns), 1))
        col_cursor = 1
        color_cycle = ["blue", "purple", "teal", "green", "orange", "red"]
        for i, col in enumerate(df.columns):
            SubElement(row_kpi, "widget", {
                "id": f"w{widget_id}",
                "type": "kpi",
                "col": str(col_cursor),
                "span": str(cols_per_kpi),
                "title": col.replace("_", " ").title(),
                "data-source": "query_result",
                "metric": col,
                "color-scheme": color_cycle[i % len(color_cycle)],
            })
            widget_id += 1
            col_cursor += cols_per_kpi
        chart_row_id = "r2"
    else:
        chart_row_id = "r1"

    row_chart = SubElement(layout, "row", {"id": chart_row_id, "label": title})

    y_fields_str = ",".join(y_cols)
    y_labels_str = ",".join(c.replace("_", " ").title() for c in y_cols)

    if chart_type in ("bar", "bar-chart"):
        SubElement(row_chart, "widget", {
            "id": f"w{widget_id}",
            "type": "bar-chart",
            "col": "1",
            "span": "12",
            "title": title,
            "data-source": "query_result",
            "x-field": x_col,
            "y-fields": y_fields_str,
            "y-labels": y_labels_str,
            "color-scheme": "multi" if len(y_cols) > 1 else "blue",
            "show-legend": "true" if len(y_cols) > 1 else "false",
            "x-tick-short": "true",
        })

    elif chart_type in ("pie", "pie-chart"):
        SubElement(row_chart, "widget", {
            "id": f"w{widget_id}",
            "type": "pie-chart",
            "col": "1",
            "span": "12",
            "title": title,
            "data-source": "query_result",
            "name-field": x_col,
            "value-field": y_cols[0] if y_cols else "",
            "color-scheme": "multi",
            "show-legend": "true",
            "variant": "donut",
        })

    elif chart_type in ("line", "line-chart"):
        SubElement(row_chart, "widget", {
            "id": f"w{widget_id}",
            "type": "line-chart",
            "col": "1",
            "span": "12",
            "title": title,
            "data-source": "query_result",
            "x-field": x_col,
            "y-fields": y_fields_str,
            "y-labels": y_labels_str,
            "color-scheme": "multi" if len(y_cols) > 1 else "blue",
            "show-legend": "true",
        })

    else:
        SubElement(row_chart, "widget", {
            "id": f"w{widget_id}",
            "type": "bar-chart",
            "col": "1",
            "span": "12",
            "title": title,
            "data-source": "query_result",
            "x-field": x_col,
            "y-fields": y_fields_str,
            "y-labels": y_labels_str,
            "color-scheme": "multi" if len(y_cols) > 1 else "blue",
            "show-legend": "true" if len(y_cols) > 1 else "false",
            "x-tick-short": "true",
        })

    indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode")


def generate_plotly_chart(
    query: Optional[str] = None,
    *,
    data_records: Optional[List[Dict[str, Any]]] = None,
    chart_attributes: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate a dashboard XML string from either pre-fetched records or a SQL query.

    Exactly one of ``data_records`` or ``query`` must be provided.  When
    ``data_records`` is supplied, no database connection is made.  When ``query``
    is supplied, it is validated against a forbidden-keyword blocklist before
    execution.

    Chart type is taken from ``chart_attributes["type"]``; if absent, the function
    auto-detects ``"line"`` for time-series X-axis columns and defaults to ``"bar"``
    otherwise.

    Args:
        query:            Optional raw SQL SELECT string to execute and plot.
        data_records:     Optional list of row dicts (bypasses DB query entirely).
        chart_attributes: Dict with optional keys ``type``, ``x_axis``, ``y_axis``,
                          and ``title`` that control chart rendering.

    Returns:
        A dashboard XML string on success, or a JSON-encoded ``{"error": "..."}``
        string on any failure (forbidden query, empty result, render error, etc.).
    """
    attrs = chart_attributes or {}

    if data_records is not None:
        df = _df_from_records(data_records)
    elif query:
        if any(re.search(rf"\b{kw}\b", query.upper()) for kw in FORBIDDEN_KEYWORDS):
            return json.dumps({"error": "Only read-only SELECT queries are allowed."})
        try:
            conn = _get_connection()
            df = pd.read_sql_query(query, conn)
            conn.close()
        except psycopg2.OperationalError as exc:
            return json.dumps({"error": f"Chart DB connection failed: {exc}"})
        except Exception as exc:
            return json.dumps({"error": f"Chart DB query failed: {exc}"})
    else:
        return json.dumps({"error": "Either query or data_records must be provided."})

    if df.empty:
        return json.dumps({"error": "Query returned no rows — nothing to plot."})

    df = df.fillna(0)

    x_col = attrs.get("x_axis") or df.columns[0]
    if x_col not in df.columns:
        x_col = df.columns[0]

    y_cols = _resolve_y_cols(attrs, df, x_col)
    if not y_cols:
        return json.dumps({"error": "No numeric column available for the Y axis."})

    chart_type = attrs.get("type", "bar").lower()
    chart_title = attrs.get("title") or f"{y_cols[0].replace('_', ' ').title()} by {x_col.replace('_', ' ').title()}"

    if not attrs.get("type"):
        is_time = (
            "date" in x_col.lower()
            or "month" in x_col.lower()
            or "time" in x_col.lower()
            or pd.api.types.is_datetime64_any_dtype(df[x_col])
        )
        if is_time:
            chart_type = "line"

    try:
        return _build_dashboard_xml(df, attrs, chart_title, chart_type, x_col, y_cols)
    except Exception as exc:
        return json.dumps({"error": f"XML generation failed: {exc}"})
