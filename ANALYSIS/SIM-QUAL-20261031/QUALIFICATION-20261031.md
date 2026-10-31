# QUALIFICATION-20261031 — 仿真器资格考试 A–F（事件级）

> 代码身份：worktree `.wt-qual-20261031`，branch `qual/sim-qual-20261031`，base commit `a90c1f9`
> （= `night/time-exp-20261031` HEAD）。本报告记录**修复前**的机制审计与**修复后**的资格考试结果。
> 测试文件：`CODE/leo_sim/tests/test_qualification_20261031.py`（15 个 test，144 条断言）。
> 原始事件证据：`ANALYSIS/SIM-QUAL-20261031/qualification-evidence/*.json`（schema
> `leo-sim-qualification-case/v1`，含参数、原始事件列表、逐条 expected/actual/pass）。
> 复跑：`python3 -m pytest CODE/leo_sim/tests/test_qualification_20261031.py -q` → **15 passed**。

## 0. 结论

| 项 | 结果 |
|---|---|
| Test A 链路 serialization | **PASS** |
| Test B transmission / propagation 分离 | **PASS** |
| Test C control 真实占带宽 + 非抢占优先 | **PASS** |
| Test D 基本排队理论（λ<μ / ≈μ / >μ） | **PASS** |
| Test E 状态消息时间链（received_at / AoI / TTL / 拥塞） | **PASS** |
| Test F 计算时间推进时钟 + frozen/refresh + commit 重验 | **PASS** |
| 发现真实仿真器缺陷 | **0**（未发现；不进入“修仿真器”分支） |
| 修复的测试缺陷 | 1（既有红点 `test_four_direction_audit.py::test_queue_state_trace_contains_exact_resource_counts`，见 §7） |
| 断言非空转验证 | 对 5 类注入错误分别红 6/1/3/9/6 个测试（§8） |

> 资格结论：**在当前代码版本下，仿真器能够按真实事件序列产生“逐包转发—排队—链路占用—
> 传播—状态同步—路由决策”这条机制链**。因此后续策略实验的机制前提成立；
> 这不证明场景现实性或研究结论，只证明平台机制不是空壳。

## 1. 方法

- 全部使用 `kernel.run_simulation` 驱动**真实内核**，fixture 为 `tests/helpers.py` 的
  `StaticGeometry` / `make_cfg` / `row`（确定性脚本化几何、显式链路参数）。
- 断言对象是**不可变输出里的原始时间戳与顺序**：`link_service_windows`、`packet_events`、
  `service_log`、`timeline`、`decision_sink`、`caches`、`queue_area_bits_s`；
  不使用“平均时延变化”作为机制证据。
- 每个场景把参数、原始事件序列、逐条断言写入 `qualification-evidence/` 的 JSON；
  同一场景两次运行证据字节级一致（确定性）。

## 2. Test A — 链路 serialization（PASS）

理论：单方向 ISL 一次只服务一个包；`T_tx = S/R`。

实测（S=8e6 bit，R=10 Mbps）：包 1 服务窗口
`[0.010001384571188912, 0.810001384571189]`，`T_tx=0.8`（与 `S/R` 差 <1e-12）；
包 2 不早于包 1 完成，且**恰在包 1 完成时刻**开始 `[0.810001384571189, 1.6100013845711891]`。
从 `packet_events` 重建队列水平序列 `S→0→S→0`，重建面积 `6336000.0` 与
`queue_area_bits_s["isl_data"]` 完全一致；另测 (2e6 bit, 8 Mbps)=0.25 s、
(4e6 bit, 20 Mbps)=0.2 s 两点均满足 `T_tx=S/R`。
**不是“第二个包稍后发送”的宽松断言，而是完成时刻的精确交接。**

## 3. Test B — transmission 与 propagation 分离（PASS）

理论：`t_arrival = t_service_start + T_tx + d/c`；传播中的包不占用发送链路。

实测（d=1000 km）：`t_arrival − (t_start + T_tx + T_prop) = 0.0`（浮点残差 0）；
`T_prop=0.0033356409519815205 = 1000/299792.458`；d=2000 km 时 `T_prop` 精确翻倍
（证明传播用**实际链路距离**而不是常数）。包 1 传输完成后、仍在传播时，包 2 的
`service_start` 已开始（0.810001384571189 < 包 1 arrival 0.8133370255231704）⇒
传播不占用发送资源；下一跳首次读到该包的决策时刻恰等于 arrival（无提前读取）。

## 4. Test C — control 是否真实占带宽（PASS）

理论：control 与 data 共用同一 ISL 服务；非抢占优先；不免费、不抢断、不被 data 跳过。

