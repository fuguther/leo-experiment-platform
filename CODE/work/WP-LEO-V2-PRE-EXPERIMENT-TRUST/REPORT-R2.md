# R11 收口轮 · 交付报告

| 字段 | 值 |
|---|---|
| 工作包 | `CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/`（本轮新增 `round2/`、`tools/`、`evidence/`） |
| 起点 HEAD | `98c858f642aeca8056d215224b20783b00354026`（未变；本轮**未提交、未推送、未建 PR**） |
| 冻结身份 | `code_sha256 = 681a10b1c71f14f3b266ddd1fa9d841f4bf2096d379f7aa6ed78bb27a1bcc02b`；`execution_chain = d3f283d4…`（**无 commit SHA**，见 §8） |
| 本轮 diff | `round2/minimal-change-r2.diff`：9 文件，**+1130 / −42** |
| 放行表 | `release-table.json`（v2，21 项，FAIL = **R11b / R18**） |

> 本轮只做三件事：关闭 R11 的**入口层**两处缺口、修掉两个漏检点、把必须留给 VM 的部分明确留下。
> 未训练模型、未启动正式研究实验、未判断任何研究问题。

---

## 0. 开工核对（未覆盖、未清理、未忽略）

进入本轮时工作树有 12 个已修改文件与 5 组未跟踪材料。开工前逐个记录 sha256；收工核对：
**5 个未触碰文件哈希逐字不变**（`receipt.py / test_pairing_trace_identity.py / test_review_residuals.py / pull-results-remote.sh / remote_job.py` 中的后 4 个 + 前一轮未动的那些），
本轮只改了任务点名的文件，且在报告里逐一列出（§2）。
`ANALYSIS/ARRIVAL-TIME-T1-20260923/`、`CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/`、
`CODE/work/WP-MATRIX/`（先前遗留的未跟踪目录）**全部未触碰**。

**先读的三份材料**：`REPORT.md`（第一轮验收报告）、`PROPOSED-remote_job-stream-binding.patch`、
V6 第三轮三份回执（`review-cold_start.json` PASS / `review-adversarial.json` PASS / `review-satellite_drl.json` **FAIL**）。

**候选补丁的审查结论**：**采纳，但加强**。

- 三份回执里与本补丁直接相关的既有结论：T1（正式命令必须请求两条流）、T2（流在 run 目录内）、
  T6（提供流时摘要被重算）、T7（不提供流时行为逐字不变）、**T8（对 v5 回执提供流路径必须显式拒绝）**。
- `review-adversarial` 的 **F2** 已经预告了冲突的一半：新增的无条件 `--decision-log` 会让"不产生任何
  decision 行"的正式配置在跑完整场仿真之后被拒（当时的修法是 `__main__.py` 降级为 v5）。
- 候选补丁只解决了"v5 不该被传流"这半边；它**会**让 v5 那一支变成"跳过校验"。这与 T8 的语义张力、
  也与本轮任务书"不得通过跳过 v6 流校验来让测试变绿"冲突。
- 因此本轮的做法是：**按 run 目录解析**（`receipt.verify_receipt_streams`）+ **`require_streams=True`**
  —— v6 的两条摘要照旧重算；v5 的降级必须被**证明**（两条侧车 + 两条流都在、决策流为空），而不是被信任。

---

## 1. 最小 diff

`round2/minimal-change-r2.diff`（对"上一轮结束状态"的逐文件 `diff -u`）：

| 文件 | +/- | 改了什么 |
|---|---|---|
| `CODE/leo_sim/receipt.py` | +155/−9 | 新增 `verify_receipt_streams()`（按目录解析流契约）与 `verify_run_artifacts()`；把流摘要比较抽成唯一的 `_stream_digest_errors()`，两条入口共用 |
| `CODE/leo_sim/pullback.py` | 新 +236 | **结果拉回本地验收入口**：必需文件、回执身份、工件哈希重算、流契约、正式增量 |
| `CODE/leo_sim/recompute.py` | +107/−13 | 生产读数缺字段/非数值改为**不匹配**（NaN 静默通过修复）；补反向 link 集合检查 |
| `CODE/leo_sim/trace.py` | +123/−5 | **突发强度判定**（事前定义 `INTENSITY_SIGMA` 与可判定条件）；`compile_trace` 增加 evidence sink（材料化报告不进 manifest） |
| `CODE/leo_sim/__main__.py` | +49/−2 | `receipt verify-pulled` 子命令；材料化证据经 `__` 键传递（只在编译路径） |
| `CODE/scripts/remote/remote_job.py` | +33/−12 | 抽出 `verify_v2_formal_result()`（VM 与测试共用）；删掉无条件流交付 |
| `CODE/leo_sim/tests/test_formal_stream_gate.py` | 新 +253 | 7 条：两条正式入口 + 篡改 + 源码形状 + 拉回门 |
| `CODE/leo_sim/tests/test_pre_experiment_trust_round2.py` | 新 +167 | 5 条：recompute 缺字段 + 突发强度 |
| `CODE/leo_sim/tests/test_pre_experiment_trust.py` | +7/−1 | 第一轮那条 size/endpoint 用例改为非 burst 配置（burst 声明现在还要过强度判定） |

