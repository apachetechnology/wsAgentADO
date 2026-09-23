"""
memory.py
CAgentMemory - long-term (episodic) memory backing the Task Planning
Agent's reflective loop, plus a lightweight short-term memory used by
the Task Setup Agent (Paper Fig. 1: "Long-term Memory", "Short-term
Memory", "Feedback Loops").

Long-term memory is a small sqlite log of past orchestrator runs
(goal, plan, outcome). Recall is a plain keyword-overlap score rather
than a vector store - this keeps the framework dependency-free; swap
in a real embedding index later without touching the rest of the
architecture (perception/reasoning/action are all memory-agnostic
callers of this class).
"""

import json
import sqlite3

from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, List, Optional

############################################################################
#
from config_agent import (
    AGENT_MEMORY_DB_PATH,
    SHORT_TERM_MEMORY_TURNS,
)

############################################################################
#
class CAgentMemory:
    """Episodic long-term memory (sqlite) + in-RAM short-term memory."""

    def __init__(self, db_path: Path = AGENT_MEMORY_DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.mConn = sqlite3.connect(db_path)
        self.mConn.row_factory = sqlite3.Row
        self._create_table()

        # Short-term memory: recent (subgoal, tool, result) turns for the
        # *current* run - reset per orchestrator invocation.
        self.mShortTerm: Deque[Dict] = deque(maxlen=SHORT_TERM_MEMORY_TURNS)

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _create_table(self) -> None:
        self.mConn.execute("""
            CREATE TABLE IF NOT EXISTS agent_episodes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_name    TEXT,
                goal          TEXT NOT NULL,
                subgoals_json TEXT,
                results_json  TEXT,
                reflection    TEXT,
                success       INTEGER,
                quarantined   INTEGER DEFAULT 0,
                created_at    TEXT NOT NULL
            );
        """)
        self.mConn.execute("""
            CREATE TABLE IF NOT EXISTS privacy_audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                action      TEXT NOT NULL,     -- 'purge' | 'erase' | 'quarantine'
                detail      TEXT,              -- e.g. "3 rows, owner=<redacted>"
                created_at  TEXT NOT NULL
            );
        """)
        self.mConn.commit()

    # ------------------------------------------------------------------ #
    # Long-term (episodic) memory
    # Data minimization at write time (GDPR Art. 5(1)(c) - minimization by design)
    # ------------------------------------------------------------------ #
    _PII_ARG_KEYS = {"owner_name"}  # redact before persisting to results_json

    def record_episode(self, goal: str, subgoals: List[str], results: List[Dict],
                        reflection: str, success: bool,
                        owner_name: Optional[str] = None) -> int:
        # Minimize: strip PII out of the serialized tool-args blob; the identity
        # is captured once, in its own indexed column, instead of scattered
        # through free-text goal strings and nested JSON.
        redacted_results = []
        for r in results:
            r = dict(r)
            if isinstance(r.get("args"), dict):
                r["args"] = {k: ("<redacted>" if k in self._PII_ARG_KEYS else v)
                            for k, v in r["args"].items()}
            redacted_results.append(r)

        cur = self.mConn.execute(
            "INSERT INTO agent_episodes "
            "(owner_name, goal, subgoals_json, results_json, reflection, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (owner_name, goal, json.dumps(subgoals),
            json.dumps(redacted_results, default=str), reflection, int(success),
            datetime.now().isoformat(timespec="seconds")),
        )
        self.mConn.commit()
        return cur.lastrowid

    @staticmethod
    def _keyword_overlap(a: str, b: str) -> float:
        tokens_a = set(a.upper().split())
        tokens_b = set(b.upper().split())
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    def recall_similar(self, goal: str, top_n: int = 3) -> List[sqlite3.Row]:
        """
        Semantic-ish recall: rank past episodes by keyword overlap with
        the new goal. Good enough to remind the TPA "you've handled
        something like this before, and here's how it went."
        """
        cur = self.mConn.execute(
            "SELECT * FROM agent_episodes ORDER BY id DESC LIMIT 200"
        )
        rows = cur.fetchall()
        scored = [(self._keyword_overlap(goal, r["goal"]), r) for r in rows]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [r for _, r in scored[:top_n]]

    # ------------------------------------------------------------------ #
    # Short-term memory
    # ------------------------------------------------------------------ #
    def add_short_term(self, subgoal: str, tool: str, ok: bool,
                        note: Optional[str] = None) -> None:
        self.mShortTerm.append({
            "subgoal": subgoal, "tool": tool, "ok": ok, "note": note,
        })

    def get_short_term(self) -> List[Dict]:
        return list(self.mShortTerm)

    def reset_short_term(self) -> None:
        self.mShortTerm.clear()

    def close(self) -> None:
        self.mConn.close()

    def check_subgoal_bias(self, max_share: float = 0.6, min_episodes: int = 20) -> Optional[str]:
        """
        Lightweight bias test (Table 17: AI/ML Risk Mgmt -> 'Bias testing').
        Flags if the planner has become skewed toward one subgoal far beyond
        what a healthy mix of user goals would produce - a signature of
        either prompt-injection steering or memory poisoning biasing recall.
        Returns a warning string, or None if within bounds.
        """
        rows = self.mConn.execute(
            "SELECT subgoals_json FROM agent_episodes ORDER BY id DESC LIMIT 200"
        ).fetchall()
        if len(rows) < min_episodes:
            return None
        counts: Dict[str, int] = {}
        total = 0
        for r in rows:
            for sg in json.loads(r["subgoals_json"]):
                counts[sg] = counts.get(sg, 0) + 1
                total += 1
        if total == 0:
            return None
        worst_key, worst_count = max(counts.items(), key=lambda kv: kv[1])
        share = worst_count / total
        if share > max_share:
            return (f"Subgoal '{worst_key}' accounts for {share:.0%} of recent "
                    f"plans (>{max_share:.0%} threshold) - possible bias/poisoning.")
        return None
    
    # Storage limitation (GDPR Art. 5(1)(e))
    # Added on 11/07/2026
    def purge_older_than(self, days: int = 90) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self.mConn.execute("DELETE FROM agent_episodes WHERE created_at < ?", (cutoff,))
        self.mConn.commit()
        self._log_privacy_action("purge", f"{cur.rowcount} row(s) older than {days}d")
        return cur.rowcount
    
    # Right to erasure (GDPR Art. 17)
    # Added on 11/07/2026
    def forget_owner(self, owner_name: str) -> int:
        """
        Erase all long-term memory tied to one data subject. Also strips any
        residual PII from goal text as a fallback, in case older rows (created
        before the owner_name column existed) still carry the identifier in
        free text.
        """
        cur = self.mConn.execute(
            "DELETE FROM agent_episodes WHERE owner_name = ?", (owner_name,)
        )
        deleted = cur.rowcount
        # Fallback for legacy rows lacking the structured column:
        cur2 = self.mConn.execute(
            "DELETE FROM agent_episodes WHERE goal LIKE ?", (f"%{owner_name}%",)
        )
        deleted += cur2.rowcount
        self.mConn.commit()
        self._log_privacy_action("erase", f"{deleted} row(s) for owner=<redacted>")
        return deleted

    # Right to erasure (GDPR Art. 17)
    # Added on 11/07/2026
    def _log_privacy_action(self, action: str, detail: str) -> None:
        """Accountability trail (GDPR Art. 5(2)) - records that an erasure/
        retention action occurred, without retaining the erased PII itself."""
        self.mConn.execute(
            "INSERT INTO privacy_audit_log (action, detail, created_at) VALUES (?, ?, ?)",
            (action, detail, datetime.now().isoformat(timespec="seconds")),
        )
        self.mConn.commit()

    # Cross-store erasure orchestration - a memory-only erasure isn't a real GDPR 
    # erasure if the holdings/nav_history data still lives in CHoldingsDatabase
    # Added on 11/07/2026
    def quarantine_episode(self, episode_id: int, reason: str) -> None:
        """
        Excludes a specific episode from future recall_similar() results
        without deleting the audit record outright - useful when
        check_subgoal_bias() or manual review flags an entry as suspect
        (e.g. poisoned goal text steering the planner). This is the same
        erasure/retention machinery built for GDPR, repurposed as an
        incident-response tool for Data Poisoning.
        """
        self.mConn.execute(
            "UPDATE agent_episodes SET quarantined = 1 WHERE id = ?", (episode_id,)
        )
        self.mConn.commit()
        self._log_privacy_action("quarantine", f"episode {episode_id}: {reason}")

