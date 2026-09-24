"""
Tests/test_controlled_framework.py
Phase 5: adversarial / edge-case tests for the additive api_Controls/
package - expiry, cascade-revoke, and breaker-trip cases, per the Phase
5 build plan.
"""
import time

from agentic_framework_controlled.bus import CObservabilityMetricsBus
from agentic_framework_controlled.signal_adapters import CSignalAdapters
from agentic_framework_controlled.autonomy_boundary import (
    CAutonomyBoundaryService, CAutonomyBoundaryConfig, ERoute,
)
from agentic_framework_controlled.delegation_ledger import DelegationLedger
from agentic_framework_controlled.tool_access_gate import (
    CToolAccessGate, CToolAccessGateConfig, EPrivilegeTier,
)
from agentic_framework_controlled.closed_loop import CClosedLoopPolicy

# ---------------------------------------------------------------------------
# Minimal stand-ins matching the exact shapes CSignalAdapters reads/writes.
# ---------------------------------------------------------------------------
class _FakeTool:
    def __init__(self, func):
        self.mTool_func = func


class _FakeRegistry:
    def __init__(self, tools):
        self._tools = tools

    def get(self, name):
        return self._tools.get(name)


class _FakeTPA:
    def __init__(self, plan_fn, reflect_fn):
        self.plan = plan_fn
        self.reflect = reflect_fn


class _FakeMemory:
    def __init__(self, bias_warning=None):
        self._bias_warning = bias_warning
        self.episodes = []

    def record_episode(self, *args, **kwargs):
        self.episodes.append((args, kwargs))
        return len(self.episodes)

    def check_subgoal_bias(self, *args, **kwargs):
        return self._bias_warning


class _FakeOrchestrator:
    def __init__(self, registry, tpa, memory):
        self.mRegistry = registry
        self.mTPA = tpa
        self.mMemory = memory


# ---------------------------------------------------------------------------
# ACP-1: signal adapter surfaces rejected/failed NAV rows
# ---------------------------------------------------------------------------
def test_acp1_adapter_publishes_rejections():
    bus = CObservabilityMetricsBus()

    def update_navs(**_):
        return {"updated": 1, "total": 2, "failures": [],
                "rejected": [{"fund_name": "F1", "reason": "300% jump vs prior NAV"}]}

    registry = _FakeRegistry({"update_navs": _FakeTool(update_navs)})
    orch = _FakeOrchestrator(
        registry,
        _FakeTPA(lambda *a, **k: [], lambda *a, **k: {"summary": "", "success": True}),
        _FakeMemory(),
    )

    CSignalAdapters(bus).attach(orch, run_id="r1")
    result = orch.mRegistry.get("update_navs").mTool_func()

    assert result["rejected"]
    events = bus.events(event_type="acp1_reject")
    assert len(events) == 1
    assert events[0].payload["rejected"][0]["fund_name"] == "F1"


def test_acp1_adapter_silent_when_nothing_rejected():
    bus = CObservabilityMetricsBus()

    def update_navs(**_):
        return {"updated": 2, "total": 2, "failures": [], "rejected": []}

    registry = _FakeRegistry({"update_navs": _FakeTool(update_navs)})
    orch = _FakeOrchestrator(
        registry,
        _FakeTPA(lambda *a, **k: [], lambda *a, **k: {"summary": "", "success": True}),
        _FakeMemory(),
    )
    CSignalAdapters(bus).attach(orch, run_id="r1b")
    orch.mRegistry.get("update_navs").mTool_func()
    assert bus.events(event_type="acp1_reject") == []


# ---------------------------------------------------------------------------
# ACP-2: full-catalog echo detection from the existing console print marker
# ---------------------------------------------------------------------------
def test_acp2_adapter_detects_full_catalog_echo(capsys):
    bus = CObservabilityMetricsBus()

    def plan(goal, context_summary):
        print("[TPA] LLM returned the entire subgoal catalog - treating that as "
              "an echoed menu, not a real plan. Falling back to keyword planning.")
        return ["update_navs"]  # the correct fallback plan, post-echo-discard

    orch = _FakeOrchestrator(
        _FakeRegistry({}),
        _FakeTPA(plan, lambda *a, **k: {"summary": "", "success": True}),
        _FakeMemory(),
    )
    CSignalAdapters(bus).attach(orch, run_id="r2")
    subgoals = orch.mTPA.plan("goal text", "context")

    assert subgoals == ["update_navs"]
    events = bus.events(event_type="acp2_plan_decision")
    assert len(events) == 1
    assert events[0].payload["full_catalog_echo_detected"] is True
    assert "echoed menu" in capsys.readouterr().out  # original print still reaches console


