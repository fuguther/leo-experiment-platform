# 真实星座上的小规模无训练验证（2026-09-26，含一次撤回）

**基线**：提交 ~~a49ce82~~（代码身份 ~~2c17718d…~~）；本文记录在其上的诊断轮次。
**身份声明**：全部为**诊断**——未走授权、无回执、~~research_eligible~~ 为假、不在信任链内。
**不得升级为研究结论。**

> **本文撤回一个判断。** 上一版把 no_info 的默认原因命名为 ~~RANGE~~，并断言"服务目的地的卫星
> 就是更远"。**那是没有证据的归因**：观测只说明"没有覆盖本包目的地的广告到达过"，这与
> "还没生成 / 还在飞 / 被丢弃 / 途中过期"完全同形。该断言已撤回，见 §1。

---

## 1. ① no_info：默认原因改为"缺少目的地广告，原因未确定"

工具 ~~CODE/experiment_platform/no_info_diagnosis.py~~。分类只用**该次决策自己记录的观测**。

| 原因 | 观测层面可否判定 | 判据 |
|---|---|---|
| **NO_DESTINATION_ADVERTISEMENT**（默认） | **不能** | 缓存里有条目，没有一条宣称服务本包目的地。四种解释同形，**不下结论** |
| ABSENT | 能 | 缓存一条条目都没有 |
| EXPIRED | 能 | 到达过的条目在该时刻 ~~age_s > ttl_s~~ |
| COVERING_ENTRY_PRESENT | 能 | 有覆盖条目却仍报 no_info —— 矛盾，必须暴露 |

**首次覆盖改为按"卫星 + 目的 cell"统计**（~~first_covered_key = "sat|destination_cell"~~）：
一颗星可以对目的地 A 覆盖良好、对目的地 B 从未覆盖，只按卫星会把这两人平均成一个谁都不描述的数。

实测（控制面 ~~vis_k=2~~、~~ttl_s=10 s~~、~~advertise_interval_s=2 s~~）：

| 运行 | frozen 尝试 | no_info | 原因 | 从未覆盖的 (卫星\|cell) |
|---|---|---|---|---|
| smoke 基线 | 5 | 2 | ~~{NO_DESTINATION_ADVERTISEMENT: 2}~~ | — |
| **smoke 强制 S**（原失败案例） | 29 | 28 | ~~{NO_DESTINATION_ADVERTISEMENT: 28}~~ | ~~[7\|G1:142:270]~~ |
| scan 基线 | 41 | 2 | 同上 | — |
| scan 强制 N | 98 | 61 | 同上 | ~~[3\|…]~~ |

修正的一处分类器缺陷：目的地是**逐包**的。此前拿 trace 全局目的地集合比较，会把"覆盖别人目的地"
的条目误判为覆盖本包（曾产生 59 条假 ~~COVERING_ENTRY_PRESENT~~）。修正后上述四组无一条误判。

---

## 2. ② 隔离的事后全局审计：把默认原因拆成有名有姓的生命周期事件

新增 **opt-in 的控制面审计通道**（~~Kernel(control_audit=True)~~，默认关；只写 timeline，
决策一律不读它）。它对**同一 config + 同一 trace** 再跑一次，记录广告的
**生成 / 转发 / 到达 / 过期 / 跳数截断**。诊断工具因此不再被限制为只能看策略局部观测。

~~python3 -m CODE.experiment_platform.no_info_diagnosis --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml --forced-decision-id 2 --forced-action S --ctrl-audit --out out/no-info-forced-S-audited.json~~

原失败包（29 次尝试、28 次 no_info）的审计判定：

| 审计判定 | 次数 | 含义 |
|---|---|---|
| ~~AUDIT_HOP_LIMIT~~ | **26** | 含该目的地的广告**被生成、被转发到跳数上限**（vis_k=2）却始终没到达该星 |
| ~~AUDIT_GENERATED_AFTER_THE_DECISION~~ | 2 | 该目的地广告首次到达该星发生在决策之后 |
| ~~AUDIT_UNEXPLAINED~~ | 1 | 审计未能解释 —— **如实保留，不并入其它类别** |

