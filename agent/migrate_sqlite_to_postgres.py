import os
import sqlite3

import pandas as pd
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv()

SQLITE_TO_PG = {
    "TEXT": "TEXT",
    "INTEGER": "BIGINT",
    "REAL": "DOUBLE PRECISION",
    "NUMERIC": "NUMERIC",
    "BLOB": "BYTEA",
    "TIME": "TEXT",
    "": "TEXT",
}

SQLITE_DB = os.path.join(os.path.dirname(__file__), "agent_database2.db")


def pg_type(sqlite_affinity: str) -> str:
    return SQLITE_TO_PG.get(sqlite_affinity.upper().strip(), "TEXT")


def get_pg_connection():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        sslmode=os.environ.get("POSTGRES_SSLMODE", "prefer"),
    )


def migrate():
    print(f"Source : {SQLITE_DB}")
    print(f"Target : {os.environ['POSTGRES_HOST']}/{os.environ['POSTGRES_DB']}\n")

    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_cur = sqlite_conn.cursor()

    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in sqlite_cur.fetchall()]

    if not tables:
        print("No tables found in the SQLite database. Nothing to migrate.")
        sqlite_conn.close()
        return

    pg_conn = get_pg_connection()
    pg_cur = pg_conn.cursor()

    for table in tables:
        print(f"Table: {table}")
        sqlite_cur.execute(f"PRAGMA table_info({table})")
        cols = sqlite_cur.fetchall()

        col_defs = []
        col_names = []
        for col in cols:
            _, name, col_type, notnull, _, is_pk = col
            pg_col_type = pg_type(col_type)
            not_null = "NOT NULL" if notnull else ""
            primary_key = "PRIMARY KEY" if is_pk else ""
            col_defs.append(f'"{name}" {pg_col_type} {not_null} {primary_key}'.strip())
            col_names.append(name)
        create_ddl = (
            f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)});'
        )
        pg_cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
        pg_cur.execute(create_ddl)
        print(f"Created table with {len(col_names)} column(s).")

        df = pd.read_sql_query(f'SELECT * FROM "{table}"', sqlite_conn)
        if df.empty:
            print(f"No rows to migrate (table is empty).")
        else:
            placeholders = ", ".join(["%s"] * len(col_names))
            quoted_cols = ", ".join([f'"{c}"' for c in col_names])
            insert_sql = (
                f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'
            )
            rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
            pg_cur.executemany(insert_sql, rows)
            print(f"Migrated {len(rows)} row(s).")

        pg_conn.commit()

    pg_cur.close()
    pg_conn.close()
    sqlite_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
