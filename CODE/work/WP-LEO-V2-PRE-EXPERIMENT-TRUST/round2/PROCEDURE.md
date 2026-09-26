# 冷启动复核过程（R11 收口轮）

**审阅者须知**：这是一份**改动审查**材料。本轮按任务书禁止提交，因此没有 commit SHA；
唯一可冻结的身份是 `code_sha256` / `execution_chain_sha256`（见 `criteria.json`）。
在它们被写进一个提交之前，任何回执都无法指向本轮的代码身份，本材料**不足以**支撑
"面向合并的独立冷启动复核"。请在结论里如实标注这一点。

判据集：`round2/criteria.json`（18 条，封闭，轮内不可增补）。
判定格式：每条给出 PASS/FAIL；FAIL 必须附「复现命令 + 实际输出」，给不出即自动降级。
`overall` 只能由 `criteria_results` 推导。

---

## 0. 先核对身份（不核对身份，后面全部无意义）

```bash
cd /Users/lge/Desktop/topic/leo-experiment-platform
git rev-parse HEAD                       # 期望 98c858f642aeca8056d215224b20783b00354026
python3 -c "from CODE.leo_sim.receipt import code_sha256; print(code_sha256())"
python3 -c "import json;from CODE.leo_sim import governance as g;print(json.dumps(g.execution_chain_sha256(),sort_keys=True))"
```

期望：`code_sha256 = 681a10b1c71f14f3b266ddd1fa9d841f4bf2096d379f7aa6ed78bb27a1bcc02b`，
execution chain 含 `remote_job.py = c468c2bcb77832f31b0b0bbaf590c04be276a678019738f73640e369bdb1d13e`。
**任何一个不匹配即停止**：判据全部失效，直接给 FAIL 并说明。

---

## 1. 全量套件（C17）

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests \
    CODE/tests ANALYSIS/tests -q -p no:cacheprovider
```

---

## 2. 正式入口两条复现（C1 / C2 / C3 / C4）

```bash
python3 CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/tools/repro_formal_streams.py \
    --out CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/evidence/formal-repro.json
```

期望（逐字）：control 臂 `exit 0 / leo-sim-receipt/v5 / decisions 0 行 / timeline 36 行 /
vm_gate_errors_now == [] / old_unconditional_stream_form_errors 恰为 1 条
"carries no stream binding"`；treatment 臂 `exit 0 / leo-sim-receipt/v6 / decisions 18 行 /
timeline 120 行 / vm_gate_errors_now == []`；`tamper_checks` 六条全部 `rejected=true`。

**注意**：脚本把 `remote_job.CANONICAL_WORKSPACE / CANONICAL_RESULTS` 重定位到本 checkout，
因为 `/data/论文/leo-direct-sim` 在本机不存在。这是唯一的重定位，脚本自己在
`relocated_constants` 里写明。**没有 VM**：本脚本复现的是"门"，不是部署、外部启动证人
与 `governance_receipt.json`。

### 反向守卫（必做，否则"通过"可能是靠全拒）

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest CODE/leo_sim/tests/test_formal_stream_gate.py -q -p no:cacheprovider
```

重点看 `test_the_gate_never_hands_over_stream_paths_unconditionally`"：它断言
`decision_log=str(_run_dir` 已从 `remote_job.py` 消失。**请自行判断这条断言是否足够** ——
VM 路径无法在本机执行，源码形状断言是它的替代品，这本身是一条应当被质疑的弱点。

---

## 3. 拉回本地门（C10 / C11 / C12）

```bash
W=/tmp/pb; rm -rf $W; mkdir -p $W
SRC=CODE/Results/EXP-LEO-V2-PRE-TRUST-REPRO-treatment-s7
cp -R $SRC $W/formal        # 带 formal_run.json：本地没有 governance_receipt.json
cp -R $SRC $W/diag && rm $W/diag/formal_run.json   # 诊断目录
cp -R $SRC $W/no-timeline && rm $W/no-timeline/timeline.jsonl
cp -R $SRC $W/tampered && printf '{"x":1}\n' >> $W/tampered/decisions.jsonl
cp -R $SRC $W/no-ledger && rm $W/no-ledger/ledgers.json

for n in formal diag no-timeline tampered no-ledger; do
  python3 -m CODE.leo_sim receipt verify-pulled --run-dir $W/$n >/dev/null 2>&1
  echo "$n exit=$?"
done
```

**实测期望（2026-09-25）**：

