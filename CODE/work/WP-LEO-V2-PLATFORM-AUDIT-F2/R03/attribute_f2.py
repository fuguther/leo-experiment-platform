#!/usr/bin/env python3
"""F2 attribution step for WP-LEO-V2-PLATFORM-AUDIT-F2 (PROCEDURE section 5b).

Why this tool exists: the F2 node-processing stage is recorded ONLY on the kernel
timeline sink (kernel.py:4297/4300; it deliberately emits no packet_event).
metrics_independent.decompose_packet_delay defines decision_compute_s as the sum of
the UNCOVERED intervals of the packet timeline, and an F2 occupancy IS exactly an
uncovered interval -- so an analyzer that is not handed the F2 spans publishes
satellite node-processing time as decision computation time.  This tool performs
both readings on the SAME persisted run so the difference is explicit and auditable.

HARDENING (revision 3).  Revision 2 of this tool reported ok=true on a TRUNCATED
timeline, on an EMPTY timeline, and on a vacuous ledger, and it took the compute
delay from a CLI argument that need not match the arms.  A verifier that passes on
damaged artifacts is worse than no verifier, so every one of those channels is now
closed and each closure is exercised by --self-test:

  * the delays come from EACH ARM resolved_config.json, never from a flag alone;
  * the timeline sidecar is verified: row_count and log_sha256 must match the
    stream actually read, and receipt_sha256 must match the receipt on disk;
  * exactly one of {control, f2} must have node_process milestones, and the arm
    whose resolved node_process_delay_s > 0 must have a POSITIVE, EVEN count;
  * at least one packet must be delivered, and the declared delivery set must be
    non-empty, or the run is refused instead of reported as vacuously ok.

Usage:
  python3 CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R02/attribute_f2.py \
      --results-root CODE/Results \
      --experiment EXP-20260924-PLATFORM-AUDIT-F2-R02 \
      --out ANALYSIS/EXP-20260924-PLATFORM-AUDIT-F2-R02/f2-attribution.json
  python3 .../attribute_f2.py --self-test     # exercises every refusal path
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from CODE.leo_sim import metrics_independent as indep  # noqa: E402

ARMS = ("control", "f2")
TOL = 1e-9


class AttributionError(RuntimeError):
    """A claimed identity did not hold, or an input is too damaged to judge."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_arm(results_root: Path, run_id: str, compute_delay_s: float | None):
    base = results_root / run_id
    ledger_path = base / "ledgers.json"
    timeline_path = base / "timeline.jsonl"
    manifest_path = base / "timeline.jsonl.manifest.json"
    resolved_path = base / "resolved_config.json"
    receipt_path = base / "receipt.json"
    for path in (ledger_path, timeline_path, manifest_path, resolved_path,
                 receipt_path):
        if not path.is_file():
            raise AttributionError(
                f"{run_id}: missing {path.name} -- F2 is unattributable without "
                f"the full artifact set; re-run with --timeline-log")

    # The delays are read from the ARM OWN resolved configuration.  A caller-
    # supplied flag can only be cross-checked against it, never substituted for
    # it: revision 2 accepted --compute-delay-s 0.1 on configs that say 0.05.
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    execution = resolved["config"]["execution"]
    arm_compute = float(execution["compute_delay_s"])
    arm_node = float(execution["node_process_delay_s"])
    if arm_compute <= 0.0:
        raise AttributionError(
            f"{run_id}: execution.compute_delay_s = {arm_compute!r}; the "
            f"decision stage must be active or the separation is not "
            f"discriminating")
    if compute_delay_s is not None and arm_compute != compute_delay_s:
        raise AttributionError(
            f"{run_id}: --compute-delay-s {compute_delay_s!r} does not match "
            f"the arm resolved_config.json value {arm_compute!r}")

    # The sidecar binds the stream to this very run.  A truncated stream must
    # not be judgeable: revision 2 reported ok=true after whole node pairs were
    # deleted, silently shrinking the F2 term.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("log_sha256") != _sha256(timeline_path):
        raise AttributionError(
            f"{run_id}: timeline sha256 != sidecar log_sha256 -- the stream was "
            f"modified after the run")
    if manifest.get("receipt_sha256") != _sha256(receipt_path):
        raise AttributionError(
            f"{run_id}: receipt sha256 != sidecar receipt_sha256")

    # The receipt is the INDEPENDENT anchor for the ledger: it records
    # ledgers_sha256 at run time.  Without this cross-check a consistently
    # edited ledger+timeline pair passes every sidecar test -- revision 3
    # round 2 defeated the tool exactly that way by deleting a packet.
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    declared_ledger_sha = receipt.get("ledgers_sha256")
    if declared_ledger_sha != _sha256(ledger_path):
        raise AttributionError(
            f"{run_id}: ledgers.json sha256 != receipt.ledgers_sha256 -- the "
            f"ledger was edited after the run")
    if receipt.get("natural_end") is not True or \
            receipt.get("conservation_ok") is not True:
        raise AttributionError(
            f"{run_id}: receipt is not a natural-end conserving run "
            f"(natural_end={receipt.get('natural_end')!r}, "
            f"conservation_ok={receipt.get('conservation_ok')!r})")

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in
            timeline_path.read_text(encoding="utf-8").splitlines() if line]
    if manifest.get("row_count") != len(rows):
        raise AttributionError(
            f"{run_id}: sidecar row_count {manifest.get('row_count')!r} != "
            f"{len(rows)} rows actually read -- the timeline is truncated")
    return ledger, rows, arm_compute, arm_node


