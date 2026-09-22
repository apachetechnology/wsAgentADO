"""
api_Controls/bus.py
Phase 0: Observability & Metrics Bus (Paper 3, Figure 3 - "Observability
and Metrics Bus / Feeds operational risk metrics").

Purely additive and framework-agnostic: this module never imports
`agentic_framework` or any wsAgenticAIFW file. Other api_Controls
components (signal_adapters, autonomy_boundary/controlled_orchestrator,
tool_access_gate, delegation_ledger, closed_loop) push events onto it;
this module only stores and aggregates them. That separation keeps the
bus unit-testable on its own and keeps "observation" and "enforcement"
as distinct concerns, matching Section 4.2's description of the bus as
"an observation and evidence component rather than an independent
enforcement point."
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


@dataclass
class CBusEvent:
    event_type: str                       # e.g. "acp1_reject", "acp4_decision"
    acp: Optional[str] = None             # "ACP-1" .. "ACP-6", or None if cross-cutting
    run_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CObservabilityMetricsBus:
    """
    Append-only event log plus rollup queries over it. In-memory by
    default; a durable sink (e.g. sqlite, alongside CAgentMemory's own
    `agent_episodes` / `privacy_audit_log` tables) can be attached via
    `subscribe()` without this class needing to know about it.
    """

    def __init__(self) -> None:
        self._events: List[CBusEvent] = []
        self._subscribers: List[Callable[[CBusEvent], None]] = []

    # -- publish / subscribe -------------------------------------------------
    def subscribe(self, callback: Callable[[CBusEvent], None]) -> None:
        self._subscribers.append(callback)

    def publish(self, event_type: str, acp: Optional[str] = None,
                run_id: Optional[str] = None, **payload) -> CBusEvent:
        evt = CBusEvent(event_type=event_type, acp=acp, run_id=run_id, payload=payload)
        self._events.append(evt)
        for cb in self._subscribers:
            cb(evt)
        return evt

    def events(self, event_type: Optional[str] = None, acp: Optional[str] = None,
               run_id: Optional[str] = None) -> List[CBusEvent]:
        out = self._events
        if event_type is not None:
            out = [e for e in out if e.event_type == event_type]
        if acp is not None:
            out = [e for e in out if e.acp == acp]
        if run_id is not None:
            out = [e for e in out if e.run_id == run_id]
        return list(out)

    def clear(self) -> None:
        self._events.clear()

    def scoped(self, run_id: Optional[str] = None) -> "CObservabilityMetricsBus":
        """Read-only view of this bus filtered to one run_id, so rollups()
        can be computed per-run without duplicating the rollup methods."""
        view = CObservabilityMetricsBus()
        view._events = self.events(run_id=run_id) if run_id else list(self._events)
        return view

    # -- rollups referenced in Paper 3 Section 5 (Operational Risk Metrics) --
    def delegation_depth_and_duration(self) -> Dict[str, Any]:
        issued = self.events(event_type="delegation_issued")
        revoked = self.events(event_type="delegation_revoked")
        depths = [e.payload.get("depth", 0) for e in issued]
        revoked_at = {e.payload.get("grant_id"): e.timestamp for e in revoked}
        durations = []
        for e in issued:
            gid = e.payload.get("grant_id")
            if gid in revoked_at:
                try:
                    t0 = datetime.fromisoformat(e.timestamp)
                    t1 = datetime.fromisoformat(revoked_at[gid])
                    durations.append((t1 - t0).total_seconds())
                except ValueError:
                    pass
        return {
            "issued_total": len(issued),
            "revoked_total": len(revoked),
            "active_grants": max(len(issued) - len(revoked), 0),
            "max_depth": max(depths, default=0),
            "avg_depth": (sum(depths) / len(depths)) if depths else 0.0,
            "avg_revoked_duration_s": (sum(durations) / len(durations)) if durations else None,
        }

    def tool_blast_radius(self) -> Dict[str, Any]:
        """
        Approximation of BlastRadius(a) (Paper 3 Sec. 5, proposed 5-tuple
        <R_a, D_a, S_a, I_a, P_a>) from ACP-5 authorise/deny events, using
        privilege-tier counts as a stand-in until a formal aggregation
        function is defined. Deliberately NOT conflated with the scalar
        B(a,r) impact-budget term inside Authorize() - see Paper 3's open
        item on how the two relate.
        """
        decisions = self.events(event_type="acp5_authorize")
        by_tier: Dict[str, int] = {}
        for e in decisions:
            tier = e.payload.get("tier") or "unknown"
            by_tier[tier] = by_tier.get(tier, 0) + 1
        denied = [e for e in decisions if not e.payload.get("authorized", True)]
        return {
            "invocations_total": len(decisions),
            "denied_total": len(denied),
            "by_privilege_tier": by_tier,
        }

    def autonomy_persistence(self) -> Dict[str, Any]:
        decisions = self.events(event_type="acp4_decision")
        streak, max_streak, escalations = 0, 0, 0
        for e in decisions:
            if e.payload.get("route") == "proceed":
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
                if e.payload.get("route") == "escalate":
                    escalations += 1
        return {
            "decisions_total": len(decisions),
            "max_consecutive_non_escalated": max_streak,
            "escalations_total": escalations,
        }

    def escalation_latency(self) -> Dict[str, Any]:
        raised = {e.event_id: e for e in self.events(event_type="escalation_raised")}
        resolved = self.events(event_type="escalation_resolved")
        latencies = []
        for r in resolved:
            src = raised.get(r.payload.get("escalation_event_id"))
            if src is None:
                continue
            try:
                t0 = datetime.fromisoformat(src.timestamp)
                t1 = datetime.fromisoformat(r.timestamp)
                latencies.append((t1 - t0).total_seconds())
            except ValueError:
                continue
        return {
            "raised_total": len(raised),
            "resolved_total": len(resolved),
            "pending": max(len(raised) - len(resolved), 0),
            "avg_latency_s": (sum(latencies) / len(latencies)) if latencies else None,
        }

    def rollups(self) -> Dict[str, Any]:
        return {
            "delegation": self.delegation_depth_and_duration(),
            "blast_radius": self.tool_blast_radius(),
            "autonomy_persistence": self.autonomy_persistence(),
            "escalation_latency": self.escalation_latency(),
        }

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_dict(), default=str) for e in self._events)
