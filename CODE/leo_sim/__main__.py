"""Canonical CLI for leo_sim V2.

    python -m CODE.leo_sim config validate <file.yaml>
    python -m CODE.leo_sim trace compile --config <file.yaml> --out <dir>
    python -m CODE.leo_sim run --config <file.yaml> [--dry-run] [--out <dir>]
    python -m CODE.leo_sim receipt verify <dir>
    python -m CODE.leo_sim platform check --out <dir>

Fail closed everywhere: unknown fields, hash mismatches, missing mechanisms
and unavailable learning dependencies all exit non-zero with a message.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from . import acceptance as acceptance_mod
from . import comparison as comparison_mod
from . import config as config_mod
from . import governance, kernel, learning, platform_check as platform_check_mod
from . import receipt as receipt_mod, trace as trace_mod


def _load(path: str) -> dict:
    return config_mod.load_config_file(path)


def _check_new_destination(target: Path) -> None:
    """Reject existing targets and every symlink in the parent chain."""
    if target.exists() or target.is_symlink():
        raise governance.IntentError(
            f"decision log destination must be new and non-symlink: {target}")
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise governance.IntentError(
            f"decision log parent must be an existing non-symlink directory: {parent}")
    cursor = parent
    while True:
        if cursor.is_symlink():
            raise governance.IntentError(
                f"decision log parent chain may not contain a symlink: {cursor}")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent


def _publish_new_file(temporary: Path, target: Path) -> None:
    """Publish without following/replacing an existing target."""
    try:
        os.link(str(temporary), str(target), follow_symlinks=False)
    except FileExistsError as exc:
        raise governance.IntentError(
            f"decision log destination appeared during publish: {target}") from exc
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


class _DecisionLogWriter:
    """Bounded-memory streaming writer for diagnostic decision rows."""

    def __init__(self, path: str):
        self.target = Path(path)
        _check_new_destination(self.target)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.target.name}.", suffix=".tmp",
            dir=str(self.target.parent))
        os.close(fd)
        self._temporary = Path(temporary_name)
        self._handle = self._temporary.open("w", encoding="utf-8")
        self.row_count = 0
        self._log_hasher = hashlib.sha256()
        self.log_sha256: str | None = None

    def append(self, row: dict) -> None:
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        self._handle.write(line)
        self._log_hasher.update(line.encode("utf-8"))
        self.row_count += 1

    def close(self) -> str:
        if self._handle is None:
            if self.log_sha256 is None:
                raise governance.IntentError("decision log writer already closed")
            return self.log_sha256
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()
        self._handle = None
        _publish_new_file(self._temporary, self.target)
        self.log_sha256 = self._log_hasher.hexdigest()
        return self.log_sha256

    def abort(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        if self._temporary.exists() and not self._temporary.is_symlink():
            self._temporary.unlink()


def _decision_manifest_target(path: Path) -> Path:
    return path.with_name(path.name + ".manifest.json")


def _write_decision_manifest(path: Path, resolved: dict, trace_manifest: dict,
                             result: dict, receipt_payload: dict, out_dir: str,
                             row_count: int, log_sha256: str) -> Path:
    target = _decision_manifest_target(path)
    _check_new_destination(target)
    payload = {
        "schema": "leo-sim-decision-log/v1",
        "log_path": str(path.resolve()),
        "log_sha256": log_sha256,
        "row_count": row_count,
        "config_sha256": resolved["sha256"],
        "trace_sha256": trace_manifest["__trace_sha256"],
        "trace_identity_sha256": trace_manifest.get("trace_identity_sha256", ""),
        "code_sha256": receipt_mod.code_sha256(),
        "result_dir": str(Path(out_dir).resolve()),
        "natural_end": bool(result["natural_end"]),
        "conservation_ok": bool(receipt_payload["conservation_ok"]),
        "receipt_sha256": hashlib.sha256(
            (Path(out_dir) / "receipt.json").read_bytes()).hexdigest(),
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False,
                                    indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _publish_new_file(temporary, target)
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise
    return target


def _cmd_config_validate(args) -> int:
    try:
        resolved = _load(args.file)
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        print(f"CONFIG INVALID: {exc}")
        return 2
    print(json.dumps({"status": "ok", "version": resolved["version"],
                      "sha256": resolved["sha256"]}, indent=2))
    if args.show:
        print(json.dumps(resolved["config"], indent=2, sort_keys=True))
    return 0


def _compile(resolved: dict, out_dir: str) -> tuple[dict, bytes, list[dict]]:
    manifest = trace_mod.compile_trace(resolved, out_dir)
    trace_bytes = (Path(out_dir) / "trace.csv").read_bytes()
    manifest_bytes = (Path(out_dir) / "manifest.json").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    rows = trace_mod.load_trace(
        str(Path(out_dir) / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"])
    return manifest, trace_bytes, rows


def _cmd_trace_compile(args) -> int:
    try:
        resolved = _load(args.config)
        manifest, _tb, _rows = _compile(resolved, args.out)
    except (config_mod.ConfigError, trace_mod.TraceError, FileNotFoundError) as exc:
        print(f"TRACE COMPILE FAILED: {exc}")
        return 2
    print(json.dumps({"status": "ok", "trace_sha256": manifest["__trace_sha256"],
                      "manifest_sha256": manifest["__sha256"],
                      "offered_packets": manifest["offered_packets"],
                      "offered_bits": manifest["offered_bits"],
                      "provenance": manifest["provenance"]}, indent=2))
    return 0


def _cmd_experiment_compile(args) -> int:
    try:
        report = governance.compile_experiment(
            Path(args.request), Path(args.out), project_root=Path.cwd())
    except governance.IntentError as exc:
        print(f"EXPERIMENT COMPILE FAILED: {exc}")
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _project_root_for(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "CODE" / "leo_sim").is_dir() and (parent / "EXPERIMENTS").is_dir():
            return parent
    raise governance.IntentError("formal V2 config is not inside a project root")


def _verify_formal_args(args, resolved: dict) -> dict | None:
    supplied = [args.authorization, args.launch_nonce, args.expect_run_id]
    if not any(supplied):
        return None
    if not all(supplied):
        raise governance.IntentError(
            "formal run requires authorization, launch_nonce and expect_run_id together")
    if len(args.launch_nonce) != 32 or any(
            c not in "0123456789abcdef" for c in args.launch_nonce):
        raise governance.IntentError("launch_nonce must be 32 lowercase hex characters")
    from CODE.experiment_platform.authorize_experiment import (
        verify_authorization_for_leo_sim_v2_config,
    )
    cfg_path = Path(args.config).resolve()
    root = _project_root_for(cfg_path)
    auth_path = Path(args.authorization).resolve()
    authorization = verify_authorization_for_leo_sim_v2_config(
        root, auth_path, cfg_path, args.expect_run_id)
    return {
        "run_id": args.expect_run_id,
        "launch_nonce": args.launch_nonce,
        "authorization_sha256": hashlib.sha256(auth_path.read_bytes()).hexdigest(),
        "config_sha256": resolved["sha256"],
        "code_sha256": receipt_mod.code_sha256(),
        "results_dir": str((root / "CODE" / "Results").resolve()),
    }


def _write_formal_witness(out_dir: str, formal: dict, receipt_payload: dict) -> None:
    out = Path(out_dir)
    if out.resolve(strict=False) != (
            Path(formal["results_dir"]) / formal["run_id"]).resolve(strict=False):
        raise governance.IntentError(
            "formal V2 output must be CODE/Results/<expected-run-id>")
    errors = receipt_mod.verify_receipt_dir(str(out))
    if errors:
        raise governance.IntentError(
            "formal V2 receipt failed self-verification: " + "; ".join(errors))
    witness = {
        "schema": "leo-sim-formal-run/v1",
        **{key: value for key, value in formal.items() if key != "results_dir"},
        "receipt_sha256": hashlib.sha256(
            (out / "receipt.json").read_bytes()).hexdigest(),
        "natural_end": receipt_payload["natural_end"],
        "conservation_ok": receipt_payload["conservation_ok"],
    }
    (out / "formal_run.json").write_text(
        json.dumps(witness, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pointers = out.parent / "_run_receipts"
    if pointers.is_symlink():
        raise governance.IntentError("formal result pointer directory may not be symbolic")
    pointers.mkdir(exist_ok=True)
    pointer = pointers / f"{formal['launch_nonce']}.txt"
    if pointer.is_symlink():
        raise governance.IntentError("formal result pointer may not be symbolic")
    pointer.write_text(str(out.resolve()) + "\n", encoding="utf-8")


def _load_precompiled(resolved: dict, trace_dir: str) -> tuple[dict, bytes, list[dict]]:
    """Consume an already-compiled immutable trace; fail closed on any
    identity mismatch with the resolved config's trace scope."""
    td = Path(trace_dir)
    mpath = td / "manifest.json"
    tpath = td / "trace.csv"
    if (not mpath.is_file() or mpath.is_symlink()
            or not tpath.is_file() or tpath.is_symlink()):
        raise trace_mod.TraceError(f"precompiled trace dir incomplete: {trace_dir}")
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except Exception as exc:
        raise trace_mod.TraceError(f"precompiled manifest unreadable: {exc}")
    if not isinstance(manifest, dict):
        raise trace_mod.TraceError("precompiled manifest must be a JSON object")
    from . import config as _config
    manifest_schema = manifest.get("schema")
    if manifest_schema != trace_mod.TRACE_MANIFEST_SCHEMA:
        raise trace_mod.TraceError(
            "precompiled trace manifest must use manifest/v2; legacy traces "
            "require explicit recompilation")
    provenance = manifest.get("provenance_contract")
    if (not isinstance(provenance, dict)
            or provenance.get("schema") != trace_mod.TRACE_PROVENANCE_SCHEMA):
        raise trace_mod.TraceError(
            "precompiled trace provenance must use provenance/v2; legacy "
            "traces require explicit recompilation")
    expected_identity = _config.trace_identity_sha256(
        resolved, manifest.get("input_sha256", ""))
    if manifest.get("trace_identity_sha256") != expected_identity:
        raise trace_mod.TraceError(
            "precompiled trace manifest trace_identity_sha256 != resolved "
            "config trace identity")
    trace_bytes = tpath.read_bytes()
    sha = hashlib.sha256(trace_bytes).hexdigest()
    if manifest.get("trace_sha256") and manifest["trace_sha256"] != sha:
        raise trace_mod.TraceError("trace.csv sha != manifest trace_sha256")
    cfg = resolved["config"]
    emission_end = cfg["demand"]["emission_end_s"]
    emission_end = (cfg["scenario"]["duration_s"]
                    if emission_end is None else float(emission_end))
    if (manifest.get("simulation_horizon_s") != cfg["scenario"]["duration_s"]
            or manifest.get("emission_end_s") != emission_end
            or manifest.get("drain_s") != cfg["scenario"]["duration_s"] - emission_end):
        raise trace_mod.TraceError("precompiled trace emission contract mismatch")
    manifest["__trace_sha256"] = sha
    manifest["__sha256"] = hashlib.sha256(mpath.read_bytes()).hexdigest()
    rows = trace_mod.load_trace(
        str(tpath),
        horizon_s=emission_end,
        max_packets=resolved["config"]["execution"]["max_packets"])
    return manifest, trace_bytes, rows


