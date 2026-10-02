# WP-T1-COMPLETE — 状态账本

> 计划：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md` 顶部当前节。交付：`REPORT.md` 当前准备复核段；机器判据：`criteria.json`；旧合同：`contract_dev_c.yaml`（失效，禁止运行）。
> **执行边界：** 用户已授权连续实施代码、反例/回归、最多51个新增开发calls及独立≤100 VM分钟DDQN训练包；旧B费用继续计入。根已单独批准本合同绑定的唯一seed7低负载四臂成本探针，允许提交推送、发布及最多4 calls；须先完成clean release/runtime/input/run身份验真，不扩成主矩阵或训练。FORMAL_RUN、seed1001+和main合并不在授权内。

## 当前研究与工程状态（2026-10-02；DESIGN_READY=true，PROBE_CODE_READY=true，COST_PROBE_READY=false，RELEASE_READY=false）

- 根接受的范围仅为seed7原生人口低负载四臂成本探针代码子图和语义输入；授权提交/推送任务分支、immutable release和唯一新run，最多4个kernel calls，不append/resume/隐藏重试，不启动主矩阵或训练。FORMAL_RUN、seed1001+和main合并不在此范围。
- 持久合同`contract_dev_cost_probe.yaml` SHA-256 `6b02bf9014ae2423f99b08d6951594aa87d57d5675f1857847c27b9bbf62a505`含根限域授权；唯一cell `b-bounded_population_cost_smoke-network-seed-7`、四臂、4 calls，执行链SHA `a296d85827243f89b51eafeada4cf938132d2df65d596998c0e5e526607ec633`，cell input SHA `70553d1dc6bce687309f18348c84c863c5213679cdc264b915ea9564f2dbf905`。根compile-only核对validate=true及280/14、5Mbps/12000bit/N1/1ms、research D=4/物理TTL独立、窗口正确。
- 根独立核验原始五因果反例+stage门30 passed，相关ETA/四臂/receipt/control/trace/config/population 164 passed。新增合同测试要求删授权副本仍拒绝，持久授权合同只能放行精确单cell/4-call scope；此前REQUEST_CHANGES与251 passed/4 failed中断事实保留。
- B历史8 calls/109.071837305s继续计入60/3600上限；本段更新时本轮VM calls=0。提交、push、release、VM依赖/GPW/实际bundle及run身份验真完成后才可把COST_PROBE_READY记为通过。整体CODE_READY、CORE_COST、MODEL、DDQN_COST、FULL_COST和完整RELEASE均false。

## 历史工作包 B 状态（2026-10-01；第二 pilot 失败后停止）

- **运行身份与回传。** 第二 pilot 使用 release `ffedada9274e7db1fdb5ea548d13ce4920c9815b-8e624431c26c54ea52efc795fa0cc72b5d484e32708bb1a4ff5f535959753a74`，源码 commit `ffedada9274e7db1fdb5ea548d13ce4920c9815b`，artifact SHA-256 `ae6c9845ffa2b64fe56c30c34b351f197b84382075e527fc3518c26b6280fa7d`。唯一新 run `wp-b-dev-20261001-02` 已结束 `failed` / exit 3，状态 `SMOKE_FAILED`；不可变回执 pullback 已 `VERIFIED`，证据 URI `evidence://t1/wp-b-dev-20261001-02`。canonical receipt SHA `23399d84caa975b252e6a325d85be67834e1c9bd4699ded37dbcc49e224090cd`，receipt 文件 SHA `374ec812050c35721e29adc8e20f1eaec7bf7ec859cede72dc1584aebea43bd3`，manifest SHA `03b76ef8b2a816cc87d9cf22b0472f8110fa8e9f016832c89fb3c888d7c92c44`，archive SHA `8724bb437e33ca91aff9a7b46ea298f35e59ceecfd65e954d2a256f6ff1d3c48`；46 个回传文件。先前 run `wp-b-dev-20261001-01` 的 receipt/result 均按旧身份保留。
- **实际执行矩阵。** B tier 共 8 格/52 静态调用上界；本 run 仅启动第一格 `b-steady_multi_od_negative_control_b-network-seed-7`，任务 cell 自身 `ok`，但该格的 negative-control 正交付 smoke 未通过。其余 7 格、48 次静态调用未启动；没有试跑或续跑竞争 seed/模式/分支格。剩余 4 格/18 次本来只在 pilot 过门后 append，本次不得追加。
- **逐臂结局。** stale/now/common/candidate 每臂 60 offered、60 admitted at satellite ingress、0 delivered、0 bits、0 goodput；规范 `outcome_document.partition_exact=true`、row_count=75。所有 60 包到 stop=50 s 时均为 `IN_SYSTEM_AT_STOP`，不能算终端丢包。D=30、人口窗 [5,20] 的 45 包/臂结局精确，deadline loss=1.0，0 interval-censored、0 not-computable；E2E latency 无 delivered samples，故不可计算。结果是场景未准入且结局删失，不是算法负结果。
- **场景准入与路由日志。** VM 全网准入 summary 为 admitted=0、not_valid=0、not_computable=1；冻结门槛未改。冻结 source trace 有六个有向 OD，最大 5 个同时 OD；实际逐包 route audit 120 rows/臂，只对应 40 个唯一 PID、4 个 OD，每包三次决策记录。eq→Pacific 与 Pacific→eq 两个 OD 的 20 个包没有 route audit；不以 source trace 代替全网准入，不能报告配对决策或样本量。`paired-analysis` 为 `INCOMPLETE`，没有 eligible block。
- **有界的策略机制诊断。** `kernel.py` `_build_ta_snapshot` 将 `pkt.dst` 交给 `_candidate_resource_map`；后者按 `destinations_in_cache(cache,pkt.dst,now)` 识别目的服务星，目的输入与已到达广告确实存在。问题在于目的卫星直达候选被记为 `status=delivered_downlink`，而 `_build_ta_snapshot` 只给 `status=ok` 的 ISL 候选建 `ResourceKey`；评分器把缺 `resource_mapping` 或预测/ETA 的候选标为 fallback，并排在任何完整候选之后。
  - PID1（equator→North）在 sat1 时缓存已有 sat2 的目的服务广告。N→2 的资源状态为 `delivered_downlink`，N 得到 fallback/missing `resource_mapping` 与 `no_received_history`；W→19 得到有限总分 `0.2030894264789592 s`，四臂均选 W。其候选尾部估计方向 E、egress peer=sat1，`remaining_hops=2`、`remaining_prop=0.00139295098597 s`；sat1 已在 PID1 的 packet path 中，但 `_candidate_resource_map` 的 lookahead 没有对 `pkt.path` 作过滤。实际 route 继续为 0→1→19→20。
  - 同一直接服务候选回退出现在 PID20001（North→equator，sat1 的 S→sat0）及 PID30001（North→Pacific，sat3 的 E→sat9）；四臂记录分别选 W 与 N。候选尾成本只含本地已知 ETA、单个 peer egress 广告队列工作和到最近可见 serving sat 的“剩余跳数×当前候选传播时延”代理，不是整条后续路径的队列/服务/下传成本。有限样本这些路由排序足以证明目的直达候选被评分回退并出现绕行；尚不能解释全网 0 交付、无决策的两个 OD 或控制安装/完整端到端服务。
  - 源码 `_precompute_build` 为各 target 生成有向 hop 表；`_precomputed_order` 在决策时用收到的目的 serving 广告选择 target，再把 `table[target]` 方向排在前面。按已记录状态，它会把 PID1 的 N→sat2 与 PID30001 的 E→sat9 置于首选方向，North→equator 的目标卫星方向同理。这是代码推断；本 pilot 的 precomputed cell 未运行，因此不是实测比较，更不保证交付。