def test_acp2_adapter_no_false_positive_on_normal_plan():
    bus = CObservabilityMetricsBus()
    orch = _FakeOrchestrator(
        _FakeRegistry({}),
        _FakeTPA(lambda g, c: ["portfolio_report"], lambda *a, **k: {"summary": "", "success": True}),
        _FakeMemory(),
    )
    CSignalAdapters(bus).attach(orch, run_id="r3")
    orch.mTPA.plan("show my portfolio", "ctx")
    events = bus.events(event_type="acp2_plan_decision")
    assert events[0].payload["full_catalog_echo_detected"] is False

def test_acp2_marker_string_matches_source():
    """Regression guard: signal_adapters._FULL_ECHO_MARKER must stay a
    substring of layer_reasoning.plan()'s actual print message, or ACP-2
    detection silently stops working."""
    import inspect
    from agentic_framework.layer_reasoning import CTaskPlanningAgent
    from agentic_framework_controlled.signal_adapters import _FULL_ECHO_MARKER

    source = inspect.getsource(CTaskPlanningAgent.plan)
    assert _FULL_ECHO_MARKER in source

# ---------------------------------------------------------------------------
# ACP-3: deterministic-fallback detection (see the known upstream bug note
# in signal_adapters.py - the LLM-authored branch never actually runs in
# the current commit, so this reads True for every real reflect() call)
# ---------------------------------------------------------------------------
def test_acp3_adapter_flags_grounded_fallback():
    bus = CObservabilityMetricsBus()

    def reflect(goal, log):
        return {"summary": "Completed 2/2 step(s) successfully.", "success": True,
                "steps_ok": 2, "steps_total": 2}

    orch = _FakeOrchestrator(_FakeRegistry({}), _FakeTPA(lambda *a, **k: [], reflect), _FakeMemory())
    CSignalAdapters(bus).attach(orch, run_id="r4")
    orch.mTPA.reflect("goal", [])

    events = bus.events(event_type="acp3_reflection")
    assert events[0].payload["used_grounded_fallback"] is True
    assert events[0].payload["ungrounded_currency_detected"] is False


def test_acp3_adapter_flags_ungrounded_currency_if_it_ever_slips_through():
    bus = CObservabilityMetricsBus()

    def reflect(goal, log):
        # Simulates what an UNPATCHED currency check would have let
        # through - documents the exact risk this adapter watches for.
        return {"summary": "Your fund gained $500 this month.", "success": True,
                "steps_ok": 1, "steps_total": 1}

    orch = _FakeOrchestrator(_FakeRegistry({}), _FakeTPA(lambda *a, **k: [], reflect), _FakeMemory())
    CSignalAdapters(bus).attach(orch, run_id="r5")
    orch.mTPA.reflect("goal", [])

    events = bus.events(event_type="acp3_reflection")
    assert events[0].payload["ungrounded_currency_detected"] is True


# ---------------------------------------------------------------------------
# ACP-6: bias signal fires check_subgoal_bias() after every record_episode(),
# since the baseline framework never calls it at all
# ---------------------------------------------------------------------------
def test_acp6_adapter_publishes_bias_signal():
    bus = CObservabilityMetricsBus()
    memory = _FakeMemory(bias_warning="Subgoal 'update_navs' accounts for 90% of recent plans")
    orch = _FakeOrchestrator(_FakeRegistry({}), _FakeTPA(lambda *a, **k: [], lambda *a, **k: {}), memory)

    CSignalAdapters(bus).attach(orch, run_id="r6")
    orch.mMemory.record_episode("goal", [], [], "summary", True)

    events = bus.events(event_type="acp6_bias_signal")
    assert len(events) == 1
    assert "90%" in events[0].payload["warning"]


def test_acp6_adapter_silent_when_no_bias():
    bus = CObservabilityMetricsBus()
    memory = _FakeMemory(bias_warning=None)
    orch = _FakeOrchestrator(_FakeRegistry({}), _FakeTPA(lambda *a, **k: [], lambda *a, **k: {}), memory)
    CSignalAdapters(bus).attach(orch, run_id="r7")
    orch.mMemory.record_episode("goal", [], [], "summary", True)
    assert bus.events(event_type="acp6_bias_signal") == []


