"""
nav_fetcher.py
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import json
from datetime import date
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter, Retry

from config_db import SCHEME_CACHE_PATH, LIST_FUNDS

############################################################################
#
@dataclass
class SchemeMatch:
    scheme_code: str
    scheme_name: str
    score: float


############################################################################
#
class CFetchNAV:
    cMFAPI_ALL_SCHEMES_URL = "https://api.mfapi.in/mf"
    cMFAPI_LATEST_NAV_URL = "https://api.mfapi.in/mf/{scheme_code}/latest"
    cMFAPI_HISTORY_URL = "https://api.mfapi.in/mf/{scheme_code}"

    # Fill this in for any fund that still doesn't resolve confidently -
    # checked first, before any fuzzy matching.
    dictMANUAL_SCHEME_CODE_OVERRIDES: Dict[str, str] = {
        # "YOUR FUND NAME EXACTLY AS IN fund_list": "123456",
    }

    # Terms to exclude unless the query itself asks for them - these are
    # different payout options of the *same* scheme, not different schemes.
    cEXCLUDED_PLAN_TERMS = ("IDCW", "DIVIDEND", "BONUS")

    def __init__(self):
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    # ------------------------------------------------------------------ #
    # Normalization: make shorthand and official names comparable
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize(name: str) -> str:
        text = name.upper()
        # Drop parenthetical asides like "(erstwhile Bluechip Fund)" - these
        # are naming-history footnotes, not part of the scheme's identity,
        # and their extra length was unfairly dragging down seq_ratio for
        # the otherwise-correct match.
        text = re.sub(r"\([^)]*\)", " ", text)
        text = text.replace("&", " AND ")
        text = text.replace("-", " ")
        # Canonicalize spelled-out variants down to the short forms your
        # statement uses, so both sides end up looking the same.
        text = re.sub(r"\bFUND\s+OF\s+FUNDS?\b", "FOF", text)
        text = re.sub(r"\bMUTUAL\s+FUND\b", "MF", text)
        text = re.sub(r"\bPLAN\b", "", text)
        text = re.sub(r"\bOPTION\b", "", text)
        text = re.sub(r"[^A-Z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    # ------------------------------------------------------------------ #
    # Scoring: token overlap (order-independent) + sequence similarity
    # ------------------------------------------------------------------ #
    @staticmethod
    def _score(query_norm: str, candidate_norm: str) -> float:
        query_tokens = set(query_norm.split())
        cand_tokens = set(candidate_norm.split())
        if not query_tokens or not cand_tokens:
            return 0.0

        overlap = len(query_tokens & cand_tokens)
        jaccard = overlap / len(query_tokens | cand_tokens)
        coverage = overlap / len(query_tokens)  # how much of *our* name is present
        precision = overlap / len(cand_tokens)   # how much of the *candidate* is extra/unexplained
        seq_ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio()

        # Weight coverage highest: every word in our shorthand should show
        # up in the official name. Jaccard and seq_ratio break remaining ties.
        return (0.4 * coverage) + (0.2 * precision) + (0.2 * jaccard) + (0.2 * seq_ratio)

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    def search_fund_by_name(self, fund_name: str, top_n: int = 5) -> List[SchemeMatch]:
        """
        Rank every cached scheme against fund_name and return the top_n
        best matches (best first). No network call - uses the cache
        built once in __init__.
        """
        if not hasattr(self, "_all_schemes"):
            self.GetNAVsAll(bForceUpdate=False)

        query_norm = self._normalize(fund_name)
        query_wants_idcw = any(term in query_norm for term in self.cEXCLUDED_PLAN_TERMS)
        query_wants_direct = "DIRECT" in query_norm

        candidates: List[SchemeMatch] = []
        for scheme in self._all_schemes:
            code = str(scheme["schemeCode"])
            cand_norm = self._normalized_cache[code]

            if not query_wants_idcw and any(term in cand_norm for term in self.cEXCLUDED_PLAN_TERMS):
                continue
            # Prefer the plan type that matches (Regular is the default
            # when the query doesn't say "Direct" - these are bank/
            # distributor-sold folios).
            cand_is_direct = "DIRECT" in cand_norm
            if query_wants_direct != cand_is_direct:
                continue

            score = self._score(query_norm, cand_norm)
            if score > 0:
                candidates.append(SchemeMatch(code, scheme["schemeName"], score))

        candidates.sort(key=lambda m: m.score, reverse=True)
        return candidates[:top_n]

    # ------------------------------------------------------------------ #
    # NAV lookup
    # ------------------------------------------------------------------ #
    def get_latest_nav(self, scheme_code: str) -> Optional[Dict]:
        """Fetch latest NAV for a given scheme code using mfapi.in"""
        url = self.cMFAPI_LATEST_NAV_URL.format(scheme_code=scheme_code)
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "SUCCESS" and data.get("data"):
                nav_data = data["data"][0]
                raw_nav = nav_data.get("nav")
                return {
                    "fund_name": data.get("meta", {}).get("scheme_name", "Unknown"),
                    "nav": float(raw_nav) if raw_nav is not None else None,
                    "date": nav_data.get("date"),
                    "scheme_code": scheme_code,
                }
        except requests.RequestException as e:
            print(f"Error fetching NAV for {scheme_code}: {e}")
        return None
    
    # ------------------------------------------------------------------ #
    # Resolve one fund end-to-end, with manual overrides checked first
    # ------------------------------------------------------------------ #
    def resolve_fund(self, fund_name: str, min_score: float = 0.55) -> Optional[Dict]:
        if fund_name in self.dictMANUAL_SCHEME_CODE_OVERRIDES:
            scheme_code = self.dictMANUAL_SCHEME_CODE_OVERRIDES[fund_name]
            return self.get_latest_nav(scheme_code)

        matches = self.search_fund_by_name(fund_name, top_n=5)
        if not matches or matches[0].score < min_score:
            print(f"✗ No confident match for: {fund_name}")
            if matches:
                print("   Closest candidates:")
                for m in matches[:3]:
                    print(f"     [{m.score:.2f}] {m.scheme_name}  (code {m.scheme_code})")
                print("   -> Add the right one to MANUAL_SCHEME_CODE_OVERRIDES above.\n")
            return None

        best = matches[0]
        nav_data = self.get_latest_nav(best.scheme_code)
        if nav_data:
            nav_data["match_score"] = best.score
        return nav_data
    
    # Public APIs
    
    # ------------------------------------------------------------------ #
    # One-time full list download (fixes the repeated-download problem)
    # ------------------------------------------------------------------ #
    def CheckCache(self, strToday):
        """Load today's cache if present and valid. Returns True on a cache hit."""
        try:
            with open(SCHEME_CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("date") == strToday:
                self._all_schemes = cached["schemes"]
                self._normalized_cache = {
                    str(s["schemeCode"]): self._normalize(s["schemeName"])
                    for s in self._all_schemes
                }
                print(f"Loaded {len(self._all_schemes)} schemes from local cache "
                        f"(date={strToday}).\n")
                return True
            else:
                print(f"Cache is stale (cached={cached.get('date')}, "
                        f"today={strToday}) - refreshing.")
                SCHEME_CACHE_PATH.unlink()
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"Cache read failed ({e}) - refreshing.")
            if SCHEME_CACHE_PATH.exists():
                SCHEME_CACHE_PATH.unlink()
        return False

    def DownloadAllNAVs(self) -> None:
        print("Downloading full scheme list from mfapi.in (one-time)...")

        self._all_schemes: List[Dict] = []          # raw [{schemeCode, schemeName}, ...]
        self._normalized_cache: Dict[str, str] = {}  # schemeCode -> normalized name
        resp = self.session.get(self.cMFAPI_ALL_SCHEMES_URL, timeout=30)
        resp.raise_for_status()
        self._all_schemes = resp.json()
        for scheme in self._all_schemes:
            code = str(scheme["schemeCode"])
            self._normalized_cache[code] = self._normalize(scheme["schemeName"])
    
    def GetNAVsAll(self, bForceUpdate) -> None:
        strToday = date.today().isoformat()

        # Try to use today's cache first - no network call needed.
        cache_hit = False
        if bForceUpdate == False and SCHEME_CACHE_PATH.exists():
            cache_hit = self.CheckCache(strToday)

        if not cache_hit:
            # No valid cache for today (missing, stale, or bForceUpdate) -
            # fetch fresh from mfapi.in.
            self.DownloadAllNAVs()

            # Persist today's fetch, overwriting any previous cache.
            with open(SCHEME_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"date": strToday, "schemes": self._all_schemes}, f)

            print(f"Loaded {len(self._all_schemes)} schemes and cached to "
                f"{SCHEME_CACHE_PATH.name}.\n")
            
    # ------------------------------------------------------------------ #
    # Keyword search over the cache (no network call)
    # ------------------------------------------------------------------ #
    def find_by_keyword(self, keyword: str, top_n: int = 20) -> List[Dict]:
        """
        Case-insensitive substring search over the cached scheme list.
        GetNAVsAll() must have been called first. Returns up to top_n
        matches as [{"scheme_code": ..., "scheme_name": ...}, ...].
        """
        if not hasattr(self, "_all_schemes"):
            print("Cache not loaded - call GetNAVsAll() first.")
            return []

        key = keyword.upper()
        matches = [
            {"scheme_code": str(s["schemeCode"]), "scheme_name": s["schemeName"]}
            for s in self._all_schemes
            if key in s["schemeName"].upper()
        ]

        if len(matches) > top_n:
            print(f"{len(matches)} schemes match '{keyword}' - showing first {top_n}. "
                  f"Narrow the keyword for a tighter list.")

        return matches[:top_n]

    def lookup_nav_by_keyword(self, keyword: str, top_n: int = 20) -> List[Dict]:
        """
        Find every cached scheme whose name contains keyword, then fetch
        the latest NAV for each match (one network call per match - keep
        keyword specific enough that top_n doesn't get hit). Prints a
        summary and returns the list of nav_data dicts.
        """
        matches = self.find_by_keyword(keyword, top_n=top_n)
        if not matches:
            print(f"No cached schemes match: {keyword}")
            return []

        print(f"Found {len(matches)} scheme(s) matching '{keyword}':\n")
        results = []
        for m in matches:
            nav_data = self.get_latest_nav(m["scheme_code"])
            if nav_data:
                results.append(nav_data)
                print(f"[{m['scheme_code']}] {nav_data['fund_name']}")
                print(f"   NAV : ₹{nav_data['nav']} as on {nav_data['date']}\n")
            else:
                print(f"[{m['scheme_code']}] {m['scheme_name']} - NAV fetch failed\n")

        return results
    
    # ------------------------------------------------------------------ #
    # Highest NAV lookup - full history scan (not just since-tracked high)
    # ------------------------------------------------------------------ #
    def _get_highest_nav(self, scheme_code: str) -> Optional[Dict]:
        """
        Fetch the complete NAV history for scheme_code and return the
        highest NAV ever recorded, along with the date it occurred.
        """
        url = self.cMFAPI_HISTORY_URL.format(scheme_code=scheme_code)
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()

            history = data.get("data")
            if not history:
                return None

            best_entry = max(
                history,
                key=lambda entry: float(entry["nav"]) if entry.get("nav") is not None else float("-inf"),
            )

            return {
                "fund_name": data.get("meta", {}).get("scheme_name", "Unknown"),
                "nav_highest": float(best_entry["nav"]),
                "date": best_entry["date"],
                "scheme_code": scheme_code,
            }
        except requests.RequestException as e:
            print(f"Error fetching NAV history for {scheme_code}: {e}")
        return None

    # ------------------------------------------------------------------ #
    # Resolve one fund end-to-end to its all-time highest NAV
    # ------------------------------------------------------------------ #
    def resolve_fund_highest(self, fund_name: str, min_score: float = 0.55) -> Optional[Dict]:
        if fund_name in self.dictMANUAL_SCHEME_CODE_OVERRIDES:
            scheme_code = self.dictMANUAL_SCHEME_CODE_OVERRIDES[fund_name]
            return self._get_highest_nav(scheme_code)

        matches = self.search_fund_by_name(fund_name, top_n=5)
        if not matches or matches[0].score < min_score:
            print(f"✗ No confident match for: {fund_name}")
            return None

        best = matches[0]
        nav_data = self._get_highest_nav(best.scheme_code)
        if nav_data:
            nav_data["match_score"] = best.score
        return nav_data
    
    # ------------------------------------------------------------------ #
    # Lowest NAV lookup - full history scan
    # ------------------------------------------------------------------ #
    def _get_lowest_nav(self, scheme_code: str) -> Optional[Dict]:
        """
        Fetch the complete NAV history for scheme_code and return the
        lowest NAV ever recorded, along with the date it occurred.
        """
        url = self.cMFAPI_HISTORY_URL.format(scheme_code=scheme_code)
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()

            history = data.get("data")
            if not history:
                return None

            best_entry = min(
                history,
                key=lambda entry: float(entry["nav"]) if entry.get("nav") is not None else float("inf"),
            )

            return {
                "fund_name": data.get("meta", {}).get("scheme_name", "Unknown"),
                "nav_lowest": float(best_entry["nav"]),
                "date": best_entry["date"],
                "scheme_code": scheme_code,
            }
        except requests.RequestException as e:
            print(f"Error fetching NAV history for {scheme_code}: {e}")
        return None

    # ------------------------------------------------------------------ #
    # Resolve one fund end-to-end to its all-time lowest NAV
    # ------------------------------------------------------------------ #
    def resolve_fund_lowest(self, fund_name: str, min_score: float = 0.55) -> Optional[Dict]:
        if fund_name in self.dictMANUAL_SCHEME_CODE_OVERRIDES:
            scheme_code = self.dictMANUAL_SCHEME_CODE_OVERRIDES[fund_name]
            return self._get_lowest_nav(scheme_code)

        matches = self.search_fund_by_name(fund_name, top_n=5)
        if not matches or matches[0].score < min_score:
            print(f"✗ No confident match for: {fund_name}")
            return None

        best = matches[0]
        nav_data = self._get_lowest_nav(best.scheme_code)
        if nav_data:
            nav_data["match_score"] = best.score
        return nav_data

# ==================== MAIN EXECUTION ====================
if __name__ == "__main__":
    print("Fetching latest NAV for Mutual Funds...\n")

    objFNAV = CFetchNAV()
    results = []

    objFNAV.GetNAVsAll()
    for strFund_Name in LIST_FUNDS:
        print(f"Searching: {strFund_Name}")
        nav_data = objFNAV.resolve_fund(strFund_Name)

        if nav_data:
            results.append(nav_data)
            score = nav_data.get("match_score")
            score_note = f" (match {score:.2f})" if score is not None else ""
            print(f"✓ {nav_data['fund_name']}{score_note}")
            print(f"   NAV : ₹{nav_data['nav']} as on {nav_data['date']}\n")

    # Summary Table
    print("=" * 80)
    print("FINAL NAV SUMMARY")
    print("=" * 80)
    for r in results:
        print(f"{r['fund_name'][:60]:<60} | NAV: ₹{r['nav']:<8} | {r['date']}")

    unresolved = len(LIST_FUNDS) - len(results)
    if unresolved:
        print(f"\n{unresolved} fund(s) unresolved - see candidates above and add "
              f"overrides to dictMANUAL_SCHEME_CODE_OVERRIDES.")