- **最小修复建议（未实现）。** 为进入目的服务卫星的动作提供有限且明确的终端/下传服务项；信息未知要和不可行分开，不要仅因无 ISL peer-resource map 就把目的直达方向排在所有有限绕行之后。预测尾部遵守当前 `pkt.path`，不得预测回到已访问卫星；如主张端到端尾延迟，则需包括可观测的后续排队/服务与目的下传，否则缩窄为现有局部队列+传播代理。先用这两个实际审计状态做单元反例，再做固定几何、真实收到目的广告的内核交付测试；未来 VM 开跑前仍须完整全网通过原 admission/smoke 门槛，不能放宽。
- **区域几何。** 业务端点为合成走廊三聚合格：equator G1:90:180 (0.5,0.5)、North G1:142:270 (52.5,90.5)、Pacific G1:131:336 (41.5,156.5)，North–Pacific 约 4967.35 km。按同一 `Constellation(24,3,550km,53°,25°)` 对固定服务星 0/2/9 用 `next_gsl_change` 检查 [0,50] 无可见性切换，20 与 50 s 均可见；按运行所用 `routing.build_topology` 于 0,5,…,50 s 边界重算后，三端点对 ISL 最短跳数为 2/4/2；route audit 涉及的 sat 0/1/2/3/4/5/9/19/20 方向邻居映射在这些边界未变。描述为有限走廊，不称邻近 North 或本地城市范围。几何只证明服务星可见和离散拓扑结构条件，不证明控制广告/访问接入/包交付。
- **真实账本。** 新 run 4/4 调用结束，failed=0、timeout=0、unresolved=0，模拟内层 wall=66.970945898 s，VM 外层 elapsed=86 s。加旧 B 4 次/42.100891407 s，B 累计 8/60 次、109.071837305 s。A+B 实际保守记账为 A 外层 1061 s + B 内层 109.071837305 s =1170.071837305/7200 s（混合计费口径）。每臂 compute service=23.84299999998487 s、compute queue wait=134.06938052110257 s、query=23,843 次 / service=0.023842999980325352 s / queue wait=157.91238052105885 s、background=0 jobs。control ledger 通用合计为 offered 31.68 Mb、delivered 31.2 Mb、0.48 Mb still in system、0 terminal loss；它不能替代逐目的广告安装证据。
- **当前报告边界。** 没有完成配对表、竞争 seeds 11/23/42 完整性、方法收益排名、五模式完整总成本或 replay HTML；`replay=NOT_AVAILABLE`。未启动 formal、confirm seed 1001+ 或训练；未合并 main。之前 schema 修正/B 合同测试 2 passed、准入 4 passed 属于软件证据，不覆盖本次 VM smoke 失败。

