#!/usr/bin/env python3
"""Round-3 main-matrix analyzer: six routing policies x sync x regime x seed.

Reads the read-only per-cell evidence a ``load_calibration`` cell writes:

    <run-dir>/<cell-id>/arms/now/decisions.jsonl.gz   every decision + TA audit
    <run-dir>/<cell-id>/arms/now/target_probe.jsonl   same-run truth at the
                                                      candidate target instant
    <run-dir>/<cell-id>/arms/now/result.json.gz       packet/fate/queue/control
    <run-dir>/<cell-id>/arms/now/config.json          resolved config identity
    <run-dir>/<cell-id>/trace-manifest.json           trace identity
    <run-dir>/<cell-id>/trace.jsonl.gz                compiled trace (pid -> dst)

Cell ids (parsed, never guessed from the run id):

    m3-<regime>-<policy>-h<sync_ms>-s<seed>   e.g. m3-c2-queue_greedy-h1000-s7
    policy in {geometry, queue_greedy, queue_greedy_hysteresis,
               queue_greedy_ewma, queue_softmin, queue_greedy_sticky}
    sync in {1000, 20}                         (advertisement interval, ms)

Round-2 ids are accepted for regression smoke tests:

    m2-<regime>-<policy>-s<seed>
    m2-sync<advert_ms>-<regime>-<policy>-s<seed>

FROZEN SEMANTICS (Lead-frozen; do not reinterpret in prose)

* ``flip`` = **truth-substituted same-decision re-ranking**, NOT a re-simulated
  counterfactual: ``a_fresh`` is the argmin of the SAME policy cost after
  replacing the queue state that policy actually used with the probe truth
  work-ahead bits.  It is not "what would have happened had the packet taken
  the other branch".
* J_truth:
    - queue-aware policies (legacy / queue_greedy / *_hysteresis / *_ewma /
      queue_softmin / queue_greedy_sticky):
        J_truth = j_used_s - resource_work_used_s
                  + truth.work_ahead_bits / service_rate_bps
    - geometry: the cost carries no state term, therefore J_truth = j_used_s
      and ``flip``/``regret`` are ZERO BY CONSTRUCTION on the evaluable
      subset (negative control).  A non-zero geometry flip is an analyzer bug.
* a_fresh variants (both reported):
    - ``flip_rule``  = the policy's own rule re-applied with truth-substituted
      J: geometry -> the policy's recorded used-state top (``applied_order``
      filtered to the legal candidates); queue_greedy/_ewma/legacy -> argmin
      J_truth; hysteresis -> recorded ``last_direction`` kept when
      ``J_truth[last] <= min(J_truth) + recorded eta``; sticky -> the recorded
      ``sticky_keep`` direction when the audit says the hold fired; softmin ->
      re-sample the RECORDED uniform draw through the truth-cost softmax.
    - ``flip_truth`` = chosen vs the plain truth argmin (cross-policy
      comparator; geometry must also be 0 there).
  Tie-break everywhere replicates ``state_policy.rank``: fallbacks after every
  fully-known candidate, then (J, index in the recorded candidate/legal order,
  direction name).
* ``regret`` R = J_truth(chosen) - min_a J_truth(a) >= 0.
* ``W`` = consecutive-wrong-decision duration: inside one (sat, dst) decision
  stream, for every flip decision at t0, W = t1 - t0 where t1 is the next
  LATER non-flip decision of that stream; no later clean decision is censored
  at the horizon.  W is a decision-level duration, NOT packet queueing time.
* ``Gamma`` = |d(Q_a/rate_a)/dt - d(Q_b/rate_b)/dt| * tau / m_used over the
  used-state top-1/top-2 pair (the pair the decision actually had in front of
  it).  Slopes are a local linear fit over +-0.25 s of the probe truth series
  at that decision's target instant; m_used = J_used[2nd] - J_used[1st].
  Same definition as sq_policy_analyze.py (reused, not re-derived).
* ``tau`` = t_commit - used_state[chosen].measured_at, decomposed into
  gen->recv = received_at - measured_at and recv->decision = t_commit -
  received_at.
* truth rows are accepted ONLY when ``identity_source == "used_state"`` AND
  the probe row's ``generation`` equals the decision's
  ``used_state[direction].generation``.
* cross-cell "same trace" is ``trace-manifest.json:source_trace_sha256``
  (NOT the csv identity: that one embeds the cell id in ``csv_path``).

The response-sensitivity axes in this report are BEHAVIOURAL PROXIES (switch
rate, mean same-direction run length, flip, action entropy, near-tie
fraction), not a mathematical sensitivity of the cost function.

Usage:
    python3 sq_round3_analyze.py cell   <cell-dir> [--json F] [--md F]
    python3 sq_round3_analyze.py matrix <cell-dir> [<cell-dir> ...]
                                        [--json F] [--md F] [--out-dir D]
                                        [--baseline POLICY] [--jobs N]
                                        [--cells-file F]

Only reads evidence; writes only the paths given on the command line.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Frozen definitions reused from the Round-1/2 analyzer (never re-derived):
#   qstats          nearest-rank quantiles (index = round(p*(n-1)))
#   _mean           finite-safe mean
#   load_trace_map  pid -> {src, dst, bits} from the compiled trace
#   local_slope     local linear d(bits/rate)/dt over +-half_window
from sq_policy_analyze import (_mean, load_trace_map,  # noqa: E402
                               local_slope, qstats)

SCHEMA = "sq-round3/v1"
FLIP_SEMANTICS = "truth_substituted_rerank_not_resimulated_counterfactual"

# ------------------------------------------------------------------ taxonomy
LEGACY = "legacy"
GEOMETRY = "geometry"
QUEUE_GREEDY = "queue_greedy"
QUEUE_GREEDY_HYSTERESIS = "queue_greedy_hysteresis"
QUEUE_GREEDY_EWMA = "queue_greedy_ewma"
QUEUE_SOFTMIN = "queue_softmin"
QUEUE_GREEDY_STICKY = "queue_greedy_sticky"

CANONICAL_POLICIES = (GEOMETRY, QUEUE_GREEDY, QUEUE_GREEDY_HYSTERESIS,
                      QUEUE_GREEDY_EWMA, QUEUE_SOFTMIN, QUEUE_GREEDY_STICKY,
                      LEGACY)

#: queue-aware = the policy's J contains a state term that truth substitution
#: replaces.  ``geometry`` is the only policy without one.
QUEUE_AWARE = frozenset({LEGACY, QUEUE_GREEDY, QUEUE_GREEDY_HYSTERESIS,
                         QUEUE_GREEDY_EWMA, QUEUE_SOFTMIN,
                         QUEUE_GREEDY_STICKY})

POLICY_ALIASES = {
    "geom": GEOMETRY, "geometry": GEOMETRY,
    "greedy": QUEUE_GREEDY, "queue_greedy": QUEUE_GREEDY,
    "hyst": QUEUE_GREEDY_HYSTERESIS, "hysteresis": QUEUE_GREEDY_HYSTERESIS,
    "queue_greedy_hysteresis": QUEUE_GREEDY_HYSTERESIS,
    "ewma": QUEUE_GREEDY_EWMA, "queue_greedy_ewma": QUEUE_GREEDY_EWMA,
    "softmin": QUEUE_SOFTMIN, "queue_softmin": QUEUE_SOFTMIN,
    "sticky": QUEUE_GREEDY_STICKY, "queue_greedy_sticky": QUEUE_GREEDY_STICKY,
    "legacy": LEGACY,
}

# Round-3: m3-<regime>-<policy>-h<sync_ms>-s<seed>
ROUND3_RE = re.compile(
    r"^(?P<prefix>m3|r3|sq5)-(?P<regime>[a-z0-9]+)-(?P<policy>[a-z_]+)"
    r"-(?P<sync>h\d+)-(?P<seed>s\d+)$")
# Round-2 regression: m2-<regime>-<policy>-s<seed>, optional sync<ms> token
ROUND2_RE = re.compile(
    r"^(?P<prefix>m2|f2)-(?:(?P<sync>sync\d+)-)?(?P<regime>[a-z0-9]+)"
    r"-(?P<policy>[a-z_]+)-(?P<seed>s\d+)$")

# ------------------------------------------------------------------- bins
GAMMA_BINS = (("lt_0.5", None, 0.5), ("0.5_1", 0.5, 1.0),
              ("1_2", 1.0, 2.0), ("2_5", 2.0, 5.0), ("gt_5", 5.0, None))
TAU_BINS = (("lt_50ms", None, 0.05), ("50_100ms", 0.05, 0.10),
            ("100_250ms", 0.10, 0.25), ("250_500ms", 0.25, 0.50),
            ("500ms_1s", 0.50, 1.0), ("ge_1s", 1.0, None))
MARGIN_BINS = (("lt_0.2ms", None, 0.0002), ("0.2_1ms", 0.0002, 0.001),
               ("1_2ms", 0.001, 0.002), ("2_5ms", 0.002, 0.005),
               ("5_10ms", 0.005, 0.01), ("10_50ms", 0.01, 0.05),
               ("ge_50ms", 0.05, None))
#: near-tie thresholds used by the behavioural sensitivity proxy (seconds)
NEAR_TIE_EPS_S = (0.0002, 0.0005, 0.001, 0.002, 0.005)
#: scalar metrics carried on every cell block and used by aggregates/paired
SCALAR_KEYS = (
    "n_decisions_multicandidate", "n_evaluable",
    "aoi_p50_ms", "aoi_p90_ms", "aoi_p95_ms", "aoi_p99_ms",
    "margin_used_p50_ms", "margin_truth_p50_ms",
    "flip_rule_rate", "flip_truth_rate",
    "regret_mean_ms", "regret_p50_ms", "regret_p90_ms", "regret_p99_ms",
    "regret_frac_gt0", "regret_frac_gt1ms",
    "gamma_median", "switch_rate", "run_length_mean_decisions",
    "entropy_mean_bits", "sticky_keep_rate", "sticky_override_rate",
    "near_tie_1ms_frac", "near_tie_2ms_frac", "near_tie_5ms_frac",
    "e2e_mean_ms", "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
    "pdr", "throughput_mbps", "hops_mean", "hops_p95",
    "isl_data_area", "util_busy_max", "util_busy_p95",
    "ctrl_offered_mbits", "ctrl_share", "ctrl_expired",
    "crosscheck_geometry_mismatch", "crosscheck_audit_identity_violations",
)
#: scalar keys compared in the freshness-vs-stability (sync) delta table
SYNC_DELTA_KEYS = (
    "aoi_p50_ms", "aoi_p90_ms", "flip_rule_rate", "flip_truth_rate",
    "switch_rate", "run_length_mean_decisions", "regret_mean_ms",
    "e2e_mean_ms", "e2e_p95_ms", "e2e_p99_ms", "pdr",
    "ctrl_offered_mbits", "ctrl_share",
)

DEFINITIONS = {
    "schema": SCHEMA,
    "flip_semantics": FLIP_SEMANTICS,
    "flip_not_claiming": (
        "flip is a truth-substituted re-ranking of the SAME decision row; it "
        "is NOT a re-simulated counterfactual and must not be reported as the "
        "causal consequence of replaying branch A instead of branch B"),
    "evaluable_subset": (
        "kind=='forward' AND >=2 legal candidates AND >=2 candidates with a "
        "usable truth row AND the chosen candidate itself has a usable truth "
        "row; every flip/regret/W/Gamma statistic is computed on it"),
    "truth_filter": (
        'target_probe rows with identity_source=="used_state" AND probe '
        "generation == decision used_state[direction].generation AND finite "
        "truth.work_ahead_bits AND a positive service rate"),
    "j_truth_queue_aware": (
        "j_used_s - resource_work_used_s + truth.work_ahead_bits / "
        "service_rate_bps (probe.service_rate_bps preferred, then the score's)"),
    "j_truth_geometry": (
        "j_used_s (no state term; flip/regret are 0 by construction)"),
    "a_fresh_rule": (
        "policy re-applied with truth-substituted J: geometry -> recorded "
        "used-state top among legal candidates; greedy/_ewma/_legacy -> argmin "
        "J_truth; hysteresis -> recorded last_direction if "
        "J_truth[last] <= min + recorded eta; sticky -> recorded sticky_keep "
        "direction when the hold fired, else argmin; softmin -> recorded "
        "uniform re-sampled through the truth-cost softmax"),
    "a_fresh_truth": "plain argmin J_truth (cross-policy comparator)",
    "tie_break": (
        "(J, index in the recorded candidate/legal order, direction name); "
        "fallback candidates rank after every fully-known candidate"),
    "tau_s": (
        "t_commit - used_state[chosen].measured_at; gen_to_recv = "
        "received_at - measured_at; recv_to_decision = t_commit - received_at"),
    "regret_s": "J_truth(chosen) - min_a J_truth(a); >= 0",
    "W_s": (
        "consecutive-wrong-decision duration: per (sat,dst) stream and per flip "
        "decision at t0, W = t1 - t0 with t1 the next later NON-flip decision "
        "of that stream; censored at the horizon. NOT packet queueing time"),
    "gamma": (
        "|d(Q_a/rate_a)/dt - d(Q_b/rate_b)/dt| * tau / m_used over the "
        "used-state top-1/top-2 pair; slope from a local linear fit over "
        "+-0.25 s of the probe truth series at the decision target instant"),
    "switch_rate": (
        "fraction of consecutive multi-candidate decisions inside one "
        "(sat,dst) stream whose chosen direction changed"),
    "run_length": (
        "number of consecutive same-chosen multi-candidate decisions inside "
        "one (sat,dst) stream"),
    "action_entropy_bits": (
        "mean Shannon entropy of ta_audit.state_policy.selection.probabilities "
        "(or selection.entropy); n/a for policies whose audit exposes no "
        "probabilities (all but queue_softmin today)"),
    "sticky_keep_rate": (
        "fraction of decisions whose state_policy audit says selection.rule == "
        "'sticky_keep' (P5); n/a when the audit has no sticky fields"),
    "near_tie_proxy": (
        "fraction of decisions with a used-state margin below eps; this is the "
        "behavioural 'small score perturbation -> action divergence' proxy, "
        "not a mathematical sensitivity"),
    "util_busy": (
        "per ISL link: sum(window.end-window.start for stage=='isl') / "
        "stop_time from link_service_windows; max/p95 over links that served "
        "data.  Static topology in these cells, so no availability correction"),
    "ctrl_share": (
        "occupied.ctrl_isl_s / stop_time_s (the ledger's authoritative control "
        "ISL occupancy).  link_service_windows carry no kind=='ctrl' marker, "
        "so the window-sum variant is not available"),
    "same_trace": (
        "trace-manifest.json:source_trace_sha256 (never the csv identity)"),
    "paired_e2e": (
        "cells sharing family+regime+sync_ms+seed also share one trace, so "
        "packet ids align; only packets DELIVERED in BOTH cells are paired, "
        "delta = policy - baseline"),
    "response_sensitivity_axes": (
        "BEHAVIOURAL PROXIES (switch rate, mean run length, flip, entropy, "
        "near-tie fraction); not a strict mathematical sensitivity of J"),
}


# ---------------------------------------------------------------- io helpers
def _open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def load_json(path: Path):
    with _open_text(path) as fh:
        return json.load(fh)


def iter_jsonl(path: Path):
    with _open_text(path) as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _finite(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _rate(numerator, denominator):
    return None if not denominator else numerator / denominator


def _ms(value):
    return None if value is None else 1000.0 * float(value)


def _quantile_ms(values):
    """One-pass quantile summary in milliseconds (nearest-rank via qstats)."""
    clean = [float(v) for v in values if _finite(v)]
    if not clean:
        return {"n": 0, "mean": None, "p50": None, "p90": None,
                "p95": None, "p99": None}
    quantiles = qstats(clean, points=(0.5, 0.9, 0.95, 0.99))
    return {"n": len(clean), "mean": _ms(_mean(clean)),
            "p50": _ms(quantiles.get("p50")),
            "p90": _ms(quantiles.get("p90")),
            "p95": _ms(quantiles.get("p95")),
            "p99": _ms(quantiles.get("p99"))}


# ------------------------------------------------------------------- cells
def resolve_cell_dir(path: Path) -> Path:
    """Accept the cell dir itself, or a run dir holding exactly one cell."""
    if (path / "arms" / "now" / "decisions.jsonl.gz").is_file():
        return path
    if path.is_dir():
        nested = sorted(p for p in path.iterdir()
                        if (p / "arms" / "now" / "decisions.jsonl.gz").is_file())
        if len(nested) == 1:
            return nested[0]
    return path


def _sync_ms_from_token(token):
    if not token:
        return None
    digits = re.sub(r"[^0-9]", "", token)
    return int(digits) if digits else None


def parse_cell_id(name: str):
    """Parse a Round-3 or Round-2 cell id; never guess from the run id."""
    for family, regex in (("m3", ROUND3_RE), ("m2", ROUND2_RE)):
        match = regex.match(name)
        if not match:
            continue
        groups = match.groupdict()
        raw = groups["policy"]
        policy = POLICY_ALIASES.get(raw)
        return {
            "family": family,
            "regime": groups["regime"],
            "policy_raw": raw,
            "policy": policy,
            "sync_ms": _sync_ms_from_token(groups.get("sync")),
            "explicit_sync": groups.get("sync") is not None,
            "seed": int(groups["seed"][1:]),
            "parsed": True,
        }
    return None


def _manifest(cell: Path) -> dict:
    path = cell / "trace-manifest.json"
    if not path.is_file():
        return {}
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _config(cell: Path) -> dict:
    path = cell / "arms" / "now" / "config.json"
    if not path.is_file():
        return {}
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _trace_key(manifest: dict):
    """Frozen identity for 'these two cells share one trace'."""
    return manifest.get("source_trace_sha256") or manifest.get("jsonl_sha256")


def cell_meta(cell: Path, config: dict, manifest: dict):
    """Cell label from the id, cross-checked against the resolved config.

    The config is authoritative for the policy name, the advertisement
    interval and the seed; a disagreement with the id is reported, never
    silently resolved.
    """
    parsed = parse_cell_id(cell.name) or {
        "family": None, "regime": None, "policy_raw": None, "policy": None,
        "sync_ms": None, "explicit_sync": False, "seed": None,
        "parsed": False}
    # A run series is part of the experiment identity: Round-2 produced the
    # "default" cells (no sync token) and the separate ``sync<ms>`` sweep.
    # Round-3 ids always carry h<ms>, so every Round-3 cell is one series.
    series = ("default" if parsed["family"] == "m2"
              and not parsed["explicit_sync"] else "sync")
    warnings = []
    resolved = config.get("resolved_config") or {}
    ta = resolved.get("time_alignment") or {}
    audit_policy = ta.get("state_policy")
    audit_policy = POLICY_ALIASES.get(audit_policy, audit_policy)
    advertise = (resolved.get("control_plane") or {}).get("advertise_interval_s")
    audit_sync_ms = (int(round(float(advertise) * 1000.0))
                     if _finite(advertise) else None)
    audit_seed = (resolved.get("scenario") or {}).get("seed")

    if parsed["policy"] and audit_policy and parsed["policy"] != audit_policy:
        warnings.append("policy id %r != config state_policy %r"
                        % (parsed["policy"], audit_policy))
    if parsed["sync_ms"] is None:
        parsed["sync_ms"] = audit_sync_ms
    elif audit_sync_ms is not None and parsed["sync_ms"] != audit_sync_ms:
        warnings.append("sync id %r ms != config advertise %r ms"
                        % (parsed["sync_ms"], audit_sync_ms))
    if parsed["seed"] is None:
        parsed["seed"] = audit_seed
    elif audit_seed is not None and parsed["seed"] != audit_seed:
        warnings.append("seed id %r != config scenario.seed %r"
                        % (parsed["seed"], audit_seed))
    policy = parsed["policy"] or audit_policy
    if policy is None:
        warnings.append("cell id not parsed and config carries no state_policy")
    policy_params = {
        "state_policy": audit_policy,
        "hysteresis_eta_s": ta.get("hysteresis_eta_s"),
        "ewma_alpha": ta.get("ewma_alpha"),
        "softmin_temperature_s": ta.get("softmin_temperature_s"),
        "sticky_hold_s": ta.get("sticky_hold_s"),
        "advertise_interval_s": advertise,
        "control_plane": {
            "advertise_interval_s": advertise,
            "packet_bits": (resolved.get("control_plane") or {}).get("packet_bits"),
            "vis_k": (resolved.get("control_plane") or {}).get("vis_k"),
            "ttl_s": (resolved.get("control_plane") or {}).get("ttl_s"),
            "priority": (resolved.get("control_plane") or {}).get("priority"),
        },
        "predictor": ta.get("predictor"),
        "state_source": ta.get("state_source"),
    }
    if policy == QUEUE_GREEDY_HYSTERESIS and not _finite(
            policy_params["hysteresis_eta_s"]):
        warnings.append("hysteresis cell without a finite recorded eta")
    if policy == QUEUE_SOFTMIN and not _finite(
            policy_params["softmin_temperature_s"]):
        warnings.append("softmin cell without time_alignment.softmin_temperature_s")
    if policy == QUEUE_GREEDY_STICKY and not _finite(
            policy_params["sticky_hold_s"]):
        warnings.append("sticky cell without time_alignment.sticky_hold_s")
    return {
        "cell_id": cell.name,
        "family": parsed["family"],
        "regime": parsed["regime"],
        "policy_raw": parsed["policy_raw"],
        "policy": policy,
        "policy_known": policy in CANONICAL_POLICIES,
        "sync_ms": parsed["sync_ms"],
        "series": series,
        "explicit_sync": parsed["explicit_sync"],
        "seed": parsed["seed"],
        "id_parsed": parsed["parsed"],
        "policy_params": policy_params,
        "trace_source_sha256": manifest.get("source_trace_sha256"),
        "trace_jsonl_sha256": manifest.get("jsonl_sha256"),
        "trace_rows": manifest.get("rows"),
        "trace_key": _trace_key(manifest),
        "config_sha256": config.get("sha256"),
        "warnings": warnings,
    }


# ------------------------------------------------------------------- probe
def load_probe(cell: Path):
    """One streaming pass over target_probe.jsonl.

    Returns truth / series / adv_delay / counters where

    truth      {(decision_id, direction): {bits, rate, gen, resource_id, eval}}
               only rows passing the frozen identity filter
    series     {resource_id: [(eval_target_s, bits, rate)]} deduplicated and
               sorted (the dQ/dt input)
    adv_delay  [received_at - measured_at] for used_state rows (the
               advertisement age at receipt, the control-plane freshness)
    counters   row / identity census and the probe's decision coverage
    """
    truth, adv_delay, identity = {}, [], Counter()
    series = defaultdict(dict)
    decisions = set()
    rows = 0
    path = cell / "arms" / "now" / "target_probe.jsonl"
    if not path.is_file():
        return truth, {}, adv_delay, {"rows": 0, "identity_sources": {},
                                      "missing": str(path)}
    for row in iter_jsonl(path):
        rows += 1
        identity[str(row.get("identity_source"))] += 1
        decisions.add(row.get("decision_id"))
        if row.get("identity_source") != "used_state":
            continue
        block = row.get("truth")
        if not isinstance(block, dict):
            continue
        bits = block.get("work_ahead_bits")
        rate = row.get("service_rate_bps")
        if not (_finite(rate) and float(rate) > 0):
            rate = block.get("rate_bps")
        if not (_finite(bits) and _finite(rate) and float(rate) > 0):
            continue
        resource = block.get("resource_id") or row.get("resource_id")
        truth[(row.get("decision_id"), row.get("direction"))] = {
            "bits": float(bits),
            "rate": float(rate),
            "gen": row.get("generation"),
            "resource_id": resource,
            "eval": row.get("eval_target_s"),
        }
        moment = row.get("eval_target_s")
        if isinstance(resource, str) and _finite(moment):
            series[resource][round(float(moment), 9)] = (float(bits),
                                                         float(rate))
        measured = row.get("state_measured_at_s")
        received = row.get("state_received_at_s")
        if _finite(measured) and _finite(received):
            adv_delay.append(float(received) - float(measured))
    frozen = {resource: [(t, bits, rate) for t, (bits, rate)
                         in sorted(samples.items())]
              for resource, samples in series.items()}
    counters = {"rows": rows,
                "identity_sources": dict(sorted(identity.items())),
                "truth_rows": len(truth),
                "truth_resources": len(frozen),
                "decisions_in_probe": len([d for d in decisions if d is not None])}
    return truth, frozen, adv_delay, counters


# --------------------------------------------------------------- decisions
def _policy_view(decision: dict, config_policy):
    """Policy view of one decision, with the legacy scorer fallback.

    When the audit carries no ``state_policy`` block (policy ``legacy``), the
    scorer block is used directly: J_used = scores[dir].total_s and
    resource_work_used = scores[dir].terms.resource_work_s.
    """
    obs = decision.get("observation_at_start") or {}
    audit = obs.get("time_alignment") or {}
    scores = audit.get("scores") or {}
    used = audit.get("used_state") or {}
    raw = audit.get("state_policy")
    legacy = not isinstance(raw, dict)
    raw = raw or {}
    per_dir = raw.get("per_direction") or {}
    policy = POLICY_ALIASES.get(raw.get("policy"), raw.get("policy"))
    if policy is None:
        policy = POLICY_ALIASES.get(config_policy, config_policy) or LEGACY
    per_direction = {}
    for direction in decision.get("candidates") or []:
        entry = per_dir.get(direction)
        if isinstance(entry, dict):
            per_direction[direction] = {
                "j_used": entry.get("j_used_s"),
                "resource_work": entry.get("resource_work_used_s"),
                "rate": entry.get("service_rate_bps"),
                "fallback": bool(entry.get("fallback")),
                "kept": bool(entry.get("kept")),
                "ewma_samples": entry.get("ewma_samples"),
                "q_used_bits": entry.get("q_used_bits"),
            }
            continue
        score = scores.get(direction) or {}
        per_direction[direction] = {
            "j_used": score.get("total_s"),
            "resource_work": (score.get("terms") or {}).get("resource_work_s"),
            "rate": score.get("service_rate_bps"),
            "fallback": bool(score.get("fallback")),
            "kept": False,
            "ewma_samples": None,
            "q_used_bits": None,
        }
    return {
        "policy": policy,
        "legacy": legacy,
        "raw": raw,
        "used": used,
        "per_direction": per_direction,
        "selection": raw.get("selection") or {},
        "applied_order": raw.get("applied_order"),
        "last_direction": raw.get("last_direction"),
        "eta_s": raw.get("hysteresis_eta_s"),
    }


def _audit_entropy(selection: dict, raw: dict):
    """Action entropy of one decision when the audit exposes it."""
    probs = (selection.get("probabilities") if isinstance(selection, dict)
             else None) or raw.get("probabilities")
    if isinstance(probs, dict) and len(probs) >= 2:
        values = [float(v) for v in probs.values()
                  if _finite(v) and float(v) > 0.0]
        total = sum(values)
        if total > 0:
            return -sum((value / total) * math.log2(value / total)
                        for value in values)
    for token in (selection.get("entropy"), raw.get("entropy")):
        if _finite(token):
            return float(token)
    return None


def _softmin_fresh(truth_known: dict, order, selection: dict, raw: dict,
                   params: dict):
    """Re-sample the recorded uniform draw through the truth-cost softmax.

    The uniform belongs to the decision, so re-using it makes the
    truth-substituted softmin action deterministic (no hidden RNG).
    """
    uniform = selection.get("uniform", raw.get("uniform"))
    temperature = None
    for token in (selection.get("temperature"),
                  selection.get("temperature_s"),
                  raw.get("softmin_temperature_s"),
                  params.get("softmin_temperature_s")):
        if _finite(token):
            temperature = float(token)
            break
    if not (_finite(uniform) and _finite(temperature) and temperature > 0):
        return None, "softmin_resample_unavailable: argmin used"
    floor = min(truth_known.values())
    weights = [math.exp(-(truth_known[direction] - floor) / temperature)
               for direction in order]
    total = sum(weights)
    if total <= 0:
        return None, "softmin_resample_unavailable: empty weights"
    draw = min(max(float(uniform), 0.0), 1.0 - 1e-15)
    cumulative = 0.0
    for direction, weight in zip(order, weights):
        cumulative += weight / total
        if draw <= cumulative:
            return direction, "softmin_resample"
    return order[-1], "softmin_resample"


def analyze_decision(decision: dict, config_policy, truth: dict,
                     trace_map: dict, series: dict, params: dict):
    """One multi-candidate forward decision -> (record, None) or (None, reason)."""
    candidates = decision.get("candidates") or []
    chosen = decision.get("chosen")
    if chosen not in candidates:
        return None, "chosen_not_in_candidates"
    view = _policy_view(decision, config_policy)
    policy = view["policy"]
    queue_aware = policy in QUEUE_AWARE
    decision_id = decision.get("decision_id")
    used = view["used"]

    j_used, j_truth, truth_resources = {}, {}, {}
    for direction in candidates:
        entry = view["per_direction"].get(direction) or {}
        j = entry.get("j_used")
        work = entry.get("resource_work")
        rate = entry.get("rate")
        if not (_finite(j) and _finite(work) and _finite(rate)
                and float(rate) > 0):
            continue
        j_used[direction] = float(j)
        probe = truth.get((decision_id, direction))
        if probe is None:
            continue
        generation = (used.get(direction) or {}).get("generation")
        if probe["gen"] is not None and generation is not None \
                and probe["gen"] != generation:
            continue
        rate = float(probe["rate"]) if (_finite(probe["rate"])
                                       and float(probe["rate"]) > 0) else float(rate)
        j_truth[direction] = (float(j) - float(work) + probe["bits"] / rate
                              if queue_aware else float(j))
        truth_resources[direction] = probe["resource_id"]
    if len(j_used) < 2:
        return None, "lt_two_fully_known_candidates"
    if len(j_truth) < 2 or chosen not in j_truth:
        return None, "chosen_or_pair_without_truth"

    # state_policy.rank tie-break: (J, legal-order index, direction name)
    def order_key(mapping):
        return lambda d: (mapping[d], candidates.index(d), d)

    policy_top = None
    for direction in view["applied_order"] or []:
        if direction in candidates:
            policy_top = direction
            break
    if policy_top is None:
        policy_top = min(j_used, key=order_key(j_used))
    fresh_truth = min(j_truth, key=order_key(j_truth))
    truth_order = sorted(j_truth, key=order_key(j_truth))

    fresh_rule, fresh_note = fresh_truth, "argmin_truth"
    if policy == GEOMETRY:
        # No state term exists, so truth cannot change the ordering: the
        # fresh action is exactly the recorded used-state policy top.  Using
        # the truth-subset argmin here instead would manufacture flips when a
        # candidate without a truth row sits above the chosen one.
        fresh_rule = fresh_truth = policy_top
        fresh_note = "geometry_recorded_policy_top"
    elif policy == QUEUE_GREEDY_HYSTERESIS:
        last = view["last_direction"]
        eta = view["eta_s"]
        if not _finite(eta):
            eta = params.get("hysteresis_eta_s", 0.0)
        eta = float(eta or 0.0)
        if (last is not None and last in j_truth
                and float(j_truth[last]) <= float(j_truth[fresh_truth]) + eta):
            fresh_rule, fresh_note = last, "hysteresis_keep"
        else:
            fresh_note = "hysteresis_switch"
    elif policy == QUEUE_SOFTMIN:
        sampled, fresh_note = _softmin_fresh(j_truth, truth_order,
                                            view["selection"], view["raw"],
                                            params)
        if sampled is not None:
            fresh_rule = sampled
    elif policy == QUEUE_GREEDY_STICKY:
        selection = view["selection"]
        kept = selection.get("kept_direction")
        if selection.get("rule") == "sticky_keep" and kept in j_truth:
            fresh_rule, fresh_note = kept, "sticky_keep"
        else:
            fresh_note = "sticky_argmin"

    used_argmin = min(j_used, key=order_key(j_used))
    flipped_rule = chosen != fresh_rule
    flipped_truth = chosen != fresh_truth
    regret = float(j_truth[chosen]) - float(j_truth[fresh_truth])

    margin_used = None
    if len(j_used) >= 2:
        ordered_used = sorted(j_used.values())
        margin_used = ordered_used[1] - ordered_used[0]
    margin_truth = None
    if len(j_truth) >= 2:
        ordered_truth = sorted(j_truth.values())
        margin_truth = ordered_truth[1] - ordered_truth[0]

    commit = decision.get("t")
    state = used.get(chosen) or {}
    tau = gen_to_recv = recv_to_decision = None
    if _finite(commit) and _finite(state.get("measured_at")):
        tau = float(commit) - float(state["measured_at"])
    if _finite(state.get("measured_at")) and _finite(state.get("received_at")):
        gen_to_recv = float(state["received_at"]) - float(state["measured_at"])
    if _finite(commit) and _finite(state.get("received_at")):
        recv_to_decision = float(commit) - float(state["received_at"])

    gamma = None
    if len(j_used) >= 2 and tau is not None:
        ordered_dirs = sorted(j_used, key=order_key(j_used))
        margin = j_used[ordered_dirs[1]] - j_used[ordered_dirs[0]]
        if margin > 1e-9:
            probe_a = truth.get((decision_id, ordered_dirs[0])) or {}
            probe_b = truth.get((decision_id, ordered_dirs[1])) or {}
            resource_a = (probe_a.get("resource_id")
                          or (used.get(ordered_dirs[0]) or {}).get("resource_id"))
            resource_b = (probe_b.get("resource_id")
                          or (used.get(ordered_dirs[1]) or {}).get("resource_id"))
            moment = probe_a.get("eval") if _finite(probe_a.get("eval")) \
                else commit
            if resource_a and resource_b and _finite(moment):
                slope_a = local_slope(series, resource_a, float(moment))
                slope_b = local_slope(series, resource_b, float(moment))
                if slope_a is not None and slope_b is not None:
                    gamma = abs(slope_a - slope_b) * float(tau) / margin

    selection = view["selection"]
    entropy = _audit_entropy(selection, view["raw"])
    sticky_keep = sticky_override = None
    if policy == QUEUE_GREEDY_STICKY:
        sticky_keep = selection.get("rule") == "sticky_keep"
        token = selection.get("override")
        sticky_override = bool(token) if token is not None else None
        if sticky_override is None and _finite(selection.get("forced_keep_count")):
            sticky_override = float(selection["forced_keep_count"]) > 0

    pid = decision.get("pid")
    dst = (trace_map.get(pid) or {}).get("dst")
    if dst is None:
        dst = decision.get("dst")
    return {
        "decision_id": decision_id,
        "t": float(commit) if _finite(commit) else None,
        "pid": pid,
        "sat": decision.get("sat"),
        "dst": dst,
        "chosen": chosen,
        "policy": policy,
        "policy_top": policy_top,
        "fresh_rule": fresh_rule,
        "fresh_note": fresh_note,
        "flip_rule": bool(flipped_rule),
        "flip_truth": bool(flipped_truth),
        "regret": regret,
        "tau": tau,
        "gen_to_recv": gen_to_recv,
        "recv_to_decision": recv_to_decision,
        "margin_used": margin_used,
        "margin_truth": margin_truth,
        "gamma": gamma,
        "entropy": entropy,
        "sticky_keep": sticky_keep,
        "sticky_override": sticky_override,
        "n_truth_candidates": len(j_truth),
        "used_argmin": used_argmin,
        "identity_violation": _identity_violation(view["per_direction"]),
        "legacy_fallback": view["legacy"],
        "audit_policy": view["policy"],
    }, None


def _identity_violation(per_direction: dict) -> bool:
    """True when the audit's own identity
    ``resource_work_used_s == q_used_bits / service_rate_bps`` is violated.

    ``state_policy.rank`` guarantees it, so a violation means the offline
    replay ``J_fresh = j_used - resource_work + truth/rate`` is not exact.
    """
    for entry in per_direction.values():
        work = entry.get("resource_work")
        rate = entry.get("rate")
        # q_used_bits is not projected into the compact view; the identity can
        # only be checked where the audit exposes it.
        bits = entry.get("q_used_bits")
        if not (_finite(work) and _finite(rate) and _finite(bits)
                and float(rate) > 0):
            continue
        expected = float(bits) / float(rate)
        if abs(float(work) - expected) > max(1e-12, 1e-9 * abs(expected)):
            return True
    return False


def load_decisions(cell: Path, config_policy, truth: dict, trace_map: dict,
                   series: dict, params: dict):
    records = []
    counters = Counter()
    verdicts = Counter()
    path = cell / "arms" / "now" / "decisions.jsonl.gz"
    if not path.is_file():
        return records, counters, verdicts, {"rows": 0, "missing": str(path)}
    for decision in iter_jsonl(path):
        counters["rows"] += 1
        kind = decision.get("kind")
        counters["kind_%s" % kind] += 1
        if kind != "forward":
            continue
        counters["forward"] += 1
        candidates = decision.get("candidates") or []
        counters["candidate_count_%d" % len(candidates)] += 1
        if len(candidates) < 2:
            continue
        counters["forward_multicandidate"] += 1
        record, reason = analyze_decision(decision, config_policy, truth,
                                          trace_map, series, params)
        if record is None:
            verdicts[reason] += 1
            continue
        records.append(record)
        if record["legacy_fallback"]:
            counters["legacy_fallback_records"] += 1
    counters["evaluable"] = len(records)
    return records, counters, verdicts, {"rows": counters["rows"]}


# -------------------------------------------------------- decision aggregates
def _flip_block(records, key):
    n = len(records)
    flips = sum(1 for record in records if record[key])
    return {"n": n, "flips": flips, "rate": _rate(flips, n)}


def _bin_table(records, key, bins):
    rows = []
    for label, low, high in bins:
        selected = [record for record in records
                    if record.get(key) is not None
                    and (low is None or record[key] >= low)
                    and (high is None or record[key] < high)]
        rows.append({
            "label": label, "low": low, "high": high, "n": len(selected),
            "flip_rule": _flip_block(selected, "flip_rule"),
            "flip_truth": _flip_block(selected, "flip_truth"),
        })
    return rows


def _streams(records):
    streams = defaultdict(list)
    for record in records:
        if record["t"] is None:
            continue
        streams[(record["sat"], record["dst"])].append(record)
    for key in streams:
        streams[key].sort(key=lambda record: (record["t"],
                                              record["decision_id"] or 0))
    return streams


def _wrong_durations(streams, flip_key):
    """Per flip decision: seconds to the next later non-flip decision.

    O(n) per stream: scan once from the end carrying the nearest later
    non-flip index, so a long flip run cannot make this quadratic.
    """
    durations, censored, flips = [], 0, 0
    for sequence in streams.values():
        size = len(sequence)
        next_clean = [None] * size
        nearest = None
        for index in range(size - 1, -1, -1):
            next_clean[index] = nearest
            if sequence[index][flip_key] is False:
                nearest = index
        for index, record in enumerate(sequence):
            if not record[flip_key]:
                continue
            flips += 1
            target = next_clean[index]
            if (target is None or record["t"] is None
                    or sequence[target]["t"] is None):
                censored += 1
                continue
            durations.append(float(sequence[target]["t"]) - float(record["t"]))
    return {
        "n_flips": flips,
        "n_uncensored": len(durations),
        "n_censored": censored,
        "censored_frac": _rate(censored, flips),
        "median_ms": _ms(statistics.median(durations)) if durations else None,
        "p90_ms": _ms(qstats(durations, points=(0.9,)).get("p90")),
        "mean_ms": _ms(_mean(durations)),
    }


def decision_stats(records, counters, verdicts):
    aoi, gen2rx, rx2dec = [], [], []
    margins_used, margins_truth, regrets, gammas = [], [], [], []
    entropies, sticky_keeps, sticky_overrides = [], [], []
    for record in records:
        for value, bucket in ((record["tau"], aoi),
                              (record["gen_to_recv"], gen2rx),
                              (record["recv_to_decision"], rx2dec),
                              (record["margin_used"], margins_used),
                              (record["margin_truth"], margins_truth),
                              (record["regret"], regrets),
                              (record["gamma"], gammas),
                              (record["entropy"], entropies)):
            if value is not None:
                bucket.append(value)
        if record["sticky_keep"] is not None:
            sticky_keeps.append(record["sticky_keep"])
        if record["sticky_override"] is not None:
            sticky_overrides.append(record["sticky_override"])

    streams = _streams(records)
    switch_pairs, switch_moves, run_lengths = 0, 0, []
    for sequence in streams.values():
        for left, right in zip(sequence, sequence[1:]):
            switch_pairs += 1
            switch_moves += int(left["chosen"] != right["chosen"])
        current = 1
        for left, right in zip(sequence, sequence[1:]):
            if left["chosen"] == right["chosen"]:
                current += 1
            else:
                run_lengths.append(current)
                current = 1
        run_lengths.append(current)

    regret_pos = [value for value in regrets if value > 0]
    stats = {
        "n_evaluable": len(records),
        "counters": dict(sorted(counters.items())),
        "excluded_reasons": dict(sorted(verdicts.items())),
        "legacy_fallback_records": counters.get("legacy_fallback_records", 0),
        "aoi_ms": _quantile_ms(aoi),
        "aoi_decomposition_ms": {
            "gen_to_recv": _quantile_ms(gen2rx),
            "recv_to_decision": _quantile_ms(rx2dec),
        },
        "margin_ms": {
            "used": _quantile_ms(margins_used),
            "truth": _quantile_ms(margins_truth),
            "note": "used = J_used 1st/2nd gap; truth = J_truth 1st/2nd gap",
        },
        "flip": {
            "semantics": FLIP_SEMANTICS,
            "denominator": DEFINITIONS["evaluable_subset"],
            "rule": _flip_block(records, "flip_rule"),
            "truth_argmin": _flip_block(records, "flip_truth"),
        },
        "regret_ms": {
            "mean": _ms(_mean(regrets)),
            "p50": _ms(qstats(regrets, points=(0.5,)).get("p50")),
            "p90": _ms(qstats(regrets, points=(0.9,)).get("p90")),
            "p99": _ms(qstats(regrets, points=(0.99,)).get("p99")),
            "n": len(regrets),
            "frac_gt_0": _rate(len(regret_pos), len(regrets)),
            "frac_gt_1ms": _rate(sum(1 for value in regrets if value > 1e-3),
                                 len(regrets)),
        },
        "switch": {"rate": _rate(switch_moves, switch_pairs),
                   "switches": switch_moves, "pairs": switch_pairs,
                   "streams": len(streams)},
        "run_length": {"mean": _mean(run_lengths),
                       "p50": qstats(run_lengths, points=(0.5,)).get("p50"),
                       "p90": qstats(run_lengths, points=(0.9,)).get("p90"),
                       "max": max(run_lengths) if run_lengths else None,
                       "runs": len(run_lengths)},
        "gamma": {"definition": DEFINITIONS["gamma"],
                  "median": statistics.median(gammas) if gammas else None,
                  "n": len(gammas),
                  "bins": _bin_table(records, "gamma", GAMMA_BINS)},
        "gamma_note": ("each Gamma bin's flip rate is over the records in that "
                       "bin; the n column is the bin size"),
        "flip_vs_tau_bins": _bin_table(records, "tau", TAU_BINS),
        "flip_vs_margin_bins": _bin_table(records, "margin_used", MARGIN_BINS),
        "entropy_bits": {
            "mean": _mean(entropies), "n": len(entropies),
            "reason": (None if entropies else
                       "no selection.probabilities/selection.entropy in the "
                       "state_policy audit for this policy")},
        "sticky": {
            "n_with_audit": len(sticky_keeps),
            "keep_rate": _rate(sum(1 for value in sticky_keeps if value),
                               len(sticky_keeps)),
            "override_rate": _rate(sum(1 for value in sticky_overrides if value),
                                   len(sticky_overrides)),
            "n_override_audit": len(sticky_overrides),
            "reason": (None if sticky_keeps else
                       "no sticky_keep/override fields in the audit")},
        "wrong_duration_W_ms": {"definition": DEFINITIONS["W_s"],
                                "rule": _wrong_durations(streams, "flip_rule"),
                                "truth_argmin": _wrong_durations(streams,
                                                                 "flip_truth")},
        "near_tie_frac": {
            "eps_%gms" % (eps * 1000.0): _rate(
                sum(1 for value in margins_used if value < eps), len(margins_used))
            for eps in NEAR_TIE_EPS_S},
        "cross_checks": {
            "chosen_equals_used_argmin": _rate(
                sum(1 for record in records
                    if record["chosen"] == record["used_argmin"]), len(records)),
            "chosen_not_used_argmin": sum(
                1 for record in records
                if record["chosen"] != record["used_argmin"]),
            "geometry_policy_top_mismatch": sum(
                1 for record in records
                if record.get("policy") == GEOMETRY
                and record["chosen"] != record["policy_top"]),
            "audit_identity_violations": sum(
                1 for record in records if record["identity_violation"]),
            "note": ("chosen_not_used_argmin is expected > 0 only for "
                     "hysteresis/sticky/softmin (their rule may hold or sample "
                     "a non-argmin); geometry_policy_top_mismatch and "
                     "audit_identity_violations must be 0"),
        },
    }
    stats["coverage"] = {
        "n_decision_rows_total": counters.get("rows"),
        "n_forward_total": counters.get("forward"),
        "n_forward_multicandidate": counters.get("forward_multicandidate"),
        "n_evaluable_with_truth": len(records),
        "evaluable_fraction_of_multicandidate": _rate(
            len(records), counters.get("forward_multicandidate")),
        "candidate_count_histogram": {
            key.split("_")[-1]: value for key, value in sorted(counters.items())
            if key.startswith("candidate_count_")},
    }
    return stats


# ------------------------------------------------------------------ network
def network_stats(result: dict, duration: float):
    metrics = result.get("congestion_metrics") or {}
    packets = metrics.get("packets") or {}
    e2e = [float(p["e2e_s"]) for p in packets.values()
           if isinstance(p, dict) and _finite(p.get("e2e_s"))]
    totals = result.get("totals") or {}
    fates = result.get("fate_counts") or {}
    deliveries = result.get("deliveries") or {}
    hops = [len(record.get("path") or []) - 1 for record in deliveries.values()
            if isinstance(record.get("path"), list) and record["path"]]
    queue_area = result.get("queue_area_bits_s") or {}
    control = result.get("control") or {}
    control_bits = control.get("bits") or {}
    counters = control.get("counters") or {}
    occupied = result.get("occupied") or {}

    busy, bits_by_link, rates = defaultdict(float), defaultdict(float), {}
    for window in result.get("link_service_windows") or []:
        if window.get("stage") != "isl":
            continue
        link = window.get("link_id")
        start, end = window.get("start"), window.get("end")
        if not isinstance(link, str) or not (_finite(start) and _finite(end)):
            continue
        width = float(end) - float(start)
        if width <= 0:
            continue
        busy[link] += width
        bits_by_link[link] += float(window.get("served_bits") or 0.0)
        if _finite(window.get("rate_bps")) and float(window["rate_bps"]) > 0:
            rates[link] = float(window["rate_bps"])
    util_busy = [busy[link] / duration for link in sorted(busy) if duration]
    util_bits = [bits_by_link[link] / (rates[link] * duration)
                 for link in sorted(busy) if duration and rates.get(link)]
    ordered = sorted(util_busy)
    p95 = (ordered[min(len(ordered) - 1,
                       int(round(0.95 * (len(ordered) - 1))))]
           if ordered else None)
    offered_packets = metrics.get("offered_packets")
    delivered = fates.get("DELIVERED")
    return {
        "e2e_ms": _quantile_ms(e2e),
        "delivery": {
            "delivered_packets": delivered,
            "offered_packets": offered_packets,
            "pdr": _rate(delivered, offered_packets),
            "pdr_by_horizon": metrics.get("network_delivery_rate_by_horizon"),
            "fate_counts": dict(sorted(fates.items())),
            "delivered_bits": totals.get("delivered_bits"),
            "offered_bits": totals.get("offered_bits"),
            "terminal_loss_bits": totals.get("terminal_loss_bits"),
        },
        "throughput_mbps": ((float(totals.get("delivered_bits") or 0.0)
                             / duration / 1e6) if duration else None),
        "hops": {"mean": _mean(hops),
                 "p50": qstats(hops, points=(0.5,)).get("p50"),
                 "p95": qstats(hops, points=(0.95,)).get("p95"),
                 "max": max(hops) if hops else None, "n": len(hops),
                 "path_stretch": None,
                 "path_stretch_reason": "no shortest-path baseline in evidence"},
        "queue_area_bits_s": {key: queue_area.get(key) for key in
                              ("isl_data", "isl_ctrl", "holding", "uplink",
                               "downlink")},
        "link_util": {"isl_links_with_windows": len(busy),
                      "busy_fraction_max": max(util_busy) if util_busy else None,
                      "busy_fraction_p95": p95,
                      "bits_fraction_max": max(util_bits) if util_bits else None,
                      "duration_s": duration,
                      "definition": DEFINITIONS["util_busy"]},
        "control": {
            "bits": {key: control_bits.get(key) for key in
                     ("offered", "delivered", "terminal_loss", "in_system")},
            "offered_mbits": ((float(control_bits.get("offered") or 0.0) / 1e6)
                              if control_bits else None),
            "counters": dict(sorted(counters.items())),
            "fate_counts": dict(sorted((control.get("fate_counts") or {}).items())),
            "instances": len(control.get("instances") or {}),
            "ctrl_isl_s": occupied.get("ctrl_isl_s"),
            "service_share": (_rate(occupied.get("ctrl_isl_s"), duration)
                              if _finite(occupied.get("ctrl_isl_s")) else None),
            "service_share_method": DEFINITIONS["ctrl_share"],
            "queue_area_bits_s": queue_area.get("isl_ctrl"),
        },
    }


def load_result(cell: Path):
    path = cell / "arms" / "now" / "result.json.gz"
    if not path.is_file():
        return None, {"missing": str(path)}
    try:
        return load_json(path), {}
    except (OSError, json.JSONDecodeError, EOFError) as exc:
        return None, {"missing": str(path), "error": repr(exc)}


def _delivered_e2e(result: dict):
    packets = (result.get("congestion_metrics") or {}).get("packets") or {}
    return {int(pid): float(p["e2e_s"]) for pid, p in packets.items()
            if isinstance(p, dict) and _finite(p.get("e2e_s"))}


# ---------------------------------------------------------------- cell block
def analyze_cell(cell_dir, params):
    cell = Path(cell_dir)
    config = _config(cell)
    manifest = _manifest(cell)
    meta = cell_meta(cell, config, manifest)
    config_policy = (meta.get("policy_params") or {}).get("state_policy")
    trace_map = load_trace_map(cell)
    truth, series, adv_delay, probe_counters = load_probe(cell)
    records, counters, verdicts, decision_counters = load_decisions(
        cell, config_policy, truth, trace_map, series, params)
    stats = decision_stats(records, counters, verdicts)
    stats["probe"] = probe_counters
    stats["aoi_decomposition_ms"]["advertisement_delivery"] = _quantile_ms(
        adv_delay)
    stats["aoi_decomposition_ms"]["advertisement_delivery"]["definition"] = (
        "probe state_received_at_s - state_measured_at_s (advertisement age at "
        "receipt)")

    result, result_problem = load_result(cell)
    warnings = list(meta.get("warnings") or [])
    if result is None:
        warnings.append("result.json.gz unavailable: %s"
                        % result_problem.get("missing"))
        network = {"missing": True, "reason": result_problem}
        delivered = {}
    else:
        duration = float(result.get("stop_time_s")
                         or result.get("horizon_s") or 0.0)
        network = network_stats(result, duration)
        network["natural_end"] = result.get("natural_end")
        network["stop_time_s"] = result.get("stop_time_s")
        network["error"] = result.get("error")
        delivered = _delivered_e2e(result)
    if not records:
        warnings.append("no evaluable decisions: every flip/regret value is null")
    if counters.get("legacy_fallback_records"):
        warnings.append(
            "%d evaluable decisions carried no state_policy block: the legacy "
            "scorer fallback reconstructed J"
            % counters["legacy_fallback_records"])
    if probe_counters.get("missing"):
        warnings.append("target_probe.jsonl unavailable: %s"
                        % probe_counters["missing"])
    stats["scalars"] = _cell_scalars(stats, network)
    block = {
        "cell_id": cell.name,
        "cell_dir": str(cell),
        "meta": {key: meta.get(key) for key in
                 ("cell_id", "family", "series", "regime", "policy",
                  "policy_raw", "policy_known", "id_parsed", "sync_ms", "seed",
                  "trace_source_sha256", "trace_jsonl_sha256", "trace_rows",
                  "trace_key", "config_sha256", "policy_params")},
        "source": {"decisions": decision_counters,
                   "config_sha256": config.get("sha256"),
                   "trace_manifest": manifest},
        "decisions": stats,
        "network": network,
        "warnings": warnings,
    }
    return block, delivered


def _cell_scalars(stats, network):
    def pick(node, *path):
        for token in path:
            if not isinstance(node, dict):
                return None
            node = node.get(token)
        return node

    scalars = {
        "n_decisions_multicandidate": stats["counters"].get(
            "forward_multicandidate"),
        "n_evaluable": stats["n_evaluable"],
        "aoi_p50_ms": pick(stats, "aoi_ms", "p50"),
        "aoi_p90_ms": pick(stats, "aoi_ms", "p90"),
        "aoi_p95_ms": pick(stats, "aoi_ms", "p95"),
        "aoi_p99_ms": pick(stats, "aoi_ms", "p99"),
        "margin_used_p50_ms": pick(stats, "margin_ms", "used", "p50"),
        "margin_truth_p50_ms": pick(stats, "margin_ms", "truth", "p50"),
        "flip_rule_rate": pick(stats, "flip", "rule", "rate"),
        "flip_truth_rate": pick(stats, "flip", "truth_argmin", "rate"),
        "regret_mean_ms": pick(stats, "regret_ms", "mean"),
        "regret_p50_ms": pick(stats, "regret_ms", "p50"),
        "regret_p90_ms": pick(stats, "regret_ms", "p90"),
        "regret_p99_ms": pick(stats, "regret_ms", "p99"),
        "regret_frac_gt0": pick(stats, "regret_ms", "frac_gt_0"),
        "regret_frac_gt1ms": pick(stats, "regret_ms", "frac_gt_1ms"),
        "gamma_median": pick(stats, "gamma", "median"),
        "switch_rate": pick(stats, "switch", "rate"),
        "run_length_mean_decisions": pick(stats, "run_length", "mean"),
        "entropy_mean_bits": pick(stats, "entropy_bits", "mean"),
        "sticky_keep_rate": pick(stats, "sticky", "keep_rate"),
        "sticky_override_rate": pick(stats, "sticky", "override_rate"),
        "near_tie_1ms_frac": pick(stats, "near_tie_frac", "eps_1ms"),
        "near_tie_2ms_frac": pick(stats, "near_tie_frac", "eps_2ms"),
        "near_tie_5ms_frac": pick(stats, "near_tie_frac", "eps_5ms"),
        "e2e_mean_ms": pick(network, "e2e_ms", "mean"),
        "e2e_p50_ms": pick(network, "e2e_ms", "p50"),
        "e2e_p95_ms": pick(network, "e2e_ms", "p95"),
        "e2e_p99_ms": pick(network, "e2e_ms", "p99"),
        "pdr": pick(network, "delivery", "pdr"),
        "throughput_mbps": network.get("throughput_mbps"),
        "hops_mean": pick(network, "hops", "mean"),
        "hops_p95": pick(network, "hops", "p95"),
        "isl_data_area": pick(network, "queue_area_bits_s", "isl_data"),
        "util_busy_max": pick(network, "link_util", "busy_fraction_max"),
        "util_busy_p95": pick(network, "link_util", "busy_fraction_p95"),
        "ctrl_offered_mbits": pick(network, "control", "offered_mbits"),
        "ctrl_share": pick(network, "control", "service_share"),
        "ctrl_expired": pick(network, "control", "counters", "expired"),
        "crosscheck_geometry_mismatch": pick(
            stats, "cross_checks", "geometry_policy_top_mismatch"),
        "crosscheck_audit_identity_violations": pick(
            stats, "cross_checks", "audit_identity_violations"),
    }
    return {key: scalars.get(key) for key in SCALAR_KEYS}


# --------------------------------------------------------------- aggregation
def _cell_sort_key(block):
    meta = block["meta"]
    return (str(meta.get("family")), str(meta.get("series")),
            str(meta.get("regime")), str(meta.get("policy")),
            meta.get("sync_ms") or 0, meta.get("seed") or 0,
            block["cell_id"])


def _family_token(meta):
    """Unparsed ids keep their own slot instead of pooling under None."""
    family = meta.get("family")
    return family if family else "unparsed:%s" % meta.get("cell_id")


def _group_key(block, with_policy=True, with_seed=False):
    meta = block["meta"]
    parts = [_family_token(meta), meta.get("series"), meta.get("regime")]
    if with_policy:
        parts.append(meta.get("policy"))
    parts.append("h%s" % meta.get("sync_ms"))
    if with_seed:
        parts.append("s%s" % meta.get("seed"))
    return "|".join("na" if part in (None, "hNone", "sNone") else str(part)
                    for part in parts)


def _summary(values):
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"n": 0}
    clean.sort()
    return {"n": len(clean), "mean": statistics.fmean(clean),
            "median": statistics.median(clean),
            "min": clean[0], "max": clean[-1]}


def aggregate(blocks):
    groups = defaultdict(list)
    for block in blocks:
        groups[_group_key(block)].append(block)
    out = {}
    for key in sorted(groups):
        members = sorted(groups[key],
                         key=lambda b: (b["meta"].get("seed") is None,
                                        b["meta"].get("seed") or 0,
                                        b["cell_id"]))
        out[key] = {
            "cells": [block["cell_id"] for block in members],
            "seeds": [block["meta"].get("seed") for block in members],
            "family": members[0]["meta"].get("family"),
            "regime": members[0]["meta"].get("regime"),
            "policy": members[0]["meta"].get("policy"),
            "sync_ms": members[0]["meta"].get("sync_ms"),
            "scalars": {name: _summary([block["decisions"]["scalars"].get(name)
                                        for block in members])
                        for name in SCALAR_KEYS},
        }
    return out


def _trace_sha_of(block):
    return block["meta"].get("trace_key")


def paired_policy(blocks, delivered, baseline):
    """Paired E2E delta (policy - baseline) inside one regime/sync/seed."""
    groups = defaultdict(dict)
    for block in blocks:
        groups[_group_key(block, with_policy=False, with_seed=True)][
            block["meta"].get("policy")] = block
    rows, groups_without_baseline = [], 0
    for key in sorted(groups):
        members = groups[key]
        base = members.get(baseline)
        if base is None:
            groups_without_baseline += 1
            continue
        base_e2e = delivered.get(base["cell_id"]) or {}
        for policy in sorted(members):
            if policy == baseline:
                continue
            other = members[policy]
            other_e2e = delivered.get(other["cell_id"]) or {}
            common = sorted(set(base_e2e) & set(other_e2e))
            deltas = [other_e2e[pid] - base_e2e[pid] for pid in common]
            rows.append({
                "group": key,
                "family": other["meta"].get("family"),
                "regime": other["meta"].get("regime"),
                "sync_ms": other["meta"].get("sync_ms"),
                "seed": other["meta"].get("seed"),
                "baseline": baseline,
                "policy": policy,
                "same_trace": _trace_sha_of(other) == _trace_sha_of(base),
                "paired_packets": len(common),
                "delta_mean_ms": _ms(_mean(deltas)),
                "delta_p50_ms": _ms(qstats(deltas, points=(0.5,)).get("p50")),
                "delta_p95_ms": _ms(qstats(deltas, points=(0.95,)).get("p95")),
                "faster": sum(1 for value in deltas if value < -1e-12),
                "slower": sum(1 for value in deltas if value > 1e-12),
                "baseline_e2e_mean_ms": base["decisions"]["scalars"]
                .get("e2e_mean_ms"),
                "policy_e2e_mean_ms": other["decisions"]["scalars"]
                .get("e2e_mean_ms"),
                "policy_flip_rule_rate": other["decisions"]["scalars"]
                .get("flip_rule_rate"),
                "policy_regret_mean_ms": other["decisions"]["scalars"]
                .get("regret_mean_ms"),
            })
    return rows, groups_without_baseline


def sync_deltas(blocks):
    """Delta (fast sync - slow sync) for the paired sync axis, per seed."""
    groups = defaultdict(dict)
    for block in blocks:
        key = (block["meta"].get("family"), block["meta"].get("series"),
               block["meta"].get("regime"), block["meta"].get("policy"),
               block["meta"].get("seed"))
        groups[key][block["meta"].get("sync_ms")] = block
    rows = []
    for key in sorted(groups, key=lambda k: tuple(map(str, k))):
        members = groups[key]
        if len(members) < 2:
            continue
        syncs = sorted(members)
        slow, fast = syncs[0], syncs[-1]
        slow_block, fast_block = members[slow], members[fast]
        deltas = {}
        for name in SYNC_DELTA_KEYS:
            before = slow_block["decisions"]["scalars"].get(name)
            after = fast_block["decisions"]["scalars"].get(name)
            deltas[name] = (None if before is None or after is None
                            else float(after) - float(before))
        rows.append({
            "family": key[0], "series": key[1], "regime": key[2],
            "policy": key[3], "seed": key[4],
            "slow_sync_ms": slow, "fast_sync_ms": fast,
            "same_trace": _trace_sha_of(slow_block) == _trace_sha_of(fast_block),
            "slow_cell": slow_block["cell_id"],
            "fast_cell": fast_block["cell_id"],
            "deltas": deltas,
        })
    return rows


def _average_deltas(rows, group_keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in group_keys)].append(row)
    out = {}
    for key in sorted(groups, key=lambda k: tuple(map(str, k))):
        members = groups[key]
        entry = {"n_pairs": len(members),
                 "seeds": [member.get("seed") for member in members],
                 "same_trace": all(member.get("same_trace") for member in members)}
        for name in SYNC_DELTA_KEYS:
            entry[name] = _summary([member["deltas"].get(name)
                                    for member in members])
        out["|".join("na" if token is None else str(token) for token in key)] = entry
    return out


def response_sensitivity(aggregates):
    """Behavioural proxy axes -> outcome table (not a strict sensitivity)."""
    rows = []
    for key in sorted(aggregates):
        group = aggregates[key]
        scalars = group["scalars"]

        def mean(name):
            return (scalars.get(name) or {}).get("mean")

        rows.append({
            "group": key,
            "family": group.get("family"),
            "regime": group.get("regime"),
            "policy": group.get("policy"),
            "sync_ms": group.get("sync_ms"),
            "n_cells": len(group["cells"]),
            "n_evaluable_mean": mean("n_evaluable"),
            "switch_rate": mean("switch_rate"),
            "run_length_mean_decisions": mean("run_length_mean_decisions"),
            "flip_rule_rate": mean("flip_rule_rate"),
            "flip_truth_rate": mean("flip_truth_rate"),
            "entropy_mean_bits": mean("entropy_mean_bits"),
            "near_tie_1ms_frac": mean("near_tie_1ms_frac"),
            "near_tie_5ms_frac": mean("near_tie_5ms_frac"),
            "e2e_mean_ms": mean("e2e_mean_ms"),
            "e2e_p99_ms": mean("e2e_p99_ms"),
        })
    return {"note": DEFINITIONS["response_sensitivity_axes"], "rows": rows}


def freshness_stability(sync_rows):
    """1 s -> 20 ms trade-off per (family, regime, policy), averaged over seeds."""
    averaged = _average_deltas(
        sync_rows, group_keys=("family", "series", "regime", "policy"))
    table = []
    for key in sorted(averaged):
        entry = averaged[key]

        def mean(name):
            return (entry.get(name) or {}).get("mean")

        table.append({
            "group": key, "n_pairs": entry["n_pairs"], "seeds": entry["seeds"],
            "same_trace": entry["same_trace"],
            "delta_aoi_p50_ms": mean("aoi_p50_ms"),
            "delta_aoi_p90_ms": mean("aoi_p90_ms"),
            "delta_flip_rule": mean("flip_rule_rate"),
            "delta_flip_truth": mean("flip_truth_rate"),
            "delta_switch": mean("switch_rate"),
            "delta_e2e_mean_ms": mean("e2e_mean_ms"),
            "delta_e2e_p99_ms": mean("e2e_p99_ms"),
            "delta_ctrl_offered_mbits": mean("ctrl_offered_mbits"),
        })
    return {"note": ("delta = fast sync (smaller advertisement interval) - slow "
                     "sync, same family/regime/policy/seed, then mean over "
                     "seeds; paired by construction, no re-simulation."),
            "table": table}


def collect_gaps(blocks, aggregates):
    def cell_ids(predicate):
        return [block["cell_id"] for block in blocks if predicate(block)]

    return {
        "cells_without_result_json": cell_ids(
            lambda b: b["network"].get("missing")),
        "cells_without_evaluable_decisions": cell_ids(
            lambda b: b["decisions"]["n_evaluable"] == 0),
        "cells_without_action_entropy_audit": cell_ids(
            lambda b: b["decisions"]["entropy_bits"]["n"] == 0),
        "cells_without_sticky_audit": cell_ids(
            lambda b: b["decisions"]["sticky"]["n_with_audit"] == 0),
        "cells_without_gamma_samples": cell_ids(
            lambda b: b["decisions"]["gamma"]["n"] == 0),
        "cells_using_legacy_fallback": cell_ids(
            lambda b: b["decisions"]["legacy_fallback_records"] > 0),
        "cells_with_id_parse_warnings": cell_ids(
            lambda b: any("!=" in warning for warning in b["warnings"])),
        "cells_with_unparsed_id": cell_ids(
            lambda b: not b["meta"].get("id_parsed")),
        "metrics_not_in_evidence": [
            "action entropy: only queue_softmin audits expose "
            "selection.probabilities/entropy",
            "sticky keep/override: only queue_greedy_sticky audits expose them",
            "path stretch: no shortest-path baseline in the evidence (hops only)",
            "window-summed control service time: link_service_windows carry no "
            "kind=='ctrl' marker, so occupied.ctrl_isl_s / stop_time is used",
            "control packet delay: control.instances[3] is an absolute "
            "received_at, so advertisement age is taken from the probe "
            "(state_received_at_s - state_measured_at_s)",
            "Gamma needs >=3 truth samples inside +-0.25 s on BOTH resources",
        ],
        "aggregate_groups": len(aggregates),
        "note": ("an empty list means the metric is available for every cell; "
                 "entries are cell ids"),
    }


# ------------------------------------------------------------------ markdown
def _fmt(value, spec="%.3f"):
    if value is None:
        return "n/a"
    try:
        return spec % float(value)
    except (TypeError, ValueError):
        return "n/a"


def render_cell_md(report):
    block = report["cell"]
    dec, net = block["decisions"], block["network"]
    lines = ["# Round-3 cell report — %s" % block["cell_id"], "",
             "- policy: `%s` (family %s), sync %s ms, regime %s, seed %s"
             % (block["meta"].get("policy"), block["meta"].get("family"),
                block["meta"].get("sync_ms"), block["meta"].get("regime"),
                block["meta"].get("seed")),
             "- state_policy params: `%s`"
             % json.dumps(block["meta"].get("policy_params"),
                          sort_keys=True),
             "- config sha256: `%s`" % block["meta"].get("config_sha256"),
             "- trace source sha256: `%s`"
             % block["meta"].get("trace_source_sha256"),
             "- flip semantics: `%s`" % FLIP_SEMANTICS,
             "- coverage: %s rows, %s forward, %s multi-candidate, %s evaluable"
             " (%s of multi-candidate)"
             % (dec["counters"].get("rows"), dec["counters"].get("forward"),
                dec["counters"].get("forward_multicandidate"),
                dec["n_evaluable"],
                _fmt(dec["coverage"]["evaluable_fraction_of_multicandidate"],
                     "%.4f")),
             ""]
    lines += ["| metric | value |", "|---|---|",
              "| AoI tau p50 / p90 / p95 / p99 ms | %s / %s / %s / %s |"
              % (_fmt(dec["aoi_ms"]["p50"], "%.2f"),
                 _fmt(dec["aoi_ms"]["p90"], "%.2f"),
                 _fmt(dec["aoi_ms"]["p95"], "%.2f"),
                 _fmt(dec["aoi_ms"]["p99"], "%.2f")),
              "| gen->recv / recv->decision p50 ms | %s / %s |"
              % (_fmt(dec["aoi_decomposition_ms"]["gen_to_recv"]["p50"], "%.3f"),
                 _fmt(dec["aoi_decomposition_ms"]["recv_to_decision"]["p50"],
                      "%.3f")),
              "| margin used / truth p50 ms | %s / %s |"
              % (_fmt(dec["margin_ms"]["used"]["p50"], "%.3f"),
                 _fmt(dec["margin_ms"]["truth"]["p50"], "%.3f")),
              "| flip rule / truth-argmin | %s / %s |"
              % (_fmt(dec["flip"]["rule"]["rate"], "%.4f"),
                 _fmt(dec["flip"]["truth_argmin"]["rate"], "%.4f")),
              "| proxy regret mean / p90 / p99 ms | %s / %s / %s |"
              % (_fmt(dec["regret_ms"]["mean"], "%.3f"),
                 _fmt(dec["regret_ms"]["p90"], "%.3f"),
                 _fmt(dec["regret_ms"]["p99"], "%.3f")),
              "| switch rate / mean run length | %s / %s |"
              % (_fmt(dec["switch"]["rate"], "%.4f"),
                 _fmt(dec["run_length"]["mean"], "%.2f")),
              "| entropy bits (n) | %s (%s) |"
              % (_fmt(dec["entropy_bits"]["mean"], "%.3f"),
                 dec["entropy_bits"]["n"]),
              "| sticky keep / override | %s / %s |"
              % (_fmt(dec["sticky"]["keep_rate"], "%.4f"),
                 _fmt(dec["sticky"]["override_rate"], "%.4f")),
              "| W p50 / p90 ms (censored frac) | %s / %s (%s) |"
              % (_fmt(dec["wrong_duration_W_ms"]["rule"]["median_ms"], "%.1f"),
                 _fmt(dec["wrong_duration_W_ms"]["rule"]["p90_ms"], "%.1f"),
                 _fmt(dec["wrong_duration_W_ms"]["rule"]["censored_frac"],
                      "%.3f")),
              "| E2E mean / p95 / p99 ms | %s / %s / %s |"
              % (_fmt(net.get("e2e_ms", {}).get("mean"), "%.2f"),
                 _fmt(net.get("e2e_ms", {}).get("p95"), "%.2f"),
                 _fmt(net.get("e2e_ms", {}).get("p99"), "%.2f")),
              "| PDR / throughput Mbps | %s / %s |"
              % (_fmt(net.get("delivery", {}).get("pdr"), "%.5f"),
                 _fmt(net.get("throughput_mbps"), "%.2f")),
              "| hops mean / p95 | %s / %s |"
              % (_fmt(net.get("hops", {}).get("mean"), "%.2f"),
                 _fmt(net.get("hops", {}).get("p95"), "%.1f")),
              "| isl_data area / util max | %s / %s |"
              % (_fmt((net.get("queue_area_bits_s") or {}).get("isl_data"),
                      "%.0f"),
                 _fmt(net.get("link_util", {}).get("busy_fraction_max"),
                      "%.3f")),
              "| control Mbit / share | %s / %s |"
              % (_fmt(net.get("control", {}).get("offered_mbits"), "%.2f"),
                 _fmt(net.get("control", {}).get("service_share"), "%.4f")),
              ""]
    lines += ["## Gamma bins (flip rates)", "",
              "| bin | n | flip(rule) | flip(truth) |", "|---|---|---|---|"]
    for row in dec["gamma"]["bins"]:
        lines.append("| %s | %d | %s | %s |"
                     % (row["label"], row["n"],
                        _fmt(row["flip_rule"]["rate"], "%.3f"),
                        _fmt(row["flip_truth"]["rate"], "%.3f")))
    if block["warnings"]:
        lines += ["", "## Warnings", ""] + ["- %s" % warning
                                            for warning in block["warnings"]]
    return "\n".join(lines) + "\n"


def render_markdown(report):
    blocks = list(report["cells"].values())
    lines = ["# Round-3 six-policy matrix — behavioural analysis", "",
             "Schema `%s`. %d cells." % (report["schema"], len(blocks)), "",
             "- **flip semantics**: `%s` — a truth-substituted re-ranking of "
             "the same decision row, **not** a re-simulated counterfactual."
             % FLIP_SEMANTICS,
             "- geometry is the negative control: its J carries no state term, "
             "so `flip` and `regret` must be 0 on the evaluable subset.",
             "- `W` is a consecutive-wrong-decision duration; it is not packet "
             "queueing time.",
             "- response-sensitivity columns are **behavioural proxies**, not a "
             "strict mathematical sensitivity.",
             "- paired deltas only compare cells sharing "
             "`trace-manifest:source_trace_sha256`.",
             ""]
    lines += ["## Per-cell metrics", "",
              "| cell | policy | sync ms | seed | multi | eval | AoI p50 | "
              "AoI p90 | m p50 | flip rule | flip truth | R mean | R p90 | "
              "switch | run | E2E mean | E2E p95 | E2E p99 | PDR | hops | "
              "isl_data | util max | ctrl Mbit | ctrl share |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---"
              "|---|---|---|---|"]
    for block in sorted(blocks, key=_cell_sort_key):
        scalars = block["decisions"]["scalars"]
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s "
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (block["cell_id"], block["meta"].get("policy"),
               block["meta"].get("sync_ms"), block["meta"].get("seed"),
               scalars.get("n_decisions_multicandidate"),
               scalars.get("n_evaluable"),
               _fmt(scalars.get("aoi_p50_ms"), "%.1f"),
               _fmt(scalars.get("aoi_p90_ms"), "%.1f"),
               _fmt(scalars.get("margin_used_p50_ms"), "%.2f"),
               _fmt(scalars.get("flip_rule_rate"), "%.4f"),
               _fmt(scalars.get("flip_truth_rate"), "%.4f"),
               _fmt(scalars.get("regret_mean_ms"), "%.2f"),
               _fmt(scalars.get("regret_p90_ms"), "%.2f"),
               _fmt(scalars.get("switch_rate"), "%.3f"),
               _fmt(scalars.get("run_length_mean_decisions"), "%.2f"),
               _fmt(scalars.get("e2e_mean_ms"), "%.1f"),
               _fmt(scalars.get("e2e_p95_ms"), "%.1f"),
               _fmt(scalars.get("e2e_p99_ms"), "%.1f"),
               _fmt(scalars.get("pdr"), "%.5f"),
               _fmt(scalars.get("hops_mean"), "%.2f"),
               _fmt(scalars.get("isl_data_area"), "%.0f"),
               _fmt(scalars.get("util_busy_max"), "%.3f"),
               _fmt(scalars.get("ctrl_offered_mbits"), "%.1f"),
               _fmt(scalars.get("ctrl_share"), "%.4f")))

    lines += ["", "## Policy x regime x sync aggregates (mean [min..max] over seeds)",
              "",
              "| group | cells | flip rule | flip truth | R mean ms | AoI p50 | "
              "AoI p90 | switch | run | E2E mean | E2E p99 | PDR |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(report["aggregates"]):
        group = report["aggregates"][key]
        scalars = group["scalars"]

        def agg(name, spec):
            summary = scalars.get(name) or {}
            if not summary.get("n"):
                return "n/a"
            return (spec + " [" + spec + ".." + spec + "]") % (
                summary["mean"], summary["min"], summary["max"])

        lines.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s "
                     "| %s |"
                     % (key, len(group["cells"]),
                        agg("flip_rule_rate", "%.4f"),
                        agg("flip_truth_rate", "%.4f"),
                        agg("regret_mean_ms", "%.2f"),
                        agg("aoi_p50_ms", "%.1f"),
                        agg("aoi_p90_ms", "%.1f"),
                        agg("switch_rate", "%.3f"),
                        agg("run_length_mean_decisions", "%.2f"),
                        agg("e2e_mean_ms", "%.1f"),
                        agg("e2e_p99_ms", "%.1f"),
                        agg("pdr", "%.5f")))

    bad_geometry = [block["cell_id"] for block in blocks
                    if (block["decisions"]["cross_checks"]
                        ["geometry_policy_top_mismatch"])]
    bad_identity = [block["cell_id"] for block in blocks
                    if (block["decisions"]["cross_checks"]
                        ["audit_identity_violations"])]
    lines += ["", "## Consistency cross-checks", "",
              "- geometry `chosen != recorded policy top` (must be 0): %s"
              % (bad_geometry or "0 in every cell"),
              "- audit identity `resource_work == q_used_bits / rate` violations "
              "(must be 0): %s" % (bad_identity or "0 in every cell"),
              "- `chosen != used-state argmin` is expected only for "
              "hysteresis/sticky/softmin; per cell values are in the JSON."]
    mismatched = [(block["cell_id"],
                   block["decisions"]["cross_checks"]["chosen_not_used_argmin"])
                  for block in blocks
                  if block["decisions"]["cross_checks"]
                  ["chosen_not_used_argmin"]]
    if mismatched:
        lines.append("- cells whose committed action differed from the "
                     "used-state argmin: %s"
                     % ", ".join("`%s` (%d)" % item for item in mismatched))

    primary = report.get("gamma_primary_group") or {}
    primary_cells = primary.get("cells") or []
    lines += ["", "## Gamma bins — flip rate (largest regime/sync group)", ""]
    if primary_cells:
        lines += ["Cells: %s (full per-cell bins in the JSON)."
                  % ", ".join("`%s`" % cell for cell in primary_cells), "",
                  "| cell | " + " | ".join(label for label, _, _ in GAMMA_BINS)
                  + " |", "|---" * (len(GAMMA_BINS) + 1) + "|"]
        for block in sorted(blocks, key=_cell_sort_key):
            if block["cell_id"] not in primary_cells:
                continue
            cells = ["%s (%d)" % (_fmt(row["flip_rule"]["rate"], "%.3f"),
                                  row["n"])
                     for row in block["decisions"]["gamma"]["bins"]]
            lines.append("| %s | %s |" % (block["cell_id"], " | ".join(cells)))
    else:
        lines.append("(no cells)")

    paired = report.get("paired_policy") or []
    if paired:
        lines += ["", "## Paired E2E inside one trace (delta = policy - %s)"
                  % report["parameters"].get("baseline"), "",
                  "| group | policy | same trace | paired | mean delta ms | "
                  "p50 | p95 | faster | slower |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for row in paired:
            lines.append("| %s | %s | %s | %d | %s | %s | %s | %d | %d |"
                         % (row["group"], row["policy"], row["same_trace"],
                            row["paired_packets"],
                            _fmt(row["delta_mean_ms"], "%.3f"),
                            _fmt(row["delta_p50_ms"], "%.3f"),
                            _fmt(row["delta_p95_ms"], "%.3f"),
                            row["faster"], row["slower"]))

    sync_table = (report.get("freshness_stability") or {}).get("table") or []
    if sync_table:
        lines += ["", "## Freshness vs stability: delta(fast sync - slow sync)",
                  "",
                  "| family|regime|policy | pairs | seeds | dAoI p50 | "
                  "dAoI p90 | dflip rule | dswitch | dE2E mean | dE2E p99 | "
                  "same trace |", "|---|---|---|---|---|---|---|---|---|---|"]
        for row in sync_table:
            lines.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s |"
                         % (row["group"], row["n_pairs"], row["seeds"],
                            _fmt(row["delta_aoi_p50_ms"], "%.1f"),
                            _fmt(row["delta_aoi_p90_ms"], "%.1f"),
                            _fmt(row["delta_flip_rule"], "%.4f"),
                            _fmt(row["delta_switch"], "%.3f"),
                            _fmt(row["delta_e2e_mean_ms"], "%.2f"),
                            _fmt(row["delta_e2e_p99_ms"], "%.2f"),
                            row["same_trace"]))

    sensitivity = report.get("response_sensitivity") or {}
    sensitivity_rows = sensitivity.get("rows") or []
    if sensitivity_rows:
        lines += ["", "## Response sensitivity (behavioural proxy)", "",
                  "| group | policy | sync | switch | run | flip rule | flip "
                  "truth | entropy | near-tie 1ms | E2E mean | E2E p99 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for row in sensitivity_rows:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                         % (row["group"], row["policy"], row["sync_ms"],
                            _fmt(row["switch_rate"], "%.3f"),
                            _fmt(row["run_length_mean_decisions"], "%.2f"),
                            _fmt(row["flip_rule_rate"], "%.4f"),
                            _fmt(row["flip_truth_rate"], "%.4f"),
                            _fmt(row["entropy_mean_bits"], "%.3f"),
                            _fmt(row["near_tie_1ms_frac"], "%.3f"),
                            _fmt(row["e2e_mean_ms"], "%.1f"),
                            _fmt(row["e2e_p99_ms"], "%.1f")))
        lines += ["", "_%s_" % sensitivity.get("note")]

    gaps = report.get("gaps") or {}
    lines += ["", "## Metric gaps and known limitations", "",
              "- cells without evaluable decisions: %s"
              % (gaps.get("cells_without_evaluable_decisions") or "none"),
              "- cells without an action-entropy audit: %d of %d"
              % (len(gaps.get("cells_without_action_entropy_audit") or []),
                 len(blocks)),
              "- cells without a sticky audit: %d of %d"
              % (len(gaps.get("cells_without_sticky_audit") or []), len(blocks)),
              "- cells with no Gamma samples: %d of %d"
              % (len(gaps.get("cells_without_gamma_samples") or []), len(blocks)),
              "- cells using the legacy scorer fallback: %s"
              % (gaps.get("cells_using_legacy_fallback") or "none"),
              "- cells whose id disagrees with the config: %s"
              % (gaps.get("cells_with_id_parse_warnings") or "none"),
              "- cells whose id could not be parsed: %s"
              % (gaps.get("cells_with_unparsed_id") or "none")]
    for note in gaps.get("metrics_not_in_evidence") or []:
        lines.append("- %s" % note)
    if gaps.get("duplicate_cell_slots"):
        lines.append("- duplicate experiment slots: %s"
                     % json.dumps(gaps["duplicate_cell_slots"]))
    if report.get("paired_policy_baseline_missing"):
        lines.append("- paired groups without the baseline policy: %d"
                     % report["paired_policy_baseline_missing"])
    warnings = report.get("warnings") or []
    if warnings:
        lines += ["", "### Run-level warnings", ""] + ["- %s" % warning
                                                       for warning in warnings]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- build
def _assemble(blocks, delivered, params, warnings):
    aggregates = aggregate(blocks)
    sync_rows = sync_deltas(blocks)
    paired_rows, paired_missing = paired_policy(blocks, delivered,
                                                params.get("baseline"))
    duplicates = _duplicate_keys(blocks)
    if duplicates:
        warnings = warnings + [
            "duplicate experiment slots (same family|series|regime|policy|"
            "sync|seed): %s" % json.dumps(duplicates)]
    gaps = collect_gaps(blocks, aggregates)
    gaps["duplicate_cell_slots"] = duplicates
    return {
        "schema": SCHEMA,
        "generator": "ANALYSIS/SIM-QUAL-20261031/sq_round3_analyze.py",
        "parameters": dict(params),
        "definitions": DEFINITIONS,
        "cells": {block["cell_id"]: block for block in blocks},
        "aggregates": aggregates,
        "paired_policy": paired_rows,
        "paired_policy_baseline_missing": paired_missing,
        "sync_delta_rows": sync_rows,
        "freshness_stability": freshness_stability(sync_rows),
        "response_sensitivity": response_sensitivity(aggregates),
        "gaps": gaps,
        "gamma_primary_group": _primary_group(blocks),
        "warnings": warnings,
    }


def build_matrix(cell_dirs, params):
    blocks, delivered, warnings = [], {}, []
    for path in cell_dirs:
        cell = resolve_cell_dir(Path(path))
        if not (cell / "arms" / "now" / "decisions.jsonl.gz").is_file():
            warnings.append("skipped (no decisions.jsonl.gz): %s" % path)
            continue
        block, e2e = analyze_cell(cell, params)
        blocks.append(block)
        delivered[block["cell_id"]] = e2e
    blocks.sort(key=_cell_sort_key)
    return _assemble(blocks, delivered, params, warnings)


def build_cell(cell_dir, params):
    cell = resolve_cell_dir(Path(cell_dir))
    if not (cell / "arms" / "now" / "decisions.jsonl.gz").is_file():
        raise SystemExit("not a cell dir (missing arms/now/decisions.jsonl.gz): %s"
                         % cell)
    block, _ = analyze_cell(cell, params)
    return {
        "schema": SCHEMA,
        "generator": "ANALYSIS/SIM-QUAL-20261031/sq_round3_analyze.py",
        "parameters": dict(params),
        "definitions": DEFINITIONS,
        "cell": block,
        "gaps": collect_gaps([block], {}),
        "warnings": list(block.get("warnings") or []),
    }


def _duplicate_keys(blocks):
    """Cells that claim the same experiment slot (family|series|regime|policy|
    sync|seed).  Round-3 has exactly one cell per slot; a non-empty result means
    the input list mixed cells that are not independent replicates."""
    slots = defaultdict(list)
    for block in blocks:
        meta = block["meta"]
        key = "|".join(str(token) for token in (
            _family_token(meta), meta.get("series"), meta.get("regime"),
            meta.get("policy"), meta.get("sync_ms"), meta.get("seed")))
        slots[key].append(block["cell_id"])
    return {key: sorted(cells) for key, cells in sorted(slots.items())
            if len(cells) > 1}


def _primary_group(blocks):
    """Largest (family, regime, sync) group; used for the MD Gamma section."""
    groups = defaultdict(list)
    for block in blocks:
        meta = block["meta"]
        groups[(meta.get("family"), meta.get("series"), meta.get("regime"),
                meta.get("sync_ms"))].append(block["cell_id"])
    if not groups:
        return {"cells": [], "group": None}
    key = sorted(groups, key=lambda token: (-len(groups[token]),
                                            tuple(map(str, token))))[0]
    return {"group": [str(part) for part in key],
            "cells": sorted(groups[key])}


def _parallel_cell(path, params):
    cell = resolve_cell_dir(Path(path))
    if not (cell / "arms" / "now" / "decisions.jsonl.gz").is_file():
        return None, None
    block, delivered = analyze_cell(cell, params)
    return block, delivered


def _matrix_parallel(paths, params, jobs):
    from concurrent.futures import ProcessPoolExecutor
    workers = max(1, min(int(jobs or 1), len(paths)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_parallel_cell,
                                [str(path) for path in paths],
                                [params] * len(paths)))
    blocks, delivered, warnings = [], {}, []
    for path, (block, e2e) in zip(paths, results):
        if block is None:
            warnings.append("skipped (no decisions.jsonl.gz): %s" % path)
            continue
        blocks.append(block)
        delivered[block["cell_id"]] = e2e
    blocks.sort(key=_cell_sort_key)
    return _assemble(blocks, delivered, params, warnings)


# ----------------------------------------------------------------------- cli
def _default_params(args):
    return {
        "baseline": args.baseline,
        "flip_semantics": FLIP_SEMANTICS,
        "hysteresis_eta_s": 0.0,
        "softmin_temperature_s": None,
        "gamma_half_window_s": 0.25,
        "near_tie_eps_s": list(NEAR_TIE_EPS_S),
    }


def _expand_cells(args):
    paths = [Path(item) for item in args.cells]
    if args.cells_file:
        text = Path(args.cells_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                paths.append(Path(line.strip()))
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Round-3 six-policy stale-state matrix analysis")
    parser.add_argument("command", choices=["cell", "matrix"])
    parser.add_argument("cells", nargs="*", help="cell dirs (or run dirs)")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--md", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--baseline", default=QUEUE_GREEDY,
                        help="baseline policy for paired E2E deltas")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--cells-file", default=None)
    args = parser.parse_args(argv)
    params = _default_params(args)
    paths = _expand_cells(args)
    if not paths:
        parser.error("no cell directories given")
    if args.command == "cell":
        report = build_cell(paths[0], params)
    elif args.jobs > 1 and len(paths) > 1:
        report = _matrix_parallel(paths, params, args.jobs)
    else:
        report = build_matrix(paths, params)
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        stem = "round3-cell" if args.command == "cell" else "round3-matrix"
        if args.json is None:
            args.json = args.out_dir / (stem + ".json")
        if args.md is None:
            args.md = args.out_dir / (stem + ".md")
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text, encoding="utf-8")
    if args.md is not None:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        markdown = (render_cell_md(report) if "cell" in report
                    else render_markdown(report))
        args.md.write_text(markdown, encoding="utf-8")
    if args.json is None and args.md is None:
        print(text)
    cells = report.get("cells") or ([report["cell"]] if "cell" in report else [])
    summary = {"schema": SCHEMA, "cells": len(cells),
               "aggregate_groups": len(report.get("aggregates") or {}),
               "paired_rows": len(report.get("paired_policy") or []),
               "sync_pairs": len(report.get("sync_delta_rows") or []),
               "json": str(args.json) if args.json else None,
               "md": str(args.md) if args.md else None}
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
