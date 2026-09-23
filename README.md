# High Level Design

## Layout

```
api_Controls/
  bus.py                     Observability & Metrics Bus
  signal_adapters.py         ACP-1 / ACP-2 / ACP-3 / ACP-6 signal adapters
  autonomy_boundary.py       ACP-4 policy (irreversibility, cost, persistence)
  controlled_orchestrator.py CControlledOrchestrator (ACP-4 wired into the run loop)
  delegation_ledger.py        ACP-5 DelegationGrant / DelegationLedger
  tool_access_gate.py         ACP-5 CToolAccessGate, Authorize(a,t,r) literally
  controlled_execution.py     CControlledExecutionEnvironment (ACP-5 wired into run_step)
  closed_loop.py              ACP-1/ACP-6 signals -> ACP-5 circuit breakers
  metrics_readout.py          renders bus.rollups() for Paper 3 Section 5
  __init__.py
Tests/
  test_control_plane_redteam.py   Expiry / cascade-revoke / breaker-trip red-team cases
examples/
  example_wire_control_plane.py   full wiring order, all six ACPs together
```

## ACP -> module map

| ACP | Table 1 mechanism | Where it's implemented here |
|---|---|---|
| ACP-1 | Plausibility-bound NAV validation | `signal_adapters.py` wraps `update_navs`'s existing `rejected`/`failures` return values |
| ACP-2 | SUBGOAL_CATALOG allow-list, rejects full-catalog echo | `signal_adapters.py` wraps `plan()`, detects the echo via its existing console print |
| ACP-3 | Grounded-facts constraint, reject ungrounded currency | `signal_adapters.py` wraps `reflect()` - **see bug note below** |
| ACP-4 | Autonomy boundary service | `autonomy_boundary.py` + `controlled_orchestrator.py` |
| ACP-5 | Permission gate + delegation ledger + tool access gate | `delegation_ledger.py` + `tool_access_gate.py` + `controlled_execution.py` |
| ACP-6 | `check_subgoal_bias()` diagnostic signal | `signal_adapters.py` wraps `record_episode()` and calls it (baseline never does) |

## Testing

- Verified: `Tests/test_control_plane_redteam.py` - 22/22 passing, standalone (no Ollama, no live DB, no network).

In brief `examples/example_wire_control_plane.py`.

1. Build the baseline `CAgenticOrchestrator` exactly as `agentic_console.py` does.
2. Create the bus (Phase 0).
3. `CSignalAdapters(bus).attach(orchestrator)` - Phase 1 (ACP-1/2/3/6).
4. `DelegationLedger.bootstrap_from_allowed_permissions(...)`, then wrap
   `orchestrator.mExecution` in `CControlledExecutionEnvironment` - Phase 3 (ACP-5).
5. `CClosedLoopPolicy(bus, gate)` - Phase 4.
6. Wrap the whole thing in `CControlledOrchestrator` - Phase 2 (ACP-4).

This script is for reference, not a tested artifact: Ollama and `mfapi.in` are both outside this sandbox's network allowlist, so it could not be executed end-to-end here. Everything else (`api_Controls/` itself and the Phase 5 tests) was compiled and run against the real cloned repo.

## A. Design Primitives for Secure Agentic Systems
| **Primitive** | **Methodology** | **Unit Test** | 
|---|---|---|
| **Bounded & scoped autonomy** | Run the same goal twice - once under `DEFAULT_ALLOWED_PERMISSIONS`, once under `ALL_PERMISSIONS` - and log the delta in what gets executed vs. denied. | Set `config_agent.DEFAULT_ALLOWED_PERMISSIONS = {"READ","COMPUTE"}` and `ALL_PERMISSIONS`; `CAgenticOrchestrator.__init__` takes `allowed_permissions` as a per-run parameter to test the goal | 
| **Revocable & time-bounded delegation** | Show a delegation window = one `run()` invocation; demonstrate `quarantine_episode()` revoking influence of a specific prior (bad) episode on subsequent planning. | `mMemory.reset_short_term()` called at the top of every `run()`; `SHORT_TERM_MEMORY_TURNS`-bounded deque; `CAgentMemory.quarantine_episode()` excludes a specific past episode from future recall | 
| **Graduated, blast-radius-aware tool access** | Classify all 9 registered tools into blast-radius tiers by permission set (e.g., `update_navs = WRITE + NETWORK = high`; `performance_review = READ + COMPUTE = low`) - this becomes a table in the paper. | `CTool.mTool_permissions` tags per tool (`READ`, `COMPUTE`, `WRITE`, `NETWORK`, `PLOT`) in `agent_tools.py`; enforced by the permission-diff check in `CExecutionEnvironment.run_step()` | 


