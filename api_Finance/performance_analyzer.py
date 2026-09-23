"""
performance_analyzer.py
CPerformanceAnalyzer - CAGR, drawdown, rolling returns, and benchmark
comparison for held funds, backed by the nav_history table.

Note: outputs are objective indicators (CAGR, drawdown %, momentum
flags, alpha vs benchmark) - not buy/sell recommendations.
"""

import matplotlib.pyplot as plt
from datetime import date, datetime
from typing import Dict, Optional

from api_Finance.database import CHoldingsDatabase
import config_db as config_db

############################################################################
#
class CPerformanceAnalyzer:
    def __init__(self, db: Optional[CHoldingsDatabase] = None):
        self.mDB = db or CHoldingsDatabase()

    CLR_BLUE, CLR_RED, CLR_RESET = "\033[94m", "\033[91m", "\033[0m"

    def _colorize(self, value: Optional[float], text: str) -> str:
        if value is None:
            return text
        return f"{self.CLR_BLUE if value >= 0 else self.CLR_RED}{text}{self.CLR_RESET}"

    # ------------------------------------------------------------------ #
    # Call once per day, after UpdateAllFundNAVs, to snapshot every
    # distinct fund's nav_latest into nav_history.
    # ------------------------------------------------------------------ #
    def RecordTodayHistory(self, owner_name: Optional[str] = None) -> None:
        strToday = date.today().isoformat()

        if self.mDB.nav_history_exists_for_date(strToday):
            print(f"NAV history already recorded for {strToday} skipping "
                  f"(run FetchNAVsAll for a new date first).")
            return
        
        rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
        seen = set()
        strToday = date.today().isoformat()
        count = 0
        for r in rows:
            fund_name = r["fund_name"]
            if fund_name in seen or r["nav_latest"] is None:
                continue
            seen.add(fund_name)
            self.mDB.record_nav_history(fund_name, strToday, r["nav_latest"])
            count += 1
        # End FOR
        print(f"Recorded NAV history for {count} distinct fund(s) on {strToday}.")
    # ------------------------------------------------------------------ #
    # Row count per date in nav_history sanity check for how many
    # distinct funds got a NAV recorded on each day.
    # ------------------------------------------------------------------ #
    def RowCountByDate(self) -> None:
        rows = self.mDB.fetch_nav_history_counts_by_date()
        if not rows:
            print("No NAV history recorded yet.")
            return

        print(f"\n{'Date':<12} {'Rows':>6}")
        print("-" * 20)
        total = 0
        for r in rows:
            print(f"{r['nav_date']:<12} {r['row_count']:>6}")
            total += r["row_count"]
        print("-" * 20)
        print(f"{'TOTAL':<12} {total:>6}")

    # ------------------------------------------------------------------ #
    # CAGR since purchase (statement_date -> today). Annualizing a return
    # over a very short holding period massively amplifies noise, so we
    # require a minimum holding period before computing it at all.
    # ------------------------------------------------------------------ #
    MIN_DAYS_FOR_CAGR = 30

    @classmethod
    def compute_cagr(cls, nav_base: float, nav_latest: float, statement_date: Optional[str]) -> Optional[float]:
        if not statement_date or nav_base <= 0:
            return None
        try:
            purchased = datetime.strptime(statement_date, "%d-%m-%Y").date()
        except ValueError:
            try:
                purchased = date.fromisoformat(statement_date)
            except ValueError:
                return None

        days_held = (date.today() - purchased).days
        if days_held < cls.MIN_DAYS_FOR_CAGR:
            return None

        years = days_held / 365.0
        return (nav_latest / nav_base) ** (1 / years) - 1

    # ------------------------------------------------------------------ #
    # Drawdown from all-time high (0 = at peak)
    # ------------------------------------------------------------------ #
    @staticmethod
    def compute_drawdown(nav_latest: float, nav_highest: Optional[float]) -> Optional[float]:
        if not nav_highest or nav_highest <= 0:
            return None
        return (nav_highest - nav_latest) / nav_highest

    # ------------------------------------------------------------------ #
    # % change over trailing `days`, from nav_history
    # ------------------------------------------------------------------ #
    def compute_rolling_return(self, fund_name: str, days: int) -> Optional[float]:
        history = self.mDB.fetch_nav_history(fund_name)
        if len(history) < 2:
            return None

        cutoff = date.today().toordinal() - days
        window = [h for h in history if date.fromisoformat(h["nav_date"]).toordinal() >= cutoff]
        if len(window) < 2:
            return None

        start_nav, end_nav = window[0]["nav"], window[-1]["nav"]
        if start_nav <= 0:
            return None
        return (end_nav - start_nav) / start_nav

    # ------------------------------------------------------------------ #
    # Momentum flag - neutral signal, not a recommendation
    # ------------------------------------------------------------------ #
    def below_recent_average(self, fund_name: str, days: int = 90) -> Optional[bool]:
        history = self.mDB.fetch_nav_history(fund_name)
        if not history:
            return None

        cutoff = date.today().toordinal() - days
        window = [h["nav"] for h in history if date.fromisoformat(h["nav_date"]).toordinal() >= cutoff]
        if not window:
            return None

        avg = sum(window) / len(window)
        return history[-1]["nav"] < avg

    # ------------------------------------------------------------------ #
    # Fund's rolling return vs its mapped benchmark's, over same window.
    # Requires an entry in db_config.BENCHMARK_MAP.
    # ------------------------------------------------------------------ #
    def compare_to_benchmark(self, fund_name: str, days: int = 365) -> Optional[Dict]:
        benchmark_name = config_db.BENCHMARK_MAP.get(fund_name)
        if not benchmark_name:
            return None

        fund_return = self.compute_rolling_return(fund_name, days)
        benchmark_return = self.compute_rolling_return(benchmark_name, days)
        if fund_return is None or benchmark_return is None:
            return None

        return {
            "fund_name": fund_name,
            "benchmark_name": benchmark_name,
            "fund_return": fund_return,
            "benchmark_return": benchmark_return,
            "alpha": fund_return - benchmark_return,
        }
    
    def _getCAGR(self, r):
        days_held = None
        if r["statement_date"]:
            try:
                purchased = datetime.strptime(r["statement_date"], "%d-%m-%Y").date()
            except ValueError:
                try:
                    purchased = date.fromisoformat(r["statement_date"])
                except ValueError:
                    purchased = None
            if purchased:
                days_held = (date.today() - purchased).days

        cagr = self.compute_cagr(r["nav_base"], r["nav_latest"], r["statement_date"]) \
            if r["nav_latest"] is not None else None

        if cagr is not None:
            cagr_str = self._colorize(cagr, f"{cagr:>8.2%}")
        elif days_held is not None and days_held < self.MIN_DAYS_FOR_CAGR:
            cagr_str = f"{'<30d':>9}"
        else:
            cagr_str = f"{'N/A':>9}"
        
        return cagr_str

    # ------------------------------------------------------------------ #
    # Prints objective indicators per fund. No buy/sell verdicts.
    # ------------------------------------------------------------------ #
    def PerformanceSummary(self, owner_name: Optional[str] = None) -> None:
        rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
        if not rows:
            print("No entries found.")
            return

        print(f"\n{'Fund':<40} {'CAGR':>9} {'Drawdown':>10} {'90d Trend':>10} {'Alpha (1y)':>11}")
        print("-" * 85)

        for r in rows:
            # 1
            fund_name = r["fund_name"]
            
            # 2
            cagr_str = self._getCAGR(r)

            # 3
            drawdown = self.compute_drawdown(r["nav_latest"], r["nav_highest"]) \
                if r["nav_latest"] is not None else None
            drawdown_str = f"{-drawdown:>9.2%}" if drawdown is not None else f"{'N/A':>10}"

            # 4
            below_avg = self.below_recent_average(fund_name)
            trend_str = ("below avg" if below_avg else "above avg") if below_avg is not None else "N/A"

            #  5
            bench = self.compare_to_benchmark(fund_name)
            alpha_str = self._colorize(bench["alpha"], f"{bench['alpha']:>10.2%}") if bench else f"{'N/A':>11}"

            # print line 
            print(f"{fund_name[:40]:<40} {cagr_str} {drawdown_str} {trend_str:>10} {alpha_str}")

        print("-" * 85)

    #-------------------------------------------------------------------------------------------------------
    # GRAPHS
    # ------------------------------------------------------------------ #
    # NAV history plot with horizontal reference lines for nav_highest
    # (blue) and nav_lowest (red), pulled from the holdings row.
    # ------------------------------------------------------------------ #
    def PlotFundNAV(self, fund_name: str, owner_name: Optional[str] = None,
                     since_date: Optional[str] = None, save_path: Optional[str] = None) -> None:

        rows = self.mDB.fetch_nav_history(fund_name, since_date=since_date)
        if not rows:
            print(f"No NAV history found for: {fund_name}")
            return

        try:
            listDate = [datetime.fromisoformat(h["nav_date"]) for h in rows]
            listNAVs = [h["nav"] for h in rows]

            row = self.mDB.fetch_entry(fund_name, owner_name)
            nav_base = row["nav_base"] if row else None
            nav_highest = row["nav_highest"] if row else None
            nav_lowest = row["nav_lowest"] if row else None

            fig, ax = plt.subplots(figsize=(10, 3))
            ax.plot(listDate, listNAVs, color="darkgreen", linewidth=1.5, marker="o", markersize=3, label="NAV")

            if nav_base is not None:
                ax.axhline(nav_base, color="black", linestyle="-", linewidth=1,
                        label=f"Base ({nav_base:.4f})")
            if nav_highest is not None:
                ax.axhline(nav_highest, color="blue", linestyle="--", linewidth=1,
                        label=f"Highest ({nav_highest:.4f})")
            if nav_lowest is not None:
                ax.axhline(nav_lowest, color="red", linestyle="--", linewidth=1,
                        label=f"Lowest ({nav_lowest:.4f})")

            ax.set_title(fund_name, fontsize=11, color="orange", fontweight="bold")
            ax.set_xlabel("Date")
            ax.set_ylabel("NAV")
            ax.legend(loc="best")
            ax.grid(True, alpha=0.3)
            fig.autofmt_xdate()
            fig.tight_layout()
        except Exception as e:
            print(e.getMessage())

        if save_path:
            fig.savefig(save_path, dpi=150)
            print(f"Saved plot to {save_path}")
        else:
            plt.show()
