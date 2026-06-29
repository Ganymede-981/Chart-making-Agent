import os
import psycopg2


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


def get_frammer_schema() -> str:
    """
    Fetch and format the live public-schema table/column listing from PostgreSQL.

    Queries ``information_schema.columns`` for all tables in the ``public``
    schema, excluding ``schema_migrations``, ``secrets``, ``subscription``, and
    any tables prefixed with ``pg_``.

    Returns:
        A multi-line human-readable string in the form::

            Frammer AI Database Schema :

            Table: channel_metrics
            Columns: id (integer NOT NULL), channel (text NOT NULL), ...

        Returns an error string prefixed with ``"Error:"`` on connection or
        query failure.
    """
    query = """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name NOT IN ('schema_migrations', 'secrets', 'subscription') AND table_name NOT LIKE 'pg_%'
        ORDER BY table_name, ordinal_position;
    """

    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "No tables found in the public schema."

        schema_info = "Frammer AI Database Schema :\n"
        current_table = None
        col_parts = []

        for table_name, col_name, data_type, nullable in rows:
            if table_name != current_table:
                if current_table is not None:
                    schema_info += f"\nTable: {current_table}\nColumns: {', '.join(col_parts)}\n"
                current_table = table_name
                col_parts = []
            nullable_tag = "" if nullable == "YES" else " NOT NULL"
            col_parts.append(f"{col_name} ({data_type}{nullable_tag})")

        # Flush the last table
        if current_table:
            schema_info += f"\nTable: {current_table}\nColumns: {', '.join(col_parts)}\n"

        return schema_info

    except psycopg2.OperationalError as exc:
        return f"Error: Database connection failed {exc}"
    except Exception as exc:
        return f"Error retrieving schema: {exc}"