---

## 历史状态（WP-A 与更早执行；后文身份不作为当前执行身份）

## 0. 身份

| 项 | 值 |
|---|---|
| 仓库根 | `独立研究工作树（路径由执行环境管理）` |
| 分支 | `t1/frozen-branch-and-async-design` |
| 起始 HEAD | `0876b12dbb0e067a840743dcc57b804d4600e9b0` |
| 复审轮次 | 53aeb30（R1–R9）→ 2330701（S1–S7）→ a124bba（第三轮：S1/S3/S5/S6/S8 返工）
  → b1f44af（第四轮：S1 B1/B2 回修）→ **06572b6 / f9f6c5c（第五轮：B1/B2 CONFIRMED_FIXED，S1_OPEN 关闭）** |
| 独立验收状态 | S1 已关闭；S3/S5 第三轮确认；**真实 DDQN 模型读取器未实现（内部缺口）**；**P9 PARTIAL（研究设计未就绪）** |
| 最新证据 | run-id `t1-final-06572b6`；HEAD `06572b6`；`identity.git.dirty=false`；执行链 `b7641418…` |
| 第三轮复审 | 四路只读验收回齐；S2/S7/S8(1)/证据链已确认；S1(A/D)、S4(d)、S5-R2、S6、S8(3a/i) 已返工；S1(B/C)、S3、S5 非 DDQN 标记仍未修（见 REPORT 末节） |
| VM | `ssh vm` = cuda-liguang13；隔离实验根 `T1 隔离工作根`；正式部署 `旧正式部署根` **从未写入** |
| VM runner | `CODE/scripts/remote/t1-vm.sh sync|run|pull|experiment` |
| 链一致性 | VM 链 `b7641418…` == 本机重算（57 个执行链文件；执行链已排除 ._ 元数据并远端剪枝） |

