# 真实星座上的小规模无训练验证（2026-09-26）

**基线**：提交 ~~a49ce82c19329c86c50f96ce2bfa97fe0bbc62c7~~（代码身份 ~~2c17718d…~~）。
本文所有数字都是本机实跑得到的；命令、配置与原始产物见 §6。

**身份声明**：本文全部内容属**诊断**，不是正式授权运行——没有回执、~~research_eligible~~ 为假、
不在任何信任链内。**不得升级为研究结论。**

---

## 1. ① 原场景 no_info 的原因：控制面可达范围，不是别的

工具：~~CODE/experiment_platform/no_info_diagnosis.py~~。
它只读一次普通运行已经写下的两条流（decision rows / timeline），
分类完全来自**该次决策自己记录的观测**，不使用任何全局真值，也不回填任何策略输入。

四个候选原因在实现里是互斥的，因为它们的补救办法不同：

| 原因 | 判据（全部取自观测本身） | 实测 |
|---|---|---|
| **RANGE** 传播范围 | 缓存里有条目，最深的 ~~hops == vis_k~~，且没有一条 ~~advertised_serve_cells~~ 含本包目的地 | ✅ **命中** |
| NOT_ARRIVED 初始化时间 | 存在覆盖目的地的条目，但在该决策时刻尚未到达 | ❌ |
| EXPIRED 有效期 | 到达过覆盖条目，但在该时刻 ~~age_s > ttl_s~~ | ❌ |
| ABSENT 根本没收到 | 缓存里一条条目都没有 | ❌ |

实测（控制面：~~vis_k = 2~~，~~ttl_s = 10 s~~，~~advertise_interval_s = 2 s~~）：

| 运行 | frozen 尝试 | no_info | 原因分布 | 从未被覆盖的卫星 |
|---|---|---|---|---|
| smoke 基线 | 5 | 2 | ~~{RANGE: 2}~~ | — |
| **smoke 强制 S**（原失败案例） | 29 | 28 | ~~{RANGE: 28}~~ | ~~[7]~~ |
| scan 基线 | 41 | 2 | ~~{RANGE: 2}~~ | — |
| scan 强制 N | 98 | 61 | ~~{RANGE: 61}~~ | ~~[3]~~ |

**原失败案例的直接证据**（~~out/no-info-forced-S.json~~，卫星 7，t = 42.526 起，共 28 次）：

~~~
origin=0 hops=1 age=0.53 serve_cells=['G1:90:180']   <- 源 cell，不是目的 cell
origin=1 hops=2 age=0.53 serve_cells=[]
origin=5 hops=2 age=0.53 serve_cells=[]
origin=6 hops=1 age=0.53 serve_cells=[]
~~~

四条排除：

1. **不是初始化时间**：卫星 7 的缓存从 t ≈ 42.5 起持续有 4 条条目，年龄 0.6--2.0 s，远小于 ~~ttl_s = 10 s~~，
   从未空过、从未过期。
2. **不是后续策略**：~~destinations_in_cache~~ 返回空是因为**没有一条条目宣称服务该目的地**；
   这是"信息没到"，不是"策略选了别的"。策略按它看到的信息做了正确的事（hold）。
3. **不是观察窗口**：冻结观测每次都取到 4 条有效条目，窗口不空、不陈旧。
4. **是传播范围**：最深的条目恰好在 ~~hops = 2 = vis_k~~，即控制面允许的最远处；
   而卫星 7 在整段窗口内**从未**收到过任何覆盖其目的地的广告（~~satellites_never_covered = [7]~~）。

附带观察：卫星 7 收到了**源** cell 的广告（origin 0，hops=1），却收不到**目的** cell 的广告。
所以这不是"控制面没工作"，而是"控制面只覆盖到 2 跳"。

---

## 2. ② 预先确定的场景与选点规则

**场景（~~CODE/leo_sim/profiles/t1_real_dual_scan.yaml~~，跑之前就写定）**：
与 ~~t1_frozen_branch_smoke.yaml~~ **只差一个字段** —— ~~demand.offered_mbps~~ 0.5 → 2.0。
原因是原 profile 只产生 1 个包、3 个决策，算不出比例。几何、种子、控制面、路由策略、观测模式、
计算时延、节点处理成本全部保持不变，~~node_process_delay_s~~ 仍为 0.5（与分析 no_info 的场景可比）。

**选点规则（工具 docstring 与产物 ~~selection_rule~~ 里都写了，跑之前确定）**：

- **R1** 候选点 = 一次无强制基线运行里**已提交**的 forward 决策，且记录的候选集 ≥ 2。被 hold / 被拒的决策没有 chosen 可比，不算点。
- **R2** 对点（chosen = c，合法集 L），备选 = L 中除 c 以外的每一个；每个备选执行一次强制重放（同 config、同 trace、同 seed，只在该决策强制该方向）。
- **R3** 该备选**双可达** ⟺ 目标包在基线与强制两条分支里**都**是 DELIVERED。其余一律算失败并**带原因上报**。
- **R4** 一个点只要有**任一**备选双可达就计为双可达点；同时逐个上报每个备选，避免一个侥幸方向掩盖其余失败。
- 成本上界：最多执行 ~~--max-points~~ 个点，按 decision id 升序；被上界跳过的点**列出**而不是静默丢弃。

---

## 3. ②③ 实测人口与结果：真实星座里一个双可达点都没有

~~python3 -m CODE.experiment_platform.dual_reachable_scan --config CODE/leo_sim/profiles/t1_real_dual_scan.yaml --max-points 12~~（原始产物 ~~out/dual-scan.json~~）：

