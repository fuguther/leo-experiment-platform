# 场景标定 v3 / v4 本地实测（2026-10-20）

本目录保存 **2026-10-20 场景标定第二轮**的配置、运行身份与结论摘要。
它承接 `汇报材料/流量带宽与时间维度_关注清单_20261019.md` 的判据，目标是把压力场景的
时间尺度改到"传播主导、排队可见、预测跨度有意义"，并解决单 OD 对下链路利用率分布不真实的问题。

## 1. 代码身份

| 项 | 值 |
|---|---|
| 仓库 | `leo-experiment-platform`，分支 `research/scene-v3v4-20261020`（从 `main` 分出） |
| 基线提交 | `5458e57`（`origin/main`，2026-09-30） |
| `code_sha256` | `57cca0eda33994844cf072c91914545708b53837957d829af248ab9ad1a8b011` |
| 运行性质 | 本机诊断运行，`research_eligible = false`；不是 VM 正式实验，无授权链 |

`code_sha256` 覆盖 `CODE/leo_sim/*.py`；本分支未改任何 `leo_sim` 代码，只新增 profile 与文档。

## 2. 文件

- `CODE/leo_sim/profiles/t1_pressure_corridor_v3.yaml` —— v3 压力走廊基准（8 kbit / 10 Mbps / 1 Mbit 队列 / 0.2 s 广告 / 60 s）。
- `CODE/leo_sim/profiles/t1_grid_multiod_v4.yaml` —— v4 多 OD 对基准（24 星 / population_gravity / 24 Mbps）。
- `profiles/` —— 当天实际跑过的变体（与 /tmp 原件同名保存）：
  - `v3_15.30.yaml` / `v3_16.40.yaml` / `v3_20.40.yaml` / `v3_off16.yaml` / `v3_off20.yaml`：v3 负载/时长变体；
  - `v4_10.yaml`：24 星多 OD 对照（v4p 用的配置）；
  - `v4b.yaml`：**Kepler 140 星**（7×20, 600 km, 98.6°）+ 人口重力，40 Mbps；
  - `v4d.yaml`：同 v4b，`destination_population_exponent = 4`（更集中）；
  - `v4c.yaml`：同 v4b 但 `mode = hotspot`（**当天未运行**）。
- `runs/<run>/summary.json` —— 每个运行的 receipt/manifest/resolved_config 关键字段 + trace 哈希校验。
  完整 receipt / manifest / resolved_config / trace 在 `_archive/scene-v3v4-20261020/runs/`（本机）与原始 `/tmp/e0run-*`；
  数百 MB 的 `ledgers.json` 未入库（仿真可重跑）。
- `docs/` —— 当天使用的三份文档副本（v3 实测报告、关注清单 1019、参考项目设置清单 1019）。

## 3. 运行与结果（本机，seed 7，`/tmp/e0run-*`）

| run | 场景 | offered | delivered | in-system at stop | 丢失 | 说明 |
|---|---|---|---|---|---|---|
| e0run-v3 | v3 走廊，`max_isl_km=3000` | 90,118 | **0** | 16,003 | 74,115 HOLDING_QUEUE_OVERFLOW | 走廊被切断（不可达），已回退 6000 |
| e0run-v3b | v3 走廊，`max_isl_km=6000` | 90,118 | **89,986** | 132 | 0 | E0 四判据通过，端到端 p50 41.2 ms / p95 43.4 ms（见 v3 实测报告） |
| e0run-v4b | 140 星，人口重力，exp=1，40 Mbps | 60,193 | 23,309 | 36,884 | 0 | 12 s horizon 太短，未收尾，需调负载/时长 |
| e0run-v4d | 140 星，人口重力，exp=4，40 Mbps | 60,193 | 17,816 | 42,377 | 0 | 极化了目的地，仍大量 in-system |
| e0run-v4p | 24 星，人口重力，24 Mbps | 30,211 | 503 | 29,708 | 0 | 10 s 内几乎全部在系统内，未形成有效测量窗口 |
| e0run-off15 | v3 变体（15 Mbps） | 56,397 | — | — | — | **未收尾**（无 receipt） |
| e0run-off20 | v3 变体（20 Mbps） | 100,130 | — | — | — | **未收尾**（无 receipt） |

结论摘要（供下一步）：v3 时间尺度已自洽且实测端到端 41 ms；单 OD 对下 48 条 ISL 只有 4 条承载流量，
必须多 OD；v4 的 140 星人口重力运行尚未形成有意义的测量窗口（in-system 占比过高），
下一步应调整时长/负载/测量窗口后再比较队列分布，而不是直接下结论。

## 4. 复现

```bash
# 在仓库根目录（本分支）
python3 -B -m CODE.leo_sim run \
  --config CODE/leo_sim/profiles/t1_pressure_corridor_v3.yaml \
  --out <new-run-dir>
# 变体
python3 -B -m CODE.leo_sim run \
  --config ANALYSIS/SCENE-V3V4-20261020/profiles/v4b.yaml \
  --out <new-run-dir>
```

`--out` 必须是新目录；run-id 永不复用。运行回执为 `leo-sim-receipt/v5`（本批未开 decision/timeline 流）。

## 5. 边界

- 这是**本机诊断**，不是正式实验证据：无 VM 授权、无 research eligibility、单 seed。
- v4 的配置仍在调参阶段，结果不得当"多 OD 下排队机制已成立"的证据。
- 与 `codex/20261018-scene-calibration`（10-18/19 场景标定 + tie-break 干预）是**两代场景**：
  后者在 96 星 / 1 Gbps / 1500 B 场景族上工作，结论不可与本目录混统计。
- 完整仿真数据（ledgers）不在 git；本目录只保证"配置 + 身份 + 摘要"可追溯。