## 1. 阶段状态（含两轮复审纠正）

| 阶段 | 状态 | 说明 |
|---|---|---|
| P0–P3 | DONE | 基线/账本、单位与身份、时间事件与有限算力、快照/预测/评分 |
> 状态口径：**DONE = 代码已修 + 本机测试通过 + 在新身份上 VM 复跑通过，但第三轮独立复核尚未覆盖新身份**。
> 任一条目的历史身份：53aeb30（R1–R9）→ 2330701（S1–S7）→ a124bba（第三轮裁决）→ `b1f44af`（第三轮返工 + 新身份）。

| P4 四组理想诊断 | DONE（S1；第三轮返工：截断服务窗回退内核观测、未知不折数、控制包时间线） | 队列真值按内核事件语义重建 + 控制流量事件 |
| P5 四组可部署执行 | DONE | |
| P6 包粒度/基准/压力 | DONE（S5；第三轮返工：四臂对齐入门禁、模型来源标记） | 计时走真实在线入口；工件声明非 DDQN、非星载 |
| P7 异步更新器 | DONE（S3；第三轮返工：逐流缓存改请求时冻结） | 查表不付逐包计算；命中不因查询等待失效而变成未付费的完整评分 |
| P8 五执行模式/DDQN | DONE（S6 第三轮返工：推理边界改成"声明+探针"、策略统一委托 adapter.act、检查点 metadata 硬门槛） | 真实检查点：**模型读取器未实现**（内部缺口），不是仅缺 tensorflow/检查点 |
| P9 指标/统计/停止 | **PARTIAL（研究设计未就绪）** | 冻结 D、观察窗口删失已实现；但开发块配对差恒为 0 → `common_strong` 未冻结、**样本量无法估计**。这是**研究设计尚未就绪**，不等于"只差正式授权" |
| P10 t1_suite/恢复 | DONE（S4 第三轮返工：完整性失败也登记进 excluded） | 谓词失败即整轮失败；CLI 退出码 3 |
| P11 证据链/回归/待执行包 | DONE（S7 已确认修复） | acceptance/dev 在新身份跑通；formal 包可编译校验、拒绝未授权执行 |
| P12 最终交付 | DONE | REPORT 重写现状并标注历史；账本与 `still_open` 已对齐 |

## 2. 实测（本机=测试；实验=VM）

```
# 本机（允许：单元/回归测试）
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
1313 passed, 2 skipped, 1 warning in 483.15s
  （含三轮返工新增的反例文件：S1/S4/S5/S6 反例 + runner 清单反例）

# VM（实验唯一位置），隔离根 T1 隔离工作根
run-id: t1-final-06572b6   pulled -> out/vm/t1-final-06572b6/
t1_suite compile  -> 20 cells
t1_suite validate -> valid true（bundle_fingerprint 334dbbd2…）
t1_suite run --tier acceptance -> ok 10/10（谓词全过，not_ok=0，result_sha256 逐格核对）
t1_suite run --tier dev        -> ok 10/10
report -> run_status ok；common_strong.frozen = False（诚实：开发块配对差恒为 0）
身份一致性: commit=06572b6…, dirty=false, status_short=[]；VM 链 == 本机重算（b7641418…）；拉回 52 文件逐字节一致；平台 Linux-…aarch64
四臂对齐: targets_match/ranking_match 全 True
真实在线路径 p50 = 509.8 µs —— **确定性评分器的 VM 主机计时，非 DDQN、非星载**
  （工件 `model_provenance.trained_checkpoint_used=false` / `ddqn=false`；benchmark 谓词已强制该标记）
有限池: N=1 41 请求 23 排队 | N=2 47 请求 25 排队 | N=0 无界 0 排队
五模式: per_packet 41 计算请求；per_flow 34 请求 + 7 缓存命中；precomputed/async_point/async_window 各 0
```

