"""Train PCVR-0 from paired, same-world counterfactual outcomes.

Input is JSONL produced by CODE.experiment_platform.pcvr0_collect.  Every
sample_id must contain at least two action forks sharing the same world,
offered cohort, policy profile and branch-point fingerprint.  Validation is
leave-one-world-out: no decisions from a held-out world enter its fit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np

from CODE.algorithms.pcvr0.policy import (
    ALGORITHM_NAME, FEATURE_NAMES, MODEL_SCHEMA, OBJECTIVES,
)


DATA_SCHEMA = "pcvr0-training-row/v1"


class TrainingError(ValueError):
    """Invalid counterfactual training data or an unusable validation split."""


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainingError(f"{name} must be numeric")
    out = float(value)
    if not math.isfinite(out):
        raise TrainingError(f"{name} must be finite")
    return out


def read_jsonl(path: Path):
    raw = path.read_bytes()
    rows = []
    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrainingError(f"invalid JSON on line {line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise TrainingError(f"line {line_no} must contain an object")
        rows.append(row)
    if not rows:
        raise TrainingError("training dataset is empty")
    return rows, hashlib.sha256(raw).hexdigest()


def validate_rows(rows):
    groups = defaultdict(list)
    worlds = set()
    policy_ids = set()
    seen = set()
    for i, row in enumerate(rows):
        if row.get("schema") != DATA_SCHEMA:
            raise TrainingError(f"row {i} schema must be {DATA_SCHEMA!r}")
        for key in ("sample_id", "world_id", "cohort_id", "policy_id",
                    "branch_fingerprint", "action"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise TrainingError(f"row {i} requires non-empty {key}")
        if row["action"] not in {"N", "E", "S", "W"}:
            raise TrainingError(f"row {i} has unsupported action {row['action']!r}")
        features = row.get("features")
        if not isinstance(features, dict) or set(features) != set(FEATURE_NAMES):
            raise TrainingError(
                f"row {i} features must contain exactly FEATURE_NAMES")
        for name in FEATURE_NAMES:
            _finite(features[name], f"row {i} features.{name}")
        outcome = row.get("outcome")
        if not isinstance(outcome, dict) or set(outcome) != set(OBJECTIVES):
            raise TrainingError(
                f"row {i} outcome must contain exactly {OBJECTIVES}")
        for name in OBJECTIVES:
            value = _finite(outcome[name], f"row {i} outcome.{name}")
            if name == "delivery_loss" and not 0.0 <= value <= 1.0:
                raise TrainingError("delivery_loss must lie in [0, 1]")
            if name != "delivery_loss" and value < 0.0:
                raise TrainingError(f"{name} must be nonnegative")
        if row.get("natural_end") is not True:
            raise TrainingError(f"row {i} is not from a natural simulation end")
        key = (row["sample_id"], row["action"])
        if key in seen:
            raise TrainingError(f"duplicate action row for sample/action {key}")
        seen.add(key)
        groups[row["sample_id"]].append(row)
        worlds.add(row["world_id"])
        policy_ids.add(row["policy_id"])

    if len(worlds) < 3:
        raise TrainingError(
            "need at least three distinct world_id values for leave-one-world-out validation")
    if len(policy_ids) != 1:
        raise TrainingError(
            "all worlds must use the same policy_id; do not mix policy profiles")
    for sample_id, group in groups.items():
        if len({r["action"] for r in group}) < 2:
            raise TrainingError(f"sample {sample_id} has fewer than two action forks")
        for key in ("world_id", "cohort_id", "policy_id", "branch_fingerprint"):
            if len({r[key] for r in group}) != 1:
                raise TrainingError(
                    f"sample {sample_id} mixes {key}; refusing unpaired outcomes")
    return groups, sorted(worlds), next(iter(policy_ids))


def _fit_head(x, y, alpha):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    z = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    penalty = np.eye(z.shape[1], dtype=np.float64) * float(alpha)
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(z.T @ z + penalty, z.T @ y)
    return mean, scale, beta


def _predict(x, mean, scale, beta):
    z = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    return z @ beta


def _nearest_rank_quantile(values, q):
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise TrainingError("cannot calculate a validation quantile from no errors")
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def train_rows(rows, dataset_sha256: str, ridge_alpha: float = 1.0):
    groups, worlds, policy_id = validate_rows(rows)
    x_all = np.asarray(
        [[_finite(r["features"][name], name) for name in FEATURE_NAMES]
         for r in rows], dtype=np.float64)
    y_all = {
        target: np.asarray(
            [_finite(r["outcome"][target], target) for r in rows],
            dtype=np.float64)
        for target in OBJECTIVES
    }

    # Grouped leave-one-world-out validation. All action forks from a world
    # stay together, preventing same-world leakage across train/validation.
    oof_errors = {target: [] for target in OBJECTIVES}
    for held_out in worlds:
        train_idx = [i for i, row in enumerate(rows) if row["world_id"] != held_out]
        valid_idx = [i for i, row in enumerate(rows) if row["world_id"] == held_out]
        if len({rows[i]["world_id"] for i in train_idx}) < 2:
            raise TrainingError("each validation fold needs at least two training worlds")
        for target in OBJECTIVES:
            mean, scale, beta = _fit_head(
                x_all[train_idx], y_all[target][train_idx], ridge_alpha)
            predicted = _predict(
                x_all[valid_idx], mean, scale, beta)
            oof_errors[target].extend(
                np.abs(predicted - y_all[target][valid_idx]).tolist())

    validation_error = {
        target: _nearest_rank_quantile(oof_errors[target], 0.90)
        for target in OBJECTIVES
    }
    final_mean = None
    final_scale = None
    final_coefficients = {}
    for target in OBJECTIVES:
        mean, scale, beta = _fit_head(x_all, y_all[target], ridge_alpha)
        if final_mean is None:
            final_mean, final_scale = mean, scale
        final_coefficients[target] = beta.tolist()

    return {
        "schema": MODEL_SCHEMA,
        "algorithm": ALGORITHM_NAME,
        "status": "trained",
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": final_mean.tolist(),
        "feature_scale": final_scale.tolist(),
        "coefficients": final_coefficients,
        "validation_abs_error_p90": validation_error,
        "validation_method": "leave_one_world_out",
        "validation_worlds": worlds,
        "training_worlds": worlds,
        "training_rows": len(rows),
        "paired_decision_samples": len(groups),
        "training_policy_id": policy_id,
        "dataset_sha256": dataset_sha256,
        "ridge_alpha": float(ridge_alpha),
        "objective_order": list(OBJECTIVES),
        "selection_guard": (
            "lexicographic; switch only when predicted improvement exceeds "
            "twice the leave-one-world-out p90 absolute error plus tie tolerance"
        ),
        "limitations": [
            "empirical validation errors are not formal confidence bounds",
            "one-step action forks evaluate responses under the recorded policy profile",
            "distribution shift after repeated policy changes is not certified",
        ],
    }


def publish_model(model, out_dir: Path):
    if out_dir.exists():
        raise TrainingError(f"output directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    source_policy = Path(__file__).with_name("policy.py")
    target_policy = out_dir / "policy.py"
    shutil.copyfile(source_policy, target_policy)
    model_path = out_dir / "model.json"
    model_path.write_text(
        json.dumps(model, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = {
        "schema": "leo-policy-plugin/v1",
        "name": "pcvr0",
        "entry_module": "policy.py",
        "entry_class": "PCVRPolicy",
        "module_sha256": digest(target_policy),
        "checkpoint_sha256": {"model.json": digest(model_path)},
        "info_rights": [
            "frozen_local_snapshot",
            "actually_received_resource_history",
            "legal_directions",
            "time_alignment_candidate_scores",
            "no_future_truth_or_kernel_access",
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "training_report.json").write_text(
        json.dumps({
            "algorithm": ALGORITHM_NAME,
            "status": "trained",
            "training_worlds": model["training_worlds"],
            "training_rows": model["training_rows"],
            "paired_decision_samples": model["paired_decision_samples"],
            "validation_abs_error_p90": model["validation_abs_error_p90"],
            "dataset_sha256": model["dataset_sha256"],
            "model_sha256": digest(model_path),
            "policy_sha256": digest(target_policy),
        }, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train the PCVR-0 policy model")
    parser.add_argument("--data", type=Path, required=True, help="paired PCVR-0 JSONL dataset")
    parser.add_argument("--out-dir", type=Path, required=True, help="new directory for frozen plugin bundle")
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.ridge_alpha) or args.ridge_alpha < 0:
        raise TrainingError("--ridge-alpha must be finite and >= 0")
    rows, dataset_sha = read_jsonl(args.data)
    model = train_rows(rows, dataset_sha, args.ridge_alpha)
    manifest = publish_model(model, args.out_dir)
    print(json.dumps({
        "status": "trained",
        "algorithm": ALGORITHM_NAME,
        "out_dir": str(args.out_dir),
        "worlds": model["training_worlds"],
        "rows": model["training_rows"],
        "paired_samples": model["paired_decision_samples"],
        "validation_abs_error_p90": model["validation_abs_error_p90"],
        "manifest": manifest,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