**未做**：未改 `metrics.py`、未改 manifest/receipt 键集、未新增 config key、未删测试、未放宽任何 tolerance。
`pull-results-remote.sh` **本轮未改**（它上一轮已经在拉回名单里加了 4 个流文件，本轮不动）。

---

## 2. 零决策 / 非零决策两条正式入口复现

脚本：`tools/repro_formal_streams.py` → 记录：`evidence/formal-repro.json`。
**真实的部分**：矩阵编译、work finalization、`authorize_experiment.build_authorization`（所以运行带
`--authorization / --launch-nonce / --expect-run-id` 并写出 `formal_run.json`）、
`remote_job.formal_command` 构造的 argv、run CLI、以及 **VM 自己调用的那个函数** `remote_job.verify_v2_formal_result`。
**唯一重定位**：`CANONICAL_WORKSPACE / CANONICAL_RESULTS` 指向本 checkout（`/data/论文/leo-direct-sim` 在本机不存在）。
**没有 VM**：本脚本复现的是门，不是部署、外部启动证人与 `governance_receipt.json`。

```text
compile: COMPILED_REVIEW_REQUIRED  errors=[]  design single_factor_verified=True
authorization: AUTHORIZED (status)

control   (routing.policy=hop)    exit 0  receipt=leo-sim-receipt/v5
          decision_rows=0  timeline_rows=36
          vm_gate_errors_now = []
          old_unconditional_stream_form_errors =
            ["stream paths were supplied but this receipt is leo-sim-receipt/v5,
              which carries no stream binding"]          <-- 修复前的错判，实测
treatment (routing.policy=oracle) exit 0  receipt=leo-sim-receipt/v6
          decision_rows=18  timeline_rows=120
          vm_gate_errors_now = []

tamper_checks（全部 rejected=true）:
  v5 | empty decision stream given a row  -> True
  v5 | decision stream deleted            -> True
  v5 | timeline stream deleted            -> True
  v6 | decision stream appended to        -> True   (decision_log_sha256 does not match ...)
  v6 | timeline stream deleted            -> True   (timeline_log_sha256: missing or symbolic ...)
  v6 | timeline stream rewritten          -> True   (timeline_log_sha256 does not match ...)
```

**读法**：零决策的合法运行在修复前被判失败（第 4 行），修复后通过；非零决策的 v6 两条摘要**确实被重算**
（追加/删除/改写三条篡改全部被抓）。回归测试：`tests/test_formal_stream_gate.py` **7 passed**。

---

## 3. 篡改与缺失反例（结果拉回本地门）

`python3 -m CODE.leo_sim receipt verify-pulled --run-dir <dir>`（`receipt verify-pulled`，exit 0 / 11）

```text
目录                                        exit  失败的检查   说明
formal（带 formal_run.json，无 governance）   11   P5            B10 在本地被正确暴露：governance_receipt.json
                                                                 只由 VM 的 remote_job 写，本地不可能有
diag（去掉 formal_run.json）                  0   ——            诊断目录；claimable=false
no-timeline                                 11   P4, P5        流缺失被本地哈希重算抓到
tampered（decisions.jsonl 追加一行）          11   P4, P5        摘要不符
no-ledger                                   11   P1, P3, P5    核心文件缺失
```

**"VM 上校验过"不能替代"拉回后校验"** 的机械证明：
`test_a_recorded_vm_verdict_cannot_make_an_incomplete_copy_pass` 取一个真实 v6 run 目录，
删掉 `timeline.jsonl`（模拟拉回静默丢文件：`pull-results-remote.sh` 的 `add_file` 对缺失文件
静默返回），再补上**声称一切正常**的 `formal_run.json` 与
`governance_receipt.json（research_eligible=true, verification_errors=[]）` ——
拉回门仍然 FAIL（P4）。也就是说：**记在文件里的 VM 结论不能把一份不完整的拷贝变成完整的**。

