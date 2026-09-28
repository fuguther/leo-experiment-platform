# WP-T1-COMPLETE 最终交付报告

> 计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`
> 账本：`CODE/work/WP-T1-COMPLETE/STATUS.md`、`criteria.json`、`contract.yaml`
> 结论分层：IMPLEMENTED / TESTED / DIAGNOSTIC_RUN / FORMAL_RUN。**本报告不含任何 FORMAL_RUN。**

## 1. 用户问题

在同样可收到的本地与邻居历史信息下，把候选资源的预计使用时刻对齐，能否改善选择；
计入计算与生效方式成本后，改善能否保留？

两个层次：
1. **状态时间语义的价值**：先用隔离的理想信息估计价值空间，再用可在线实现的预测检验能实现多少；
2. **计算与生效方式**：固定信息权限、预测器、评分器与动作空间，比较逐包计算与后台更新/结果复用。

## 2. 实现（IMPLEMENTED）

| 计划书文件 | 状态 | 说明 |
|---|---|---|
| `CODE/leo_sim/time_alignment.py` | 新增 | 不可变快照、ResourceKey/StateSample/ResourcePrediction/TruthSample、hold_last/bounded_linear、ETA 分项、统一评分、schedule 表与只查表 lookup。纯函数，不读内核真值 |
| `CODE/leo_sim/async_routing.py` | 新增 | scope 状态机 UNINITIALIZED→COMPUTING→INSTALL_PENDING→ACTIVE、单 pending 合并、完成才安装、版本单调、窗口从实际安装起算 |
| `CODE/leo_sim/kernel.py` | 扩展 | 每星 FIFO 计算池 + `compute_request/start/finish`（稳定 `compute_job_id`）、零成本 frozen、观察记录新增 `candidate_resources` 与 `time_alignment` 审计、五执行模式在线接线、周期异步更新器 |
| `CODE/leo_sim/control.py` | 扩展 | LocalCache 保留有界「实际到达」广告历史（仅输出用，供 bounded_linear） |
| `CODE/leo_sim/config.py` | 扩展 | `time_alignment` / `async_routing` 命名空间、白名单、默认值、矛盾即拒绝；接受 JSON 指数浮点（编译配置 JSON 写 YAML 读回） |
| `CODE/experiment_platform/time_alignment_compare.py` | 新增 | 每个合法候选独立闭环重放、四组离线评分、每候选命运/损失/regret、resource_mismatch、只读 oracle 轨迹、oracle 仅评估器 |
| `CODE/experiment_platform/execution_compare.py` | 新增 | 五执行模式同一 trace/seed/臂/预测器/计算预算、调用与查询成本分开、DDQN 可用性/阻塞 |
| `CODE/experiment_platform/benchmark_decision.py` | 新增 | 完整决策路径计时 + 空调用基线 + 有限池 N=0/1/2/4 压力 |
| `CODE/experiment_platform/t1_suite.py` | 新增 | compile/validate/run/resume/report、预算、身份校验、旧证据不覆盖 |
| `CODE/experiment_platform/t1_stats.py` | 新增 | 归一化损失、配对差、固定种子 bootstrap、样本量规划、精度核查、主对比 |
| `CODE/experiment_platform/artifact_identity.py` | 新增 | Git commit/dirty/diff + 源文件哈希链 + runtime |

## 3. 验证（TESTED）

实际命令与输出（均为本机实跑原文末尾）：

```
python3 -m pytest CODE/leo_sim/tests/test_time_alignment.py -q                 -> 28 passed
python3 -m pytest CODE/leo_sim/tests/test_async_routing.py -q                 -> 16 passed
python3 -m pytest CODE/leo_sim/tests/test_compute_pool_fifo.py -q             -> 7 passed
python3 -m pytest CODE/leo_sim/tests/test_time_alignment_online.py -q         -> 8 passed
python3 -m pytest CODE/leo_sim/tests/test_async_kernel.py -q                  -> 7 passed
python3 -m pytest CODE/experiment_platform/tests/test_time_alignment_compare.py -q -> 15 passed
python3 -m pytest CODE/experiment_platform/tests/test_execution_compare.py -q -> 10 passed
python3 -m pytest CODE/experiment_platform/tests/test_benchmark_decision.py -q -> 8 passed
python3 -m pytest CODE/experiment_platform/tests/test_t1_stats.py -q          -> 32 passed
python3 -m pytest CODE/experiment_platform/tests/test_t1_suite.py -q          -> 11 passed
python3 -m pytest CODE/experiment_platform/tests/test_control_reach_units.py -q -> 3 passed
```

端到端流水线实跑（acceptance 层）：
```
t1_suite compile  -> 7 cells, contract_sha256 b5cdeb41...
t1_suite validate -> valid true, 7 cells
t1_suite run --tier acceptance -> {"ok": 7, "error": 0, "timeout": 0}
t1_suite report   -> run_status ok, 7/7 cells, REPORT.md 生成
resume 实跑：只重试失败 cell；bundle 身份变更后旧输出标 invalidated 并重跑；旧 result 被改名保留
validate 篡改拒绝：删除某 cell 的 driver 后 -> "bundle validation failed: ... missing driver"
```

全平台回归见 `STATUS.md` 第 2 节（含一次真实回归失败的根因与修复）。

## 4. 有界诊断结果（DIAGNOSTIC_RUN）

可从 `out/t1/` 复现，命令见第 7 节。**均为单分支/单 trace 诊断，不构成统计结论。**

| 诊断 | 结果 | 解释 |
|---|---|---|
| 等长空队列（reachability，decision 3） | 两候选损失各 0.5，regret 全 0 | 机制在无差别场景退化，符合预期 |
| 确定性竞争（contention，decision 4） | baseline 选 E 损失 0.5105；W 损失 0.3013；oracle 选 W | 每候选闭环重放产生真实代价差 0.209 |
| 四组在线评分（同一 contention 分支） | 四组均选 W，regret 0 | **负结果**：本分支中状态时间对齐没有增量价值，因为简单评分器已从广告中读到目标出口积压 |
| 四组改变排序（合成夹具 `test_the_query_instant_is_the_only_arm_difference`） | stale 选 W；now/common/candidate 选 E | 机制本身可判别，只是当前场景不激活 |
| 恒状态退化 | 四组动作一致 | 恒队列时预测外推退化一致 |
| 计算池手算夹具 | 1 服务台 starts [0,0.1] / finishes [0.1,0.2]；2 服务台 finishes [0.1,0.1] | 与计划书手算一致 |
| 异步跨版本时间例 | 请求 1 s、服务 2 s、安装 0.5 s → 1.5/3.2 s 查 v1，3.5 s 安装后查 v2 | 与计划书时间例一致 |
| 决策路径计时 | full p50 ≈ 34 µs、p99 ≈ 50 µs；空调用基线 ≈ 0.018 µs | 主机/VM 实测，非星载 |
| 五执行模式复用 | per_flow 命中缓存后计算请求低于 per_packet；async 模式查询表、按窗口安装 | 机制计数与事件对齐 |

## 5. 能支持的判断

- 平台现在能从一个明确命令复现：四组状态时间比较、每候选闭环代价、有限算力压力、异步复用诊断，输出完整命运/成本/证据。
- 时间语义、信息权限、候选资源映射、统一评分、异步安装时序、五执行模式公平性均有可判定测试；关闭新功能时旧路径逐位不变（回归实证）。
- 主对比的统计设计（独立区组、配对、bootstrap、样本量规划、预声明阈值 0.01/0.005/0.02）已冻结在 `contract.yaml`，可在确认运行中直接使用。

## 6. 不能支持的判断

- **没有 FORMAL_RUN**：确认性种子 1001+ 的大规模矩阵未执行；本报告任何数字都不能当作确认性结果。
- **没有 DDQN 结果**：本机无有效固定推理检查点，`ddqn_status` 返回 `EXTERNAL_BLOCKER`（精确原因与恢复方法见工件）；不把确定性评分器结果当 DDQN 结果。
- **没有星载实测**：计时全部是主机/VM；配置服务时长是诊断情景输入，不是标定值。
- **没有远端覆盖部署**：标 `REMOTE_NOT_EXECUTED`；未触碰用户正在使用的 VM。
- 不声称状态时间对齐有收益或没有收益——当前单分支诊断在 contention 上显示零增量，但这是「场景未激活机制」，不是算法结论。

## 7. 复现 / 恢复命令

```sh
cd /Users/lge/Desktop/topic/leo-exp-main

