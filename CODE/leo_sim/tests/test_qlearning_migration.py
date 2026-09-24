"""Tabular Q-learning migration tests (legacy M1 QLearning -> V2 contract).

Legacy reference: class QLearning, SimulationRL.py:5682. The legacy module
cannot be imported here (module-level TensorFlow import), so golden values
are recomputed from the cited formulas with plain math/numpy:

- init: qTable = np.random.rand(...) per (state, action) — 5703-5704;
- non-terminal update: Q <- (1-alpha)*Q + alpha*(r + gamma*maxQ(s')) —
  5791-5794 (alpha=0.25 at :558, gamma=0.99 at :274);
- terminal: Q(s,a) is written DIRECTLY with the arrive reward — 5743;
- explore: uniform among available directions; exploit: argmax with
  unavailable directions excluded — 5758-5769.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pytest

from CODE.leo_sim import config, kernel, learning, receipt, trace
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

ALPHA = 0.25   # legacy alpha, SimulationRL.py:558
GAMMA = 0.99   # legacy gamma, SimulationRL.py:274
A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)

FULL_MASK = {a: True for a in learning.ACTIONS}


def _learner(mode="train", contract="C3", **cfg_over):
    cfg = config.resolve_config({
        "routing": {"learning_enabled": True, "policy": "hop"},
        "learning": {"algorithm": "qlearning", "mode": mode, **cfg_over},
    })
    return learning.TabularQLearning(contract, cfg["config"]["learning"], seed=7)


def test_update_rule_golden():
    """Golden: Q <- (1-a)Q + a*(r + g*maxQ(s')), SimulationRL.py:5791-5794."""
    ql = _learner()
    s1 = np.array([0.1, 0.2, 0.3, 0.4])
    s2 = np.array([0.5, 0.6, 0.7, 0.8])
    ql.table[ql._key(s1)] = np.array([0.5, 0.6, 0.7, 0.8, 0.9])
    ql.table[ql._key(s2)] = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    ql.remember(s1, "N", 2.0, s2, FULL_MASK, False)
    # "N" is ACTIONS[1]; old Q = 0.6
    expected = (1 - ALPHA) * 0.6 + ALPHA * (2.0 + GAMMA * 5.0)
    assert ql.table[ql._key(s1)][1] == pytest.approx(expected, rel=1e-15)
    assert ql.train_steps == 1 and ql.transitions == 1


def test_terminal_writes_reward_directly():
    """Golden: terminal Q(s,a) = reward (no EMA, no bootstrap),
    SimulationRL.py:5743."""
    ql = _learner()
    s1 = np.zeros(4)
    ql.table[ql._key(s1)] = np.array([0.5, 0.6, 0.7, 0.8, 0.9])
    ql.remember(s1, "deliver", 50.0, np.ones(4), FULL_MASK, True)
    assert ql.table[ql._key(s1)][0] == 50.0


def test_bootstrap_maxes_over_legal_next_only():
    """maxQ(s') is over legal actions (legacy masks unavailable to -inf,
    5765-5769 -> argmax over legal set)."""
    ql = _learner()
    s1 = np.zeros(4)
    s2 = np.ones(4)
    ql.table[ql._key(s1)] = np.zeros(5)
    ql.table[ql._key(s2)] = np.array([100.0, 1.0, 1.0, 1.0, 1.0])
    mask = {a: a != "deliver" for a in learning.ACTIONS}
    ql.remember(s1, "E", 0.0, s2, mask, False)
    expected = (1 - ALPHA) * 0.0 + ALPHA * (0.0 + GAMMA * 1.0)
    assert ql.table[ql._key(s1)][3] == pytest.approx(expected, rel=1e-15)


def test_fresh_rows_init_uniform_0_1():
    """Golden: fresh Q values are uniform [0,1), SimulationRL.py:5703-5704."""
    ql = _learner()
    row = ql._row(np.zeros(8))
    assert np.all(row >= 0.0) and np.all(row < 1.0)


def test_exploit_picks_best_legal_and_explore_stays_legal():
    row = np.array([9.0, 0.1, 0.2, 0.3, 0.4])  # deliver best but illegal
    s = np.zeros(4)
    mask = {a: a in ("N", "E") for a in learning.ACTIONS}
    ql_force = _learner(epsilon_start=0.0, epsilon_end=0.0)
    ql_force.table[ql_force._key(s)] = row
    assert ql_force.choose(s, mask, 0.0) == "E"
    ql_explore = _learner(epsilon_start=1.0, epsilon_end=1.0)
    for _ in range(20):
        assert ql_explore.choose(s, mask, 0.0) in ("N", "E")


def test_eval_mode_does_not_update_table(tmp_path):
    train = _learner()
    dim = learning.CONTRACT_DIMS["C3"]
    train.remember(np.zeros(dim), "E", 1.5, np.ones(dim), FULL_MASK, False)
    meta = train.save_and_verify(tmp_path)
    table_path = tmp_path / "q_table.json"
    # eval requires a pinned checkpoint (config contract)
    ql = _learner(mode="eval", checkpoint_path=str(table_path),
                  checkpoint_sha256=meta["checkpoint_sha256"],
                  checkpoint_metadata_sha256=meta["metadata_sha256"])
    before = {k: v.copy() for k, v in ql.table.items()}
    s = np.zeros(dim)
    ql.remember(s, "E", 50.0, np.ones(4), FULL_MASK, True)
    assert ql.table[ql._key(s)][3] == before[ql._key(s)][3]
    assert ql.train_steps == 0 and ql.transitions == 1


def test_eval_mode_does_not_consume_rng_or_mutate_table():
    """Eval mode must evaluate a fixed policy: no epsilon roll per decision,
    and unseen states get a deterministic zero row (first legal action)
    instead of a random-init row inserted into the table."""
    cfg = {"mode": "eval", "gamma": 0.99}
    q = learning.TabularQLearning("C3", cfg, seed=7)
    obs = np.zeros(learning.CONTRACT_DIMS["C3"])
    mask = {"deliver": False, "N": True, "S": False, "E": True, "W": False}
    state_before = q.rng.bit_generator.state
    action = q.choose(obs, mask, 1.0)
    state_after = q.rng.bit_generator.state
    assert q.table == {}  # unseen state must not be written into the table
    assert state_after == state_before  # no RNG consumed
    assert action == "N"  # deterministic: first legal action on a zero row


def test_save_load_roundtrip_verified(tmp_path):
    ql = _learner()
    dim = learning.CONTRACT_DIMS["C3"]
    ql.remember(np.zeros(dim), "E", 1.5, np.ones(dim), FULL_MASK, False)
    meta = ql.save_and_verify(tmp_path)
    assert meta["checkpoint_verified"] is True
    table_path = tmp_path / "q_table.json"
    sha = hashlib.sha256(table_path.read_bytes()).hexdigest()
    assert meta["checkpoint_sha256"] == sha
    ql2 = _learner(mode="eval", checkpoint_path=str(table_path),
                   checkpoint_sha256=sha,
                   checkpoint_metadata_sha256=meta["metadata_sha256"])
    assert ql2.table.keys() == ql.table.keys()
    for key, row_vals in ql.table.items():
        assert np.array_equal(ql2.table[key], row_vals)
    with pytest.raises(learning.LearningUnavailable):
        _learner(mode="eval", checkpoint_path=str(table_path),
                 checkpoint_sha256="0" * 64)


def test_exact_resume_restores_table_counters_and_rng(tmp_path):
    """An interrupted Q-learning run must continue from the full state.

    This is deliberately stronger than model save/load: the table, update
    counters and exploration RNG all belong to the continuation contract.
    """
    ql = _learner(epsilon_start=1.0, epsilon_end=1.0)
    dim = learning.CONTRACT_DIMS["C3"]
    state = np.zeros(dim)
    next_state = np.ones(dim)
    ql.choose(state, FULL_MASK, 0.0)
    ql.remember(state, "E", 1.5, next_state, FULL_MASK, False)
    meta = ql.save_and_verify(tmp_path)
    resumed = _learner(
        mode="train", epsilon_start=1.0, epsilon_end=1.0,
        resume_path=str(tmp_path / "resume"),
        resume_sha256=meta["resume_sha256"],
    )
    assert resumed.table.keys() == ql.table.keys()
    for key, values in ql.table.items():
        assert np.array_equal(resumed.table[key], values)
    assert resumed.decisions == ql.decisions
    assert resumed.transitions == ql.transitions
    assert resumed.train_steps == ql.train_steps
    assert resumed.loaded_resume_sha256 == meta["resume_sha256"]
    assert resumed.choose(state, FULL_MASK, 0.0) == ql.choose(state, FULL_MASK, 0.0)


def test_checkpoint_contract_mismatch_rejected(tmp_path):
    """A checkpoint trained under one observation contract must not load
    under a different contract with the same input width (C3/C4 both have
    dimension 14 but different semantics)."""
    train = _learner(contract="C3")
    dim = learning.CONTRACT_DIMS["C3"]
    train.remember(np.zeros(dim), "E", 1.5, np.ones(dim), FULL_MASK, False)
    meta = train.save_and_verify(tmp_path)
    table_path = tmp_path / "q_table.json"
    with pytest.raises(learning.LearningUnavailable,
                       match="contract mismatch"):
        _learner(mode="eval", contract="C4",
                 checkpoint_path=str(table_path),
                 checkpoint_sha256=meta["checkpoint_sha256"])


def test_config_accepts_qlearning_and_validates_alpha():
    cfg = config.resolve_config({
        "routing": {"learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    assert cfg["config"]["learning"]["qlearning_alpha"] == ALPHA
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(config.ConfigError):
            config.resolve_config({
                "routing": {"learning_enabled": True},
                "learning": {"algorithm": "qlearning",
                             "qlearning_alpha": bad}})


class _TwoSatGeometry(StaticGeometry):
    def subpoint(self, sat_id, t):
        return (0.0, float(sat_id), 550.0)


def _two_sat_geo():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    return _TwoSatGeometry(2, neighbors_map=nb, visible=vis)


def test_kernel_end_to_end_qlearning_without_tensorflow(tmp_path):
    """Full kernel run with the real tabular learner (no TF), incl. receipt."""
    cfg = make_cfg({
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    tdir = tmp_path / "compiled"
    manifest = trace.compile_trace(cfg, str(tdir))
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(tdir / "trace.csv"),
        horizon_s=cfg["config"]["scenario"]["duration_s"],
        max_packets=cfg["config"]["execution"]["max_packets"])
    assert rows, "compiled trace must contain packets for this scenario"
    out = tmp_path / "run"
    result = kernel.run_simulation(cfg, rows, geometry=_two_sat_geo(),
                                   learning_out_dir=out / "qlearning")
    assert result["natural_end"] is True
    lr = result["learning"]
    assert lr["algorithm"] == "qlearning"
    assert lr["decisions"] > 0 and lr["table_size"] > 0
    assert lr["train_steps"] == lr["transitions"]
    assert lr["checkpoint_verified"] is True
    # receipt chain: write + verify with the qlearning artifact
    receipt.write_run(str(out), cfg, tbytes, manifest, result, rows)
    assert receipt.verify_receipt_dir(str(out)) == []


def _legacy_v1_table(path, contract="C3", with_metadata=True):
    """Write a legacy v1 q_table.json (no contract field) plus the sibling
    metadata.json that old save_and_verify produced.  Legacy keys are the
    exact float64 bytes of the contract observation, so a loadable C3 table
    has keys of CONTRACT_DIMS[C3]*8 = 112 bytes."""
    payload = {"schema": "leo-sim-qlearning-table/v1",
               "entries": [[
                   "00" * (learning.CONTRACT_DIMS[contract] * 8),
                   [0.1] * len(learning.ACTIONS)]]}
    table = path / "q_table.json"
    table.write_text(json.dumps(payload, sort_keys=True) + "\n")
    sha = hashlib.sha256(table.read_bytes()).hexdigest()
    if with_metadata:
        meta = {"schema": "leo-sim-qlearning/v1", "algorithm": "qlearning",
                "contract": contract, "checkpoint": "q_table.json",
                "checkpoint_sha256": sha, "checkpoint_verified": True,
                "mode": "eval"}
        (path / "metadata.json").write_text(
            json.dumps(meta, sort_keys=True) + "\n")
        meta_sha = hashlib.sha256(
            (path / "metadata.json").read_bytes()).hexdigest()
    else:
        meta_sha = None
    return str(table), sha, meta_sha


def test_legacy_v1_table_migrates_via_sibling_metadata(tmp_path):
    """A v1 table without payload contract loads when the sibling metadata
    independently binds contract+filename+SHA and is itself pinned."""
    table_path, sha, meta_sha = _legacy_v1_table(tmp_path)
    q = _learner(mode="eval", contract="C3",
                 checkpoint_path=table_path, checkpoint_sha256=sha,
                 checkpoint_metadata_sha256=meta_sha)
    assert len(q.table) == 1


def test_legacy_v1_table_rejected_without_verifiable_metadata(tmp_path):
    """No payload contract and no usable sibling metadata -> fail closed."""
    table_path, sha, _ = _legacy_v1_table(tmp_path, with_metadata=False)
    with pytest.raises(learning.LearningUnavailable,
                       match="contract mismatch"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=table_path, checkpoint_sha256=sha)


def test_legacy_v1_table_rejected_on_metadata_contract_mismatch(tmp_path):
    table_path, sha, meta_sha = _legacy_v1_table(tmp_path, contract="C4")
    with pytest.raises(learning.LearningUnavailable,
                       match="contract mismatch"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=table_path, checkpoint_sha256=sha,
                 checkpoint_metadata_sha256=meta_sha)


def test_legacy_v1_table_rejected_on_wrong_key_width(tmp_path):
    """A legacy table whose keys do not match the contract observation width
    would silently miss every runtime lookup and degrade to the unseen-state
    fallback; it must fail closed instead."""
    payload = {"schema": "leo-sim-qlearning-table/v1",
               "entries": [["00" * 16, [0.1] * len(learning.ACTIONS)]]}
    table = tmp_path / "q_table.json"
    table.write_text(json.dumps(payload, sort_keys=True) + "\n")
    sha = hashlib.sha256(table.read_bytes()).hexdigest()
    meta = {"schema": "leo-sim-qlearning/v1", "algorithm": "qlearning",
            "contract": "C3", "checkpoint": "q_table.json",
            "checkpoint_sha256": sha, "checkpoint_verified": True,
            "mode": "eval"}
    (tmp_path / "metadata.json").write_text(
        json.dumps(meta, sort_keys=True) + "\n")
    meta_sha = hashlib.sha256(
        (tmp_path / "metadata.json").read_bytes()).hexdigest()
    with pytest.raises(learning.LearningUnavailable,
                       match="state key width"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=str(table), checkpoint_sha256=sha,
                 checkpoint_metadata_sha256=meta_sha)


def test_metadata_relabel_is_rejected_by_sha_pin(tmp_path):
    """F1 regression: rewriting sibling metadata (C3 -> C4) while keeping the
    checkpoint SHA must fail because the metadata file itself is pinned."""
    table_path, sha, meta_sha = _legacy_v1_table(tmp_path)
    meta_path = tmp_path / "metadata.json"
    forged = json.loads(meta_path.read_text(encoding="utf-8"))
    forged["contract"] = "C4"
    meta_path.write_text(json.dumps(forged, sort_keys=True) + "\n",
                         encoding="utf-8")
    with pytest.raises(learning.LearningUnavailable,
                       match="metadata.json SHA-256 differs"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=table_path, checkpoint_sha256=sha,
                 checkpoint_metadata_sha256=meta_sha)


def test_metadata_invalid_utf8_is_fail_closed(tmp_path):
    """Invalid UTF-8 metadata must surface as LearningUnavailable (not a raw
    UnicodeDecodeError escaping the learning contract)."""
    table_path, sha, _ = _legacy_v1_table(tmp_path)
    bad = b"\xff\xfe invalid utf8"
    (tmp_path / "metadata.json").write_bytes(bad)
    meta_sha = hashlib.sha256(bad).hexdigest()
    with pytest.raises(learning.LearningUnavailable,
                       match="metadata.json unreadable"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=table_path, checkpoint_sha256=sha,
                 checkpoint_metadata_sha256=meta_sha)


def test_qlearning_payload_schema_and_structure_are_fail_closed(tmp_path):
    """R4C F3 regression: bogus schema, non-mapping payload, duplicate keys
    and non-finite Q values must all be rejected."""
    dim = learning.CONTRACT_DIMS["C3"]
    key = "00" * (dim * 8)
    payloads = [
        {"schema": "bogus", "contract": "C3",
         "entries": [[key, [0.1] * len(learning.ACTIONS)]]},
        {"schema": "leo-sim-qlearning-table/v1", "contract": "C3",
         "entries": [[key, [0.1] * len(learning.ACTIONS)],
                     [key, [0.2] * len(learning.ACTIONS)]]},
        {"schema": "leo-sim-qlearning-table/v1", "contract": "C3",
         "entries": [[key, [0.1] * 4 + [float("nan")]]]},
    ]
    for i, payload in enumerate(payloads):
        table = tmp_path / f"bad_{i}.json"
        table.write_text(json.dumps(payload, sort_keys=True) + "\n")
        sha = hashlib.sha256(table.read_bytes()).hexdigest()
        with pytest.raises(learning.LearningUnavailable):
            _learner(mode="eval", contract="C3",
                     checkpoint_path=str(table), checkpoint_sha256=sha)
    table = tmp_path / "bad_list.json"
    table.write_text("[1, 2, 3]\n")
    sha = hashlib.sha256(table.read_bytes()).hexdigest()
    with pytest.raises(learning.LearningUnavailable,
                       match="not a mapping"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=str(table), checkpoint_sha256=sha)


def test_qlearning_top_level_key_set_and_contract_null_are_strict(tmp_path):
    """N2/F3 coverage: extra top-level key, missing required key and explicit
    contract:null must all be rejected."""
    dim = learning.CONTRACT_DIMS["C3"]
    key = "00" * (dim * 8)
    payloads = [
        {"schema": "leo-sim-qlearning-table/v1", "contract": "C3",
         "entries": [[key, [0.1] * len(learning.ACTIONS)]], "extra": 1},
        {"schema": "leo-sim-qlearning-table/v1", "contract": "C3"},
        {"schema": "leo-sim-qlearning-table/v1", "contract": None,
         "entries": [[key, [0.1] * len(learning.ACTIONS)]]},
    ]
    for i, payload in enumerate(payloads):
        table = tmp_path / f"strict_{i}.json"
        table.write_text(json.dumps(payload, sort_keys=True) + "\n")
        sha = hashlib.sha256(table.read_bytes()).hexdigest()
        with pytest.raises(learning.LearningUnavailable):
            _learner(mode="eval", contract="C3",
                     checkpoint_path=str(table), checkpoint_sha256=sha)


def test_qlearning_canonical_without_pin_loads_and_records_none(tmp_path):
    """Canonical table with no metadata pin: loader skips metadata and records
    None (no-pin positive path)."""
    ql = _learner()
    dim = learning.CONTRACT_DIMS["C3"]
    ql.remember(np.zeros(dim), "E", 1.5, np.ones(dim), FULL_MASK, False)
    meta = ql.save_and_verify(tmp_path)
    table_path = tmp_path / "q_table.json"
    loaded = _learner(mode="eval", checkpoint_path=str(table_path),
                      checkpoint_sha256=meta["checkpoint_sha256"])
    assert loaded.loaded_checkpoint_metadata_sha256 is None


def test_save_rejects_invalid_table_state(tmp_path):
    """F4-RUNTIME-STATE-KEY: save_and_verify must fail closed on wrong-width
    keys or non-finite Q rows so verified always implies loadable."""
    ql = _learner()
    dim = learning.CONTRACT_DIMS["C3"]
    ql.remember(np.zeros(dim), "E", 1.5, np.ones(dim), FULL_MASK, False)
    bad_key = b"\x00" * 16
    ql.table[bad_key] = np.zeros(len(learning.ACTIONS))
    with pytest.raises(learning.LearningUnavailable,
                       match="state key width"):
        ql.save_and_verify(tmp_path / "bad_width")
    del ql.table[bad_key]
    good_key = next(iter(ql.table))
    ql.table[good_key] = np.array([1.0, 2.0, 3.0, 4.0, float("nan")])
    with pytest.raises(learning.LearningUnavailable,
                       match="finiteness"):
        ql.save_and_verify(tmp_path / "bad_row")


def test_qlearning_canonical_table_metadata_pin_is_verified(tmp_path):
    """N1/R1 regression: a canonical (payload-contract) table with an explicit
    checkpoint_metadata_sha256 must verify the sibling metadata and record
    the actual SHA; a wrong pin must fail at load time."""
    ql = _learner()
    dim = learning.CONTRACT_DIMS["C3"]
    ql.remember(np.zeros(dim), "E", 1.5, np.ones(dim), FULL_MASK, False)
    meta = ql.save_and_verify(tmp_path)
    table_path = tmp_path / "q_table.json"
    meta_sha = meta["metadata_sha256"]
    loaded = _learner(mode="eval", checkpoint_path=str(table_path),
                      checkpoint_sha256=meta["checkpoint_sha256"],
                      checkpoint_metadata_sha256=meta_sha)
    assert loaded.loaded_checkpoint_metadata_sha256 == meta_sha
    with pytest.raises(learning.LearningUnavailable,
                       match="metadata.json SHA-256 differs"):
        _learner(mode="eval", checkpoint_path=str(table_path),
                 checkpoint_sha256=meta["checkpoint_sha256"],
                 checkpoint_metadata_sha256="ab" * 32)


def test_qlearning_payload_invalid_utf8_is_fail_closed(tmp_path):
    """A q_table.json with invalid UTF-8 must raise LearningUnavailable."""
    table = tmp_path / "q_table.json"
    table.write_bytes(b"\xff\xfe {\"schema\":")
    sha = hashlib.sha256(table.read_bytes()).hexdigest()
    with pytest.raises(learning.LearningUnavailable,
                       match="checkpoint unreadable"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=str(table), checkpoint_sha256=sha)


def test_qlearning_state_key_representation_must_be_finite(tmp_path):
    """F2-STATE-KEY regression: a correct-width key whose float64 payload is
    NaN/Inf can never be produced by _key() and would silently degrade every
    lookup to the zero-row fallback; it must be rejected."""
    dim = learning.CONTRACT_DIMS["C3"]
    nan_key = b"\xff" * 8 + b"\x00" * (dim * 8 - 8)
    payload = {"schema": "leo-sim-qlearning-table/v1", "contract": "C3",
               "entries": [[nan_key.hex(), [0.1] * len(learning.ACTIONS)]]}
    table = tmp_path / "q_table.json"
    table.write_text(json.dumps(payload, sort_keys=True) + "\n")
    sha = hashlib.sha256(table.read_bytes()).hexdigest()
    with pytest.raises(learning.LearningUnavailable,
                       match="not a finite"):
        _learner(mode="eval", contract="C3",
                 checkpoint_path=str(table), checkpoint_sha256=sha)


def test_qlearning_eval_receipt_with_metadata_pin_e2e(tmp_path):
    """Full kernel->learning ledger->receipt chain for qlearning eval with a
    pinned metadata SHA: loader records the verified SHA and the receipt
    accepts the run."""
    cfg = make_cfg({
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    tdir = tmp_path / "compiled"
    manifest = trace.compile_trace(cfg, str(tdir))
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(tdir / "trace.csv"),
        horizon_s=cfg["config"]["scenario"]["duration_s"],
        max_packets=cfg["config"]["execution"]["max_packets"])
    train_out = tmp_path / "train"
    result = kernel.run_simulation(cfg, rows, geometry=_two_sat_geo(),
                                   learning_out_dir=train_out / "qlearning")
    assert result["natural_end"] is True
    table_path = train_out / "qlearning" / "q_table.json"
    meta = json.loads((train_out / "qlearning" / "metadata.json")
                      .read_text(encoding="utf-8"))
    meta_sha = hashlib.sha256(
        (train_out / "qlearning" / "metadata.json").read_bytes()).hexdigest()
    cfg_eval = make_cfg({
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {
            "algorithm": "qlearning", "mode": "eval",
            "checkpoint_path": str(table_path),
            "checkpoint_sha256": meta["checkpoint_sha256"],
            "checkpoint_metadata_sha256": meta_sha,
        },
    })
    eval_out = tmp_path / "eval"
    res = kernel.run_simulation(cfg_eval, rows, geometry=_two_sat_geo(),
                                learning_out_dir=eval_out / "qlearning")
    assert res["natural_end"] is True
    assert res["learning"]["loaded_checkpoint_metadata_sha256"] == meta_sha
    assert res["learning"]["train_steps"] == 0
    receipt.write_run(str(eval_out), cfg_eval, tbytes, manifest, res, rows)
    assert receipt.verify_receipt_dir(str(eval_out)) == []


def test_metadata_verifier_fail_closed_on_every_field():
    """DDQN metadata provenance gate: any missing/mismatched field rejects."""
    good = {"schema": "leo-sim-ddqn/v1", "algorithm": "ddqn",
            "contract": "C3", "checkpoint": "online.keras",
            "checkpoint_sha256": "ab" * 32, "checkpoint_verified": True}
    assert learning._verify_checkpoint_metadata(
        good, "C3", "online.keras", "ab" * 32,
        "leo-sim-ddqn/v1", "ddqn") is None
    for mutate in (
            lambda m: m.update({"contract": "C4"}),
            lambda m: m.update({"checkpoint_sha256": "00" * 32}),
            lambda m: m.update({"checkpoint": "other.keras"}),
            lambda m: m.update({"schema": "other"}),
            lambda m: m.update({"algorithm": "qlearning"}),
            lambda m: m.update({"checkpoint_verified": False}),
            lambda m: m.pop("contract")):
        m = dict(good)
        mutate(m)
        with pytest.raises(learning.LearningUnavailable):
            learning._verify_checkpoint_metadata(
                m, "C3", "online.keras", "ab" * 32,
                "leo-sim-ddqn/v1", "ddqn")
    with pytest.raises(learning.LearningUnavailable):
        learning._verify_checkpoint_metadata(
            "not-a-dict", "C3", "online.keras", "ab" * 32,
            "leo-sim-ddqn/v1", "ddqn")