| 量 | 值 |
|---|---|
| 包数 | 13 |
| forward 决策 | 26 |
| 已提交决策 | 39 |
| 决策尝试 | 41（~~{held: 2, committed: 39}~~） |
| **候选点（≥2 候选）** | **13** |
| 已执行点 | 12（1 个被上界跳过，id = 36） |
| 备选总数 | 12 |
| **双可达点** | **0 / 12 = 0.0%** |
| **双可达备选** | **0 / 12 = 0.0%** |
| 失败原因 | ~~{FORCED_NOT_DELIVERED: 12}~~ |
| 双可达时的代价差样本 | 0（无样本可报） |

**失败原因不是"没跑"，是被强制的那一支根本没送达**：12 个备选全部以
~~IN_SYSTEM_AT_STOP~~ 收尾，且对第 1 个点（decision 2，pid 1，sat 2，chosen S，
candidates [N, S]）单独做原因诊断，得到 ~~{RANGE: 61}~~、~~satellites_never_covered = [3]~~ ——
**与①同一个机制**：换到另一个合法方向后，包到达的对端星同样在控制面 2 跳覆盖之外，
拿不到目的地的广告，于是反复 hold 到地平线。

**③ 的结论（先于预测器）**：**在本场景配置下，真实星座里不存在值得预测的代价差异**——
不是"差异很小"，而是**备选方向一个都不送达**，因此没有可比较的代价。
在动预测器之前必须先解决的是**信息可达性**（§1），不是预测精度。

**必须同时说的边界**：这是**一个配置、一个种子**的人口扫描，不是统计样本；
~~hop~~ 策略 + 冻结推理取 ~~legal[0]~~ 的结构决定了"非首选候选"在此拓扑里多半是死端。
换策略、换 OD 或放宽 ~~vis_k~~ 会改变这个比例——**那是研究选择，本轮未替你决定**。

---

## 4. ④ 异步线：定义先纠正，判据暂不实现

~~ANALYSIS/ASYNC-SCHEDULING-DESIGN.md~~ 新增 §0.4，把"异步流量调度"的三种读法摊开：

| 读法 | 调度对象 | 与①的关系 | 需要的新状态 |
|---|---|---|---|
| **P 路径调度** | 下一跳 / 路径 | **就是①**（同一动作空间、同一合法性、同一信息） | 无新动作空间 |
| **R 分流比例** | 每个候选出口分走多少 | ①的推广（① 是权重退化为 one-hot） | **按权重选出口**的决策原语（属引擎语义新增） |
| **S 发送速率** | 发送 / 注入速率 | **与①正交**，作用于包的发射时刻 | **整形/限速**原语；且会改动 trace 这一输入工件 |

文档明确：此前写成 ~~(schedule_epoch, binding_scope, installed_action)~~ 的定义**实际上是 P**，
即研究线① 换了个名字；三选一**未裁定**，属研究者决定。
**在裁定之前，§1.4 的 D1--D5 与 §最小执行设计 一律不得落地成代码**——它们只对 P 成立；
对 R 而言 D1--D5 的自然形式不同，对 S 而言它们完全无关。
与三选一无关、已可确定并保留的只有三条：计算完成与版本安装是显式事件且不回溯；
计算期间旧版本继续完整生效、版本号单调；新信息只走两条既有输出流。

---

## 5. 保留的诊断身份

- 本轮的运行**没有**走 ~~authorize_experiment~~ / ~~run-remote.sh --authorization~~，
  产物没有回执、~~research_eligible~~ 为假、不在任何信任链里。
- 证据级别：**local / synthetic diagnostic**，不是 ~~formally governed VM~~。
- 本文的结论**只**说明：(a) 该配置下 no_info 的机制是控制面 2 跳可达范围；
  (b) 该配置下非首选候选全部不送达。**不说明**任何策略优劣，也不说明真实运营星座的性质。

---

## 6. 可复跑命令 / 配置 / 原始产物 / 提交号

**配置**（本仓内）：
~~CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml~~（原 no_info 案例）、
~~CODE/leo_sim/profiles/t1_real_dual_scan.yaml~~（本轮扫描，预先声明）。

~~~
cd /Users/lge/Desktop/topic/leo-exp-main

# ① no_info 原因：基线 / 强制分支（原失败案例）
python3 -m CODE.experiment_platform.no_info_diagnosis \
  --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
  --out out/no-info-baseline.json
python3 -m CODE.experiment_platform.no_info_diagnosis \
  --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
  --forced-decision-id 2 --forced-action S --out out/no-info-forced-S.json

# ① 在扫描场景上的同两个诊断
python3 -m CODE.experiment_platform.no_info_diagnosis \
  --config CODE/leo_sim/profiles/t1_real_dual_scan.yaml --out out/no-info-scan.json
python3 -m CODE.experiment_platform.no_info_diagnosis \
  --config CODE/leo_sim/profiles/t1_real_dual_scan.yaml \
  --forced-decision-id 2 --forced-action N --out out/no-info-scan-forced.json

# ②③ 双可达扫描
python3 -m CODE.experiment_platform.dual_reachable_scan \
  --config CODE/leo_sim/profiles/t1_real_dual_scan.yaml \
  --max-points 12 --out out/dual-scan.json
~~~

**原始产物**（本机 ~~out/~~，未随提交入库——~~out/~~ 在 ~~.gitignore~~ 内）：
~~no-info-baseline.json~~、~~no-info-forced-S.json~~、~~no-info-scan.json~~、
~~no-info-scan-forced.json~~、~~dual-scan.json~~。

**提交号**：本轮的代码与文档在提交 ~~a49ce82c19329c86c50f96ce2bfa97fe0bbc62c7~~
之后的**本轮提交**里（见汇报中的 commit id）；代码身份 ~~2c17718d…~~ 由
~~git archive <commit> CODE/leo_sim~~ 重算，不由工作树重算。
