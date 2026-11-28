"""Single-hop counterfactual causal attribution audit for Pigou-MetaDRL V2.

Directly tests Reviewer #1's core question:
  Given identical exogenous traffic, outages, and random seeds, when we vary ONLY
  a single packet's next-hop action ``a in legal(sat*)`` at a contested snapshot
  ``(pid*, sat*, t*)``, does the low-cost local externality proxy correctly rank
  harmful detours vs beneficial detours by the true incremental failures and
  retries inflicted on OTHER packets within ``[t*, t* + window_s]``?
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from CODE.leo_sim import config, kernel
from CODE.leo_sim.batch import compile_run_trace
from CODE.leo_sim.rl.agents.pigou_online import PigouLocalTracker
from CODE.scripts.build_three_phase_trace import build


class _SnapshotKernel(kernel.Kernel):
    """Records contested multi-port snapshots and supports single-action override."""

    def __init__(self, resolved, rows, *, record_window=(2.0, 3.5),
                 override: tuple[int, int, float, str] | None = None):
        super().__init__(resolved, rows)
        self._audit_tracker = PigouLocalTracker()
        self._record_window = record_window
        self._override = override
        self._override_applied = False
        self.snapshots: list[dict] = []

    def _choose_isl(self, pkt, sat, legal):
        now = float(self.env.now)
        if (self._override is not None and not self._override_applied
                and int(pkt.pid) == self._override[0]
                and int(sat) == self._override[1]
                and abs(now - self._override[2]) <= 1e-6):
            forced = self._override[3]
            if forced in legal:
                self._override_applied = True
                return forced

        choice = super()._choose_isl(pkt, sat, legal)
        t0, t1 = self._record_window
        if (self._override is None and t0 <= now <= t1
                and len(legal) >= 2 and len(self.snapshots) < 200):
            view = self._local_forward_view(sat, pkt, legal, ())
            _, aux = self._audit_tracker.build_observation(
                view, orig_emit_time_s=pkt.orig_emit_time_s,
                retries_used=pkt.retries_used)
            local_qs = [float(p.queue) for p in view.ports]
            peer_qs = [
                max((e.queue for e in p.reports if e is not None), default=0.0)
                for p in view.ports
            ]
            # Keep snapshots where at least one legal port is under real load.
            legal_indices = [("N", "S", "E", "W").index(d) for d in legal if d in ("N", "S", "E", "W")]
            if legal_indices and max(local_qs[i] for i in legal_indices) >= 0.45:
                self.snapshots.append({
                    "pid": int(pkt.pid),
                    "sat": int(sat),
                    "t": now,
                    "legal": [d for d in legal if d in ("N", "S", "E", "W")],
                    "local_qs": local_qs,
                    "peer_qs": peer_qs,
                    "dq_dt": list(aux["dq_dt"]),
                    "f_2d": list(aux["f_2d"]),
                    "detour_flags": aux["detour_flags"].tolist(),
                })
        return choice


def _proxy_score(snap: dict, action: str) -> float:
    d_idx = ("N", "S", "E", "W").index(action)
    q_loc = float(snap["local_qs"][d_idx])
    dq_dt = float(snap["dq_dt"][d_idx])
    peer_qs = snap["peer_qs"]
    q_peer = float(peer_qs[d_idx])
    legal_peer_qs = [
        float(peer_qs[("N", "S", "E", "W").index(d)]) for d in snap["legal"]
    ]
    min_legal_peer_q = min(legal_peer_qs) if legal_peer_qs else 0.0
    excess_peer_q = max(0.0, q_peer - max(0.50, min_legal_peer_q + 0.10))
    f_2d = float(snap["f_2d"][d_idx])
    is_detour = float(snap["detour_flags"][d_idx + 1])
    c_busy_proxy = (
        0.40 * ((q_loc - 0.70) / 0.30)
        if (q_loc > 0.70 and dq_dt > 0.0) else 0.0
    )
    c_spill_proxy = (
        is_detour * min(1.0, (excess_peer_q / 0.50) * (1.0 + 0.5 * f_2d))
        if excess_peer_q > 0.0 else 0.0
    )
    return float(0.50 * c_busy_proxy + 0.65 * c_spill_proxy)


def _window_other_damage(result: dict, rows: list[dict], *,
                         exclude_pid: int, t0: float, window_s: float) -> dict:
    t1 = t0 + window_s
    fates = result["packet_fates"]
    other_failures = sum(
        1 for row in rows
        if int(row["packet_id"]) != exclude_pid
        and t0 <= float(row["emit_time_s"]) <= t1
        and fates.get(int(row["packet_id"]), ("", 0))[0] != "DELIVERED"
    )
    other_retries = sum(
        1 for ev in result.get("packet_events", [])
        if ev.get("kind") == "retry_reinject"
        and int(ev["pid"]) != exclude_pid
        and t0 <= float(ev["at"]) <= t1
    )
    return {
        "other_failures": int(other_failures),
        "other_retries": int(other_retries),
        "damage": float(other_failures + 0.5 * other_retries),
    }


def run_audit(*, scene: str = "C", seed: int = 41, offered_mbps: float = 175.0,
              multiplier: float = 2.2, interval: float = 0.10,
              num_snapshots: int = 8, window_s: float = 0.50) -> dict:
    with tempfile.TemporaryDirectory(prefix="pigou-cf-audit-") as tmp:
        root = Path(tmp)
        workload = build(
            root / "traffic", scene=scene, seed=seed,
            interval=interval, multiplier=multiplier,
            offered_mbps=offered_mbps, duration_s=4.5)
        raw = workload["config"]
        raw["routing"].update(policy="local_queue", ephemeris_egress_fallback=True)
        raw["outputs"]["out_dir"] = str(root / "base")
        resolved = config.resolve_config(raw)
        _, _, rows = compile_run_trace(resolved, str(root / "base"))

        base_kern = _SnapshotKernel(resolved, rows)
        base_kern.run()
        candidates = base_kern.snapshots
        if not candidates:
            return {"status": "NO_CONTESTED_SNAPSHOTS", "pairs": 0}
        step = max(1, len(candidates) // num_snapshots)
        selected = candidates[::step][:num_snapshots]

        concordant = 0
        discordant = 0
        tied_damage = 0
        records = []
        for snap in selected:
            arms = []
            for act in snap["legal"]:
                k = _SnapshotKernel(
                    resolved, rows,
                    override=(snap["pid"], snap["sat"], snap["t"], act))
                res = k.run()
                if not k._override_applied:
                    continue
                dmg = _window_other_damage(
                    res, rows, exclude_pid=snap["pid"],
                    t0=snap["t"], window_s=window_s)
                arms.append({
                    "action": act,
                    "proxy": _proxy_score(snap, act),
                    **dmg,
                })
            for i in range(len(arms)):
                for j in range(i + 1, len(arms)):
                    d_diff = arms[i]["damage"] - arms[j]["damage"]
                    p_diff = arms[i]["proxy"] - arms[j]["proxy"]
                    if abs(d_diff) < 1e-9:
                        tied_damage += 1
                    elif d_diff * p_diff > 0:
                        concordant += 1
                    elif d_diff * p_diff < 0:
                        discordant += 1
            records.append({"snapshot": {k: snap[k] for k in ("pid", "sat", "t", "legal")},
                            "arms": arms})
        informative = concordant + discordant
        return {
            "status": "OK",
            "scene": scene,
            "seed": seed,
            "snapshots_evaluated": len(records),
            "concordant_pairs": concordant,
            "discordant_pairs": discordant,
            "tied_damage_pairs": tied_damage,
            "pairwise_concordance": (concordant / informative) if informative else None,
            "records": records,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="C", choices=("A", "B", "C"))
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--offered-mbps", type=float, default=175.0)
    parser.add_argument("--multiplier", type=float, default=2.2)
    parser.add_argument("--interval", type=float, default=0.10)
    parser.add_argument("--snapshots", type=int, default=6)
    args = parser.parse_args()
    report = run_audit(
        scene=args.scene, seed=args.seed, offered_mbps=args.offered_mbps,
        multiplier=args.multiplier, interval=args.interval,
        num_snapshots=args.snapshots)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
