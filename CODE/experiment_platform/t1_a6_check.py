"""A6: the VM acceptance for the parts the matrix run cannot cover.

WHY A SEPARATE ENTRY POINT
-------------------------
`t1-vm.sh experiment` compiles, validates and runs the acceptance and dev
tiers.  Three things it cannot prove are proven here, on the VM, in one
bounded run:

  1. A REAL repository checkpoint is exported, read back through the A1
     reader, and the ACTION it produces is the action the run takes;
  2. the model forward pass is timed on the VM CPU (never quoted as a
     satellite timing, never as a policy result);
  3. the formal tier REFUSES without an authorization and refuses a package
     that moved after the compile.  Both refusals are the A5 boundary.

Nothing here trains: the export uses test weights and is labelled
`test_weights` in the metadata, so it can never be mistaken for a trained
policy.  Nothing here is a research result.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path

from CODE.experiment_platform import artifact_identity, t1_suite, t1_tasks
from CODE.leo_sim import config as config_mod, inference, learning

SCHEMA = "t1-a6-acceptance/v1"
CONTRACT = Path("CODE/work/WP-T1-COMPLETE/contract.yaml")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_test_checkpoint(out_dir, contract):
    """Export a REAL repository checkpoint whose weights are test weights."""
    cfg = dict(config_mod.DEFAULTS["learning"])
    cfg.update({"mode": "eval", "seed": 11, "weights_purpose": "test_weights",
                "fast_train": False})
    model = learning.TensorflowDDQN(contract, cfg, 11)
    meta = model.save_and_verify(Path(out_dir))
    return {"checkpoint": str(Path(out_dir) / "online.keras"),
            "metadata": str(Path(out_dir) / "metadata.json"),
            "checkpoint_sha256": meta["checkpoint_sha256"],
            "metadata_sha256": meta["metadata_sha256"],
            "contract": contract,
            "feature_width": learning.CONTRACT_DIMS[contract],
            "weights_purpose": meta.get("weights_purpose"),
            "trained": False}


def measure_model_forward(adapter, features, *, warmup=100, rounds=5,
                          iterations=1000):
    """Time the real forward pass.  Host CPU only, never an on-board number."""
    for _ in range(warmup):
        adapter.act(features)
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        for _ in range(iterations):
            adapter.act(features)
        samples.append((time.perf_counter() - started) / iterations)
    ordered = sorted(samples)
    return {"warmup": warmup, "rounds": rounds, "iterations": iterations,
            "per_call_s": samples, "median_s": statistics.median(samples),
            "best_s": ordered[0], "worst_s": ordered[-1],
            "clock": "time.perf_counter", "device": "host_cpu",
            "on_board": False,
            "note": "a real checkpoint forward pass on the VM CPU; NOT a "
                    "satellite timing, NOT a DDQN policy result"}


def read_back_and_decide(exported, root):
    """Load the exported artifact and let IT choose the action (A1)."""
    adapter = inference.load_fixed_adapter_from_checkpoint(
        exported["checkpoint"], exported["checkpoint_sha256"],
        exported["metadata"], exported["metadata_sha256"],
        expected_contract=exported["contract"],
        expected_width=exported["feature_width"])
    receipt = adapter.receipt()
    before = adapter.parameter_sha256()

    # a REAL branch of a REAL profile, with the checkpoint as the policy
    contract = t1_suite.yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    params = dict(contract.get("dev_parameters") or {})
    work = Path(tempfile.mkdtemp(prefix="t1-a6-", dir=str(root)))
    cfg_path = t1_suite._seed_config(contract, work, 7, params,
                                     tag="a6")
    resolved, rows, geometry, source = t1_tasks.design(config_path=cfg_path,
                                                      root=work)
    user = copy.deepcopy(resolved["config"])
    user["routing"]["learning_enabled"] = True
    user["learning"].update({
        "algorithm": "ddqn", "mode": "eval", "fixed_inference": True,
        "checkpoint_path": exported["checkpoint"],
        "checkpoint_sha256": exported["checkpoint_sha256"],
        "checkpoint_metadata_sha256": exported["metadata_sha256"], "seed": 7})
    local = config_mod.resolve_config(user)
    sink, timeline = [], []
    result = learning_kernel_run(local, rows, geometry, sink, timeline)
    forwards = [row for row in sink if row.get("kind") == "forward"]
    ledger = result["learning"] or {}
    after = adapter.parameter_sha256()
    return {"receipt": receipt, "rows": len(rows),
            "source": source,
            "forward_decisions": len(forwards),
            "chosen_directions": sorted({row["chosen"] for row in forwards}),
            "ledger_kind": ledger.get("kind"),
            "ledger_calls": ledger.get("calls"),
            "ledger_checkpoint_sha256": ledger.get("checkpoint_sha256"),
            "parameters_stable": bool(before == after),
            "training": ledger.get("training"),
            "updates": ledger.get("updates"),
            "delivered": len(result["deliveries"]),
            "forward_timing": measure_model_forward(
                adapter, [0.25] * exported["feature_width"])}


def learning_kernel_run(resolved, rows, geometry, sink, timeline):
    from CODE.leo_sim import kernel

    return kernel.run_simulation(resolved, rows, geometry=geometry,
                                 decision_sink=sink, timeline_sink=timeline)


def formal_refusals(root):
    """The A5 boundary, exercised as a NEGATIVE control on the VM."""
    out = {}
    bundle_dir = Path(root) / "formal-bundle"
    t1_suite.compile_bundle(CONTRACT, bundle_dir)
    bundle = json.loads((bundle_dir / "bundle.json").read_text(
        encoding="utf-8"))
    out["formal_cells"] = len((bundle.get("tiers") or {}).get("formal") or [])
    try:
        t1_suite.run_bundle(bundle_dir, "formal", Path(root) / "formal-run",
                            authorization=None)
        out["refused_without_authorization"] = False
        out["reason"] = "the formal tier STARTED without an authorization"
    except t1_suite.SuiteError as exc:
        out["refused_without_authorization"] = True
        out["reason"] = str(exc)
    # a package that moved after the compile must not be runnable at all
    document = json.loads((bundle_dir / "bundle.json").read_text(
        encoding="utf-8"))
    document["cells"][0]["args"] = list(document["cells"][0]["args"]) + [
        "--tampered"]
    (bundle_dir / "bundle.json").write_text(json.dumps(document),
                                            encoding="utf-8")
    try:
        t1_suite.run_bundle(bundle_dir, "formal", Path(root) / "formal-run-2",
                            authorization=None)
        out["refused_a_moved_package"] = False
    except t1_suite.SuiteError as exc:
        out["refused_a_moved_package"] = True
        out["moved_package_reason"] = str(exc)[:400]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", default="C3")
    args = parser.parse_args(argv)
    if not inference.tf_available():
        print("A6 REFUSED: this entry point must run on the host that owns "
              "the pinned loader (the VM)")
        return 2
    work = Path(tempfile.mkdtemp(prefix="t1-a6-work-", dir=str(args.root)))
    document = {"schema": SCHEMA, "contract": args.contract,
                "identity": artifact_identity.build_identity(
                    driver_paths=artifact_identity.execution_chain_paths())}
    exported = export_test_checkpoint(work / "checkpoint", args.contract)
    document["checkpoint"] = exported
    document["read_back"] = read_back_and_decide(exported, work)
    document["formal_refusals"] = formal_refusals(work)
    document["limits"] = [
        "the checkpoint carries TEST WEIGHTS: this proves the interface, "
        "not a policy",
        "the forward timing is a VM CPU number, never an on-board number",
        "the refusals are negative controls: they prove the boundary "
        "closes, not that any run was approved",
    ]
    args.out.write_text(json.dumps(document, ensure_ascii=False,
                                    sort_keys=True, indent=1) + "\n",
                        encoding="utf-8")
    ok = (document["read_back"]["ledger_kind"]
          == "checkpoint_fixed_inference_policy"
          and document["read_back"]["ledger_calls"] > 0
          and document["read_back"]["parameters_stable"] is True
          and document["formal_refusals"]["refused_without_authorization"]
          is True
          and document["formal_refusals"]["refused_a_moved_package"] is True)
    print(json.dumps({"status": "ok" if ok else "FAILED",
                      "out": str(args.out),
                      "calls": document["read_back"]["ledger_calls"],
                      "chosen": document["read_back"]["chosen_directions"]},
                     ensure_ascii=False))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
