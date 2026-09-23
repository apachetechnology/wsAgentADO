"""
agentic_console.py
Entry point for the agentic fund-management framework (Paper Section
3.2 baseline architecture) built on top of the existing holdings
tracker. Mirrors the style of console.py / nav_fetcher.py's own
__main__ blocks.

Run:
    python agentic_console.py

Requires an Ollama server running locally with the models named in
agentic_framework/agent_config.py (MODEL_TPA, MODEL_TSA) pulled, e.g.:
    ollama pull llama3.2:1b
    ollama pull gemma3:1b
"""

from api_server.Ollama_server import COllamaServer
from api_Finance.database import CHoldingsDatabase
from api_Finance.nav_fetcher import CFetchNAV
from api_Finance.performance_analyzer import CPerformanceAnalyzer
from api_Finance.db_interface import CDBInterface

from config_agent import DEFAULT_ALLOWED_PERMISSIONS, ALL_PERMISSIONS
from agentic_framework.layer_orchestrator import CAgenticOrchestrator

def build_orchestrator(allow_writes: bool = True) -> CAgenticOrchestrator:
    objOS = COllamaServer()
    if not objOS.start_server():
        raise RuntimeError("Ollama server could not be started - run 'ollama serve' manually.")
    objOS.get_client("ollama")

    objDB = CHoldingsDatabase()
    objFNAV = CFetchNAV()
    objFNAV.GetNAVsAll(bForceUpdate=False)
    objAnalyzer = CPerformanceAnalyzer(objDB)
    objDBInterface = CDBInterface()

    # WRITE/NETWORK/PLOT are opt-in per the action layer's sandbox -
    # default to allowing them here since this is an interactive demo.
    permissions = ALL_PERMISSIONS if allow_writes else DEFAULT_ALLOWED_PERMISSIONS

    return CAgenticOrchestrator(
        aOllamaServer=objOS, aDB=objDB, aFetcher=objFNAV,
        aAnalyzer=objAnalyzer, aDBInterface=objDBInterface,
        allowed_permissions=permissions,
    )


if __name__ == "__main__":
    objOrchestrator = build_orchestrator(allow_writes=True)

    # Example goals - the TPA plans, the TSA fills in arguments, and the
    # action layer executes each step under sandboxed permissions.
    objOrchestrator.run(
        "Update NAVs for owner SG, record today's history, then give me "
        "a performance review and flag any fund down more than 8% from its peak.",
        owner_name="SG",
    )

    print("\n" + "=" * 80 + "\n")

    objOrchestrator.run(
        'Give me a portfolio report and plot the NAV history for '
        '"NIPPON INDIA SILVER ETF FOF REGULAR-GROWTH".',
        owner_name="SG",
    )
