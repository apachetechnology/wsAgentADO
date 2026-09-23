"""
perception.py
CPerceptionLayer - Paper Fig. 1 "Perception Layer": initiates task
processing once tasks are scheduled by the orchestration layer, and
acquires external and contextual data from sensors, databases, and
digital interfaces.

Here:
  - "database"        -> CHoldingsDatabase (holdings + nav_history)
  - "sensor"           -> CFetchNAV (the live mfapi.in NAV feed)
  - "digital interface"-> the compact text summary handed to the
                          reasoning layer's LLM calls
"""

from typing import Dict, List, Optional

from api_Finance.database import CHoldingsDatabase
from api_Finance.nav_fetcher import CFetchNAV

############################################################################
#
class CPerceptionLayer:
    def __init__(self, db: CHoldingsDatabase, fetcher: CFetchNAV):
        self.mDB = db
        self.mFetcher = fetcher

    # ------------------------------------------------------------------ #
    # Database acquisition
    # ------------------------------------------------------------------ #
    def gather_portfolio_snapshot(self, owner_name: Optional[str] = None) -> List[Dict]:
        rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
        return [dict(r) for r in rows]

    def gather_fund_context(self, fund_name: str,
                             owner_name: Optional[str] = None) -> Optional[Dict]:
        row = self.mDB.fetch_entry(fund_name, owner_name)
        if row is None:
            return None
        context = dict(row)
        context["nav_history"] = [
            dict(h) for h in self.mDB.fetch_nav_history(fund_name)
        ]
        return context

    # ------------------------------------------------------------------ #
    # Sensor acquisition (live market data)
    # ------------------------------------------------------------------ #
    def sense_market(self, fund_name: str) -> Optional[Dict]:
        return self.mFetcher.resolve_fund(fund_name)

    # ------------------------------------------------------------------ #
    # Digital interface - compact context for the reasoning layer.
    # Small local LLMs do better with a short, structured brief than a
    # full row dump, so this intentionally caps to a handful of funds.
    # ------------------------------------------------------------------ #
    def describe_context(self, snapshot: List[Dict], max_funds: int = 8) -> str:
        if not snapshot:
            return "Portfolio is empty - no holdings on record."

        owners = sorted({r["owner_name"] for r in snapshot})
        lines = [f"Owners on record: {', '.join(owners)}.",
                 f"Total holdings rows: {len(snapshot)}."]

        for r in snapshot[:max_funds]:
            nav_latest = r.get("nav_latest")
            nav_str = f"{nav_latest:.4f}" if nav_latest is not None else "N/A"
            lines.append(
                f"- [{r['owner_name']}] {r['fund_name'][:50]}: "
                f"units={r['holding_units']:.2f}, nav_latest={nav_str}"
            )
        if len(snapshot) > max_funds:
            lines.append(f"...and {len(snapshot) - max_funds} more.")

        return "\n".join(lines)
