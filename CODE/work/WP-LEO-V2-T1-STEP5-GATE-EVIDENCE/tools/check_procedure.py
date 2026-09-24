#!/usr/bin/env python3
"""Mechanical procedure gate (step-5 batch, revision R06).

THE STRUCTURAL GUARANTEE (G0)
=============================
Four review rounds tried to close "the procedure points at the wrong revision" by
SCANNING human prose for suspicious experiment ids, and each patch was evaded: the
R04 gate scanned only fenced blocks (an unfenced command passed); the R05 gate
scanned the whole document (an id split across backslash-continued lines, or a
lower-case id, passed). Scanning human text is whack-a-mole.

G0 therefore does not scan. It calls the reviewed generator
tools/procedure_commands.py to DERIVE the four run commands from the compiled
run-manifests and requires the procedure to contain the derived marked block
BYTE-FOR-BYTE. Commands are reproduced, not checked: any deviation - fenced,
unfenced, split, re-cased, variable-built, or edited by one character - is simply
not the generated text. G0's controls (printed on every run) edit ONE character
inside the block and require detection, and remove the markers and require
detection. A control that does not trigger makes G0 an UNPROVEN GATE.

G1a/G1b ARE HYGIENE ONLY and are explicitly NOT the structural guarantee: they
remain because they catch realistic accidental drift cheaply, but they are known
to be evadable by line-splitting and re-casing.

Every check runs a negative control in the same invocation and prints it. A check
whose control does not trigger is an UNPROVEN GATE and does not count as passed;
all_checks_ok is true only when no executed check failed AND none is unproven.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parents[3]))

import procedure_commands as PC  # noqa: E402  (reviewed generator, bound in the artifact set)

SCHEMA = "leo-sim-step5-procedure-gate/v4"
FENCE = chr(96) * 3
NL = chr(10)
REVISION_ID = re.compile(r"EXP-[A-Za-z0-9_-]*-R(\d\d)\b")
UNSUPPORTED_METRIC = "definitely_not_a_supported_metric_xyz"
F2_KEY = "node_process_delay_s"
STRUCTURAL_CHECK = "G0_procedure_contains_generated_run_commands_byte_for_byte"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fence_index(text: str):
    lines, inside, flags = text.splitlines(), False, []
    for line in lines:
        if line.strip().startswith(FENCE):
            flags.append(False)
            inside = not inside
            continue
        flags.append(inside)
    return lines, flags


def bash_blocks(text: str):
    out, current, inside = [], [], False
    for line in text.splitlines():
        if line.strip().startswith(FENCE):
            if inside:
                out.append(NL.join(current)); current, inside = [], False
            else:
                inside = True
            continue
        if inside:
            current.append(line)
    if inside:
        out.append(NL.join(current))
    return out


def _verdict(name, ok, control, control_triggered, detail, skipped_reason=None, structural=False):
    entry = {"check": name, "ok": bool(ok), "control": control,
             "control_triggered": bool(control_triggered), "proven": bool(control_triggered),
             "detail": detail, "structural_guarantee": bool(structural)}
    if skipped_reason:
        entry["skipped_reason"] = skipped_reason
    return entry


# ------------------------------------------------------------------ G0
def _mutate_one_char(block: str):
    """Change exactly one character inside the generated block."""
    marker = "R06"
    i = block.find(marker)
    if i < 0:
        marker = "-R0"
        i = block.find(marker)
        if i < 0:
            return block, "no mutable marker found"
    j = i + len(marker) - 1
    old = block[j]
    new = "7" if old != "7" else "8"
    mutated = block[:j] + new + block[j + 1:]
    return mutated, "character at offset " + str(j) + " changed from " + repr(old) + " to " + repr(new)


def check_g0(root, procedure_text, experiment_ids, results):
    block = PC.generate_block(root, experiment_ids)
    contained = block in procedure_text
    cells_n = len(list(PC.cells(root, experiment_ids)))

    mutated, desc = _mutate_one_char(block)
    mutated_doc = procedure_text.replace(block, mutated, 1) if contained else procedure_text + mutated
    detected_single = block not in mutated_doc

    # Marker control: remove the two marker lines from the DOCUMENT (leaving the
    # fenced commands) and require the containment check to fail. Stripping the
    # markers from the block itself would be wrong: the remaining body is exactly
    # what legitimately appears inside the marked region.
    no_markers_doc = procedure_text.replace(PC.BEGIN + NL, "", 1).replace(PC.END + NL, "", 1)
    detected_markers = block not in no_markers_doc

    ctrl_ok = bool(detected_single and detected_markers)
    control = {"single_character_mutation": {"edit": desc, "detected": bool(detected_single)},
               "marker_removal": {"detected": bool(detected_markers)}}
    if not ctrl_ok:
        control["note"] = "a control did not trigger - G0 is UNPROVEN and must not be counted as passed"
    results.append(_verdict(
        STRUCTURAL_CHECK, contained, control, ctrl_ok,
        {"generated_bytes": len(block.encode("utf-8")), "cells": cells_n,
         "generator": "CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/procedure_commands.py",
         "how": "derive the run commands from the run-manifests and require this document to contain "
                "the derived marked block byte-for-byte; no prose scanning is involved",
         "markers": {"begin": PC.BEGIN, "end": PC.END}},
        structural=True))


# ------------------------------------------------------------------ G1a / G1b (hygiene)
def _foreign_ids(text, own):
    seen = sorted({m.group(0) for m in REVISION_ID.finditer(text)})
    return seen, sorted(t for t in seen if t not in own)


def check_g1a(procedure_text, own_set, results):
    seen, foreign = _foreign_ids(procedure_text, own_set)
    if own_set:
        first = sorted(own_set)[0]
        injected = first[:-3] + "R99"
        _s, ctrl_foreign = _foreign_ids(procedure_text + NL + "See also " + injected + "." + NL, own_set)
        ctrl_ok = injected in ctrl_foreign
        control = {"injected_id": injected, "injected_outside_fences": True, "detected": bool(ctrl_ok)}
    else:
        ctrl_ok, control = False, {"detected": False, "note": "no own id to mutate"}
    results.append(_verdict("G1a_HYGIENE_no_foreign_revision_anywhere_in_document",
                            not foreign, control, ctrl_ok,
                            {"revision_ids_in_document": seen, "foreign": foreign,
                             "scope_note": "HYGIENE ONLY - evadable by line-splitting and re-casing; "
                                           "the structural guarantee is " + STRUCTURAL_CHECK}))


def _planned_cells(root, experiment_ids):
    planned = {}
    for eid in experiment_ids:
        man = json.loads((root / "EXPERIMENTS" / eid / "run-manifest.json").read_text())
        for cell in man["cells"]:
            planned[cell["run_id"]] = cell["config_path"]
    return planned


def _is_command_like(line: str) -> bool:
    return "run-remote.sh" in line and ("--config" in line or "--runtime-kind" in line)


def check_g1b(root, procedure_text, experiment_ids, results):
    lines, inside = _fence_index(procedure_text)
    outside = [{"line_number": i + 1, "line": line.strip()[:120]}
               for i, line in enumerate(lines) if _is_command_like(line) and not inside[i]]
    ctrl_ok = False
    control = {"injected": "unfenced complete run-remote invocation", "detected": False}
    if experiment_ids:
        own = sorted(experiment_ids)[0]
        bogus = ("Prose: CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 "
                 "--config EXPERIMENTS/" + own + "/resolved/NOPE.leo-sim.yaml "
                 "--authorization EXPERIMENTS/" + own + "/authorization.json --session bogus")
        l2, in2 = _fence_index(procedure_text + NL + bogus + NL)
        hits = [i + 1 for i, line in enumerate(l2) if _is_command_like(line) and not in2[i]]
        ctrl_ok = len(hits) >= 1
        control = {"injected": "unfenced complete run-remote invocation", "detected": bool(ctrl_ok),
                   "hit_line_numbers": hits[:3]}
    results.append(_verdict("G1b_HYGIENE_run_commands_are_fenced", not outside, control, ctrl_ok,
                            {"command_like_lines_outside_any_fence": outside,
                             "scope_note": "HYGIENE ONLY - known evadable by line continuation; the "
                                           "structural guarantee is " + STRUCTURAL_CHECK}))


# ------------------------------------------------------------------ G2
def _metric_fixture():
    receipt = {"totals": {"delivered_bits": 1000.0, "terminal_loss_bits": 0.0,
                          "in_system_bits_at_stop": 0.0}, "fate_counts": {"DELIVERED": 1}}
    ledgers = {"congestion_metrics": {
        "access_admission_rate": 1.0, "network_delivery_rate_by_horizon": 1.0,
        "packets": {"1": {"e2e_s": 1.5, "total_queue_wait_s": 0.25, "tx_s": 1.0, "prop_s": 0.25}},
        "links": {"isl:0:1": {"stage": "isl", "utilization": 0.5},
                  "gsl:uplink:0:A": {"stage": "uplink", "utilization": 0.25}}}}
    return receipt, ledgers


def check_g2(root, experiment_ids, results):
    from CODE.experiment_platform import v2_analysis as V
    receipt, ledgers = _metric_fixture()
    accepted, rejected = [], []
    for name in ("delivery_rate", "delivered_bits", "terminal_loss_bits",
                 "in_system_bits_at_stop", "access_admission_rate",
                 "network_delivery_rate_by_horizon", "e2e_delay_mean_s",
                 "queue_wait_mean_s", "tx_time_mean_s", "propagation_time_mean_s",
                 "link_utilization_mean", "service_window_utilization_mean",
                 "isl_link_utilization_mean", "isl_link_utilization_max"):
        try:
            V._metric_from_result(receipt, ledgers, name); accepted.append(name)
        except V.V2AnalysisError as exc:
            rejected.append({"metric": name, "error": str(exc)[:120]})
    declared = {}
    for eid in experiment_ids:
        ana = json.loads((root / "EXPERIMENTS" / eid / "analysis-request.json").read_text())
        declared[eid] = ana["analysis"]["primary_metric"]
    bad = [{"experiment": eid, "primary_metric": m} for eid, m in declared.items() if m not in accepted]
    try:
        V._metric_from_result(receipt, ledgers, UNSUPPORTED_METRIC)
        ctrl_ok, control = False, {"probe": UNSUPPORTED_METRIC, "rejected": False}
    except V.V2AnalysisError as exc:
        ctrl_ok, control = True, {"probe": UNSUPPORTED_METRIC, "rejected": True, "error": str(exc)[:120]}
    results.append(_verdict("G2_declared_primary_metric_accepted_by_toolchain", not bad, control, ctrl_ok,
                            {"declared": declared, "accepted_metrics": accepted, "rejected_declared": bad}))


# ------------------------------------------------------------------ G3 (derived commands)
def _parse_generated_cells(block: str):
    out = []
    for chunk in block.split(FENCE + "bash"):
        if "--config" not in chunk:
            continue
        cfg = re.search(r"--config\s+(\S+)", chunk)
        auth = re.search(r"--authorization\s+(\S+)", chunk)
        sess = re.search(r"--session\s+(\S+)", chunk)
        if cfg and auth and sess:
            out.append({"config": cfg.group(1), "authorization": auth.group(1), "session": sess.group(1)})
    return out


def check_g3(root, procedure_text, experiment_ids, results, phase):
    from CODE.experiment_platform import authorize_experiment as A
    block = PC.generate_block(root, experiment_ids)
    if phase == "pre-review":
        results.append(_verdict("G3_derived_commands_reach_launcher_decision_point", True, {}, False, {},
                                skipped_reason="phase pre-review: no authorization exists yet; run --phase all"))
        return
    derived = _parse_generated_cells(block)
    per_cell, ok = [], True
    for cell in derived:
        cfg_path = (root / cell["config"]).resolve()
        auth_path = (root / cell["authorization"]).resolve()
        run_id = Path(cell["config"]).name.removesuffix(".leo-sim.yaml")
        entry = {"run_id": run_id, "config": cell["config"], "session": cell["session"]}
        if not auth_path.is_file():
            entry["authorization_binding"] = "NO_AUTHORIZATION_YET"; ok = False; per_cell.append(entry); continue
        try:
            A.verify_authorization_for_leo_sim_v2_config(root, auth_path, cfg_path, run_id)
            entry["authorization_binding"] = "ACCEPT"
        except Exception as exc:
            entry["authorization_binding"] = "REJECT: " + type(exc).__name__ + ": " + str(exc)[:110]
            ok = False
        proc = subprocess.run(
            [sys.executable, "-m", "CODE.experiment_platform.v2_serial_gate",
             "--root", str(root), "--experiment", str(cfg_path.parents[1]),
             "--authorization", str(auth_path), "--next-run-id", run_id],
            cwd=str(root), capture_output=True, text=True)
        entry["serial_gate"] = "ACCEPT" if proc.returncode == 0 else \
            "REJECT(exit=" + str(proc.returncode) + "): " + (proc.stderr or proc.stdout).strip()[:140]
        if proc.returncode != 0:
            ok = False
        per_cell.append(entry)
    ctrl, ctrl_ok = [], True
    for cell in derived:
        cfg_path = (root / cell["config"]).resolve()
        auth_path = (root / cell["authorization"]).resolve()
        if not auth_path.is_file():
            ctrl_ok = False; ctrl.append({"note": "no authorization to test the control against"}); continue
        run_id = Path(cell["config"]).name.removesuffix(".leo-sim.yaml") + "-NONEXISTENT"
        try:
            A.verify_authorization_for_leo_sim_v2_config(root, auth_path, cfg_path, run_id)
            ctrl_ok = False; ctrl.append({"wrong_run_id": run_id, "result": "ACCEPTED (control failed)"})
        except Exception as exc:
            ctrl.append({"wrong_run_id": run_id, "result": "REJECTED: " + type(exc).__name__})
    results.append(_verdict("G3_derived_commands_reach_launcher_decision_point", ok,
                            {"wrong_run_id_probe": ctrl}, ctrl_ok,
                            {"how": "config/authorization/session are taken from the GENERATED commands",
                             "derived_cells": len(derived), "cells": per_cell}))


# ------------------------------------------------------------------ G4
def _binding_problems(arts, procedure_path, root, gate_path, generator_path):
    if not str(gate_path).startswith(str(root)):
        return ["gate path " + str(gate_path) + " is NOT inside root " + str(root)], None, None
    rel_gate = str(gate_path.relative_to(root))
    rel_proc = str(procedure_path.relative_to(root))
    problems = []
    for rel, path, label in ((rel_gate, gate_path, "gate"),
                             (rel_proc, procedure_path, "procedure"),
                             (str(generator_path.relative_to(root)), generator_path, "generator")):
        if rel not in arts:
            problems.append(label + " not bound: " + rel)
        elif arts[rel] != sha256_file(path):
            problems.append(label + " bound with a stale hash")
    return problems, rel_gate, rel_proc


def check_g4(root, procedure, artifact_set, results):
    gate_path = Path(__file__).resolve()
    generator_path = TOOLS / "procedure_commands.py"
    if not artifact_set.is_file():
        results.append(_verdict("G4_gate_generator_procedure_inside_reviewed_set", False, {}, False,
                                {"reason": "artifact-set.json not found"}))
        return
    arts = json.loads(artifact_set.read_text())
    problems, rel_gate, rel_proc = _binding_problems(arts, procedure, root, gate_path, generator_path)
    ctrl_ok, ctrl_problems = False, []
    if rel_gate is not None:
        synth = dict(arts); synth[rel_gate] = "0" * 64
        ctrl_problems, _g, _p = _binding_problems(synth, procedure, root, gate_path, generator_path)
        ctrl_ok = bool(ctrl_problems)
    control = {"injected": "gate hash replaced with zeros in a synthetic set",
               "detected": bool(ctrl_problems), "control_problems": ctrl_problems}
    results.append(_verdict("G4_gate_generator_procedure_inside_reviewed_set", not problems, control, ctrl_ok,
                            {"gate": rel_gate, "procedure": rel_proc,
                             "generator": str(generator_path.relative_to(root)), "problems": problems}))


# ------------------------------------------------------------------ G5
def _f2_ok(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) == 0.0


def check_g5(root, experiment_ids, results):
    per_cell, ok = [], True
    for eid in experiment_ids:
        exp_dir = root / "EXPERIMENTS" / eid
        man = json.loads((exp_dir / "run-manifest.json").read_text())
        for cell in man["cells"]:
            cfg = json.loads((exp_dir / cell["config_path"]).read_text())
            ex = cfg["execution"]
            good = _f2_ok(ex.get(F2_KEY))
            if not good:
                ok = False
            per_cell.append({"run_id": cell["run_id"], F2_KEY: ex.get(F2_KEY),
                             "compute_delay_s": ex.get("compute_delay_s"), "f2_disabled": good})
    ctrl_ok = not _f2_ok(0.5)
    control = {"probe": {F2_KEY: 0.5}, "flagged_as_not_disabled": bool(ctrl_ok)}
    results.append(_verdict("G5_f2_disabled_and_actual_value_reported", ok, control, ctrl_ok,
                            {"cells": per_cell,
                             "statement": "deployment contains F2 code with F2 DISABLED; actual resolved "
                                          "values reported per cell"}))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--procedure", required=True)
    ap.add_argument("--artifact-set", required=True)
    ap.add_argument("--experiment-id", action="append", required=True)
    ap.add_argument("--phase", choices=["pre-review", "pre-run", "all"], default="all")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Path(a.root).resolve()
    procedure = (root / a.procedure).resolve()
    artifact_set = (root / a.artifact_set).resolve()
    if not procedure.is_file():
        print("gate: procedure not found: " + str(procedure), file=sys.stderr)
        return 2
    text = procedure.read_text(encoding="utf-8")
    results = []
    check_g0(root, text, a.experiment_id, results)
    check_g1a(text, set(a.experiment_id), results)
    check_g1b(root, text, a.experiment_id, results)
    check_g2(root, a.experiment_id, results)
    check_g3(root, text, a.experiment_id, results, a.phase)
    check_g4(root, procedure, artifact_set, results)
    check_g5(root, a.experiment_id, results)

    executed = [c for c in results if "skipped_reason" not in c]
    failed = [c["check"] for c in executed if not c["ok"]]
    unproven = [c["check"] for c in executed if not c["proven"]]
    skipped = [{"check": c["check"], "reason": c["skipped_reason"]}
               for c in results if "skipped_reason" in c]
    ok = not failed and not unproven
    report = {"schema": SCHEMA, "phase": a.phase, "root": str(root), "procedure": a.procedure,
              "experiment_ids": a.experiment_id, "checks": results,
              "checks_executed": len(executed), "failed_checks": failed,
              "unproven_gates": unproven, "skipped_checks": skipped, "all_checks_ok": ok,
              "structural_guarantee": STRUCTURAL_CHECK,
              "rule": ("all_checks_ok is true only when no executed check failed AND no executed check "
                       "is unproven; an unproven check means its negative control did not trigger")}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + NL, encoding="utf-8")
    print(json.dumps({"wrote": str(out), "phase": a.phase, "all_checks_ok": ok,
                      "structural_guarantee": STRUCTURAL_CHECK, "failed_checks": failed,
                      "unproven_gates": unproven, "skipped_checks": [s["check"] for s in skipped],
                      "checks_executed": len(executed)}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
