"""
Streamlit Cloud entry point for PSX-ENGINE-NEW.

Keep the original full-featured dashboard in dashboard.py.
This launcher:
- initializes SQLite tables before the dashboard starts;
- optionally performs a first engine run when the database is empty;
- provides Streamlit-native periodic auto-refresh;
- preserves the existing dashboard rather than replacing it.
"""
import os
import sqlite3
import runpy

import streamlit as st
from streamlit_autorefresh import st_autorefresh

DB_PATH = os.environ.get("PSX_ENGINE_DB", "psx_engine.db")
AUTO_REFRESH_MS = 5 * 60 * 1000  # 5 minutes


def _database_has_runs() -> bool:
    if not os.path.exists(DB_PATH):
        return False
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='runs'"
            ).fetchone()
            if not row:
                return False
            count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            return count > 0
    except sqlite3.Error:
        return False


def _initialise_database() -> None:
    """Create/migrate the DB schema without requiring market data."""
    import database as db
    db.init_db()


def _initial_engine_run_if_needed() -> None:
    """
    A fresh Streamlit deployment may have no committed DB yet.

    GitHub Actions remains the preferred production updater. We only attempt
    one bootstrap run on an actually empty DB, and we never run the engine
    on every Streamlit refresh.
    """
    if _database_has_runs():
        return

    try:
        import main
        with st.spinner("Initialising PSX Engine data..."):
            main.full_run()
    except Exception as exc:
        st.warning(
            "The dashboard is running, but the first PSX data run could not "
            "complete yet. GitHub Actions can populate the database on its "
            "next engine run."
        )
        with st.expander("Initial engine diagnostic"):
            st.exception(exc)


st.set_page_config(
    page_title="PSX Shariah Engine",
    page_icon="📈",
    layout="wide",
)

_initialise_database()
_initial_engine_run_if_needed()

# Run the original dashboard exactly as designed.
runpy.run_path("dashboard.py", run_name="__main__")

# Native Streamlit refresh. This does not use browser JavaScript and therefore
# works reliably on Streamlit Cloud and mobile browsers.
st_autorefresh(
    interval=AUTO_REFRESH_MS,
    limit=None,
    key="psx_engine_cloud_autorefresh",
)
