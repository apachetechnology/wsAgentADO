"""
Tests/conftest.py
Shared fixtures for wsAgentADO's own red-team suite
(`test_perception_redteam.py`, `test_reasoning_redteam.py`). New,
additive file — `test_control_plane_redteam.py` needed none of this
since it uses its own self-contained fakes; these two fixtures back the
ORIGINAL red-team tests, which exercise the real framework classes
end-to-end. Nothing here touches the network or a real Ollama server —
each test monkeypatches the one call that would (`resolve_fund` /
`get_response`) — and nothing touches production data, since
CHoldingsDatabase/CAgentMemory are both pointed at pytest's per-test
`tmp_path` explicitly.
"""
import pytest

from api_Finance.database import CHoldingsDatabase, HoldingEntry
from api_Finance.nav_fetcher import CFetchNAV
from api_Finance.performance_analyzer import CPerformanceAnalyzer
from agentic_framework.agent_tools import CToolRegistry
from agentic_framework.agent_memory import CAgentMemory
from agentic_framework.layer_reasoning import CTaskPlanningAgent


class _FakeDBInterface:
    """
    Stand-in for CDBInterface. The real class's __init__ always builds
    its OWN CHoldingsDatabase()/CFetchNAV() with no dependency-injection
    hook at all (no db_path parameter), so a real CDBInterface can't be
    pointed at a temp DB — using one here would touch whatever sqlite
    file config_db.DB_PATH names in this checkout. Only `add_fund` calls
    mDBInterface (AddNewBaseFund), and neither red-team test below
    exercises `add_fund`, so a bare stand-in that fails loudly if ever
    called is the correct, safe substitute.
    """
    def AddNewBaseFund(self, *args, **kwargs):
        raise NotImplementedError(
            "add_fund is not exercised by the perception/reasoning red-team "
            "tests — if a new test needs it, give CDBInterface its own "
            "temp-path CHoldingsDatabase/CFetchNAV rather than extending this stub."
        )


@pytest.fixture
def tool_registry(tmp_path):
    """
    A real CToolRegistry wired to a temp-file CHoldingsDatabase (so
    update_navs' actual MAX_DAILY_MOVE logic runs unmodified) and a
    real CFetchNAV (network calls are monkeypatched per-test on
    `resolve_fund` — the method update_navs() actually calls, NOT
    `get_latest_nav`).
    """
    db = CHoldingsDatabase(tmp_path / "holdings_test.db")
    db.insert_holding(HoldingEntry(
        owner_name="SG", fund_name="TEST FUND", holding_units=100.0,
        nav_base=10.0, cost_value=1000.0, statement_date="2026-01-01",
        nav_latest=10.0, nav_highest=10.0, nav_lowest=10.0, nav_change=0.0,
    ))
    fetcher = CFetchNAV()
    analyzer = CPerformanceAnalyzer(db)
    registry = CToolRegistry(db, fetcher, analyzer, _FakeDBInterface())
    yield registry
    db.close()


class _FakeOllamaServer:
    """
    Bare stand-in for COllamaServer. `build_message` is real (it's a
    trivial dict-shaping helper the TPA calls before ever reaching the
    network), `get_response` deliberately raises unless a test
    monkeypatches it — exactly what every existing reasoning red-team
    case already does, so this never silently starts a real Ollama
    round-trip.
    """
    def build_message(self, role: str, content: str) -> dict:
        return {"role": role, "content": content}

    def get_response(self, *args, **kwargs):
        raise NotImplementedError("monkeypatch tpa.mOS.get_response per-test")


@pytest.fixture
def tpa(tmp_path):
    memory = CAgentMemory(db_path=tmp_path / "agent_memory_test.db")
    return CTaskPlanningAgent(_FakeOllamaServer(), memory)
