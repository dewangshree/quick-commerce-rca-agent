import sqlite3
import pandas as pd
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "orders.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "quick_commerce_orders_gold_20260422.csv")


def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")

    df = pd.read_csv(CSV_PATH)
    # Normalize city names for easier querying
    df["city_lower"] = df["city"].str.lower().str.strip()

    conn = get_connection()
    df.to_sql("orders", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()
    print(f"[DB] Loaded {len(df)} rows into SQLite at {DB_PATH}")


def run_query(sql: str):
    """Run a SQL query and return list of dicts."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        return {"error": str(e)}
