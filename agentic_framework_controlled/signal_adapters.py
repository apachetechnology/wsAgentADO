"""
api_Controls/signal_adapters.py
STEP 1: ACP-1 / ACP-2 / ACP-3 / ACP-6 signal adapters.

Non-invasive instrumentation: every adapter here composes over an
already-constructed CAgenticOrchestrator by substituting a wrapping
closure for a public, mutable attribute (`mTool_func` on a CTool,
`.plan` / `.reflect` on the TPA, `.record_episode` on CAgentMemory).

Adapters capture existing return values/rejections into the bus.
"""
from __future__ import annotations

import io
import re
import sys
import contextlib
from typing import Optional, Set

from agentic_framework_controlled.bus import CObservabilityMetricsBus

# config_agent.SUBGOAL_CATALOG is a public, intentional part of the
# framework's config surface (not a private/internal detail) - the same
# constant CTaskPlanningAgent.plan() itself is built against.
from config_agent import SUBGOAL_CATALOG

_FALLBACK_RE = re.compile(
    r"^(Completed \d+/\d+ step\(s\) successfully\.|"
    r"No applicable steps were identified for this goal\.)"
)
_FULL_ECHO_MARKER = "entire subgoal catalog"


def _tee_stdout(fn, *args, **kwargs):
    """Run fn under stdout capture, replay the captured text to the real
    stdout so console behaviour is unchanged, and return (result, captured_text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    captured = buf.getvalue()
    sys.stdout.write(captured)
    return result, captured

##########################################################################
##
class CSignalAdapters:
    """
    Attaches ACP-1/2/3/6 observability adapters to a live
    CAgenticOrchestrator. Construct once, then call `.attach(orchestrator,
    run_id=...)` per orchestrator instance (idempotent - re-attaching the
    same instance is a no-op).
    """

    def __init__(self, bus: CObservabilityMetricsBus):
        self.bus = bus
        self._attached_ids: Set[int] = set()

    def attach(self, orchestrator, run_id: Optional[str] = None) -> None:
        if id(orchestrator) in self._attached_ids:
            return
        self._attached_ids.add(id(orchestrator))
        self._wrap_acp1_update_navs(orchestrator, run_id)
        self._wrap_acp2_plan(orchestrator, run_id)
        self._wrap_acp3_reflect(orchestrator, run_id)
        self._wrap_acp6_bias(orchestrator, run_id)

    # -- ACP-1: perception input / pre-write validation ----------------------
    # Table 1: "Plausibility-bound validation of retrieved NAV values ...
    # in update_navs." The tool already returns a "rejected" list
    # (MAX_DAILY_MOVE / non-positive-NAV checks) - this adapter surfaces it.
    def _wrap_acp1_update_navs(self, orchestrator, run_id) -> None:
        registry = orchestrator.mRegistry
        tool = registry.get("update_navs")
        if tool is None:
            return
        original_func = tool.mTool_func

        def instrumented(*args, **kwargs):
            result = original_func(*args, **kwargs)
            rejected = result.get("rejected", []) if isinstance(result, dict) else []
            failures = result.get("failures", []) if isinstance(result, dict) else []
            if rejected or failures:
                self.bus.publish(
                    "acp1_reject", acp="ACP-1", run_id=run_id,
                    rejected=rejected, failures=failures,
                    updated=result.get("updated") if isinstance(result, dict) else None,
                    total=result.get("total") if isinstance(result, dict) else None,
                )
            return result

        tool.mTool_func = instrumented

    # -- ACP-2: reasoning / plan() --------------------------------------------
    # Table 1: "SUBGOAL_CATALOG allow-list ... rejects full-catalog echo."
    # plan() enforces the allow-list internally and prints a fixed message
    # when it detects (and discards) a full-catalog echo; that print is the
    # only externally observable signal for this case, so this adapter
    # tees stdout rather than reaching into plan()'s private parsing helpers.
    # NOTE: silent per-item whitelist stripping (an out-of-catalog subgoal
    # name dropped without triggering the full-echo path) produces no
    # console signal in the baseline and is therefore NOT surfaced here;
    # detecting it would require duplicating plan()'s private JSON-parsing
    # helper against the raw LLM response (see Tests/test_reasoning.py's
    # own monkeypatch-and-assert pattern for how that would be done).
    def _wrap_acp2_plan(self, orchestrator, run_id) -> None:
        tpa = orchestrator.mTPA
        original_plan = tpa.plan

        def instrumented(goal, context_summary):
            subgoals, captured = _tee_stdout(original_plan, goal, context_summary)
            full_echo = _FULL_ECHO_MARKER in captured
            self.bus.publish(
                "acp2_plan_decision", acp="ACP-2", run_id=run_id,
                goal=goal, subgoals=subgoals, subgoal_count=len(subgoals),
                full_catalog_echo_detected=full_echo,
                all_in_catalog=all(s in SUBGOAL_CATALOG for s in subgoals),
            )
            return subgoals

        tpa.plan = instrumented

    # -- ACP-3: reasoning / reflect() -----------------------------------------
    # Table 1: "Grounded-facts constraint and reject ungrounded currency()
    # validation."
    def _wrap_acp3_reflect(self, orchestrator, run_id) -> None:
        tpa = orchestrator.mTPA
        original_reflect = tpa.reflect

        def instrumented(goal, execution_log):
            reflection = original_reflect(goal, execution_log)
            summary = reflection.get("summary", "") or ""
            used_fallback = bool(_FALLBACK_RE.match(summary))
            ungrounded_currency = bool(re.search(r"\$|USD|dollars?", summary, re.IGNORECASE))
            self.bus.publish(
                "acp3_reflection", acp="ACP-3", run_id=run_id,
                goal=goal, used_grounded_fallback=used_fallback,
                ungrounded_currency_detected=ungrounded_currency,
                steps_ok=reflection.get("steps_ok"), steps_total=reflection.get("steps_total"),
            )
            return reflection

        tpa.reflect = instrumented

    # -- ACP-6: memory / check_subgoal_bias() ---------------------------------
    # Table 1: "check_subgoal_bias() and memory-derived anomaly signals."
    # check_subgoal_bias() is defined in agent_memory.py. 
    # This adapter is therefore its first caller in practice,
    # invoked right after each record_episode() so the signal is checked
    # once per completed run, as Table 1's "episodic recording and recall"
    # boundary implies.
    def _wrap_acp6_bias(self, orchestrator, run_id) -> None:
        memory = orchestrator.mMemory
        original_record_episode = memory.record_episode

        def instrumented(*args, **kwargs):
            episode_id = original_record_episode(*args, **kwargs)
            warning = memory.check_subgoal_bias()
            if warning:
                self.bus.publish(
                    "acp6_bias_signal", acp="ACP-6", run_id=run_id,
                    episode_id=episode_id, warning=warning,
                )
            return episode_id

        memory.record_episode = instrumented
