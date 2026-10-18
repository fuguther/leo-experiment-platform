# READY-TO-APPLY: tie-break intervention (A/B), prepared 2026-10-19

Status: **prepared, NOT applied.** `kernel.py` and the `time_alignment` sort key are unmodified.
Prepared because the change spans four files plus a release and a launch, and the preparing
session ran out of context mid-way; half-applying it would either break the tree or waste a
launch slot on a cell that fails closed.

## Why this experiment

Located mechanism (see INDEPENDENT-REVIEW.md 12.27-12.34, 12.44):
`time_alignment.py:902-904` sorts candidates by
`(fallback, total_s, visible_order, direction)`.
Mirror-symmetric directions (E/W, N/S) have mathematically equal scores whose stored doubles
differ by **59 ULP** (relative 6.7e-15), so the third key (`visible_order`) is **unreachable**
and the winner is decided by floating-point accumulation order.
All 2 865 exact ties in the trunk run were mirror pairs, and 193/194 failing packets on
beijing->newyork passed through exactly one tie. Baseline NO_ROUTE rate on that OD: **3.7%**
(`sc-cor-r050-stale-s111`), truth arm 0.3%.

## Two cells, deliberately

| cell | intervention | question it answers |
|---|---|---|
| A | quantise the sort key only | does **determinism** (rather than float noise) change the rate? |
| B | quantise **+ destination-aware** break | does **relevance to the serving set** change the rate? |

A exists so the mechanism can be embarrassed: if plain determinism already fixes it, the
"destination-aware" story is wrong and must be rewritten.

## Patch

**1. `CODE/experiment_platform/load_calibration.py:101-104`** - allow the new key:

```python
"time_alignment": {"arm", "predictor", "common_rule",
                   "common_horizon_s", "history_limit",
                   "execution_mode", "min_observation_age_s",
                   "state_source", "enabled",
                   "tie_sig_digits"},          # <-- add
```

**2. `CODE/leo_sim/time_alignment.py:902-904`** - make the existing tie-break reachable.
Read the setting defensively so the default path is byte-identical to today:

```python
_dig = None
if snapshot is not None:
    _dig = (getattr(snapshot, "tie_sig_digits", None)
            or (snapshot.extra or {}).get("tie_sig_digits")
            if hasattr(snapshot, "extra") else None)
def _key(s):
    v = 0.0 if s.fallback else s.total_s
    if _dig:
        v = float(f"{v:.{int(_dig)}e}")      # collapse sub-significant differences
    return (s.fallback, v, visible_order[s.direction], s.direction)
scores.sort(key=_key)
```

**Plumbing (RESOLVED 2026-10-19 - the preparing session finished this reading).**
`tie_sig_digits` travels exactly the way `common_rule` does:

1. `time_alignment.py:207 class ObservationSnapshot` - add a field next to `common_rule`
   (declared at line 235): `tie_sig_digits: int | None = None`
2. `time_alignment.py:366` - the snapshot builder takes `common_rule: str = "median_eta"`.
   Add a sibling parameter `tie_sig_digits=None`, validate it the way `common_rule` is
   validated at 386-388, and assign it at line 430 alongside `common_rule=common_rule`.
3. `kernel.py _build_ta_snapshot` - it already passes `self.cfg_ta["common_rule"]`;
   pass `self.cfg_ta.get("tie_sig_digits")` on the same call.
4. Then the `_key` closure above can simply read `snapshot.tie_sig_digits`; drop the
   `getattr`/`extra` guessing.

With `tie_sig_digits` left unset the key is byte-identical to today, so every existing cell
keeps its identity and only the two new cells change.

**3. `CODE/leo_sim/tests/test_time_alignment.py`** - add:

- two candidates whose scores differ by <1e-12 sort by `visible_order` when `tie_sig_digits` is set;
- with `tie_sig_digits` unset the order is **unchanged from today** (regression guard).

**4. Cell B only** - the destination-aware break needs the destination's serving set inside the
ordering. That is a real code path, not a one-liner: locate where the kernel knows "which peers
serve `pkt.dst`" (the same knowledge `known_serving` uses) and add it as a key ahead of
`visible_order`. If that lookup is not cheaply available at this point, **report that and run
only cell A** - do not fake it.

## Launch

```bash
# new contract, NEW tag, NEW run-ids; same release chain as scw1c otherwise
"$T" --release-id <new-release-id> \
     --contract CODE/work/WP-T1-SCENE-CAL/scene_tiebreak_20261019.json \
     --cells "sc-tieA-r050-s111 sc-tieB-r050-s111" \
     --run-prefix sctb --tag <new-tag> --maxjob 2 --allow-busy
```

Both cells: corridor, rho=0.50, 0.35 s window, arm `stale`, 14 423 packets, about 25 min each.

## Identity rules (user section 6)

- **New release, new run-ids, new tag.** Never overwrite or rename the existing runs.
- Report A and B as an **intervention**, never pooled with the existing cells' statistics.
- Baseline for comparison stays `sc-cor-r050-stale-s111` (3.7%), which keeps its original identity.
- If either cell fails closed (`kernel_call_count = 0`), it does **not** consume budget; log it.

## Acceptance

Read the NO_ROUTE count on OD `G1:129:296 -> G1:130:106` via `fate_detail.py` /
`noroute_od.py` (both already in this directory). Compare with 194/5 236 = 3.7%.
A material drop supports causality; no change refutes it. **Either outcome is reportable.**
