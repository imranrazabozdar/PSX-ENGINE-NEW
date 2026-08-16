import os
import runpy
import sqlite3

import streamlit as st
from streamlit_autorefresh import st_autorefresh

DB_PATH = "psx_engine.db"
AUTO_REFRESH_MS = 5 * 60 * 1000


def get_database_status():
    if not os.path.exists(DB_PATH):
        return False, 0, 0

    size = os.path.getsize(DB_PATH)

    try:
        with sqlite3.connect(
            f"file:{DB_PATH}?mode=ro",
            uri=True,
            timeout=10
        ) as conn:
            table = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='runs'"
            ).fetchone()

            if table is None:
                return True, size, 0

            runs = conn.execute(
                "SELECT COUNT(*) FROM runs"
            ).fetchone()[0]

            return True, size, runs

    except sqlite3.Error:
        return True, size, 0


st.set_page_config(
    page_title="PSX Shariah Engine",
    page_icon="📈",
    layout="wide"
)

db_exists, db_size, run_count = get_database_status()

if not db_exists:
    st.error(
        "PSX database not found. "
        "Please run the GitHub Actions PSX Engine workflow first."
    )
    st.stop()

if run_count == 0:
    st.warning(
        "PSX database exists but contains no engine runs. "
        "Please run the GitHub Actions workflow."
    )
    st.stop()


with st.sidebar:
    st.markdown("### ⚙ Database")
    st.write(f"📊 Database: `{DB_PATH}`")
    st.write(f"📦 Size: `{db_size / (1024 * 1024):.1f} MB`")
    st.write(f"🗂 Engine runs: `{run_count}`")
    st.write("🔄 Auto-refresh: `5 minutes`")


# GitHub Actions populates the database.
# Streamlit must not run main.full_run() itself.
runpy.run_path(
    "dashboard.py",
    run_name="__main__"
)

st_autorefresh(
    interval=AUTO_REFRESH_MS,
    limit=None,
    key="psx_engine_autorefresh"
)
