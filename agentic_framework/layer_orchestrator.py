"""
orchestrator.py
CAgenticOrchestrator - Paper Fig. 1 "Orchestration Layer": manages
workflow execution by assigning tasks, optimizing resource allocation,
and facilitating inter-agent communication (TPA <-> TSA <-> Action).
Serves as the strategic control-and-coordination hub for the whole run.

Flow for a single call to run():
    1. Perception  : snapshot the portfolio + describe it compactly
    2. Reasoning/TPA: plan()      -> ordered subgoals
    3. Reasoning/TSA: setup()     -> tool + args per subgoal
    4. Action       : run_step()  -> sandboxed execution, one per subgoal
    5. Reasoning/TPA: reflect()   -> plain-language summary + success flag
    6. Memory       : record_episode() for future recall
"""

import textwrap
from typing import Dict, Optional, Set

from api_Finance.database import CHoldingsDatabase
from api_Finance.nav_fetcher import CFetchNAV
from api_Finance.performance_analyzer import CPerformanceAnalyzer
from api_Finance.db_interface import CDBInterface
from api_server.Ollama_server import COllamaServer

from config_agent import DEFAULT_ALLOWED_PERMISSIONS, print_wrap
from agentic_framework.agent_memory import CAgentMemory
from agentic_framework.layer_perception import CPerceptionLayer
from agentic_framework.agent_tools import CToolRegistry
from agentic_framework.layer_execution import CExecutionEnvironment
from agentic_framework.layer_reasoning import CTaskPlanningAgent, CTaskSetupAgent


############################################################################
#
class CAgenticOrchestrator:
    def __init__(self, aOllamaServer: COllamaServer, aDB: CHoldingsDatabase,
                 aFetcher: CFetchNAV, aAnalyzer: CPerformanceAnalyzer,
                 aDBInterface: CDBInterface,
                 allowed_permissions: Set[str] = DEFAULT_ALLOWED_PERMISSIONS):
        self.mMemory = CAgentMemory()
        self.mPerception = CPerceptionLayer(aDB, aFetcher)
        self.mRegistry = CToolRegistry(aDB, aFetcher, aAnalyzer, aDBInterface)
        self.mExecution = CExecutionEnvironment(self.mRegistry, allowed_permissions)
        self.mTPA = CTaskPlanningAgent(aOllamaServer, self.mMemory)
        self.mTSA = CTaskSetupAgent(aOllamaServer, self.mMemory)

    # ------------------------------------------------------------------ #
    # Resource allocation - a simple per-run tally of tool calls by
    # permission bucket, printed as part of the coordination log. Keeps
    # the "optimizing resource allocation" duty of the orchestration
    # layer visible rather than implicit.
    # ------------------------------------------------------------------ #
    def _summarize_resource_use(self, log) -> str:
        by_status: Dict[str, int] = {}
        for record in log:
            by_status[record.mStrStatus] = by_status.get(record.mStrStatus, 0) + 1
        return ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())) or "no steps run"

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def run(self, strGoal: str, owner_name: Optional[str] = None,
            extra_args: Optional[Dict[str, Dict]] = None, aWidth=80) -> Dict:
        """
        extra_args: optional {subgoal: {arg_name: value}} overrides/additions
        for subgoals the TSA can't reliably fill from free text alone
        (e.g. add_fund's holding_units/nav_base).
        """
        extra_args = extra_args or {}
        self.mExecution.reset_state()
        self.mMemory.reset_short_term()

        # 1. Perception - task assignment starts once context is gathered.
        dictSnapshot = self.mPerception.gather_portfolio_snapshot(owner_name)
        strContextSummary = self.mPerception.describe_context(dictSnapshot)
        print_wrap(f"[Orchestrator] Context gathered: {len(dictSnapshot)} holding row(s).")

        # 2. Reasoning/TPA - plan the subgoals.
        listSubgoals = self.mTPA.plan(strGoal, strContextSummary)
        print_wrap(f"[Orchestrator] Task assignment - subgoals: {listSubgoals or '(none identified)'}")

        # 3-4. Reasoning/TSA + Action - set up and execute each subgoal.
        for subgoal in listSubgoals:
            dictStep = self.mTSA.setup(subgoal, strGoal, default_owner=owner_name)
            if dictStep is None:
                self.mMemory.add_short_term(subgoal, tool="?", ok=False, note="no tool mapping")
                continue

            dictStep["args"].update(extra_args.get(subgoal, {}))
            objExeRecord = self.mExecution.run_step(dictStep["tool"], dictStep["args"])
            self.mMemory.add_short_term(subgoal, dictStep["tool"], objExeRecord.mStrStatus == "ok",
                                         note=objExeRecord.mError)

            strStatusNote = f"{dictStep['tool']} -> {objExeRecord.mStrStatus}"
            if objExeRecord.mError:
                strStatusNote += f" ({objExeRecord.mError})"
            print_wrap(f"[Orchestrator] {strStatusNote}")

        # 5. Reasoning/TPA - reflect over the full execution log.
        dictReflection = self.mTPA.reflect(strGoal, self.mExecution.get_log())
        print_wrap(f"[Orchestrator] Resource allocation - {self._summarize_resource_use(self.mExecution.get_log())}")
        print_wrap(f"[Orchestrator] Reflection: {dictReflection['summary']}")

        # 6. Memory - record the episode for future recall.
        self.mMemory.record_episode(strGoal, listSubgoals,
                             [vars(r) for r in self.mExecution.get_log()],
                             dictReflection["summary"], dictReflection["success"],
                             owner_name=owner_name)

        print("-" * 80)
        print("Goal:", textwrap.fill(strGoal, width=aWidth))
        print("Subgoals:", listSubgoals)
        self.mExecution.print_log_tabular()
        
        print('\n' + "_" * 80)

        return dictReflection
