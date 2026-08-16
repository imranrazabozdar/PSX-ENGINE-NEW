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
            timeout=10,
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


# dashboard.py owns Streamlit page configuration.
# This launcher validates the database and adds Cloud-safe behavior.
db_exists, db_size, run_count = get_database_status()

if not db_exists:
    st.error(
        "PSX database not found. Run the GitHub Actions PSX Engine workflow first."
    )
    st.stop()

if run_count == 0:
    st.warning(
        "PSX database exists but contains no engine runs. "
        "Run the GitHub Actions workflow."
    )
    st.stop()


# dashboard.py generates HTML pills/cards. Streamlit escapes HTML unless
# unsafe_allow_html=True, which caused the raw <span> badges to appear.
# Enable HTML only for strings that clearly contain generated HTML.
_original_markdown = st.markdown


def _cloud_markdown(body, *args, **kwargs):
    if "unsafe_allow_html" not in kwargs:
        text = str(body)
        if "<span style=" in text or "<div style=" in text:
            kwargs["unsafe_allow_html"] = True
    return _original_markdown(body, *args, **kwargs)


st.markdown = _cloud_markdown


with st.sidebar:
    st.markdown("### ⚙ Database")
    st.write(f"📊 Database: `{DB_PATH}`")
    st.write(f"📦 Size: `{db_size / (1024 * 1024):.1f} MB`")
    st.write(f"🗂 Engine runs: `{run_count}`")
    st.write("🔄 Auto-refresh: `5 minutes`")


# GitHub Actions is the only engine runner.
# Streamlit never calls main.full_run().
runpy.run_path("dashboard.py", run_name="__main__")


# Reload the complete dashboard every five minutes.
st_autorefresh(
    interval=AUTO_REFRESH_MS,
    limit=None,
    key="psx_engine_autorefresh",
)
