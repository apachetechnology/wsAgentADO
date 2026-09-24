"""
api_Controls/tool_access_gate.py
Phase 3: ACP-5 - Tool Access Gate (Fig. 3 "Tool Access Gate / Tiers &
breakers"). Implements Section 4.2's formal authorisation predicate
literally:

    Authorize(a, t, r) = P(a) ^ G(a, t) ^ S(a) ^ B(a, r) ^ not C(a, r)

    P(a)   - policy/permission validity: the tool's required permission
             tags (agentic_framework.agent_tools.CTool.mTool_permissions)
             are all present in the run's allowed_permissions set - the
             same check CExecutionEnvironment.run_step() makes, re-derived
             here so the gate can veto BEFORE run_step() is called.
    G(a,t) - delegation-grant validity at time t (DelegationLedger.is_valid).
    S(a)   - requested tool's privilege tier is within the configured max.
    B(a,r) - cumulative impact budget not yet exhausted for run r.
    C(a,r) - an active circuit breaker on tool a, or on run r as a whole.

Categorical permission tags in the baseline framework carry no severity
ordering, so TOOL_PRIVILEGE_TIER / TOOL_IMPACT_COST below are this
package's own manual severity map (Section 3.3's "tiered tool-chain
privileges"), not a discovered framework property.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Set

from agentic_framework_controlled.delegation_ledger import DelegationLedger

class EPrivilegeTier(IntEnum):
    READ_ONLY = 1
    COMPUTE = 2
    WRITE_LOCAL = 3
    WRITE_EXTERNAL = 4       # network-facing write, e.g. update_navs


TOOL_PRIVILEGE_TIER: Dict[str, EPrivilegeTier] = {
    "portfolio_report":   EPrivilegeTier.READ_ONLY,
    "fund_lookup":        EPrivilegeTier.READ_ONLY,
    "performance_review": EPrivilegeTier.COMPUTE,
    "flag_risk":          EPrivilegeTier.COMPUTE,
    "plot_fund":          EPrivilegeTier.COMPUTE,
    "record_history":     EPrivilegeTier.WRITE_LOCAL,
    "rename_fund":        EPrivilegeTier.WRITE_LOCAL,
    "add_fund":           EPrivilegeTier.WRITE_LOCAL,
    "update_navs":        EPrivilegeTier.WRITE_EXTERNAL,
}

# Cumulative impact cost per invocation, at the same arbitrary unit as
# autonomy_boundary.TOOL_COST_ESTIMATE but tracked separately: ACP-4's
# cost threshold gates a single decision before execution, while ACP-5's
# impact budget accumulates over a run/task/grant (Section 4.2; Paper 3
# Sec. 5's open item on B(a,r) vs. the proposed BlastRadius(a) tuple).
TOOL_IMPACT_COST: Dict[str, float] = {
    "portfolio_report": 0.5, "fund_lookup": 0.5, "performance_review": 0.5,
    "flag_risk": 0.5, "plot_fund": 0.5, "record_history": 1.0,
    "rename_fund": 2.0, "add_fund": 3.0, "update_navs": 3.0,
}


@dataclass
class CToolAccessGateConfig:
    max_tier: EPrivilegeTier = EPrivilegeTier.WRITE_EXTERNAL
    impact_budget_per_run: float = 20.0


@dataclass
class CAuthorizationResult:
    authorized: bool
    tool_name: str
    tier: Optional[EPrivilegeTier]
    p_policy_permission: bool
    g_delegation_valid: bool
    s_scope_tier_ok: bool
    b_budget_ok: bool
    c_circuit_breaker_active: bool
    reasons: List[str] = field(default_factory=list)


class CToolAccessGate:
    def __init__(self, ledger: DelegationLedger, config: Optional[CToolAccessGateConfig] = None,
                 bus=None):
        self.ledger = ledger
        self.config = config or CToolAccessGateConfig()
        self.bus = bus
        self._impact_spent: Dict[Optional[str], float] = {}    # run_id -> cumulative impact
        self._tool_breakers: Set[str] = set()                  # tool_name suspended everywhere
        self._run_breakers: Set[str] = set()                   # run_id halted entirely

    # -- circuit breakers (Section 4.2: "suspend a tool ... or halt a workflow") --
    def trip_tool_breaker(self, tool_name: str, reason: str, run_id: Optional[str] = None) -> None:
        self._tool_breakers.add(tool_name)
        if self.bus:
            self.bus.publish("circuit_breaker_tripped", acp="ACP-5", run_id=run_id,
                              scope="tool", tool_name=tool_name, reason=reason)

    def trip_run_breaker(self, run_id: str, reason: str) -> None:
        self._run_breakers.add(run_id)
        if self.bus:
            self.bus.publish("circuit_breaker_tripped", acp="ACP-5", run_id=run_id,
                              scope="run", reason=reason)

    def clear_tool_breaker(self, tool_name: str) -> None:
        self._tool_breakers.discard(tool_name)

    def clear_run_breaker(self, run_id: str) -> None:
        self._run_breakers.discard(run_id)

    def _circuit_breaker_active(self, tool_name: str, run_id: Optional[str]) -> bool:
        return tool_name in self._tool_breakers or (run_id in self._run_breakers if run_id else False)

    def authorize(self, tool_name: str, required_permissions: Set[str],
                  allowed_permissions: Set[str], grant_id: str,
                  run_id: Optional[str] = None) -> CAuthorizationResult:
        reasons: List[str] = []

        p_ok = required_permissions.issubset(allowed_permissions)
        if not p_ok:
            reasons.append(f"missing permission(s): {sorted(required_permissions - allowed_permissions)}")

        g_ok = self.ledger.is_valid(grant_id)
        if not g_ok:
            reasons.append(f"delegation grant '{grant_id}' invalid/expired/revoked")

        tier = TOOL_PRIVILEGE_TIER.get(tool_name)
        s_ok = tier is not None and tier <= self.config.max_tier
        if not s_ok:
            reasons.append(f"tool tier {tier} exceeds max_tier {self.config.max_tier}")

        cost = TOOL_IMPACT_COST.get(tool_name, 1.0)
        spent = self._impact_spent.get(run_id, 0.0)
        b_ok = (spent + cost) <= self.config.impact_budget_per_run
        if not b_ok:
            reasons.append(f"impact budget exceeded: {spent + cost:.1f} > {self.config.impact_budget_per_run}")

        c_active = self._circuit_breaker_active(tool_name, run_id)
        if c_active:
            reasons.append(f"circuit breaker active for '{tool_name}' or run '{run_id}'")

        authorized = p_ok and g_ok and s_ok and b_ok and not c_active

        if authorized:
            self._impact_spent[run_id] = spent + cost
            self.ledger.record_invocation(grant_id)

        result = CAuthorizationResult(authorized, tool_name, tier, p_ok, g_ok, s_ok, b_ok, c_active, reasons)

        if self.bus:
            self.bus.publish("acp5_authorize", acp="ACP-5", run_id=run_id,
                              tool_name=tool_name, tier=(tier.name if tier else None),
                              authorized=authorized, reasons=reasons,
                              impact_spent=self._impact_spent.get(run_id, spent))
        return result