**没有靠跳过 v6 流校验让测试变绿**：`verify_receipt_streams` 的 v6 分支没有任何开关，
两条摘要必须存在且逐字相等；`pullback.verify_pulled_run` 也没有"忽略流"的参数。

---

## 4. recompute 缺失字段漏检

改动前（NaN 比较恒为 False）：把生产读数里每个 link 的 `served_bits` 删掉，`mismatch_total` 仍为 **0**、
R5 仍为 true、`ok` 仍为 true —— 一份没被核验过的读数被判为通过。

改动后实测：

```text
### intact
ok True  mismatch_total 0  R5 True
### production served_bits deleted
exit=10
ok False mismatch_total 20 R5 False
first mismatch: {'field': 'served_bits', 'independent': 0.0,
                 'link_id': 'gsl:downlink:0:G1:90:180', 'production': None,
                 'why': 'field is absent from the production reading'}
```

另外补了两条：非数值（`served_bits="lots"`）报 "not numeric" 而不是崩溃；生产读数**多出**一条
独立实现没有的 link 也会被报（反向集合检查）。回归测试：`test_pre_experiment_trust_round2.py` 里 3 条，
其中第一条自带**反向守卫**（未改动的运行必须 `mismatch_total == 0`）。

---

## 5. 突发强度：窗内有包 ≠ 强度达标

**事前定义的容差**（写在 `CODE/leo_sim/trace.py`，不是写在测试里）：
生成器是精确的 Poisson 稀疏化，窗内接受率为 `total_rate × multiplier`，因此
`E_burst = total_rate × multiplier × W`（嵌套族再乘 inclusion probability）。
判据：`|E_burst − E_base| > INTENSITY_SIGMA·√E_burst + INTENSITY_SIGMA·√E_base`
（`INTENSITY_SIGMA = 3.0`）才**可判定**；可判定时观测值必须落在 `E_burst ± 3√E_burst` 内。

实测三种结果：

```text
强 burst（multiplier=5, 40 Mbps）   inside=225  applied=True   E_burst=250  E_base=50
                                  required=68.6  gap=200        -> VERIFIED
弱 burst（multiplier=1.2, 同上）    inside=49   applied=True   E_burst=60   E_base=50
                                  required=44.45 gap=10        -> UNDECIDABLE
                                  （窗内满包，但"强度符合声明"不可判）
合成稀缺样本（20 包 vs 期望 250）                              -> VIOLATED
                                  materialization_report 抛 TraceError，compile 拒绝
```

**没有仅凭窗内一包标为强度验收**：`applied` 只回答"窗内有没有包"，强度是**另一个**字段
`burst.intensity.status` / `intensity_verified`；UNDECIDABLE 时 `intensity_verified=False`。

---

## 6. 测试命令与原始输出

```bash
# 全量（本轮定稿）
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests \
    CODE/tests ANALYSIS/tests -q -p no:cacheprovider

# 本轮新增的两组
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest CODE/leo_sim/tests/test_formal_stream_gate.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest CODE/leo_sim/tests/test_pre_experiment_trust_round2.py -q -p no:cacheprovider

# 正式入口复现
python3 CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/tools/repro_formal_streams.py \
    --out CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/evidence/formal-repro.json

# 拉回门
python3 -m CODE.leo_sim receipt verify-pulled --run-dir <dir>
```

原始输出（定稿）：`test_formal_stream_gate.py` → `7 passed`；`test_pre_experiment_trust_round2.py` → `5 passed`；
受影响的 6 个测试文件合跑 → `95 passed in 40.38s`；
全量（定稿，逐字结尾）：@@967 passed, 1 skipped, 1 warning in 601.72s (0:10:01)@@（唯一 warning 是既有的未知 mark @@scene_smoke@@）。
第一轮基线为 955 passed；本轮新增 12 条（7 + 5）。

> 第一轮遗留的一条测试（`test_materialization_report_checks_size_endpoints_and_window`）在本轮被**修正**：
> 它的 burst 夹具只有 2 个包却声明 250 个期望窗内包，按新口径应判 VIOLATED。修法是把该用例改成
> 非 burst 配置（它本来测的是包长/endpoint/窗口），**不是**放宽强度判定。

