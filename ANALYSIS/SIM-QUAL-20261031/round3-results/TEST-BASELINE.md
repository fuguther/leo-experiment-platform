# Test baseline (2026-11-01, local macOS, Python 3.14)

## leo_sim
- `python3 -m pytest CODE/leo_sim/tests -q` → **1258 passed / 5 failed / 2 skipped**
  (1058 after P4/P5 introduced). The 5 failures are the same set that fails at the
  Round-2 branch base `a90c1f9` (4 x receipt-frozen-fixture, 1 x trace population-gravity
  byte regression); they are pre-existing local-environment/version failures, not Round-3.

## experiment_platform
- Before the compatibility fix: 23 failures. Cause: P4/P5 added the optional
  `decision_ref=` keyword to `Kernel._time_aligned_order`; `benchmark_decision` monkeypatches
  that method with a spy pinned to the old signature.
- Fix `CODE/experiment_platform/benchmark_decision.py` (spy forwards `**kwargs`) → 17/17
  benchmark_decision tests pass; whole suite **824 passed / 4 failed / 6 skipped**.
- The remaining 4 failures were re-run on a clean `git archive c63c071` baseline (no Round-3
  code): **the same 4 fail there**, so they are pre-existing, not Round-3:
  - `test_t1_suite.py::test_root_authorized_persistent_contract_allows_only_exact_four_call_cell`
  - `test_t1_suite.py::test_persistent_cost_probe_compile_preserves_native_profile_parameters`
  - `test_t1_suite.py::test_the_dev_tier_runs_with_behaviour_predicates`
  - `test_graph_publication.py::test_network_capture_preserves_complete_history_and_resource_evidence`
  They are frozen-hash/profile-compile expectations and a network-replay DEGRADED fixture;
  none interacts with the six state policies or the Round-3 matrix.