## 3. 未做范围（非工程缺口）

1. FORMAL_RUN：确认性矩阵未执行（formal 包可生成/校验，运行需授权）。
2. 真实 DDQN 检查点：本机与 VM 均无 tensorflow 训练产物；接口与硬门槛已验证，不作策略结论。
3. 未写入 `旧正式部署根`，未做正式远端验收部署。
4. **研究设计尚未就绪（不只是"缺授权"）**：开发块配对差恒为 0 → `common_strong` 保持未冻结，
   正式样本量无法估计，formal 包因此标 `PENDING_DEV_SELECTION`。需先补有判别力的开发场景，
   才谈得上确认性运行；把它概括成"只差正式授权"是不准确的。


## 工作包 A 当前状态（2026-09-30）

- 整合提交 `156b422796c8ddb78fb04c85228b77cd969bf076` 和 scratch 修复提交
  `2107e405fd2026789b33e9faa91a485458e47417` 均推送到任务分支；后一提交已发布为不可变隔离 release，release 身份、源树、运行时锁和 pullback 回执均已核验。
- `wp-a-dev-20260930-01` 是实际模拟调用 0 的启动失败。`wp-a-dev-20260930-02` 是唯一新的仿真 run：13 格，5 `ok`、8 timeout，exit 3；运行耗时 1,061 s。静态调用上界 78/80，内部实际调用数未被单独记录。run receipt canonical SHA `d21e12e3…e82c8d4`，manifest SHA `58b2ff50…6713e0c9`，evidence URI `evidence://t1/wp-a-dev-20260930-02` 为 `VERIFIED`。不 resume、不重试超时格、不再扩展本包 VM 运行。
- 阴性对照 A0 smoke 通过：四臂各 31 offered / 31 satellite ingress / 28 delivered，62 次 forward decision；五模式负对照均 28/31 delivered、3 个 administratively censored。低负载结果不支持收益结论。
- 人工合成非对称多 OD 场景的 branch、network、benchmark 和部分模式格超时，结构准入与 H1 为 `NOT_COMPUTABLE`。五模式中只有 3 个 pressure cell 的外层状态为 `ok`，均 seed 7；预计算交付最高，异步 point/window 在所测格逐项相同。期限 D=30 主损失无法由精简结果输出重算；行政删失分别报告，不能用成功包时延替代总体主损失。
- 运行后发现四臂派生表将 admitted 记成 0；规范 `congestion_metrics` 对应值为 31。源代码已改为读取规范 satellite-ingress 计数并增加反例断言；该报告字段缺陷没有改变内核轨迹或 run02，修复代码未在 VM 重跑。新增只读重算脚本可校验 run/receipt/result 哈希并生成 35 行结果表、四臂校验表和 SVG；细节及边界见 `REPORT.md` 的本轮章节。
- 修复后的本机定向测试：`test_benchmark_decision.py` + `test_t1_tasks.py` 42 passed；两个 A0 smoke/预算测试 2 passed。一次较宽测试在 439.93 s 时中止，已有 76 passed，不是全量通过。更早 1,494 passed / 8 skipped / 1 warning 全仓库结果早于 TMPDIR 修复。
- `criteria.json`、本报告和派生证据保留超时、哈希、分母、删失与成本数据。confirm seeds、FORMAL_RUN 和训练均未执行；不声称科研确认、星载时延、能耗或预计算完整生命周期成本。若另开 B 包，须先由主控集中验收，并另行冻结期限指标可计算的设计和预算；本包不继续消耗 VM 额度。
- 独立只读审查以 d489 源码提交和 run02 精确身份核对了 61 个回执文件及派生结果，未发现哈希/算术不一致；单人审查兼顾实现证据与研究统计，非双人复核。判定规则有轻度措辞可操作性改进项，详见 `REPORT.md`。
