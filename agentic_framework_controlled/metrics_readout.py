"""
api_Controls/metrics_readout.py
Phase 6: renders CObservabilityMetricsBus.rollups() as the metrics
readout referenced in Paper 3 Section 5 (Operational Risk Metrics -
delegation duration/active grants, autonomy persistence, escalation
latency, and the BlastRadius(a) approximation via ACP-5 tier counts).
Purely a formatting layer over bus.rollups(); no new metrics are
computed here.
"""
from __future__ import annotations

import json
from typing import Optional

from agentic_framework_controlled.bus import CObservabilityMetricsBus


def render_readout(bus: CObservabilityMetricsBus, run_id: Optional[str] = None) -> str:
    target = bus.scoped(run_id) if run_id else bus
    rollups = target.rollups()

    sections = [
        ("Delegation (depth / duration / active grants)", rollups["delegation"]),
        ("Tool blast-radius approximation (ACP-5 tier counts)", rollups["blast_radius"]),
        ("Autonomy persistence (ACP-4)", rollups["autonomy_persistence"]),
        ("Escalation latency (ACP-4)", rollups["escalation_latency"]),
    ]
    title = f"# Operational Risk Metrics readout{f' - run {run_id}' if run_id else ''}"
    lines = [title, ""]
    for heading, data in sections:
        lines.append(f"## {heading}")
        lines.append(f"```\n{json.dumps(data, indent=2, default=str)}\n```")
    return "\n\n".join(lines)
