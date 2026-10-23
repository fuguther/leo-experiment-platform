"""test_index_runs.py - index_runs.py 的单元测试.

只用标准库 + pytest, 用 tmp_path 造假 run 目录, 不碰真仓库的任何字节.
覆盖: 全字段抽取 / 缺字段为 unknown / 同名 run_id 跨 root 报冲突 /
run_id 与目录名不符 / receipt_sha256 不符 / summary bundle 自校验 / 四种输出格式 /
以及最重要的: 运行前后输入树的文件集合与 mtime 逐字不变 (证明只读).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "index_runs.py"
_spec = importlib.util.spec_from_file_location("index_runs_under_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
index_runs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(index_runs)

FIXED_MTIME = 1_700_000_000  # 2023-11-14T22:13:20Z
FIXED_MTIME_ISO = "2023-11-14T22:13:20Z"


def write_json(path: Path, obj) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
    path.write_bytes(blob)
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return blob


def full_receipt(**over):
    obj = {
        "schema": "leo-sim-receipt/v5",
        "code_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "config_version": "leo-sim-config/v1",
        "seed": 7,
        "horizon_s": 60.0,
        "conservation_ok": True,
        "natural_end": True,
        "interrupted": False,
        "error": None,
        "research_eligible": False,
        "events_processed": 100,
        "trace_sha256": "c" * 64,
        "trace_identity_sha256": "d" * 64,
        "ledgers_sha256": "e" * 64,
        "fate_counts": {"DELIVERED": 995, "DATA_DEADLINE_EXPIRED": 5},
        "totals": {
            "offered_bits": 8000000,
            "delivered_bits": 7996000,
            "terminal_loss_bits": 4000,
            "in_system_bits_at_stop": 0,
        },
    }
    obj.update(over)
    return obj


def build_tree(base: Path):
    """造一棵假输入树. results 与 analysis 是两个 root."""
    results = base / "results"
    analysis = base / "analysis"

    # 指针/证人目录: 名字以下划线开头, 不是 run, 必须被跳过
    write_json(results / "_run_receipts" / "pointer.json", {"run_id": "not-a-run"})

    # run-alpha: 完整 leo_sim_v2 族 + cell.json
    alpha = results / "run-alpha"
    receipt_blob = write_json(alpha / "receipt.json", full_receipt())
    write_json(alpha / "manifest.json", {
        "offered_packets": 1000, "offered_bits": 8000000,
        "simulation_horizon_s": 60.0, "provenance": "synthetic",
    })
    write_json(alpha / "resolved_config.json", {
        "version": "leo-sim-config/v1",
        "config": {
            "scenario": {"name": "t1-pressure-corridor-v3", "seed": 7, "num_satellites": 24},
            "routing": {"policy": "hop"},
        },
    })
    write_json(alpha / "formal_run.json", {
        "schema": "leo-sim-formal-run/v1", "run_id": "run-alpha",
        "receipt_sha256": hashlib.sha256(receipt_blob).hexdigest(),
        "authorization_sha256": "f" * 64, "launch_nonce": "n1", "natural_end": True,
    })
    write_json(alpha / "cell.json", {
        "cell_id": "cell-01", "tier": "T1", "arm": "treatment", "run_id": "run-alpha",
        "git_head": "7850ad2afb4c7218549e757998b4355aa0a44170",
        "wall_s": 123.5, "finished_at": "2026-10-20T12:00:00Z",
    })

    # run-gamma: 大面积缺字段, 只有 receipt.json
    write_json(results / "run-gamma" / "receipt.json",
               {"schema": "leo-sim-receipt/v5", "natural_end": False, "conservation_ok": True})

    # run-renamed: 目录名与 formal_run.json 里的 run_id 不一致
    renamed = results / "run-renamed"
    blob = write_json(renamed / "receipt.json", full_receipt())
    write_json(renamed / "formal_run.json",
               {"run_id": "run-canonical", "receipt_sha256": hashlib.sha256(blob).hexdigest()})

    # run-mismatch: formal 声明的 receipt_sha256 与按字节实算的不一致
    write_json(results / "run-mismatch" / "receipt.json", full_receipt())
    write_json(results / "run-mismatch" / "formal_run.json",
               {"run_id": "run-mismatch", "receipt_sha256": "0" * 64})

    # ANALYSIS 侧: 独立 summary bundle 族
    write_json(analysis / "SCN-1" / "runs" / "run-alpha" / "summary.json", {
        "run": "run-alpha", "source_dir": "/tmp/run-alpha", "note": "",
        "manifest": {"trace_sha256": "c" * 64, "offered_packets": 1000, "simulation_horizon_s": 60.0},
        "receipt": {
            "schema": "leo-sim-receipt/v5", "code_sha256": "a" * 64, "config_sha256": "b" * 64,
            "seed": 7, "conservation_ok": True, "research_eligible": False,
            "fate_counts": {"DATA_DEADLINE_EXPIRED": 5},
            "totals": {"offered_bits": 8000000, "terminal_loss_bits": 4000},
        },
        "resolved_config": {"scenario": {"name": "t1-pressure-corridor-v3", "seed": 7}},
        "trace_sha256_recomputed": "c" * 64, "trace_sha256_matches_manifest": True,
    })
    write_json(analysis / "SCN-1" / "runs" / "bundle-delta" / "summary.json", {
        "run": "bundle-delta",
        "manifest": {"offered_packets": 500},
        "receipt": {"seed": 11, "conservation_ok": True},
        "resolved_config": {"scenario": {"name": "t1-grid-140-v4b", "seed": 11}},
        "trace_sha256_matches_manifest": False,
    })
    return results, analysis


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def tree(tmp_path):
    """输入树放在 tmp_path/input 下, 这样输出文件可以写到 tmp_path 里而仍在输入树之外."""
    return build_tree(tmp_path / "input")


def snapshot(root: Path):
    """文件集合 + mtime + 大小, 用来证明只读."""
    out = {}
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        out[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size, path.is_dir())
    return out


def by_id(rows):
    return {row["run_id"]: row for row in rows}


# --------------------------------------------------------------------------
# 全字段
# --------------------------------------------------------------------------
def test_full_row_fields(tree):
    results, _ = tree
    rows, conflicts, _ = index_runs.build_index([results])
    alpha = by_id(rows)["run-alpha"]
    assert alpha["cell_id"] == "cell-01"
    assert alpha["tier"] == "T1"
    assert alpha["arm"] == "treatment"
    assert alpha["scenario"] == "t1-pressure-corridor-v3"
    assert alpha["seed"] == "7"
    assert alpha["status"] == "completed"
    assert alpha["status_basis"] == "receipt.natural_end=True"
    assert alpha["research_eligible"] == "False"
    assert alpha["lifecycle"] == "formal_run"
    assert alpha["metric_name"] == index_runs.METRIC_NAME_PRIMARY
    assert alpha["metric_value"] == repr(5 * 100.0 / 1000)
    assert "SPEC" in alpha["metric_definition"] and "D4" in alpha["metric_definition"]
    assert alpha["alt_metric_name"] == index_runs.METRIC_NAME_ALT
    assert alpha["alt_metric_value"] == repr(4000 / 8000000)
    expected_sha = hashlib.sha256((results / "run-alpha" / "receipt.json").read_bytes()).hexdigest()
    assert alpha["contract_sha256"] == expected_sha
    assert len(alpha["bundle_fingerprint"]) == 64
    assert alpha["git_head"] == "7850ad2afb4c7218549e757998b4355aa0a44170"
    assert alpha["wall_s"] == "123.5"
    assert alpha["finished_at"] == "2026-10-20T12:00:00Z"
    assert alpha["finished_at_source"] == "json:finished_at"
    assert alpha["conflict"] == ""
    assert [c for c in conflicts if "run-alpha" in c] == []
    assert set(alpha) == set(index_runs.COLUMNS)


def test_required_columns_present(tree):
    results, _ = tree
    rows, _, _ = index_runs.build_index([results])
    required = {
        "run_id", "cell_id", "tier", "scenario", "seed", "arm", "status",
        "research_eligible", "lifecycle", "metric_name", "metric_value",
        "metric_definition", "contract_sha256", "bundle_fingerprint",
        "git_head", "wall_s", "finished_at",
    }
    assert required <= set(index_runs.COLUMNS)
    for row in rows:
        assert required <= set(row)


# --------------------------------------------------------------------------
# 缺字段 = unknown
# --------------------------------------------------------------------------
def test_missing_fields_are_unknown_not_guessed(tree):
    results, _ = tree
    rows, _, _ = index_runs.build_index([results])
    gamma = by_id(rows)["run-gamma"]
    for col in ("cell_id", "tier", "arm", "scenario", "seed", "git_head", "wall_s",
                "metric_name", "metric_value", "alt_metric_name", "alt_metric_value",
                "research_eligible"):
        assert gamma[col] == index_runs.UNKNOWN, col
    assert gamma["metric_definition"].startswith(index_runs.UNKNOWN)
    assert gamma["status"] == "not_natural_end"
    assert gamma["lifecycle"] == "raw_artifacts"
    assert gamma["contract_sha256"] != index_runs.UNKNOWN  # receipt.json 在, 有实算哈希
    assert gamma["finished_at"] == FIXED_MTIME_ISO
    assert gamma["finished_at_source"].startswith("file_mtime")
    assert set(gamma) == set(index_runs.COLUMNS)


def test_missing_root_is_noted_not_fatal(tmp_path):
    rows, conflicts, notes = index_runs.build_index([tmp_path / "nope"])
    assert rows == []
    assert conflicts == []
    assert any("不存在" in note for note in notes)


# --------------------------------------------------------------------------
# 冲突必须报出, 不许静默合并
# --------------------------------------------------------------------------
def test_duplicate_run_id_across_roots_is_reported_not_merged(tree):
    results, analysis = tree
    rows, conflicts, _ = index_runs.build_index([results, analysis])
    alpha_rows = [row for row in rows if row["run_id"] == "run-alpha"]
    assert len(alpha_rows) == 2, "同名 run_id 出现在两个 root 时必须保留两条"
    assert {row["root"] for row in alpha_rows} == {str(results.resolve()), str(analysis.resolve())}
    for row in alpha_rows:
        assert "duplicate_run_id" in row["conflict"]
    assert any("duplicate_run_id" in item for item in conflicts)


def test_run_id_dirname_mismatch_flagged(tree):
    results, _ = tree
    rows, conflicts, _ = index_runs.build_index([results])
    assert "run-canonical" in by_id(rows)
    row = by_id(rows)["run-canonical"]
    assert "run_id_dirname_mismatch" in row["conflict"]
    assert any("run_id_dirname_mismatch" in item for item in conflicts)


def test_receipt_sha256_mismatch_flagged_and_computed_digest_wins(tree):
    results, _ = tree
    rows, conflicts, _ = index_runs.build_index([results])
    row = by_id(rows)["run-mismatch"]
    assert "receipt_sha256_mismatch" in row["conflict"]
    assert row["contract_sha256"] == hashlib.sha256(
        (results / "run-mismatch" / "receipt.json").read_bytes()).hexdigest()
    assert row["contract_sha256"] != "0" * 64
    assert any("receipt_sha256_mismatch" in item for item in conflicts)


def test_fail_on_conflict_exit_code(tree):
    results, _ = tree
    assert index_runs.main(["--root", str(results), "--format", "csv"]) == 0
    assert index_runs.main(["--root", str(results), "--format", "csv", "--fail-on-conflict"]) == 2


# --------------------------------------------------------------------------
# summary bundle 族 (无 receipt.json) 用自校验标志定状态
# --------------------------------------------------------------------------
def test_summary_bundle_self_verification(tree):
    _, analysis = tree
    rows, _, _ = index_runs.build_index([analysis])
    rows_by_id = by_id(rows)
    ok = rows_by_id["run-alpha"]
    bad = rows_by_id["bundle-delta"]
    assert ok["lifecycle"] == "diagnostic_bundle"
    assert ok["status"] == "completed_self_verified"
    assert "trace_sha256_matches_manifest" in ok["status_basis"]
    assert bad["status"] == "trace_sha256_mismatch"
    assert bad["status"] != ok["status"]
    assert ok["scenario"] == "t1-pressure-corridor-v3"
    assert bad["scenario"] == "t1-grid-140-v4b"
    assert ok["contract_sha256"] == index_runs.UNKNOWN  # 无 receipt.json 文件, 不假装有


def test_non_run_directories_are_skipped(tree):
    results, _ = tree
    rows, _, notes = index_runs.build_index([results])
    assert "not-a-run" not in by_id(rows)
    assert any("_run_receipts" in note for note in notes)


# --------------------------------------------------------------------------
# 只读
# --------------------------------------------------------------------------
def test_input_tree_is_byte_identical_after_run(tree):
    results, analysis = tree
    base = results.parent
    before = snapshot(base)
    assert index_runs.main(["--root", str(results), "--root", str(analysis), "--format", "md"]) == 0
    assert index_runs.main(["--root", str(results), "--format", "csv"]) == 0
    assert index_runs.main(["--root", str(results), "--format", "json"]) == 0
    assert index_runs.main(["--root", str(results), "--format", "table"]) == 0
    after = snapshot(base)
    assert before == after, "输入树的文件集合/mtime/大小必须逐字不变"
    assert not [p for p in base.rglob("*") if p.name in ("__pycache__", ".pytest_cache")]


def test_out_writes_outside_tree_and_tree_stays_clean(tree, tmp_path):
    results, _ = tree
    base = results.parent
    before = snapshot(base)
    target = tmp_path / "outside" / "index.md"
    target.parent.mkdir()
    assert index_runs.main(["--root", str(results), "--format", "md", "--out", str(target)]) == 0
    assert target.is_file() and target.read_text(encoding="utf-8").startswith("# 运行索引")
    assert snapshot(base) == before


def test_all_formats_emit_every_column(tree, tmp_path):
    results, analysis = tree
    index_runs.main(["--root", str(results), "--root", str(analysis), "--format", "json",
                     "--out", str(tmp_path / "index.json")])
    payload = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert payload["columns"] == list(index_runs.COLUMNS)
    assert payload["read_only"] is True
    assert payload["row_count"] == len(payload["rows"]) == 6
    for row in payload["rows"]:
        assert set(row) == set(index_runs.COLUMNS)
    index_runs.main(["--root", str(results), "--format", "csv", "--out", str(tmp_path / "index.csv")])
    lines = (tmp_path / "index.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",") == list(index_runs.COLUMNS)
    assert len(lines) == 1 + 4
    index_runs.main(["--root", str(results), "--format", "md", "--out", str(tmp_path / "index.md")])
    body = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "| " + " | ".join(index_runs.COLUMNS) + " |" in body


# --------------------------------------------------------------------------
# 默认 root / 真数据冒烟
# --------------------------------------------------------------------------
def test_default_roots_point_at_script_checkout():
    roots = index_runs.default_roots()
    assert roots[0].name == "Results" and roots[0].parent.name == "CODE"
    assert all(root.name in ("runs", "Results") for root in roots)
    assert roots == sorted(roots, key=lambda p: (p.name != "Results", str(p)))


def test_real_results_root_is_indexable():
    real = REPO_ROOT / "CODE" / "Results"
    if not real.is_dir():
        pytest.skip("真 CODE/Results 不在本 checkout")
    rows, _, _ = index_runs.build_index([real])
    assert rows, "真 CODE/Results 至少应有一个 run"
    for row in rows:
        assert row["run_id"] != index_runs.UNKNOWN
        assert set(row) == set(index_runs.COLUMNS)

def test_multi_arm_cell_reports_every_arm_it_ran(tmp_path):
    """A network-alignment cell runs four arms; the row must not name one."""
    run = tmp_path / "run-multi"
    run.mkdir()
    write_json(run / "cell.json", {
        "cell_id": "b-fixed_hotspot-network-seed-7", "tier": "b_dev",
        "arms": ["stale", "now", "common", "candidate"],
        "git_head": "7850ad2afb4c7218549e757998b4355aa0a44170",
        "wall_s": 9.0,
    })
    rows, _, _ = index_runs.build_index([tmp_path])
    row = by_id(rows)["run-multi"]
    assert row["arm"] == "stale,now,common,candidate"
    assert row["tier"] == "b_dev"
    assert row["git_head"] == "7850ad2afb4c7218549e757998b4355aa0a44170"


def test_single_arm_cell_still_reports_one_arm(tmp_path):
    run = tmp_path / "run-single"
    run.mkdir()
    write_json(run / "cell.json", {
        "cell_id": "c1", "tier": "dev", "arm": "candidate", "wall_s": 1.0,
    })
    rows, _, _ = index_runs.build_index([tmp_path])
    assert by_id(rows)["run-single"]["arm"] == "candidate"