## B. Agentic Control Points and Runtime Assurance

| **Layer** | **ACP** | **Runtime Assurance Evidence** | 
|---|---|---|
| **Perception** | `MAX_DAILY_MOVE` plausibility bound in `update_navs()` | `test_perception_redteam.py` - spoofed-feed rejection (already committed, already passing) | 
| **Reasoning** | Closed-vocabulary whitelist in `CTaskPlanningAgent.plan()`; `CAgentMemory.check_subgoal_bias()` flagging skewed subgoal distributions as a possible poisoning/injection signature | `test_reasoning_redteam.py` - adversarial-goal rejection | 
| **Action/Execution** | Permission gate + fail-closed `try/except` in `run_step()` | Already the substance of the Case-Study-B mechanism (kept out of this paper per your "clean" call, but the general mechanism - not that specific incident - is fair game to cite as the ACP) | 
| **Orchestration** | `_summarize_resource_use()` tally; `quarantine_episode()` as a revocation ACP | Batch-run the framework and show `check_subgoal_bias()` firing on an artificially skewed episode set | 

------------------
## Two honesty-critical findings from inspecting the live code

These affect how ACP-3 and ACP-5 should be described going forward worth a look before the next Paper 3 revision, alongside the three honesty-critical notes already carried in the draft (ACP-1 trust boundary siting, the delegation-primitive gap, ACP-6 being diagnostic-only).