实测：`data_1` 窗口 `[4.010001384571189, 12.01000138457119]` 完整 8 s，
窗口内**没有任何 ctrl service**（未抢占）；t=5.0 生成的 control 在 data_1 发送期入队
（`backlog_before.in_service_remaining_bits=7010001.38`），data_1 完成后**立刻**服务
control（12.01000138457119），每个 control 传输 `0.008 s = 8000 bit / 1 Mbps`；
早于 control 排队的 `data_2` 只能在 8 个 control（seq 11..25）之后于
12.074001384571183 开始 ⇒ 原始事件顺序 `data_1 → ctrl×8 → data_2`。
广播频率对比：interval 1.0→0.25 时，control 包数 30→120、服务字节 480k→1.92M bit（4.0×），
**控制广播频率提高确实增加链路服务工作量**。

## 5. Test D — 基本排队理论（PASS）

理论：`λ<μ` 队列有界；`λ>μ` 队列持续增长且斜率 ≈ `λ−μ`（bit/s）。

实测（单链路 S=8 Mbit，μ=1 Mbps）：
- λ=0.5 pkt/s（ρ=0.4）：队列上界 8 Mbit、回 0、拟合斜率 0；
- λ=1.2（ρ=0.96）：上界 8 Mbit、斜率 0（有限缓冲内稳态）；
- λ=2.0（ρ=1.6）：拟合斜率 **6,003,066.68 bit/s**，理论 `λS−μ = 6,000,000 bit/s`，
  相对误差 **5.1e-4**；队列 5 s 时 24 Mbit → 停止时 208 Mbit；5 s 窗口均值单调递增。
重建队列面积与 native `isl_data` 面积完全一致。
⇒ 以后讨论 `r=μ−λ` 与 `ΔQ≈(δλ−r)W` 时，底层队列实现不是错的。

## 6. Test E — 状态消息时间链（PASS）

实测：广告 `t_m=2.0`，control 8000 bit / 1 Mbps tx 0.008 s，prop 0.0033356 s ⇒
`t_rx=2.0113356409519816`。
- `t=2.003009` 的决策只产生 `no_info/hold`（**t<t_rx 绝对读不到**）；t_rx 前无任何 commit；
- t=2.1 起 forward，cache age `=0.10000000000000053 = t−t_m`（AoI 定义精确）；
- 每个决策使用的 cache `received_at <= t`（不读未来状态）；
- 停止时 AoI=1.0（=t−t_m）；TTL=0.5 时 2.4 s 包可用并 forward，2.6 s 包只产生
  no_info hold、无 forward（TTL 过期后不可用）；
- 同一 t_m=3.0 的广告：空闲 received_at=3.0113356，拥塞下 received_at=5.2163370
  = data_end(5.2050014)+0.008+prop（差 2.2050014 s 恰为被占用的 data 服务时间）
  ⇒ **控制拥塞真实增加 received_at**。

## 7. Test F — 决策计算时间真的推进模拟时钟（PASS）

- compute=0：所有决策 `t == t_decision_start`；
- compute=2.0：`commit − start = 2.0`；compute 窗口内仍有 `ctrl_advert_arrived`、
  `service_start/service_finish`、`queue_enter`、`peer_arrival` 与另一包的决策
  ⇒ **网络事件在计算期间继续发生**；
- frozen：`observation_at_start.t_observed == t_decision_start`，
  `source=frozen_snapshot_before_compute`；refresh：`t_observed == commit`，
  `source=commit_time_state`；
- frozen 在 start 合法、commit 非法：写 `commit_rejected`（`reason=action_no_longer_legal`，
  `inferred_at=开始时刻`），不产生 decision 行、包进入 hold/parked，
  **不偷偷用未来状态重新优化**；同一 fixture 的 refresh 会重新求解（无 rejected 概念）。
- 反向 fixture：frozen 在 t0 推断 hold 并在 commit 落 hold，不会抢跑 forward；
  之后 t=7.1 才用真正新鲜的 observation forward；refresh 在 7.010001 直接 forward
  （start=5.010001 < flip=6.0 < commit）——两模式恰在该点分歧。

## 8. 断言不是空转（mutation check）

在临时副本上分别注入 5 类错误并重跑本套件：propagation 置 0 → 6 个测试红；
`CacheEntry.valid_at` 恒真 → 1 个红；抹掉 decision 的 `t_decision_start` → 3 个红；
ISL 速率 ×2 → 9 个红；control 不入队/不占服务 → 6 个红。
（注入只发生在临时副本，仓库未被修改。）

## 9. 交叉审计结论（独立于资格测试）