| 目录 | exit | 失败的检查 | 读法 |
|---|---|---|---|
| `formal`（带 `formal_run.json`，无 `governance_receipt.json`） | **11** | P5 | **这是 B10 在本地被正确暴露**：`governance_receipt.json` 只由 VM 上的 `remote_job` 写，本地正式运行不可能有它。拉回一个正式 run 却没有它，就是拉回不完整 |
| `diag`（去掉 `formal_run.json`） | **0** | —— | 诊断目录：不声称正式增量，`claimable=false` |
| `no-timeline` | 11 | P4, P5 | 流缺失被本地哈希重算抓到 |
| `tampered` | 11 | P4, P5 | 流被追加一行 |
| `no-ledger` | 11 | P1, P3, P5 | 核心文件缺失 |

**上表第一行不是缺陷**，而是本轮无法绕开的事实：没有 VM 就没有 `formal_run.json` + `governance_receipt.json` 这一对，
所以**任何本地目录都不可能通过完整的正式拉回门**。这正是"没有 VM 时 R11 只能保留为未通过"的机械证据。

（`/tmp` 只用于本节的临时副本，与平台的"日志父目录不得含符号链接"约束无关，
因为这些副本不参与任何 trace 编译。）

**C11 的独立做法**：给一个被截断的副本补上 `formal_run.json` 与
`governance_receipt.json`（`research_eligible=true`、`verification_errors=[]`），
观察拉回门是否仍然 FAIL。这正是 `test_a_recorded_vm_verdict_cannot_make_an_incomplete_copy_pass`。
**请判断这条是否真的证明了"VM 校验过≠拉回后校验"**，还是只是重复了 C10。

---

## 4. recompute 漏检（C13 / C14）

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  CODE/leo_sim/tests/test_pre_experiment_trust_round2.py -q -p no:cacheprovider
```

手工复现（对抗做法）：从 `evidence/run-dirs/sampled-run/ledgers.json` 的每个
`congestion_metrics.links[*]` 里删掉 `served_bits`，跑
`python3 -m CODE.leo_sim recompute --run-dir <dir>`，期望 `ok=false` 且
`production_cross_check.mismatch_total > 0`，且 `why` 为
"field is absent from the production reading"。**改动前**这条会给出
`mismatch_total = 0`（NaN 比较恒为 False），请独立确认这一点。

---

## 5. 突发强度（C15）

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  CODE/leo_sim/tests/test_pre_experiment_trust_round2.py -q -p no:cacheprovider -k burst
```

对抗做法：把 `burst_multiplier` 设成 1.2（窗口仍然满包），确认
`intensity.status == "UNDECIDABLE"` 且 `intensity_verified is False` ——
即"窗内有包"**没有**被当成强度验收。再确认可判定条件
（`E_burst - E_base > sigma*sqrt(E_burst) + sigma*sqrt(E_base)`）写在
`CODE/leo_sim/trace.py` 里，而不是写在测试里。

---

## 6. 键集与身份不变（C16 / C18）

```bash
python3 - <<'EOF'
import json, hashlib, pathlib
run = pathlib.Path("CODE/Results/EXP-LEO-V2-PRE-TRUST-REPRO-treatment-s7")
r = json.loads((run/"receipt.json").read_text())
print("manifest sha match:", r["trace_manifest_sha256"] == hashlib.sha256((run/"manifest.json").read_bytes()).hexdigest())
print("__ keys on disk:", [k for k in json.loads((run/"manifest.json").read_text()) if k.startswith("__")])
EOF
python3 -c "from CODE.leo_sim import config as c;print(c.load_config_file('CODE/leo_sim/profiles/smoke.yaml')['sha256'])"
```

期望：`manifest sha match: True`、`__ keys on disk: []`、
smoke `config_sha256 = 934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。

---

## 7. 已知弱点（请优先攻击这几处，不要只复述上面的通过项）

1. **没有 commit SHA**：本轮材料无法被冻结给一个独立回执，见 briefing 的 caveat。
2. **VM 路径只有源码形状断言**（C8/C9）：`verify_v2_formal_result` 在 VM 上真正跑起来的样子未被观测。
3. **v5 分支的"矛盾检查"不是密码学保证**：v5 回执本来就不绑定任何摘要，检查的是
   "两条侧车齐备 + 决策流非空"这一矛盾，以及 `require_streams` 下的完整性。
   一个能同时删掉侧车与流文件的攻击者可以让检查退化为"什么都没请求"。
   **请判断这个残余面是否应当被接受，或是否应当把侧车 manifest 的哈希也绑进回执。**
4. **突发强度只在可判定样本上成立**：`INTENSITY_SIGMA = 3.0` 与可判定条件是本轮选定的口径，
   不是用户给定的口径。若研究设计另有要求，应以设计为准。
5. **pull-back 门不重算运行时身份**：这是刻意的（否则 VM 结果永远失败），
   但也意味着它不能替代 `v2_analysis` 的后验身份绑定链。
