"""
api_Controls/controlled_orchestrator.py
STEP 2: CControlledOrchestrator composes an already-built
CAgenticOrchestrator's public attributes (mTPA, mTSA, mExecution,
mMemory, mPerception, mRegistry) and re-implements the run() loop with
an ACP-4 autonomy-boundary evaluate() step inserted before
CExecutionEnvironment.run_step(). CAgenticOrchestrator.run() itself is
left completely untouched and remains independently usable - this class
is an alternative entry point, not a subclass or monkeypatch of it.

For ACP-5 enforcement as well, construct this with a
CControlledExecutionEnvironment (controlled_execution.py) passed in as
the orchestrator's mExecution before wrapping - see examples/ for the
full wiring order.
"""
from __future__ import annotations

import textwrap
import uuid
from typing import Dict, List, Optional

from config_agent import print_wrap
from agentic_framework.layer_orchestrator import CAgenticOrchestrator

from agentic_framework_controlled.bus import CObservabilityMetricsBus
from agentic_framework_controlled.autonomy_boundary import (
    CAutonomyBoundaryService, CAutonomyBoundaryConfig, ERoute,
)


class CControlledOrchestrator:
    def __init__(self, orchestrator: CAgenticOrchestrator,
                 bus: CObservabilityMetricsBus,
                 boundary_config: Optional[CAutonomyBoundaryConfig] = None):
        self._orchestrator = orchestrator
        self.mMemory = orchestrator.mMemory
        self.mPerception = orchestrator.mPerception
        self.mRegistry = orchestrator.mRegistry
        self.mExecution = orchestrator.mExecution
        self.mTPA = orchestrator.mTPA
        self.mTSA = orchestrator.mTSA
        self.bus = bus
        self.boundary = CAutonomyBoundaryService(boundary_config)
        self.escalation_queue: Dict[str, Dict] = {}   # escalation_event_id -> context

    def run(self, strGoal: str, owner_name: Optional[str] = None,
            extra_args: Optional[Dict[str, Dict]] = None, aWidth: int = 80,
            trust_state: float = 1.0) -> Dict:
        run_id = uuid.uuid4().hex[:12]
        extra_args = extra_args or {}
        self.mExecution.reset_state()
        self.mMemory.reset_short_term()
        self.boundary.reset()

        dictSnapshot = self.mPerception.gather_portfolio_snapshot(owner_name)
        strContextSummary = self.mPerception.describe_context(dictSnapshot)
        print_wrap(f"[ControlledOrchestrator] Context gathered: {len(dictSnapshot)} holding row(s).")

        listSubgoals = self.mTPA.plan(strGoal, strContextSummary)
        print_wrap(f"[ControlledOrchestrator] Task assignment - subgoals: {listSubgoals or '(none identified)'}")

        for subgoal in listSubgoals:
            decision = self.boundary.evaluate(subgoal, trust_state=trust_state)
            self.bus.publish(
                "acp4_decision", acp="ACP-4", run_id=run_id,
                subgoal=subgoal, tool_name=decision.tool_name,
                route=decision.route.value, reasons=decision.reasons,
            )

            if decision.route == ERoute.REJECT:
                self.mMemory.add_short_term(subgoal, tool="?", ok=False,
                                             note="ACP-4 rejected: " + "; ".join(decision.reasons))
                print_wrap(f"[ControlledOrchestrator] REJECTED {subgoal}: {'; '.join(decision.reasons)}")
                continue

            if decision.route in (ERoute.ESCALATE, ERoute.DEFER):
                event = self.bus.publish(
                    "escalation_raised", acp="ACP-4", run_id=run_id,
                    subgoal=subgoal, tool_name=decision.tool_name,
                    route=decision.route.value, reasons=decision.reasons,
                )
                self.escalation_queue[event.event_id] = {
                    "run_id": run_id, "subgoal": subgoal, "strGoal": strGoal,
                    "owner_name": owner_name, "extra_args": extra_args,
                    "route": decision.route.value, "reasons": decision.reasons,
                }
                self.mMemory.add_short_term(subgoal, tool="?", ok=False,
                                             note=f"ACP-4 {decision.route.value}: " + "; ".join(decision.reasons))
                print_wrap(f"[ControlledOrchestrator] {decision.route.value.upper()} {subgoal}: "
                           f"{'; '.join(decision.reasons)} (queued, event id {event.event_id})")
                continue

            # PROCEED - hand off to the action layer exactly as the
            # baseline orchestrator does.
            dictStep = self.mTSA.setup(subgoal, strGoal, default_owner=owner_name)
            if dictStep is None:
                self.mMemory.add_short_term(subgoal, tool="?", ok=False, 
                                            note="no tool mapping")
                continue

            # Added by SG
            if dictStep["tool"] == "add_fund" and subgoal not in extra_args:
                self.mMemory.add_short_term(subgoal, tool=dictStep["tool"], ok=False,
                                            note="add_fund requires extra_args={'add_fund': {...}}")
                print_wrap(f"[ControlledOrchestrator] SKIPPED {subgoal}: missing required extra_args")
                continue
            
            dictStep["args"].update(extra_args.get(subgoal, {}))
            objExeRecord = self.mExecution.run_step(dictStep["tool"], dictStep["args"])
            self.mMemory.add_short_term(subgoal, dictStep["tool"], objExeRecord.mStrStatus == "ok",
                                         note=objExeRecord.mError)

            strStatusNote = f"{dictStep['tool']} -> {objExeRecord.mStrStatus}"
            if objExeRecord.mError:
                strStatusNote += f" ({objExeRecord.mError})"
            print_wrap(f"[ControlledOrchestrator] {strStatusNote}")

        dictReflection = self.mTPA.reflect(strGoal, self.mExecution.get_log())
        print_wrap(f"[ControlledOrchestrator] Reflection: {dictReflection['summary']}")

        self.mMemory.record_episode(strGoal, listSubgoals,
                             [vars(r) for r in self.mExecution.get_log()],
                             dictReflection["summary"], dictReflection["success"],
                             owner_name=owner_name)

        print("-" * 80)
        print("Goal:", textwrap.fill(strGoal, width=aWidth))
        print("Subgoals:", listSubgoals)
        self.mExecution.print_log_tabular()
        print("\n" + "_" * 80)

        dictReflection["run_id"] = run_id
        dictReflection["escalations_pending"] = [
            eid for eid, ctx in self.escalation_queue.items() if ctx["run_id"] == run_id
        ]
        return dictReflection

    def resolve_escalation(self, escalation_event_id: str, approved: bool) -> Optional[Dict]:
        """
        Supervisory decision on a queued ACP-4 escalation. The baseline
        framework has no human-in-the-loop primitive at all; this is the
        additive package's own mechanism for the "reserved for a human or
        supervisory process" requirement in Section 3.1. Returns None if
        the escalation id is unknown (already resolved, or never queued).
        """
        ctx = self.escalation_queue.pop(escalation_event_id, None)
        if ctx is None:
            return None

        self.bus.publish(
            "escalation_resolved", acp="ACP-4", run_id=ctx["run_id"],
            escalation_event_id=escalation_event_id, subgoal=ctx["subgoal"], approved=approved,
        )
        if not approved:
            return {"approved": False}

        dictStep = self.mTSA.setup(ctx["subgoal"], ctx["strGoal"], default_owner=ctx["owner_name"])
        if dictStep is None:
            return {"approved": True, "status": "no tool mapping"}
        
        dictStep["args"].update(ctx["extra_args"].get(ctx["subgoal"], {}))
        objExeRecord = self.mExecution.run_step(dictStep["tool"], dictStep["args"])
        self.mMemory.add_short_term(ctx["subgoal"], dictStep["tool"], objExeRecord.mStrStatus == "ok",
                                     note=objExeRecord.mError)
        return {"approved": True, "tool": dictStep["tool"], "status": objExeRecord.mStrStatus,
                "error": objExeRecord.mError}
