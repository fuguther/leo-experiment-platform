#!/usr/bin/env python3
"""Generate FREEZE-SCOPE.md from the actual hash functions and the git diff.

Sections 1-3 of the document (paths, counts, line ranges, hashes) are produced
from the functions that define them -- receipt.code_sha256,
governance.EXECUTION_CHAIN_PATHS -- and from git status plus an explicit
new-file list.  Sections 4-5 (the exclusion policy and the operating order) are
hand-maintained constants in this file; the generator only guarantees they do
not contradict the code or the repository.  Any disagreement inside sections
1-3 is therefore a bug in the generator, not a stale sentence.

USAGE
  python3 CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/round4/generate_freeze_scope.py \
      --out CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/round3/FREEZE-SCOPE.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from CODE.leo_sim import governance, receipt  # noqa: E402

#: Platform files this change set ADDS.  Listed explicitly because git cannot
#: tell a new platform source file from a new research artifact, and the freeze
#: must include the first and exclude the second.
NEW_FILES = (
    "CODE/experiment_platform/primary_metrics.py",
    "CODE/leo_sim/pullback.py",
    "CODE/leo_sim/recompute.py",
    "CODE/leo_sim/tests/test_burst_transform.py",
    "CODE/leo_sim/tests/test_formal_stream_gate.py",
    "CODE/leo_sim/tests/test_pre_experiment_trust.py",
    "CODE/leo_sim/tests/test_pre_experiment_trust_round2.py",
    "CODE/leo_sim/tests/test_pullback_authority.py",
    # added after the VM acceptance round found three defects that no local
    # fixture could (see round5/FINDINGS-VM-ACCEPTANCE.md)
    "CODE/experiment_platform/tests/test_design_accounting_extension.py",
    "CODE/experiment_platform/tests/test_stale_authorization_guard.py",
)

#: Never part of a code freeze, even though some sit under CODE/leo_sim.
EXCLUDED = (
    ("ANALYSIS/ARRIVAL-TIME-T1-20260923/",
     "他人的唯一研究材料（未跟踪）"),
    ("CODE/leo_sim/profiles/linkgen_generic_rf_mcs_1g.yaml",
     "研究材料；位于 CODE/leo_sim/ 下但不在 *.py 的非递归 glob 内"),
    ("CODE/leo_sim/profiles/linkgen_published_optical_100g.yaml", "同上"),
    ("CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml", "同上"),
    ("CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/", "既有复核回执（只读证据）"),
    ("CODE/work/WP-MATRIX/", "先前遗留的未跟踪目录"),
    ("CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/", "本任务的证据/报告；可作附件，非代码身份"),
    ("CODE/work/WP-LEO-V2-PRE-TRUST-REPRO/", "复现脚本生成物（可重建）"),
    ("EXPERIMENTS/EXP-LEO-V2-PRE-TRUST-REPRO/", "同上"),
    ("CODE/Results/", "运行结果目录（gitignored）"),
    ("CODE/scripts/remote/remote.env", "本机 VM 连接配置（gitignored，含主机信息）"),
)

BT = chr(96)  # backtick


def q(text: str) -> str:
    return f"{BT}{text}{BT}"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function_lines(rel: str, name: str) -> str:
    """Line range of a top-level function, read from the file itself.

    Hard-coded line numbers go stale the moment anyone edits above them: the
    first version of this document cited receipt.py:152-158 for code_sha256.
    That was already one line short at HEAD (the function spans 152-159 there)
    and became badly wrong after this round's edit inserted eight lines earlier
    in the file.  Caught by the independent review of 2026-09-25; the range is
    now derived, so it cannot drift.
    """
    import re as _re
    lines = (REPO / rel).read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        if _re.match(rf"^def {_re.escape(name)}\(", line):
            start = index + 1
            break
    if start is None:
        raise SystemExit(f"{rel} has no top-level def {name}")
    # End at the LAST non-blank line of the body: the next top-level statement
    # (or EOF) terminates it, but the blank line before that statement is not
    # part of the function.
    last_body = start
    for index in range(start, len(lines)):
        if lines[index].strip() and not lines[index][0].isspace():
            break
        if lines[index].strip():
            last_body = index + 1
    return f"{rel}:{start}-{last_body}"


def aggregate(items) -> str:
    """SHA256 over sorted '<path>\\0<sha256>\\n' records (declared here)."""
    h = hashlib.sha256()
    for rel, digest in sorted(items):
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\n")
    return h.hexdigest()


def tracked_modified() -> list:
    out = subprocess.run(["git", "status", "--porcelain=v1"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    return sorted(line[3:].strip() for line in out.splitlines()
                  if line.startswith(" M "))


def code_hash_coverage() -> list:
    pkg = REPO / "CODE" / "leo_sim"
    return sorted(str(p.relative_to(REPO)) for p in pkg.glob("*.py"))


#: Filled in by main() from the command line.  The document used to record only
#: the development worktree's HEAD, which is NOT the commit whose bytes it
#: hashes -- an independent review of 2026-09-25 flagged the mismatch and the
#: fact that no file in the repository named the freeze commit at all.
FREEZE_COMMIT: str | None = None
ATTESTATION_COMMIT: str | None = None


def render() -> str:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.strip()
    modified = tracked_modified()
    freeze = sorted(set(modified) | set(NEW_FILES))
    missing = [rel for rel in freeze if not (REPO / rel).is_file()]
    if missing:
        raise SystemExit(f"freeze list names missing files: {missing}")

    runtime = code_hash_coverage()
    # Self-check: the documented file list must reproduce receipt.code_sha256()
    # under that function's own algorithm, so the list cannot drift from it.
    h = hashlib.sha256()
    for rel in runtime:
        h.update(Path(rel).name.encode())
        h.update(hashlib.sha256((REPO / rel).read_bytes()).digest())
    if h.hexdigest() != receipt.code_sha256():
        raise SystemExit(
            "documented coverage list does not reproduce receipt.code_sha256()")
    chain = list(governance.EXECUTION_CHAIN_PATHS)
    tests = [rel for rel in freeze if "/tests/" in rel]
    coverage = set(runtime) | set(chain)
    others = [rel for rel in freeze
              if rel not in coverage and rel not in tests]
    chain_map = governance.execution_chain_sha256()
    chain_aggregate = hashlib.sha256(
        json.dumps(chain_map, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()

    def table(paths) -> list:
        rows = ["| 文件 | sha256 |", "|---|---|"]
        rows += [f"| {q(rel)} | {q(sha256_file(REPO / rel))} |" for rel in paths]
        return rows

    L = []
    add = L.append
    add("# 冻结当前代码的准确文件范围（由生成器产出）")
    add("")
    add(f"> 本文件由 {q('round4/generate_freeze_scope.py')} 生成。")
    add(f"> **§1–§3 的路径、数量、行号与哈希全部由生成器从实际函数与 "
        f"{q('git status')} 产出**，"
        "不做手写；§4 的排除清单与 §5 的操作顺序是人工维护的策略文本（生成器中的常量），"
        "生成器只保证这两节与代码/仓库现状不矛盾。")
    add("> 任何与代码的不一致都是生成器的缺陷，而不是文档过期。")
    add("")
    add("| 量 | 值 |")
    add("|---|---|")
    add(f"| 开发工作树 HEAD（本文件生成处，**不是**冻结提交） | {q(head)} |")
    add(f"| **冻结提交** | {q(FREEZE_COMMIT or '未指定（用 --freeze-commit 指定）')} |")
    add(f"| 本文件所在提交 | {q(ATTESTATION_COMMIT or '附着提交（本文件不在冻结提交内）')} |")
    add(f"| code_sha256()（当前树） | {q(receipt.code_sha256())} |")
    add(f"| execution_chain 汇总 | {q(chain_aggregate)} |")
    add(f"| 冻结集合文件数 | **{len(freeze)}**"
        f"（已跟踪修改 {len(modified)} + 新增 {len(NEW_FILES)}） |")
    add(f"| 其中：运行代码哈希覆盖 | {len(set(runtime) & set(freeze))} |")
    add(f"| 其中：执行链哈希覆盖 | {len(set(chain) & set(freeze))} |")
    add(f"| 其中：测试 | {len(tests)} |")
    add(f"| 冻结集合汇总哈希 | {q(aggregate([(r, sha256_file(REPO / r)) for r in freeze]))} |")
    add("")
    add("---")
    add("")
    add(f"## 1. 运行代码哈希覆盖：{q('receipt.code_sha256()')}")
    add("")
    add(f"实现（{q(function_lines('CODE/leo_sim/receipt.py', 'code_sha256'))}，行号由生成器从源码读出）：对 {q('CODE/leo_sim/')} 下 "
        f"**非递归**的 {q('*.py')}，按文件名排序，")
    add(f"依次 {q('h.update(name)')} 与 {q('h.update(sha256(bytes).digest())')}。")
    add("")
    add(f"- **覆盖集合**：{q('CODE/leo_sim/*.py')}（{len(runtime)} 个文件）")
    add(f"- **不覆盖**：{q('CODE/leo_sim/tests/')}（子目录，非递归）、"
        f"{q('CODE/leo_sim/profiles/*.yaml')}、{q('CODE/experiment_platform/')}、"
        f"{q('CODE/scripts/')}。")
    add("  测试因此**需要冻结但不属于本哈希的覆盖范围**；不为迎合文档修改哈希算法。")
    add(f"- **汇总哈希**：{q(receipt.code_sha256())}（即该函数的返回值本身）")
    add("")
    L += table(runtime)
    add("")
    add("> 本轮改动落在该覆盖内的文件："
        + ", ".join(q(rel) for rel in sorted(set(runtime) & set(freeze))))
    add("")
    add("---")
    add("")
    add(f"## 2. 执行链哈希覆盖：{q('governance.execution_chain_sha256()')}")
    add("")
    add(f"实现（{q(function_lines('CODE/leo_sim/governance.py', 'execution_chain_sha256'))}，行号由生成器从源码读出）：对 "
        f"{q('EXECUTION_CHAIN_PATHS')} 逐个取文件字节的 sha256，返回 "
        f"{q('{path: sha256}')} 映射。")
    add("")
    add(f"- **覆盖集合**：{len(chain)} 个显式路径（不随目录扫描变化）")
    add(f"- **汇总哈希**（对映射做 {q('json.dumps(mapping, sort_keys=True, separators=(",", ":"))')} "
        f"后取 sha256；默认分隔符会得到不同的值，所以分隔符是规则的一部分）：{q(chain_aggregate)}")
    add("")
    L += table(chain)
    add("")
    changed = sorted(set(chain) & set(freeze))
    add("> 本轮改动落在该覆盖内的文件："
        + (", ".join(q(rel) for rel in changed) if changed else "**无**"))
    add("")
    add("---")
    add("")
    add("## 3. 版本复现所需的测试与其他依赖")
    add("")
    add(f"**测试文件（{len(tests)} 个；必须在冻结集合内，但不在第 1 节的哈希覆盖内）**")
    add("")
    L += table(tests)
    add("")
    add("**其他依赖（既不在运行代码哈希、也不在执行链哈希覆盖内，但属于本次改动集）**")
    add("")
    L += table(others) if others else ["（无）"]
    add("")
    add(f"**冻结集合全量（{len(freeze)} 个文件，逐文件 sha256）**")
    add("")
    L += table(freeze)
    add("")
    add("---")
    add("")
    add("## 4. 明确不得进入冻结集合的东西")
    add("")
    add("| 路径 | 为什么 |")
    add("|---|---|")
    for rel, why in EXCLUDED:
        add(f"| {q(rel)} | {why} |")
    add("")
    add("---")
    add("")
    add("## 5. 建立可复核版本的操作顺序")
    add("")
    add(BT * 3 + "text")
    add("1. 在独立工作区只 add §3 列出的全部路径建一次提交（禁止 git add . / -A）；")
    add("   路径条数以生成器当次读到的为准，不要抄写数字")
    add("2. 提交后回填 criteria.json 的 frozen_at_sha，并记录 code_sha256、")
    add("   execution_chain 汇总与冻结集合汇总哈希")
    add("3. 在该提交的全新检出目录做独立冷启动复核（作者自检不算复核）")
    add("4. 只在复核 PASS 之后：重编矩阵 -> 重审 -> 重签授权 -> 部署 -> 正式运行")
    add(BT * 3)
    add("")
    add(f"**为什么必须按这个顺序**：{q('CODE/leo_sim/*.py')} 的任何变化都会作废该 checkout 上的")
    add("全部已编译矩阵与已发授权（平台审计 finding B2）。")
    add("")
    add("---")
    add("")
    add("## 生成命令")
    add("")
    add(BT * 3 + "bash")
    add("python3 CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/round4/generate_freeze_scope.py \\")
    add("    --out CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/round3/FREEZE-SCOPE.md")
    add(BT * 3)
    add("")
    return "\n".join(L)


def main() -> int:
    global FREEZE_COMMIT, ATTESTATION_COMMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--freeze-commit", default=None,
                    help="the commit whose bytes this document describes")
    ap.add_argument("--attestation-commit", default=None,
                    help="the commit that carries this document")
    args = ap.parse_args()
    FREEZE_COMMIT = args.freeze_commit
    ATTESTATION_COMMIT = args.attestation_commit
    out = Path(args.out)
    out.write_text(render(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
