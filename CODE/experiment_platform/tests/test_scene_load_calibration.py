"""Calibrated main scene: the load calculator must reproduce its own arithmetic,
the profile pair must not drift, and the contract must be traceable to the
calibration it came from.

These tests exist because the 2026-10-18 scene calibration is the first place
where the injection rate handed to the simulator is DERIVED from a reference
route set rather than typed in.  If the derivation and the profile disagree,
every downstream arm is calibrated against a scene it is not running on.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from CODE.experiment_platform import scene_load_calibration as SLC  # noqa: E402

PROFILE = REPO / "CODE/leo_sim/profiles/t1_scene_trunk_v1.yaml"
BURST_PROFILE = REPO / "CODE/leo_sim/profiles/t1_scene_trunk_burst_v1.yaml"
WORK = REPO / "CODE/work/WP-T1-SCENE-CAL"
SPEC = WORK / "scene-spec-trunk-v1.json"
STATIONARY_CONTRACT = WORK / "scene_calibration_stationary_20261018.json"
BURST_CONTRACT = WORK / "scene_calibration_burst_20261018.json"

#: Pinned from the calibration run recorded in WORK/.../calibration/.
#: max_e b_e is the whole reason this scene replaced the regional one: at
#: b_e = 0.0398 no affordable network total could load a 1 Gbps ISL.
PINNED_MAX_B_E = 2.0 / 3.0
PINNED_TIERS = {0.20: 299.5, 0.50: 749.5, 0.70: 1049.5,
                0.85: 1274.5, 0.95: 1424.5, 1.10: 1649.5}


def test_selftest_closed_form():
    """The hand-checked 4-node chain must reproduce exact arithmetic."""
    assert SLC.selftest() == 0


def test_uniform_od_matches_simulator_rule():
    """trace.py::_dst_choices draws destinations UNIFORMLY over the other
    endpoints for mode in (uniform, burst, diurnal), and sources with weight
    W**source_exponent.  Reproduce that exactly, not approximately."""
    sites = [{"id": "a", "weight": 4.0}, {"id": "b", "weight": 1.0},
             {"id": "c", "weight": 1.0}]
    od = SLC.od_weights_uniform_destinations(sites)
    assert od == {(0, 1): 2 / 3 / 2, (0, 2): 2 / 3 / 2,
                  (1, 0): (1 / 6) / 2, (1, 2): (1 / 6) / 2,
                  (2, 0): (1 / 6) / 2, (2, 1): (1 / 6) / 2}
    assert abs(sum(od.values()) - 1.0) < 1e-12


@pytest.mark.skipif(not SPEC.exists(), reason="calibration spec not present")
def test_calibration_reproduces_pinned_tiers():
    spec = json.loads(SPEC.read_text())
    res = SLC.calibrate(spec)
    assert res["traffic_split"]["zero_hop_weight"] == pytest.approx(0.0, abs=1e-12)
    assert res["link_coefficients"]["max_b_e"] == pytest.approx(PINNED_MAX_B_E, rel=1e-9)
    assert res["traffic_split"]["unroutable_weight"] == pytest.approx(0.0, abs=1e-12)
    got = {t["target_rho"]: t["R_mbps"] for t in res["tiers"]}
    for rho, mbps in PINNED_TIERS.items():
        assert got[rho] == pytest.approx(mbps, abs=0.05), rho
        row = next(t for t in res["tiers"] if t["target_rho"] == rho)
        assert row["feasible"] is True
        # the tier definition, recomputed from the row itself
        assert (row["R_bit_s"] * row["bottleneck_b_e"]
                + row["bottleneck_c_e_bit_s"]) / row["bottleneck_capacity_bit_s"] \
            == pytest.approx(rho, rel=1e-9)


def test_control_load_comes_from_the_relay_rule_not_a_percentage():
    """c_e must equal (broadcast-tree edge count) * packet_bits / interval,
    and must never be a fixed fraction of capacity."""
    spec = json.loads(SLC.SELFTEST_SPEC.__class__(SLC.SELFTEST_SPEC) and
                      json.dumps(SLC.SELFTEST_SPEC))
    res = SLC.calibrate(spec)
    for edge, c in res["_c_e"].items():
        assert c == pytest.approx(8000 / 0.05, rel=1e-12), edge
    # vis_k = 2 makes the tree wider than one hop, so at least one edge must
    # carry more than one origin's relay
    spec2 = json.loads(json.dumps(SLC.SELFTEST_SPEC))
    spec2["control_plane"] = {"packet_bits": 8000, "interval_s": 0.05, "vis_k": 2}
    res2 = SLC.calibrate(spec2)
    assert max(res2["_c_e"].values()) > 8000 / 0.05


def test_infeasible_tier_is_named_not_clamped():
    spec = json.loads(json.dumps(SLC.SELFTEST_SPEC))
    spec["control_plane"] = {"packet_bits": 8000, "interval_s": 1e-6, "vis_k": 2}
    spec["tiers"] = [0.20]
    res = SLC.calibrate(spec)
    row = res["tiers"][0]
    assert row["feasible"] is False
    assert row["infeasible_link_count"] >= 1
    assert "R_bit_s" not in row


def test_buffer_is_defined_by_drain_time():
    res = SLC.calibrate(json.loads(json.dumps(SLC.SELFTEST_SPEC)))
    rate = 1e9
    for tau, block in res["checks"]["buffer"].items():
        assert block["bits"] == pytest.approx(rate * float(tau), rel=1e-12)
        assert block["drain_time_s"] == pytest.approx(float(tau), rel=1e-12)
        assert block["packets"] == pytest.approx(rate * float(tau) / 12000, rel=1e-12)


def test_profile_pair_differs_only_in_the_declared_burst_lines():
    """The stationary and burst profiles must share geometry, OD, access and
    control byte for byte, or the burst arm is a different scene."""
    import yaml
    a = yaml.safe_load(PROFILE.read_text())
    b = yaml.safe_load(BURST_PROFILE.read_text())
    assert a["scenario"]["name"] == "t1-scene-trunk-v1"
    assert b["scenario"]["name"] == "t1-scene-trunk-burst-v1"
    a["scenario"].pop("name")
    b["scenario"].pop("name")
    assert a["demand"]["mode"] == "uniform"
    assert b["demand"]["mode"] == "burst"
    assert b["demand"]["burst_multiplier"] > 1.0, "a no-op burst is refused by the compiler"
    for key in ("burst_start_s", "burst_duration_s", "burst_multiplier", "mode"):
        a["demand"].pop(key, None)
        b["demand"].pop(key, None)
    a["outputs"].pop("out_dir", None)
    b["outputs"].pop("out_dir", None)
    assert a == b, ("the stationary and burst profiles must be the same scene; "
                    "differences: " + repr({k: (a.get(k), b.get(k)) for k in set(a) | set(b)
                                            if a.get(k) != b.get(k)}))


def test_contracts_trace_to_the_calibration():
    """Every stationary tier cell's offered_mbps must equal the calibrated
    R(rho) for its tier, and no cell may exceed the contract packet cap."""
    cal = json.loads((WORK / "calibration/calibration.json").read_text())
    by_rho = {t["target_rho"]: t["R_mbps"] for t in cal["tiers"]}
    doc = json.loads(STATIONARY_CONTRACT.read_text())
    assert doc["base_profile"] == "CODE/leo_sim/profiles/t1_scene_trunk_v1.yaml"
    cap = doc["max_packets"]
    seen = set()
    for cell in doc["cells"]:
        assert cell["id"] not in seen, "duplicate cell id"
        seen.add(cell["id"])
        d = cell["overrides"]["demand"]
        rho = int(cell["id"].split("-")[1][1:]) / 100.0
        if "burst" not in cell["id"] and rho in by_rho:
            assert d["offered_mbps"] == pytest.approx(by_rho[rho], abs=0.05)
        est = d["offered_mbps"] * 1e6 * (d["emission_end_s"] - d["emission_start_s"]) / d["packet_bits"]
        assert est <= cap, f"{cell['id']} estimates {est:.0f} packets > cap {cap}"


def test_burst_cells_preserve_the_long_run_mean():
    """p*a + (1-p)*b == 1 exactly, b >= 0, and the simulator's (a/b, low=1)
    realisation must give the same mean as the rho=0.50 stationary cell."""
    cal = json.loads((WORK / "calibration/calibration.json").read_text())
    r050 = next(t["R_mbps"] for t in cal["tiers"] if t["target_rho"] == 0.50)
    doc = json.loads(BURST_CONTRACT.read_text())
    assert doc["base_profile"] == "CODE/leo_sim/profiles/t1_scene_trunk_burst_v1.yaml"
    assert len(doc["cells"]) == 3
    for cell in doc["cells"]:
        d = cell["overrides"]["demand"]
        T = d["emission_end_s"] - d["emission_start_s"]
        p = d["burst_duration_s"] / T
        a = 3.0
        b = (1.0 - p * a) / (1.0 - p)
        assert b >= 0.0, f"{cell['id']}: b = {b} < 0"
        assert p * a + (1 - p) * b == pytest.approx(1.0, rel=1e-12)
        # the contract declares offered_mbps and burst_multiplier to 6 decimals
        assert d["offered_mbps"] == pytest.approx(r050 * b, rel=1e-6)
        assert d["burst_multiplier"] == pytest.approx(a / b, rel=1e-6)
        # mean of the simulated envelope = offered * (p*(a/b) + (1-p)) == r050
        mean = d["offered_mbps"] * (p * d["burst_multiplier"] + (1 - p))
        assert mean == pytest.approx(r050, rel=1e-6)


@pytest.mark.skipif(os.environ.get("SLC_SKIP_TRACE_COMPILE") == "1",
                    reason="trace compile disabled")
def test_compiled_trace_matches_the_analytic_od_weights(tmp_path):
    """The strongest check available without the VM: compile the real trace
    from the real profile and compare its realised OD split with the weights
    the calibration was computed from."""
    out = tmp_path / "trace"
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "CODE.leo_sim", "trace", "compile",
         "--config", str(PROFILE), "--out", str(out)],
        cwd=str(REPO), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)})
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    import csv as _csv
    import collections
    rows = list(_csv.DictReader((out / "trace.csv").open(newline="")))
    assert rows, "compiled trace is empty"
    od = collections.Counter((r["src_grid_id"], r["dst_grid_id"]) for r in rows)
    assert len(od) == 6, sorted(od)
    weights = sorted((n / len(rows)) for n in od.values())
    # analytic: two OD pairs at 1/3 and four at 1/12
    expect = sorted([1 / 3, 1 / 3, 1 / 12, 1 / 12, 1 / 12, 1 / 12])
    for got, want in zip(weights, expect):
        assert abs(got - want) < 0.02, (weights, expect)
    import yaml
    prof = yaml.safe_load(PROFILE.read_text())
    dm = prof["demand"]
    predicted = (dm["offered_mbps"] * 1e6
                 * (dm["emission_end_s"] - dm["emission_start_s"]) / dm["packet_bits"])
    assert abs(len(rows) - predicted) / predicted < 0.02