审计还给出可核对的量：该目的地广告被生成多少次、首次生成时刻、全星座到达数、**最大到达跳数**、
本星到达数/首次到达时刻、跳数截断事件数。

---

## 3. ③ 两个单因素诊断（固定原业务 trace）与新增控制开销

工具 ~~CODE/experiment_platform/control_reach_probe.py~~。两臂都**复用基线的 trace 行**：
排空臂在 trace 编译并加载**之后**覆写 ~~scenario.duration_s~~（包集不变），
范围臂只改 ~~control_plane.vis_k~~；工具**断言三臂的 trace 摘要相同**，不同就拒绝。
**只用于辨别原因，不作为性能结果。**

~~python3 -m CODE.experiment_platform.control_reach_probe --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml --forced-decision-id 2 --forced-action S --drain-to 200 --vis-k 4 --out out/control-reach.json~~

| 臂 | 改变 | no_info | 送达 | 控制包注册 | 事件数 |
|---|---|---|---|---|---|
| baseline | — | 28 | 0 | 6,816 | 49,845 |
| **extended_drain** | ~~duration_s~~ 70 → 200 | **147**（更多） | **0** | 19,296（**2.83×**） | 143,310（2.88×） |
| **wider_reach** | ~~vis_k~~ 2 → 4 | **2** | **1** | 12,696（**1.86×**） | 72,622（1.46×） |

**判别结论（由干预而非断言得到）**：
- **时间不是原因**：把排空窗口从 70 s 拉到 200 s，包**仍然不送达**，no_info 反而从 28 涨到 147。
- **可达范围是原因**：把 ~~vis_k~~ 从 2 提到 4，no_info 从 28 降到 2，包**送达**。
- **代价一并记录**：范围臂的控制包注册量 **1.86×**、事件数 **1.46×**；排空臂花掉 2.83×/2.88× 却毫无收益。
  两臂的**物理容量、节点处理、输入业务均未改动**。

---

## 4. ④ 双可达人口扫描（结论不变，实测保留）

~~python3 -m CODE.experiment_platform.dual_reachable_scan --config CODE/leo_sim/profiles/t1_real_dual_scan.yaml --max-points 12 --out out/dual-scan.json~~
（场景 ~~t1_real_dual_scan.yaml~~ 与原 profile 只差 ~~demand.offered_mbps~~ 0.5→2.0，跑前声明；
选点规则 R1--R4 在 docstring 与产物的 ~~selection_rule~~ 里预先声明。）

13 包 / 26 forward 决策 / 39 已提交 / **13 个候选点**，执行 12（跳过 id=36）：

**双可达点 0 / 12 = 0.0%，双可达备选 0 / 12**，失败原因全部为 ~~FORCED_NOT_DELIVERED~~（12/12）。

**③ 的前置结论**：本配置下真实星座里**不存在可比较的代价差异**——备选方向一个都不送达。
先要解决的是**信息可达性**，不是预测精度。边界：一个配置一个种子，非统计样本。

---

## 5. ⑤ 身份、命令、产物、提交号

**命令**（全部在仓库根目录）：见 §1--§4 各节的命令行；配置为
~~CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml~~ 与 ~~CODE/leo_sim/profiles/t1_real_dual_scan.yaml~~。
**原始产物**（本机 ~~out/~~，未入库）：~~no-info-forced-S.json~~、~~no-info-forced-S-audited.json~~、
~~no-info-baseline.json~~、~~no-info-scan.json~~、~~no-info-scan-forced.json~~、
~~control-reach.json~~、~~dual-scan.json~~。

**身份**：诊断。本轮新增了 opt-in 审计通道，**因此 ~~CODE/leo_sim/*.py~~ 字节身份再次变更**；
新身份由 ~~git archive <commit> CODE/leo_sim~~ 重算给出（见汇报）。任何既有授权对新身份一律失效。
