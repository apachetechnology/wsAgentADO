# High Level Design

### A. Design Primitives for Secure Agentic Systems

| **Primitive** | **Existing Evidence** | **Demonstration** | 
| **Bounded & scoped autonomy** | `config_agent.DEFAULT_ALLOWED_PERMISSIONS = {"READ","COMPUTE"}` vs `ALL_PERMISSIONS`; `CAgenticOrchestrator.__init__` takes `allowed_permissions` as a per-run parameter, not a global | Run the same goal twice — once under `DEFAULT_ALLOWED_PERMISSIONS`, once under `ALL_PERMISSIONS` — and log the delta in what gets executed vs. denied | 
| **Revocable & time-bounded delegation** | `mMemory.reset_short_term()` called at the top of every `run()`; `SHORT_TERM_MEMORY_TURNS`-bounded deque; `CAgentMemory.quarantine_episode()` excludes a specific past episode from future recall | Show a delegation window = one `run()` invocation; demonstrate `quarantine_episode()` revoking influence of a specific prior (bad) episode on subsequent planning | 
| **Graduated, blast-radius-aware tool access** | `CTool.mTool_permissions` tags per tool (`READ`, `COMPUTE`, `WRITE`, `NETWORK`, `PLOT`) in `agent_tools.py`; enforced by the permission-diff check in `CExecutionEnvironment.run_step()` | Classify all 9 registered tools into blast-radius tiers by permission set (e.g. `update_navs = WRITE + NETWORK = high`; `performance_review = READ + COMPUTE = low`) — this becomes a table in the paper | 

### B. Agentic Control Points and Runtime Assurance

| **Layer** | **ACP Already in Code** | **Runtime Assurance Evidence** | 
| **Perception** | `MAX_DAILY_MOVE` plausibility bound in `update_navs()` | `test_perception_redteam.py` — spoofed-feed rejection (already committed, already passing) | 
| **Reasoning** | Closed-vocabulary whitelist in `CTaskPlanningAgent.plan()`; `CAgentMemory.check_subgoal_bias()` flagging skewed subgoal distributions as a possible poisoning/injection signature | `test_reasoning_redteam.py` — adversarial-goal rejection | 
| **Action/Execution** | Permission gate + fail-closed `try/except` in `run_step()` | Already the substance of the Case-Study-B mechanism (kept out of this paper per your "clean" call, but the general mechanism — not that specific incident — is fair game to cite as the ACP) | 
| **Orchestration** | `_summarize_resource_use()` tally; `quarantine_episode()` as a revocation ACP | **New:** batch-run the framework and show `check_subgoal_bias()` firing on an artificially skewed episode set | 

