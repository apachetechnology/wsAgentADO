"""
api_Controls/closed_loop.py
Phase 4: closes the assurance loop described in Section 4.2's closing
paragraph - ACP-1 rejections and the ACP-6 bias signal become C(a,r)
circuit-breaker triggers on the ACP-5 Tool Access Gate, instead of being
merely logged to the bus (Table 1, ACP-6: "the baseline produces a
diagnostic signal; the proposed control plane consumes it to trigger
increased scrutiny, escalation, or privilege reduction").

Policy is intentionally simple and explicit (no learned thresholds), so
it stays auditable end to end:
  - Repeated ACP-1 rejections for the SAME tool within one run trip a
    tool-level breaker on that tool for the rest of the run.
  - An ACP-6 bias warning trips a run-level breaker, since the signal
    is about the planner's behaviour across the whole run rather than
    any single tool.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from agentic_framework_controlled.bus import CObservabilityMetricsBus, CBusEvent
from agentic_framework_controlled.tool_access_gate import CToolAccessGate


class CClosedLoopPolicy:
    def __init__(self, bus: CObservabilityMetricsBus, gate: CToolAccessGate,
                 acp1_rejects_before_trip: int = 2):
        self.bus = bus
        self.gate = gate
        self.acp1_rejects_before_trip = acp1_rejects_before_trip
        self._acp1_reject_counts: Dict[Tuple[Optional[str], str], int] = {}
        bus.subscribe(self._on_event)

    def _on_event(self, event: CBusEvent) -> None:
        if event.event_type == "acp1_reject":
            # ACP-1 is currently only wired to update_navs (agent_tools.py's
            # only tool that returns a "rejected" list) - keyed by tool name
            # here so the policy generalises if that ever changes.
            key = (event.run_id, "update_navs")
            self._acp1_reject_counts[key] = self._acp1_reject_counts.get(key, 0) + 1
            if self._acp1_reject_counts[key] >= self.acp1_rejects_before_trip:
                self.gate.trip_tool_breaker(
                    "update_navs",
                    reason=f"ACP-1 rejected {self._acp1_reject_counts[key]} NAV update(s) this run",
                    run_id=event.run_id,
                )
        elif event.event_type == "acp6_bias_signal":
            if event.run_id:
                self.gate.trip_run_breaker(
                    event.run_id,
                    reason=f"ACP-6 subgoal-bias signal: {event.payload.get('warning')}",
                )
