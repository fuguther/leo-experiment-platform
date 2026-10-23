"""Third-round independent review (S6): the counterexamples it found.

Every test in this file FAILS on the reviewed commit and passes only after the
S6 rework.  They are transcribed from the reviewer probes, not from the
implementation own tests -- the point of the round was precisely that the
existing suite was green while these four defects were live:

  1. FixedModelPolicy could not be constructed from FixedInferenceAdapter at
     all, because assert_inference_only rejected it for exposing the very
     training entry points it defines in order to refuse;
  2. adapter.act() was dead code, so adapter.counters()["calls"] stayed 0;
  3. verify_checkpoint reported AVAILABLE for a fabricated file when
     metadata_path or metadata_sha256 was omitted;
  4. a configured inference_policy was silently ignored when
     time_alignment.enabled was false.
"""
from __future__ import annotations

import json

import pytest

from CODE.leo_sim import config as config_mod, inference


def _adapter(width=8, seed=20260927):
    return inference.deterministic_small_model(width, seed=seed)


def _branch_fixture():
    from CODE.experiment_platform import scripted_scenarios
    resolved, rows, geometry, meta = scripted_scenarios.build("same_flow")
    cfg = config_mod.resolve_config(
        dict(resolved["config"], time_alignment=dict(
            resolved["config"]["time_alignment"], enabled=True)))
    return cfg, rows, geometry, meta


def _disabled_fixture():
    """The same scenario with time_alignment switched off."""
    from CODE.experiment_platform import scripted_scenarios
    resolved, rows, geometry, meta = scripted_scenarios.build("same_flow")
    raw = resolved["config"]
    cfg = config_mod.resolve_config(
        dict(raw, time_alignment=dict(raw["time_alignment"], enabled=False)))
    return cfg, rows, geometry, meta


# ---- 1. the fixed adapter must be able to back the fixed policy -----------
def test_the_fixed_adapter_can_back_a_fixed_model_policy():
    adapter = _adapter()
    policy = inference.FixedModelPolicy(adapter)
    assert policy.name == adapter.name
    assert policy.parameter_sha256 == adapter.parameter_sha256()


# ---- 2. the adapter own act() must be the single live code path -----------
def test_the_adapter_counter_is_live_on_a_real_branch():
    from CODE.leo_sim import kernel

    cfg, rows, geometry, _meta = _branch_fixture()
    policy = inference.WidthAdaptiveFixedPolicy(seed=5)
    kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=[],
                          timeline_sink=[], inference_policy=policy)
    counters = policy.counters()
    assert counters["calls"] > 0, "the policy must have acted on the branch"
    assert counters.get("adapter_calls") == counters["calls"], (
        "every policy decision must go through the adapter act() path; "
        f"got policy calls={counters['calls']} adapter calls="
        f"{counters.get('adapter_calls')!r}")


# ---- 3. a fabricated checkpoint must never be AVAILABLE ------------------
def test_a_checkpoint_without_a_declared_metadata_hash_is_not_available(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "tf_available", lambda: True)
    ckpt = tmp_path / "model.h5"
    ckpt.write_bytes(b"\x00" * 120)   # never trained, never exported
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"feature_width": 8, "trained": False}),
                    encoding="utf-8")
    import hashlib
    sha = hashlib.sha256(ckpt.read_bytes()).hexdigest()

    report = inference.verify_checkpoint(ckpt, sha, meta, None)
    assert report["state"] != "AVAILABLE", (
        "metadata_path without a declared metadata hash must not reach "
        f"AVAILABLE (got {report['state']!r})")


def test_a_checkpoint_without_any_metadata_is_not_available(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "tf_available", lambda: True)
    ckpt = tmp_path / "model.h5"
    ckpt.write_bytes(b"\x00" * 120)
    import hashlib
    sha = hashlib.sha256(ckpt.read_bytes()).hexdigest()

    report = inference.verify_checkpoint(ckpt, sha, None, None)
    assert report["state"] != "AVAILABLE", (
        "a checkpoint with no sibling metadata must not reach AVAILABLE "
        f"(got {report['state']!r})")


# ---- 4. a configured policy must not be silently ignored ----------------
def test_the_kernel_refuses_a_policy_when_time_alignment_is_disabled():
    from CODE.leo_sim import kernel

    cfg, rows, geometry, _meta = _disabled_fixture()
    policy = inference.WidthAdaptiveFixedPolicy(seed=5)
    with pytest.raises(kernel.KernelError) as excinfo:
        kernel.Kernel(cfg, rows, geometry=geometry, decision_sink=[],
                      inference_policy=policy)
    assert "time_alignment" in str(excinfo.value)


# ---- 5. the policy must actually DRIVE the action ------------------------
def test_the_policy_choice_drives_the_action_not_the_scorer():
    """A policy that consistently prefers a different legal direction must win.

    The reviewed suite asserted that two identical runs agree, which holds even
    if the kernel ignores the policy entirely.  This one cannot pass that way.
    """
    from CODE.leo_sim import kernel

    cfg, rows, geometry, _meta = _branch_fixture()

    class PreferS(inference.InferencePolicy):
        name = "prefer-S"
        parameter_sha256 = "constant"

        def __init__(self):
            self.calls = 0

        def act(self, snapshot):
            legal = tuple(snapshot.legal_directions)
            self.calls += 1
            return "S" if "S" in legal else legal[0]

        def counters(self):
            return {"calls": self.calls, "training": False}

    policy = PreferS()
    sink = []
    kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=sink,
                          timeline_sink=[], inference_policy=policy)
    forwards = [r for r in sink if r.get("kind") == "forward"]
    assert forwards
    assert policy.calls == len(forwards)
    for row in forwards:
        expected = "S" if "S" in row["candidates"] else row["candidates"][0]
        assert row["chosen"] == expected, (
            f"{row['decision_id']}: the scorer overrode the policy "
            f"(chose {row['chosen']!r}, policy said {expected!r})")
