#!/usr/bin/env python3
"""Build the Scene-A protocol-causality diagnostic arm set.

This command only materializes deterministic traces, simulator configs and a
plan manifest. It does not run the simulator. The VM runner consumes the
configs after the diagnostic release is pinned.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from CODE.leo_sim import config as leo_config  # noqa: E402
from CODE.scripts.build_three_phase_trace import build  # noqa: E402
from CODE.scripts.pigou_study import cell_config  # noqa: E402


BASE_RETRY = {
    "enabled": False,
    "max_retries": 2,
    "retry_fraction": 0.65,
    "backoff_s": 0.040,
    "jitter_s": 0.020,
    "attempt_timeout_s": 0.240,
    "stop_reinject_at_s": 9.3,
    "discovery": "endpoint_timeout",
    "backoff_policy": "fixed",
    "deadline_policy": "endpoint_only",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def _retry(*, enabled: bool, backoff_policy: str) -> dict[str, object]:
    value = deepcopy(BASE_RETRY)
    value["enabled"] = enabled
    value["backoff_policy"] = backoff_policy
    return value


def _config(base: dict[str, object], *, algorithm: str,
            retry: dict[str, object]) -> dict[str, object]:
    raw = cell_config(base["config"], {"alg": algorithm})
    raw["demand"]["retry"] = deepcopy(retry)
    leo_config.resolve_config(raw)
    return raw


def _emit_arm(root: Path, manifest: dict, *, group: str, name: str,
              base: dict[str, object], algorithm: str,
              retry: dict[str, object]) -> None:
    raw = _config(base, algorithm=algorithm, retry=retry)
    path = root / "configs" / f"{name}.json"
    _write_json(path, raw)
    resolved = leo_config.resolve_config(raw)
    manifest["arms"].append({
        "name": name,
        "group": group,
        "algorithm": algorithm,
        "config_path": str(path),
        "config_sha256": resolved["sha256"],
        "trace_path": str(base["trace_path"]),
        "trace_sha256": base["trace_sha256"],
        "packet_bits": int(raw["demand"]["packet_bits"]),
        "multiplier": float(base["multiplier"]),
        "offered_mbps": float(base["offered_mbps"]),
        "routing_policy": str(raw["routing"]["policy"]),
        "time_alignment_enabled": bool(raw["time_alignment"]["enabled"]),
        "retry": deepcopy(retry),
    })


def build_plan(out: Path, *, seed: int) -> dict[str, object]:
    if out.is_symlink():
        raise ValueError("output directory may not be a symlink")
    out.mkdir(parents=True, exist_ok=True)
    if not out.is_dir():
        raise ValueError("output path must be a directory")
    out = out.resolve()
    for child in ("trace-64k", "trace-12k", "configs"):
        (out / child).mkdir()

    main_64k = build(
        out / "trace-64k",
        scene="A",
        seed=seed,
        multiplier=2.8,
        interval=0.05,
        offered_mbps=235.0,
        packet_bits=64_000,
    )
    granularity_64k = build(
        out / "trace-64k-granularity",
        scene="A",
        seed=seed,
        multiplier=1.4,
        interval=0.05,
        offered_mbps=235.0,
        packet_bits=64_000,
    )
    granularity_12k = build(
        out / "trace-12k",
        scene="A",
        seed=seed,
        multiplier=1.4,
        interval=0.05,
        offered_mbps=235.0,
        packet_bits=12_000,
    )

    manifest = {
        "schema": "pigou-protocol-diagnostic-plan/v1",
        "status": "PLANNED_NOT_RUN",
        "scene": "A",
        "seed": seed,
        "main_contrast": (
            "retry off versus endpoint-visible exponential timeout; "
            "reported separately from backoff and packet-granularity sensitivities"
        ),
        "acceptance_requires_vm_receipts": True,
        "arms": [],
    }

    for group, base in (
        ("main", main_64k),
        ("granularity-64k", granularity_64k),
        ("granularity-12k", granularity_12k),
    ):
        for algorithm in ("B1", "B2"):
            prefix = "sp" if algorithm == "B1" else "dyn"
            if group == "main":
                _emit_arm(
                    out, manifest, group=group, name=f"{prefix}-retry-off",
                    base=base, algorithm=algorithm,
                    retry=_retry(enabled=False, backoff_policy="fixed"),
                )
                _emit_arm(
                    out, manifest, group=group, name=f"{prefix}-rto-fixed",
                    base=base, algorithm=algorithm,
                    retry=_retry(enabled=True, backoff_policy="fixed"),
                )
                _emit_arm(
                    out, manifest, group=group, name=f"{prefix}-rto-exp",
                    base=base, algorithm=algorithm,
                    retry=_retry(enabled=True, backoff_policy="exponential"),
                )
            else:
                _emit_arm(
                    out, manifest, group=group, name=f"{prefix}-{group}",
                    base=base, algorithm=algorithm,
                    retry=_retry(enabled=True, backoff_policy="exponential"),
                )

    for item in manifest["arms"]:
        trace = Path(item["trace_path"])
        if _sha256(trace) != item["trace_sha256"]:
            raise ValueError(f"trace changed while planning: {trace}")
    manifest["arm_count"] = len(manifest["arms"])
    manifest["semantic_scope"] = {
        "physical_router_deadline": "disabled by endpoint_only policy",
        "feedback_visibility": "queued-loss discovery waits for endpoint timeout",
        "retry_backoff": "factor 1 (fixed) or 2**retry_index (exponential)",
        "unsupported": [
            "per-route RTT/PTO estimator",
            "ACK loss and ACK-delay semantics",
            "receiver reassembly and duplicate suppression",
        ],
    }
    _write_json(out / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=101)
    args = parser.parse_args(argv)
    manifest = build_plan(args.out, seed=args.seed)
    print(json.dumps({
        "manifest_path": str((args.out / "manifest.json").resolve()),
        "arm_count": manifest["arm_count"],
        "status": manifest["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