def _expected_occupancy_starts(events) -> dict:
    """Satellite-visit instants implied by the packet event record.

    F2 occupies the arriving satellite for one uplink ingress and for every
    ISL arrival; the destination downlink does NOT enter _node_process
    (kernel.py:4283-4287).  Every expected instant is therefore an instant
    that already exists in packet_events, which is what makes it usable as
    an independent anchor for the timeline.
    """
    hop_stage, hop_start, arrivals = {}, {}, {}
    ingress, expected = {}, {}
    for event in events:
        kind = event.get("kind")
        pid = event.get("pid")
        if kind == "propagation_start":
            hop_stage[event.get("prop_id")] = event.get("stage")
            hop_start.setdefault(pid, []).append(event.get("prop_id"))
        elif kind == "propagation_arrival":
            arrivals[event.get("prop_id")] = float(event["at"])
        elif kind == "satellite_ingress":
            ingress[pid] = float(event["at"])
    for pid, prop_ids in hop_start.items():
        instants = []
        if pid in ingress:
            instants.append(ingress[pid])
        for prop_id in prop_ids:
            if hop_stage.get(prop_id) == "isl" and prop_id in arrivals:
                instants.append(arrivals[prop_id])
        expected[pid] = sorted(instants)
    return expected


def analyse_arm(results_root: Path, run_id: str,
                compute_delay_s: float | None = None) -> dict:
    ledger, timeline, arm_compute, arm_node = _load_arm(
        results_root, run_id, compute_delay_s)
    events = ledger.get("packet_events") or []
    windows = ledger.get("link_service_windows") or []

    milestones = [r.get("milestone") for r in timeline]
    starts = milestones.count("node_process_start")
    ends = milestones.count("node_process_end")
    if starts != ends:
        raise AttributionError(
            f"{run_id}: {starts} node_process_start vs {ends} node_process_end "
            f"-- an unclosed occupancy must never be silently dropped")
    if arm_node > 0.0 and starts == 0:
        raise AttributionError(
            f"{run_id}: resolved node_process_delay_s = {arm_node!r} but the "
            f"timeline carries NO node_process milestones -- the stream cannot "
            f"support the claim it is being used for")
    if arm_node == 0.0 and starts != 0:
        raise AttributionError(
            f"{run_id}: resolved node_process_delay_s = 0 but {starts} node "
            f"occupancies were recorded")

    spans = indep.node_process_spans(timeline)

    # ANCHOR (a)+(c): the packet event record independently determines WHICH
    # satellite visits happened and WHEN.  F2 occupies one satellite visit
    # per uplink ingress and per ISL arrival -- the destination downlink is
    # deliberately out of scope (kernel.py:4283-4287) -- so the expected
    # occupancy starts are: the satellite_ingress instant, plus the arrival
    # instant of every propagation hop whose stage is "isl".  Comparing the
    # timeline against THIS anchor is what makes deleting a whole occupancy
    # pair, or injecting a fabricated span into a genuine decision-compute
    # gap, fail loud: both leave the event record untouched.
    expected = _expected_occupancy_starts(events) if arm_node > 0.0 else {}
    actual = {int(pid): sorted(float(s) for s, _e in values)
              for pid, values in spans.items()}
    expected = {pid: starts for pid, starts in expected.items() if starts}
    if set(actual) != set(expected):
        raise AttributionError(
            f"{run_id}: node occupancies recorded for {sorted(actual)} but the "
            f"event record requires {sorted(expected)}")
    for pid in sorted(expected):
        if len(actual[pid]) != len(expected[pid]):
            raise AttributionError(
                f"{run_id}: packet {pid} has {len(actual[pid])} node "
                f"occupancies but its packet_events imply {len(expected[pid])} "
                f"satellite visits")
        for got, want in zip(actual[pid], expected[pid]):
            if abs(got - want) > TOL:
                raise AttributionError(
                    f"{run_id}: packet {pid} node occupancy at {got!r} does not "
                    f"coincide with any arrival instant (nearest expected "
                    f"{want!r}) -- the span is fabricated")

    # ANCHOR (b): the delivered set must equal the receipt own fate count.
    # Deleting a packet consistently from ledger AND timeline otherwise leaves
    # the receipt as the only witness that it ever existed.
    receipt_path = results_root / run_id / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    declared_delivered = (receipt.get("fate_counts") or {}).get("DELIVERED")
    observed_delivered = sum(1 for e in events
                             if e.get("kind") == "delivered")
    if declared_delivered != observed_delivered:
        raise AttributionError(
            f"{run_id}: receipt fate_counts.DELIVERED = {declared_delivered!r} "
            f"but the ledger carries {observed_delivered} delivered events -- "
            f"packets were removed from the ledger")

    node_total = math.fsum(end - start
                           for values in spans.values()
                           for start, end in values)

    labelled = indep.verify_delay_decomposition(
        events, windows, ledger, node_spans_by_pid=spans)
    unlabelled = indep.verify_delay_decomposition(events, windows, ledger)

    delivered = list(labelled["delivered_pids"])
    if not delivered:
        raise AttributionError(
            f"{run_id}: no delivered packet in the declared set -- a vacuous "
            f"ledger must not be reported as ok")

    labelled_dc = labelled["total_decision_compute_s"]
    unlabelled_dc = unlabelled["total_decision_compute_s"]
    leakage = unlabelled_dc - labelled_dc
    decisions = labelled_dc / arm_compute

    checks = {
        "spans_supplied": labelled["node_process_spans_supplied"] is True,
        "labelled_closure_ok": bool(labelled["ok"]),
        "labelled_residual_within_tol":
            labelled["max_abs_residual_s"] <= TOL,
        "leakage_equals_node_total": math.isclose(
            leakage, node_total, rel_tol=0.0, abs_tol=TOL),
        "labelled_dc_is_multiple_of_compute_delay":
            abs(decisions - round(decisions)) <= 1e-9,
        "unlabelled_closure_ok": bool(unlabelled["ok"]),
    }
    links = (ledger.get("congestion_metrics") or {}).get("links") or {}
    return {
        "run_id": run_id,
        "resolved_compute_delay_s": arm_compute,
        "resolved_node_process_delay_s": arm_node,
        "node_process_milestones": {"start": starts, "end": ends},
        "f2_packets": sorted(int(pid) for pid in spans),
        "total_node_process_s": node_total,
        "labelled_total_decision_compute_s": labelled_dc,
        "unlabelled_total_decision_compute_s": unlabelled_dc,
        "leakage_s": leakage,
        "decision_count": round(decisions),
        "labelled_max_abs_residual_s": labelled["max_abs_residual_s"],
        "delivered_pids": delivered,
        "per_packet": {
            pid: {"node_process_s": item["node_process_s"],
                  "decision_compute_s": item["decision_compute_s"],
                  "e2e_s": item["e2e_s"]}
            for pid, item in labelled["packets"].items()},
        "link_capacity": {
            link: {"served_bits": item["served_bits"],
                   "capacity_bits": item["capacity_bits"],
                   "available_capacity_bits": item["available_capacity_bits"],
                   "available_time_s": item["available_time_s"],
                   "utilization": item["utilization"]}
            for link, item in links.items()},
        "checks": checks,
    }