# ---------------------------------------------------------------------------
# ACP-4: autonomy boundary - irreversibility, cost, persistence limit
# ---------------------------------------------------------------------------
def test_autonomy_boundary_escalates_irreversible_tool():
    svc = CAutonomyBoundaryService()
    decision = svc.evaluate("add_fund")
    assert decision.route == ERoute.ESCALATE


def test_autonomy_boundary_persistence_limit_forces_escalation():
    cfg = CAutonomyBoundaryConfig(treat_irreversible_as_escalate=False,
                                   cost_threshold=999, autonomy_persistence_limit=2)
    svc = CAutonomyBoundaryService(cfg)
    d1 = svc.evaluate("portfolio_report")
    d2 = svc.evaluate("portfolio_report")
    d3 = svc.evaluate("portfolio_report")
    assert d1.route == d2.route == ERoute.PROCEED
    assert d3.route == ERoute.ESCALATE
    assert "persistence" in " ".join(d3.reasons)


def test_autonomy_boundary_rejects_unmapped_subgoal():
    svc = CAutonomyBoundaryService()
    decision = svc.evaluate("not_a_real_subgoal")
    assert decision.route == ERoute.REJECT


# ---------------------------------------------------------------------------
# ACP-5: delegation ledger - expiry + cascade revoke
# ---------------------------------------------------------------------------
def test_delegation_expires_by_ttl():
    ledger = DelegationLedger()
    grant = ledger.issue("operator", "orchestrator", {"READ"}, ttl_seconds=0.05)
    assert ledger.is_valid(grant.grant_id)
    time.sleep(0.1)
    assert not ledger.is_valid(grant.grant_id)


def test_delegation_expires_by_invocation_count():
    ledger = DelegationLedger()
    grant = ledger.issue("operator", "orchestrator", {"WRITE"}, max_invocations=2)
    assert ledger.is_valid(grant.grant_id)
    ledger.record_invocation(grant.grant_id)
    ledger.record_invocation(grant.grant_id)
    assert not ledger.is_valid(grant.grant_id)


def test_delegation_cascade_revoke():
    ledger = DelegationLedger()
    parent = ledger.issue("operator", "orchestrator", {"READ", "WRITE"})
    child = ledger.issue("orchestrator", "sub_agent", {"READ"}, parent_id=parent.grant_id)
    grandchild = ledger.issue("sub_agent", "tool_x", {"READ"}, parent_id=child.grant_id)

    revoked = ledger.revoke(parent.grant_id, reason="operator withdrew authority")

    assert set(revoked) == {parent.grant_id, child.grant_id, grandchild.grant_id}
    assert not ledger.is_valid(child.grant_id)
    assert not ledger.is_valid(grandchild.grant_id)


def test_delegation_revoke_does_not_affect_siblings():
    ledger = DelegationLedger()
    parent = ledger.issue("operator", "orchestrator", {"READ"})
    child_a = ledger.issue("orchestrator", "agent_a", {"READ"}, parent_id=parent.grant_id)
    child_b = ledger.issue("orchestrator", "agent_b", {"READ"}, parent_id=parent.grant_id)

    ledger.revoke(child_a.grant_id, reason="agent_a compromised")

    assert not ledger.is_valid(child_a.grant_id)
    assert ledger.is_valid(child_b.grant_id)
    assert ledger.is_valid(parent.grant_id)


# ---------------------------------------------------------------------------
# ACP-5: tool access gate - Authorize(a,t,r) = P ^ G ^ S ^ B ^ not C
# ---------------------------------------------------------------------------
def test_gate_denies_on_missing_permission():
    ledger = DelegationLedger()
    grant = ledger.issue("op", "orch", {"READ", "COMPUTE"})
    gate = CToolAccessGate(ledger)
    result = gate.authorize("update_navs", required_permissions={"WRITE", "NETWORK"},
                             allowed_permissions={"READ", "COMPUTE"}, grant_id=grant.grant_id, run_id="r")
    assert not result.authorized
    assert not result.p_policy_permission


