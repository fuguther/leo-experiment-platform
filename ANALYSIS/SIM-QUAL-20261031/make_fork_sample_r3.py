#!/usr/bin/env python3
"""Round-3 expansion sampler for same-continuation forced-action forks.

Extends ``make_fork_sample.py`` (Round-2, 5 strata, n=12 on one cell) to the
seven strata the round-3 protocol asks for, with per-cell quotas that sum to a
pre-registered 36-fork batch:

    large_gamma 6 · small_margin 5 · large_proxy_R 5 · long_persistence 5 ·
    high_z 5 · random_control 5 · low_gamma 5            = 36

12 of the 36 are the already-completed Round-2 forks on ``m2-c2-greedy-s7``;
the remaining 24 come from ``m2-c2-greedy-s8`` / ``-s9`` (12 each).  Quotas are
passed per cell, so the merge step can prove the totals.

Definitions (frozen; reused, not re-invented)
---------------------------------------------
* flip: ``chosen != fresh`` where fresh is the *truth-substituted* same-decision
  argmin, computed exactly as ``sq_policy_analyze._fresh_action`` (geometry is
  the constructive negative control; hysteresis re-applies last_direction+eta).
* Gamma = ``|slope_a - slope_b| * tau / m_used``: ``sq_policy_analyze.local_slope``
  on the target probe's ``truth.work_ahead_bits / service_rate_bps`` series
  (same estimator that produced RESULTS-ROUND2).
* margin_s = used-state top1 - top2 of ``j_used`` (same as Round-2 ``margin_s``).
* proxy_regret_s = ``J_truth(chosen) - min J_truth`` (truth-substituted, NOT the
  causal regret; the causal quantity only comes out of a real fork).
* long_persistence = same-``(sat,dst)`` stream, consecutive flips whose *chosen*
  direction is identical, run length >= 2.  ``dst`` is joined from
  ``trace.jsonl.gz`` by pid (the decision projection drops ``dst``).
* high_z: eps_r = mu_r - served_bits_r/window per 0.5 s bin
  (``sq_persistence_analyze.epsilon_table``, PROTOCOL-ROUND2 F4 floor), and
  ``z_bits = bits / eps_r`` evaluated on the bin containing the decision for the
  *chosen* resource -- the task's ``z = |bits|/eps`` approximation.  The
  frozen Round-2 ``z_delta = |delta_r|/eps_r`` (same delta accounting as
  ``sq_persistence_analyze.analyze_cell``) is recorded next to it.
* low_gamma: Gamma <= p25 of eligible flips -- the negative control (small
  predicted drift should not move the action).

Eligibility (identical to Round-2, plus the evaluation window)
-------------------------------------------------------------
kind == forward, >= 2 candidates, chosen != fresh, fresh is inside the recorded
candidate set (so the forced action is legal at the branch point), chosen has a
truth cost, and the decision lies inside the frozen evaluation window
4.0 <= t < 14.0 s (PROTOCOL section 2; required for eps/z anyway).

Determinism: every pool is sorted by (metric, decision_id); the random control
uses a fixed seed; no wall clock, no set iteration.

Usage
-----
    # one cell -> one 12-fork sample
    python3 make_fork_sample_r3.py cell <cell-dir> \
        --quota large_gamma=1,small_margin=1,large_proxy_R=1,long_persistence=2,high_z=3,random_control=2,low_gamma=2 \
        --exclude fork-samples/c2-greedy-s7.json \
        --out fork-samples/r3-m2-c2-greedy-s8.json

    # merge the completed s7 batch + the two new samples into the 36-fork batch
    python3 make_fork_sample_r3.py merge \
        --s7 fork-samples/c2-greedy-s7.json \
        --new fork-samples/r3-m2-c2-greedy-s8.json \
        --new fork-samples/r3-m2-c2-greedy-s9.json \
        --out fork-samples/round3-expansion.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import sq_policy_analyze as A                     # noqa: E402
import sq_persistence_analyze as P                # noqa: E402
from make_fork_sample import _fresh, _gamma_and_margin   # noqa: E402

STRATA = ("large_gamma", "small_margin", "large_proxy_R", "long_persistence",
          "high_z", "random_control", "low_gamma")
DEFAULT_SEED = 20261101
SCHEMA_CELL = "sq-fork-sample-r3/v1"
SCHEMA_MERGED = "sq-fork-sample-round3-merged/v1"
#: Round-2 sampled `sustained` with the same same-direction-run rule; the merged
#: batch reports it under the round-3 name.
STRATUM_ALIASES = {"sustained": "long_persistence"}
SCHEDULED_QUOTAS = {"large_gamma": 6, "small_margin": 5, "large_proxy_R": 5,
                    "long_persistence": 5, "high_z": 5, "random_control": 5,
                    "low_gamma": 5}
FORK_PREFIX = "fk3-"
ROUND2_PREFIX = "fk-c-"
EVAL_START_S = 4.0
EVAL_END_S = 14.0


# ------------------------------------------------------------------ helpers
def percentile(values, q):
    """Nearest-rank percentile (same estimator family as P.qstats)."""
    clean = sorted(float(v) for v in values
                   if v is not None and math.isfinite(float(v)))
    if not clean:
        return None
    index = int(round(q * (len(clean) - 1)))
    index = min(len(clean) - 1, max(0, index))
    return clean[index]


def _hash_order(seed, decision_id):
    digest = hashlib.sha256(f"{seed}:{decision_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _delta_bps(rows):
    """delta_r per (bin, link) in bit/s, same accounting as analyze_cell."""
    delta_bits = [defaultdict(float) for _ in range(P.N_BINS)]
    unattributed = 0
    for row in rows:
        if not row["flip"]:
            continue
        if not row["chosen_resource"] or not row["fresh_resource"]:
            unattributed += 1
            continue
        delta_bits[row["bin"]][row["chosen_resource"]] += float(row["bits"] or 0.0)
        delta_bits[row["bin"]][row["fresh_resource"]] -= float(row["bits"] or 0.0)
    out = []
    for index in range(P.N_BINS):
        width = P.bin_bounds(index)[1] - P.bin_bounds(index)[0]
        out.append({link: value / width
                    for link, value in delta_bits[index].items()})
    return out, unattributed


def _flip_runs(rows):
    """(sat,dst)-stream runs of consecutive flips with the same chosen direction.

    Returns decision_id -> {"run_len", "run_id", "run_first_t"}.  dst comes from
    the trace join; rows without dst are grouped under the literal None key and
    reported (never silently dropped from the count).
    """
    streams = defaultdict(list)
    for row in rows:
        streams[(row["sat"], row["dst"])].append(row)
    info = {}
    run_counter = 0
    for key in sorted(streams, key=lambda k: (str(k[0]), str(k[1]))):
        seq = sorted(streams[key], key=lambda r: (r["t"], r["decision_id"]))
        current = []
        current_dir = None

        def flush():
            nonlocal run_counter
            if not current:
                return
            run_counter += 1
            first_t = seq_by_id[current[0]]["t"]
            for did in current:
                info[did] = {"run_len": len(current), "run_id": run_counter,
                             "run_first_t": first_t}

        seq_by_id = {row["decision_id"]: row for row in seq}
        for row in seq:
            is_flip = row["flip"]
            if is_flip and row["chosen"] == current_dir:
                current.append(row["decision_id"])
            elif is_flip:
                flush()
                current = [row["decision_id"]]
                current_dir = row["chosen"]
            else:
                flush()
                current = []
                current_dir = None
        flush()
    return info


# --------------------------------------------------------------- collect
def collect(cell: Path) -> dict:
    """One read-only pass over the cell; returns the eligible-plus-context set."""
    config = P.load_config(cell)
    ta = config.get("time_alignment") or {}
    policy = ta.get("state_policy")
    mu_bps = float(config["links"]["isl_rate_mbps"]) * 1e6
    # PROTOCOL-ROUND2 section 4: z is only defined on the *valid* subset, i.e.
    # eps_r >= max(1 bps, 1% of mu).  A saturated link (eps below the floor)
    # would otherwise produce a meaningless astronomically large z and take
    # over the high_z stratum; those decisions are excluded and counted.
    eps_floor_bps = max(P.EPS_FLOOR_BPS, P.Z_EPS_FLOOR_FRAC * mu_bps)
    result = P.load_result(cell)
    windows = P.service_windows_by_link(result)
    eps, _served = P.epsilon_table(windows, mu_bps)
    bits_by_pid, dst_by_pid = P.load_packet_bits(cell)
    probe, probe_meta = P.load_probe(cell)
    _totals, rows = P.collect_decisions(cell, probe, bits_by_pid, dst_by_pid,
                                        policy, lambda _m: None)
    delta_bps, unattributed = _delta_bps(rows)
    runs = _flip_runs(rows)

    analysis_rows = {row["decision_id"]: row for row in A.load_decisions(cell)}
    series = A.load_truth_series(cell)
    probe_index = A.truth_probe_index(cell)

    eligible = []
    skipped = defaultdict(int)
    counts = defaultdict(int)
    for row in rows:
        did = row["decision_id"]
        base = analysis_rows.get(did)
        if base is None:
            skipped["not_scored_multi_candidate"] += 1
            continue
        fresh = row["fresh"]
        if fresh is None or not row["flip"]:
            skipped["not_a_flip"] += 1
            continue
        if fresh not in base["directions"]:
            skipped["fresh_outside_candidates"] += 1
            continue
        if row["chosen"] not in base["j_truth"]:
            skipped["chosen_without_truth"] += 1
            continue
        gamma, margin = _gamma_and_margin(base, series, probe_index, policy)
        jt = base["j_truth"]
        proxy = float(jt[row["chosen"]]) - min(float(v) for v in jt.values())
        bits = float(row["bits"] or 0.0)
        link = row["chosen_resource"]
        eps_link = None
        if link is not None:
            eps_link = float(eps[row["bin"]].get(link, mu_bps))
        z_bits = z_delta = None
        delta = None
        z_basis = "no_resource"
        if eps_link is not None:
            z_basis = "below_eps_floor"
            if eps_link >= eps_floor_bps:
                z_basis = "valid"
                if bits:
                    z_bits = bits / eps_link
                delta = float(delta_bps[row["bin"]].get(link, 0.0))
                z_delta = abs(delta) / eps_link
        counts["z_" + z_basis] = counts.get("z_" + z_basis, 0) + 1
        run = runs.get(did) or {}
        eligible.append({
            "decision_id": did,
            "pid": row["pid"],
            "sat": row["sat"],
            "dst": row["dst"],
            "hop_index": row["hop_index"],
            "t": row["t"],
            "bin": row["bin"],
            "chosen": row["chosen"],
            "fresh": fresh,
            "directions": list(base["directions"]),
            "chosen_resource": link,
            "fresh_resource": row["fresh_resource"],
            "bits": bits,
            "gamma": gamma,
            "margin_s": margin,
            "proxy_regret_s": proxy,
            "z_bits": z_bits,
            "z_delta": z_delta,
            "delta_bps": delta,
            "eps_bps": eps_link,
            "z_basis": z_basis,
            "run_len": run.get("run_len"),
            "run_id": run.get("run_id"),
            "run_first_t": run.get("run_first_t"),
        })

    gammas = [r["gamma"] for r in eligible if r["gamma"] is not None]
    margins = [r["margin_s"] for r in eligible if r["margin_s"] is not None]
    proxies = [r["proxy_regret_s"] for r in eligible if r["proxy_regret_s"] is not None]
    zs = [r["z_bits"] for r in eligible if r["z_bits"] is not None]
    thresholds = {
        "gamma_p25": percentile(gammas, 0.25),
        "gamma_p75": percentile(gammas, 0.75),
        "margin_p10": percentile(margins, 0.10),
        "proxy_regret_p90": percentile(proxies, 0.90),
        "z_bits_p90": percentile(zs, 0.90),
        "n_gamma": len(gammas), "n_margin": len(margins),
        "n_proxy_regret": len(proxies), "n_z_bits": len(zs),
        "eps_floor_bps": eps_floor_bps,
        "eps_floor_rule": "max(1 bps, 1% of mu) -- PROTOCOL-ROUND2 section 4",
        "z_basis_counts": dict(sorted(counts.items())),
    }
    config_doc = json.loads((cell / "arms" / "now" / "config.json").read_text())
    trace_manifest = json.loads((cell / "trace-manifest.json").read_text())
    provenance = {
        "cell": cell.name,
        "policy": policy,
        "config_sha256": config_doc.get("sha256"),
        "trace_source_sha256": trace_manifest.get("source_trace_sha256"),
        "trace_rows": trace_manifest.get("rows"),
        "mu_bps": mu_bps,
        "eval_window_s": [EVAL_START_S, EVAL_END_S],
        "probe_rows_used": probe_meta["rows_used"],
        "probe_rows_skipped": probe_meta["rows_skipped"],
        "n_eval_decisions": len(rows),
        "n_flip_unattributed": unattributed,
        "n_eligible": len(eligible),
        "skipped": dict(sorted(skipped.items())),
        "thresholds": thresholds,
    }
    return {"provenance": provenance, "eligible": eligible,
            "thresholds": thresholds}


# --------------------------------------------------------------- selection
def _ranked(pool, key, descending=True, allow_none=False):
    clean = [row for row in pool
             if row.get(key) is not None or allow_none]
    if descending:
        return sorted(clean, key=lambda r: (-(r[key] if r[key] is not None else -math.inf),
                                            r["decision_id"]))
    return sorted(clean, key=lambda r: (r[key] if r[key] is not None else math.inf,
                                        r["decision_id"]))


def _pool_high_z(eligible, threshold):
    pool = [row for row in eligible
            if row["z_bits"] is not None and threshold is not None
            and row["z_bits"] >= threshold]
    return sorted(pool, key=lambda r: (-r["z_bits"],
                                       -(r["z_delta"] if r["z_delta"] is not None else 0.0),
                                       r["decision_id"]))


def select(cell_doc, quotas, seed=DEFAULT_SEED, excluded_ids=()):
    """Fill the stratum quotas deterministically; returns picks + extras."""
    eligible = [row for row in cell_doc["eligible"]
                if row["decision_id"] not in excluded_ids]
    thresholds = cell_doc["thresholds"]
    with_gamma = [row for row in eligible if row["gamma"] is not None]
    pools = {
        "large_gamma": _ranked([row for row in eligible
                                if row["gamma"] is not None
                                and thresholds["gamma_p75"] is not None
                                and row["gamma"] >= thresholds["gamma_p75"]],
                               "gamma"),
        "small_margin": _ranked([row for row in eligible
                                 if row["margin_s"] is not None
                                 and thresholds["margin_p10"] is not None
                                 and row["margin_s"] <= thresholds["margin_p10"]],
                                "margin_s", descending=False),
        "large_proxy_R": _ranked([row for row in eligible
                                  if row["proxy_regret_s"] is not None
                                  and thresholds["proxy_regret_p90"] is not None
                                  and row["proxy_regret_s"] >= thresholds["proxy_regret_p90"]],
                                 "proxy_regret_s"),
        "long_persistence": sorted(
            [row for row in eligible
             if (row["run_len"] or 0) >= 2],
            key=lambda r: (-r["run_len"], r["t"], r["decision_id"])),
        "high_z": _pool_high_z(eligible, thresholds["z_bits_p90"]),
        "low_gamma": _ranked([row for row in with_gamma
                              if thresholds["gamma_p25"] is not None
                              and row["gamma"] <= thresholds["gamma_p25"]],
                             "gamma", descending=False),
        "random_control": sorted(
            eligible, key=lambda r: (_hash_order(seed, r["decision_id"]),
                                     r["decision_id"])),
    }
    caps = {
        # a "high z" batch must not be five copies of one saturated link,
        # and a persistence batch must not repeat one run
        "high_z": {"sat": 1, "resource": 1},
        "long_persistence": {"run": 1},
        "low_gamma": {"run": 1},
    }
    picked = []
    seen = set()
    filled = {}
    order = [name for name in STRATA if name != "random_control"]

    def _ok(row, cap, name):
        if cap is None:
            return True
        counts = [p for p in picked if p["stratum"] == name]
        if "sat" in cap and sum(1 for p in counts if p["sat"] == row["sat"]) >= cap["sat"]:
            return False
        if "resource" in cap and sum(1 for p in counts
                                     if p["chosen_resource"] == row["chosen_resource"]) >= cap["resource"]:
            return False
        if "run" in cap and sum(1 for p in counts if p["run_id"] == row["run_id"]) >= cap["run"]:
            return False
        return True

    for name in order:
        wanted = int(quotas.get(name, 0))
        taken = 0
        for row in pools[name]:
            if taken >= wanted:
                break
            if row["decision_id"] in seen:
                continue
            if not _ok(row, caps.get(name), name):
                continue
            entry = dict(row)
            entry["stratum"] = name
            entry["rank_in_stratum"] = taken + 1
            entry["stratum_threshold"] = _threshold_for(name, thresholds)
            picked.append(entry)
            seen.add(row["decision_id"])
            taken += 1
        if taken < wanted and caps.get(name):
            for row in pools[name]:
                if taken >= wanted:
                    break
                if row["decision_id"] in seen:
                    continue
                entry = dict(row)
                entry["stratum"] = name
                entry["rank_in_stratum"] = taken + 1
                entry["stratum_threshold"] = _threshold_for(name, thresholds)
                entry["cap_relaxed"] = True
                picked.append(entry)
                seen.add(row["decision_id"])
                taken += 1
        filled[name] = taken

    wanted_total = int(sum(int(v) for v in quotas.values()))
    wanted_random = int(quotas.get("random_control", 0))
    taken_random = 0
    for row in pools["random_control"]:
        if taken_random >= wanted_random:
            break
        if row["decision_id"] in seen:
            continue
        entry = dict(row)
        entry["stratum"] = "random_control"
        entry["rank_in_stratum"] = taken_random + 1
        entry["stratum_threshold"] = None
        picked.append(entry)
        seen.add(row["decision_id"])
        taken_random += 1
    filled["random_control"] = taken_random

    shortfall = wanted_total - len(picked)
    return {
        "picked": picked,
        "quota_requested": dict(quotas),
        "quota_filled": filled,
        "n": len(picked),
        "n_requested": wanted_total,
        "shortfall": shortfall,
        "pool_sizes": {name: len(pool) for name, pool in pools.items()},
        "caps": {"high_z": {"max_per_sat": 1, "max_per_resource": 1},
                 "long_persistence": {"max_per_run": 1},
                 "low_gamma": {"max_per_run": 1}},
        "n_cap_relaxed": sum(1 for row in picked if row.get("cap_relaxed")),
    }


def _threshold_for(name, thresholds):
    key = {"large_gamma": "gamma_p75", "small_margin": "margin_p10",
           "large_proxy_R": "proxy_regret_p90", "high_z": "z_bits_p90",
           "low_gamma": "gamma_p25", "long_persistence": "run_len_ge2",
           }.get(name)
    if key == "run_len_ge2":
        return {"rule": "run_len >= 2"}
    return {"rule": key, "value": thresholds.get(key)} if key else None


# ------------------------------------------------------------------- merge
def _cell_tag(cell_name):
    for suffix in ("s7", "s8", "s9"):
        if cell_name.endswith(suffix):
            return suffix
    return cell_name


def _completed_fork(evidence_root, run_id, decision_id):
    """Read one already-published cf-<decision>.json, if the evidence is local."""
    if evidence_root is None:
        return None
    path = Path(evidence_root) / run_id / f"cf-{decision_id}.json"
    if not path.is_file():
        return None
    doc = json.loads(path.read_text())
    target = (doc.get("target") or {}).get("baseline") or {}
    tagged = doc.get("tagged_packet") or {}
    base_del = (tagged.get("baseline") or {}).get("delivery") or {}
    force_del = (tagged.get("counterfactual") or {}).get("delivery") or {}
    delta = None
    if base_del.get("delivered_at") is not None and force_del.get("delivered_at") is not None:
        delta = float(force_del["delivered_at"]) - float(base_del["delivered_at"])
    return {
        "evidence_path": str(path),
        "candidates": target.get("candidates"),
        "baseline_action": (doc.get("verification") or {}).get("baseline_action"),
        "forced_action": (doc.get("verification") or {}).get("forced_action"),
        "action_changed": (doc.get("verification") or {}).get("action_changed"),
        "legal_at_branch_point": (doc.get("verification") or {}).get("legal_at_branch_point"),
        "branch_states_identical": (doc.get("verification") or {}).get("branch_states_identical"),
        "branch_fingerprint": (doc.get("verification") or {}).get("baseline_branch_fingerprint"),
        "tagged_delta_s": delta,
        "cohort_n_common_delivered": (doc.get("cohort") or {}).get("n_common_delivered"),
        "cohort_n_fate_changes": len((doc.get("cohort") or {}).get("fate_changes") or {}),
    }


def merge(s7_path, new_paths, out_path, run_prefix=FORK_PREFIX,
          release_id=None, evidence_root=None):
    s7 = json.loads(Path(s7_path).read_text())
    entries = []
    completed_missing = []
    for row in s7["decisions"]:
        original = row["stratum"]
        mapped = STRATUM_ALIASES.get(original, original)
        did = row["decision_id"]
        entries.append({
            "cell": s7["cell"],
            "config": "EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s7.json",
            "stratum": mapped,
            "original_stratum": original,
            "decision_id": did,
            "pid": row.get("pid"), "sat": row.get("sat"), "t": row.get("t"),
            "chosen": row.get("chosen"), "fresh": row.get("fresh"),
            "forced": row.get("fresh"),
            "candidates": row.get("directions"),
            "gamma": row.get("gamma"), "margin_s": row.get("margin_s"),
            "proxy_regret_s": row.get("proxy_regret_s"),
            "status": "completed",
            "source": "round2_completed",
            "evidence_run_id": f"{ROUND2_PREFIX}{did}-20261031-01",
            "run_id": f"{ROUND2_PREFIX}{did}-20261031-01",
        })
        completed = _completed_fork(evidence_root,
                                    f"{ROUND2_PREFIX}{did}-20261031-01", did)
        if completed is None:
            completed_missing.append(did)
        else:
            entry = entries[-1]
            entry["candidates"] = completed["candidates"]
            entry["completed_verification"] = {
                key: completed[key] for key in
                ("baseline_action", "forced_action", "action_changed",
                 "legal_at_branch_point", "branch_states_identical",
                 "branch_fingerprint")
            }
            entry["completed_verification"]["crosscheck_baseline_action_matches_sample"] = (
                completed["baseline_action"] == entry["chosen"])
            entry["completed_verification"]["crosscheck_forced_matches_sample"] = (
                completed["forced_action"] == entry["forced"])
            entry["completed_verification"]["legal_set_contains_forced"] = (
                completed["candidates"] is not None
                and completed["forced_action"] in completed["candidates"])
            entry["tagged_delta_s"] = completed["tagged_delta_s"]
            entry["cohort_n_common_delivered"] = completed["cohort_n_common_delivered"]
            entry["cohort_n_fate_changes"] = completed["cohort_n_fate_changes"]
    cells = {}
    for path in new_paths:
        doc = json.loads(Path(path).read_text())
        cell_name = doc["cell"]
        tag = _cell_tag(cell_name)
        config_rel = f"EXPERIMENTS/contracts/fork-configs/{cell_name}.json"
        cells[cell_name] = {
            "sample": str(path),
            "config": config_rel,
            "config_sha256": doc["provenance"]["config_sha256"],
            "trace_source_sha256": doc["provenance"]["trace_source_sha256"],
            "trace_rows": doc["provenance"]["trace_rows"],
            "policy": doc["provenance"]["policy"],
            "n": doc["n"],
            "n_cap_relaxed": doc.get("n_cap_relaxed"),
            "quota_requested": doc["quota_requested"],
            "quota_filled": doc["quota_filled"],
            "thresholds": doc["provenance"]["thresholds"],
        }
        for row in doc["decisions"]:
            did = row["decision_id"]
            run_id = f"{run_prefix}{tag}-{did}-20261031-01"
            entries.append({
                "cell": cell_name,
                "config": config_rel,
                "stratum": row["stratum"],
                "original_stratum": row["stratum"],
                "decision_id": did,
                "pid": row.get("pid"), "sat": row.get("sat"), "t": row.get("t"),
                "chosen": row.get("chosen"), "fresh": row.get("fresh"),
                "forced": row.get("fresh"),
                "candidates": row.get("directions"),
                "gamma": row.get("gamma"), "margin_s": row.get("margin_s"),
                "proxy_regret_s": row.get("proxy_regret_s"),
                "z_bits": row.get("z_bits"), "z_delta": row.get("z_delta"),
                "eps_bps": row.get("eps_bps"), "z_basis": row.get("z_basis"),
                "bits": row.get("bits"), "hop_index": row.get("hop_index"),
                "chosen_resource": row.get("chosen_resource"),
                "run_len": row.get("run_len"),
                "status": "pending",
                "source": "round3_new",
                "run_id": run_id,
                "evidence_run_id": None,
            })
    totals = {name: sum(1 for e in entries if e["stratum"] == name)
              for name in STRATA}
    cells[s7["cell"]] = {
        "sample": str(s7_path),
        "config": "EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s7.json",
        "n": s7["n"], "status": "completed",
        "policy": s7.get("policy"),
    }
    document = {
        "schema": SCHEMA_MERGED,
        "batch": "round3-expansion",
        "n": len(entries),
        "quotas": SCHEDULED_QUOTAS,
        "strata_totals": totals,
        "strata_ok": all(totals[name] == SCHEDULED_QUOTAS[name] for name in STRATA),
        "n_completed": sum(1 for e in entries if e["status"] == "completed"),
        "n_pending": sum(1 for e in entries if e["status"] == "pending"),
        "completed_evidence_missing": completed_missing,
        "verification_gate": [
            "verification.branch_states_identical == true",
            "verification.action_changed == true",
            "verification.legal_at_branch_point == true",
            "verification.baseline_action == decisions.jsonl.gz row chosen (same cell+decision_id)",
            "target.pid / target.hop_index / target.candidates == the same decision row",
            "config sha256 == evidence arms/now/config.json sha256 (pinned fork config)",
            "trace_source_sha256 == evidence trace-manifest.source_trace_sha256",
        ],
        "witness": ("decision-log prefix only: both branches must reproduce every "
                    "decision row committed before the target plus the target's "
                    "14 pre-commit fields; NOT full SimPy state"),
        "sign_convention": ("forced action = truth-substituted fresh direction; "
                            "delta = forced(fresh) - baseline(stale); POSITIVE = "
                            "the fresh action was slower (the stale action was "
                            "better at that branch point); NEGATIVE = fresh was "
                            "faster"),
        "cells": cells,
        "decisions": entries,
        "run_commands": {
            "note": ("a NEW release containing EXPERIMENTS/contracts/fork-configs/"
                     "m2-c2-greedy-s8.json and -s9.json is required; the Round-2 "
                     "s7 forks used release de632b3e5495074a90e69a6daa60e7525315217d-"
                     "76a4127eefc94e8d5074912e7307e6635cb052091d428bc05c6bc562c581c9a3"),
            "s8": ("cd /Users/lge/Desktop/topic/.wt-qual-20261031 && "
                   "RUN_FORK_PREFIX=fk3-s8- ANALYSIS/SIM-QUAL-20261031/run_forks.sh "
                   "<RELEASE_ID> EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s8.json "
                   "ANALYSIS/SIM-QUAL-20261031/fork-samples/r3-m2-c2-greedy-s8.json"),
            "s9": ("cd /Users/lge/Desktop/topic/.wt-qual-20261031 && "
                   "RUN_FORK_PREFIX=fk3-s9- ANALYSIS/SIM-QUAL-20261031/run_forks.sh "
                   "<RELEASE_ID> EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s9.json "
                   "ANALYSIS/SIM-QUAL-20261031/fork-samples/r3-m2-c2-greedy-s9.json"),
            "pull": ("CODE/scripts/remote/pull-release-results-remote.sh --run-id "
                     "<RUN_ID> --release-id <RELEASE_ID> --evidence-root "
                     "/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1"),
            "already_done": ("the 12 m2-c2-greedy-s7 forks are COMPLETE "
                             "(evidence run ids fk-c-<decision_id>-20261031-01, "
                             "release de632b3e...); do NOT rerun them"),
            "release_id_used": release_id,
        },
    }
    Path(out_path).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
    return document


# -------------------------------------------------------------------- cli
def _parse_quota(text):
    quotas = {}
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        quotas[key.strip()] = int(value)
    unknown = set(quotas) - set(STRATA)
    if unknown:
        raise SystemExit(f"unknown strata in --quota: {sorted(unknown)}")
    return quotas


def _parse_ids(text):
    return {int(chunk) for chunk in text.split(",") if chunk.strip()}


def _load_exclusions(paths):
    excluded = set()
    for path in paths or ():
        doc = json.loads(Path(path).read_text())
        for row in doc.get("decisions") or ():
            if row.get("decision_id") is not None:
                excluded.add(int(row["decision_id"]))
    return excluded


def cmd_cell(args):
    cell = Path(args.cell)
    doc = collect(cell)
    excluded = _load_exclusions(args.exclude) | _parse_ids(args.exclude_ids or "")
    result = select(doc, _parse_quota(args.quota), seed=args.seed,
                    excluded_ids=excluded)
    document = {
        "schema": SCHEMA_CELL,
        "cell": doc["provenance"]["cell"],
        "policy": doc["provenance"]["policy"],
        "provenance": doc["provenance"],
        "seed": args.seed,
        "excluded_decision_ids": sorted(excluded),
        "n": result["n"],
        "n_requested": result["n_requested"],
        "shortfall": result["shortfall"],
        "quota_requested": result["quota_requested"],
        "quota_filled": result["quota_filled"],
        "pool_sizes": result["pool_sizes"],
        "caps": result["caps"],
        "n_cap_relaxed": result["n_cap_relaxed"],
        "selection_rules": {
            "universe": ("kind=forward, >=2 candidates, chosen != truth-substituted "
                         "fresh, fresh inside the recorded candidate set, chosen has "
                         "a truth cost, 4.0 <= t < 14.0 s (PROTOCOL section 2)"),
            "strata": {
                "large_gamma": "gamma >= p75 of eligible flips (desc)",
                "small_margin": "margin_s <= p10 of eligible flips (asc)",
                "large_proxy_R": "proxy_regret_s >= p90 (desc)",
                "long_persistence": ("same-(sat,dst) stream, consecutive flips with the "
                                     "same chosen direction, run_len >= 2; ranked by "
                                     "run_len desc, at most 1 per run"),
                "high_z": ("chosen-resource eps_r on the decision's 0.5 s bin; "
                           "z_bits = bits/eps_r; only eps_r >= 1% of mu (valid subset); "
                           "at most 1 per resource and 1 per satellite"),
                "low_gamma": ("gamma <= p25 and gamma is defined -- the low-drift "
                              "negative control; at most 1 per run"),
                "random_control": "sha256(seed:decision_id) order over all eligible flips",
            },
            "ordering": "every pool is sorted by (metric, decision_id); no wall clock",
            "caps": result["caps"],
        },
        "strata": {name: sum(1 for r in result["picked"] if r["stratum"] == name)
                   for name in STRATA},
        "decisions": result["picked"],
    }
    Path(args.out).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
    print(json.dumps({"out": args.out, "cell": document["cell"],
                      "n": document["n"], "shortfall": document["shortfall"],
                      "strata": document["strata"],
                      "thresholds": doc["thresholds"],
                      "pool_sizes": document["pool_sizes"]}, indent=1))


def cmd_merge(args):
    evidence_root = args.evidence_root
    if evidence_root is None:
        default_root = Path("/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1")
        evidence_root = str(default_root) if default_root.is_dir() else None
    document = merge(args.s7, args.new, args.out,
                     run_prefix=args.run_prefix, release_id=args.release_id,
                     evidence_root=evidence_root)
    print(json.dumps({"out": args.out, "n": document["n"],
                      "n_completed": document["n_completed"],
                      "n_pending": document["n_pending"],
                      "strata_totals": document["strata_totals"],
                      "strata_ok": document["strata_ok"],
                      "completed_evidence_missing": document["completed_evidence_missing"]},
                     indent=1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cell_p = sub.add_parser("cell", help="sample one cell")
    cell_p.add_argument("cell")
    cell_p.add_argument("--quota", required=True)
    cell_p.add_argument("--out", required=True)
    cell_p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    cell_p.add_argument("--exclude", action="append", default=[])
    cell_p.add_argument("--exclude-ids", default="")
    cell_p.set_defaults(func=cmd_cell)
    merge_p = sub.add_parser("merge", help="build the merged 36-fork batch")
    merge_p.add_argument("--s7", required=True)
    merge_p.add_argument("--new", action="append", required=True)
    merge_p.add_argument("--out", required=True)
    merge_p.add_argument("--run-prefix", default=FORK_PREFIX)
    merge_p.add_argument("--release-id", default=None)
    merge_p.add_argument("--evidence-root", default=None)
    merge_p.set_defaults(func=cmd_merge)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