def run(results_root: Path, experiment: str, compute_delay_s: float | None,
        pairing_key: str = "s7") -> dict:
    if not pairing_key or "/" in pairing_key or not pairing_key.isalnum():
        raise AttributionError(
            f"pairing_key must be a non-empty alphanumeric string, got "
            f"{pairing_key!r}")
    # The pairing key selects the cell; it is NOT guessed.  The compiled
    # run-manifest is the authority on which run ids exist, so a wrong key
    # fails here rather than silently looking for directories that never
    # existed.
    manifest_path = ROOT / "EXPERIMENTS" / experiment / "run-manifest.json"
    if not manifest_path.is_file():
        raise AttributionError(
            f"{experiment}: compiled run-manifest.json not found at "
            f"{manifest_path}; the pairing key cannot be validated")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    known = {cell.get("run_id") for cell in manifest.get("cells", [])}
    wanted = {f"{experiment}-{arm}-{pairing_key}" for arm in ARMS}
    missing = sorted(wanted - known)
    if missing:
        raise AttributionError(
            f"pairing_key {pairing_key!r} selects run ids absent from the "
            f"compiled manifest: {missing}; manifest knows {sorted(known)}")

    arms = {arm: analyse_arm(results_root,
                             f"{experiment}-{arm}-{pairing_key}",
                             compute_delay_s)
            for arm in ARMS}
    if arms["control"]["node_process_milestones"]["start"] != 0:
        raise AttributionError("control arm recorded node occupancies")
    if arms["f2"]["node_process_milestones"]["start"] <= 0:
        raise AttributionError("f2 arm recorded no node occupancy")

    left = arms["control"]["link_capacity"]
    right = arms["f2"]["link_capacity"]
    differing = sorted(k for k in set(left) | set(right)
                       if left.get(k) != right.get(k))
    failed = [f"{arm}:{name}" for arm, data in arms.items()
              for name, ok in data["checks"].items() if not ok]
    return {
        "schema": "leo-sim-f2-attribution/v1",
        "experiment": experiment,
        "tolerance_s": TOL,
        "arms": arms,
        "cross_arm": {
            "differing_link_count": len(differing),
            "differing_links": differing[:50],
            "asserted_equal": False,
            "note": ("reported, not asserted: a 0.05 s shift may legitimately move "
                     "an availability window across the 0.1 s sampling boundary"),
        },
        "failed_checks": failed,
        "ok": not failed,
    }


