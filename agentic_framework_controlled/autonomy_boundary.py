"""
api_Controls/autonomy_boundary.py
STEP 2: ACP-4 - Autonomy Boundary Service (orchestration-layer,
pre-execution control; Fig. 3 "Autonomy Boundary / Escalation & limits").

Composes over the existing SUBGOAL_TO_TOOL mapping in
agentic_framework/agent_tools.py to resolve a subgoal to its tool, then
evaluates it against configurable decision boundaries: irreversibility,
estimated cost, data sensitivity, resource scope, trust state, and an
autonomy-persistence counter (Section 3.1 design primitives; Table 1
ACP-4 mechanism).

The baseline framework carries NO severity/cost/PII metadata on its own
tools - CTool only has a permission-tag set. The IRREVERSIBLE_TOOLS /
TOOL_COST_ESTIMATE / SENSITIVE_DATA_TOOLS maps below are this additive
package's own policy layer, not a discovered framework property, and
are deliberately kept separate from tool_access_gate.py's own
TOOL_PRIVILEGE_TIER / TOOL_IMPACT_COST maps: ACP-4 gates a single
decision before a subgoal is even set up for execution, while ACP-5
(tool_access_gate.py) gates the actual tool invocation and accumulates
impact over a run - see Section 5's regarding a
per-decision cost threshold relates to the cumulative B(a,r) budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from agentic_framework.agent_tools import SUBGOAL_TO_TOOL


class ERoute(str, Enum):
    PROCEED = "proceed"
    ESCALATE = "escalate"
    DEFER = "defer"
    REJECT = "reject"


# --- Policy-layer metadata (Section 3.1 / 3.3 design primitives) -----------
# Tools with no corresponding "undo_*" tool anywhere in agent_tools.py -
# their effects cannot be cleanly reversed through the framework's own API.
IRREVERSIBLE_TOOLS: Set[str] = {"update_navs", "add_fund", "rename_fund"}

# Coarse relative cost estimate per tool (arbitrary unit; only the
# ordering against cost_threshold matters, not calibrated currency).
TOOL_COST_ESTIMATE: Dict[str, float] = {
    "update_navs": 5.0, "record_history": 1.0, "performance_review": 1.0,
    "flag_risk": 1.0, "portfolio_report": 1.0, "fund_lookup": 2.0,
    "add_fund": 8.0, "rename_fund": 4.0, "plot_fund": 1.0,
}

# Tools that read or write per-owner holdings data - treated as
# sensitive-data-bearing for this boundary (owner_name-scoped, per the
# GDPR minimisation machinery already in agent_memory.py).
SENSITIVE_DATA_TOOLS: Set[str] = {
    "update_navs", "record_history", "performance_review", "flag_risk",
    "portfolio_report", "add_fund", "rename_fund", "plot_fund",
}


@dataclass
class CAutonomyBoundaryConfig:
    cost_threshold: float = 6.0
    treat_irreversible_as_escalate: bool = True
    treat_sensitive_as_escalate: bool = False       # scoped, not blanket (Section 3.1(2))
    autonomy_persistence_limit: int = 5             # consecutive non-escalated steps
    min_trust_state: float = 0.0                    # in [0, 1]; below this -> escalate


@dataclass
class CBoundaryDecision:
    route: ERoute
    reasons: List[str] = field(default_factory=list)
    subgoal: str = ""
    tool_name: Optional[str] = None


class CAutonomyBoundaryService:
    """
    Pre-execution gate evaluated once per proposed subgoal, before
    CExecutionEnvironment.run_step() is invoked (Table 1, ACP-4).
    Stateful across a run: tracks the autonomy-persistence counter, so
    construct/`.reset()` one instance per orchestrator run.
    """

    def __init__(self, config: Optional[CAutonomyBoundaryConfig] = None):
        self.config = config or CAutonomyBoundaryConfig()
        self._consecutive_non_escalated = 0

    def reset(self) -> None:
        self._consecutive_non_escalated = 0

    def evaluate(self, subgoal: str, trust_state: float = 1.0) -> CBoundaryDecision:
        tool_name = SUBGOAL_TO_TOOL.get(subgoal)
        if tool_name is None:
            return CBoundaryDecision(ERoute.REJECT, 
                                     ["no tool mapping for subgoal"], 
                                     subgoal, tool_name)

        reasons: List[str] = []
        route = ERoute.PROCEED

        irreversible = tool_name in IRREVERSIBLE_TOOLS
        cost = TOOL_COST_ESTIMATE.get(tool_name, 0.0)
        sensitive = tool_name in SENSITIVE_DATA_TOOLS

        if trust_state < self.config.min_trust_state:
            route = ERoute.ESCALATE
            reasons.append(f"trust_state {trust_state:.2f} below floor {self.config.min_trust_state:.2f}")

        if irreversible and self.config.treat_irreversible_as_escalate:
            route = ERoute.ESCALATE
            reasons.append(f"'{tool_name}' has irreversible effects")

        if cost > self.config.cost_threshold:
            route = ERoute.ESCALATE
            reasons.append(f"estimated cost {cost} exceeds threshold {self.config.cost_threshold}")

        if sensitive and self.config.treat_sensitive_as_escalate:
            route = ERoute.ESCALATE
            reasons.append(f"'{tool_name}' touches sensitive/owner-scoped data")

        if route == ERoute.PROCEED:
            if self._consecutive_non_escalated >= self.config.autonomy_persistence_limit:
                route = ERoute.ESCALATE
                reasons.append(
                    "autonomy persistence limit reached "
                    f"({self._consecutive_non_escalated} consecutive steps without validation)"
                )

        if route == ERoute.PROCEED:
            self._consecutive_non_escalated += 1
        else:
            self._consecutive_non_escalated = 0

        return CBoundaryDecision(route, reasons, subgoal, tool_name)
