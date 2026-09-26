# 第三轮：拉回验收结论权限 + 突发强度统计语义

| 字段 | 值 |
|---|---|
| 起点 HEAD | `98c858f642aeca8056d215224b20783b00354026`（未变；**未提交、未推送、未建 PR**） |
| 复核输入 | `round2/independent-review-probes.json`（本轮作者只读，未修改 round2/ 任何文件） |
| 冻结身份 | `code_sha256 = 61d48a7dbab79cfae9c8aa49853ccd223cf616d189675dcde9459334297401e4`；execution chain 与上一轮相同（本轮未动那 6 个文件） |
| 本轮 diff | `round3/minimal-change-r3.diff`：8 文件，**+1040 / −180** |
| 证据 | `round3/evidence.json`（改前/改后逐项） |
| 放行表 | `release-table.json` v3：23 项，FAIL = **R11b / R18** |

---

## 0. 开工核对

HEAD 与工作树与上一轮收工一致；`git stash list` 为空；`round2/` 目录（含复核者加入的
`independent-review-probes.json`）**未修改**；`ANALYSIS/ARRIVAL-TIME-T1-20260923/`、
`CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/`、`CODE/work/WP-MATRIX/` 均未触碰。
`CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/` 的三份回执本轮只作为设计前提引用。

---

## 1. 发现一：拉回验收的结论权限

**复核探针**：一个手写目录，`formal_run.json` 只有 1 个键、`governance_receipt.json` 只有 5 个键，
缺 `schema / run_id / launch_nonce / deployment_receipt / external_launch_witness / authorization_binding`。
改前实测：`ok=true, claimable=true, errors=[]` —— 一个文件完整性检查冒充了正式资格判定。

**处置**（`CODE/leo_sim/pullback.py` 重写）：

1. **`claimable` 恒为 `false`。** 通过本门只表示"本地文件完整 + 已覆盖的绑定检查通过"。
   报告新增 `claimable_reason` 与 `formal_eligibility`，其 `status` 恒为 `NOT_EVALUATED`、
   `authority` 指向既有的 `CODE/experiment_platform/v2_analysis.py`，并逐条列出**本门没有检查**的东西
   （授权 cohort 绑定、部署身份、运行时身份链、外部启动证人的 VM 侧字段）。
   **没有**新建一套"正式资格"校验，因为那会与既有权威并行；本门只声明自己判不了。
2. **`expect` 由调用方显式声明**（`"formal"` / `"diagnostic"`，必填）。
   不再由"`formal_run.json` 在不在"推断：拉回流程**对缺失文件是静默跳过**的，
   所以"文件不在"恰恰是本门要抓的失败模式，不能反过来变成降级依据。
   两个方向都强制：声明 formal 而正式产物缺失 → 失败；声明 diagnostic 而目录带着正式产物 → 失败。
3. **契约键集**照生产者写出的形状强制：正式见证 9 键（+1 可选），治理回执 20 键；
   另加自封印校验 `payload_sha256 == canonical_sha(其余字段)`（复用 `v2_analysis.canonical_sha`）、
   三个哈希绑定、`research_eligible / verification_errors`、nonce 与授权绑定一致性、摘要格式。
   复用既有词汇表：`v2_analysis.EXTERNAL_STATUS_SCHEMA`、`GOVERNANCE_WITNESS_FIELDS`。
   `tests/test_pullback_authority.py::test_the_contract_key_sets_match_their_producers`
   用真实生产者写下文件再比对键集，防止常量漂移。
4. **外部启动证人只在调用方声明位置时检查**（`--witness-root`）；未声明时报告写
   `not_checked_here`，不假装检查过。

**改前 / 改后**

```text
改前（复核探针原样复现）
  ok=true  claimable=true  errors=[]
改后  简化见证 + --expect formal
  exit=11  ok=false  claimable=false  status=NOT_EVALUATED
  失败检查 P5
  formal_run.json is missing contract fields ['authorization_sha256','code_sha256',
    'config_sha256','conservation_ok','launch_nonce','natural_end','run_id','schema']
  governance_receipt.json is missing contract fields [15 项，含 payload_sha256 ...]
改后  正式见证丢失 + --expect formal      -> 失败：formal_run.json is missing or symbolic
改后  正式见证丢失 + --expect diagnostic  -> 失败：contains formal artifacts but the
    caller declared expect='diagnostic'; a lost witness must not be accepted as a
    diagnostic result
改后  既无正式产物 + --expect diagnostic  -> ok=true, claimable=false, NOT_EVALUATED
```

---

## 2. 发现二：突发强度的统计语义

**复核探针**：`_burst_cfg()` 默认参数、只改 `scenario.seed=222`、生成器未改动，产出 198 个窗内包，
却被强度门拒绝。本轮把这一点量化了：

