"""Collect PCVR-0 training rows from paired same-world action forks.

For each selected forward decision, the collector:
1. runs the baseline once with a capture policy that exactly follows the
   time-alignment scorer's first-ranked action;
2. forces each alternative legal action at that one decision in a fresh replay;
3. verifies the pre-branch fingerprint is identical;
4. lets every later node continue reacting under the same baseline policy;
5. labels each action with whole-offered-cohort outcomes.

The collector refuses interrupted runs, non-frozen observations, nonzero
compute delay, policy-plugin stacking, and unpaired branch points.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict, deque
from pathlib import Path
import tempfile

from CODE.algorithms.pcvr0.policy import extract_snapshot_features
from CODE.leo_sim import config as config_mod
from CODE.leo_sim import counterfactual, kernel, time_alignment as ta, trace as trace_mod

DATA_SCHEMA = "pcvr0-training-row/v1"


class CollectionError(RuntimeError):
    """A paired PCVR-0 data collection run could not be certified."""


class FeatureCapturePolicy:
    """Inference-only baseline adapter that records the exact local snapshot."""

    inference_only = True
    name = "pcvr0-feature-capture"

    def __init__(self):
        self.records = []
        self.calls = 0

    def act(self, snapshot):
        features, baseline = extract_snapshot_features(snapshot)
        self.records.append({
            "satellite": int(snapshot.satellite),
            "snapshot_at": float(snapshot.snapshot_at),
            "legal_directions": list(snapshot.legal_directions),
            "features": features,
            "baseline_action": baseline,
        })
        self.calls += 1
        return baseline

    def counters(self):
        return {
            "name": self.name,
            "calls": self.calls,
            "training": False,
            "updates": 0,
        }


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _nearest_rank(values, q=0.99):
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise CollectionError("no delivered E2E samples; cannot label p99/mean")
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def cohort_outcome(result: dict, offered_packets: int) -> dict:
    """Compute fixed-offered-cohort metrics; never condition PDR on ingress."""
    if result.get("natural_end") is not True or result.get("interrupted") is True:
        raise CollectionError(
            "counterfactual run did not reach natural_end; interrupted runs are not labels")
    fates = result.get("fates")
    if not isinstance(fates, dict) or len(fates) != offered_packets:
        raise CollectionError(
            "fate ledger does not cover the complete offered cohort")
    fate_counts = result.get("fate_counts") or {}
    delivered = int(fate_counts.get("DELIVERED", 0))
    if delivered < 1:
        raise CollectionError("at least one delivered packet is required for p99/mean labels")
    packet_rows = (result.get("congestion_metrics") or {}).get("packets") or {}
    delays = [
        float(packet["e2e_s"])
        for packet in packet_rows.values()
        if isinstance(packet, dict) and packet.get("e2e_s") is not None
    ]
    if len(delays) != delivered:
        raise CollectionError(
            f"delivered fate count {delivered} != reconstructed E2E count {len(delays)}")
    control = result.get("control") or {}
    counters = control.get("counters") or {}
    control_packets = counters.get("entered_queue")
    if isinstance(control_packets, bool) or not isinstance(control_packets, int):
        raise CollectionError("control entered_queue counter is unavailable")
    return {
        "delivery_loss": 1.0 - delivered / float(offered_packets),
        "p99_e2e_s": _nearest_rank(delays, 0.99),
        "mean_e2e_s": sum(delays) / len(delays),
        "control_packets": float(control_packets),
    }


def _policy_id(config: dict) -> str:
    """Bind a policy profile while excluding world-specific seed and demand."""
    payload = {
        "routing": config["routing"],
        "time_alignment": config["time_alignment"],
        "decision_execution": {
            key: config["execution"].get(key)
            for key in ("compute_delay_s", "decision_observation_mode",
                        "compute_servers_per_satellite")
        },
    }
    return _sha(_canonical(payload).encode("utf-8"))


def _match_capture_records(sink, records):
    """Pair captures with committed rows by (satellite, frozen snapshot time)."""
    queues = defaultdict(deque)
    for record in records:
        key = (record["satellite"], round(record["snapshot_at"], 12))
        queues[key].append(record)
    matched = []
    for row in sink:
        observation = row.get("observation_at_start") or {}
        time_audit = observation.get("time_alignment")
        if not isinstance(time_audit, dict):
            continue
        key = (int(row["sat"]), round(float(time_audit["snapshot_at"]), 12))
        if not queues[key]:
            continue
        capture = queues[key].popleft()
        if int(capture["satellite"]) != int(row["sat"]):
            continue
        if abs(float(capture["snapshot_at"]) - float(time_audit["snapshot_at"])) > 1e-9:
            continue
        if row.get("chosen") != capture["baseline_action"]:
            # The kernel may apply a later legality gate. Such a row is not a
            # clean baseline action sample for this first prototype.
            continue
        candidates = [
            str(d) for d in (row.get("candidates") or ())
            if str(d) in capture["features"]
        ]
        if len(candidates) < 2 or row.get("chosen") not in candidates:
            continue
        matched.append((row, capture, candidates))
    return matched


def _load_world(config_path: Path, work_dir: Path):
    resolved = config_mod.load_config_file(str(config_path))
    config = resolved["config"]
    if config["learning"]["algorithm"] != "none":
        raise CollectionError("PCVR-0 collection requires learning.algorithm=none")
    if not config["time_alignment"]["enabled"]:
        raise CollectionError("PCVR-0 collection requires time_alignment.enabled=true")
    if config["routing"].get("policy_plugin") is not None:
        raise CollectionError("do not stack another policy_plugin during PCVR-0 collection")
    if config["execution"].get("decision_observation_mode") != "frozen":
        raise CollectionError("PCVR-0 collection requires frozen decision observations")
    if float(config["execution"].get("compute_delay_s", 0.0)) != 0.0:
        raise CollectionError(
            "first PCVR-0 collector requires compute_delay_s=0 for exact snapshot/commit pairing")
    trace_manifest = trace_mod.compile_trace(resolved, str(work_dir))
    trace_path = work_dir / "trace.csv"
    rows = trace_mod.load_trace(
        str(trace_path),
        horizon_s=trace_manifest["emission_end_s"],
        max_packets=config["execution"]["max_packets"],
    )
    trace_sha = trace_manifest.get("__trace_sha256") or trace_manifest.get("trace_sha256")
    if not trace_sha:
        trace_sha = _sha(trace_path.read_bytes())
    return resolved, rows, str(trace_sha)


def collect(config_path: Path, out_path: Path, *, max_decisions: int = 8,
            decision_stride: int = 1) -> dict:
    if out_path.exists():
        raise CollectionError(f"output already exists: {out_path}")
    if max_decisions < 1 or decision_stride < 1:
        raise CollectionError("max_decisions and decision_stride must be positive")
    if not out_path.parent.is_dir():
        raise CollectionError(f"output parent directory does not exist: {out_path.parent}")

    with tempfile.TemporaryDirectory(prefix="pcvr0-world-") as temp:
        work_dir = Path(temp)
        resolved, rows, trace_sha = _load_world(config_path, work_dir)
        if not rows:
            raise CollectionError("compiled trace has no offered packets")
        config = resolved["config"]
        capture = FeatureCapturePolicy()
        baseline_sink = []
        baseline_result = kernel.run_simulation(
            resolved, rows, decision_sink=baseline_sink,
            inference_policy=capture,
        )
        baseline_outcome = cohort_outcome(baseline_result, len(rows))
        matched = _match_capture_records(baseline_sink, capture.records)
        if not matched:
            raise CollectionError(
                "no committed decisions could be paired with frozen feature snapshots")

        world_id = _sha((str(resolved["sha256"]) + "|" + trace_sha).encode("utf-8"))
        cohort_id = trace_sha
        policy_id = _policy_id(config)
        selected = matched[::decision_stride][:max_decisions]
        output_rows = []
        accepted = 0
        rejected = []

        for decision_row, capture_row, candidates in selected:
            decision_id = int(decision_row["decision_id"])
            base_fp = counterfactual.branch_fingerprint(baseline_sink, decision_id)
            sample_id = _sha(
                f"{world_id}|{decision_id}|{base_fp}".encode("utf-8"))
            baseline_action = str(decision_row["chosen"])
            action_outcomes = {baseline_action: baseline_outcome}

            for action in candidates:
                if action == baseline_action:
                    continue
                cf_sink = []
                cf_capture = FeatureCapturePolicy()
                cf_result = kernel.run_simulation(
                    resolved, rows,
                    decision_sink=cf_sink,
                    forced_actions={decision_id: action},
                    inference_policy=cf_capture,
                )
                cf_fp = counterfactual.branch_fingerprint(cf_sink, decision_id)
                if cf_fp != base_fp:
                    raise CollectionError(
                        f"decision {decision_id} action {action}: branch prefix mismatch")
                cf_row = next(
                    (r for r in cf_sink if int(r.get("decision_id", -1)) == decision_id),
                    None,
                )
                if cf_row is None or cf_row.get("chosen") != action:
                    raise CollectionError(
                        f"decision {decision_id} action {action}: forced action was not committed")
                action_outcomes[action] = cohort_outcome(cf_result, len(rows))

            # All candidate labels must exist before a sample is published.
            if set(action_outcomes) != set(candidates):
                rejected.append({
                    "decision_id": decision_id,
                    "reason": "not_all_candidate_actions_have_outcomes",
                    "candidates": candidates,
                    "observed": sorted(action_outcomes),
                })
                continue
            for action in candidates:
                output_rows.append({
                    "schema": DATA_SCHEMA,
                    "sample_id": sample_id,
                    "world_id": world_id,
                    "cohort_id": cohort_id,
                    "policy_id": policy_id,
                    "branch_fingerprint": base_fp,
                    "decision_id": decision_id,
                    "baseline_action": baseline_action,
                    "action": action,
                    "features": capture_row["features"][action],
                    "outcome": action_outcomes[action],
                    "offered_packets": len(rows),
                    "natural_end": True,
                })
            accepted += 1

    if not output_rows:
        raise CollectionError(
            "no complete paired action samples were collected; nothing was written")
    payload = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
        for row in output_rows
    )
    temporary = out_path.with_name("." + out_path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(out_path)
    return {
        "status": "collected",
        "schema": DATA_SCHEMA,
        "out": str(out_path),
        "rows": len(output_rows),
        "paired_decision_samples": accepted,
        "offered_packets": len(rows),
        "baseline_outcome": baseline_outcome,
        "world_id": world_id,
        "cohort_id": cohort_id,
        "policy_id": policy_id,
        "trace_sha256": trace_sha,
        "capture_calls": capture.calls,
        "matched_decisions": len(matched),
        "rejected_samples": rejected,
        "warning": (
            "one-step action forks measure downstream response under this fixed "
            "baseline policy profile; they do not certify repeated policy changes"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Collect PCVR-0 paired action-fork training data")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-decisions", type=int, default=8)
    parser.add_argument("--decision-stride", type=int, default=1)
    args = parser.parse_args(argv)
    result = collect(
        args.config, args.out,
        max_decisions=args.max_decisions,
        decision_stride=args.decision_stride,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
