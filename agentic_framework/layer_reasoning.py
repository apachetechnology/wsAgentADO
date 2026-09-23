"""
reasoning.py
Reasoning layer - Paper Fig. 1 / Section 3.2: Task Planning Agent (TPA)
and Task Setup Agent (TSA), backed by a local Ollama model via
COllamaServer.

TPA: goal -> ordered subgoals (drawn from the closed
     agent_config.SUBGOAL_CATALOG), plus post-hoc reflection over the
     execution log (meta-reasoning / self-critique, cf. Reflexion/ReAct
     in the paper). Consults long-term memory for similar past runs.

TSA: subgoal -> concrete tool-chain step (tool name + arguments),
     using short-term memory and simple slot extraction from the goal
     text. One subgoal maps to exactly one tool here (tools.SUBGOAL_TO_TOOL),
     so the TSA's real job is argument extraction, not tool selection.

Both agents degrade gracefully: small local models occasionally return
malformed JSON, so every LLM call has a deterministic, rule-based
fallback. This is what keeps the framework usable with 1B-class models
rather than requiring a frontier model for the reasoning layer.
"""

import json
import re
from typing import Dict, List, Optional

from api_server.Ollama_server import COllamaServer

from config_agent import MODEL_TPA, MODEL_TSA, SUBGOAL_CATALOG
from agentic_framework.agent_memory import CAgentMemory
from agentic_framework.agent_tools import SUBGOAL_TO_TOOL

CURRENCY_SYMBOL = "\u20b9"  # ₹ — all figures in this system are INR

