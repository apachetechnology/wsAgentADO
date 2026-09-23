"""
config.py
Central configuration for the Holdings Tracker.
"""

from pathlib import Path

# Folder where this package lives - _DB file sits alongside it by default.
BASE_DIR = Path(__file__).resolve().parent

# Fetched data
SCHEME_CACHE_PATH = BASE_DIR / "_DB/scheme_cache.json"

# SQLite database file path.
DB_PATH = BASE_DIR / "_DB/holdings.db"

# Name of the table holding fund entries.
TABLE_NAME = "holdings"

# Name of the NAV history table (one row per fund per day)
TABLE_NAME_NAV_HISTORY = "nav_history"

# Map each held fund to a benchmark/proxy fund_name (must also exist in
# the _DB or be resolvable via CFetchNAV) for relative-performance
# comparison. Fill in as you identify suitable index-fund proxies.
BENCHMARK_MAP = {
    "NIPPON INDIA LARGE CAP FUND-GROWTH": "NIFTY 50 INDEX FUND-GROWTH",
}

# ==================== YOUR FUND LIST ====================
LIST_FUNDS = [
    ("ADITYA BIRLA SUN LIFE SILVER ETF FOF REGULAR-GROWTH", 100000),
    ("HDFC SILVER ETF FOF REGULAR-GROWTH", 100000),
    ("HSBC INFRASTRUCTURE FUND-REGULAR GROWTH", 100000),
    ("ICICI PRUDENTIAL LARGE & MID CAP FUND-GROWTH", 100000),
    ("ICICI PRUDENTIAL SILVER ETF FOF-GROWTH", 100000),
    ("LIC MF CONSUMPTION FUND REGULAR-GROWTH", 100000),
    ("NIPPON INDIA LARGE CAP FUND-GROWTH", 100000),
    ("NIPPON INDIA SILVER ETF FOF REGULAR-GROWTH", 100000),
    ("SBI GOLD FUND-GROWTH", 100000),
    ("SBI SILVER ETF FOF REGULAR-GROWTH", 100000),
    ("TATA SILVER ETF FOF REGULAR-GROWTH", 100000),
]