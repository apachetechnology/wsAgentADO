"""
agent_config.py
Configuration for the agentic-AI fund-management framework
(see Paper Section 3.2, "Baseline architecture" - orchestration,
perception, reasoning, action layers).
"""

from pathlib import Path
from typing import Dict
import textwrap

BASE_DIR = Path(__file__).resolve().parent

# ------------------------------------------------------------------ #
# Reasoning layer - model per role. Small local models are fine here
# because plans are drawn from a *closed* vocabulary (SUBGOAL_CATALOG
# below), so the LLM is choosing from a known menu rather than
# free-form reasoning - that's what makes 1B-class models reliable
# planners in this setup.
# ------------------------------------------------------------------ #
MODEL_TPA = "llama3.2:1b"   # Task Planning Agent  - goal -> subgoals, reflection
MODEL_TSA = "gemma3:1b"     # Task Setup Agent     - subgoal -> tool-chain args

# ------------------------------------------------------------------ #
# Long-term memory (episodic/semantic) - sqlite, sits next to holdings.db
# ------------------------------------------------------------------ #
AGENT_MEMORY_DB_PATH = BASE_DIR / "_DB" / "agent_memory.db"

# Short-term memory - recent (subgoal, tool, result) turns kept in RAM
# for the duration of a single orchestrator run.
SHORT_TERM_MEMORY_TURNS = 8

# ------------------------------------------------------------------ #
# Action layer - sandbox / permission system. Each tool declares the
# permissions it needs; the execution environment only runs a step if
# every required permission is in the allowed set for that run. WRITE,
# NETWORK, and PLOT are opt-in per-run so destructive or external-facing
# actions stay deliberate; READ/COMPUTE are safe defaults.
# ------------------------------------------------------------------ #
DEFAULT_ALLOWED_PERMISSIONS = {"READ", "COMPUTE"}
ALL_PERMISSIONS = {"READ", "COMPUTE", "WRITE", "NETWORK", "PLOT"}

# ------------------------------------------------------------------ #
# Reasoning layer - closed subgoal vocabulary. One subgoal maps to
# exactly one action-layer tool (see tools.SUBGOAL_TO_TOOL), so the TPA
# only has to pick *which* of these apply to a goal, and the TSA only
# has to extract arguments (owner, fund name, threshold, ...).
# ------------------------------------------------------------------ #
SUBGOAL_CATALOG: Dict[str, str] = {
    "update_navs":        "Fetch and store the latest NAV for tracked funds.",
    "record_history":     "Snapshot today's NAV into the NAV history table.",
    "performance_review": "Compute CAGR, drawdown, trend and alpha per fund.",
    "flag_risk":          "Flag funds that have dropped materially from their peak.",
    "portfolio_report":   "Produce a summary table of holdings and P/L.",
    "fund_lookup":        "Search for a scheme by keyword and report its NAV.",
    "add_fund":           "Add a new fund holding to the database.",
    "rename_fund":        "Rename a fund across one or all owners.",
    "plot_fund":          "Plot NAV history for a fund with base/highest/lowest lines.",
}

def print_wrap(aText, aWidth=80):
    """Pretty print preserving paragraph breaks."""
    for para in aText.split("\n"):
        if para.strip():
            print(textwrap.fill(para, width=aWidth))
        else:
            print()          # preserve blank lines between paragraphs