---

## 7. 仍存在的限制（逐项，不隐藏）

1. **没有 VM（R11b）**：`deployment_receipt`、外部启动证人、`governance_receipt.json`、端到端实测全部缺失。
   实测的机械证据：本地任何 formal run 目录都会因为缺 `governance_receipt.json` 而被拉回门 P5 拒绝。
2. **VM 路径只有源码形状断言**：`run_formal` 无法在本机执行；`test_the_gate_never_hands_over_stream_paths_unconditionally`
   断言的是源码里不再出现 `decision_log=str(_run_dir`。这**不是**行为验证，是替代品。
3. **v5 分支不是密码学保证**：v5 回执本来就不绑定任何摘要。能同时删掉侧车与流文件的攻击者可以让
   检查退化为"什么都没请求"。残余面已写入 `round2/PROCEDURE.md` §7 供审阅者裁定。
4. **突发强度口径是本轮选定的**（3σ + 可判定阈值），不是研究设计给定的。研究设计另有要求时以设计为准。
5. **拉回门不重算运行时身份**（刻意：否则 VM 结果永远失败）。它不能替代 `v2_analysis` 的后验身份绑定链。
6. **R18：没有可冻结的身份**。任务书禁止提交，因此本轮的复核材料只够做**改动审查**，
   不够做面向合并的独立冷启动复核。

---

## 8. 代码哈希影响与冷启动复核材料

- 影响清单：`HASH-IMPACT.md`（三个 code_sha256、execution chain 前后值、逐产物作废字段与重算点、重启顺序）。
  本轮改了 `v2_analysis.py`（第一轮）与 `remote_job.py`（本轮），因此 **execution chain 也变了**。
  **当前 checkout 上不存在任何有效的正式授权**，必须重编 → 重审 → 重签。
- 复核材料：`round2/brief.json`（范围/允许输入/可声明与不可声明/provenance 披露）、
  `round2/criteria.json`（18 条判据，每条带 check 与 touches）、`round2/PROCEDURE.md`（逐条复现命令）。
  brief 里明确写了"作者同时是改动者，不能自我判定为独立复核"，并列出**5 处应被优先攻击的弱点**。
- 生成物（未跟踪，供复核）：`CODE/Results/EXP-LEO-V2-PRE-TRUST-REPRO-{control,treatment}-s7/`、
  `EXPERIMENTS/EXP-LEO-V2-PRE-TRUST-REPRO/`、`CODE/work/WP-LEO-V2-PRE-TRUST-REPRO/`。

---

## 9. 放行表（摘要；完整 21 项见 `release-table.json`）

| 项 | 判定 | 依据 |
|---|---|---|
| R1–R10（第一轮的门） | 通过 | 见第一轮报告；R10 本轮补了 manifest 一致性 |
| **R11a** 正式入口流契约（零决策 v5 不错判 / v6 摘要真重算） | **通过** | §2 |
| **R11b** VM 上的真实门（部署→授权→运行→回执→拉回→重验） | **未通过** | §7.1；无 VM |
| **R11c** 结果拉回本地验收入口 | **通过** | §3 |
| **R11d** 两处未提交改动的互斥缺陷 | **通过** | §0 补丁审查结论 |
| R12 无 VM 回执不得声称打通 | 通过（纪律） | §7.1 |
| R13 O3 单列后续工作 | 通过（纪律） | 第一轮 §6.4 |
| R14 不以单测宣布可信 | 通过（纪律） | 本表依据复现记录 + 篡改反例 + 限制清单 |
| **R15** recompute 缺字段必须失败 | **通过** | §4 |
| **R16** 突发强度分别判定、不足则不可判 | **通过** | §5 |
| **R17** 哈希影响列明、不复用旧授权 | **通过** | §8 |
| **R18** 独立冷启动复核材料充分 | **未通过** | §7.6：无 commit SHA 可冻结 |

### 能否启动正式实验

**否。** 两条独立的阻塞理由：**R11b**（没有 VM 实测：部署身份、外部启动证人、`governance_receipt.json` 都不存在）
与 **R17**（`code_sha256` / `execution_chain` 已漂移，当前 checkout 上没有任何有效正式授权）。

**本次修好、可以立刻使用的**：入口层的流契约、结果拉回的本地验收入口、recompute 的字段核验、突发强度判定。
