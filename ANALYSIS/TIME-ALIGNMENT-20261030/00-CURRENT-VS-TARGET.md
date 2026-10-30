# CURRENT vs TARGET 审计（恢复真实状态后）

> 生成时间：2026-10-30 22:3x（Asia/Shanghai）。所有事实均来自本轮实查（git / 文件 / VM ssh），
> 不采用聊天记录或历史汇报中的状态描述。
> 本文只记录差距与阻塞处置，不含研究结论。

## 0. 本轮实际读过的真源（不是记忆）

| 类别 | 文件/命令 |
|---|---|
| 工作区规则 | `topic/AGENTS.md`、`topic/SPEC.md`（v0.4 Draft）、`topic/STATUS.md`（2026-10-27 生成视图） |
| 推理账本 | `domain-map/reasoning/AGENTS.md`、`README.md`、`state.yaml`、`validate.py` |
| 平台仓库 | `platform/AGENTS.md`、`platform/STATUS.md`、`platform/README.md`、`platform/CODE/...` |
| Git 实查 | `git status` / `git branch -vv` / `git log` / `git worktree list` / `git ls-tree` / `git merge-base --is-ancestor` |
| VM 实查 | `ssh vm`：`/data/论文/leo-t1-wt/{releases,runs,envs}`、`run.log`、`run-manifest.json`、`ps` |
| 运行中证据 | `runs/instr-20261030-{baseline,baseline2,ledger1}` |

**一个必须先纠正的前提**：任务书里的 `reasoning/` 在本工作区**不存在于仓库根**。
唯一存在的是 `domain-map/reasoning/`（2026-10-28 由 commit `1c74ece` 引入），其 `state.yaml`
`topic.id = RQ-TIME-ALIGNMENT`，正是本任务的研究线。本报告与后续状态更新都以它为真源。
`validate.py` 没有 `--stamp` 参数（无 argparse），只能 `python3 reasoning/validate.py`。

## 1. Git / 部署真实状态

| 位置 | 分支 | HEAD | 状态 | 是否实验代码源 |
|---|---|---|---|---|
| `platform/` | `research/scene-v3v4-20261020` | `65acf0c` | clean、与 origin 同步 | ❌ **不含** `CODE/leo_sim/time_alignment.py` |
| `platform/`（本地 `main`） | `main` | `a0904ad` | 落后远端 2 提交 | 有 `time_alignment.py`，但**不含** `f666b59` 候选使用时刻修复 |
| `.wt-fix-20261024/` | `fix/t1-sprint-20261024` | `a2789f7` | dirty：5 文件（deadline 门 + suite loader，**与 ledger/instrumentation 无关**） | 否 |
| `.wt-burst2-20261028/` | `research/local-burst-20261027` | `61d88a6` | clean | 是（F3 实验代码身份） |
| `.wt-instr-20261030/` | `instr/ledger-export-20261030` | `7e7fd5a` | clean、已推送 | ✅ **当前唯一实验代码源** |
| `/private/tmp/base-check` | detached | `61d88a6` | — | 零回归基线用 |

- `f666b59` / `61d88a6` / `7e7fd5a` **都不是** `65acf0c` 的祖先；`65acf0c` 甚至没有 `time_alignment.py`。
  ⇒ topic `STATUS.md` 里"代码：`research/scene-v3v4-20261020 @ 65acf0c`"这句话**对实验代码已失效**，
  它描述的是场景标定线的 checkout，不是时间语义实验的代码线。
- `61d88a6` 是 `research/local-burst-20261027` 的 HEAD，也是 `7e7fd5a` 的父提交 ⇒ 仍是**零回归基线**，
  血缘 relevant，但不能当"当前实验代码"。
- PR #14 = `5e9d9cd`（2026-10-18/19 场景标定 + 预测—真值探针，11 提交）。它是旧主线的合并点，
  不含 arrival-rate / 候选使用时刻修复；**对当前问题已被 `f666b59` 之后的工作取代**。
- VM 已安装 release：`7e7fd5a3333875…-71bc6396…`（对应 commit `7e7fd5a`），
  今天已在其上跑 3 个 diagnostic run（1 个 15 min 超时、2 个完整跑到序列化阶段）。

## 2. CURRENT vs TARGET（任务书要求的最小审计表）