- `AUDIT-MECHANISMS.md`（只读源码审计）：14 项机制 **PASS 11 / WEAK 3 / GAP 0**；
  3 个 WEAK（传播距离、非抢占、AoI 时间链）正是本套件新补的事件级锁定。
- `AUDIT-EXISTING-TESTS.md`（实际运行既有测试）：既有 9 个相关文件 129 passed；
  A COVERED、B WEAK、C COVERED、D MISSING、E WEAK、F COVERED；本套件补齐 D 与 B/E 缺口。
- 本套件与既有 6 个相关套件合跑 **88 passed**。

## 10. 修复：既有红点（测试缺陷，不是仿真器缺陷）

`CODE/leo_sim/tests/test_four_direction_audit.py::test_queue_state_trace_contains_exact_resource_counts`
在 base `a90c1f9` 上是红的：测试用 `object.__new__(Kernel)` 构造半成品实例，只设置
`timeline_sink/env`，而 `_record_isl_queue_state` 自 resource journal 加入后还会读
`self.resource_journal`；真实 run 由 `Kernel.__init__` 把它设为 `None`，故真实仿真不受影响，
但该测试通道**没有通过的测试**。修复=在 fixture 里补 `kernel.resource_journal = None`
（不删断言、不放宽断言），修复后 2 passed。

## 11. 遗留限制（不得过度解读）

1. 资格考试用脚本化 `StaticGeometry`（固定拓扑/距离/可见性），证明的是**机制实现正确**，
   不是真实星座统计性质；真实 Walker 几何在 `test_model.py` 与后续全网络 run 中覆盖。
2. D 的稳定性测试用确定性恒定输入，验证的是实现与理论式一致，不是随机到达的统计检验。
3. 资格测试不覆盖 GE 随机中断、动态拓扑重匹配、MCS 速率变化；这些不属于本轮 A–F 判据，
   且在 pilot profile 里被显式关闭（GE off、static topology、constant rate）。
4. 资格通过 ≠ 研究结论成立：它只回答“机制是否真实”，
   stale-state 的严重性必须由后续 `RESULTS-20261031.md` 的预注册实验回答。

## 12. 资格审计中发现的平台建模限制（不属于 A–F 失败，但必须记录）

**控制广播使用"逻辑拓扑"，未按物理可用性过滤。** 140/7 场景的静态拓扑含 560 个有向方向槽
（140 × 4），其中 **364 个可用、196 个超过 `max_isl_km=3000 km`**（t=0 实查）。路由在合法性
过滤里会剔除不可用方向（data 不会走），但控制面 `control_broadcast_children` 按逻辑 topo 建树，
控制包被真实 enqueue 到**不可用链路**的 ctrl 队列上：这些链路永远不服务，包在队列里等到 TTL 过期。

实测（sq-p1-high-* 与本地 4 s 诊断）：

| 观察 | 数值 | 来源 |
|---|---|---|
| 控制实例注册 / 过期 / 停止时仍在系统 | 44,240 / 6,556 (14.8%) / 5,924 | 20 s run control ledger |
| `isl_ctrl` 队列面积（20 s） | 848 Mbit·s | result.queue_area_bits_s |
| 可用链路 vs 不可用链路的 ctrl 队列面积（4 s 诊断） | **0.0 vs 54.6 Mbit·s** | 本地诊断：`available_now()` 分组统计 |
| 可用链路上控制"生成→到达"年龄 p50 | 7.0 ms（≈ 1 跳传播） | decisions.used_state 分解 |

影响判定：
1. **信息面不受影响**：只有可用链路上的控制包能到达；不可用链路上的包本来就无法到达，
   因此"实际到达的广告"与 availability-aware 实现相同（缓存覆盖率 99.9%+）。
2. **AoI 不受影响**：本矩阵 AoI p50 487 ms（adv=1.0 s）与 56 ms（adv=0.1 s）主要由广告周期
   与传播决定，不是控制拥塞。
3. **控制面开销指标被高估**：`control.bits.offered` 与 `isl_ctrl` 队列面积含不可用链路的死信，
   **不得**把它们当作真实控制拥塞证据；本轮报告控制开销时同时给出该解释。
4. **策略比较不受影响**：四策略共享同一控制面与同一拓扑，死信行为相同。
5. 修复建议（下一轮，不在本实验中途改）：广播树只沿 `geometry.isl_available()` 为真的方向
   扩散；对不可用方向不入队；并加回归测试断言"不可用链路的 ctrl 队列在稳态为 0"。
   本轮不修，是为了保持已冻结矩阵与 ta-pilot 历史结果的可比性。
