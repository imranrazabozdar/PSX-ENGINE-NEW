"""
Create a sample database for Streamlit deployment
This creates psx_engine.db with realistic sample data
"""

import sqlite3
import json
from datetime import datetime, timedelta
import random

DB_PATH = "psx_engine.db"

# Sample stocks with realistic data
STOCKS = [
    {"symbol": "PSO", "sector": "Oil Marketing", "shariah": "Compliant (KMI-30)"},
    {"symbol": "MEBL", "sector": "Islamic Banking", "shariah": "Compliant (KMI-30)"},
    {"symbol": "LUCK", "sector": "Cement", "shariah": "Compliant (KMI-30)"},
    {"symbol": "FFC", "sector": "Fertilizer", "shariah": "Compliant (KMI-30)"},
    {"symbol": "OGDC", "sector": "Oil & Gas Exploration", "shariah": "Compliant (KMI-30)"},
    {"symbol": "MARI", "sector": "Oil & Gas Exploration", "shariah": "Compliant (KMI-30)"},
    {"symbol": "SYS", "sector": "Technology", "shariah": "Compliant (KMI-30)"},
    {"symbol": "HUBC", "sector": "Power Generation", "shariah": "Compliant (KMI-30)"},
    {"symbol": "EFERT", "sector": "Fertilizer", "shariah": "Compliant (KMI-30)"},
    {"symbol": "NRL", "sector": "Refinery", "shariah": "Compliant (KMI-30)"},
    {"symbol": "DGKC", "sector": "Cement", "shariah": "Compliant (KMI-30)"},
    {"symbol": "PPL", "sector": "Oil & Gas Exploration", "shariah": "Compliant (KMI-30)"},
    {"symbol": "FABL", "sector": "Islamic Banking", "shariah": "Compliant (Faysal Bank)"},
    {"symbol": "AIRLINK", "sector": "Technology", "shariah": "Compliant (KMI-30)"},
    {"symbol": "TREET", "sector": "Consumer", "shariah": "Compliant (KMI-30)"},
]

# Generate realistic signals
def generate_signal(score):
    if score >= 80:
        return "Strong Buy"
    elif score >= 70:
        return "Buy"
    elif score >= 60:
        return "Watch"
    elif score >= 50:
        return "Hold"
    else:
        return "Avoid"

def generate_risk(score):
    if score >= 75:
        return "Low"
    elif score >= 60:
        return "Medium"
    else:
        return "High"

