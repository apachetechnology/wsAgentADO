"""
tools.py
Action layer - "Tool Integration" (Paper Fig. 1): API connectors,
document/report generation, and database interfaces, exposed as a
uniform registry the Task Setup Agent can wire into tool-chains and
the execution environment can invoke under sandboxed permissions.

Each tool wraps an existing, already-tested class (CHoldingsDatabase,
CFetchNAV, CPerformanceAnalyzer, CDBInterface) rather than reimplementing
logic - the agentic layer is an orchestration skin over the current
fund-tracker, not a replacement for it.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from api_Finance.database import CHoldingsDatabase
from api_Finance.nav_fetcher import CFetchNAV
from api_Finance.performance_analyzer import CPerformanceAnalyzer
from api_Finance.db_interface import CDBInterface

############################################################################
# One subgoal (agent_config.SUBGOAL_CATALOG) maps to exactly one tool here.
SUBGOAL_TO_TOOL = {
    "update_navs":        "update_navs",
    "record_history":     "record_history",
    "performance_review": "performance_review",
    "flag_risk":          "flag_risk",
    "portfolio_report":   "portfolio_report",
    "fund_lookup":        "fund_lookup",
    "add_fund":           "add_fund",
    "rename_fund":        "rename_fund",
    "plot_fund":          "plot_fund",
}


############################################################################
@dataclass
class CTool:
    mTool_name: str
    mTool_permissions: set
    mTool_func: Callable
    mTool_description: str = "None"


############################################################################
#
class CToolRegistry:
    """
    Builds and holds the Action layer's callable tools. Instantiated
    once per orchestrator with live references to the domain objects
    (db/fetcher/analyzer/console) it wraps.
    """

    def __init__(self, db: CHoldingsDatabase, fetcher: CFetchNAV,
                 analyzer: CPerformanceAnalyzer, DBInterface: CDBInterface):
        self.mDB = db
        self.mFetcher = fetcher
        self.mAnalyzer = analyzer
        self.mDBInterface = DBInterface
        self.mTools: Dict[str, CTool] = {}
        self._register_all()

    def _register(self, name: str, permissions: set, func: Callable, description: str) -> None:
        self.mTools[name] = CTool(name, permissions, func, description)

    def get(self, name: str) -> Optional[CTool]:
        return self.mTools.get(name)

    # ------------------------------------------------------------------ #
    # Tool implementations
    # ------------------------------------------------------------------ #
    def _register_all(self) -> None:

        # def update_navs(owner_name: Optional[str] = None, **_) -> Dict:
        #     rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
        #     updated, failures = 0, []
        #     for row in rows:
        #         fund_name = row["fund_name"]
        #         nav_data = self.mFetcher.resolve_fund(fund_name)
        #         if not nav_data or nav_data.get("nav") is None:
        #             failures.append(fund_name)
        #             continue
        #         nav_latest = nav_data["nav"]
        #         prev = row["nav_latest"]
        #         if prev is not None:
        #             self.mDB.update_nav_change(row["owner_name"], fund_name, nav_latest - prev)
        #         self.mDB.update_nav_latest(row["owner_name"], fund_name, nav_latest)
        #         if row["nav_highest"] is None or nav_latest > row["nav_highest"]:
        #             self.mDB.update_nav_highest(row["owner_name"], fund_name, nav_latest)
        #         if row["nav_lowest"] is None or nav_latest < row["nav_lowest"]:
        #             self.mDB.update_nav_lowest(row["owner_name"], fund_name, nav_latest)
        #         updated += 1
        #     return {"updated": updated, "total": len(rows), "failures": failures}

        # Add to agent_tools.py inside _register_all(), replacing the current update_navs body

        MAX_DAILY_MOVE = 0.30  # 30% single-day move is implausible for these fund types

        def update_navs(owner_name: Optional[str] = None, **_) -> Dict:
            rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
            updated, failures, rejected = 0, [], []
            for row in rows:
                fund_name = row["fund_name"]
                nav_data = self.mFetcher.resolve_fund(fund_name)
                if not nav_data or nav_data.get("nav") is None:
                    failures.append(fund_name)
                    continue

                nav_latest = nav_data["nav"]
                prev = row["nav_latest"]

                # --- Evasion-resistant validation (Table 17: Security -> "Sensor
                # data validation" / paper text: "adversarial robustness checks") ---
                if nav_latest <= 0:
                    rejected.append({"fund_name": fund_name, "reason": "non-positive NAV"})
                    continue
                if prev is not None and prev > 0:
                    move = abs(nav_latest - prev) / prev
                    if move > MAX_DAILY_MOVE:
                        rejected.append({"fund_name": fund_name, "reason": f"{move:.0%} jump vs prior NAV"})
                        continue
                # --------------------------------------------------------------

                if prev is not None:
                    self.mDB.update_nav_change(row["owner_name"], fund_name, nav_latest - prev)
                self.mDB.update_nav_latest(row["owner_name"], fund_name, nav_latest)
                if row["nav_highest"] is None or nav_latest > row["nav_highest"]:
                    self.mDB.update_nav_highest(row["owner_name"], fund_name, nav_latest)
                if row["nav_lowest"] is None or nav_latest < row["nav_lowest"]:
                    self.mDB.update_nav_lowest(row["owner_name"], fund_name, nav_latest)
                updated += 1
            return {"updated": updated, "total": len(rows), "failures": failures, "rejected": rejected}

        def record_history(owner_name: Optional[str] = None, **_) -> Dict:
            self.mAnalyzer.RecordTodayHistory(owner_name)
            return {"recorded": True}

        def performance_review(owner_name: Optional[str] = None, **_) -> Dict:
            rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
            out = []
            for r in rows:
                nav_latest = r["nav_latest"]
                cagr = (self.mAnalyzer.compute_cagr(r["nav_base"], nav_latest, r["statement_date"])
                        if nav_latest is not None else None)
                drawdown = (self.mAnalyzer.compute_drawdown(nav_latest, r["nav_highest"])
                            if nav_latest is not None else None)
                below_avg = self.mAnalyzer.below_recent_average(r["fund_name"])
                bench = self.mAnalyzer.compare_to_benchmark(r["fund_name"])
                out.append({
                    "fund_name": r["fund_name"], "owner_name": r["owner_name"],
                    "cagr": cagr, "drawdown": drawdown, "below_recent_average": below_avg,
                    "alpha_vs_benchmark": bench["alpha"] if bench else None,
                })
            return {"funds": out}

        def flag_risk(owner_name: Optional[str] = None, threshold: float = 0.10, **_) -> Dict:
            rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
            flagged = []
            for r in rows:
                if r["nav_latest"] is None:
                    continue
                dd = self.mAnalyzer.compute_drawdown(r["nav_latest"], r["nav_highest"])
                if dd is not None and dd >= threshold:
                    flagged.append({"fund_name": r["fund_name"], "owner_name": r["owner_name"],
                                     "drawdown": dd})
            # Objective indicator only - not a buy/sell recommendation.
            return {"threshold": threshold, "flagged": flagged}

        def portfolio_report(owner_name: Optional[str] = None, **_) -> Dict:
            rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
            total_cost, total_expected = 0.0, 0.0
            funds = []
            for r in rows:
                cost = r["cost_value"]
                nav_latest = r["nav_latest"]
                expected = r["holding_units"] * nav_latest if nav_latest is not None else None
                funds.append({"fund_name": r["fund_name"], "owner_name": r["owner_name"],
                               "cost_value": cost, "expected_value": expected})
                total_cost += cost
                if expected is not None:
                    total_expected += expected
            return {"funds": funds, "total_cost_value": total_cost,
                     "total_expected_value": total_expected}

        def fund_lookup(keyword: str, **_) -> Dict:
            results = self.mFetcher.lookup_nav_by_keyword(keyword)
            return {"keyword": keyword, "results": results}

        def add_fund(owner_name: str, fund_name: str, holding_units: float,
                     nav_base: float, **_) -> Dict:
            self.mDBInterface.AddNewBaseFund(owner_name, fund_name, holding_units, nav_base)
            return {"added": fund_name, "owner_name": owner_name}

        def rename_fund(old_fund_name: str, new_fund_name: str,
                        owner_name: Optional[str] = None, **_) -> Dict:
            updated = self.mDB.rename_fund(old_fund_name, new_fund_name, owner_name)
            return {"old_fund_name": old_fund_name, "new_fund_name": new_fund_name,
                    "rows_updated": updated}

        def plot_fund(fund_name: str, owner_name: Optional[str] = None,
                      save_path: Optional[str] = None, **_) -> Dict:
            self.mAnalyzer.PlotFundNAV(fund_name, owner_name=owner_name, save_path=save_path)
            return {"fund_name": fund_name, "saved_to": save_path}

        self._register("update_navs", {"NETWORK", "WRITE"}, update_navs,
                        "Fetch latest NAVs and update nav_latest/highest/lowest in place.")
        self._register("record_history", {"WRITE"}, record_history,
                        "Snapshot today's nav_latest into nav_history.")
        self._register("performance_review", {"READ", "COMPUTE"}, performance_review,
                        "CAGR / drawdown / trend / alpha per fund.")
        self._register("flag_risk", {"READ", "COMPUTE"}, flag_risk,
                        "Flag funds drawn down more than `threshold` from peak.")
        self._register("portfolio_report", {"READ", "COMPUTE"}, portfolio_report,
                        "Holdings, cost value, and expected value summary.")
        self._register("fund_lookup", {"READ", "NETWORK"}, fund_lookup,
                        "Keyword search for a scheme + its latest NAV.")
        self._register("add_fund", {"WRITE"}, add_fund,
                        "Insert a new holding row.")
        self._register("rename_fund", {"WRITE"}, rename_fund,
                        "Rename a fund_name across matching rows.")
        self._register("plot_fund", {"READ", "PLOT"}, plot_fund,
                        "Plot NAV history with base/highest/lowest reference lines.")
