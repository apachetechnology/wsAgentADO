# High Level Design

## Design Primitives for Secure Agentic Systems

| **Primitive** | **Methodology** | **Unit Test** | 
|---|---|---|
| **Bounded & scoped autonomy** | Run the same goal against **two separate `CAgenticOrchestrator` instances**: one built with `DEFAULT_ALLOWED_PERMISSIONS`, one with `ALL_PERMISSIONS` and diff the execution logs. | `allowed_permissions` is a **constructor-time** parameter (`CAgenticOrchestrator.__init__`), not a per-`run()` parameter - a single instance can't be re-parameterized per call. Baseline enforcement (`run_step()`'s permission diff) is genuinely fail-closed. `control_plane`'s `CToolAccessGate.authorize()` adds a *second*, per-tool-call gate on top (STEP 3), tested by `test_gate_denies_*` (5/5 passing). |
| **Revocable & time-bounded delegation** | Reframe around `control_plane.DelegationLedger`: issue a `DelegationGrant` with `ttl_seconds` or `max_invocations`, show it expires; issue a child grant, revoke the parent, show cascade. | `test_delegation_expires_by_ttl`, `test_delegation_expires_by_invocation_count`, `test_delegation_cascade_revoke`, `test_delegation_revoke_does_not_affect_siblings` - all passing, no framework dependency. `reset_short_term()` at the top of every `run()` and the `SHORT_TERM_MEMORY_TURNS`-bounded deque are both verified accurate as originally stated. |
| **Progressive privileges and impact-bounded access to tool-chains** | Classify all 9 registered tools into blast-radius tiers by permission set (e.g., `update_navs = WRITE + NETWORK = high`; `performance_review = READ + COMPUTE = low`) | All 9 tools' permission tags verified exactly: `update_navs={NETWORK,WRITE}`, `record_history={WRITE}`, `performance_review={READ,COMPUTE}`, `flag_risk={READ,COMPUTE}`, `portfolio_report={READ,COMPUTE}`, `fund_lookup={READ,NETWORK}`, `add_fund={WRITE}`, `rename_fund={WRITE}`, `plot_fund={READ,PLOT}`. Enforced at `run_step()`, genuinely fail-closed. Progressive tiering and the impact budget are enforced and tested one level up, at `control_plane.CToolAccessGate.authorize()`: `test_gate_denies_over_tier` shows a tool above the configured `max_tier` denied regardless of its permission tags; `test_gate_denies_over_impact_budget` shows the same tool/grant denied on a second call once cumulative cost exceeds the run's budget (both passing).  |

## ACP -> module map

| **ACP** | **Mechanism** | **Implementation details** |
|---|---|---|
| ACP-1 | Plausibility-bound NAV validation | `signal_adapters.py` wraps `update_navs`'s `mTool_func`; publishes an `acp1_reject` bus event whenever the tool's own `rejected`/`failures` lists are non-empty, without altering the tool's return value |
| ACP-2 | SUBGOAL_CATALOG allow-list, rejects full-catalog echo | `signal_adapters.py` wraps `CTaskPlanningAgent.plan()`; tees stdout to detect the existing "entire subgoal catalog" console marker, then publishes `acp2_plan_decision` with the echo flag and whether all returned subgoals are in-catalog |
| ACP-3 | Grounded-facts constraint, reject ungrounded currency | `signal_adapters.py` wraps `CTaskPlanningAgent.reflect()`; publishes `acp3_reflection` flagging whether the deterministic fallback summary was used and whether a `$`/USD/dollars pattern still slipped through |
| ACP-4 | Autonomy boundary service | `autonomy_boundary.py` (`CAutonomyBoundaryService`) evaluates each proposed subgoal against irreversibility, cost, and persistence-limit boundaries; `controlled_orchestrator.py` (`CControlledOrchestrator`) calls it before every `run_step()` and routes PROCEED/ESCALATE/DEFER/REJECT accordingly |
| ACP-5 | Permission gate + delegation ledger + tool access gate | `delegation_ledger.py` (`DelegationLedger`/`DelegationGrant`) issues and revokes time-bounded grants; `tool_access_gate.py` (`CToolAccessGate`) evaluates the full `Authorize(a,t,r) = P^G^S^B^¬C` predicate; `controlled_execution.py` (`CControlledExecutionEnvironment`) calls the gate before forwarding to the real `run_step()` |
| ACP-6 | `check_subgoal_bias()` diagnostic signal | `signal_adapters.py` wraps `CAgentMemory.record_episode()`; calls `check_subgoal_bias()` right after each recorded episode and publishes `acp6_bias_signal` when a skew warning is returned |

## Agentic Control Points and Runtime Assurance

