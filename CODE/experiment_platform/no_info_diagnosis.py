"""Why the real-constellation branch point reports no_info.

The question this answers
-------------------------
A frozen decision that infers a hold records routing_status = "no_info", which
means the local control cache held no valid, actually-arrived advertisement
whose serve_cells listed the destination cell.  That single label hides four
different causes, and they have different remedies:

  RANGE        the advertisement travelled as far as vis_k allows and the
               destination-serving satellite is simply further away;
  NOT_ARRIVED  a serving satellite exists within vis_k hops but its
               advertisement has not arrived yet at the decision instant
               (warm-up / advertise interval / propagation);
  EXPIRED      an entry for a serving satellite arrived but is no longer valid
               at the decision instant (ttl);
  ABSENT       the cache held no entry from anywhere, i.e. the control plane
               did not reach this satellite at all.

What it reads
-------------
Only the two streams a normal run already writes: decision rows and timeline
milestones.  Every classification comes from what the decision's OWN
observation recorded (neighbours, hops, advertised_serve_cells, age_s), so the
diagnosis never needs geometry, and it never feeds anything back into a policy.

Boundary
--------
This is a diagnostic.  It does not change routing, and it must not be used to
infer what a policy "should" have done: the observation is reported as it was.

Usage
-----
    python3 -m CODE.experiment_platform.no_info_diagnosis \
        --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
        --out out/no-info-diagnosis.json
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from CODE.leo_sim import config as config_mod, kernel, trace as trace_mod

SCHEMA = "no-info-diagnosis/v1"


class DiagnosisError(RuntimeError):
    pass


def _attempts(decision_rows, timeline_rows, dst_cells):
    """Every decision ATTEMPT, committed or not, with its observation.

    A held attempt never becomes a decision row, so the timeline is the only
    place it exists; reading decision rows alone would show a run with no
    failures at all.
    """
    out = []
    for row in decision_rows:
        obs = row.get("observation_at_start")
        if not isinstance(obs, dict):
            continue
        out.append({
            "origin": "decision_row",
            "decision_id": row.get("decision_id"),
            "pid": row.get("pid"),
            "sat": row.get("sat"),
            "at": float(row.get("t_decision_start", row["t"])),
            "mode": row.get("obs_mode"),
            "kind": row.get("kind"),
            "status": obs.get("routing_status"),
            "obs": obs,
        })
    for row in timeline_rows:
        if row.get("milestone") not in ("frozen_inferred_hold", "commit_rejected"):
            continue
        obs = row.get("observation_at_start")
        if not isinstance(obs, dict):
            continue
        out.append({
            "origin": "timeline:" + row["milestone"],
            "decision_id": row.get("decision_id"),
            "pid": row.get("pid"),
            "sat": row.get("sat"),
            "at": float(row.get("inferred_at", row["at"])),
            "mode": row.get("obs_mode"),
            "kind": obs.get("kind"),
            "status": obs.get("routing_status"),
            "obs": obs,
        })
    out.sort(key=lambda a: (a["at"], a["sat"]))
    return out


def _classify(attempt, dst_cells, ttl_s):
    obs = attempt["obs"]
    neighbours = obs.get("neighbours") or {}
    entries = []
    for key, entry in sorted(neighbours.items()):
        advertised = entry.get("advertised_serve_cells") or []
        entries.append({
            "origin": entry.get("origin"),
            "hops": entry.get("hops"),
            "age_s": entry.get("age_s"),
            "received_at": entry.get("received_at"),
            "generated_at": entry.get("generated_at"),
            "advertised_serve_cells": advertised,
            "covers_destination": any(c in dst_cells for c in advertised),
        })
    covering = [e for e in entries if e["covers_destination"]]
    expired = [e for e in entries
               if isinstance(e["age_s"], (int, float)) and e["age_s"] > ttl_s]
    if covering:
        cause = "COVERING_ENTRY_PRESENT"
        note = ("a valid entry advertising the destination was in the "
                "observation, so no_info did not come from a missing "
                "advertisement")
    elif not entries:
        cause = "ABSENT"
        note = "the cache held no entry at all: the control plane did not reach this satellite"
    elif expired:
        cause = "EXPIRED"
        note = "entries arrived but were older than ttl_s at the decision instant"
    else:
        hop_values = [e["hops"] for e in entries if isinstance(e["hops"], int)]
        cause = "RANGE"
        note = ("entries arrived, none advertises the destination; the "
                "deepest entry is at hops=%s, which is the reach vis_k allows"
                % (max(hop_values) if hop_values else None))
    return {
        "cause": cause,
        "note": note,
        "cache_entries": entries,
        "deepest_hops": max([e["hops"] for e in entries
                             if isinstance(e["hops"], int)], default=None),
        "entries_with_no_serve_cells": sum(
            1 for e in entries if not e["advertised_serve_cells"]),
    }


def diagnose(config_path: Path, root: Path, forced=None):
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise DiagnosisError(f"config invalid: {exc}") from exc
    execution = resolved["config"]["execution"]
    cp = resolved["config"]["control_plane"]

    work = Path(tempfile.mkdtemp(prefix="no-info-", dir=str(root)))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=execution["max_packets"])
    finally:
        for leftover in sorted(work.glob("*"), reverse=True):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass

    # The destination is PER PACKET: a scenario can carry several, and an
    # advertisement covering somebody else's destination is not information
    # this packet's decision could have used.
    dst_of = {r["packet_id"]: r["dst_grid_id"] for r in rows}
    dst_cells = sorted(set(dst_of.values()))
    sink, timeline = [], []
    forced_actions = None
    if forced is not None:
        forced_actions = {int(forced[0]): str(forced[1])}
    result = kernel.run_simulation(resolved, rows, decision_sink=sink,
                                   timeline_sink=timeline,
                                   forced_actions=forced_actions)
    attempts = _attempts(sink, timeline, dst_cells)

    per_attempt = []
    for attempt in attempts:
        if attempt["mode"] != "frozen":
            continue
        own_dst = dst_of.get(attempt["pid"])
        per_attempt.append({
            "origin": attempt["origin"],
            "decision_id": attempt["decision_id"],
            "pid": attempt["pid"],
            "sat": attempt["sat"],
            "at": round(attempt["at"], 9),
            "kind": attempt["kind"],
            "routing_status": attempt["status"],
            "legal_directions": attempt["obs"].get("legal_directions"),
            "packet_dst_cell": own_dst,
            **_classify(attempt, {own_dst} if own_dst else set(),
                        cp["ttl_s"]),
        })

    no_info = [a for a in per_attempt if a["routing_status"] == "no_info"]
    causes = {}
    for a in no_info:
        causes[a["cause"]] = causes.get(a["cause"], 0) + 1

    # When did each satellite FIRST hold an advertisement covering the
    # destination?  That separates "never in range" from "not yet arrived".
    first_covered = {}
    for a in per_attempt:
        if not any(e["covers_destination"] for e in a["cache_entries"]):
            continue
        sat = a["sat"]
        if sat not in first_covered or a["at"] < first_covered[sat]:
            first_covered[sat] = a["at"]

    dead_end_sats = sorted({a["sat"] for a in no_info
                            if a["sat"] not in first_covered})
    return {
        "schema": SCHEMA,
        "source": {
            "config": str(config_path),
            "config_sha256": resolved["sha256"],
            "decision_observation_mode": execution["decision_observation_mode"],
            "compute_delay_s": execution["compute_delay_s"],
            "routing_policy": resolved["config"]["routing"]["policy"],
            "learning_algorithm": resolved["config"]["learning"]["algorithm"],
            "destination_cells": dst_cells,
            "packets": len(rows),
        },
        "control_plane": {
            "enabled": cp["enabled"],
            "vis_k": cp["vis_k"],
            "ttl_s": cp["ttl_s"],
            "advertise_interval_s": cp["advertise_interval_s"],
            "meaning": "an advertisement propagates at most vis_k actual hops "
                       "and is valid until generated_at + ttl_s",
        },
        "totals": {
            "frozen_attempts": len(per_attempt),
            "no_info_attempts": len(no_info),
            "no_info_by_cause": causes,
            "no_info_attempts_seen": len(no_info),
        },
        "first_covered_at": {str(k): round(v, 9)
                             for k, v in sorted(first_covered.items())},
        "satellites_never_covered": dead_end_sats,
        "attempts": per_attempt,
        "forced": (None if forced is None
                   else {"decision_id": int(forced[0]),
                         "action": str(forced[1]),
                         "meaning": "this diagnosis describes the FORCED "
                                    "branch, not the baseline"}),
        "fate_counts": {k: v for k, v in result["fate_counts"].items() if v},
        "limits": [
            "diagnostic only: nothing here is fed back into any policy",
            "every classification comes from the decision's own recorded "
            "observation; no global truth is used",
            "RANGE is reported when the deepest arrived advertisement is at "
            "hops == vis_k and none covers the destination, which is the "
            "signature of a reach limit rather than a timing limit",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Why no_info happened")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--forced-decision-id", type=int, default=None)
    parser.add_argument("--forced-action", default=None)
    args = parser.parse_args(argv)
    if (args.forced_decision_id is None) != (args.forced_action is None):
        print("DIAGNOSIS REFUSED: give both --forced-decision-id and "
              "--forced-action, or neither")
        return 2
    forced = (None if args.forced_decision_id is None
              else (args.forced_decision_id, args.forced_action))
    try:
        document = diagnose(args.config, args.root, forced)
    except DiagnosisError as exc:
        print(f"DIAGNOSIS REFUSED: {exc}")
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(f"DIAGNOSIS REFUSED: output destination exists: {args.out}")
        return 2
    handle, temporary = tempfile.mkstemp(
        prefix=f".{args.out.name}.", suffix=".tmp", dir=str(args.out.parent))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                  indent=1)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, args.out)
    print(json.dumps({
        "status": "diagnosed",
        "out": str(args.out),
        "control_plane": document["control_plane"],
        "totals": document["totals"],
        "satellites_never_covered": document["satellites_never_covered"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