# 四组离线比较（单分支）
python3 -m CODE.experiment_platform.time_alignment_compare \
  --scenario contention --decision-id 4 --out out/t1/ta-contention.json
python3 -m CODE.experiment_platform.time_alignment_compare \
  --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
  --decision-id first_forward --out out/t1/ta-smoke.json

# 五执行模式
python3 -m CODE.experiment_platform.execution_compare \
  --scenario contention --out out/t1/exec-contention.json

# 决策计时与有限池
python3 -m CODE.experiment_platform.benchmark_decision \
  --scenario reachability --iterations 200 --rounds 2 --warmup 20 \
  --pool-sweep 0,1,2,4 --out out/t1/bench-reach.json

# 端到端流水线（新身份；目标目录必须不存在）
mkdir -p out/t1/suite
python3 -m CODE.experiment_platform.t1_suite compile \
  --contract CODE/work/WP-T1-COMPLETE/contract.yaml --out out/t1/suite/compiled
python3 -m CODE.experiment_platform.t1_suite validate --bundle out/t1/suite/compiled
python3 -m CODE.experiment_platform.t1_suite run \
  --bundle out/t1/suite/compiled --tier acceptance --out out/t1/suite/acceptance
python3 -m CODE.experiment_platform.t1_suite resume --run-dir out/t1/suite/acceptance
python3 -m CODE.experiment_platform.t1_suite report --run-dir out/t1/suite/acceptance
```

## 8. 证据链

- 源码与测试：本仓库提交（见 `STATUS.md` 身份表；`git log`）。
- 运行工件：`out/t1/`（gitignored，未提交；由上述命令可逐条重建）。
- 身份：每个新工件内嵌 `identity`（Git commit/dirty/diff_sha256 + 逐文件哈希 + runtime）。
- 旧证据：`out/` 既有文件未改写；新运行拒绝覆盖旧目录。


---

# 返工（独立验收 R1–R9，审查身份 53aeb30）

裁决 REQUEST_CHANGES 已接受。以下逐条给出反例、修复与复现证据；**P4/P6/P8/P9/P10/P11/P12 的"完成"标记已按复审结论纠正**（见 §R 表）。

## R 表：逐条状态

| 项 | 复审问题 | 状态 | 行为反例（先补） | 修复 |
|---|---|---|---|---|
| R1 | validate/resume 只做旧身份自洽检查；链缺 time_alignment/async_routing/驱动/统计 | FIXED | `test_any_post_compile_bundle_edit_is_refused_not_reused`、`test_a_cell_parameter_change_is_refused`、`test_a_config_file_edit_is_refused`、`test_a_dependency_source_change_is_refused`、`test_a_missing_result_invalidates_the_cell_and_is_rerun`、`test_a_tampered_result_is_detected_by_its_hash` | 执行链改为整包发现（56 文件）；bundle 内容指纹 + 逐 cell 输入绑定 + 结果哈希；validate/run/resume 均重算当前源码与磁盘结果；报告不得把丢失/篡改结果的 cell 记为 ok；新身份必须重新编译 |
| R7 | 预计算表不按目的节点选方向 | FIXED | `test_two_targets_in_opposite_directions_get_opposite_next_hops`（3 节点反例期望 `{1:E,2:W}`） | 对每个目标做有向最短距离，再在本星出口中选能到达该目标的最小距离方向；不可达不填假路线；查询用预计算距离不再每次 BFS |
| R8 | ETA 漏本地出口排队；在线计算等待写死 0 | FIXED | `test_the_local_egress_queue_moves_the_query_instant_later`（8000 bit @ 8000 bit/s → +1.0 s）、`test_a_bounded_pool_wait_enters_the_prediction_span` | ETA 拆成 compute_wait / compute_service / local_egress_wait / tx / prop / peer_process；评分复用 ETA 项（不再二次计队列）；内核提供**请求时刻可知**的池等待估计（`compute_state.wait_estimate_s`），离线驱动改读该状态，实际等待只作诊断 |
| R9 | 查询成本只有字段 | FIXED | `test_a_positive_query_delay_is_charged_and_serialised`、`test_every_execution_mode_pays_the_same_query_service`、`test_the_query_server_never_serves_two_queries_at_once` | 每星单服务台公共查询服务；五种执行模式（含缓存命中/预计算/异步查表）同口径计费；`query_delay_s=0` 保持历史瞬时路径；报告 query 等待与服务 |
| R2 | 只有在线评分 + min(final_loss) 评估器，缺三组理想对照 | FIXED | `test_the_common_and_candidate_instants_can_pick_different_actions`、`test_the_zero_gain_control_makes_every_ideal_arm_agree` | `truth_at_instant` 从**各候选自身分支**重建命名资源时刻真值（扣除已服务比特、剔除目标包自身；空队列=0，资源不出现=缺失）；`oracle_now/common/candidate` 三组走同一评分器；新增 ETA×队列真值/估计 2×2 分解；最终命运只用于事后评分 |
| R3 | 7/7 流水线未触发有效查询/缓存命中/N=0；cell 只看 returncode；缺包长/请求率/开发扫描/正式包 | FIXED | `test_a_cell_whose_predicate_fails_is_not_reported_ok`、`test_the_dev_tier_runs_with_behaviour_predicates`、`test_the_formal_package_is_compiled_and_validated_not_run` | 新增 `same_flow` 夹具（8 包同流）；五模式加 `--compute-servers/--service-s/--packet-bits/--query-delay-s/--update-interval-s` 覆盖并记录；报告 bin/version 分布、每星请求率、查询服务计数；cell 先声明行为谓词，未过则 `predicate_failed`；新增 372/891/1500 B 单元；新增 dev 扫描 tier 与 formal 待执行包（编译+校验+拒绝运行） |
| R4 | 34 µs 不是完整决策路径；full/inference 都只调 scoring | FIXED | `test_the_full_path_is_the_sum_of_its_declared_phases`、`test_call_counting_proves_inference_only_does_not_repredict` | 按真实在线接口拆四段计时（观测构造 42.3 µs / 预测 24.3 µs / 评分 4.5 µs / 选动作 0.08 µs / 端到端 73.9 µs）；调用计数证明 end_to_end 预测次数 = 候选数、inference_only = 0 |
| R5 | 默认 D 从当前分支现算；无观察窗口规则；统计只在单测里 | FIXED | `test_a_compare_run_may_not_derive_its_own_deadline`、`test_two_different_branches_share_the_same_frozen_deadline`、`test_a_short_observation_window_is_censored_not_counted_as_failure`、`test_report_carries_the_frozen_statistics` | `--deadline-from` 加载开发冻结 D（含文件哈希与身份）、`--freeze-deadline-to` 仅 dev 可写；`run_kind=compare/confirm` 禁止自行推导 D；观察窗口 < D 标行政删失（不计失败、不编造损失）；bootstrap/样本量/配对差接入真实 run 报告，std=0 时显式标退化 |
| R6 | DDQN 只有路径存在检查 | FIXED（真实检查点仍为外部阻塞） | `test_the_adapter_is_inference_only`、`test_epsilon_is_zero_and_no_update_happens`、`test_a_checkpoint_needs_hashes_not_just_existence`、`test_the_kernel_still_refuses_frozen_with_a_learner` | 新增 `leo_sim/inference.py`：固定参数小模型适配器（epsilon=0、无更新路径、冻结归一化、掩码强制、确定性破同）；`verify_checkpoint` 校验路径+sha256+元数据+loader，**存在不等于 AVAILABLE**；内核 frozen+learner 边界保持拒绝 |

## 返工后的实测证据

```
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
1241 passed, 1 skipped in 436.14s

