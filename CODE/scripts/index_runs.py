#!/usr/bin/env python3
"""index_runs.py - 只读运行索引层 (read-only run index layer).

把已经落地的证据链 (CODE/Results/<run_id>/ 与 ANALYSIS/<topic>/runs/<run_id>/)
抽成一张可横向对比的索引表, 解决"命名乱 / 记录散 / 结果无法横向对比"里的
"记录散"与"对比"两件事.

纪律 (改这个脚本前先读):
  * 只读: 绝不创建/移动/删除输入树里的任何文件; 唯一的写操作是 --out 指定的文件.
  * 不猜: 缺字段一律写 unknown, 并用 *_basis / *_source 列说明值是从哪来的.
  * 不合并: 同名 run_id 出现在两个 root 时两条都保留, 并各带 conflict 标记.
  * 不做统计: 只给每个 run 的单点标量比值; 跨 run 配对差/CI 交给
    ANALYSIS/paired_analysis.py 与 t1_stats.paired_differences, 不在本层.

依据 (只读引用, 未修改):
  - platform/EXPERIMENTS/README.md:19                  禁止从目录名解析科研参数
  - platform/EXPERIMENTS/contracts/run-artifact-contract.md:53   结果目录即 run_id
  - platform/EXPERIMENTS/contracts/run-artifact-contract.md:56   文件存在不等于正式准入
  - 三端文件与同步治理/实验调度机制.md:57               以产物真凭据判定完成, 不以日志行判定
  - 汇报材料/2026-W43/平台就绪度独立审查_20261021.md:60/:61      两套 metric 口径与 deadline 尺度
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "leo-index-runs/v1"
UNKNOWN = "unknown"

# 每个 run 目录里会被本层读取的文件 (只读取, 不产生)
RUN_MARKERS = (
    "receipt.json",
    "summary.json",
    "formal_run.json",
    "governance_receipt.json",
    "resolved_config.json",
    "manifest.json",
    "cell.json",
)
# 这些名字不是 run 目录 (指针/证人目录), 发现阶段跳过
SKIP_DIR_PREFIXES = ("_", ".")

COLUMNS = [
    "run_id",
    "cell_id",
    "tier",
    "scenario",
    "seed",
    "arm",
    "status",
    "status_basis",
    "research_eligible",
    "lifecycle",
    "metric_name",
    "metric_value",
    "metric_definition",
    "alt_metric_name",
    "alt_metric_value",
    "alt_metric_definition",
    "contract_sha256",
    "bundle_fingerprint",
    "git_head",
    "wall_s",
    "finished_at",
    "finished_at_source",
    "root",
    "conflict",
]

METRIC_NAME_PRIMARY = "packet_deadline_expired_pp"
METRIC_NAME_ALT = "terminal_loss_ratio_bits"

METRIC_DEF_PRIMARY = (
    "SPEC 主终点代理: receipt.fate_counts.DATA_DEADLINE_EXPIRED / manifest.offered_packets * 100 (pp). "
    "注意1: 该计数用的是仿真自身 --deadline-s; 独立审查 :60 记 default=4.0 s 而 v3 端到端 p50 仅 41.2 ms, "
    "尺度大近两个数量级, 所以接近 0 的取值是口径产物, 不是结论。"
    "注意2: SPEC 主终点是每包 D4 未达标 pp (审查 :61 记平台内无此一等实现), 本列只是最接近的可用代理。"
)
METRIC_DEF_ALT = (
    "平台现成口径: receipt.totals.terminal_loss_bits / receipt.totals.offered_bits (位丢失比)。"
    "与 SPEC 主终点不同口径, 不得据此外推未达标 pp。"
    "平均归一化损失 min(delay, D)/D 由 CODE/experiment_platform/outcome_metrics.py:307 计算"
    "(该模块当前只在 PR #14 分支), 需要 ledgers.json 逐包重算, 本层不计算。"
)

GIT_HEAD_KEYS = (
    "git_head",
    "git_commit",
    "git_sha",
    "head_sha",
    "commit",
    "code_commit",
    "deployment_commit",
    "source_commit",
)
WALL_KEYS = ("wall_s", "wall_seconds", "wall_time_s", "elapsed_s", "duration_wall_s")
FINISHED_KEYS = ("finished_at", "completed_at", "run_finished_at", "done_at", "end_time")


# --------------------------------------------------------------------------
# 只读 IO / 取值助手
# --------------------------------------------------------------------------
def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def read_json(path: Path):
    """读一个 JSON 文件. 返回 (obj|None, sha256|None, error|None). 只读."""
    try:
        blob = path.read_bytes()
    except OSError:
        return None, None, "unreadable"
    digest = sha256_bytes(blob)
    try:
        obj = json.loads(blob)
    except ValueError:
        return None, digest, "not-json"
    if not isinstance(obj, dict):
        return None, digest, "not-object"
    return obj, digest, None


def dig(obj, dotted: str, default=UNKNOWN):
    """按 a.b.c 取值; 任一层非 dict 或缺失 -> default. 绝不猜."""
    if not isinstance(obj, dict):
        return default
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    if cur is None:
        return default
    return cur


def fmt(value) -> str:
    """统一渲染. None/缺失 -> unknown; bool 保留 True/False (不用真值判断)."""
    if value is None or value is UNKNOWN:
        return UNKNOWN
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (int, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def first_of(*candidates) -> str:
    for cand in candidates:
        if cand is None or cand is UNKNOWN:
            continue
        if isinstance(cand, str) and cand.strip() == "":
            continue
        return fmt(cand)
    return UNKNOWN


def ratio(numerator, denominator) -> str:
    if not isinstance(numerator, (int, float)) or isinstance(numerator, bool):
        return UNKNOWN
    if not isinstance(denominator, (int, float)) or isinstance(denominator, bool):
        return UNKNOWN
    if denominator == 0:
        return UNKNOWN
    return repr(float(numerator) / float(denominator))


def canonical_sha(payload) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(blob)


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# 发现
# --------------------------------------------------------------------------
def looks_like_run_dir(path: Path) -> bool:
    return any((path / name).is_file() for name in RUN_MARKERS)


def discover_run_dirs(root: Path, max_depth: int = 3):
    """返回 (run_dirs, notes).

    root 自身是 run 目录时只取它; 否则按字典序下探到 max_depth 层, 遇到 run 目录就不再
    往里走 (run 目录内部不会被当成子 run). 名字以 _ 或 . 开头的目录一律跳过 (指针/证人目录).
    """
    notes = []
    if not root.exists():
        return [], ["root 不存在: %s" % root]
    if not root.is_dir():
        return [], ["root 不是目录: %s" % root]
    if looks_like_run_dir(root):
        return [root], notes
    found = []

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            notes.append("已达 max_depth=%d, 未继续下探: %s" % (max_depth, current))
            return
        for child in sorted(current.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith(SKIP_DIR_PREFIXES):
                notes.append("跳过非 run 目录: %s" % child)
                continue
            if looks_like_run_dir(child):
                found.append(child)
            else:
                walk(child, depth + 1)

    walk(root, 1)
    if not found:
        notes.append("max_depth=%d 内未发现 run 目录: %s" % (max_depth, root))
    return found, notes


# --------------------------------------------------------------------------
# 抽取
# --------------------------------------------------------------------------
def derive_status(receipt, artifacts, summary=None):
    """状态只由产物判定 (实验调度机制.md:57 不以日志行判定). basis 记录依据字段.

    独立 summary bundle (ANALYSIS/<topic>/runs/<run_id>/summary.json, 没有 receipt.json 文件)
    优先看它自己的自校验标志; 有 receipt.json 的 leo_sim_v2 族看 receipt 字段.
    """
    bundle_only = ("summary.json" in artifacts) and ("receipt.json" not in artifacts)
    if bundle_only:
        # A summary bundle without receipt.json is NOT complete merely because
        # its trace hash matches: the run may have been interrupted before the
        # receipt was written (the bundle declares receipt=null / an
        # "incomplete" note).  Do not turn "trace consistent" into "completed".
        if isinstance(summary, dict) and "receipt" in summary \
                and summary.get("receipt") is None:
            return "summary_bundle_incomplete", (
                "独立 summary bundle 但 receipt=null（未收尾/中断）")
        note = (summary or {}).get("note") if isinstance(summary, dict) else None
        if isinstance(note, str) and note.strip().lower().startswith("incomplete"):
            return "summary_bundle_incomplete", "独立 summary bundle 且 note=incomplete"
        matches = dig(summary, "trace_sha256_matches_manifest", None) if summary else None
        if matches is True:
            return "completed_self_verified", "独立 summary bundle 且 trace_sha256_matches_manifest=True"
        if matches is False:
            return "trace_sha256_mismatch", "独立 summary bundle 但 trace_sha256_matches_manifest=False"
    if receipt is None:
        if bundle_only:
            return "summary_bundle_only", "独立 summary bundle (无 receipt 对象, 也无自校验标志)"
        return UNKNOWN, "无 receipt.json/receipt 对象"
    error = dig(receipt, "error", None)
    interrupted = dig(receipt, "interrupted", None)
    natural_end = dig(receipt, "natural_end", None)
    conservation = dig(receipt, "conservation_ok", None)
    if isinstance(error, str) and error.strip():
        return "error", "receipt.error=%s" % fmt(error)[:40]
    if interrupted is True:
        return "interrupted", "receipt.interrupted=True"
    if natural_end is True and conservation is False:
        return "completed_conservation_failed", "receipt.natural_end=True 且 receipt.conservation_ok=False"
    if natural_end is True:
        return "completed", "receipt.natural_end=True"
    if natural_end is False:
        return "not_natural_end", "receipt.natural_end=False"
    if bundle_only:
        return "summary_bundle_only", "独立 summary bundle, receipt 内无 natural_end/interrupted/error"
    return UNKNOWN, "receipt 内无 natural_end/interrupted/error"


def derive_lifecycle(artifacts, gov, receipt):
    if gov is not None:
        eligible = dig(gov, "research_eligible", None)
        if eligible is True:
            return "formal_governed"
        return "governance_receipt_present"
    if "formal_run.json" in artifacts:
        return "formal_run"
    if "summary.json" in artifacts and "receipt.json" not in artifacts:
        return "diagnostic_bundle"
    if receipt is not None:
        return "raw_artifacts"
    return UNKNOWN


def extract_row(run_dir: Path, root: Path):
    artifacts = set()
    loaded = {}
    problems = []
    digests = {}
    for name in RUN_MARKERS:
        path = run_dir / name
        if not path.is_file():
            continue
        artifacts.add(name)
        obj, digest, err = read_json(path)
        digests[name] = digest
        if err:
            problems.append("%s:%s" % (name, err))
        else:
            loaded[name] = obj

    summary = loaded.get("summary.json")
    receipt = loaded.get("receipt.json")
    if receipt is None and summary is not None:
        nested = dig(summary, "receipt", None)
        receipt = nested if isinstance(nested, dict) else None
    resolved = None
    if "resolved_config.json" in loaded:
        candidate = dig(loaded["resolved_config.json"], "config", None)
        resolved = candidate if isinstance(candidate, dict) else loaded["resolved_config.json"]
    elif summary is not None:
        candidate = dig(summary, "resolved_config", None)
        resolved = candidate if isinstance(candidate, dict) else None
    manifest = loaded.get("manifest.json")
    if manifest is None and summary is not None:
        nested = dig(summary, "manifest", None)
        manifest = nested if isinstance(nested, dict) else None
    formal = loaded.get("formal_run.json")
    cell = loaded.get("cell.json")
    gov = loaded.get("governance_receipt.json")

    dirname = run_dir.name
    declared = first_of(
        dig(cell, "run_id", None) if cell else None,
        dig(formal, "run_id", None) if formal else None,
        dig(summary, "run", None) if summary else None,
        dig(receipt, "run_id", None) if receipt else None,
        dig(gov, "run_id", None) if gov else None,
    )
    run_id = declared if declared != UNKNOWN else dirname
    conflicts = []
    if problems:
        conflicts.append("artifact_problem:" + ",".join(sorted(problems)))
    if declared != UNKNOWN and declared != dirname:
        conflicts.append("run_id_dirname_mismatch:%s!=%s" % (declared, dirname))

    status, status_basis = derive_status(receipt, artifacts, summary)

    eligible = first_of(
        dig(gov, "research_eligible", None) if gov else None,
        dig(receipt, "research_eligible", None) if receipt else None,
    )

    receipt_sha_computed = digests.get("receipt.json")
    receipt_sha_declared = first_of(dig(formal, "receipt_sha256", None) if formal else None)
    if receipt_sha_computed and receipt_sha_declared != UNKNOWN and receipt_sha_computed != receipt_sha_declared:
        conflicts.append("receipt_sha256_mismatch")
    contract_sha = receipt_sha_computed or (receipt_sha_declared if receipt_sha_declared != UNKNOWN else None)

    identity = {
        "run_id": run_id,
        "code_sha256": fmt(dig(receipt, "code_sha256", None) if receipt else None),
        "config_sha256": fmt(dig(receipt, "config_sha256", None) if receipt else None),
        "seed": fmt(dig(receipt, "seed", None) if receipt else None),
        "trace_sha256": fmt(dig(receipt, "trace_sha256", None) if receipt else None),
        "trace_identity_sha256": fmt(dig(receipt, "trace_identity_sha256", None) if receipt else None),
        "ledgers_sha256": fmt(dig(receipt, "ledgers_sha256", None) if receipt else None),
        "receipt_sha256": fmt(contract_sha),
    }

    git_head = UNKNOWN
    wall_s = UNKNOWN
    for holder in (cell, formal, receipt, manifest, summary, gov):
        if git_head == UNKNOWN:
            for key in GIT_HEAD_KEYS:
                value = dig(holder, key, None)
                if value not in (None, UNKNOWN, ""):
                    git_head = fmt(value)
                    break
        if wall_s == UNKNOWN:
            for key in WALL_KEYS:
                value = dig(holder, key, None)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    wall_s = fmt(value)
                    break

    finished_at, finished_source = UNKNOWN, UNKNOWN
    for holder in (cell, formal, receipt, gov, summary):
        for key in FINISHED_KEYS:
            value = dig(holder, key, None)
            if value not in (None, UNKNOWN, ""):
                finished_at, finished_source = fmt(value), "json:%s" % key
                break
        if finished_at != UNKNOWN:
            break
    if finished_at == UNKNOWN:
        stamps = [run_dir / name for name in ("receipt.json", "summary.json", "formal_run.json", "cell.json")]
        mtimes = [p.stat().st_mtime for p in stamps if p.is_file()]
        if mtimes:
            finished_at, finished_source = iso_utc(max(mtimes)), "file_mtime(最新产物)"

    row = {
        "run_id": run_id,
        "cell_id": first_of(
            dig(cell, "cell_id", None) if cell else None,
            dig(formal, "cell_id", None) if formal else None,
            dig(resolved, "cell_id", None) if resolved else None,
        ),
        "tier": first_of(
            dig(cell, "tier", None) if cell else None,
            dig(formal, "tier", None) if formal else None,
            dig(resolved, "tier", None) if resolved else None,
        ),
        "scenario": first_of(
            dig(resolved, "scenario.name", None) if resolved else None,
            dig(cell, "scenario", None) if cell else None,
        ),
        "seed": first_of(
            dig(receipt, "seed", None) if receipt else None,
            dig(resolved, "scenario.seed", None) if resolved else None,
            dig(formal, "seed", None) if formal else None,
        ),
        "arm": first_of(
            dig(cell, "arm", None) if cell else None,
            # 2026-10-21: a network-alignment cell loops over all four arms, so
            # the suite records `arms` and deliberately no single `arm`;
            # naming one of the four would be an invisible lie.
            ",".join(str(a) for a in (cell.get("arms") or [])) if cell else None,
            dig(formal, "arm", None) if formal else None,
            dig(resolved, "arm", None) if resolved else None,
        ),
        "status": status,
        "status_basis": status_basis,
        "research_eligible": eligible,
        "lifecycle": derive_lifecycle(artifacts, gov, receipt),
        "metric_name": UNKNOWN,
        "metric_value": UNKNOWN,
        "metric_definition": UNKNOWN,
        "alt_metric_name": UNKNOWN,
        "alt_metric_value": UNKNOWN,
        "alt_metric_definition": UNKNOWN,
        "contract_sha256": fmt(contract_sha),
        "bundle_fingerprint": canonical_sha(identity),
        "git_head": git_head,
        "wall_s": wall_s,
        "finished_at": finished_at,
        "finished_at_source": finished_source,
        "root": str(root),
        "conflict": ";".join(conflicts) if conflicts else "",
    }

    expired = dig(receipt, "fate_counts.DATA_DEADLINE_EXPIRED", None) if receipt else None
    offered_packets = dig(manifest, "offered_packets", None) if manifest else None
    if isinstance(expired, (int, float)) and not isinstance(expired, bool) and isinstance(offered_packets, (int, float)):
        row["metric_name"] = METRIC_NAME_PRIMARY
        row["metric_value"] = ratio(expired * 100.0, offered_packets)
        row["metric_definition"] = METRIC_DEF_PRIMARY
    else:
        row["metric_definition"] = (
            "unknown: 无法计算(缺 receipt.fate_counts.DATA_DEADLINE_EXPIRED 或 manifest.offered_packets); 口径="
            + METRIC_DEF_PRIMARY
        )

    loss_bits = dig(receipt, "totals.terminal_loss_bits", None) if receipt else None
    offered_bits = dig(receipt, "totals.offered_bits", None) if receipt else None
    if isinstance(loss_bits, (int, float)) and not isinstance(loss_bits, bool) and isinstance(offered_bits, (int, float)):
        row["alt_metric_name"] = METRIC_NAME_ALT
        row["alt_metric_value"] = ratio(loss_bits, offered_bits)
        row["alt_metric_definition"] = METRIC_DEF_ALT
    else:
        row["alt_metric_definition"] = (
            "unknown: 无法计算(缺 receipt.totals.terminal_loss_bits 或 receipt.totals.offered_bits); 口径="
            + METRIC_DEF_ALT
        )

    return row


def build_index(roots, max_depth: int = 3):
    rows, notes = [], []
    for root in roots:
        run_dirs, root_notes = discover_run_dirs(root, max_depth)
        notes.extend(root_notes)
        for run_dir in run_dirs:
            rows.append(extract_row(run_dir, root))
    by_id = {}
    for row in rows:
        by_id.setdefault(row["run_id"], []).append(row)
    conflicts = []
    for run_id, group in sorted(by_id.items()):
        distinct_dirs = {(r["root"], r["run_id"]) for r in group}
        if len(group) > 1 and len({r["root"] for r in group}) > 1:
            detail = "duplicate_run_id:%s 出现在 %d 个 root" % (
                run_id,
                len({r["root"] for r in group}),
            )
            for row in group:
                row["conflict"] = (row["conflict"] + ";" + detail) if row["conflict"] else detail
            conflicts.append(detail)
        for row in group:
            if row["conflict"]:
                conflicts.append("%s @ %s: %s" % (run_id, row["root"], row["conflict"]))
    rows.sort(key=lambda r: (r["run_id"], r["root"]))
    return rows, sorted(set(conflicts)), notes


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
TABLE_FOLD = {"metric_definition", "alt_metric_definition"}
HASH_FIELDS = {"contract_sha256", "bundle_fingerprint"}
FOLD_LABEL = {"metric_definition": "[SPEC-proxy]", "alt_metric_definition": "[platform-legacy]"}


def render_table(rows):
    if not rows:
        return "（无 run）\n"
    cells = []
    for row in rows:
        line = []
        for col in COLUMNS:
            value = row[col]
            if col in TABLE_FOLD:
                value = FOLD_LABEL[col] if value in (METRIC_DEF_PRIMARY, METRIC_DEF_ALT) else "unknown"
            elif col in HASH_FIELDS and len(value) > 12:
                value = value[:12] + "..."
            line.append(value)
        cells.append(line)
    widths = [max(len(col), *(len(r[i]) for r in cells)) for i, col in enumerate(COLUMNS)]
    out = io.StringIO()
    out.write(" | ".join(col.ljust(widths[i]) for i, col in enumerate(COLUMNS)) + "\n")
    out.write("-+-".join("-" * w for w in widths) + "\n")
    for line in cells:
        out.write(" | ".join(line[i].ljust(widths[i]) for i in range(len(COLUMNS))) + "\n")
    out.write("注: table 视图把 contract_sha256/bundle_fingerprint 截断为 12 字符, "
              "metric_definition/alt_metric_definition 折叠为口径代码; 完整值见 --format csv|json|md。\n")
    return out.getvalue()


def render_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row[col] for col in COLUMNS})
    return out.getvalue()


def render_json(rows, roots, conflicts, notes):
    return json.dumps(
        {
            "schema": SCHEMA,
            "roots": [str(r) for r in roots],
            "columns": COLUMNS,
            "row_count": len(rows),
            "rows": rows,
            "conflicts": conflicts,
            "notes": notes,
            "read_only": True,
        },
        ensure_ascii=False,
        indent=1,
    ) + "\n"


def render_md(rows, roots, conflicts, notes):
    out = io.StringIO()
    out.write("# 运行索引（由 CODE/scripts/index_runs.py 只读生成）\n\n")
    out.write("schema: %s | 行数: %d | roots: %s\n\n" % (SCHEMA, len(rows), ", ".join(str(r) for r in roots)))
    out.write("| " + " | ".join(COLUMNS) + " |\n")
    out.write("|" + "---|" * len(COLUMNS) + "\n")
    for row in rows:
        out.write("| " + " | ".join(row[col].replace("|", "/") for col in COLUMNS) + " |\n")
    if conflicts:
        out.write("\n## 冲突（未合并，逐条列出）\n\n")
        for item in conflicts:
            out.write("- %s\n" % item)
    if notes:
        out.write("\n## 发现阶段备注\n\n")
        for item in notes:
            out.write("- %s\n" % item)
    return out.getvalue()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def default_roots():
    repo = Path(__file__).resolve().parents[2]
    roots = [repo / "CODE" / "Results"]
    analysis = repo / "ANALYSIS"
    if analysis.is_dir():
        roots.extend(sorted(p / "runs" for p in analysis.iterdir() if (p / "runs").is_dir()))
    return roots


def build_parser():
    parser = argparse.ArgumentParser(
        description="只读运行索引层: 把证据链抽成可横向对比的表。不写输入树, 不做统计。",
        epilog="缺字段一律写 unknown; 同名 run_id 跨 root 不合并。详见 汇报材料/2026-W43/实验记录索引层_使用说明_20261021.md",
    )
    parser.add_argument("--root", action="append", default=None,
                        help="结果根目录, 可多次传入; 省略时默认扫 CODE/Results 与 ANALYSIS/*/runs")
    parser.add_argument("--format", choices=["table", "csv", "json", "md"], default="table")
    parser.add_argument("--out", default=None, help="写到该文件 (唯一允许的写操作); 省略则写 stdout")
    parser.add_argument("--max-depth", type=int, default=3, help="root 下最多下探几层找 run 目录 (默认 3)")
    parser.add_argument("--fail-on-conflict", action="store_true", help="发现冲突时以退出码 2 结束 (供 CI 用)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    roots = [Path(r).resolve() for r in args.root] if args.root else [p.resolve() for p in default_roots()]
    rows, conflicts, notes = build_index(roots, args.max_depth)
    if args.format == "csv":
        text = render_csv(rows)
    elif args.format == "json":
        text = render_json(rows, roots, conflicts, notes)
    elif args.format == "md":
        text = render_md(rows, roots, conflicts, notes)
    else:
        text = render_table(rows)
        if conflicts:
            text += "\n!! 冲突 %d 条（未合并）:\n" % len(conflicts) + "\n".join("  - " + c for c in conflicts) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    for note in notes:
        sys.stderr.write("note: %s\n" % note)
    if conflicts:
        sys.stderr.write("warning: %d 条冲突未合并\n" % len(conflicts))
    return 2 if (conflicts and args.fail_on_conflict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