| 项目 | CURRENT 实际状态 | TARGET 需要状态 | Gap | 是否阻塞 |
|---|---|---|---|---|
| 推理账本 `state.yaml` | `schema_version: 1`；`nodes` C1–C6 全 `open`、`source_refs` 全空；`gaps` G1–G6 全 `open`；commit `1c74ece` 后**从未写入证据** | C1–C6 按本轮证据逐节点改写状态、挂 source_refs、补 attack/gap | 内容全缺；`revision/focus/next_action` 字段在 schema 里**不存在**（只有 `topic.updated_at`） | 否（是产出，不是阻塞） |
| 实验代码真正所在 | `instr/ledger-export-20261030 @ 7e7fd5a`（clean、已推、已发布到 VM） | 唯一 pinned release + 显式 commit 记录 | 该分支**未合入 main**；topic STATUS 指向的分支不含 time_alignment | 否（release 身份已固定，可复现） |
| `time_alignment` 能力在哪条线 | `time_alignment.py` + `kernel._time_aligned_order`；四臂 `stale/now/common/candidate`；`state_source ∈ {advertised, ideal_current}` | 保留四臂作为历史，新增"三真值语义 S0/S1/S2，不用 predictor" | 现在只有"按臂查询"，**没有**"对同一决策取三个真值语义"的离线入口；需靠账本+审计记录离线拼 | 是（可离线解决，见 §4） |
| 未提交 ledger/instrumentation 修改 | **无**。全部在 `7e7fd5a`（新增 `queue_ledger.py`、`kernel` 一个 hook、`--queue-ledger`、验收脚本、测试、profile 快照）；`.wt-fix` 的 dirty 是 deadline 门，无关 | 零回归证据 | baseline2 已跑完（switch off）；ledger1 正在序列化（switch on）。**两份同 commit 同 seed 的输出尚未比对** | **是**（收尾前必须完成） |
| 旧血缘（61d88a6 / PR#14 / VM CODE） | `61d88a6` = F3 实验身份（80 run，已出 `F3-RESULTS`）；PR#14 被 `f666b59` 取代；VM 旧 `CODE/` 目录只读 | 明确哪些结果可复算 | F3 结果在**不同场景**（144/96 星、2–5.5 Mbps、D=0.4–0.6 s）且是**四臂对比**，不能搬到 Stage C | 否（只作背景，不作新证据） |
| 场景（Stage C）性质 | `stage_c_e2_6g_adv200.yaml`：140 星、1.2 s、6 城 gravity、offered 6000 Mbps、包 600 kbit、ISL 1 Gbps、队列 24 Mbit、adv 0.2 s | 冻结的 regime 定义 + 有效性门 | **是否 queue-sensitive 尚无实测**；但已完成 run 的 `time_alignment_audit` 显示 124873 个决策中 **103900（83.1%）被时间臂重排** | 部分（需用实测 util/queue share 定性） |
| 测量能力（能否重建 Q_ρ(t_g/t_d/T_ρ)） | 决定行默认**已**记录 `used_state{measured_at,received_at,bits}`、`eta_targets`（T_ρ）、`query_targets`、`scores{predicted_bits,terms}`、`four_direction_audit{candidate_details{resource_id,generation,queue_bits_before}}`；`routing_audit_log` 按 `(pid,hop_index)` 采样 5000 条（默认 cap） | 每候选三真值 | 真值 Q(t) 需要**队列账本**（本轮新增，ledger1 已产出 2.6 MB gz / 逐次 add-remove）；采样 5000 条是 3.7%，可用 `T1_AUDIT_MAX_DECISIONS` 提高 | 否（现有能力**基本足够**，见 §4） |
| 反事实 fork 能力 | `kernel.run_simulation(forced_actions=…)` + `counterfactual.py`（branch fingerprint、拒绝不同分叉点）+ `minimal_branch_compare.py` | C5 的 same-continuation regret | 机制存在，但**本线从未在 VM 上跑过**；且 `OUTCOME_KEYS` 是聚合块，非 per-packet | 是（C5 阶段才需要） |
| 场景真实性 provenance | M-Lab OD 权重 + GPW 人口 + Kepler 星座参数有来源；burst ×3/200 ms **无实测依据**（F3 文献已判） | 允许/禁止论断清单 | 见 `AUDIT-SCENARIO.md` | 否 |
| 文献 novelty 边界 | 有 `F3-LITERATURE-20261028.md` / `01-文献审计.md`；"何时 stale 才真正改决策"未被系统研究（初步） | 两证据层 | 见 `LIT-BOUNDARY.md` | 否 |
| 预算/调度 | VM 256 核 / 628 GB，当前 load ~10；3 个 run 今日已发 | 预注册 regime 后按成本公式并发 | 无调度声明文件（`check_batch_scheduling.py` 不在本 repo 版本内） | 否 |

## 3. 立即处理的阻塞项（本轮已做/在做）

1. **零回归比对**（阻塞项）：`instr-20261030-baseline2`（同 commit、同 seed、同 config、**无** `--queue-ledger`）
   与 `instr-20261030-ledger1`（**有** `--queue-ledger`）正在产出；完成后逐字段比对
   `outcome / fate_counts / queue_area_bits_s / delivery / network_outcome / packet_outcomes_sha256`。
   - 已知 baseline2 输出摘要：`packet_outcomes_sha256`、`fate_counts`、`events_processed` 等可直接核。
   - 跨版本（对 `61d88a6`）比对**不**在本轮做：需要另一个 release 再跑 35 min；现有证据改为
     "switch-off 路径逐字节等价 + 账本自洽（queue area 重建）+ 单测"，并在报告里**明确标注该限制**。
2. **决定行与账本的 join**：`routing_audit_log` 默认 5000 条采样 + `queue-levels` 全量，足以做首轮机制统计；
   如需全量，用 `T1_AUDIT_MAX_DECISIONS` 提高后补一个 run（不重跑四臂，只跑单臂）。
3. **`T_ρ` 语义冻结**：`TIME-STATE-SEMANTICS.md` 已给出"FIFO 出口的工作量状态，其物理使用时刻是
   到达时刻 `T_arrival`"的判定，并指出 `f666b59` 把 candidate 臂改到 `T_service` 带来的
   语义不一致 + 潜在重复计价。**本线一律用 `T_arrival`**，并在报告里同时给 `T_service` 作对照。

## 4. 一句话状态

代码线、部署、账本能力都已就绪且可复现；**真正的 gap 是"从未把已记录的状态/时刻拼成 C1→C4 的定量证据"**，
以及零回归比对尚未落盘。前者本轮用 ledger1 的账本 + 5000 条决策审计做，后者等两个 run 结束。