def _self_test() -> int:
    """Exercise every refusal path on a synthetic, deliberately damaged run."""
    import copy
    good = {
        "packet_events": [
            {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
            {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue": "isl",
             "link_id": "isl:0:1", "queue_id": 7},
            {"kind": "service_start", "pid": 1, "at": 0.5, "stage": "isl",
             "link_id": "isl:0:1", "queue_id": 7, "bits": 100,
             "rate_bps": 400.0},
            {"kind": "propagation_start", "pid": 1, "at": 0.75, "stage": "isl",
             "link_id": "isl:0:1", "prop_id": 3, "delay_s": 0.25},
            {"kind": "propagation_arrival", "pid": 1, "at": 1.0, "prop_id": 3},
            # the anchor requires every occupancy to coincide with a real
            # satellite visit: one uplink ingress at 0.2 plus the ISL
            # arrival at 1.0.
            {"kind": "satellite_ingress", "pid": 1, "at": 0.2,
             "endpoint": "g1", "satellite": 0, "bits": 100},
            {"kind": "delivered", "pid": 1, "at": 1.0},
        ],
        "link_service_windows": [{
            "pid": 1, "stage": "isl", "link_id": "isl:0:1", "start": 0.5,
            "end": 0.75, "rate_bps": 400.0, "capacity_bits": 100.0,
            "served_bits": 100, "bits": 100, "outcome": "ok"}],
        "deliveries": {"1": {"delivered_at": 1.0}},
        "congestion_metrics": {"links": {}},
    }
    timeline = [
        {"milestone": "node_process_start", "pid": 1, "sat": 0, "via": "uplink",
         "at": 0.2},
        {"milestone": "node_process_end", "pid": 1, "sat": 0, "via": "uplink",
         "at": 0.25, "started_at": 0.2},
        {"milestone": "node_process_start", "pid": 1, "sat": 1, "via": "isl",
         "at": 1.0},
        {"milestone": "node_process_end", "pid": 1, "sat": 1, "via": "isl",
         "at": 1.05, "started_at": 1.0},
    ]
    failures = []

    def build(root: Path, *, ledger=None, tl=None, manifest=None,
              compute=0.05, node=0.05, receipt=None):
        base = root / "EXP-x-f2-s7"
        base.mkdir(parents=True)
        ledger_obj = good if ledger is None else ledger
        ledger_bytes = json.dumps(ledger_obj).encode()
        (base / "ledgers.json").write_bytes(ledger_bytes)
        if receipt is None:
            receipt = json.dumps({
                "ledgers_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
                "natural_end": True, "conservation_ok": True,
                "fate_counts": {"DELIVERED": 1},
            }).encode()
        raw = "".join(json.dumps(r) + "\n" for r in (timeline if tl is None else tl))
        (base / "timeline.jsonl").write_text(raw)
        (base / "resolved_config.json").write_text(json.dumps(
            {"config": {"execution": {"compute_delay_s": compute,
                                        "node_process_delay_s": node}}}))
        (base / "receipt.json").write_bytes(receipt)
        rows = [r for r in raw.splitlines() if r]
        side = {"row_count": len(rows),
                "log_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "receipt_sha256": hashlib.sha256(receipt).hexdigest()}
        side.update(manifest or {})
        (base / "timeline.jsonl.manifest.json").write_text(json.dumps(side))
        return root

    def expect_refusal(name, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            root = build(Path(tmp), **kwargs)
            try:
                analyse_arm(root, "EXP-x-f2-s7", None)
            except AttributionError:
                print(f"  control triggered: {name}")
                return
            except Exception as exc:  # noqa: BLE001
                print(f"  control triggered (other): {name} -> {type(exc).__name__}")
                return
            failures.append(name)
            print(f"  UNPROVEN CONTROL: {name}")

    print("self-test refusal controls:")
    expect_refusal("truncated timeline (sidecar row_count mismatch)",
                   manifest={"row_count": 3})
    expect_refusal("empty timeline while config says node>0", tl=[])
    expect_refusal("vacuous ledger (no deliveries)",
                   ledger={**good, "deliveries": {}})
    expect_refusal("node_process_delay_s = 0 but milestones present",
                   node=0.0)
    expect_refusal("compute_delay_s = 0 (not discriminating)", compute=0.0)
    expect_refusal("receipt hash mismatch",
                   manifest={"receipt_sha256": "0" * 64})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            root = build(Path(tmp))
            analyse_arm(root, "EXP-x-f2-s7", 0.1)
        except AttributionError:
            print("  control triggered: --compute-delay-s disagrees with the arm")
        else:
            failures.append("compute-delay mismatch")
            print("  UNPROVEN CONTROL: --compute-delay-s disagrees with the arm")
    # ANCHOR (a): delete one whole occupancy pair and RE-SEAL the sidecar, so
    # every sidecar check passes.  The packet_events anchor must still refuse.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            analyse_arm(build(Path(tmp), tl=timeline[:2]), "EXP-x-f2-s7", None)
        except AttributionError:
            print("  control triggered: whole occupancy pair deleted, sidecar re-sealed")
        else:
            failures.append("occupancy deleted")
            print("  UNPROVEN CONTROL: whole occupancy pair deleted")

    # ANCHOR (c): a fabricated span at an instant that is NOT a satellite visit
    with tempfile.TemporaryDirectory() as tmp:
        forged = [dict(r) for r in timeline]
        forged[0] = {**forged[0], "at": 0.9}
        forged[1] = {**forged[1], "at": 0.95, "started_at": 0.9}
        try:
            analyse_arm(build(Path(tmp), tl=forged), "EXP-x-f2-s7", None)
        except AttributionError:
            print("  control triggered: forged span off any arrival instant")
        else:
            failures.append("forged span")
            print("  UNPROVEN CONTROL: forged span off any arrival instant")

    # ANCHOR (b): edit the ledger AND re-seal the sidecar, but leave the
    # receipt alone -- receipt.ledgers_sha256 is the independent witness
    with tempfile.TemporaryDirectory() as tmp:
        original = json.dumps(good).encode()
        stale_receipt = json.dumps({
            "ledgers_sha256": hashlib.sha256(original).hexdigest(),
            "natural_end": True, "conservation_ok": True,
            "fate_counts": {"DELIVERED": 1}}).encode()
        trimmed = json.loads(json.dumps(good))
        trimmed["deliveries"] = {}
        try:
            analyse_arm(build(Path(tmp), ledger=trimmed, receipt=stale_receipt),
                        "EXP-x-f2-s7", None)
        except AttributionError:
            print("  control triggered: ledger edited after the run")
        else:
            failures.append("ledger edited")
            print("  UNPROVEN CONTROL: ledger edited after the run")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            root = build(Path(tmp))
            out = analyse_arm(root, "EXP-x-f2-s7", None)
        except AttributionError as exc:
            failures.append(f"clean input refused: {exc}")
            print(f"  UNPROVEN CONTROL: clean input refused: {exc}")
        else:
            print("  clean input accepted (expected)"
                  f" leakage={out['leakage_s']!r}")
    if failures:
        print(f"SELF-TEST FAILED: {failures}")
        return 1
    print("self-test: all controls triggered")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=Path("CODE/Results"))
    parser.add_argument("--experiment")
    parser.add_argument("--compute-delay-s", type=float, default=None)
    parser.add_argument("--pairing-key", default="s7",
                        help="the trace-seed suffix of the compiled run ids "
                             "(default s7). It is validated against the "
                             "compiled run-manifest.json, and a key that "
                             "selects run ids the manifest does not contain is "
                             "refused.")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    if not args.experiment or args.out is None:
        parser.error("--experiment and --out are required (or use --self-test)")

    report = run(args.results_root, args.experiment, args.compute_delay_s,
                 args.pairing_key)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    arms = report["arms"]
    print(json.dumps({
        "status": "ok" if report["ok"] else "FAILED",
        "out": str(args.out),
        "failed_checks": report["failed_checks"],
        **{f"{a}_node_process_s": d["total_node_process_s"]
           for a, d in arms.items()},
        **{f"{a}_labelled_decision_compute_s":
           d["labelled_total_decision_compute_s"] for a, d in arms.items()},
        **{f"{a}_unlabelled_decision_compute_s":
           d["unlabelled_total_decision_compute_s"] for a, d in arms.items()},
        "cross_arm_differing_links": report["cross_arm"]["differing_link_count"],
    }, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
