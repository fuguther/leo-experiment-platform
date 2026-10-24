# 走廊四臂实验 · 结果摘要（2026-10-24 夜）

> 本文件是仓库侧的可引用摘要；完整分析/日志在 workspace `2026-10-24-corridor-20261024/`。
> 数据：VM `/data/论文/leo-t1-wt/scratch/corridor-20261024/runs/`（stageC_s7_*, stageD_s8..12_*, fresh_*）。
> 代码：该批 CODE 与 `5e52ba3` 的 `leo_sim`+`experiment_platform` 235/235 文件哈希一致；
> **run 自身未记录 commit**（`identity.git.available=false`）⇒ scratch 级证据，非 release 级。
> 配置：`CODE/leo_sim/profiles/corridor/` 下 `e2_6g_adv200.yaml`（及 per-seed 变体）。

## 1. 场景

140 星 7×20@600 km 98.6° / 6 城 gravity（tokyo/singapore/london/newyork/saopaulo/lagos）/
`max_isl_km=3000`（走廊）/ ISL `constant` 1 Gbps / `packet_bits=600000` / `isl_queue_bits=24000000` /
`advertise_interval_s=0.2` / `vis_k=3` / adv v2 / `predictor=bounded_linear` / `history_limit=8` /
`duration_s=1.2`，发射窗 0.10–1.00 s。

实测（stale 臂）：`isl_n=364`、`util_max=0.79`、`frac(util≥0.2)=29%`、lost=0、censor 0.56%、fallback≈51%。

## 2. 终点定义（预注册，数据前写死）

- 主终点：逐包 D4 未达标 pp，**D_frozen=0.1928 s**（stale 臂 p85；p92=0.21733 > `T−emission_end=0.20`，
  按预注册"降档"规则取可行最高分位）。population = `packet_outcomes.in_population=True` 全部包。
- 次要：同 pid 配对平均 e2e 时延、交付率、censor。
- 配对：同 seed 内同 pid；跨 seed 用 seed 级配对差与种子间 SD（**不用**池化逐包 SE）。

## 3. 结果（D=0.1928，逐 seed 配对差）

| seed | common−stale | candidate−stale | candidate−common | now−stale |
|---|---|---|---|---|
| 7 | −3.27 pp / −5.06 ms | −2.70 / −5.50 | +0.56 / −0.44 | +0.89 / +0.34 |
| 8 | +1.27 / +2.63 | +2.04 / +2.68 | +0.77 / +0.04 | +2.46 / +4.33 |
| 9 | +3.11 / +6.14 | +2.96 / +5.70 | `<TBD>` | +2.77 / +5.03 |
| 10 | +1.91 / +1.40 | +0.40 / +0.19 | +3.39 / −1.27 | +2.21 / +1.51 |
| 11 | −6.05 / −9.68 | −5.94 / −8.98 | +0.11 / +0.78 | −6.33 / −10.06 |
| 12 | −3.00 / −5.18 | −0.60 / −1.79 | +2.40 / +3.39 | −3.55 / −4.91 |

- **R1（common/candidate vs stale）：未建立（k=6）**：common−stale 均值 **−1.01 pp / −1.63 ms**（SD 3.61/5.95，MDE6 4.13/6.80，**同向 3/6**，CI 含 0）；
  candidate−stale −0.64 pp / −1.28 ms（SD 3.27/5.37，3/6）。**SD > 均值、MDE > 均值 ⇒ 功效不足**（检出 1/2/3 pp 需 ~87/22/10 seeds）。
- **now−stale：未建立**（k=4 曾 4/4 正，seed 11 反转后 k=6 = 2/6、−0.26 pp、SD 3.79）。
- **R2（candidate−common）：复现的 null（唯一稳健）**：+0.36 pp / +0.34 ms，CI 含 0，3/6 同向、SD 1.28 pp/1.64 ms；
  38.0% 查询时刻逐位相同；query 时刻差 p50 = 0.000 ms。
- **oracle（ideal_now）k=4 也未建立**：−12.59/+2.44/−3.09/−12.80 ms ⇒ 均值 −6.51 ms、SD 7.49、CI [−18.4,+5.4]、3/4 同向。
- **within-run 预测层判死**：now/common/candidate 的外推相对 hold_last 差 +11.0%/+15.5%/+19.4%（CI 不含 0）；
  离线 hold_last 在 100–200 ms 尺度最优 ⇒ SPEC §2C "无可用增量"。
- **H_load（offered 9000，seeds 21–23）：批次未完成**。首轮 `xargs -P 5` × ~15.4 GB/run ⇒ 容器 64 GiB OOM
  （`oom_kill 110`、3 run rc=137、0 产物）；幸存者（s21 stale/common、s22 stale）在 swap 下 55 min+ 未收尾；
  补跑降为 `-P 2`（s22_common、s23_stale）。**最终最多 k=2，按方向/描述写，不执行 3/3 PASS/FAIL**。
  单 run 内存/并发上限见 `workspace 平台发现-20261024.md` P-8 更新。
- **MDE 更正**：6-seed MDE 用**种子间 SD**（pp 3.8–4.3、ms 6.1–6.8）；早前 0.465/0.653 是逐包 SE 下界，作废。

## 4. 机制结论

外推到"当前时刻"（now）稳定有害；到"消费时刻"（common/candidate）方向随 trace 反转；
同一微观机制（翻转率 11.77%/11.48%、每翻转 d_prop 中位 +6.738 ms）产生相反宏观符号 ⇒ 单 trace 不可外推。
预测层（bounded_linear/ou_shrink）相对 hold_last 显著更差（+11.0%/+15.5%/+19.4%，CI 不含 0）；
离线验证 hold_last 在 100–200 ms 尺度最优 ⇒ SPEC §2C "预测层无可用增量"。

## 5. 复现

```bash
D=/data/论文/leo-t1-wt/scratch/corridor-20261024
PY=/data/论文/leo-t1-wt/envs/t1-linux-aarch64-py311-pkgset-v1/bin/python
ssh vm "cd $D && $PY -m CODE.experiment_platform.t1_tasks --task network_alignment \
  --config profiles/e2_6g_adv200.yaml --arms stale --deadline-s 0.1928 --out runs/repro_stale.json"
# 汇总：workspace `2026-10-24-corridor-20261024/scratch/final_analysis.py --D 0.1928 'runs/stage*_s*_*-summary.json'`
```