def test_gate_denies_on_expired_grant():
    ledger = DelegationLedger()
    grant = ledger.issue("op", "orch", {"WRITE", "NETWORK"}, max_invocations=1)
    gate = CToolAccessGate(ledger)
    ledger.record_invocation(grant.grant_id)  # exhaust it
    result = gate.authorize("update_navs", required_permissions={"WRITE", "NETWORK"},
                             allowed_permissions={"WRITE", "NETWORK"}, grant_id=grant.grant_id, run_id="r")
    assert not result.authorized
    assert not result.g_delegation_valid


def test_gate_denies_over_tier():
    ledger = DelegationLedger()
    grant = ledger.issue("op", "orch", {"WRITE", "NETWORK"})
    gate = CToolAccessGate(ledger, CToolAccessGateConfig(max_tier=EPrivilegeTier.WRITE_LOCAL))
    result = gate.authorize("update_navs", required_permissions={"WRITE", "NETWORK"},
                             allowed_permissions={"WRITE", "NETWORK"}, grant_id=grant.grant_id, run_id="r")
    assert not result.authorized
    assert not result.s_scope_tier_ok


def test_gate_denies_over_impact_budget():
    ledger = DelegationLedger()
    grant = ledger.issue("op", "orch", {"WRITE", "NETWORK"})
    gate = CToolAccessGate(ledger, CToolAccessGateConfig(impact_budget_per_run=5.0))
    r1 = gate.authorize("update_navs", {"WRITE", "NETWORK"}, {"WRITE", "NETWORK"}, grant.grant_id, run_id="r")
    assert r1.authorized                     # cost 3.0 <= budget 5.0
    r2 = gate.authorize("update_navs", {"WRITE", "NETWORK"}, {"WRITE", "NETWORK"}, grant.grant_id, run_id="r")
    assert not r2.authorized                 # cumulative 3.0 + 3.0 = 6.0 > 5.0
    assert not r2.b_budget_ok


def test_gate_denies_when_circuit_breaker_tripped():
    ledger = DelegationLedger()
    grant = ledger.issue("op", "orch", {"WRITE", "NETWORK"})
    gate = CToolAccessGate(ledger)
    gate.trip_tool_breaker("update_navs", reason="repeated ACP-1 rejections", run_id="r")
    result = gate.authorize("update_navs", {"WRITE", "NETWORK"}, {"WRITE", "NETWORK"}, grant.grant_id, run_id="r")
    assert not result.authorized
    assert result.c_circuit_breaker_active


# ---------------------------------------------------------------------------
# Phase 4: closed loop - repeated ACP-1 rejects and an ACP-6 bias signal
# trip the ACP-5 gate's circuit breakers
# ---------------------------------------------------------------------------
def test_closed_loop_trips_tool_breaker_after_repeated_acp1_rejects():
    bus = CObservabilityMetricsBus()
    ledger = DelegationLedger()
    grant = ledger.issue("op", "orch", {"WRITE", "NETWORK"})
    gate = CToolAccessGate(ledger, bus=bus)
    CClosedLoopPolicy(bus, gate, acp1_rejects_before_trip=2)

    bus.publish("acp1_reject", acp="ACP-1", run_id="rX", rejected=[{"fund_name": "F1"}])
    result = gate.authorize("update_navs", {"WRITE", "NETWORK"}, {"WRITE", "NETWORK"}, grant.grant_id, run_id="rX")
    assert result.authorized  # only 1 reject so far

    bus.publish("acp1_reject", acp="ACP-1", run_id="rX", rejected=[{"fund_name": "F1"}])
    result2 = gate.authorize("update_navs", {"WRITE", "NETWORK"}, {"WRITE", "NETWORK"}, grant.grant_id, run_id="rX")
    assert not result2.authorized  # 2nd reject trips the breaker


def test_closed_loop_trips_run_breaker_on_acp6_bias():
    bus = CObservabilityMetricsBus()
    ledger = DelegationLedger()
    grant = ledger.issue("op", "orch", {"READ"})
    gate = CToolAccessGate(ledger, bus=bus)
    CClosedLoopPolicy(bus, gate)

    bus.publish("acp6_bias_signal", acp="ACP-6", run_id="rY", warning="skewed toward update_navs")
    result = gate.authorize("fund_lookup", {"READ", "NETWORK"}, {"READ", "NETWORK"}, grant.grant_id, run_id="rY")
    assert not result.authorized
    assert result.c_circuit_breaker_active