**1. `reflect()`'s currency check never actually runs (ACP-3).**
`CTaskPlanningAgent._reject_ungrounded_currency` is declared
`@staticmethod` with signature `(self, text)`, but called as
`self._reject_ungrounded_currency(raw)`. A `@staticmethod` strips the
implicit `self` binding even through an instance, so that call always
raises `TypeError: missing 1 required positional argument: 'text'`
(confirmed directly against the cloned file - see
`signal_adapters.py`'s module docstring for the repro). That
`TypeError` is swallowed by `reflect()`'s own `except Exception:
summary = None`, so **in the current commit, `reflect()` always falls
through to the deterministic, grounded-facts summary** - the
LLM-authored, currency-checked branch never executes, regardless of
what the model actually said. The net effect Table 1 describes (no `$`
figures reach the user) still holds, but only as a side effect of an
exception path, not via the explicit check. The ACP-3 signal adapter
reports `used_grounded_fallback`, which will read `True` for
essentially every real run until this is fixed in a tracked file -
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
can't take down an otherwise-controlled run - but the underlying bug is
still there in the tracked file.

Also worth noting: `check_subgoal_bias()` is defined in `agent_memory.py`
but is not called from anywhere in the baseline framework (no caller in
`agentic_framework/`, `Tests/`, or the `sim_*` scenarios) - the ACP-6
signal adapter is its first real caller.

## Phase 5 test-suite scope

The reference commit has no `Tests/conftest.py`, so the `tpa` /
`tool_registry` fixtures that `Tests/test_reasoning_redteam.py` and
`Tests/test_perception_redteam.py` reference aren't defined anywhere -
those two files can't be collected by `pytest` as-is in this commit.
`test_control_plane_redteam.py` is therefore self-contained: it uses
minimal stand-ins matching the exact shapes `signal_adapters.py`
reads/writes, and exercises `delegation_ledger.py` / `tool_access_gate.py`
/ `autonomy_boundary.py` / `closed_loop.py` directly with no framework,
Ollama, or database dependency. Scenario replay against
`nbAgenticConsole.ipynb` remains a separate, manual validation step, as
the Phase 5 plan specifies.


Sandeep
```

## Phased Plan

* **Phase 0 - Bus first (foundation).** `bus.py`: an append-only event log (`ObservabilityBus.emit(acp, event_type, payload)`) plus rollup methods for the four metrics named in the paper (delegation depth/duration, tool blast radius, autonomy persistence, escalation latency). Everything below emits into it.
* **Phase 1 - Wire ACP-1/2/3/6 (already implemented, just unobserved).** Thin adapters, no logic changes:
  * **ACP-1:** Read rejected/failures from `agent_tools.update_navs()`'s return dict $\rightarrow$ `bus.emit`.
  * **ACP-2/3:** Diff `CTaskPlanningAgent.plan()`'s LLM output against its filtered return, and capture `_reject_ungrounded_currency()` discards $\rightarrow$ `bus.emit`.
  * **ACP-6:** Call `CAgentMemory.check_subgoal_bias()` once per run, post-reflection $\rightarrow$ `bus.emit`.
* **Phase 2 - ACP-4: Autonomy Boundary Service.** `autonomy_boundary.py`: policy config (irreversible-tool list, cost threshold, sensitive-data flag per subgoal, persistence counter). `controlled_orchestrator.py` builds a `CControlledOrchestrator` that holds the same `mTPA`/`mTSA`/`mExecution`/`mMemory` instances `CAgenticOrchestrator` already exposes as public attributes, and re-implements the `run()` loop with one insertion: before calling `mExecution.run_step()`, call `boundary.evaluate(subgoal, step)` $\rightarrow$ allow / escalate / reject. No change to `layer_orchestrator.py`.
* **Phase 3 - ACP-5: Delegation Ledger + Tool Access Gate.**
  * `delegation_ledger.py`: `DelegationGrant(grantor, grantee, scope, expiry, parent_id)`, `DelegationLedger.issue`/`revoke`/`is_valid` with cascade-on-parent-revoke. First target: reinterpret `allowed_permissions` as the initial operator$\rightarrow$orchestrator grant.
  * `tool_access_gate.py`: privilege tier per tool (derive from existing `CToolRegistry` permission tags plus a manual severity map), cumulative impact budget, circuit-breaker flags. Implements $\text{Authorize}(a,t,r) = P(a) \land G(a,t) \land S(a) \land B(a,r) \land \neg C(a,r)$ literally as a boolean method.
  * `controlled_execution.py`: `CControlledExecutionEnvironment` wraps a `CExecutionEnvironment` instance - calls `gate.authorize(...)` first, only forwards to the real `run_step()` if authorized, else returns a synthetic "denied" record. No change to `layer_execution.py`.
* **Phase 4 - Close the loop.** Feed ACP-1's rejections and ACP-6's bias signal into `ToolAccessGate` as $C(a,r)$ triggers (repeated NAV rejections or sustained subgoal skew $\rightarrow$ trip breaker, tighten `allowed_permissions` via the ledger).
* **Phase 5 - Validation.** New `test_control_plane_redteam.py`: reuse the adversarial fixtures from the existing two red-team test files, add cases for expiry (grant used after window closes), cascade revoke (parent revoked $\rightarrow$ child denied), and breaker trip (repeated rejections $\rightarrow$ subsequent calls denied without re-triggering the underlying check). Run end-to-end against `nbAgenticConsole.ipynb` scenarios for a real (not synthetic) trace.
* **Phase 6 - Metrics readout.** `bus.py` rollups become the numbers for the paper's Operational Risk Metrics section (Section 5), computed from Phase 5's runs.


# TODO list
**Gap:** None of this is a human-in-the-loop escalation mechanism - there's no code path today where a control point pauses and waits for approval. If Section 2.3's "escalation ... human-in-the-loop or supervisory agents" bullet stays in, it needs to be scoped as proposed/specified rather than demonstrated, or built as new code in a separate session (can't do that under "don't modify the code" here anyway).

### C. Operational Risk Metrics - Measurability Audit

| **Metric** | **Measurable from Repo Today?** | **Source** | 
|---|---|---|
| **Tool blast radius** | Yes | `mTool_permissions` per tool | 
| **Delegation depth/duration** | Yes | Subgoal count + permission set per `run()`, via `_summarize_resource_use()` | 
| **Autonomy persistence** | Yes (baseline = 0; permissions don't carry across runs) | `run()` resets state each call | 
| **Escalation latency** | No | No escalation path exists | 
| **Cross-agent propagation potential** | No | Not in `agentic_framework` (single orchestrator, no inter-agent messaging) | 

- I'd rather put this table in the paper honestly than assert all five metrics are demonstrated - that's the exact pattern that drew fire on paper 1.

**One conflict to flag on "clean":** `sim_malware_quarantine` and `sim_flight_booking` - the only multi-component setups that might otherwise fill the escalation/cross-agent gaps - are already claimed as PoC evidence in the magazine paper's Section 5/Table 5. Reusing them here would recreate the exact overlap problem you asked me to avoid. So my recommendation is to leave those two metrics as specified-but-not-yet-demonstrated in this paper, rather than reach into the `sim_*` folders.

Phase 4 (`closed_loop.py`) is the part of Section 4.2's closing paragraph
that turns ACP-1/ACP-6 from "logged" into "enforceable": repeated ACP-1
rejections trip a tool-level breaker; an ACP-6 bias warning trips a
run-level breaker, both via `CToolAccessGate.trip_*_breaker()`.