```text
种子 1..400（同一配置，只换 seed）
  改前：拒绝 2 个（222、227）-> 假阳性率 0.5%
        窗内均值 248.402（期望 250），样本 sd 15.565（理论 sqrt(250)=15.761）
        —— 生成过程是对的，检验是错的
  改后：拒绝 0 个
```

**处置**（`CODE/leo_sim/trace.py`）：

1. **把两个问题分开**：
   - "生成器是否施加了声明的变换" —— 代码与配置的性质，由新增的 **`verify_burst_transform()` 确定性检查**：
     在 3 个经度 × 6 个边界点（窗前/窗首/窗中/窗尾−ε/窗尾/窗后）探测倍率函数，
     并校验稀疏化恒等式 `proposal_rate(t) × acceptance(t) == base_rate × multiplier(t)`。
     不成立即 `TraceError`。三种破坏（从不施加、处处施加、按经度泄漏）实测全部被抓。
   - "这一次抽样落在哪里" —— 种子的性质，保留为**诊断**，**永不**拒绝编译。
2. **不换种子、不重试**：一次种子、一次生成、一份报告。`test_one_seed_generates_once_and_is_never_reseeded`
   断言同种子两次编译的 trace 逐字节相同，换种子则不同。
3. **命名不越权**：`VERIFIED / VIOLATED` → `COMPATIBLE / INCOMPATIBLE`；
   `intensity_verified` → `intensity_compatible`；理由字段明写
   "statistically COMPATIBLE ... which is not a proof that the transform is correct"。
   统计相容不再被叫作"已证明变换正确"。
4. **原始样本与统计诊断完整保留**：`scenario_seed / observed_packets / expected_packets_if_burst_applied /
   expected_packets_if_burst_not_applied / acceptance_band / sigma_distance /
   required_separation / predicted_separation / raw_sample_preserved`。
5. **明确的统计设计**（`tests/test_burst_transform.py`，常数在看任何样本之前定下）：
   N 个种子的**均值**，接受带 `3·sqrt(m/N)`；并且**先断言设计有区分力**
   （声明倍率与"未施加"倍率的差必须超过 4 个带），否则测试自身失败。
   实测：mult=5、N=40 → 均值 246.4（带 ±7.5）；mult=1.2、N=200 → 均值 60.37（带 ±1.64，
   而"未施加"为 50，差 10 > 4×1.64）。
6. **结构性错误仍然拒绝**（未放松）：模式不支持 burst、窗口越界、倍率 ≤ 1、生成器不施加变换、
   以及**窗内零包**。最后一条是**充分性**拒绝而不是统计检验：声明窗口一个包都没收到时
   处理不可观测，操作者可据以调高速率或放宽窗口；拒绝消息里带上样本统计量与种子，可复核。
   该定性已写进测试注释与放行表 R16 的 limits。

**改前 / 改后（种子 222）**

```text
改前  TRACE COMPILE FAILED: the declared burst intensity did not materialize:
      198 packets inside the window against an expected 250.000 +/- 47.434 (3.0 sigma)
改后  compile 通过；problems == []
      transform.mismatched_probes = 0
      intensity.status = INCOMPATIBLE  intensity_compatible = false
      scenario_seed = 222  observed = 198  sigma_distance = 3.289
      reason: "property of THIS SEED, not as a defect of the transform"
```

---

## 3. 最小 diff

`round3/minimal-change-r3.diff`（对"第二轮结束状态"的逐文件 `diff -u`）：

| 文件 | +/- | 改了什么 |
|---|---|---|
| `CODE/leo_sim/pullback.py` | +321/−94 | 结论权限重写：`claimable` 恒 false、`expect` 必填、契约键集与自封印校验、外部证人按声明检查 |
| `CODE/leo_sim/trace.py` | +177/−50 | 新增确定性变换检查；强度改为诊断（COMPATIBLE/INCOMPATIBLE），不再拒绝编译 |
| `CODE/leo_sim/__main__.py` | +15/−6 | `receipt verify-pulled --expect/--witness-root`；材料化注释同步 |
| `CODE/leo_sim/tests/test_formal_stream_gate.py` | +16/−11 | 拉回用例改用显式 `expect` |
| `CODE/leo_sim/tests/test_pre_experiment_trust_round2.py` | +32/−17 | 强度用例改到新语义；新增"偏离不阻断"用例 |
| `CODE/leo_sim/tests/test_pre_experiment_trust.py` | +8/−2 | 状态断言改为确定性检查；零包拒绝的消息断言带上统计量 |
| `CODE/leo_sim/tests/test_pullback_authority.py` | 新 +272 | 6 条：必填声明、简化见证、见证丢失（两个方向）、契约形状仍不 claimable、外部证人、键集对生产者 |
| `CODE/leo_sim/tests/test_burst_transform.py` | 新 +199 | 9 条：边界探针、三种破坏、种子 222、230 种子扫描、确定性重放、标定设计 |