def create_database():
    """Create the database with sample data"""
    
    # Remove existing database if it exists
    import os
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_time TEXT,
        symbol TEXT,
        price REAL,
        volume REAL,
        technical_score REAL,
        sentiment_score REAL,
        macro_news_score REAL,
        final_score REAL,
        signal TEXT,
        confidence REAL,
        stop_loss REAL,
        target1 REAL,
        target2 REAL,
        support REAL,
        resistance REAL,
        risk_level TEXT,
        shariah_status TEXT,
        data_quality TEXT,
        main_reason TEXT,
        main_risk TEXT,
        relative_strength REAL,
        market_regime TEXT,
        confluence INTEGER,
        cmf REAL,
        outcome TEXT
    )
    ''')
    
    # Create other tables
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS prices (
        symbol TEXT,
        ts TEXT,
        price REAL,
        volume REAL,
        source TEXT,
        PRIMARY KEY (symbol, ts)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fetched_at TEXT,
        source TEXT,
        title TEXT,
        link TEXT,
        published TEXT,
        sentiment REAL,
        symbols TEXT
    )
    ''')
    
    # Generate sample data
    now = datetime.now().isoformat()
    market_regime = "risk-on" if random.random() > 0.4 else "risk-off"
    
    reasons = [
        "Strong technical setup with breakout above resistance",
        "Bullish momentum with volume confirmation",
        "Consolidating near support with improving RSI",
        "Uptrend intact with healthy pullback",
        "Breaking down below key support level",
        "Overextended with bearish divergence",
        "Neutral consolidation phase",
        "Accumulation phase with increasing volume",
        "Distribution phase with decreasing volume",
        "Earnings beat expected next week"
    ]
    
    risks = [
        "Market volatility elevated",
        "Sector rotation risk",
        "High valuation multiple",
        "Low liquidity for large orders",
        "Earnings uncertainty next week",
        "Macro headwinds from oil prices",
        "Regulatory risk in sector"
    ]
    
    for i, stock in enumerate(STOCKS):
        # Generate random but realistic values
        base_price = random.uniform(100, 1500)
        score = random.uniform(40, 90)
        technical_score = score + random.uniform(-10, 10)
        sentiment_score = score + random.uniform(-15, 15)
        macro_score = score + random.uniform(-10, 10)
        
        final_score = (technical_score + sentiment_score + macro_score) / 3
        final_score = max(20, min(95, final_score))
        
        signal = generate_signal(final_score)
        risk_level = generate_risk(final_score)
        confidence = 50 + (final_score / 100) * 40 + random.uniform(-5, 5)
        confidence = max(25, min(95, confidence))
        
        stop_loss = base_price * random.uniform(0.88, 0.97)
        target1 = base_price * random.uniform(1.05, 1.15)
        target2 = base_price * random.uniform(1.10, 1.25)
        support = base_price * random.uniform(0.85, 0.95)
        resistance = base_price * random.uniform(1.05, 1.15)
        
        relative_strength = random.uniform(30, 80)
        
        cursor.execute('''
        INSERT INTO runs (
            run_time, symbol, price, volume, technical_score, sentiment_score,
            macro_news_score, final_score, signal, confidence, stop_loss,
            target1, target2, support, resistance, risk_level, shariah_status,
            data_quality, main_reason, main_risk, relative_strength,
            market_regime, confluence, cmf, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            now, stock["symbol"], round(base_price, 2), 
            round(random.uniform(100000, 2000000), 0),
            round(technical_score, 1), round(sentiment_score, 1),
            round(macro_score, 1), round(final_score, 1),
            signal, round(confidence, 1),
            round(stop_loss, 2), round(target1, 2), round(target2, 2),
            round(support, 2), round(resistance, 2),
            risk_level, stock["shariah"],
            "good", random.choice(reasons), random.choice(risks),
            round(relative_strength, 1), market_regime,
            random.randint(0, 4), round(random.uniform(-0.3, 0.5), 3),
            None
        ))
        
        # Add price history
        for days in range(1, 31):
            date = (datetime.now() - timedelta(days=days)).isoformat()
            price_variation = 1 + random.uniform(-0.03, 0.03)
            price = base_price * price_variation
            cursor.execute('''
            INSERT OR IGNORE INTO prices (symbol, ts, price, volume, source)
            VALUES (?, ?, ?, ?, ?)
            ''', (stock["symbol"], date, round(price, 2), 
                  round(random.uniform(100000, 2000000), 0), "sample_data"))
    
    # Add sample news
    news_titles = [
        ("PSO", "PSO announces new oil discovery in Sindh"),
        ("MEBL", "Meezan Bank reports strong quarterly earnings growth"),
        ("LUCK", "Lucky Cement expands into new markets"),
        ("FFC", "Fauji Fertilizer announces dividend increase"),
        ("OGDC", "OGDC starts production from new well"),
        ("MARI", "Mari Petroleum hits record production"),
        ("SYS", "Systems Limited wins new international contract"),
        ("HUBC", "Hub Power completes new power plant"),
        ("EFERT", "Engro Fertilizer to benefit from government subsidies"),
        ("NRL", "National Refinery to expand capacity"),
        ("PSO", "Petroleum dealers call off strike after talks"),
        ("MEBL", "Islamic banking sector shows strong growth"),
    ]
    
    for symbol, title in news_titles:
        cursor.execute('''
        INSERT OR IGNORE INTO news (fetched_at, source, title, published, symbols)
        VALUES (?, ?, ?, ?, ?)
        ''', (now, "Business Recorder", title, now, symbol))
    
    conn.commit()
    conn.close()
    
    print(f"✅ Database created successfully!")
    print(f"   - {len(STOCKS)} stocks added")
    print(f"   - 30 days of price history per stock")
    print(f"   - {len(news_titles)} news items added")
    print(f"   - File: {DB_PATH} ({os.path.getsize(DB_PATH)/1024:.1f} KB)")

if __name__ == "__main__":
    create_database()