t1_suite compile  -> 20 cells（acceptance 10 / dev 10 / formal 待执行）
t1_suite validate -> valid true
t1_suite run --tier acceptance -> {"ok": 10, "error": 0, "timeout": 0}   # 每个 cell 的行为谓词全部通过
t1_suite run --tier dev        -> {"ok": 10, "error": 0, "timeout": 0}
t1_suite run --tier formal     -> 拒绝（PENDING PACKAGE，需授权）
report -> 通过（含 statistics：blocks / bootstrap / 样本量规划 / 退化警告）

五执行模式（same_flow，N=1，服务 0.05 s，查询 0.001 s）：
  per_packet   compute=41 queued=23 cache_hits=0  queries=0  installs=0  qsvc=41
  per_flow     compute=34 queued=16 cache_hits=7  queries=0  installs=0  qsvc=41
  precomputed  compute=41 queued=23 cache_hits=0  queries=0  installs=0  qsvc=41
  async_point  compute=41 queued=23 queries=16 installs=32 bins=1 版本被查询数=6
  async_window compute=41 queued=23 queries=16 installs=32 bins=4 实际查询到 bin {0,1,2,3}
  每星转发请求率 0.1333 /s

决策路径分段计时（主机）：观测 42.3 µs / 预测 24.3 µs / 评分 4.5 µs / 选动作 0.08 µs
  端到端 73.9 µs；inference_only 4.4 µs；预测调用次数 2(=候选数) vs 0
