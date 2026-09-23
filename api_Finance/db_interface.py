"""
main.py
Entry point: creates/opens the local SQLite database, loads the
statement holdings as base rows (skipping if already loaded), and
prints a summary table.

Run:
    python main.py
"""
from datetime import date
import textwrap

from typing import List, Optional

from api_Finance.database import CHoldingsDatabase, HoldingEntry
from api_Finance.nav_fetcher import CFetchNAV

############################################################################
#
class CDBInterface:
    def __init__(self):
        try:
            self.mDB = CHoldingsDatabase()
            self.mObjFNAV = CFetchNAV()
        except Exception as e:
            print(e.getMessage())

    #----------------------------------------------------------------------
    # Helper function
    NAME_WIDTH = 30
    NAV_WIDTH = 10
    CLR_BLUE = "\033[94m"
    CLR_RED = "\033[91m"
    CLR_RESET = "\033[0m"

    def _colorize_single(self, value: Optional[float], text: str) -> str:
        if value is None:
            return text
        color = self.CLR_BLUE if value >= 0 else self.CLR_RED
        return f"{color}{text}{self.CLR_RESET}"

    def _colorize_double(self, expected: Optional[float], cost: float, text: str) -> str:
        if expected is None:
            return text
        color = self.CLR_BLUE if expected >= cost else self.CLR_RED
        return f"{color}{text}{self.CLR_RESET}" 

    #-----------------------------------------------------------------------
    # NAV
    def FetchNAVsAll(self, bForceUpdate=False):
        self.mObjFNAV.GetNAVsAll(bForceUpdate=bForceUpdate)

    def GetFundNAV(self, fund_name):
        nav_data = self.mObjFNAV.resolve_fund(fund_name)
        return nav_data['nav']
    
    def LookupNAVs(self, strKey):
        self.mObjFNAV.lookup_nav_by_keyword(strKey)

    # One-time call
    def GetFundHigestNAV(self, fund_name):
        nav_data = self.mObjFNAV.resolve_fund_highest(fund_name)
        return nav_data
    
    # One-time call
    def GetFundLowestNAV(self, fund_name):
        nav_data = self.mObjFNAV.resolve_fund_lowest(fund_name)
        return nav_data

    #--------------------------------------------------------------
    # Database
    def _load(self, raw_rows: Optional[List[tuple]],
              statement_date: str) -> int:
        """
        Insert each (owner_name, fund_name, holding_units, nav_base,
        cost_value) tuple as a row. nav_latest / nav_highest start
        unset and are filled in later via UpdateFundNAV / UpdateHighestNAV.
        Returns the number of rows inserted.
        """
        entries = [
            HoldingEntry(
                owner_name=owner_name,
                fund_name=fund_name,
                holding_units=units,
                nav_base=nav_base,
                cost_value=cost_value,
                statement_date=statement_date,
            )
            for owner_name, fund_name, units, nav_base, cost_value in raw_rows
        ]
        return self.mDB.insert_many(entries)

    def _update_highest_nav(self, fund_name, nav, row):
        """
        row is the existing holding row (sqlite3.Row) for this fund.
        Updates nav_highest in place if nav is a new high. Returns the
        new highest nav, or -1 if there was no new high.
        """
        current_highest = row["nav_highest"]
        if current_highest is not None and nav <= current_highest:
            print(f"======>No new high: current={nav} <= "
                  f"stored high={current_highest}")
            return -1

        self.mDB.update_nav_highest(row["owner_name"], fund_name, nav)
        return nav
    
    def _update_lowest_nav(self, fund_name, nav, row):
        """
        row is the existing holding row (sqlite3.Row) for this fund.
        Updates nav_lowest in place if nav is a new low. Returns the
        new lowest nav, or -1 if there was no new low.
        """
        current_lowest = row["nav_lowest"]
        if current_lowest is not None and nav >= current_lowest:
            print(f"======>No new low: current={nav} >= "
                  f"stored low={current_lowest}")
            return -1

        self.mDB.update_nav_lowest(row["owner_name"], fund_name, nav)
        return nav

    #--------------------------------------------------------------
    # Add base entry
    def LoadBaseEntrics(self, owner, RAW_HOLDINGS, STATEMENT_DATE):
        existing = self.mDB.fetch_by_owner(owner)
        if existing:
            print(f"Database already has {len(existing)} entries for owner "
                f"{owner} (statement_date={existing[0]['statement_date']}). "
                f"Skipping reload.")
        else:
            inserted = self._load(RAW_HOLDINGS, STATEMENT_DATE)
            print(f"Inserted {inserted} entries "
                f"for statement date {STATEMENT_DATE}.")

    def AddNewBaseFund(self, owner_name: str, fund_name: str,
                        holding_units: float, nav_base: float) -> None:
        """
        Insert a new holding row for a fund not yet tracked (or adding
        another lot). cost_value is computed as holding_units * nav_base,
        statement_date defaults to today.
        """
        cost_value = holding_units * nav_base
        inserted = self._load(
            [(owner_name, fund_name, holding_units, nav_base, cost_value)],
            date.today().isoformat(),
        )
        print(f"Inserted {inserted} entry for {fund_name}: "
              f"Units={holding_units}, NAV={nav_base}, Value={cost_value:,.2f}")
        
    #--------------------------------------------------------------
    # Delete the record
    def DeleteFund(self, owner_name: str, fund_name: str) -> None:
        """
        Delete the holding entry for owner_name/fund_name. nav_history is
        shared across owners (no owner_name column), so it's only purged
        if no other owner still holds this fund.
        """
        deleted = self.mDB.delete_holding(owner_name, fund_name)
        if not deleted:
            print(f"No entry found for {fund_name} (owner={owner_name}).")
            return

        print(f"Deleted {deleted} holding row(s) for {fund_name} (owner={owner_name}).")

        remaining = [r for r in self.mDB.fetch_by_fund_name(fund_name) if r["fund_name"] == fund_name]
        if remaining:
            print(f"{fund_name} still held by other owner(s) - nav_history retained.")
        else:
            hist_deleted = self.mDB.delete_nav_history(fund_name)
            print(f"No other owner holds {fund_name} - removed {hist_deleted} nav_history row(s).")

    # Update records
    # Corrections - rename a fund, or directly override nav_highest
    def UpdateFundName(self, old_fund_name: str, new_fund_name: str,
                        owner_name: Optional[str] = None) -> None:
        """
        Rename fund_name in place. If owner_name is given, only that
        owner's row is renamed; otherwise every row with old_fund_name is.
        """
        updated = self.mDB.rename_fund(old_fund_name, new_fund_name, owner_name)
        if updated:
            print(f"Renamed '{old_fund_name}' -> '{new_fund_name}' "
                  f"({updated} row(s) updated).")
        else:
            print(f"No rows found for fund_name: {old_fund_name}")
 
    def ReviseHighestNav(self, fund_name: str, nav_highest: float) -> None:
        """
        Manually set nav_highest for every row matching fund_name exactly,
        across all owners - overrides whatever is currently stored (no
        "only if higher" check). Use to correct bad data, e.g. from a
        mismatched NAV lookup.
        """
        rows = [r for r in self.mDB.fetch_all() if r["fund_name"] == fund_name]
        if not rows:
            print(f"No entry found for: {fund_name}")
            return

        updated = 0
        for row in rows:
            self.mDB.update_nav_highest(row["owner_name"], fund_name, nav_highest)
            print(f"nav_highest set for {fund_name} (owner={row['owner_name']}): {nav_highest}")
            updated += 1

        print(f"\nUpdated {updated} row(s) for {fund_name}.")

    def ReviseLowestNav(self, fund_name: str, nav_lowest: float) -> None:
        """
        Manually set nav_highest for every row matching fund_name exactly,
        across all owners - overrides whatever is currently stored (no
        "only if higher" check). Use to correct bad data, e.g. from a
        mismatched NAV lookup.
        """
        rows = [r for r in self.mDB.fetch_all() if r["fund_name"] == fund_name]
        if not rows:
            print(f"No entry found for: {fund_name}")
            return

        updated = 0
        for row in rows:
            self.mDB.update_nav_lowest(row["owner_name"], fund_name, nav_lowest)
            print(f"nav_lowest set for {fund_name} (owner={row['owner_name']}): {nav_lowest}")
            updated += 1

        print(f"\nUpdated {updated} row(s) for {fund_name}.")

    # Fetch funds
    def UniqueFundNames(self) -> List[str]:
        """Distinct fund_name values across all owners, alphabetical."""
        rows = self.mDB.fetch_all()
        return sorted({r["fund_name"] for r in rows})

    def Funds_by_owner(self, owner_name: str):
        print('Find funds for ', owner_name)
        listFunds = []
        listRows = self.mDB.fetch_by_owner(owner_name)
        for r in listRows:
            strValue = r['fund_name']
            print(strValue)
            listFunds.append(strValue)

        return listFunds

    def Funds_by_name(self, fund_name: str):
        print('Find funds for ', fund_name)
        listFunds = []
        listRows = self.mDB.fetch_by_fund_name(fund_name)
        for r in listRows:
            strValue = r['fund_name']
            print(strValue)
            listFunds.append(strValue)

        return listFunds

    # NAV updates - requires FetchNAVsAll() to have been called first.
    # Updates nav_latest (and nav_highest, if it's a new high) in place
    # for every holding in the database, or just those for one owner.
    def UpdateAllFundNAVs(self, owner_name: Optional[str] = None) -> None:
        rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
        if not rows:
            print("No entries found.")
            return

        updated = 0
        unchanged = 0
        new_highs = 0
        new_lows = 0
        for nI, row in enumerate(rows):
            fund_name = row["fund_name"]
            nav_latest = self.GetFundNAV(fund_name)
            if nav_latest is None:
                print(f"Could not resolve current NAV for: {fund_name}")
                continue

            nav_previous = row["nav_latest"]
            if nav_previous is not None and nav_latest == nav_previous:
                print(f"#{nI}. No change for {fund_name}: NAV={nav_latest} - skipping update")
                unchanged += 1
                continue

            nav_change = (nav_latest - nav_previous) if nav_previous is not None else None
            if nav_change is not None:
                self.mDB.update_nav_change(row["owner_name"], fund_name, nav_change)

            self.mDB.update_nav_latest(row["owner_name"], fund_name, nav_latest)
            expected_value = row["holding_units"] * nav_latest
            print(f"#{nI}. NAV updated for {fund_name}: NAV={nav_latest}, Value={expected_value:,.2f}")
            updated += 1

            if self._update_highest_nav(fund_name, nav_latest, row) != -1:
                new_highs += 1

            if self._update_lowest_nav(fund_name, nav_latest, row) != -1:
                new_lows += 1

        print(f"\nUpdated NAV for {updated}/{len(rows)} entries "
              f"({unchanged} unchanged, {new_highs} new high{'s' if new_highs != 1 else ''}, "
              f"{new_lows} new low{'s' if new_lows != 1 else ''}).")

    def FundsSummaryV1(self, owner_name: Optional[str] = None) -> None:
        rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
        if not rows:
            print("No entries found.")
            return

        print(f"\n{'Fund House & Scheme Name':<55} {'Units':>12} {'NAV Base':>10} "
              f"{'NAV Latest':>11} {'NAV Highest':>12} {'Cost Value':>16}")
        print("-" * 120)
        total_cost_value = 0.0
        for r in rows:
            nav_latest_str = f"{r['nav_latest']:>11.4f}" if r["nav_latest"] is not None else f"{'N/A':>11}"
            nav_highest_str = f"{r['nav_highest']:>12.4f}" if r["nav_highest"] is not None else f"{'N/A':>12}"
            print(f"{r['fund_name']:<55} {r['holding_units']:>12.3f} "
                f"{r['nav_base']:>10.4f} {nav_latest_str} {nav_highest_str} "
                f"{r['cost_value']:>16,.2f}")
            total_cost_value += r["cost_value"]

        print("-" * 120)
        print(f"{'TOTAL':<55} {'':>12} {'':>10} {'':>11} {'':>12} {total_cost_value:>16,.2f}")
        print(f"\nTotal entries in DB: {len(rows)}")

    def FundsSummaryV2(self, owner_name: Optional[str] = None) -> None:
        """
        Fund House & Scheme Name | Units | NAV Base | NAV Latest | Nav Change |
        Cost Value | Expected Value (units * nav_latest, colorized
        vs cost value)
        """
        rows = self.mDB.fetch_by_owner(owner_name) if owner_name else self.mDB.fetch_all()
        if not rows:
            print("No entries found.")
            return

        header = (f"{'Fund House & Scheme Name':<{self.NAME_WIDTH}} {'Units':>{self.NAV_WIDTH}} {'NAV Base':>{self.NAV_WIDTH}} "
                  f"{'NAV Latest':>{self.NAV_WIDTH}} {'NAV Change':>{self.NAV_WIDTH}} {'Cost Value':>15} {'Expected Value':>15}")
        print("_" * len(header))
        print(f"\n{header}")
        print("_" * len(header))

        total_expected_value = 0.0
        total_cost_value = 0.0

        for r in rows:
            units = r["holding_units"]
            
            nav_base = r["nav_base"]
            
            nav_latest = r["nav_latest"]
            nav_latest_str = f"{nav_latest:>10.4f}" if nav_latest is not None else f"{'N/A':>{self.NAV_WIDTH}}"

            nav_change = r["nav_change"]
            nav_change_str = self._colorize_single(nav_change, f"{nav_change:>10.4f}" if nav_change is not None else f"{'N/A':>{self.NAV_WIDTH}}")
            
            cost_value = r["cost_value"]

            expected_value = units * nav_latest if nav_latest is not None else None
            expected_value_str = self._colorize_double(expected_value, cost_value,
                f"{expected_value:>15,.2f}" if expected_value is not None else f"{'N/A':>15}")

            name_lines = textwrap.wrap(r["fund_name"], width=self.NAME_WIDTH) or [""]
            while len(name_lines) < 2:
                name_lines.append("")
            name_lines = name_lines[:2]
            if len(textwrap.wrap(r["fund_name"], width=self.NAME_WIDTH)) > 2:
                name_lines[1] = name_lines[1][:self.NAME_WIDTH - 1] + "…"

            print(f"{name_lines[0]:<{self.NAME_WIDTH}} {units:>10.3f} {nav_base:>10.4f} "
                  f"{nav_latest_str} {nav_change_str} {cost_value:>15,.2f} {expected_value_str}")
            if name_lines[1]:
                print(f"{name_lines[1]:<{self.NAME_WIDTH}}")

            if expected_value is not None:
                total_expected_value += expected_value
            total_cost_value += cost_value

        total_expected_str = self._colorize_double(total_expected_value, total_cost_value,
            f"{total_expected_value:>15,.2f}")

        print("_" * len(header))
        print(f"{'TOTAL':<{self.NAME_WIDTH}} {'':>{self.NAV_WIDTH}} {'':>{self.NAV_WIDTH}} "
              f"{'':>{self.NAV_WIDTH}} {'':>{self.NAV_WIDTH}} {total_cost_value:>15,.2f} {total_expected_str}")
        print("_" * len(header))
        print(f"\nTotal entries in DB: {len(rows)}")

    def OwnerProfitSummary(self, owner_name: str) -> None:
        """
        For every fund held by owner_name: compare nav_base vs nav_latest
        (realized-so-far P/L), and nav_base vs nav_highest (projected
        profit at the best NAV seen). Long fund names wrap onto a second
        line so the numeric columns stay aligned.
        """
        rows = self.mDB.fetch_by_owner(owner_name)
        if not rows:
            print(f"No entries found for owner: {owner_name}")
            return

        print(f"\nOwner: {owner_name} | Funds: {len(rows)}")

        header = (f"{'Fund House & Scheme Name':<{self.NAME_WIDTH}} {'Units':>{self.NAV_WIDTH}} {'Base NAV':>{self.NAV_WIDTH}} "
                  f"{'Latest NAV':>{self.NAV_WIDTH}} {'P/L':>12} {'Highest NAV':>{self.NAV_WIDTH}} {'Projected P/L':>12} {'Lowest NAV':>{self.NAV_WIDTH}}")
        print("_" * len(header))
        print(f"\n{header}")
        print("_" * len(header))

        total_pl = 0.0
        total_projected = 0.0

        for r in rows:
            units = r["holding_units"]
            base_nav = r["nav_base"]

            latest_nav = r["nav_latest"]
            latest_nav_str = f"{latest_nav:>10.4f}" if latest_nav is not None else f"{'N/A':>10}"

            pl = (latest_nav - base_nav) * units if latest_nav is not None else None
            pl_str = self._colorize_single(pl, f"{pl:>12,.2f}" if pl is not None else f"{'N/A':>12}")

            highest_nav = r["nav_highest"]
            highest_nav_str = f"{highest_nav:>10.4f}" if highest_nav is not None else f"{'N/A':>{self.NAV_WIDTH}}"

            projected_pl = (highest_nav - base_nav) * units if highest_nav is not None else None
            projected_str = self._colorize_single(projected_pl, f"{projected_pl:>12,.2f}" if projected_pl is not None else f"{'N/A':>12}")

            lowest_nav = r["nav_lowest"]
            lowest_nav_str = f"{lowest_nav:>10.4f}" if lowest_nav is not None else f"{'N/A':>{self.NAV_WIDTH}}"

            name_lines = textwrap.wrap(r["fund_name"], width=self.NAME_WIDTH) or [""]
            while len(name_lines) < 2:
                name_lines.append("")
            name_lines = name_lines[:2]
            if len(textwrap.wrap(r["fund_name"], width=self.NAME_WIDTH)) > 2:
                name_lines[1] = name_lines[1][:self.NAME_WIDTH - 1] + "…"

            print(f"{name_lines[0]:<{self.NAME_WIDTH}} {units:>10.3f} {base_nav:>10.4f} "
                  f"{latest_nav_str} {pl_str} {highest_nav_str} {projected_str} {lowest_nav_str}")
            if name_lines[1]:
                print(f"{name_lines[1]:<{self.NAME_WIDTH}}")

            if pl is not None:
                total_pl += pl
            if projected_pl is not None:
                total_projected += projected_pl

        total_pl_str = self._colorize_single(total_pl, f"{total_pl:>12,.2f}")
        total_projected_str = self._colorize_single(total_projected, f"{total_projected:>12,.2f}")

        print("_" * len(header))
        print(f"{'TOTAL':<{self.NAME_WIDTH}} {'':>{self.NAV_WIDTH}} {'':>{self.NAV_WIDTH}} {'':>{self.NAV_WIDTH}} "
              f"{total_pl_str} {'':>{self.NAV_WIDTH}} {total_projected_str} {'':>{self.NAV_WIDTH}}")
        print("_" * len(header))

    # Cross-store erasure orchestration - a memory-only erasure isn't a real GDPR 
    # erasure if the holdings/nav_history data still lives in CHoldingsDatabase
    def ForgetDataSubject(self, owner_name: str, agent_memory=None) -> None:
        """
        Full erasure across both stores for one data subject: holdings +
        (if provided) agentic long-term memory. Ties GDPR erasure to a single
        callable, rather than leaving it split across modules.
        """
        for fund_name in self.Funds_by_owner(owner_name):
            self.DeleteFund(owner_name, fund_name)
        if agent_memory is not None:
            agent_memory.forget_owner(owner_name)

if __name__ == "__main__":
    objConsole = CDBInterface()
