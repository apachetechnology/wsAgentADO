# High Level Design

## A. Design Primitives for Secure Agentic Systems

| **Primitive** | **Methodology** | **Unit Test** | 
|---|---|---|
| **Bounded & scoped autonomy** | Run the same goal against **two separate `CAgenticOrchestrator` instances**: one built with `DEFAULT_ALLOWED_PERMISSIONS`, one with `ALL_PERMISSIONS` and diff the execution logs. | `allowed_permissions` is a **constructor-time** parameter (`CAgenticOrchestrator.__init__`), not a per-`run()` parameter - a single instance can't be re-parameterized per call. Baseline enforcement (`run_step()`'s permission diff) is genuinely fail-closed. `control_plane`'s `CToolAccessGate.authorize()` adds a *second*, per-tool-call gate on top (Phase 3), tested by `test_gate_denies_*` (5/5 passing). |
| **Revocable & time-bounded delegation** | Reframe around `control_plane.DelegationLedger`: issue a `DelegationGrant` with `ttl_seconds` or `max_invocations`, show it expires; issue a child grant, revoke the parent, show cascade. | `test_delegation_expires_by_ttl`, `test_delegation_expires_by_invocation_count`, `test_delegation_cascade_revoke`, `test_delegation_revoke_does_not_affect_siblings` - all passing, no framework dependency. `reset_short_term()` at the top of every `run()` and the `SHORT_TERM_MEMORY_TURNS`-bounded deque are both verified accurate as originally stated. |
| **Progressive privileges and impact-bounded access to tool-chains** | Classify all 9 registered tools into blast-radius tiers by permission set (e.g., `update_navs = WRITE + NETWORK = high`; `performance_review = READ + COMPUTE = low`) | All 9 tools' permission tags verified exactly: `update_navs={NETWORK,WRITE}`, `record_history={WRITE}`, `performance_review={READ,COMPUTE}`, `flag_risk={READ,COMPUTE}`, `portfolio_report={READ,COMPUTE}`, `fund_lookup={READ,NETWORK}`, `add_fund={WRITE}`, `rename_fund={WRITE}`, `plot_fund={READ,PLOT}`. Enforced at `run_step()`, genuinely fail-closed. Progressive tiering and the impact budget are enforced and tested one level up, at `control_plane.CToolAccessGate.authorize()`: `test_gate_denies_over_tier` shows a tool above the configured `max_tier` denied regardless of its permission tags; `test_gate_denies_over_impact_budget` shows the same tool/grant denied on a second call once cumulative cost exceeds the run's budget (both passing).  |

## B. Agentic Control Points and Runtime Assurance

| **Layer** | **ACP** | **Runtime Assurance Evidence** |
|---|---|---|
| **Perception** | `MAX_DAILY_MOVE` plausibility bound in `update_navs()`, rejecting implausible or malformed NAV values before they reach holdings. | `control_plane.test_acp1_adapter_publishes_rejections` / `_silent_when_nothing_rejected` (2/2, passing) exercise the ACP-1 signal adapter wrapping `update_navs()`'s `rejected`/`failures` return values. `Tests/test_perception_redteam.py` cannot currently run: no `conftest.py` defines the `tool_registry` fixture it requires, and its call `tool_registry.get("update_navs").func(...)` targets a `func` attribute that doesn't exist on `CTool` (the field is `mTool_func`). |
| **Reasoning** | Closed-vocabulary whitelist in `CTaskPlanningAgent.plan()`; `check_subgoal_bias()` flagging skewed subgoal distributions as a possible poisoning/injection signature. | `control_plane.test_acp2_adapter_detects_full_catalog_echo` / `_no_false_positive_on_normal_plan` and `test_acp6_adapter_publishes_bias_signal` / `_silent_when_no_bias` (4/4, passing) cover the ACP-2/ACP-6 signal-adapter plumbing. `Tests/test_reasoning_redteam.py` cannot currently run - no `conftest.py` defines the `tpa` fixture it requires - but no other defect was found in it. `check_subgoal_bias()`'s SQL query carries no filter on the `quarantined` column, so a quarantined episode still counts toward its skew tally. |
| **Action/Execution** | Permission-tag diff in `run_step()` denies execution before a tool is invoked (fail-closed). The missing-required-args branch sits outside `run_step()`'s own try/except and constructs `CExecutionRecord(..., error=...)` against a field named `mError`, raising `TypeError`; `CAgenticOrchestrator.run()`'s loop has no try/except around its `run_step()` call, so that exception ends the whole run rather than just the one subgoal. `control_plane.CControlledExecutionEnvironment` wraps `run_step()` in a try/except at the ACP-5 boundary and converts any such exception into an `"error"`-status record instead. | `control_plane.test_gate_denies_*` (5/5, passing) exercise the ACP-5 authorization predicate structurally; no test yet asserts the crash-catch path itself. |
| **Orchestration** | `_summarize_resource_use()` tallies execution-log status counts once per run, immediately after `reflect()`. `quarantine_episode()` sets a `quarantined` flag on an episode row and logs an audit entry. | `control_plane.test_acp6_adapter_publishes_bias_signal` (passing) covers the ACP-6 signal-adapter plumbing. No read path (`recall_similar()`, `check_subgoal_bias()`) filters on the `quarantined` column, so a quarantined episode continues to be recalled into planning context and counted in bias aggregation; no test yet exercises `check_subgoal_bias()`'s SQL threshold logic directly against seeded data. |

# Layout

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
| ACP-3 | Grounded-facts constraint, reject ungrounded currency | `signal_adapters.py` wraps `reflect()` |
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