**未做**：未改 `metrics.py`、未改 manifest/receipt 键集、未新增 config key、未删测试、未放宽 tolerance；
未新增任何平台功能（新代码只服务这两项复核发现）。

---

## 4. 受影响测试与原始输出

```bash
for f in test_pullback_authority test_burst_transform test_formal_stream_gate \
         test_pre_experiment_trust_round2 test_pre_experiment_trust; do
  PYTHONDONTWRITEBYTECODE=1 python3 -m pytest CODE/leo_sim/tests/$f.py -q -p no:cacheprovider | tail -1
done
```

```text
test_pullback_authority                6 passed in 1.11s
test_burst_transform                   9 passed in 4.32s
test_formal_stream_gate                7 passed in 1.77s
test_pre_experiment_trust_round2       5 passed in 0.26s
test_pre_experiment_trust             13 passed in 0.47s
（五个文件合跑：40 passed in 6.46s）
```

全量（定稿，逐字结尾）：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests \
    CODE/tests ANALYSIS/tests -q -p no:cacheprovider
-> 982 passed, 1 skipped, 1 warning in 582.56s (0:09:42)
```

（第二轮基线 967 → +15：`test_pullback_authority` 6 条 + `test_burst_transform` 9 条。
唯一 warning 是既有的未知 mark `scene_smoke`。）

---

## 5. 放行表（摘要；完整 23 项见 `release-table.json`）

| 项 | 判定 | 本轮变化 |
|---|---|---|
| R1–R14（既有门） | 通过 | 未变 |
| **R11b** VM 上的真实门 | **未通过** | 仍未通过；本轮新增证据：本地 formal run 目录因缺 `governance_receipt.json` 被拉回门 P5 拒绝（B10 正确暴露） |
| **R11c** 结果拉回本地验收入口 | 通过 | **收紧**：`claimable` 恒 false、`expect` 必填、契约键集强制 |
| **R15** recompute 缺字段 | 通过 | 未变 |
| **R16** 突发倍率 | 通过 | **重写**：确定性检查 + 统计诊断；改前 400 种子拒 2、改后拒 0 |
| **R17** 哈希影响与不复用旧授权 | 通过 | 身份值更新为 `61d48a7d…` |
| **R18** 独立冷启动复核材料 | **未通过** | 补充冻结范围清单，但仍无 commit SHA 可冻结 |
| **R19** 拉回验收不得输出 claimable=true / 见证缺失不得自动降级 | **通过** | 新增 |
| **R20** 冻结文件范围已列出 | **通过** | 新增 |

### 能否启动正式实验

**否。** 阻塞理由与上一轮相同且本轮复核未改变：**R11b**（无 VM）与 **R17**（`code_sha256` 已漂移，
当前 checkout 上没有任何有效正式授权）。R18 仍然未通过 —— 本轮把冻结文件范围列清楚了，
但"把它变成一个可冻结的身份"需要一次提交，而任务书禁止提交。

---

## 6. 冻结文件范围

见 `round3/FREEZE-SCOPE.md`：**22 个必冻文件**（按路径枚举，含全部 `CODE/leo_sim/tests/*.py`，
因为它们同样决定 `code_sha256`）、**7 类不得进入集合的路径**（他人的唯一研究材料、既有复核回执、
本任务的证据产物与生成物）、以及"冻结 → 回填 frozen_at_sha → 独立复核 → 重编 / 重审 / 重签"的顺序。

---

## 7. 仍未解决的限制（逐项）

1. **R11b**：没有 VM。`deployment_receipt`、外部启动证人、`governance_receipt.json`、端到端往返全部缺失。
2. **R18**：没有可冻结的提交身份；`round2/criteria.json` 的 `frozen_at_sha` 仍为 `null`。
3. **拉回门的 v5 分支不是密码学保证**：v5 回执本来就不绑定摘要；本门检查的是"两条侧车齐备但决策流非空"这一矛盾
   与 `require_streams` 下的完整性。残余面已写入 `round2/PROCEDURE.md` §7 供审阅者裁定（本轮未改该文件）。
4. **突发强度的 σ 与可判定阈值是平台口径**（3σ + 4 带区分力），不是研究设计给定的口径。
5. **窗内零包仍是充分性拒绝**（非统计检验）：声明预测包数极小时会拒绝，理由与样本统计量一并给出。
6. **VM 路径只有源码形状断言**（`run_formal` 无法在本机执行）。