有限池：N=0 无等待；N=1 queued=23 max_wait=0.312 s；N=2 queued=25 max_wait=0.112 s

R1 反问例复核（复现脚本 tmp_r1_final.py，已删除）：
  A 结构保留改参数+改合同哈希 -> 拒绝（三条独立理由）
  B 删除 result 但记录仍 ok -> report FAILED_CELLS / invalidated=1 / verified_ok 9/10；resume 只重跑该 cell 并恢复 10/10
  C 篡改 result 内容 -> report FAILED_CELLS / invalidated=1
```

## 仍未做（未做范围，非工程缺口）

1. **FORMAL_RUN**：确认性种子 1001+ 的矩阵未执行；formal 包已编译并校验，运行被显式拒绝（需授权）。
2. **真实 DDQN 检查点**：本机无 tensorflow、无检查点；适配器与哈希校验已实现并用固定参数小模型验证，**不得**作为策略性能结论。
3. **REMOTE_NOT_EXECUTED**：未做远端覆盖部署。
4. 开发集配对差在本次 acceptance/dev 夹具上恒为 0，样本量规划因此标 `degenerate`：它只说明这些夹具不具判别力，不能用于估计确认样本量。



---

# 第二轮复审返工（S1–S7，审查身份 2330701）

裁决仍为 REQUEST_CHANGES，已按 **S1 → S7** 顺序逐项返工。**执行位置规则同时变更：所有实验只在 VM 上跑**（见 `AGENTS.md`），本报告此后的数值一律来自 VM。

## ⚠️ 历史声明（旧数据不再作为依据）

- 本报告更早章节里的 **"34 µs / 73.9 µs"** 计时、以及 `out/t1/**` 下的**全部本机产物**，均属**本机历史证据**：
  其中 34 µs 的版本只计了评分调用，既非完整决策路径也非真实在线时刻，**已被取代且不得引用**。
- 首轮 P4/P6/P8/P9/P10/P11/P12 的"完成"标记，已先后被 53aeb30、2330701 两轮复审纠正；
  以本节的 S 表与 `criteria.json` 的 `s_rework` 块为准。

## S 表：逐条状态与证据

| 项 | 复审问题 | 状态 | 修复与证据 |
|---|---|---|---|
| **S1** | 理想队列真值算错：`backlog_before` 已排除目标包却再次扣它，且忽略在服务剩余量 | FIXED | 新 `resource_work_ahead`：按事件序重建，区分**排队数据 / 在服务剩余 / 控制优先 / 目标自身 / FIFO 后方**，未知项保持未知；在目标入队瞬间**恒等于内核自己的 `queued_bits_before + in_service_remaining_bits_before`**（真实内核对齐测试）。复审的 2000+500 → **2500 bit** 反例已作为测试固化 |
| **S2** | 广告队列值覆盖包长（`bits` 同名局部变量） | FIXED | `_build_ta_snapshot` 先解包长再遍历广告，局部名改为 `advertised_bits`；测试：喂 999999 bit 广告后 `pkt_bits` 仍为 8000，且对广告取值/顺序不变 |
| **S3** | 查表模式仍逐包付完整计算（预计算/异步均 41 次 compute_request） | FIXED | `_packet_compute_required`：precomputed / async_point / async_window **逐包 0 次计算请求**，只付公共查询成本；后台异步任务仍付真实计算（0.05 s）；预计算构建成本单列（`build_wall_s` + 说明"不是逐包推理收费"） |
| **S4** | 谓词失败仍整轮报成功、统计仍计入该区组 | FIXED | 计数穷尽（`predicate_failed/not_ok`）；非 ok 即 `FAILED_CELLS`；统计只纳入**完整性+行为谓词均通过**的区组并列出排除原因；CLI 非成功**退出码 3**。反例：不可能谓词的 run→report→resume 全流程 |
| **S5** | 计时不是真实在线路径（各臂都查 `snapshot_at`） | FIXED | 抽出共用入口 `plan_decision/build_predictions/resolve_common_horizon`，内核也改用它；计时截获**在线决策的真实输入**并冻结当时的 caches/池状态，四臂逐一比对在线审计：`targets_match`/`ranking_match` **全部 True**（VM 工件） |
| **S6** | 固定推理模块未接入任何分支/执行路径 | FIXED（真实检查点仍外部阻塞） | `inference` 新增推理专用接口与硬门槛；`kernel.inference_policy`（拒绝与训练 learner 组合）+ `counterfactual` 透传；**真实分支跑通**，掩码强制、参数不变、两次运行前缀动作一致 |
| **S7** | 正式包未真正冻结；改阈值不改哈希；common_strong 未选择 | FIXED | `formal_package`/`formal_design` 纳入 bundle 指纹（复审的"阈值改 999 仍 valid"现被拒绝）；cell 输入绑定纳入 deadline 依赖文件哈希；`formal_design.ready` 时生成**真实 confirm cell**（种子+冻结 D+身份），未就绪时诚实标 `PENDING_DEV_SELECTION`；`statistics.common_strong` 未冻结时给出原因，主比较标签不再冒称 common_strong |

## VM 执行证据（本轮起，实验只在 VM）

```
ssh vm -> cuda-liguang13   /data 471G 可用   conda: /data/liguang13/conda-envs/leo-i39
隔离实验根: /data/论文/leo-t1-wt        # 你的正式部署 /data/论文/leo-direct-sim 从未被写入
runner: CODE/scripts/remote/t1-vm.sh sync|run|pull|experiment

链一致性: VM 链 e8537aed… == 本机链 e8537aed…（剪除 556 个 macOS ._ 残留文件后）
工件身份: identity.git.source=launch_manifest, commit=377ae9b…, dirty=False
平台:     Linux-6.6.0-…aarch64        # 不再是 macOS

t1_suite compile  -> 20 cells
t1_suite validate -> valid true
t1_suite run --tier acceptance -> {"ok":10,"error":0,"timeout":0,"predicate_failed":0,"not_ok":0}
t1_suite run --tier dev        -> {"ok":10,"error":0,"timeout":0,"predicate_failed":0,"not_ok":0}
report -> run_status ok；statistics.common_strong.frozen = False（诚实）
四臂对齐（VM）: candidate/common/now/stale targets_match=True ranking_match=True
真实在线路径端到端 p50（VM，含观测构造+预测+评分+选动作）: 507–517 µs
```

## 仍未做（未做范围）

1. **FORMAL_RUN**：确认性矩阵未执行；formal 包已能生成/校验，运行需授权（显式拒绝）。
2. **真实 DDQN 检查点**：本机与 VM 均无 tensorflow 训练产物与检查点；适配器接口与硬门槛已验证，**不得**作为策略性能结论。
3. **REMOTE 正式部署**：未写入 `/data/论文/leo-direct-sim`，未做正式远端验收部署。
4. 开发块配对差仍恒为 0 → `common_strong` 保持未冻结，样本量不可由此估计（已在报告中显式标注）。