def _cmd_run(args) -> int:
    try:
        resolved = _load(args.config)
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        print(f"CONFIG INVALID: {exc}")
        return 2
    cfg = resolved["config"]
    try:
        formal = _verify_formal_args(args, resolved)
    except Exception as exc:
        print(f"RUN REFUSED (formal authorization): {exc}")
        return 3
    if args.decision_log and formal is not None:
        print("RUN REFUSED: decision audit log is diagnostic-only and cannot "
              "be attached to a formal authorized run")
        return 3
    if args.decision_log and args.dry_run:
        print("RUN REFUSED: decision audit log is unavailable in dry-run mode")
        return 3
    out_dir = args.out or cfg["outputs"]["out_dir"]
    if formal is not None and Path(out_dir).exists():
        try:
            nonempty = Path(out_dir).is_symlink() or not Path(out_dir).is_dir() \
                or any(Path(out_dir).iterdir())
        except OSError:
            nonempty = True
        if nonempty:
            print("RUN REFUSED (formal output): destination must be a new empty directory")
            return 3
    decision_writer = None
    if args.decision_log:
        try:
            # The sidecar is a separate published artifact.  Preflight it
            # before trace compilation/simulation as well as the JSONL target,
            # so a collision cannot leave a partial run behind.
            _check_new_destination(
                _decision_manifest_target(Path(args.decision_log)))
            decision_writer = _DecisionLogWriter(args.decision_log)
        except Exception as exc:
            print(f"RUN REFUSED (decision audit log): {exc}")
            return 3
    tmp = None
    if args.dry_run and args.out is None:
        # dry-run never writes run artifacts into the workspace
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        out_dir = tmp.name
    trace_path = cfg["outputs"].get("trace_path")
    try:
        if trace_path:
            manifest, trace_bytes, rows = _load_precompiled(resolved, trace_path)
        else:
            manifest, trace_bytes, rows = _compile(resolved, out_dir)
    except (trace_mod.TraceError, FileNotFoundError) as exc:
        if decision_writer is not None:
            decision_writer.abort()
        print(f"TRACE COMPILE FAILED: {exc}")
        return 2
    if args.expect_trace_sha256 and \
            manifest["__trace_sha256"] != args.expect_trace_sha256:
        if decision_writer is not None:
            decision_writer.abort()
        print(f"TRACE COMPILE FAILED: trace sha256 {manifest['__trace_sha256']} "
              f"!= expected {args.expect_trace_sha256}")
        return 2
    plan = {
        "config_sha256": resolved["sha256"],
        "trace_sha256": manifest["__trace_sha256"],
        "code_sha256": receipt_mod.code_sha256(),
        "offered_packets": manifest["offered_packets"],
        "offered_bits": manifest["offered_bits"],
        "active_endpoints": manifest["active_endpoints"],
        "horizon_s": cfg["scenario"]["duration_s"],
        "caps": {k: cfg["execution"][k] for k in ("max_events", "max_entities", "max_packets")},
        "mechanisms": {
            "policy": cfg["routing"]["policy"],
            "association": cfg["access"]["association"],
            "rate_model": cfg["links"]["rate_model"],
            "control_plane": cfg["control_plane"]["enabled"],
            "ge_enabled": cfg["links"]["ge_enabled"],
            "learning": cfg["learning"]["algorithm"],
        },
    }
    if args.dry_run:
        print(json.dumps({"status": "DRY RUN", **plan}, indent=2))
        return 0
    try:
        result = kernel.run_simulation(
            resolved, rows,
            learning_out_dir=(Path(out_dir) / cfg["learning"]["algorithm"])
            if cfg["learning"]["algorithm"] in ("ddqn", "qlearning") else None,
            decision_sink=decision_writer,
        )
    except learning.LearningUnavailable as exc:
        if decision_writer is not None:
            decision_writer.abort()
        print(f"RUN REFUSED (fail closed): {exc}")
        return 3
    except kernel.CapExceeded as exc:
        if decision_writer is not None:
            decision_writer.abort()
        print(f"RUN ABORTED (bounded execution): {exc}")
        return 4
    except Exception:
        if decision_writer is not None:
            decision_writer.abort()
        raise
    decision_log_sha = None
    if decision_writer is not None:
        try:
            decision_log_sha = decision_writer.close()
        except Exception as exc:
            decision_writer.abort()
            print(f"RUN REFUSED (decision audit log): {exc}")
            return 6
    rcp = receipt_mod.write_run(out_dir, resolved, trace_bytes, manifest, result, rows)
    decision_manifest_path = None
    if args.decision_log:
        try:
            decision_manifest_path = _write_decision_manifest(
                Path(args.decision_log), resolved, manifest, result, rcp,
                out_dir, decision_writer.row_count, decision_log_sha)
        except Exception as exc:
            print(f"RUN REFUSED (decision audit log): {exc}")
            return 6
    if formal is not None and rcp["natural_end"]:
        try:
            _write_formal_witness(out_dir, formal, rcp)
        except Exception as exc:
            print(f"RUN REFUSED (formal result witness): {exc}")
            return 6
    print(json.dumps({"status": "ok" if rcp["natural_end"] else "interrupted",
                      **plan,
                      "natural_end": rcp["natural_end"],
                      "fate_counts": rcp["fate_counts"],
                      "conservation_ok": rcp["conservation_ok"],
                      **({"decision_log": str(Path(args.decision_log).resolve()),
                          "decision_log_manifest": str(decision_manifest_path.resolve())}
                         if args.decision_log else {})}, indent=2))
    return 0 if rcp["natural_end"] else 5


