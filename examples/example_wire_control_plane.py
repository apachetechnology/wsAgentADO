"""
examples/example_wire_control_plane.py
Reference wiring order for the additive api_Controls/ package, all six
ACPs together. Mirrors agentic_console.py's own build_orchestrator() -
imported, not duplicated - so this stays in sync with the tracked file
automatically.

Requires a local Ollama server with llama3.2:1b / gemma3:1b pulled, and
the wsAgenticAIFW package importable (run this from the repo root, with
api_Controls/ dropped in alongside agentic_framework/, api_Finance/,
api_server/, config_agent.py). This script is NOT executed as part of
code generation - Ollama and the mfapi.in NAV feed are both outside this
sandbox's network allowlist - so treat it as reference wiring to run in
your own environment, not as a tested artifact.
"""
from agentic_console import build_orchestrator
from config_agent import DEFAULT_ALLOWED_PERMISSIONS, ALL_PERMISSIONS

from api_Controls.bus import CObservabilityMetricsBus
from api_Controls.signal_adapters import CSignalAdapters
from api_Controls.delegation_ledger import DelegationLedger
from api_Controls.tool_access_gate import CToolAccessGate, CToolAccessGateConfig
from api_Controls.controlled_execution import CControlledExecutionEnvironment
from api_Controls.controlled_orchestrator import CControlledOrchestrator
from api_Controls.autonomy_boundary import CAutonomyBoundaryConfig
from api_Controls.closed_loop import CClosedLoopPolicy
from api_Controls.metrics_readout import render_readout


def build_controlled_orchestrator(allow_writes: bool = True) -> CControlledOrchestrator:
    # 1. Build the baseline orchestrator exactly as agentic_console.py does.
    permissions = ALL_PERMISSIONS if allow_writes else DEFAULT_ALLOWED_PERMISSIONS
    orchestrator = build_orchestrator(allow_writes=allow_writes)

    # 2. Phase 0 - the bus every other component reports to.
    bus = CObservabilityMetricsBus()

    # 3. Phase 1 - ACP-1/2/3/6 signal adapters, wrapping the orchestrator's
    #    own TPA/memory/registry in place.
    CSignalAdapters(bus).attach(orchestrator)

    # 4. Phase 3 - ACP-5: reinterpret allowed_permissions as the ledger's
    #    first grant, then wrap mExecution so every tool call is gated.
    ledger = DelegationLedger(bus=bus)
    grant = ledger.bootstrap_from_allowed_permissions(permissions)
    gate = CToolAccessGate(ledger, CToolAccessGateConfig(), bus=bus)
    orchestrator.mExecution = CControlledExecutionEnvironment(
        execution=orchestrator.mExecution, gate=gate, grant_id=grant.grant_id,
    )

    # 5. Phase 4 - close the loop: ACP-1/ACP-6 signals become ACP-5 breakers.
    CClosedLoopPolicy(bus, gate)

    # 6. Phase 2 - ACP-4: wrap the whole run() loop with the autonomy
    #    boundary service, composing the now-instrumented, now-gated
    #    orchestrator built above.
    controlled = CControlledOrchestrator(orchestrator, bus, CAutonomyBoundaryConfig())
    controlled.bus = bus  # already set in __init__; kept explicit for clarity
    return controlled


if __name__ == "__main__":
    objControlled = build_controlled_orchestrator(allow_writes=True)

    reflection = objControlled.run(
        "Update NAVs for owner SG, record today's history, then give me "
        "a performance review and flag any fund down more than 8% from its peak.",
        owner_name="SG",
    )

    print("\n" + "=" * 80)
    print("Pending escalations:", reflection.get("escalations_pending"))
    for escalation_id in reflection.get("escalations_pending", []):
        # A real deployment would route this to a human/supervisory agent;
        # here we just show both resolution paths.
        objControlled.resolve_escalation(escalation_id, approved=True)

    print("\n" + "=" * 80)
    print(render_readout(objControlled.bus, run_id=reflection["run_id"]))
