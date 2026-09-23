"""
database.py
CHoldingsDatabase - a thin, class-based wrapper around a local SQLite
database used to store mutual fund / ETF holdings entries.

Design notes:
- One connection is opened per CHoldingsDatabase instance and reused.
- Each row is ONE holding (owner + fund). There is no more BASE/UPDATE/
  HIGHEST entry_type: nav_base is set once at load time, and nav_latest /
  nav_highest are updated in place on that same row as new NAVs come in.
- Use as a context manager to guarantee the connection is closed:

    with CHoldingsDatabase() as db:
        db.insert_holding(...)
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

import config_db as config_db

############################################################################
@dataclass
class HoldingEntry:
    """Represents a single row to insert into the holdings table."""
    owner_name: str
    fund_name: str
    holding_units: float
    nav_base: float
    cost_value: float
    statement_date: Optional[str] = None   # e.g. "2026-07-02"
    nav_latest: Optional[float] = None
    nav_highest: Optional[float] = None
    nav_lowest: Optional[float] = None
    nav_change: Optional[float] = None

############################################################################
#
class CHoldingsDatabase:
    """Handles connection, schema creation, and CRUD for holdings data."""

    def __init__(self, db_path: Path = config_db.DB_PATH):
        self.mDBPath = db_path
        self.mConn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_table()
        self._create_nav_history_table()

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    def _connect(self) -> None:
        self.mConn = sqlite3.connect(self.mDBPath)
        self.mConn.row_factory = sqlite3.Row

    def close(self) -> None:
        if self.mConn:
            self.mConn.close()
            self.mConn = None

    def __enter__(self) -> "CHoldingsDatabase":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _create_table(self) -> None:
        sql = f"""
        CREATE TABLE IF NOT EXISTS {config_db.TABLE_NAME} (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_name      TEXT    NOT NULL,
            fund_name       TEXT    NOT NULL,
            holding_units   REAL    NOT NULL,
            nav_base        REAL    NOT NULL,
            nav_latest      REAL,
            nav_highest     REAL,
            nav_lowest      REAL,
            nav_change      REAL,
            cost_value      REAL    NOT NULL,
            statement_date  TEXT,
            created_at      TEXT    NOT NULL
        );
        """
        self.mConn.execute(sql)
        self.mConn.commit()

    def _migrate_add_nav_lowest(self) -> None:
        """Adds nav_lowest to a pre-existing table that predates this column."""
        try:
            self.mConn.execute(
                f"ALTER TABLE {config_db.TABLE_NAME} ADD COLUMN nav_lowest REAL"
            )
            self.mConn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # ------------------------------------------------------------------ #
    # Insert
    # ------------------------------------------------------------------ #
    def insert_holding(self, entry: HoldingEntry) -> int:
        """Insert a single HoldingEntry. Returns the new row id."""
        sql = f"""
        INSERT INTO {config_db.TABLE_NAME}
            (owner_name, fund_name, holding_units, nav_base, nav_latest,
             nav_highest, nav_lowest, nav_change, cost_value, statement_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        cur = self.mConn.execute(sql, (
            entry.owner_name,
            entry.fund_name,
            entry.holding_units,
            entry.nav_base,
            entry.nav_latest,
            entry.nav_highest,
            entry.nav_lowest,
            entry.nav_change,
            entry.cost_value,
            entry.statement_date,
            datetime.now().isoformat(timespec="seconds"),
        ))
        self.mConn.commit()
        return cur.lastrowid

    def insert_many(self, entries: Iterable[HoldingEntry]) -> int:
        """Insert several HoldingEntry rows. Returns count inserted."""
        count = 0
        for entry in entries:
            self.insert_holding(entry)
            count += 1
        return count

    def delete_holding(self, owner_name: str, fund_name: str) -> int:
        """Delete the holdings row for this owner+fund. Returns rows deleted."""
        cur = self.mConn.execute(
            f"DELETE FROM {config_db.TABLE_NAME} "
            f"WHERE owner_name = ? AND fund_name = ?",
            (owner_name, fund_name),
        )
        self.mConn.commit()
        return cur.rowcount

    def delete_nav_history(self, fund_name: str) -> int:
        """Delete all nav_history rows for fund_name. Returns rows deleted."""
        cur = self.mConn.execute(
            f"DELETE FROM {config_db.TABLE_NAME_NAV_HISTORY} WHERE fund_name = ?",
            (fund_name,),
        )
        self.mConn.commit()
        return cur.rowcount
    
    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #
    def fetch_all(self) -> List[sqlite3.Row]:
        cur = self.mConn.execute(
            f"SELECT * FROM {config_db.TABLE_NAME} ORDER BY fund_name"
        )
        return cur.fetchall()

    def fetch_by_owner(self, owner_name: str) -> List[sqlite3.Row]:
        cur = self.mConn.execute(
            f"SELECT * FROM {config_db.TABLE_NAME} WHERE owner_name = ? "
            f"ORDER BY fund_name",
            (owner_name,),
        )
        return cur.fetchall()

    def fetch_by_fund_name(self, fund_name: str) -> List[sqlite3.Row]:
        cur = self.mConn.execute(
            f"SELECT * FROM {config_db.TABLE_NAME} WHERE fund_name LIKE ? "
            f"ORDER BY created_at",
            (f"%{fund_name}%",),
        )
        return cur.fetchall()

    def row_count(self) -> int:
        cur = self.mConn.execute(f"SELECT COUNT(*) FROM {config_db.TABLE_NAME}")
        return cur.fetchone()[0]

    def fetch_entry(self, fund_name: str, owner_name: Optional[str] = None) -> Optional[sqlite3.Row]:
        """
        The holding row for this fund - optionally scoped to an owner,
        since the same fund_name can be held by more than one owner.
        If more than one row matches, the most recently created wins.
        """
        if owner_name:
            cur = self.mConn.execute(
                f"SELECT * FROM {config_db.TABLE_NAME} "
                f"WHERE fund_name = ? AND owner_name = ? "
                f"ORDER BY created_at DESC LIMIT 1",
                (fund_name, owner_name),
            )
        else:
            cur = self.mConn.execute(
                f"SELECT * FROM {config_db.TABLE_NAME} "
                f"WHERE fund_name = ? "
                f"ORDER BY created_at DESC LIMIT 1",
                (fund_name,),
            )
        return cur.fetchone()

    def fetch_distinct_funds_by_owner(self, owner_name: str) -> List[str]:
        """Distinct fund_names held by this owner, alphabetical."""
        cur = self.mConn.execute(
            f"SELECT DISTINCT fund_name FROM {config_db.TABLE_NAME} "
            f"WHERE owner_name = ? ORDER BY fund_name",
            (owner_name,),
        )
        return [r["fund_name"] for r in cur.fetchall()]

    # ------------------------------------------------------------------ #
    # In-place NAV updates (replaces the old UPDATE / HIGHEST entry_type rows)
    # ------------------------------------------------------------------ #
    def update_nav_latest(self, owner_name: str, fund_name: str, nav: float) -> int:
        """Set nav_latest on the matching row. Returns rows affected."""
        cur = self.mConn.execute(
            f"UPDATE {config_db.TABLE_NAME} SET nav_latest = ? "
            f"WHERE owner_name = ? AND fund_name = ?",
            (nav, owner_name, fund_name),
        )
        self.mConn.commit()
        return cur.rowcount
    
    def update_nav_change(self, owner_name: str, fund_name: str, nav_change: float) -> int:
        """Set nav_latest on the matching row. Returns rows affected."""
        cur = self.mConn.execute(
            f"UPDATE {config_db.TABLE_NAME} SET nav_change = ? "
            f"WHERE owner_name = ? AND fund_name = ?",
            (nav_change, owner_name, fund_name),
        )
        self.mConn.commit()
        return cur.rowcount

    def update_nav_highest(self, owner_name: str, fund_name: str, nav: float) -> int:
        """Set nav_highest on the matching row. Returns rows affected."""
        cur = self.mConn.execute(
            f"UPDATE {config_db.TABLE_NAME} SET nav_highest = ? "
            f"WHERE owner_name = ? AND fund_name = ?",
            (nav, owner_name, fund_name),
        )
        self.mConn.commit()
        return cur.rowcount

    def update_nav_lowest(self, owner_name: str, fund_name: str, nav: float) -> int:
        """Set nav_lowest on the matching row. Returns rows affected."""
        cur = self.mConn.execute(
            f"UPDATE {config_db.TABLE_NAME} SET nav_lowest = ? "
            f"WHERE owner_name = ? AND fund_name = ?",
            (nav, owner_name, fund_name),
        )
        self.mConn.commit()
        return cur.rowcount
    
    # ------------------------------------------------------------------ #
    # In-place NAV updates (rename fund)
    # ------------------------------------------------------------------ #
    def rename_fund(self, old_fund_name: str, new_fund_name: str,
                    owner_name: Optional[str] = None) -> int:
        """Rename fund_name on matching row(s). Returns rows affected."""
        if owner_name:
            cur = self.mConn.execute(
                f"UPDATE {config_db.TABLE_NAME} SET fund_name = ? "
                f"WHERE fund_name = ? AND owner_name = ?",
                (new_fund_name, old_fund_name, owner_name),
            )
        else:
            cur = self.mConn.execute(
                f"UPDATE {config_db.TABLE_NAME} SET fund_name = ? "
                f"WHERE fund_name = ?",
                (new_fund_name, old_fund_name),
            )
        self.mConn.commit()
        return cur.rowcount
    
    # ------------------------------------------------------------------ #
    # NAV history - one row per fund per day (shared across owners;
    # a fund's NAV doesn't depend on who holds it)
    # ------------------------------------------------------------------ #
    def _create_nav_history_table(self) -> None:
        sql = f"""
        CREATE TABLE IF NOT EXISTS {config_db.TABLE_NAME_NAV_HISTORY} (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_name   TEXT    NOT NULL,
            nav_date    TEXT    NOT NULL,
            nav         REAL    NOT NULL,
            created_at  TEXT    NOT NULL,
            UNIQUE(fund_name, nav_date)
        );
        """
        self.mConn.execute(sql)
        self.mConn.commit()

    def record_nav_history(self, fund_name: str, nav_date: str, nav: float) -> None:
        """
        Insert today's NAV for fund_name. Re-running on the same day
        overwrites via UNIQUE(fund_name, nav_date), so this is safe to
        call multiple times per day.
        """
        sql = f"""
        INSERT INTO {config_db.TABLE_NAME_NAV_HISTORY} (fund_name, nav_date, nav, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(fund_name, nav_date) DO UPDATE SET nav = excluded.nav
        """
        self.mConn.execute(sql, (
            fund_name, nav_date, nav,
            datetime.now().isoformat(timespec="seconds"),
        ))
        self.mConn.commit()

    def fetch_nav_history(self, fund_name: str, since_date: Optional[str] = None) -> List[sqlite3.Row]:
        """All recorded NAVs for fund_name, oldest first."""
        if since_date:
            cur = self.mConn.execute(
                f"SELECT * FROM {config_db.TABLE_NAME_NAV_HISTORY} "
                f"WHERE fund_name = ? AND nav_date >= ? ORDER BY nav_date",
                (fund_name, since_date),
            )
        else:
            cur = self.mConn.execute(
                f"SELECT * FROM {config_db.TABLE_NAME_NAV_HISTORY} "
                f"WHERE fund_name = ? ORDER BY nav_date",
                (fund_name,),
            )
        return cur.fetchall()
    
    def fetch_nav_history_counts_by_date(self) -> List[sqlite3.Row]:
        """Row count per nav_date across all funds, most recent first."""
        cur = self.mConn.execute(
            f"SELECT nav_date, COUNT(*) AS row_count "
            f"FROM {config_db.TABLE_NAME_NAV_HISTORY} "
            f"GROUP BY nav_date ORDER BY nav_date DESC"
        )
        return cur.fetchall()
    
    def nav_history_exists_for_date(self, nav_date: str) -> bool:
        """True if nav_history already has at least one row for nav_date."""
        cur = self.mConn.execute(
            f"SELECT 1 FROM {config_db.TABLE_NAME_NAV_HISTORY} "
            f"WHERE nav_date = ? LIMIT 1",
            (nav_date,),
        )
        return cur.fetchone() is not None
    
######################################################################################################
if __name__ == "__main__":
    objHD = CHoldingsDatabase()
    objHD._migrate_add_nav_lowest()