| **Layer** | **ACP** | **Runtime Assurance Evidence** |
|---|---|---|
| **Perception** | `MAX_DAILY_MOVE` plausibility bound in `update_navs()`, rejecting implausible or malformed NAV values before they reach holdings. | `control_plane.test_acp1_adapter_publishes_rejections` / `_silent_when_nothing_rejected` (2/2, passing) exercise the ACP-1 signal adapter wrapping `update_navs()`'s `rejected`/`failures` return values. |
| **Reasoning** | Closed-vocabulary whitelist in `CTaskPlanningAgent.plan()`; `check_subgoal_bias()` flagging skewed subgoal distributions as a possible poisoning/injection signature. | `control_plane.test_acp2_adapter_detects_full_catalog_echo` / `_no_false_positive_on_normal_plan` and `test_acp6_adapter_publishes_bias_signal` / `_silent_when_no_bias` (4/4, passing) cover the ACP-2/ACP-6 signal-adapter plumbing.|
| **Action/Execution** | Permission-tag diff in `run_step()` denies execution before a tool is invoked (fail-closed).  `control_plane.CControlledExecutionEnvironment` wraps `run_step()` in a try/except at the ACP-5 boundary and converts any such exception into an `"error"`-status record instead. | `control_plane.test_gate_denies_*` (5/5, passing) exercise the ACP-5 authorization predicate structurally; no test yet asserts the crash-catch path itself. |
| **Orchestration** | `_summarize_resource_use()` tallies execution-log status counts once per run, immediately after `reflect()`. `quarantine_episode()` sets a `quarantined` flag on an episode row and logs an audit entry. | `control_plane.test_acp6_adapter_publishes_bias_signal` (passing) covers the ACP-6 signal-adapter plumbing. Both recall_similar() and check_subgoal_bias() now filter on WHERE quarantined = 0, so a quarantined episode is excluded from both planning recall and bias aggregation. |

# Layout

```
console_control_framework.py   full stack implementaion, all six ACPs together

agentic_framework_controlled/
  bus.py                     Observability & Metrics Bus
  signal_adapters.py         ACP-1 / ACP-2 / ACP-3 / ACP-6 signal adapters
  autonomy_boundary.py       ACP-4 policy (irreversibility, cost, persistence)
  controlled_orchestrator.py CControlledOrchestrator (ACP-4 wired into the run loop)
  delegation_ledger.py       ACP-5 DelegationGrant / DelegationLedger
  tool_access_gate.py        ACP-5 CToolAccessGate, Authorize(a,t,r) literally
  controlled_execution.py    CControlledExecutionEnvironment (ACP-5 wired into run_step)
  closed_loop.py             ACP-1/ACP-6 signals -> ACP-5 circuit breakers
  metrics_readout.py         renders bus.rollups() for Section 5
  __init__.py

Tests/
  test_controlled_framework.py   Expiry / cascade-revoke / breaker-trip red-team cases
```

## FW Evaluation

In brief `console_control_framework.py`.

1. **Build baseline orchestrator** – calls `build_orchestrator()` from `console_baseline_framework.py`, with permission set decided by `allow_writes`.
2. **STEP 0 – Bus** – creates `CObservabilityMetricsBus()`, the shared event log every ACP will publish to.
3. **STEP 1 – ACP-1/2/3/6** – `CSignalAdapters(bus).attach(orchestrator)` wraps `update_navs`, `plan()`, `reflect()` and `record_episode()` in place, purely observational.
4. **STEP 3 – ACP-5** – `DelegationLedger.bootstrap_from_allowed_permissions()` turns the constructor-time `allowed_permissions` set into one degenerate grant. `CToolAccessGate` is built on that ledger, and `orchestrator.mExecution` is replaced by `CControlledExecutionEnvironment`, so every `run_step()` now goes through `Authorise(a,t,r) = P^G^S^B^¬C` before the real execution.
5. **STEP 4 – Closed loop** – `CClosedLoopPolicy(bus, gate)` subscribes to the bus, so repeated ACP-1 rejections or an ACP-6 bias signal trip a circuit breaker on the gate built in step 4.
6. **STEP 2 – ACP-4** – the whole thing is wrapped in `CControlledOrchestrator`, which re-implements `run()` with the autonomy boundary check inserted before each `run_step()` call, and adds an escalation queue.

## Testing

| **File** | **What it does** |
|---|---|
|**`Tests/test_controlled_framework.py`**|Verified: 22/22 passing, standalone (no Ollama, no live DB, no network).|
| **`Tests/conftest.py`** | Supplies the two pytest fixtures the other two test files need but that didn't exist anywhere in the repo before (`tool_registry`, `tpa`). Without this file, pytest can't even collect those tests - it errors immediately with "fixture not found." It builds a *real* `CToolRegistry`/`CTaskPlanningAgent` against temp-file databases, so the tests exercise your actual `update_navs()`/`plan()` logic rather than a mock of it, without ever touching your real `_DB/` files or the network. |
| **`Tests/test_perception.py`** | Tests that `update_navs()` rejects spoofed/implausible NAV feeds (a crash to near-zero, a 100,000x spike, a null value) - the ACP-1 mechanism. |
| **`Tests/test_reasoning.py`** | Tests that `plan()` strips out attacker-controlled subgoals (e.g. `"delete_everything"`) that aren't in `SUBGOAL_CATALOG`, even when the (simulated) LLM response tries to inject them - the ACP-2 whitelist mechanism. |