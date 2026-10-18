#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把工作区根 AGENTS.md 的「实验调度」节同步为本仓库 AGENTS.md 的受管块。

依据：Nx 的 managed block + git diff --exit-code 机制（不靠人记得同步）。stdlib only。

用法：
    python3 tools/sync_agents_managed.py            # 就地重写受管块
    python3 tools/sync_agents_managed.py --check    # 只检查，不一致则退出码 1

设计约束：
  * 本仓库块不含本机路径（/data/、runs/_batch_logs/、tools/mem_watchdog），
    因为这些只在某台机器上成立；本机专属内容留在工作区根。
  * 源文件不存在时（仓库被单独 clone 到别处）--check 退出 0 并提示跳过，
    不把「拿不到源」误判为「不一致」。
"""
import argparse
import os
import re
import sys

BEGIN = "<!-- managed:begin id=scheduling"
END = "<!-- managed:end id=scheduling -->"
HEADING = "## 批次并行调度（2026-10-17 起，执行层规则）"
PREAMBLE = [
    "> **本块由 `tools/sync_agents_managed.py` 从工作区根 `AGENTS.md` 的「实验调度」节生成，请勿手改。**",
    "> 校验：`python3 tools/sync_agents_managed.py --check`（与源不一致则退出码 1，可挂 pre-commit/CI）。",
    "> 本节**不含本机路径**，可随仓库走；本机专属的参考实现与台账位置见工作区根 `AGENTS.md`。",
]
LOCAL_ONLY = ("/data/", "runs/_batch_logs", "tools/mem_watchdog")


def extract_section(src_text):
    m = re.search(r"^## 实验调度（.*?）\s*$", src_text, re.M)
    if not m:
        return None
    rest = src_text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    return [ln.rstrip() for ln in body.splitlines()
            if ln.startswith("- ") and not any(t in ln for t in LOCAL_ONLY)]


def render(bullets):
    out = [BEGIN + " source=工作区根 AGENTS.md「实验调度」节 -->", HEADING, ""]
    out += PREAMBLE + [""]
    out += bullets + [END]
    return "\n".join(out)


def replace_block(text, block):
    start = text.find(BEGIN)
    if start < 0:
        raise SystemExit("目标文件里没有受管块标记：" + BEGIN)
    end = text.find(END, start)
    if end < 0:
        raise SystemExit("受管块没有结束标记")
    return text[:start] + block + text[end + len(END):]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.path.join("..", "AGENTS.md"))
    ap.add_argument("--target", default="AGENTS.md")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(a.source):
        print("跳过：源文件不存在（%s），本仓库可能被单独 clone" % a.source)
        return 0
    bullets = extract_section(open(a.source, encoding="utf-8").read())
    if not bullets:
        print("跳过：源文件里没有「实验调度」节")
        return 0

    cur = open(a.target, encoding="utf-8").read()
    want = replace_block(cur, render(bullets))

    if a.check:
        if cur == want:
            print("OK：受管块与源一致（%d 条规则）" % len(bullets))
            return 0
        print("FAIL：受管块与源不一致。运行 python3 tools/sync_agents_managed.py 修复。")
        return 1

    if cur == want:
        print("无需改动（%d 条规则）" % len(bullets))
        return 0
    open(a.target, "w", encoding="utf-8").write(want)
    print("已重写受管块（%d 条规则）" % len(bullets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