**Honest gap:** None of this is a human-in-the-loop escalation mechanism — there's no code path today where a control point pauses and waits for approval. If Section 2.3's "escalation ... human-in-the-loop or supervisory agents" bullet stays in, it needs to be scoped as proposed/specified rather than demonstrated, or built as new code in a separate session (can't do that under "don't modify the code" here anyway).

### C. Operational Risk Metrics — Measurability Audit

| **Metric** | **Measurable from Repo Today?** | **Source** | 
| **Tool blast radius** | Yes | `mTool_permissions` per tool | 
| **Delegation depth/duration** | Yes | Subgoal count + permission set per `run()`, via `_summarize_resource_use()` | 
| **Autonomy persistence** | Yes (baseline = 0; permissions don't carry across runs) | `run()` resets state each call | 
| **Escalation latency** | No | No escalation path exists | 
| **Cross-agent propagation potential** | No | Not in `agentic_framework` (single orchestrator, no inter-agent messaging) | 

— I'd rather put this table in the paper honestly than assert all five metrics are demonstrated — that's the exact pattern that drew fire on paper 1.

**One conflict to flag on "clean":** `sim_malware_quarantine` and `sim_flight_booking` — the only multi-component setups that might otherwise fill the escalation/cross-agent gaps — are already claimed as PoC evidence in the magazine paper's Section 5/Table 5. Reusing them here would recreate the exact overlap problem you asked me to avoid. So my recommendation is to leave those two metrics as specified-but-not-yet-demonstrated in this paper, rather than reach into the `sim_*` folders.



# api_Controls/ — additive ACP-1..6 implementation for Paper 3

Implements all six Agentic Control Points from Paper 3 Section 4.2
("Engineering and Assuring Secure Agentic AI Systems") as an **additive**
package that composes over `apachetechnology/wsAgenticAIFW` without
editing a single tracked file.

- Reference commit: `61f74eba4107751db612966030b44db620947bfd` (this is
  also the current `main` HEAD as of 2026-09-20 — no drift to reconcile).
- Verified: `git diff` against the clone is empty after dropping this
  package in; `git status --short` shows only new, untracked paths.
- Verified: `Tests/test_control_plane_redteam.py` — 22/22 passing,
  standalone (no Ollama, no live DB, no network).

## Layout

```
api_Controls/
  bus.py                    Phase 0 — Observability & Metrics Bus
  signal_adapters.py         Phase 1 — ACP-1 / ACP-2 / ACP-3 / ACP-6 signal adapters
  autonomy_boundary.py        Phase 2 — ACP-4 policy (irreversibility, cost, persistence)
  controlled_orchestrator.py  Phase 2 — CControlledOrchestrator (ACP-4 wired into the run loop)
  delegation_ledger.py        Phase 3 — ACP-5 DelegationGrant / DelegationLedger
  tool_access_gate.py         Phase 3 — ACP-5 CToolAccessGate, Authorize(a,t,r) literally
  controlled_execution.py     Phase 3 — CControlledExecutionEnvironment (ACP-5 wired into run_step)
  closed_loop.py              Phase 4 — ACP-1/ACP-6 signals -> ACP-5 circuit breakers
  metrics_readout.py          Phase 6 — renders bus.rollups() for Paper 3 Section 5
  __init__.py
Tests/
  test_control_plane_redteam.py   Phase 5 — expiry / cascade-revoke / breaker-trip red-team cases
examples/
  example_wire_control_plane.py   full wiring order, all six ACPs together
```

Drop `api_Controls/` and `Tests/test_control_plane_redteam.py` into the
repo root, alongside `agentic_framework/`, `api_Finance/`, `api_server/`,
and `config_agent.py`.

## ACP -> module map (Table 1)

| ACP | Table 1 mechanism | Where it's implemented here |
|---|---|---|
| ACP-1 | Plausibility-bound NAV validation | `signal_adapters.py` wraps `update_navs`'s existing `rejected`/`failures` return values |
| ACP-2 | SUBGOAL_CATALOG allow-list, rejects full-catalog echo | `signal_adapters.py` wraps `plan()`, detects the echo via its existing console print |
| ACP-3 | Grounded-facts constraint, reject ungrounded currency | `signal_adapters.py` wraps `reflect()` — **see bug note below** |
| ACP-4 | Autonomy boundary service | `autonomy_boundary.py` + `controlled_orchestrator.py` |
| ACP-5 | Permission gate + delegation ledger + tool access gate | `delegation_ledger.py` + `tool_access_gate.py` + `controlled_execution.py` |
| ACP-6 | `check_subgoal_bias()` diagnostic signal | `signal_adapters.py` wraps `record_episode()` and calls it (baseline never does) |

Phase 4 (`closed_loop.py`) is the part of Section 4.2's closing paragraph
that turns ACP-1/ACP-6 from "logged" into "enforceable": repeated ACP-1
rejections trip a tool-level breaker; an ACP-6 bias warning trips a
run-level breaker, both via `CToolAccessGate.trip_*_breaker()`.

## Wiring order

See `examples/example_wire_control_plane.py`. In brief:

1. Build the baseline `CAgenticOrchestrator` exactly as `agentic_console.py` does.
2. Create the bus (Phase 0).
3. `CSignalAdapters(bus).attach(orchestrator)` — Phase 1 (ACP-1/2/3/6).
4. `DelegationLedger.bootstrap_from_allowed_permissions(...)`, then wrap
   `orchestrator.mExecution` in `CControlledExecutionEnvironment` — Phase 3 (ACP-5).
5. `CClosedLoopPolicy(bus, gate)` — Phase 4.
6. Wrap the whole thing in `CControlledOrchestrator` — Phase 2 (ACP-4).

This script is reference wiring, not a tested artifact: Ollama and
`mfapi.in` are both outside this sandbox's network allowlist, so it
could not be executed end-to-end here. Everything else (`api_Controls/`
itself and the Phase 5 tests) was compiled and run against the real
cloned repo.

## Two honesty-critical findings from inspecting the live code

These affect how ACP-3 and ACP-5 should be described going forward —
worth a look before the next Paper 3 revision, alongside the three
honesty-critical notes already carried in the draft (ACP-1 trust
boundary siting, the delegation-primitive gap, ACP-6 being diagnostic-
only).

**1. `reflect()`'s currency check never actually runs (ACP-3).**
`CTaskPlanningAgent._reject_ungrounded_currency` is declared
`@staticmethod` with signature `(self, text)`, but called as
`self._reject_ungrounded_currency(raw)`. A `@staticmethod` strips the
implicit `self` binding even through an instance, so that call always
raises `TypeError: missing 1 required positional argument: 'text'`
(confirmed directly against the cloned file — see
`signal_adapters.py`'s module docstring for the repro). That
`TypeError` is swallowed by `reflect()`'s own `except Exception:
summary = None`, so **in the current commit, `reflect()` always falls
through to the deterministic, grounded-facts summary** — the
LLM-authored, currency-checked branch never executes, regardless of
what the model actually said. The net effect Table 1 describes (no `$`
figures reach the user) still holds, but only as a side effect of an
exception path, not via the explicit check. The ACP-3 signal adapter
reports `used_grounded_fallback`, which will read `True` for
essentially every real run until this is fixed in a tracked file —
which this package deliberately does not do.

**2. A second, independent bug in the same file affects ACP-5's error
handling.** `CExecutionEnvironment.run_step()`'s missing-required-args
branch constructs `CExecutionRecord(..., error=f"...")`, but the
dataclass field is `mError`, not `error`. If that branch is ever hit,
it raises `TypeError` instead of returning a "skipped" record, and
that exception is **not** caught by `run_step()`'s own try/except
(which only wraps the tool-function call itself). `controlled_execution.py`
defensively catches this at the ACP-5 gate boundary and converts it
into a same-shaped `"error"` record, so a single bad tool-chain step
can't take down an otherwise-controlled run — but the underlying bug is
still there in the tracked file.

Also worth noting: `check_subgoal_bias()` is defined in `agent_memory.py`
but is not called from anywhere in the baseline framework (no caller in
`agentic_framework/`, `Tests/`, or the `sim_*` scenarios) — the ACP-6
signal adapter is its first real caller.

## Phase 5 test-suite scope

The reference commit has no `Tests/conftest.py`, so the `tpa` /
`tool_registry` fixtures that `Tests/test_reasoning_redteam.py` and
`Tests/test_perception_redteam.py` reference aren't defined anywhere —
those two files can't be collected by `pytest` as-is in this commit.
`test_control_plane_redteam.py` is therefore self-contained: it uses
minimal stand-ins matching the exact shapes `signal_adapters.py`
reads/writes, and exercises `delegation_ledger.py` / `tool_access_gate.py`
/ `autonomy_boundary.py` / `closed_loop.py` directly with no framework,
Ollama, or database dependency. Scenario replay against
`nbAgenticConsole.ipynb` remains a separate, manual validation step, as
the Phase 5 plan specifies.
