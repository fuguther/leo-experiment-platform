"""Deterministically apply the preregistered R03 ISL-pressure decision tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


class PressureDecisionError(ValueError):
    """Persisted V2 diagnostics cannot support an unambiguous decision."""


NONCONGESTION_FATES = (
    "ACCESS_REJECTED",
    "ACCESS_QUEUE_OVERFLOW",
    "GEOMETRY_LOSS_IN_FLIGHT",
    "RANDOM_OUTAGE_IN_FLIGHT",
    "NO_ROUTE",
)
INVALID_OVERFLOW_FATES = ("HOLDING_QUEUE_OVERFLOW",)
ISL_OVERFLOW_FATE = "ISL_QUEUE_OVERFLOW"
CONTROL_FAILURES = ("expired", "lost", "geometry_lost", "overflow")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PressureDecisionError(f"{label} must be a mapping")
    return value


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PressureDecisionError(f"{label} must be a non-negative integer")
    return value


def _counts(value: Any, label: str) -> dict[str, int]:
    raw = _mapping(value, label)
    return {str(key): _count(item, f"{label}.{key}")
            for key, item in raw.items()}


def _links(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value):
        raise PressureDecisionError(f"{label} must be a list of link ids")
    if len(set(value)) != len(value):
        raise PressureDecisionError(f"{label} contains duplicate link ids")
    return value


def _arm(result: Any, label: str) -> dict[str, Any]:
    raw = _mapping(result, label)
    if raw.get("evidence_class") != "v2_external_witness":
        raise PressureDecisionError(
            f"{label} lacks current external-witness evidence")
    diagnostics = _mapping(raw.get("diagnostics"), f"{label}.diagnostics")
    mcs = _mapping(diagnostics.get("mcs"), f"{label}.mcs")
    access = _mapping(diagnostics.get("access"), f"{label}.access")
    control = _counts(diagnostics.get("control"), f"{label}.control")
    missing_control = sorted(set(CONTROL_FAILURES) - set(control))
    if missing_control:
        raise PressureDecisionError(
            f"{label}.control lacks required failure counters: "
            f"{missing_control}")
    fates = _counts(diagnostics.get("fate_counts"), f"{label}.fate_counts")
    drain = _mapping(diagnostics.get("drain"), f"{label}.drain")
    windowed = _mapping(
        diagnostics.get("windowed_isl"), f"{label}.windowed_isl")
    return {
        "zero_rate_holds": _count(
            mcs.get("zero_rate_holds"), f"{label}.mcs.zero_rate_holds"),
        "access_grants": _count(
            access.get("grants"), f"{label}.access.grants"),
        "control": control,
        "fates": fates,
        "isl_queue_overflows": fates.get(ISL_OVERFLOW_FATE, 0),
        "in_system_at_stop_packets": _count(
            drain.get("in_system_at_stop_packets"),
            f"{label}.drain.in_system_at_stop_packets"),
        "unmatched_isl_queue_entries": _count(
            drain.get("unmatched_isl_queue_entries"),
            f"{label}.drain.unmatched_isl_queue_entries"),
        "pressure_links": _links(
            windowed.get("pressure_candidate_link_ids"),
            f"{label}.windowed_isl.pressure_candidate_link_ids"),
    }


def classify_verified_pair(
        manifest: dict[str, Any], *, control_arm: str,
        candidate_arm: str) -> dict[str, Any]:
    """Classify one exact V2 pair; never infer evidence missing from manifest."""
    raw = _mapping(manifest, "analysis manifest")
    if raw.get("schema") != "leo-sim-v2-analysis/v1" \
            or raw.get("status") != "VERIFIED":
        raise PressureDecisionError("analysis manifest must be VERIFIED V2")
    if not isinstance(control_arm, str) or not control_arm \
            or not isinstance(candidate_arm, str) or not candidate_arm \
            or control_arm == candidate_arm:
        raise PressureDecisionError("control and candidate arms must be distinct")
    results = raw.get("run_results")
    if not isinstance(results, list):
        raise PressureDecisionError("analysis manifest run_results must be a list")
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if not isinstance(result, dict) or not isinstance(
                result.get("arm_id"), str):
            raise PressureDecisionError("every run result needs an arm_id")
        by_arm.setdefault(result["arm_id"], []).append(result)
    for arm_id in (control_arm, candidate_arm):
        if len(by_arm.get(arm_id, [])) != 1:
            raise PressureDecisionError(
                f"expected exactly one result for arm {arm_id}")
    if set(by_arm) != {control_arm, candidate_arm}:
        raise PressureDecisionError("analysis cohort contains undeclared arms")

    control = _arm(by_arm[control_arm][0], control_arm)
    candidate = _arm(by_arm[candidate_arm][0], candidate_arm)
    physical: list[str] = []
    for arm_id, arm in ((control_arm, control), (candidate_arm, candidate)):
        if arm["zero_rate_holds"]:
            physical.append(
                f"{arm_id} has {arm['zero_rate_holds']} zero-rate holds")
        for fate in INVALID_OVERFLOW_FATES:
            count = arm["fates"].get(fate, 0)
            if count:
                physical.append(f"{arm_id} has {count} {fate} fates")
    if candidate["access_grants"] != control["access_grants"]:
        physical.append(
            f"access grants differ: {control_arm}={control['access_grants']}, "
            f"{candidate_arm}={candidate['access_grants']}")
    for fate in NONCONGESTION_FATES:
        increase = candidate["fates"].get(fate, 0) - control["fates"].get(
            fate, 0)
        if increase > 0:
            physical.append(
                f"{candidate_arm} exceeds {control_arm} by {increase} {fate}")
    for field in CONTROL_FAILURES:
        increase = candidate["control"].get(field, 0) - control[
            "control"].get(field, 0)
        if increase > 0:
            physical.append(
                f"{candidate_arm} control {field} exceeds {control_arm} "
                f"by {increase}")
    if physical:
        classification = "PHYS_INVALID"
        reasons = physical
    else:
        drain = [
            f"{arm_id} has {arm['in_system_at_stop_packets']} "
            "packets in system at stop"
            for arm_id, arm in ((control_arm, control),
                                (candidate_arm, candidate))
            if arm["in_system_at_stop_packets"]
        ]
        drain.extend(
            f"{arm_id} has {arm['unmatched_isl_queue_entries']} "
            "unmatched ISL queue entries"
            for arm_id, arm in ((control_arm, control),
                                (candidate_arm, candidate))
            if arm["unmatched_isl_queue_entries"]
        )
        if drain:
            classification = "DRAIN_INCOMPLETE"
            reasons = drain
        elif control["pressure_links"]:
            classification = "CONTROL_PRESSURE_UNBRACKETED"
            reasons = [
                f"{control_arm} already has pressure-candidate links: "
                f"{control['pressure_links']}; ISL queue overflows="
                f"{control['isl_queue_overflows']}"
            ]
        elif control["isl_queue_overflows"]:
            raise PressureDecisionError(
                f"{control_arm} has {control['isl_queue_overflows']} "
                "ISL_QUEUE_OVERFLOW fates but no localized sustained "
                "pressure episode")
        elif candidate["pressure_links"]:
            classification = "PRESSURE_CANDIDATE"
            reasons = [
                f"{control_arm} has no pressure candidate and {candidate_arm} "
                f"has: {candidate['pressure_links']}; ISL queue overflows="
                f"{candidate['isl_queue_overflows']}"
            ]
        elif candidate["isl_queue_overflows"]:
            raise PressureDecisionError(
                f"{candidate_arm} has {candidate['isl_queue_overflows']} "
                "ISL_QUEUE_OVERFLOW fates but no localized sustained "
                "pressure episode")
        else:
            classification = "NO_PRESSURE_PHYS_VALID"
            reasons = ["both arms pass validity and neither has a pressure episode"]
    return {
        "schema": "leo-sim-isl-pressure-classification/v1",
        "classification": classification,
        "control_arm": control_arm,
        "candidate_arm": candidate_arm,
        "control_pressure_link_ids": control["pressure_links"],
        "candidate_pressure_link_ids": candidate["pressure_links"],
        "control_isl_queue_overflows": control["isl_queue_overflows"],
        "candidate_isl_queue_overflows": candidate["isl_queue_overflows"],
        "reasons": reasons,
    }


def classify_persisted_pair(
        root: Path, manifest_path: Path, *, control_arm: str,
        candidate_arm: str) -> dict[str, Any]:
    """Verify the persisted V2 evidence before applying the pair classifier."""
    from CODE.experiment_platform import v2_analysis

    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise PressureDecisionError(
            "analysis manifest must be inside the project root") from exc
    ok, errors = v2_analysis.verify_persisted_analysis(root, manifest_path)
    if not ok:
        raise PressureDecisionError(
            "persisted V2 analysis verification failed: " + "; ".join(errors))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PressureDecisionError(f"analysis manifest is unreadable: {exc}") \
            from exc
    result = classify_verified_pair(
        manifest, control_arm=control_arm, candidate_arm=candidate_arm)
    result["analysis_manifest"] = str(manifest_path.relative_to(root))
    result["analysis_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    """Verify, classify and persist one preregistered V2 pressure pair."""
    parser = argparse.ArgumentParser(
        description="Verify and classify one persisted V2 ISL-pressure pair")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--control-arm", required=True)
    parser.add_argument("--candidate-arm", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out = args.out.resolve()
    try:
        out.relative_to(root)
        result = classify_persisted_pair(
            root, args.manifest, control_arm=args.control_arm,
            candidate_arm=args.candidate_arm)
    except (PressureDecisionError, ValueError) as exc:
        parser.error(str(exc))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"{result['classification']}: {out.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
