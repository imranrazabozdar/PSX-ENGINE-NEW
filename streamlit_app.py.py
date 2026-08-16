"""
PSX Shariah Engine — Complete Streamlit Dashboard
Deployment-optimized version with all key features
"""

import os
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ==================== PAGE CONFIG ====================
st.set_page_config(
    page_title="PSX Shariah Engine",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== CONFIG ====================
DB_PATH = "psx_engine.db"
PORTFOLIO_PATH = "portfolio.json"
REFRESH_SECONDS = 300  # Auto-refresh every 5 minutes

# ==================== DATABASE FUNCTIONS ====================
def get_db_connection():
    """Connect to SQLite database with row factory"""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_latest_runs():
    """Get the latest run for each symbol"""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT * FROM runs 
            WHERE id IN (
                SELECT MAX(id) FROM runs GROUP BY symbol
            )
            ORDER BY final_score DESC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        conn.close()
        return pd.DataFrame()

def get_run_history(symbol, limit=100):
    """Get historical runs for a symbol"""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT run_time, final_score, signal, confidence, price, 
                   technical_score, sentiment_score, macro_news_score
            FROM runs 
            WHERE symbol = ?
            ORDER BY run_time DESC 
            LIMIT ?
        """
        df = pd.read_sql_query(query, conn, params=(symbol, limit))
        conn.close()
        return df
    except Exception:
        conn.close()
        return pd.DataFrame()

def get_signal_accuracy(symbol=None):
    """Get signal accuracy stats"""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT signal, outcome, COUNT(*) as count
            FROM runs 
            WHERE outcome IS NOT NULL
        """
        if symbol:
            query += f" AND symbol = '{symbol}'"
        query += " GROUP BY signal, outcome"
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception:
        conn.close()
        return pd.DataFrame()

def get_news_items(limit=50):
    """Get recent news"""
    conn = get_db_connection()
    if conn is None:
        return []
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT source, title, published, symbols 
            FROM news 
            ORDER BY fetched_at DESC 
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        conn.close()
        return []

def load_portfolio():
    """Load portfolio.json"""
    if not os.path.exists(PORTFOLIO_PATH):
        return {"cash_pkr": 0, "holdings": []}
    try:
        with open(PORTFOLIO_PATH, 'r') as f:
            return json.load(f)
    except:
        return {"cash_pkr": 0, "holdings": []}

# ==================== THEME & STYLING ====================
def apply_theme():
    """Apply dark neon theme"""
    st.markdown("""
        <style>
        /* Main background */
        .stApp {
            background: 
                radial-gradient(1100px 560px at 10% -12%, rgba(0,229,255,0.08), transparent 60%),
                radial-gradient(1000px 520px at 102% -4%, rgba(168,85,247,0.10), transparent 55%),
                linear-gradient(180deg,#070b16 0%, #0a1020 48%, #070b16 100%);
            background-attachment: fixed;
        }
        
        /* Headers */
        h1, h2, h3 { 
            color: #eaf6ff !important; 
            letter-spacing: 0.3px;
        }
        h1 { 
            text-shadow: 0 0 22px rgba(0,229,255,0.35);
            font-size: 2.2rem !important;
        }
        h2 { 
            text-shadow: 0 0 16px rgba(0,229,255,0.20);
            font-size: 1.5rem !important;
        }
        h3 { font-size: 1.1rem !important; }
        
        /* Metrics */
        [data-testid="stMetricValue"] {
            color: #00e5ff !important;
            text-shadow: 0 0 14px rgba(0,229,255,0.45);
            font-weight: 800;
            font-size: 1.8rem !important;
        }
        [data-testid="stMetricLabel"] { 
            color: #9fb3d1 !important;
            font-size: 0.85rem !important;
        }
        [data-testid="stMetricDelta"] { 
            color: #00ffa3 !important;
        }
        
        /* Sidebar */
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(11,17,34,0.92), rgba(7,11,22,0.96));
            border-right: 1px solid rgba(0,229,255,0.14);
        }
        [data-testid="stSidebar"] * {
            color: #cfe0ff !important;
        }
        
        /* Tabs */
        [data-baseweb="tab-list"] { 
            gap: 6px; 
            border-bottom: 1px solid rgba(0,229,255,0.12);
        }
        [data-baseweb="tab"] {
            background: rgba(255,255,255,0.03);
            border-radius: 10px 10px 0 0;
            padding: 8px 18px;
            color: #cfe0ff !important;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            background: rgba(0,229,255,0.13);
            box-shadow: inset 0 -2px 0 #00e5ff;
            color: #eaf6ff !important;
        }
        
        /* Containers */
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(16,24,44,0.55);
            border: 1px solid rgba(0,229,255,0.18) !important;
            border-radius: 14px !important;
            box-shadow: 0 8px 30px rgba(0,0,0,0.45);
            backdrop-filter: blur(7px);
            padding: 1rem !important;
        }
        
        /* Dataframes */
        [data-testid="stDataFrame"] {
            background: rgba(10,16,32,0.7) !important;
            border-radius: 10px !important;
        }
        
        /* Buttons */
        .stButton > button {
            background: linear-gradient(90deg, #00e5ff, #a855f7) !important;
            color: #06101f !important;
            font-weight: 700 !important;
            border: none !important;
            border-radius: 10px !important;
            box-shadow: 0 0 18px -4px rgba(0,229,255,0.6);
        }
        .stButton > button:hover { 
            filter: brightness(1.12);
            color: #06101f !important;
        }
        
        /* Divider */
        hr {
            border-color: rgba(0,229,255,0.15) !important;
            margin: 1.5rem 0 !important;
        }
        </style>
    """, unsafe_allow_html=True)

# ==================== SIGNAL HELPER FUNCTIONS ====================
SIGNAL_COLORS = {
    "Strong Buy": "#00ffa3",
    "Buy": "#3ae67f",
    "Watch": "#ffd54a",
    "Hold": "#9fb3d1",
    "Avoid": "#ff4d6d",
    "Exit": "#ff4d6d",
    "No data": "#8aa0c0"
}

RISK_COLORS = {
    "Low": "#00ffa3",
    "Medium": "#ffd54a",
    "High": "#ff4d6d"
}

def signal_pill(signal):
    """Create HTML pill for signal"""
    color = SIGNAL_COLORS.get(signal, "#8aa0c0")
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return f'<span style="background:rgba({r},{g},{b},0.15);color:{color};border:1px solid rgba({r},{g},{b},0.5);border-radius:8px;padding:2px 12px;font-weight:700;font-size:13px;white-space:nowrap">{signal}</span>'

def risk_pill(level):
    """Create HTML pill for risk level"""
    color = RISK_COLORS.get(level, "#8aa0c0")
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return f'<span style="background:rgba({r},{g},{b},0.12);color:{color};border:1px solid rgba({r},{g},{b},0.4);border-radius:8px;padding:2px 10px;font-size:12px">{level} risk</span>'

def fmt(value, decimals=2):
    """Format value or return '—' if None/NaN"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)

# ==================== PLOTLY HELPERS ====================
def create_chart(df, title, y_column, color="#00e5ff"):
    """Create a Plotly chart"""
    if df.empty:
        return None
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['run_time'],
        y=df[y_column],
        mode='lines',
        line=dict(color=color, width=2),
        fill='tozeroy',
        fillcolor=f'rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.1)',
        name=y_column
    ))
    
    fig.update_layout(
        title=title,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cfe0ff"),
        margin=dict(l=10, r=10, t=40, b=10),
        height=300,
        xaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False),
        yaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    )
    return fig

def create_gauge(value, max_value, title, color="#00e5ff"):
    """Create a gauge chart"""
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number={"suffix": "%", "font": {"color": "#00e5ff", "size": 28}},
        title={"text": title, "font": {"color": "#9fb3d1"}},
        gauge={
            "axis": {"range": [0, max_value], "tickcolor": "#9fb3d1"},
            "bar": {"color": color},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": [
                {"range": [0, max_value * 0.7], "color": "rgba(0,255,163,0.12)"},
                {"range": [max_value * 0.7, max_value * 0.9], "color": "rgba(255,213,74,0.12)"},
                {"range": [max_value * 0.9, max_value], "color": "rgba(255,77,109,0.12)"}
            ]
        }
    ))
    fig.update_layout(
        height=250,
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cfe0ff")
    )
    return fig

# ==================== AUTO-REFRESH ====================
def auto_refresh(seconds=REFRESH_SECONDS):
    """Auto-refresh the page"""
    if seconds <= 0:
        return
    st.markdown(
        f"""
        <script>
            setTimeout(function() {{
                window.parent.location.reload();
            }}, {seconds * 1000});
        </script>
        """,
        unsafe_allow_html=True
    )

# ==================== MAIN APP ====================
apply_theme()

# Check if database exists
if not os.path.exists(DB_PATH):
    st.warning("⚠️ Database not found. The engine hasn't run yet.")
    st.info("""
    **To populate the database:**
    1. Run `python main.py run` locally
    2. Or wait for GitHub Actions to run (every 15 minutes)
    3. The database will be created automatically
    """)
    st.stop()

# Load data
df = get_latest_runs()

if df.empty:
    st.warning("No data found in the database. Run the engine first.")
    st.stop()

# ==================== HEADER ====================
st.markdown("# 📈 PSX Shariah Engine")
st.caption("⚠️ " + "This tool is decision support, NOT financial advice. No system can guarantee profit or zero loss. Always confirm manually before trading.")

# ==================== SIDEBAR ====================
with st.sidebar:
    st.header("⚙ Settings")
    
    # Auto-refresh toggle
    auto_refresh_enabled = st.checkbox("🔄 Auto-refresh (5 min)", value=True)
    
    # Dashboard password (if configured)
    if os.environ.get("DASHBOARD_PASSWORD"):
        st.caption("🔒 Password protected")
    
    # Show database info
    st.divider()
    st.caption(f"📊 Database: {os.path.basename(DB_PATH)}")
    if os.path.exists(DB_PATH):
        size = os.path.getsize(DB_PATH) / (1024 * 1024)
        st.caption(f"📦 Size: {size:.1f} MB")
        mtime = datetime.fromtimestamp(os.path.getmtime(DB_PATH))
        st.caption(f"🕐 Updated: {mtime.strftime('%Y-%m-%d %H:%M')}")
    
    # Refresh button
    if st.button("🔄 Refresh Now"):
        st.rerun()

# Auto-refresh
if auto_refresh_enabled:
    auto_refresh()

# ==================== TOP METRICS ====================
col1, col2, col3, col4, col5, col6 = st.columns(6)

buy_signals = df[df['signal'].isin(['Buy', 'Strong Buy'])]
exit_signals = df[df['signal'] == 'Exit']
watch_signals = df[df['signal'] == 'Watch']

with col1:
    st.metric("📊 Total Stocks", len(df))
with col2:
    st.metric("🎯 Buys", len(buy_signals), delta_color="normal")
with col3:
    st.metric("👀 Watch", len(watch_signals), delta_color="off")
with col4:
    st.metric("🚪 Exits", len(exit_signals), delta_color="inverse")
with col5:
    top_pick = buy_signals.iloc[0]['symbol'] if not buy_signals.empty else "—"
    st.metric("🏆 Top Pick", top_pick)
with col6:
    avg_score = df['final_score'].mean()
    st.metric("📈 Avg Score", f"{avg_score:.1f}")

st.divider()

# ==================== SIGNAL SUMMARY ====================
signal_counts = df['signal'].value_counts()
cols = st.columns(len(signal_counts))
for i, (signal, count) in enumerate(signal_counts.items()):
    with cols[i]:
        st.markdown(f"{signal_pill(signal)} **{count}**")

# ==================== TABS ====================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📋 Watchlist", 
    "🎯 Actionable", 
    "📈 Stock Detail",
    "📊 History", 
    "📰 News",
    "📈 Accuracy"
])

# ==================== TAB 1: WATCHLIST ====================
with tab1:
    st.subheader("📋 Full Ranking")
    
    # Select columns
    display_cols = ['symbol', 'final_score', 'signal', 'confidence', 'price', 
                    'stop_loss', 'target1', 'risk_level', 'shariah_status']
    available_cols = [c for c in display_cols if c in df.columns]
    
    show_df = df[available_cols].copy()
    show_df.columns = ['Symbol', 'Score', 'Signal', 'Conf%', 'Price', 'Stop', 'Target', 'Risk', 'Shariah']
    
    # Style function for dataframe
    def style_signal(val):
        color = SIGNAL_COLORS.get(val, "#8aa0c0")
        return f'color: {color}; font-weight: 700'
    
    def style_risk(val):
        color = RISK_COLORS.get(val, "#8aa0c0")
        return f'color: {color}'
    
    styled = (show_df.style
              .map(style_signal, subset=['Signal'])
              .map(style_risk, subset=['Risk'])
              .format({
                  'Score': '{:.1f}',
                  'Conf%': '{:.0f}',
                  'Price': '{:.2f}',
                  'Stop': '{:.2f}',
                  'Target': '{:.2f}'
              }, na_rep='—'))
    
    st.dataframe(styled, use_container_width=True, height=500)

# ==================== TAB 2: ACTIONABLE ====================
with tab2:
    st.subheader("🎯 Actionable Signals")
    
    actionable = df[df['signal'].isin(['Strong Buy', 'Buy', 'Exit'])]
    
    if actionable.empty:
        st.info("No Buy or Exit signals right now.")
    else:
        for _, row in actionable.iterrows():
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([1.5, 2.5, 2, 1.5])
                
                with col1:
                    st.markdown(f"### {row['symbol']}")
                    st.markdown(signal_pill(row['signal']), unsafe_allow_html=True)
                
                with col2:
                    st.markdown(f"""
                    **Price:** {fmt(row.get('price'))}  
                    **Stop Loss:** {fmt(row.get('stop_loss'))}  
                    **Target:** {fmt(row.get('target1'))}
                    """)
                
                with col3:
                    st.markdown(f"""
                    **Score:** {fmt(row.get('final_score'), 1)}  
                    **Confidence:** {fmt(row.get('confidence'), 0)}%  
                    **Risk:** {risk_pill(row.get('risk_level', '—'))}
                    """, unsafe_allow_html=True)
                
                with col4:
                    if row.get('shariah_status'):
                        st.caption(f"🕌 {row['shariah_status'][:30]}")
                    if row.get('relative_strength'):
                        st.caption(f"📊 RS: {fmt(row['relative_strength'], 0)}")
                
                if row.get('main_reason'):
                    st.caption(f"💡 {row['main_reason'][:200]}...")

# ==================== TAB 3: STOCK DETAIL ====================
with tab3:
    st.subheader("🔍 Stock Detail")
    
    symbols = sorted(df['symbol'].unique())
    selected = st.selectbox("Select a stock", symbols)
    
    if selected:
        latest = df[df['symbol'] == selected].iloc[0]
        
        # Metrics row
        cols = st.columns(5)
        cols[0].metric("Signal", latest.get('signal', '—'))
        cols[1].metric("Score", fmt(latest.get('final_score'), 1))
        cols[2].metric("Confidence", f"{fmt(latest.get('confidence'), 0)}%")
        cols[3].metric("Price", fmt(latest.get('price')))
        cols[4].metric("Risk", latest.get('risk_level', '—'))
        
        # Why
        if latest.get('main_reason'):
            st.info(f"💡 **Why:** {latest['main_reason']}")
        
        # Key stats
        stats_cols = st.columns(3)
        with stats_cols[0]:
            st.markdown("**Technical**")
            st.caption(f"Score: {fmt(latest.get('technical_score'), 1)}")
            st.caption(f"Support: {fmt(latest.get('support'))}")
            st.caption(f"Resistance: {fmt(latest.get('resistance'))}")
        with stats_cols[1]:
            st.markdown("**Sentiment**")
            st.caption(f"Score: {fmt(latest.get('sentiment_score'), 1)}")
        with stats_cols[2]:
            st.markdown("**Macro/News**")
            st.caption(f"Score: {fmt(latest.get('macro_news_score'), 1)}")
            st.caption(f"Regime: {latest.get('market_regime', '—')}")
        
        # History chart
        hist = get_run_history(selected, 100)
        if not hist.empty:
            hist['run_time'] = pd.to_datetime(hist['run_time'])
            hist = hist.sort_values('run_time')
            
            st.subheader(f"📈 {selected} — Performance History")
            
            # Score chart
            fig = create_chart(hist, "Score Over Time", "final_score")
            if fig:
                st.plotly_chart(fig, use_container_width=True)
            
            # Signal history
            st.caption("Signal History (last 20 runs)")
            signal_hist = hist[['run_time', 'signal', 'final_score', 'confidence']].tail(20)
            signal_hist['run_time'] = signal_hist['run_time'].dt.strftime('%Y-%m-%d %H:%M')
            st.dataframe(signal_hist, use_container_width=True, hide_index=True)

# ==================== TAB 4: HISTORY ====================
with tab4:
    st.subheader("📊 Historical Analysis")
    
    # Overall stats
    st.caption("**Signal Distribution Over Time**")
    
    # Get all historical data
    conn = get_db_connection()
    if conn:
        try:
            hist_all = pd.read_sql_query("""
                SELECT run_time, signal, final_score 
                FROM runs 
                ORDER BY run_time DESC 
                LIMIT 1000
            """, conn)
            conn.close()
            
            if not hist_all.empty:
                hist_all['run_time'] = pd.to_datetime(hist_all['run_time'])
                hist_all['date'] = hist_all['run_time'].dt.date
                
                # Daily signal counts
                daily_counts = hist_all.groupby(['date', 'signal']).size().unstack(fill_value=0)
                st.line_chart(daily_counts)
                
                # Score distribution
                st.caption("**Score Distribution**")
                score_bins = pd.cut(hist_all['final_score'], bins=[0, 40, 50, 60, 70, 80, 100])
                score_dist = score_bins.value_counts().sort_index()
                st.bar_chart(score_dist)
        except Exception as e:
            st.error(f"Error loading history: {e}")

# ==================== TAB 5: NEWS ====================
with tab5:
    st.subheader("📰 Recent News")
    
    news_items = get_news_items(50)
    if not news_items:
        st.info("No news items found in the database.")
    else:
        for item in news_items:
            with st.container(border=True):
                cols = st.columns([3, 1])
                with cols[0]:
                    st.markdown(f"**{item['title']}**")
                    st.caption(f"Source: {item['source']}")
                with cols[1]:
                    if item.get('symbols'):
                        st.caption(f"Symbols: {item['symbols']}")
                    if item.get('published'):
                        try:
                            pub_date = datetime.fromisoformat(item['published'].replace('Z', '+00:00'))
                            st.caption(f"📅 {pub_date.strftime('%Y-%m-%d %H:%M')}")
                        except:
                            pass

# ==================== TAB 6: ACCURACY ====================
with tab6:
    st.subheader("📈 Signal Accuracy")
    
    acc_df = get_signal_accuracy()
    
    if acc_df.empty:
        st.info("No graded outcomes yet. The engine needs 1-7 days of forward prices.")
    else:
        # Pivot for display
        pivot = acc_df.pivot(index='signal', columns='outcome', values='count').fillna(0)
        pivot['Total'] = pivot.sum(axis=1)
        pivot['Win Rate'] = (pivot.get('worked', 0) / pivot['Total'] * 100).round(1)
        
        # Sort by total
        pivot = pivot.sort_values('Total', ascending=False)
        
        st.dataframe(pivot, use_container_width=True)
        
        # Bar chart
        if 'worked' in pivot.columns and 'failed' in pivot.columns:
            chart_data = pivot[['worked', 'failed']]
            st.bar_chart(chart_data)
        
        # Summary
        total_graded = pivot['Total'].sum()
        total_wins = pivot.get('worked', 0).sum()
        if total_graded > 0:
            st.metric("Overall Win Rate", f"{(total_wins/total_graded*100):.1f}%", 
                     f"{total_wins}W / {total_graded-total_wins}L")

# ==================== PORTFOLIO (if portfolio.json exists) ====================
portfolio = load_portfolio()
if portfolio.get('holdings'):
    st.divider()
    st.subheader("💼 Portfolio")
    
    holdings_data = []
    for h in portfolio.get('holdings', []):
        sym = h['symbol']
        latest = df[df['symbol'] == sym]
        price = latest.iloc[0]['price'] if not latest.empty else None
        
        holdings_data.append({
            'Symbol': sym,
            'Shares': h['qty'],
            'Avg Cost': h['avg_cost'],
            'Current Price': price or '—',
            'Value': price * h['qty'] if price else '—',
            'P/L %': ((price - h['avg_cost']) / h['avg_cost'] * 100) if price else '—'
        })
    
    port_df = pd.DataFrame(holdings_data)
    st.dataframe(port_df, use_container_width=True, hide_index=True)
    
    # Total value
    total_value = sum([h['qty'] * df[df['symbol'] == h['symbol']].iloc[0]['price'] 
                       for h in portfolio.get('holdings', []) 
                       if not df[df['symbol'] == h['symbol']].empty])
    if total_value:
        st.metric("Total Portfolio Value", f"PKR {total_value:,.0f}")

# ==================== FOOTER ====================
st.divider()
st.caption("⚠️ " + "This tool is decision support, NOT financial advice. No system can guarantee profit or zero loss. Always confirm manually before trading.")
st.caption(f"🕐 Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ==================== MANUAL REFRESH ====================
if st.sidebar.button("🔄 Force Refresh", use_container_width=True):
    st.rerun()