##################################################################################
def _find_json_span(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """Bracket-matched span, not a greedy regex - a naive `\\{.*\\}` with
    DOTALL spans from the first opening bracket to the LAST closing one
    in the whole response, which misfires if the model's prose contains
    any stray braces after the real JSON."""
    start = text.find(open_ch)
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find(open_ch, start + 1)
    return None

def _extract_json(text: str):
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        span = _find_json_span(text, open_ch, close_ch)
        if span:
            try:
                return json.loads(span)
            except json.JSONDecodeError:
                continue
    return None

def _grounded_facts(execution_log) -> Dict[str, str]:
    """
    Pull concrete, already-computed figures straight out of tool results
    so the reflection prompt has real numbers to paraphrase instead of
    inventing its own. Direct fix for the fabricated-NAV/return-figure
    hallucination observed in agentic runs (Paper Case Study A).
    """
    facts = {}
    for record in execution_log:
        if record.mStrStatus != "ok" or not isinstance(record.mResult, dict):
            continue
        result = record.mResult

        if record.mTool_Name == "portfolio_report":
            total_cost = result.get("total_cost_value")
            total_expected = result.get("total_expected_value")
            if total_cost is not None and total_expected is not None:
                pnl = total_expected - total_cost
                facts["portfolio_report"] = (
                    f"Total cost value {CURRENCY_SYMBOL}{total_cost:,.2f}, "
                    f"total expected value {CURRENCY_SYMBOL}{total_expected:,.2f}, "
                    f"P&L {CURRENCY_SYMBOL}{pnl:,.2f}."
                )

        elif record.mTool_Name == "flag_risk":
            flagged = result.get("flagged", [])
            facts["flag_risk"] = (
                f"{len(flagged)} fund(s) flagged past the "
                f"{result.get('threshold', 0):.0%} drawdown threshold."
            )

        elif record.mTool_Name == "update_navs":
            facts["update_navs"] = (
                f"{result.get('updated', 0)} of {result.get('total', 0)} fund(s) "
                f"had NAVs updated; {len(result.get('failures', []))} failure(s)."
            )

        elif record.mTool_Name == "performance_review":
            facts["performance_review"] = (
                f"Performance computed for {len(result.get('funds', []))} fund(s)."
            )

    return facts

############################################################################
#
class CTaskPlanningAgent:
    """TPA - long-term memory + reflective planning."""

    def __init__(self, ollama_server: COllamaServer, memory: CAgentMemory,
                 model: str = MODEL_TPA):
        self.mOS = ollama_server
        self.mMemory = memory
        self.mModel = model

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fallback_plan(goal: str) -> List[str]:
        goal_upper = goal.upper()
        keyword_map = {
            "update_navs":        ("UPDATE", "REFRESH", "LATEST NAV"),
            "record_history":     ("HISTORY", "SNAPSHOT"),
            "performance_review": ("PERFORMANCE", "CAGR", "REVIEW"),
            "flag_risk":          ("FLAG", "RISK", "DROP", "DRAWDOWN"),
            "portfolio_report":   ("SUMMARY", "REPORT", "P/L", "PROFIT"),
            "fund_lookup":        ("LOOKUP", "SEARCH", "FIND"),
            "add_fund":           ("ADD FUND", "NEW FUND", "NEW HOLDING"),
            "rename_fund":        ("RENAME",),
            "plot_fund":          ("PLOT", "CHART", "GRAPH"),
        }
        return [key for key, kws in keyword_map.items() if any(kw in goal_upper for kw in kws)]

    def plan(self, goal: str, context_summary: str) -> List[str]:
        catalog_lines = "\n".join(f'- "{k}": {v}' for k, v in SUBGOAL_CATALOG.items())
        recalled = self.mMemory.recall_similar(goal, top_n=2)
        memory_note = ""
        if recalled:
            memory_note = "Similar past run(s): " + "; ".join(
                f'"{r["goal"]}" -> {"succeeded" if r["success"] else "failed"}' for r in recalled
            )

        # system_prompt = (
        #     "You are the Task Planning Agent for a mutual-fund tracker. "
        #     "Choose which of the following subgoals (by exact key) are needed "
        #     "to satisfy the user's goal. Respond with ONLY a JSON array of "
        #     "subgoal keys, in the order they should run, nothing else.\n\n"
        #     f"Subgoals:\n{catalog_lines}"
        # )

        # Updated on 12/07/2026
        system_prompt = (
            "You are the Task Planning Agent for a mutual-fund tracker. "
            "Choose ONLY the subgoals (by exact key) that are actually needed for "
            "the user's goal below - most goals need just 1 to 4 subgoals. Do NOT "
            "return the entire list of keys shown below; that's a menu, not an "
            "answer. Respond with ONLY a JSON array of the chosen subgoal keys, "
            "in the order they should run, nothing else.\n\n"
            f"Subgoals:\n{catalog_lines}"
        )

        user_prompt = f"Portfolio context:\n{context_summary}\n\n{memory_note}\n\nGoal: {goal}"

        try:
            messages = [
                self.mOS.build_message("system", system_prompt),
                self.mOS.build_message("user", user_prompt),
            ]
            response = self.mOS.get_response(messages, aModel=self.mModel)
            parsed = _extract_json(response)
        except Exception:
            parsed = None

        subgoals = [s for s in (parsed or []) if isinstance(s, str) and s in SUBGOAL_CATALOG]
        if subgoals and set(subgoals) == set(SUBGOAL_CATALOG):
            print("[TPA] LLM returned the entire subgoal catalog - treating that as "
                "an echoed menu, not a real plan. Falling back to keyword planning.")
            subgoals = []

        if subgoals:
            return subgoals
        return self._fallback_plan(goal)

    # ------------------------------------------------------------------ #
    # Reflection (meta-reasoning / self-critique over the execution log)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reject_ungrounded_currency(self, text: str) -> Optional[str]:
        """
        A $ or 'USD' anywhere in the reflection means the model attached a
        number to a currency it was never given — that number wasn't in
        `facts` either, so it's fabricated, not just mislabeled. Discard the
        whole response rather than relabel the currency, which would just
        launder a hallucinated figure into a plausible-looking rupee one.
        """
        if re.search(r"\$|USD|dollars?", text, re.IGNORECASE):
            return None
        return text

    def reflect_old(self, goal: str, execution_log: List) -> Dict:
        ok_count = sum(1 for r in execution_log if r.mStrStatus == "ok")
        total = len(execution_log)
        rule_based_success = total > 0 and ok_count == total

        log_summary = "; ".join(
            f"{r.mTool_Name}: {r.mStrStatus}" + (f" ({r.mError})" if r.mError else "") for r in execution_log
        )

        try:
            messages = [
                self.mOS.build_message(
                    "system",
                    "You are the Task Planning Agent reflecting on a completed run. "
                    "In 1-2 sentences, summarize the outcome for the user in plain "
                    "language. Do not give investment advice."
                ),
                self.mOS.build_message(
                    "user", f"Goal: {goal}\nExecution log: {log_summary}"
                ),
            ]
            summary = self.mOS.get_response(messages, aModel=self.mModel).strip()
        except Exception:
            summary = (f"Completed {ok_count}/{total} step(s) successfully."
                       if total else "No applicable steps were identified for this goal.")

        return {"summary": summary, "success": rule_based_success,
                "steps_ok": ok_count, "steps_total": total}

    # Modified on 12/07/2026
    def reflect(self, goal: str, execution_log: List) -> Dict:
        ok_count = sum(1 for r in execution_log if r.mStrStatus == "ok")
        total = len(execution_log)
        rule_based_success = total > 0 and ok_count == total

        log_summary = "; ".join(
            f"{r.mTool_Name}: {r.mStrStatus}" + (f" ({r.mError})" if r.mError else "") for r in execution_log
        )
        facts = _grounded_facts(execution_log)
        facts_block = "\n".join(f"- {v}" for v in facts.values()) or \
            "(no monetary figures were computed in this run)"

        summary = None
        try:
            messages = [
                self.mOS.build_message(
                    "system",
                    "You are the Task Planning Agent reflecting on a completed run. "
                    "In 1-2 sentences, summarize the outcome for the user in plain "
                    "language. Do not give investment advice. "
                    "All monetary figures in this system are in Indian Rupees — "
                    "always use the \u20b9 symbol, never $ or USD. "
                    "Use ONLY the numbers given to you in the 'Facts' list below. "
                    "Do not calculate, estimate, or invent any NAV, price, "
                    "percentage, or amount that is not explicitly present there. "
                    "If no facts are given, describe the outcome qualitatively "
                    "with no numbers at all."
                ),
                self.mOS.build_message(
                    "user",
                    f"Goal: {goal}\nExecution log: {log_summary}\n\nFacts:\n{facts_block}"
                ),
            ]
            raw = self.mOS.get_response(messages, aModel=self.mModel).strip()
            summary = self._reject_ungrounded_currency(raw)
        except Exception:
            summary = None

        if not summary:
            summary = (f"Completed {ok_count}/{total} step(s) successfully."
                    if total else "No applicable steps were identified for this goal.")
            if facts:
                summary += " " + " ".join(facts.values())

        return {"summary": summary, "success": rule_based_success,
                "steps_ok": ok_count, "steps_total": total}

############################################################################
#
class CTaskSetupAgent:
    """TSA - decomposes a subgoal into a concrete tool-chain step."""

    def __init__(self, ollama_server: COllamaServer, memory: CAgentMemory,
                 model: str = MODEL_TSA):
        self.mOS = ollama_server
        self.mMemory = memory
        self.mModel = model

    def setup(self, subgoal: str, goal_text: str,
              default_owner: Optional[str] = None) -> Optional[Dict]:
        tool_name = SUBGOAL_TO_TOOL.get(subgoal)
        if tool_name is None:
            return None

        args = self._extract_args(subgoal, goal_text, default_owner)
        return {"tool": tool_name, "args": args}

    # ------------------------------------------------------------------ #
    # Argument extraction - LLM-assisted with a deterministic fallback.
    # ------------------------------------------------------------------ #
    def _extract_args(self, subgoal: str, goal_text: str,
                       default_owner: Optional[str]) -> Dict:
        args: Dict = {}

        if default_owner:
            args["owner_name"] = default_owner

        quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', goal_text)
        quoted_terms = [a or b for a, b in quoted]

        if subgoal == "flag_risk":
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', goal_text)
            if match:
                args["threshold"] = float(match.group(1)) / 100.0

        if subgoal == "fund_lookup" and quoted_terms:
            args["keyword"] = quoted_terms[0]

        if subgoal == "plot_fund" and quoted_terms:
            args["fund_name"] = quoted_terms[0]

        if subgoal == "rename_fund" and len(quoted_terms) >= 2:
            args["old_fund_name"], args["new_fund_name"] = quoted_terms[0], quoted_terms[1]

        if subgoal == "add_fund":
            # Structured additions are best supplied by the caller directly
            # (see orchestrator.run(..., extra_args=...)) - free-text
            # extraction of units/NAV from a sentence is unreliable enough
            # with a 1B model that we don't attempt it here.
            pass

        return args
