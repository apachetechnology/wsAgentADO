"""
api_Controls/metrics_readout.py
STEP 6: renders CObservabilityMetricsBus.rollups() as the metrics
readout referenced in Section 5 (Operational Risk Metrics -
delegation duration/active grants, autonomy persistence, escalation
latency, and the ImpactScope(a) approximation via ACP-5 tier counts).
Purely a formatting layer over bus.rollups(); no new metrics are
computed here.
"""
from __future__ import annotations

import json
from typing import Optional

from agentic_framework_controlled.bus import CObservabilityMetricsBus


def render_readout(bus: CObservabilityMetricsBus, run_id: Optional[str] = None) -> str:
    target = bus.scoped(run_id) if run_id else bus

    sections = [
        ("Delegation (session-wide, depth / duration / active grants)", bus.delegation_depth_and_duration()),
        ("Tool impact-scope approximation (ACP-5 tier counts)", target.tool_impact_scope()),
        ("Autonomy persistence (ACP-4)", target.autonomy_persistence()),
        ("Escalation latency (ACP-4)", target.escalation_latency()),
    ]
    
    title = f"# Operational Risk Metrics readout{f' - run {run_id}' if run_id else ''}"
    lines = [title, ""]
    for heading, data in sections:
        lines.append(f"## {heading}")
        lines.append(f"```\n{json.dumps(data, indent=2, default=str)}\n```")
    return "\n\n".join(lines)
