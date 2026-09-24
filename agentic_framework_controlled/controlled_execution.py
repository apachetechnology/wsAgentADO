"""
api_Controls/controlled_execution.py
Phase 3: CControlledExecutionEnvironment composes an existing
CExecutionEnvironment, calling CToolAccessGate.authorize() before
forwarding to the real run_step(). On denial it returns a synthetic
CExecutionRecord with mStrStatus="denied" - the same shape
CExecutionEnvironment itself already uses for its own permission-tag
denials - so callers (CControlledOrchestrator, or CAgenticOrchestrator
itself if pointed at this wrapper) never need to special-case this
wrapper's return type.
"""
from __future__ import annotations

from typing import Dict, Optional

from agentic_framework.layer_execution import CExecutionEnvironment, CExecutionRecord
from agentic_framework.agent_tools import CToolRegistry

from agentic_framework_controlled.tool_access_gate import CToolAccessGate


class CControlledExecutionEnvironment:
    def __init__(self, execution: CExecutionEnvironment, gate: CToolAccessGate,
                 grant_id: str, run_id: Optional[str] = None):
        self._execution = execution
        self.gate = gate
        self.grant_id = grant_id
        self.run_id = run_id

    def run_step(self, tool_name: str, args: Dict) -> CExecutionRecord:
        registry: CToolRegistry = self._execution.mRegistry
        tool = registry.get(tool_name)
        required_permissions = tool.mTool_permissions if tool else set()

        result = self.gate.authorize(
            tool_name=tool_name,
            required_permissions=required_permissions,
            allowed_permissions=self._execution.mAllowedPermissions,
            grant_id=self.grant_id,
            run_id=self.run_id,
        )

        if not result.authorized:
            record = CExecutionRecord(
                tool_name, args, "denied",
                mError="ACP-5 denied: " + "; ".join(result.reasons),
            )
            self._execution.mListExeRecord.append(record)
            return record

        # SG: This try/except is retained purely as a general safety net in case    
        # any tool's own mTool_func implementation raises unexpectedly, 
        # so one bad tool-chain step cannot abort the whole orchestrator run.
        try:
            return self._execution.run_step(tool_name, args)
        except Exception as e:
            record = CExecutionRecord(tool_name, args, "error", mError=str(e))
            self._execution.mListExeRecord.append(record)
            return record

    # -- pass-through so this wrapper is a drop-in for CExecutionEnvironment --
    def get_log(self):
        return self._execution.get_log()

    def reset_state(self) -> None:
        self._execution.reset_state()

    def print_log_tabular(self, bVerbose: bool = True) -> None:
        self._execution.print_log_tabular(bVerbose)

    @property
    def mAllowedPermissions(self):
        return self._execution.mAllowedPermissions

    @property
    def mRegistry(self):
        return self._execution.mRegistry

    @property
    def mListExeRecord(self):
        return self._execution.mListExeRecord
