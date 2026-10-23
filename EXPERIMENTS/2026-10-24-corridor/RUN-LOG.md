# 走廊场景 Stage A 标定批次（corridor-20261024）

> 性质：**scratch 探针批次**，不是正式证据；可用于设计下一阶段，引用须标注"scratch/非 commit 重放"。
> 运行位置：VM `cuda-liguang13:/data/论文/leo-t1-wt/scratch/corridor-20261024/`。
> 起因：`2026-10-24-scenario-hunt/候选与理论.md` §3.4 证明 H4∧H5 只能靠拓扑形状（剪枝成走廊）；
> 唯一一次走廊尝试 `K1d` 在 **730 s 被提前 kill**，而可比的 `K2d` 用 **826 s 正常完成**
> （证据 `tonight-20261024/logs/done.tsv`）⇒ "K1 路径病态"是假阴性，走廊需要重开。

## 0. 代码身份

- 运行代码：VM 树 `scratch/corridor-20261024/CODE`（由 `scratch/metrics-20261024/CODE` 整树复制）。
- **对账结论**：该树 `CODE/leo_sim` + `CODE/experiment_platform` 的 235 个 `.py` 与 commit **`5e52ba3`**
  （分支 `feat/metrics-truth-20261024`，main `a0904ad` 的后代）**逐文件 SHA-256 全一致，0 不一致、0 缺失**
  （对账记录：工作区 `2026-10-24-metrics-reconcile/对账-PR18指标扩展-20261024.md`）。
- 本仓库 commit 只承载 **profile 配置**（`CODE/leo_sim/profiles/corridor/`）；运行时以 config_sha256 记录配置身份。

## 1. 共同参数

140 星 7×20@600 km 98.6° / 6 城 gravity（tokyo, singapore, london, newyork, saopaulo, lagos）/
`max_isl_km=3000` / ISL `constant` 1000 Mbps / `packet_bits=600000` / `advertise_interval_s=0.02` /
`vis_k=3` / adv protocol v2 / `predictor=bounded_linear` / `history_limit=8` /
单臂 `stale` / `--deadline-s 0.15` / `--resource-journal --resource-journal-stride 10` /
`T1_AUDIT_MAX_DECISIONS=5000`。

## 2. 变体与结果

| run | offered (Mbps) | B (Mbit) | mode | hist | T (s) | 发射窗 | 墙钟 | 状态 | util_max | frac≥0.2 | loss | censor |
|---|---:|---:|---|---:|---:|---|---:|---|---:|---:|---:|---:|
| c1_6g_5g_b24 | 5000 | 24 | gravity | 8 | 1.2 | 0.10–1.00 | — | 运行中 | — | — | — | — |
| c2_6g_7g_b24 | 7000 | 24 | gravity | 8 | 1.2 | 0.10–1.00 | — | 运行中 | — | — | — | — |
| c3_6g_9g_b24 | 9000 | 24 | gravity | 8 | 1.2 | 0.10–1.00 | — | 运行中 | — | — | — | — |
| c4_6g_7g_b36 | 7000 | 36 | gravity | 8 | 1.2 | 0.10–1.00 | — | 运行中 | — | — | — | — |
| c5_6u_7g_b24 | 7000 | 24 | uniform | 8 | 1.2 | 0.10–1.00 | — | 运行中 | — | — | — | — |
| c6_6g_7g_b24h16 | 7000 | 24 | gravity | 16 | 1.2 | 0.10–1.00 | — | 运行中 | — | — | — | — |

（表在批次结束后由 Lead 填 `—` 处并写判停结论。）

## 3. 前置 smoke（已完成）

`smoke2_c6_g_6g`：T=0.4 / W=0.1 / offered 6000 / B=24 → 1000 包、100 s、isl_n=364、util_max 0.46、
frac(util≥0.2)=13.2%、e2e mean 88.7 ms / p95 143 ms、**reordered 81.0%**、fallback 44.5%。
证明：走廊拓扑可跑通、机制确实会重排候选；K1d 的"路径病态"不成立。

## 4. E 批次与 Stage C（04:28–05:25）

- **E 批次（adv 杠杆，单臂 stale）**：`e1_6g_adv100` / `e2_6g_adv200` / `e3_72g_adv100` / `e4_72g_adv200`
  （其余同上；`advertise_interval_s`=0.1/0.2，offered 6/7.2 Gbps）。全部跑完模拟段（journal 完整），
  但 **summary/outcome 后处理超线性（10–20 min 未收尾）被主动 kill**；结论只取 journal（T2 已用完整 journal 算 |K|）。
- **Stage C（四臂 + oracle，seed 7）**：profile `e2_6g_adv200.yaml`（6 Gbps, adv=0.2, B24, T1.2/W0.9）；
  arms = stale/now/common/candidate + `ideal_now`（`o1_ideal_now_6g_adv200.yaml`, `state_source=ideal_current`）；
  均带 `--target-probe`（stride50/max8000/horizons 0.005,0.01,0.02,0.05），audit cap 2000（keyed），无 journal。
  manifest：VM `runs/stageC_s7-manifest.json`。**单 seed，只作机制判读，不作正式结论。**