def _cmd_receipt_verify(args) -> int:
    errors = receipt_mod.verify_receipt_dir(args.dir)
    if errors:
        print(json.dumps({"status": "FAILED", "errors": errors}, indent=2))
        return 2
    print(json.dumps({"status": "verified", "dir": args.dir}, indent=2))
    return 0


def _cmd_acceptance_run(args) -> int:
    try:
        summary = acceptance_mod.run_acceptance(args.out)
    except acceptance_mod.AcceptanceError as exc:
        print(f"ACCEPTANCE REFUSED: {exc}")
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 7


def _cmd_compare_run(args) -> int:
    try:
        summary = comparison_mod.run_comparison(args.config, args.out)
    except comparison_mod.ComparisonError as exc:
        print(f"COMPARISON FAILED: {exc}")
        return 8
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 8


def _cmd_platform_check(args) -> int:
    try:
        summary = platform_check_mod.run_platform_check(
            args.out, comparison_config=args.comparison_config,
            ddqn_config=args.ddqn_config,
            population_config=args.population_config)
    except platform_check_mod.PlatformCheckError as exc:
        print(f"PLATFORM CHECK REFUSED: {exc}")
        return 9
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 9


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m CODE.leo_sim")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("config")
    csub = p.add_subparsers(dest="sub", required=True)
    v = csub.add_parser("validate")
    v.add_argument("file")
    v.add_argument("--show", action="store_true")
    v.set_defaults(fn=_cmd_config_validate)

    p = sub.add_parser("trace")
    tsub = p.add_subparsers(dest="sub", required=True)
    tc = tsub.add_parser("compile")
    tc.add_argument("--config", required=True)
    tc.add_argument("--out", required=True)
    tc.set_defaults(fn=_cmd_trace_compile)

    p = sub.add_parser("experiment")
    esub = p.add_subparsers(dest="sub", required=True)
    ec = esub.add_parser("compile")
    ec.add_argument("--request", required=True)
    ec.add_argument("--out", required=True)
    ec.set_defaults(fn=_cmd_experiment_compile)

    p = sub.add_parser("run")
    p.add_argument("--config", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--expect-trace-sha256", default=None,
                   help="fail closed unless the consumed trace has this SHA256")
    p.add_argument("--decision-log", default=None,
                   help="diagnostic-only JSONL decision/info audit path; forbidden for formal runs")
    p.add_argument("--authorization", default=None)
    p.add_argument("--launch-nonce", default=None)
    p.add_argument("--expect-run-id", default=None)
    p.set_defaults(fn=_cmd_run)

    p = sub.add_parser("receipt")
    rsub = p.add_subparsers(dest="sub", required=True)
    rv = rsub.add_parser("verify")
    rv.add_argument("dir")
    rv.set_defaults(fn=_cmd_receipt_verify)

    p = sub.add_parser("acceptance")
    asub = p.add_subparsers(dest="sub", required=True)
    ar = asub.add_parser("run")
    ar.add_argument("--out", required=True)
    ar.set_defaults(fn=_cmd_acceptance_run)

    p = sub.add_parser("compare")
    csub = p.add_subparsers(dest="sub", required=True)
    cr = csub.add_parser("run")
    cr.add_argument("--config", default=str(Path(__file__).resolve().parent / "profiles" / "comparison.yaml"))
    cr.add_argument("--out", required=True)
    cr.set_defaults(fn=_cmd_compare_run)

    p = sub.add_parser("platform")
    psub = p.add_subparsers(dest="sub", required=True)
    pc = psub.add_parser("check")
    pc.add_argument("--out", required=True)
    pc.add_argument("--comparison-config", default=str(
        Path(__file__).resolve().parent / "profiles" / "comparison.yaml"))
    pc.add_argument("--ddqn-config", default=str(
        Path(__file__).resolve().parent / "profiles" / "acceptance" / "ddqn.yaml"))
    pc.add_argument("--population-config", default=str(
        Path(__file__).resolve().parent / "profiles" / "population_gravity.yaml"))
    pc.set_defaults(fn=_cmd_platform_check)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (config_mod.ConfigError, trace_mod.TraceError) as exc:
        # command handlers normally catch these first; this is the fail-closed
        # net for any path they missed
        print(f"FAILED: {exc}")
        return 2
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
        # invalid input must never surface a raw interpreter traceback from a
        # public entry point
        print(f"FAILED (fail closed): {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
