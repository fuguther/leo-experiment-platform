#!/usr/bin/env python3
"""Pre/post control-plane fix comparison for one cell pair.

Compares a Round-2 (post-fix) cell against its Round-1 (pre-fix) twin on the
control plane, on the achieved state age, and — most importantly — on the DATA
PLANE, byte for byte.  The fix removes control advertisements that were
injected onto directed ISLs whose current geometry makes them unservable; those
never reached a peer and expired at TTL.  Control and data share the same ISL
service resource (non-preemptive priority), so removing dead control traffic may
legitimately change data service timing.  This script therefore does not assume
"data unchanged": it measures it, hashes it, and reports every difference.

Reads only verified pull-back evidence; writes only the JSON it is given.

Usage:
  python3 control_fix_compare.py --evidence-root <root> \
      --pair "pf-high-greedy:sq2-pf-high-greedy-20261031-01:sq-p1-high-greedy-20261031-01" \
      --pair "pf-sync-greedy:sq2-pf-sync-greedy-20261031-01:sq-p2-sync-greedy-20261031-01" \
      --aoi-start 7.0 --out calibration/control_fix_compare.json
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
from pathlib import Path

CONTROL_COUNTERS = (
    "snapshots_created", "registered", "entered_queue", "arrived", "expired",
    "in_system", "transmission_started", "transmission_completed",
    "overflow", "lost", "geometry_lost",
)
CONTROL_BITS = ("offered", "delivered", "in_system", "terminal_loss")


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def sha256_of(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def find_cell_dir(run_dir: Path):
    for candidate in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if (candidate / "summary.json").is_file():
            return candidate
    return None


def load_run(evidence_root: Path, run_id: str, label: str = "") -> dict:
    cell = label or run_id
    run_dir = evidence_root / run_id
    if not run_dir.is_dir():
        return {"cell": cell, "run_id": run_id, "found": False}
    cell_dir = find_cell_dir(run_dir)
    if cell_dir is None:
        return {"cell": cell, "run_id": run_id, "found": False,
                "reason": "no cell directory with summary.json"}
    summary = json.loads((cell_dir / "summary.json").read_text())
    manifest = json.loads((cell_dir / "trace-manifest.json").read_text())
    arm_dir = cell_dir / "arms" / "now"
    with gzip.open(arm_dir / "result.json.gz", "rt", encoding="utf-8") as fh:
        result = json.load(fh)
    config = json.loads((arm_dir / "config.json").read_text())
    return {
        "cell": cell, "run_id": run_id, "found": True,
        "run_dir": str(run_dir), "cell_dir": str(cell_dir),
        "status": summary.get("status"),
        "execution_wall_s": summary.get("execution_wall_s"),
        "diagnostics": summary.get("diagnostics"),
        "contract_sha256": summary.get("contract_sha256"),
        "resolved_config_sha256": summary.get("logical_config_sha256"),
        "trace_source_sha256": manifest.get("source_trace_sha256"),
        "trace_rows": manifest.get("rows"),
        "arm_config": (config.get("resolved_config") or {}),
        "result": result,
        "summary": summary,
    }


def control_block(run: dict) -> dict:
    control = run["result"].get("control") or {}
    counters = control.get("counters") or {}
    bits = control.get("bits") or control.get("totals") or {}
    mech = run["result"].get("mechanism_counters") or {}
    return {
        "counters": {key: counters.get(key) for key in CONTROL_COUNTERS},
        "counters_raw": dict(sorted(counters.items())),
        "bits": {key: bits.get(key) for key in CONTROL_BITS},
        "bits_raw": dict(sorted(bits.items())),
        "fate_counts": dict(sorted((control.get("fate_counts") or {}).items())),
        "cache_expired_open": control.get("cache_expired_open"),
        "isl_ctrl_queue_area_bits_s": run["result"].get(
            "queue_area_bits_s", {}).get("isl_ctrl"),
        "isl_data_queue_area_bits_s": run["result"].get(
            "queue_area_bits_s", {}).get("isl_data"),
        "geometry_unavailable_skips": mech.get(
            "control_geometry_unavailable_skips"),
        "mechanism_counters": dict(sorted(mech.items())),
    }


def aoi_samples(cell_dir: Path, start: float | None):
    """Per (decision, direction) source-measurement age, in seconds.

    age = snapshot_at - used_state[dir].measured_at, i.e. exactly how old the
    advertisement the predictor used was when the decision was scored.  Rows
    without a measured_at are counted, never imputed.
    """
    path = cell_dir / "arms" / "now" / "decisions.jsonl.gz"
    ages, per_decision, no_sample = [], [], 0
    snapshots = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            observation = row.get("observation_at_start") or {}
            audit = observation.get("time_alignment")
            if not isinstance(audit, dict):
                continue
            clock = row.get("t", row.get("t_decision_start"))
            if start is not None and (not isinstance(clock, (int, float))
                                      or float(clock) < start):
                continue
            snap = audit.get("snapshot_at")
            used = audit.get("used_state") or {}
            if not isinstance(snap, (int, float)) or not used:
                continue
            snapshots += 1
            local = []
            for direction, state in used.items():
                measured = (state or {}).get("measured_at")
                if isinstance(measured, (int, float)) \
                        and not isinstance(measured, bool):
                    age = float(snap) - float(measured)
                    ages.append(age)
                    local.append(age)
                else:
                    no_sample += 1
            if local:
                per_decision.append(max(local))
    def quantiles(values):
        ordered = sorted(values)
        if not ordered:
            return {}
        out = {}
        for point in (0.5, 0.9, 0.99):
            index = min(len(ordered) - 1,
                        max(0, int(round(point * (len(ordered) - 1)))))
            out["p%g" % (point * 100)] = ordered[index]
        out["mean"] = statistics.fmean(ordered)
        out["max"] = ordered[-1]
        out["n"] = len(ordered)
        return out
    return {
        "decisions_with_samples": snapshots,
        "samples": len(ages),
        "samples_without_measured_at": no_sample,
        "age_all_candidates_s": quantiles(ages),
        "age_per_decision_max_s": quantiles(per_decision),
    }


def data_plane(run: dict) -> dict:
    result = run["result"]
    deliveries = result.get("deliveries") or {}
    normalised_deliveries = {
        str(pid): {"delivered_at": float(info["delivered_at"]),
                   "path": list(info["path"])}
        for pid, info in deliveries.items()}
    fates = {str(pid): str(fate)
             for pid, fate in (result.get("fates") or {}).items()}
    events = result.get("packet_events") or []
    windows = [row for row in (result.get("link_service_windows") or [])
               if row.get("stage") in ("isl", "uplink", "downlink")]
    service_log = result.get("service_log") or {}
    return {
        "fate_counts": dict(sorted((result.get("fate_counts") or {}).items())),
        "fates": fates,
        "deliveries": normalised_deliveries,
        "packet_events": events,
        "link_service_windows": windows,
        "service_log_isl": service_log.get("isl"),
        "queue_area_bits_s": dict(sorted(
            (result.get("queue_area_bits_s") or {}).items())),
        "counts": {
            "fates": len(fates),
            "deliveries": len(normalised_deliveries),
            "packet_events": len(events),
            "link_service_windows": len(windows),
        },
        "hashes": {
            "fates": sha256_of(fates),
            "deliveries": sha256_of(normalised_deliveries),
            "packet_events": sha256_of(events),
            "link_service_windows": sha256_of(windows),
        },
    }


def wire_unit_bits(run: dict) -> dict:
    """Most common per-instance control wire size, taken from the run itself.

    ``control.instances`` stores ``[fate, total_bits, generated_at, record]`` per
    injected instance, so the wire size is measured, not assumed.  A run whose
    instances are not all one size reports the coverage of the modal value so a
    multi-size population cannot be summarised silently as one number.
    """
    instances = ((run["result"].get("control") or {}).get("instances") or {})
    sizes = []
    for value in instances.values():
        if isinstance(value, (list, tuple)) and len(value) >= 2 \
                and isinstance(value[1], (int, float)):
            sizes.append(int(value[1]))
    if not sizes:
        return {"available": False}
    counts = {}
    for size in sizes:
        counts[size] = counts.get(size, 0) + 1
    modal, hits = max(counts.items(), key=lambda item: item[1])
    return {"available": True, "modal_bits": modal,
            "modal_hits": hits, "instances": len(sizes),
            "modal_coverage": hits / len(sizes),
            "distinct_sizes": len(counts)}


def advertised_backlog(cell_dir: Path, unit_bits, start: float | None) -> dict:
    """Advertised peer-egress backlog the predictor actually consumed.

    For every (decision, legal direction) the predictor used one received
    advertisement sample; ``used_state[dir].bits`` is exactly the work-ahead it
    saw.  A sample whose bits are an exact integer multiple of one control wire
    size can only be accounted for by control packets queued on that resource,
    which is the signature of the artefact this comparison is about.
    """
    path = cell_dir / "arms" / "now" / "decisions.jsonl.gz"
    positives, multiples, resources = [], 0, {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            clock = row.get("t", row.get("t_decision_start"))
            if start is not None and (not isinstance(clock, (int, float))
                                      or float(clock) < start):
                continue
            audit = (row.get("observation_at_start") or {}).get(
                "time_alignment") or {}
            for direction, state in (audit.get("used_state") or {}).items():
                bits = (state or {}).get("bits")
                if not isinstance(bits, (int, float)) \
                        or isinstance(bits, bool) or bits <= 0:
                    continue
                positives.append(float(bits))
                if unit_bits:
                    ratio = float(bits) / float(unit_bits)
                    if abs(ratio - round(ratio)) < 1e-9:
                        multiples += 1
                        key = (state or {}).get("resource_id")
                        resources[key] = resources.get(key, 0) + 1

    def quantiles(values):
        ordered = sorted(values)
        if not ordered:
            return {}
        out = {}
        for point in (0.5, 0.9, 1.0):
            index = min(len(ordered) - 1,
                        max(0, int(round(point * (len(ordered) - 1)))))
            out["p%d" % (point * 100)] = ordered[index]
        out["n"] = len(ordered)
        return out
    return {
        "positive_backlog_samples": len(positives),
        "backlog_bits": quantiles(positives),
        "exact_control_multiples": multiples,
        "exact_control_multiple_fraction": (multiples / len(positives)
                                            if positives else None),
        "distinct_resources_with_control_only_backlog": len(resources),
        "top_control_only_resources": sorted(
            resources.items(), key=lambda item: item[1], reverse=True)[:10],
    }

def first_difference(left: list, right: list, limit: int = 12) -> dict:
    """Locate the first differing rows of two event/window sequences."""
    n = min(len(left), len(right))
    for index in range(n):
        if left[index] != right[index]:
            return {
                "first_diff_index": index,
                "left_rows": len(left), "right_rows": len(right),
                "left_sample": left[index],
                "right_sample": right[index],
                "tail_delta": len(left) - len(right),
            }
    if len(left) != len(right):
        longer = left if len(left) > len(right) else right
        return {
            "first_diff_index": n,
            "left_rows": len(left), "right_rows": len(right),
            "left_sample": (left[n] if n < len(left) else None),
            "right_sample": (right[n] if n < len(right) else None),
            "tail_delta": len(left) - len(right),
        }
    return {"first_diff_index": None, "left_rows": len(left),
            "right_rows": len(right)}


def diff_mapping(left: dict, right: dict, limit: int = 12) -> dict:
    left_keys, right_keys = set(left), set(right)
    differing, samples = 0, []
    for key in sorted(left_keys & right_keys):
        if left[key] != right[key]:
            differing += 1
            if len(samples) < limit:
                samples.append({"key": key, "pre": left[key],
                                "post": right[key]})
    return {
        "shared": len(left_keys & right_keys),
        "only_pre": sorted(left_keys - right_keys)[:limit],
        "only_pre_count": len(left_keys - right_keys),
        "only_post": sorted(right_keys - left_keys)[:limit],
        "only_post_count": len(right_keys - left_keys),
        "differing_values": differing,
        "differing_samples": samples,
        "identical": differing == 0 and not (left_keys ^ right_keys),
    }


def block_delta(pre: dict, post: dict) -> dict:
    out = {}
    for key in sorted(set(pre) | set(post)):
        a, b = pre.get(key), post.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
                and not isinstance(a, bool) and not isinstance(b, bool):
            out[key] = {"pre": a, "post": b, "delta": b - a,
                        "pct": ((b - a) / a * 100.0 if a else None)}
        else:
            out[key] = {"pre": a, "post": b, "delta": None, "pct": None}
    return out


def compare_pair(evidence_root: Path, cell: str, post_run_id: str,
                 pre_run_id: str, aoi_start) -> dict:
    pre = load_run(evidence_root, pre_run_id, cell)
    post = load_run(evidence_root, post_run_id, cell)
    if not pre.get("found") or not post.get("found"):
        return {"cell": cell, "pre": {k: pre.get(k) for k in
                                      ("run_id", "found", "reason")},
                "post": {k: post.get(k) for k in
                         ("run_id", "found", "reason")},
                "comparable": False}
    pre_control, post_control = control_block(pre), control_block(post)
    pre_data, post_data = data_plane(pre), data_plane(post)
    pre_aoi = aoi_samples(Path(pre["cell_dir"]), aoi_start)
    post_aoi = aoi_samples(Path(post["cell_dir"]), aoi_start)
    pre_unit = wire_unit_bits(pre)
    unit_bits = pre_unit.get("modal_bits")
    pre_backlog = advertised_backlog(Path(pre["cell_dir"]), unit_bits, aoi_start)
    post_backlog = advertised_backlog(Path(post["cell_dir"]), unit_bits, aoi_start)
    docs = {
        "pair": "%s / %s" % (pre["run_id"], post["run_id"]),
        "cell": cell,
        "pre": {key: pre[key] for key in (
            "run_id", "status", "execution_wall_s", "diagnostics",
            "contract_sha256", "resolved_config_sha256",
            "trace_source_sha256", "trace_rows")},
        "post": {key: post[key] for key in (
            "run_id", "status", "execution_wall_s", "diagnostics",
            "contract_sha256", "resolved_config_sha256",
            "trace_source_sha256", "trace_rows")},
        "pairing": {
            "same_trace": (pre["trace_source_sha256"]
                           == post["trace_source_sha256"]),
            "trace_source_sha256": pre["trace_source_sha256"],
            "post_trace_source_sha256": post["trace_source_sha256"],
            "same_trace_rows": pre["trace_rows"] == post["trace_rows"],
            "pre_diagnostics": pre["diagnostics"],
            "post_diagnostics": post["diagnostics"],
            "config_diff_masked": _config_diff(
                pre["arm_config"], post["arm_config"]),
        },
        "control": {
            "pre": pre_control, "post": post_control,
            "counters_delta": block_delta(pre_control["counters"],
                                          post_control["counters"]),
            "bits_delta": block_delta(pre_control["bits"],
                                      post_control["bits"]),
            "fate_counts": {"pre": pre_control["fate_counts"],
                            "post": post_control["fate_counts"]},
            "isl_ctrl_queue_area_bits_s": block_delta(
                {"v": pre_control["isl_ctrl_queue_area_bits_s"]},
                {"v": post_control["isl_ctrl_queue_area_bits_s"]})["v"],
            "per_instance_bits": {
                "pre": _avg(pre_control["bits"]["offered"],
                            pre_control["counters"]["registered"]),
                "post": _avg(post_control["bits"]["offered"],
                             post_control["counters"]["registered"]),
            },
        },
        "aoi": {"pre": pre_aoi, "post": post_aoi,
                "window_start_s": aoi_start},
        "advertised_backlog": {
            "wire_unit_bits": pre_unit,
            "pre": pre_backlog,
            "post": post_backlog,
            "interpretation": (
                "used_state[*].bits is the peer-egress work-ahead the "
                "predictor consumed; samples that are exact multiples of one "
                "control wire size can only come from control packets queued "
                "on that resource"),
        },
        "data_plane": {
            "same_fates": pre_data["fates"] == post_data["fates"],
            "same_deliveries": (pre_data["deliveries"]
                                == post_data["deliveries"]),
            "same_packet_events": (pre_data["packet_events"]
                                   == post_data["packet_events"]),
            "same_link_service_windows": (
                pre_data["link_service_windows"]
                == post_data["link_service_windows"]),
            "hashes": {"pre": pre_data["hashes"],
                       "post": post_data["hashes"]},
            "counts": {"pre": pre_data["counts"], "post": post_data["counts"]},
            "queue_area_bits_s": {"pre": pre_data["queue_area_bits_s"],
                                  "post": post_data["queue_area_bits_s"]},
            "fates_diff": diff_mapping(pre_data["fates"], post_data["fates"]),
            "deliveries_diff": diff_mapping(pre_data["deliveries"],
                                            post_data["deliveries"]),
            "packet_events_diff": first_difference(
                pre_data["packet_events"], post_data["packet_events"]),
            "link_service_windows_diff": first_difference(
                pre_data["link_service_windows"],
                post_data["link_service_windows"]),
        },
        "comparable": True,
    }
    return docs


def _avg(total, count):
    if not isinstance(total, (int, float)) or not count:
        return None
    return total / count


def _config_diff(pre: dict, post: dict) -> dict:
    """Field-level config differences, ignoring the matched cell identity.

    The two runs are different cells of different contracts, so the cell id and
    the treatment field itself are expected to differ.  Everything else must be
    identical for the comparison to be about the fix.
    """
    def flatten(value, prefix=""):
        out = {}
        if isinstance(value, dict):
            for key, item in value.items():
                out.update(flatten(item, f"{prefix}.{key}" if prefix else key))
        else:
            out[prefix] = value
        return out
    a, b = flatten(pre), flatten(post)
    diffs = {}
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            diffs[key] = {"pre": a.get(key), "post": b.get(key)}
    return diffs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--date", default="20261031")
    parser.add_argument("--pair", action="append", required=True,
                        help="label:POST_RUN_ID:PRE_RUN_ID "
                             "(full run ids, so a cell whose id changed between "
                             "rounds still pairs correctly)")
    parser.add_argument("--aoi-start", type=float, default=7.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    pairs = []
    for spec in args.pair:
        parts = spec.split(":")
        if len(parts) != 3:
            raise SystemExit(
                "--pair must be label:POST_RUN_ID:PRE_RUN_ID, got %r" % spec)
        label, post_run_id, pre_run_id = parts
        pairs.append(compare_pair(args.evidence_root, label, post_run_id,
                                  pre_run_id, args.aoi_start))
    document = {"schema": "sq-control-fix-comparison/v1",
                "evidence_root": str(args.evidence_root),
                "aoi_window_start_s": args.aoi_start,
                "pairs": pairs}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    for doc in pairs:
        if not doc.get("comparable"):
            print("MISSING %s" % doc["cell"])
            continue
        control = doc["control"]
        print("%-16s ctrl offered %s -> %s | isl_ctrl area %s -> %s | "
              "deliv %s -> %s | events same=%s" % (
                  doc["cell"],
                  control["pre"]["bits"]["offered"],
                  control["post"]["bits"]["offered"],
                  round(control["pre"]["isl_ctrl_queue_area_bits_s"], 1),
                  round(control["post"]["isl_ctrl_queue_area_bits_s"], 1),
                  control["pre"]["counters"]["registered"],
                  control["post"]["counters"]["registered"],
                  doc["data_plane"]["same_packet_events"]))
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
