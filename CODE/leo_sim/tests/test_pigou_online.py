"""Tests for online end-to-end Pigou-MetaDRL V2 (PIGOU_V2 / pigou_online)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from CODE.leo_sim import config, control, inference, kernel, receipt
from CODE.leo_sim.batch import compile_run_trace
from CODE.leo_sim.rl.agents.pigou_online import (
    ACTION_COUNT,
    CHECKPOINT_SCHEMA,
    INPUT_DIM,
    NUMPY_WEIGHT_KEYS,
    PIGOU_V2_FEATURE_ORDER,
    PORT_COUNT,
    PORT_EMBED_DIM,
    PORT_FEAT_DIM,
    RISK_QUANTILES,
    VALID_VARIANTS,
    NumpyPigouOnline,
    PigouLocalTracker,
    clip_pigou_risk_target,
    pigou_action_scores,
)
from CODE.leo_sim.rl.local_forward import EdgeReport, LocalForwardView, Port
from CODE.scripts.build_three_phase_trace import build


def _make_dummy_numpy_checkpoint(tmp_path: Path, variant: str = "full",
                                 lambda_pd: float = 0.25) -> tuple[Path, Path, str, str]:
    rng = np.random.default_rng(12345)
    shapes = {
        "port_proj_w": (PORT_FEAT_DIM, PORT_EMBED_DIM),
        "port_proj_b": (PORT_EMBED_DIM,),
        "attn_q_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
        "attn_q_b": (PORT_EMBED_DIM,),
        "attn_k_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
        "attn_k_b": (PORT_EMBED_DIM,),
        "attn_v_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
        "attn_v_b": (PORT_EMBED_DIM,),
        "attn_o_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
        "attn_o_b": (PORT_EMBED_DIM,),
        "attn_ln_gamma": (PORT_EMBED_DIM,),
        "attn_ln_beta": (PORT_EMBED_DIM,),
        "pkt_w": (18, PORT_EMBED_DIM),
        "pkt_b": (PORT_EMBED_DIM,),
        "trunk96_w": (PORT_COUNT * PORT_EMBED_DIM + PORT_EMBED_DIM, 96),
        "trunk96_b": (96,),
        "trunk64_w": (96, 64),
        "trunk64_b": (64,),
        "ego_w": (64, ACTION_COUNT),
        "ego_b": (ACTION_COUNT,),
        "risk_w": (64, ACTION_COUNT * RISK_QUANTILES),
        "risk_b": (ACTION_COUNT * RISK_QUANTILES,),
    }
    weights = {}
    for k in NUMPY_WEIGHT_KEYS:
        if k.endswith("gamma"):
            weights[k] = np.ones(shapes[k], dtype=np.float32)
        elif k.endswith("_b") or k.endswith("beta"):
            weights[k] = np.zeros(shapes[k], dtype=np.float32)
        else:
            weights[k] = rng.normal(0.0, 0.08, size=shapes[k]).astype(np.float32)

    tmp_path.mkdir(parents=True, exist_ok=True)
    keras_stub = tmp_path / "online.keras"
    keras_stub.write_bytes(b"pigou-online-v2-test-stub")
    keras_sha = hashlib.sha256(keras_stub.read_bytes()).hexdigest()

    npz_path = tmp_path / "online.npz"
    np.savez(npz_path, **weights)
    npz_sha = hashlib.sha256(npz_path.read_bytes()).hexdigest()

    meta = {
        "schema": CHECKPOINT_SCHEMA,
        "algorithm": "pigou_online",
        "contract": "PIGOU_V2",
        "variant": variant,
        "numpy_checkpoint": "online.npz",
        "numpy_checkpoint_sha256": npz_sha,
        "numpy_verified": True,
        "cvar_alpha": 0.75,
        "lambda_pd": float(lambda_pd),
        "gamma_z": 0.08,
        "weights_purpose": "trained",
        "checkpoint_file": "online.keras",
        "checkpoint_sha256": keras_sha,
        **inference.observation_contract_fields(
            contract="PIGOU_V2",
            feature_width=INPUT_DIM,
            feature_order=PIGOU_V2_FEATURE_ORDER,
        ),
    }
    meta_path = tmp_path / "metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    meta_sha = hashlib.sha256(meta_path.read_bytes()).hexdigest()
    return keras_stub, meta_path, keras_sha, meta_sha


def test_pigou_v2_observation_shape_and_tracker_updates():
    tracker = PigouLocalTracker()
    view = LocalForwardView(
        sat=3, now=0.50, pid=7, src="src", dst="dst", emitted_at=0.45,
        bits=64_000, hops_taken=2, dst_features=(0.1, -0.2, 0.4),
        egress_count=1,
        ports=(
            Port(1, True, 0.70, 1.0, 0.5, 0.46, 0.48, 2.46,
                 (EdgeReport(2, 0.6, 0.5, 1.0, 0.5), None, None, None),
                 hops=2, progress_km=400.0, last_hop=False, generation=1),
            Port(4, True, 0.20, 1.0, 0.6, 0.44, 0.47, 2.44,
                 (EdgeReport(5, 0.3, 0.2, 1.0, 0.5), None, None, None),
                 hops=3, progress_km=-100.0, last_hop=True, generation=1),
            Port(None),
            Port(None),
        ),
        legal=("N", "S"), shortest=("N",), base_order=("N", "S"),
    )
    tracker.note_arrival(3, 64_000, True)
    obs, aux = tracker.build_observation(view, orig_emit_time_s=0.40, retries_used=1)
    assert obs.shape == (INPUT_DIM,)
    assert np.all(np.isfinite(obs))
    assert aux["detour_flags"].tolist() == [0.0, 0.0, 1.0, 0.0, 0.0]
    # Frame variant zeros out temporal/structural channels (dq_dt, r_conf, F_2d, z_u)
    obs_frame, _ = tracker.build_observation(view, zero_temporal=True)
    ports_frame = obs_frame[:48].reshape(4, 12)
    assert np.all(ports_frame[:, 8:12] == 0.0)


def test_pigou_risk_target_clip_and_scoring_damping():
    clipped = clip_pigou_risk_target([-1.5, 0.2, 5.0, 1000.0], maximum=3.0)
    np.testing.assert_allclose(clipped, [0.0, 0.2, 3.0, 3.0])

    ego = np.array([0.0, 0.72, 0.80, 0.1, 0.1], dtype=np.float32)
    risk = np.zeros((5, 16), dtype=np.float32)
    risk[1, :] = 0.08  # N: low externality
    risk[2, :] = 0.65  # S: high externality detour
    detour = np.array([0.0, 0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    scores = pigou_action_scores(
        ego, risk, multiplier=0.5, alpha=0.75, z_u=1.0, detour_flags=detour, gamma_z=0.08)
    assert scores[1] > scores[2]


@pytest.mark.parametrize("variant", sorted(VALID_VARIANTS))
def test_numpy_pigou_online_all_variants_and_fixed_loader(tmp_path, variant):
    keras_stub, meta_path, keras_sha, meta_sha = _make_dummy_numpy_checkpoint(
        tmp_path, variant=variant)
    adapter = inference.load_fixed_adapter_from_checkpoint(
        str(keras_stub), keras_sha, str(meta_path), meta_sha,
        expected_contract="PIGOU_V2",
        expected_width=INPUT_DIM,
        expected_feature_order=PIGOU_V2_FEATURE_ORDER,
        algorithm="pigou_online",
    )
    assert isinstance(adapter, NumpyPigouOnline)
    assert adapter.variant == variant
    probe = np.linspace(-0.2, 0.2, INPUT_DIM, dtype=np.float32)
    action = adapter.choose(probe, {"deliver": False, "N": True, "S": True, "E": False, "W": False})
    assert action in ("N", "S")
    assert adapter.counters()["calls"] == 1
    with pytest.raises(ValueError):
        adapter.remember()


def test_pigou_online_config_validation():
    with pytest.raises(config.ConfigError, match="PIGOU_V2"):
        config.resolve_config({
            "routing": {"learning_enabled": True, "contract": "neighbor_1hop"},
            "learning": {"algorithm": "pigou_online"},
        })
    resolved = config.resolve_config({
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "PIGOU_V2",
                    "ephemeris_egress_fallback": True},
        "control_plane": {"enabled": True, "vis_k": 1},
        "demand": {"research_deadline_s": 0.24},
        "learning": {
            "algorithm": "pigou_online", "mode": "train", "reward": "deadline_loss_v1",
            "gamma": 1.0, "include_last_hop": False, "pigou_variant": "full",
        },
    })
    assert resolved["config"]["learning"]["algorithm"] == "pigou_online"
    assert resolved["config"]["routing"]["contract"] == "PIGOU_V2"


def test_pigou_online_fixed_inference_end_to_end_kernel(tmp_path):
    keras_stub, meta_path, keras_sha, meta_sha = _make_dummy_numpy_checkpoint(
        tmp_path / "ckpt", variant="full")
    workload = build(tmp_path / "traffic", scene="A", seed=29001,
                     interval=0.10, multiplier=1.5, offered_mbps=2.0)
    raw = workload["config"]
    raw["routing"].update(learning_enabled=True, contract="PIGOU_V2")
    raw["learning"].update(
        algorithm="pigou_online", mode="eval", seed=11, obs_hops=1,
        include_last_hop=False, gamma=1.0, reward="deadline_loss_v1",
        checkpoint_path=str(keras_stub), checkpoint_sha256=keras_sha,
        checkpoint_metadata_sha256=meta_sha, fixed_inference=True,
        epsilon_start=0.0, epsilon_end=0.0,
        pigou_variant="full", pigou_telemetry_sat=2,
    )
    sim_dir = tmp_path / "sim"
    raw["outputs"]["out_dir"] = str(sim_dir)
    resolved = config.resolve_config(raw)
    manifest, trace_bytes, rows = compile_run_trace(resolved, str(sim_dir))
    result = kernel.run_simulation(
        resolved, rows, learning_out_dir=sim_dir / "pigou_online")
    rcp = receipt.write_run(str(sim_dir), resolved, trace_bytes, manifest, result, rows)
    assert rcp["natural_end"]
    assert rcp["conservation_ok"]
    assert rcp["mechanisms"]["effective"]["learning"]
    assert receipt.verify_receipt_dir(str(sim_dir)) == []
    assert (sim_dir / "pigou_online" / "online.npz").is_file()
