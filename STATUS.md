# 本工作树状态：场景与负载校准 + 2026-10-19 夜间场景族

范围：仅 `worktrees/.t1-scene-calibration` / `codex/20261018-scene-calibration`；工作区级入口仍是 `topic/STATUS.md`。

## 当前分支状态（`qual/sim-qual-20261031`，2026-11-01 凌晨；本 worktree）

- 本轮两条线的入口都在 `ANALYSIS/SIM-QUAL-20261031/`：
  - **Round-1（pre-fix）**：`QUALIFICATION-20261031.md`（A–F 资格，15 tests/144 断言全绿）、
    `PROTOCOL-20261031.md`、`RESULTS-20261031.md`、`CONCLUSION-20261031.md`。
    注意：pre-fix 网络数字已被 control dead-letter 修复推翻（见下），只作历史。
  - **Round-2（post-fix）**：`calibration/{CALIB-20261031.md,CONTROL-FIX-COMPARISON.md}`、
    `PROTOCOL-ROUND2-20261031.md`、`RESULTS-ROUND2-20261031.md`、`CONCLUSION-ROUND2-20261031.md`、
    `persistence/`、`round2-results/`、`fork-samples/`、`fork-configs/`（在 EXPERIMENTS/contracts/）。
- 关键发布与运行：
  - control 修复 `fb90c01`（release `fb90c01…-96da8e3f…`）：死信 eliminated（expired 0，
    isl_ctrl area −99.99%），delivered ads/AoI 不变；**data plane 改变**（+2 hops、+35–43 ms），
    pre/post 不可混。pre-fix release `76427851…` 保留。
  - Round-2 矩阵 release `038fb16…-b8eb375…`（B/C/D/sync）、`4e7a7b5…-aac7223…`（A）、
    fork release `de632b3…-76a4127…`。
  - 证据根 `三端文件与同步治理/evidence/t1/`：`sq4-*`（矩阵）、`sq3-*`（校准/A）、
    `sq2-*`（校准/pre-post 对照）、`fk-c-*`（same-continuation forks）。
- 结论（详见 `CONCLUSION-ROUND2-20261031.md`）：**SHRINK** —— stale state 是动作层现象
  （flip 26–28%、Γ 单调），但在实测 A/B/C/D、多 seed、20 ms 同步下没有性能层转化；
  AoI 510→16.6 ms 后 flip 只降 1.8–3.7 pp、E2E 反而变差；网络性能由策略类别决定。
- 尚未关闭：完整 fork 汇总（n=12 冻结中）、根 STATUS/现态 Issue 的更新、pre-fix 历史的同 release 重跑。

## 历史分支状态（`night/time-exp-20261031`，2026-10-31）

- 时间语义 / 状态年龄线的最新入口在 `ANALYSIS/TIME-ALIGNMENT-20261030/`：
  `PILOT-20261031.md`（设计与起跑前就绪证据）、`PILOT-20261031-RESULTS.md`（第一批机制结果）、
  `ta_pilot_analyze.py`（复算脚本）、`pilot-results/*.json`（机器可读摘要）。
- 本轮 code commit `fd1cb141048efd806f9b7dd242e315aa0e557e23`；release
  `fd1cb141…-06cd728e…`；运行回执与索引见 `ANALYSIS/DEPLOYMENT-INDEX.jsonl` 的 `ta-pilot-*` 行。
- 以下历史状态块保留原日期与身份，不代表当前分支的状态。

## 2026-10-19 深夜轮（接手执行：探针 + 人口轨迹）

**已交付**
- **同一次运行内的预测—真值探针**（`CODE/leo_sim/target_probe.py`，默认关闭，契约 `diagnostics.truth_probe`）。
  在决策提交后按预测自己的 `resource_id`+generation 定位资源，于 `eval_target` 时刻由事件调度唤醒读取
  `queued(data+ctrl) + 在服剩余`；缺失全记原因。核验脚本 `probe_labels.py`（覆盖/一致性/对应/误差/样本分离）。
- **人口业务轨迹**：`population_od_program.py` 产出人口加权 93 OD、149 格、8 477 子格位置的
  `s1_stationary` 外生 trace（5 827 包、逐字节可复现、格心落点 0），并给出 149 格接入映射 CSV。

**实跑（release `2c4f4161…` commit `2c4f4161`，远端备份 pending）**
- `scpt-sc-probe-truth-s111-20261019-17`（rc=0，870 s）：47 755 行探针，覆盖 98.45%，
  sampled/resource/generation/eta 对应**全 0 不一致**，predicted/rate 与评分一致 0/47 014；
  `hold_last` 带符号误差 **−489.0 bit**（n=47 014），标签非零 4.55% 而预测非零 2.01%。
- **探针不干扰**：run 16（stride=1 截断 4 万）与 run 17（stride=4 全窗）业务结果**逐位相同**
  （7 212 包 / 188 NO_ROUTE / 7 024 送达 / 队列面积一致）。
- `scpp-pop-probe-r020-s111-20261019-18`（rc=0，807 s）：**接入主导** ——
  access 等待 268.5 s、最长 3.90 s、`IN_SYSTEM_AT_STOP 1 397/5 827 = 24%`，
  holding 面积 7.94e7 vs isl_data 2.58e4 bit·s。

**判定**
- 探针（交付一）**已通过**既定核验；其 4 万行首版因上限造成时间偏样本，已作废并改为确定性 1/stride 抽样。
- 人口场景（交付二）**trace/映射/生成核验通过**，但**“接入等待不主导”验收未通过** ⇒
  **不启动 S/O/N 与人口性能结论**，先解除接入瓶颈。
- 预算：本轮内核启动 **3 次**（41→43/60），余 **17**。（一次人口发射被 batch runner 以“另一批在跑”拒绝，未启内核、不计费。）

**证据**：`三端文件与同步治理/evidence/t1probe-20261019/<run-id>/`；
结论文档 `CODE/work/WP-T1-SCENE-CAL/PROBE-RESULTS-20261019.md`、`PREDICTION-PROBE.md`、`POPULATION-TRACE.md`。

## 2026-10-19 夜间轮（最新）

**第 41 轮（用户新指令：回到人口网格业务主场景）**
- **已停止全部在跑格**（SIGTERM，日志与 run 目录保留，无 summary，run-id 不复用）。
- **入口已核实**：分支 `codex/20261018-scene-calibration`，HEAD `dabcee0`，工作树干净、全部推送；
  人口栅格 `CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif`
  （GPW v4 rev11 2020，15 弧分，sha256 `c5742d16…`）；加载器实测：**2° 聚合得 4 938 个格，
  总人口 7 969 444 350**，全部非零，可直接做人口加权抽样。
- **缺口（按优先级）**：
  G1 人口加权 OD 采样链路缺失（现有 scope_specs 的 B/C/D/E 是手选网关列表，只有 A 用了区域人口）；
  G2 §四 要求的预测记录字段大部分没有；G3 主场景此前是三城市 6 OD（降为诊断证据）；
  G4 近等分修复 `tie_sig_digits` 是可选开关，**主比较必须统一冻结版本**，副作用格未完成即被停；
  G5 **预算硬限制**：内核启动已用 40/60，余 20，而任务书要求 探针 1–3 + 核心 18 + 预测闭环，远超余额。
- **下一步**：先补 G1（人口→OD→trace），再做 1 格探针，然后按余额决定矩阵规模。

**第三十七轮补充（核心结果定稿 + 副作用格已发）**：
- **三 seed 配对复现完成**（§12.47）：基线 186/194/198（均值 192.7，极差 6%）→ 干预 **0/1/0**。
  每格 all-NO_ROUTE 与 OD 失败数完全相等 ⇒ **没有把失败转移到别的 OD**。
- **副作用格已发** `scdt-20261019-15`（release `76e5403`）：分散 OD（56 对）+ `tie_sig_digits=9`，
  与现成的 `scds` 基线直接配对——那里有 **4 个失败 OD 与 52 个健康 OD**，
  一次回答两个方向：**失败是否也被消除 + 健康 OD 是否被影响**。32 413 包，约 3.3 h。
- 预算：真实内核启动 **40/60**，余 20。

**第三十六轮补充（给唯一正效应做配对复现）**：
- §12.44 的 194→1 是**全研究唯一的正效应**，且**只有单 seed**。已发射配对复现：
  seed 112 与 113，**每个 seed 各跑"原样"与"量化"两格**（否则 seed 效应会与干预效应混淆），
  共 4 格、每格约 21.8k 包。新 release `0eb4fd6`、tag `20261019-14`、run-prefix `screp`。
- 判据：**同 OD 的 NO_ROUTE，干预 vs 自己那一格的基线**，逐 seed 比较；**正负都报**。
  在 112/113 上若为零效应，说明 seed-111 的结果**不可复现**，那也是必须报告的结论。
- 预算：真实内核启动 **39/60**，余 21。

**第三十五轮补充（A 格跑完，但暴露了我的设计错误；已更正并重发）**：
- `sctc-20261019-12`（corridor 版 A 格）**成功跑完**（rc=0，1360 s，与基线 1374 s 几乎相同）。
- **但它测错了场景**：corridor 的 OD 是 **北京→伦敦**，而 3.7% 的失败属于**北京→纽约**，
  后者只存在于 **trunk** 的 OD 集合里；corridor 的 `NO_ROUTE` 按构造就是 **0 / 14 423**。
  ⇒ 那一格**不可能显示任何东西**。我在引用基线时把两个场景混了。**如实记录。**
- 已更正：新契约 `scene_tiebreak_trunk_20261019.json` = **trunk 格 `sc-s1-r050-w035-s111` 原样 +
  只加一个字段 `tie_sig_digits: 9`**（同 trace program、同 seed、同 OD 权重、同窗、同臂），
  21 618 包。已发 `sctd-20261019-13`，12:44 起在跑。
- 目标 OD 北京→纽约，基线 **194/5 236 = 3.7%**（该格窗口下 2.7%），真值臂 0.3%。

**第三十四轮补充（tie-break 干预 A 格：一次 fail-closed + 修复 + 已重发）**：
- 首次发射 `sctb-20261019-11` **5 秒内 rc=2**：`unknown field time_alignment.tie_sig_digits`。
  根因：我只把键加进**合同允许集**，漏了 **`config.py` 的每分组严格 schema**。
  **未消耗预算**（`kernel_call_count = 0`，护栏按设计工作：未声明的键到不了内核）。
- 修复：在 `config.py` 的 `time_alignment` schema 与默认值各加 `tie_sig_digits`（都是 None）。
  **107 个本地测试通过**。
- 已重发：`sctc-20261019-12`（新 release `3a53588`、新 tag、新 run-id），
  12:20 起在跑（config + decisions 已落盘）。
- 判据不变：该 OD 的 `NO_ROUTE` 与基线 **194/5 236 = 3.7%** 对比，**正负都报**。

**第三十三轮补充（收尾检查，2026-10-19）**：
- 分支 `codex/20261018-scene-calibration`，HEAD `fdddaf2`，**工作区干净、无未推送提交**。
- **改动范围自查**：最近 25 个提交只触及 `CODE/work/WP-T1-SCENE-CAL/*`（本任务工作包）与
  本工作树的 `STATUS.md`，**未触碰其他任务的文件**。
- 证据入口：`INDEPENDENT-REVIEW.md`（2293 行，46 小节 + 完整索引）；
  图 12 张在 `review-20261017/figures/`；分析脚本全部随 commit 入库、可复算。
- `kernel.py` **只读未改**（tie-break 与 N 两处改动均未实施，等授权）。
- 运行侧：`scds` + `scw1c` 4 格仍在排空/仿真，无 `summary.json`。

**当前处境（第 40 轮会话交接点）**：
- **分析侧已做完**：机制链闭合到代码行、对账表更新、十类图 7/10 完整（12 张）、
  测量仪器量程校验（`hold_last` 已吃掉 99.85% 可压缩量）、交接清单（§12.37）、
  文档自检（索引 45 条 ↔ 45 小节，零缺失）。
- **唯一剩余是小时级运行**：`scds`（分散 OD）+ `scw1c` 4 格（高载、1.20 s 窗、缓冲 5/100 ms），
  11:46 仍在推进（129 MB / 65 MB×3），预计还需约 6 h。
- **两处待授权**：tie-break 因果实验（`kernel.py:5075` 排序键）、N 对照（`kernel.py:5172` 策略钩子）。
- **下一步第一件事**：等这 5 格出 `summary.json` 后按 §12.37 的收法整合，更新 §12.29 对账 #3/#4，
  并补第 3 类图的 1.20 s 窗口点。**在此之前不要发射新格。**

**第三十一轮补充（交接清单）**：
- 新增 §12.37：四个未完成项（多 seed / GRU-TCN-TAP-DAR / tie-break 因果 / verified pull）
  各给出**确切入口与最小可复算命令**，不需重读全篇。
- 关键交接口：离线预测器接入点 = `offline_eval.py` 的 `evaluate(methods=...)`；
  tie-break 因果入口 = `kernel.py:5075`（排序）+ `kernel.py:6640`（`legal[0]`），
  基线对照 `sc-cor-r050-stale-s111`（3.7%）。
- 同时列明**明确不做且已给理由的三条**（trunk 加 seed、加重负载、更强预测器），避免接手者重复劳动。

**第三十轮补充（自纠 + 恢复被误杀的维度）**：
- **撤回 §12.36 的"归约卡死"判定**：decisions 文件停止增长**是设计使然**（发射窗止于 t=2.70 s），
  不是故障；旁证是 tkfx-05（43.8 k 包）文件 230 MB、r095 格 460 MB，**恰好 2 倍**；
  墙钟与包数成比例（43.8 k = 2.55 h ⇒ 82 k ≈ 5–7 h），这些格跑 5.6 h 属"接近未完成"，不是卡死。
  同批 `scds` 落盘正常增长，说明落盘机制无问题。
- **我因此误杀了 5 个 trunk 高载格**（唯一能提供"高载"维度的格），如实记录。
  更正后的结论：**高载不是"跑不出来"，而是要 6–7 小时**。
- **已恢复**：以 `scw1c` / tag `20261019-10` 重跑那 4 个格（高载、1.20 s 窗第三点、
  缓冲 5/100 ms 两档），**新 run-id、旧 release、同一声明输入**，后台运行。
- 预算：本任务真实内核启动约 **32/60**，余约 28。

**第二十九轮补充（归约瓶颈系统性复现，已终止 6 格）**：
- wave1b 的 6 个 trunk 高载格（82 k 包）：decisions 落盘停在 07:12–07:19，此后 4 小时零写入；
  进程仍在，wchar 冻结在 409–473 MB；无 summary。**与 tkfx-05 签名完全一致**
  ⇒ **系统性瓶颈**，量级约 80 k 包 / 460 MB decisions。
- 已 SIGTERM 协作终止（日志与 run 目录保留、run-id 不复用、调用数已计预算），释放 6 核给 scds。
  **未定位到具体函数**（明确未完成项）。
- **对目标的影响**：任务书要求的"高载"在当前实现下拿不到结果；corridor 高载能完成是因量级低一档。

**第二十八轮补充（P0/F/A 判据执行：零差异）**：
- `sc-blin-r050-p0fa-s111`（corridor ρ=0.50，0.35 s 窗，预测器 **`bounded_linear`**）完成：
  三臂 capped loss **全部 = 0.06156324637109916**、未按期全 0、包数 14 423 全同；
  **决策差异 common vs candidate = 0/63 642，now vs candidate = 0/63 642**。
- 且与同场景 `stale` 臂的值相同 ⇒ **corridor 上 S = P0 = F = A，一次决策都没变。**
- ⇒ 用户 §四 的 F/A 问题有直接答案：**A 相对 P0/F 无可测增量**；
  且判据是在换成外推预测器之后执行的，不是"预测器不够强"。
- 与 §12.20 离线结论（三者误差 0.89–1.17 倍）**两条独立路径给出同一结论**。
- 机制解释：corridor 后填充窗队列仅 3.04–11.80 ms，不足以改变排序；失败由入口并列 + 防环决定。

**第二十七轮补充（只读定位，§12.34 的待确认项落定）**：
- `kernel.py:5075 _time_aligned_order` 文档字符串即 "Reorder the legal forward candidates with the shared scorer"；
  决策审计的 `applied_order` 实测为 `["E","W","N","S"]` 而分数 `E=W=0.0614 < N=S=0.1465`
  ⇒ **该列表就是按分数升序**；`kernel.py:6640` 取 `legal[0]`，不做二次比较。
- ⇒ 决策确实取评分最小值；**两候选只差 59 ULP 时排序由舍入决定**，
  与"同卫星同时刻不同包选反向"完全吻合。**"浮点噪声决定路由"由推测升级为代码事实。**
- 措辞更正：`legal[0]` 不是"任意列表序"，而是"评分序第一名"；问题在于**比较的量在镜像方向上数学相等**。
- 性质：**可修复的实现缺陷**（用与目的在服集合有关的确定性规则打破并列），不是物理结论。
  **用户确认前不改评分代码。**

**第二十六轮补充（并列的性质：59 ULP 浮点噪声）**：
- 数值分析：W 与 E 的分数相差 **59.0 ULP（相对 6.67e-15）**——数学上相等的镜像方向，
  差异是**纯累加顺序噪声**。⇒ **决定走东还是走西的是浮点舍入**，不是任何与目的在服集合有关的量。
- 行为佐证：同一卫星、同一时刻（sat 91, t=2.1035），不同包选了相反方向；
  若并列有确定性打破规则，不应如此。
- 性质判断：这是**实现缺陷级**发现，不是物理机制级；解释了失败 OD 固定、
  以及 `ideal_current` 只能把 194 降到 15 而非 0。
- **未做到**：**未定位到消费该分数的 argmin 那一行**（`kernel.py:6432` 是跳数过滤器，不是分数比较）。
  ⇒ "浮点噪声决定路由"目前**证据来自数值与行为，不来自代码定位**，标为待确认。
- 升级路径：① 只读定位 argmin 确认无确定性 tie-break；② tie-break 对照格（单格）。

**第二十五轮补充（包级一一对应）**：
- 失败 OD（北京→纽约，5 236 包）按包拆开：**DELIVERED 5 042 包 / 30 948 决策 / 1 848 次精确并列（0.37 次每包）**；
  **NO_ROUTE 194 包 / 11 640 决策 / 194 次精确并列（1.00 次每包）**。
- ⇒ **194 个失败包 ⇔ 194 次并列，一一对应**；且失败包的并列率（占决策）反而更低（1.67% vs 5.97%），
  因为它们在 64 跳里做了 60 次决策——**并列只发生在入口那一段**。
- 机制三段齐备：① 入口精确并列（触发）→ ② 防环规则使错误不可撤销（放大）→ ③ 撞 64 跳上限（结果）。
- **仍不宣称因果**：成功包也遇并列（0.37 次/包）却不失败 ⇒ 并列必要但不充分；
  要升级为因果仍需 tie-break 对照格。

**第二十四轮补充（入口精确并列，相关性已测/因果未证）**：
- sat 91 t=2.1035：E 与 W 的分数**只在第 15 位有效数字上不同**（0.061401828952806735 vs ...744），
  同一时刻不同包选了相反方向；且 pid 7 与成功包选**同一方向仍失败** ⇒ 分岔不止在入口。
- 全量：有 ≥2 合法候选的 118 055 条决策中，**top-2 精确并列 2 865 = 2.43%**，
  差 <1e-5 s 的 4 027 = **3.41%**；差 p50 = 3.9e-2 s。
- **诚实边界**：失败 OD 的失败率 3.7% 与并列率量级相近，但**只有相关性、没有对照**，
  **本条不给因果结论**。仍成立的核心是 §12.31：错误分支在防环规则下不可撤销。
- **最小判别实验已设计好**（单格、可负担）：评分里加一个与目的在服集合无关的确定性 tie-break，
  其余不动，看该 OD 的 NO_ROUTE 率是否变化。

**第二十三轮补充（路径级追踪，机制精确化）**：
- 还原失败包逐跳路径：**NO_ROUTE 包 64 跳走遍 64 颗不同卫星**（96 颗里的 64 颗，从不重复），
  且是**确定性扫过**（91→3→11→19→27…），不是绕圈；**成功包 8 跳即命中服务纽约的星（52、44）**。
- ⇒ 机制精确表述：**一次错误转向 + 防环规则（禁止回到已访问星）= 不可撤销的误路由**，
  只能一路向前直到 64 跳上限。
- 两个可改点（互相独立）：① 减少错误转向（在服通告时效）；② 让错误可撤销（防环规则）。
  后者不是本研究主张，但它解释了"损失量正比于错误转向概率，而非绕路长度"。
- 也解释了为何只影响 1/6 个 OD：只有那个 OD 的入口选择会落到"通向纽约服务集之外"的分支。

**第二十二轮补充（跨场景复核，结论强化）**：
- 五格跨复核（S1/S2/S3 × 两个负载 × 两个窗长）：**NO_ROUTE 恒为 194–205 个，
  恒为 1/6 个 OD，恒为同一对 `G1:129:296→G1:130:106`（北京→纽约）**，其余 5 对零失败。
- ⇒ 失败**不是负载效应、不是突发效应、不是窗长伪影**，而是该 OD 的结构属性；
  与 §12.28 因果链（64 跳绕行、候选不空、队列满仅 308 次）一致。
- 也解释了"代价看起来小"：只影响 1/6 个 OD，全网平均只有 0.47%–1.26%。

**第二十一轮补充（补上"分散 OD"维度）**：
- 任务书要求"另加一个更分散的 OD 布局，检查结论是否只对手选热点成立"。已建并发射：
  8 网关（4 大洲）均匀 OD，同几何/链路/控制面；参考 **max_b_e 0.667 → 0.1786**、零跳份额 0 → 10.7%，
  ρ=0.20 档 R=1 118 Mbps ⇒ 0.35 s 窗约 32.6 k 包（约 1.5 h）。
- 该格要回答的问题很窄：**trunk 上发现的 NO_ROUTE 机制在分散布局下是否也出现、是否仍集中在少数 OD。**
- 若它按时完成，任务书 #4 从"未做"变为"完成"；若因归约瓶颈未完成，如实登记。

**第二十轮补充（因果链闭合）**：
- 扫全部 134 067 条决策：**候选列表为空 0 次**（delivered 与 noroute 都是 0）；
  过滤器前三为 **`loop_to_visited_satellite` 122 640**、`not_a_forward_decision` 60 944、
  **`insufficient_queue_room` 仅 308**。
- ⇒ **不是没方向、不是队列满**，而是"所有可行方向都回到已访问星"→ 绕行到 64 跳。
- 完整因果链（每环有实测）：**在服通告陈旧 → 缺可用目标 → 只能走未访问邻星 → 64 跳 → NO_ROUTE
  → 该 OD 3.7%（真值 0.3%）**。
- 这同时终结三问：ISL 排队为零（包未走到有效链路）、预测器差距仅 1.2–1.9 倍（预测的量与失败无关）、
  "代价看起来小"（只影响 1/6 个 OD，全网稀释到 1.26%）。

**第十九轮补充（机制定死）**：
- 按 pid 统计跳数并与 fate 交叉：**DELIVERED 平均 6.98 跳、max 18、撞上限 0**；
  **NO_ROUTE 194 个全部恰好 64 跳（= `max_hops`），194/194 撞上限**。
- 结合几何核验（纽约全程 6 颗可见、零空窗）⇒ 机制链完整：
  **目的小区几何可服务 → 但包找不到"当前在服"卫星 → 绕行 64 跳 → NO_ROUTE；
  换真值状态后同一 OD 从 194 降到 15。**
- 失败点因此确定为**在服集合的通告/可得性**，不是链路排队、不是几何覆盖、不是截止期。
- 这解释了预测器差距仅 1.2–1.9 倍：**它们预测的是 ISL 出口队列，与这个失败模式无关。**

**第十八轮补充（自我更正）**：
- 几何核验（min_elev 10°、[2.00,2.40] 每 10 ms）：北京 3/3、伦敦 5/5、**纽约 6/6** 颗可见卫星，
  **零覆盖空窗 0/41**。⇒ §12.25 的"没有可服务在服卫星"假设**被证伪，已撤回**。
- 另更正：`G1:130:106` 是**纽约**不是伦敦；失败 OD 是**北京→纽约**。
- 机制回到"未确定"：候选为端点关联空闲（`idle_release_s=1.0`、纽约发 1/6 业务）、
  跳数/TTL 耗尽、或在服通告滞后。**三者都未核验。**
- **保留 §12.25 原文并加注证伪**，不悄悄改掉。

**第十七轮补充（机制收窄到单 OD）**：
- 把 194 个 NO_ROUTE 按 OD 对拆开：**6 个 OD 对里只有 1 个失败**（北京→G1:130:106），
  stale 下 5 236 发 / **194 失败 = 3.7%**；truth 下同 OD **15 = 0.3%**（12 倍差）；**其余 5 个 OD 零失败**。
- ⇒ 陈旧状态的代价**不是网络级现象，而是集中在单一目的小区的覆盖空窗**；
  包在该小区无可服务卫星时滞留 holding（正是 holding 面积 18–22 倍于 ISL 队列的来源），
  少数耗尽跳数/TTL 成为 NO_ROUTE。
- 这给出一个**更敏感、可扩样的因变量**：单 OD 的 NO_ROUTE 率，而不是全网平均 e2e。

**第十六轮补充**：
- 补出第 5 类图 `fig_scene_packet_fates`（15 个 arm 运行的包结局堆叠柱）：
  **"超期"与"队列溢出"两个图例在整个图里没有任何柱子**——这本身就是结论。
- 十类图现有 **10 张**（1 部分 / 2、3、4、5、9 完整 / 8 部分 / 6、7、10 未出）。
- 已更新**研究需求主文档**与**工作区 STATUS**：前者新增「2026-10-19 场景校准轮」节；
  后者新增「新场景族的边界」节，**显式声明与旧 R1（2 Mbps 场景）不混统计**。

**第十五轮补充（全格核验，主结论级）**：
- 对**全部 15 个已完成 arm 运行**统计 fate：**有超期的格 0/15、超期包总数 0、队列溢出 0、停时在系统 0**；
  **全部 2 320 个丢失包都是 `NO_ROUTE`**。
- ⇒ 在这套配置与 D4=4 s 下，**没有任何包因"排队太晚"或"缓冲满"失败**；唯一失败模式是找不到路。
- 由此**明确否决**：① 为此加 seed（从未发生的失效模式，加 seed 测不出）；② 实现 GRU/TCN/TAP-DAR
  （它们优化队列预测，而队列不是瓶颈）；③ 加重负载补救（负载翻 2.6 倍后 ISL 排队仍是 0.001–0.017 ms）。
- 下一步唯一方向：把**在服集合的时效**做成主变量，在同场景多 seed 下测 `NO_ROUTE` 的变化。

**第十四轮补充（机制核验，已证实）**：
- 用 `fate_counts` 核验 §12.21 的推断：**每一个丢失的包都是 `NO_ROUTE`，超期数 = 0**。
  cond-arm4 stale：194 NO_ROUTE（1.26%）/ 0 超期；cond-truth：**15 NO_ROUTE（0.10%）**/ 0 超期；
  corridor r050：**0 NO_ROUTE**、100% 交付。
- 数量自洽：capped loss 差 0.011022 × 15 430 ≈ 170 个全损包 ≈ 194−15=179。
- ⇒ **陈旧状态的代价 100% 是"找不到路"，没有一分来自排队迟到。**
- 研究定位随之改写：本场景族要测的是**在服集合/目的可达性的时效**，
  而不是 ISL 出口队列的预测。已写入 §12.22。

**第十三轮补充（六问直答 + 机制修正）**：
- 汇总全部已完成格的交付结局（11 行 fates 表）。**新发现**：trunk 各格"未按期"数恒定在 **194–215**，
  **与负载无关**（289→754 Mbps 同量级），且与 `NO_ROUTE` 同量级；corridor（单 OD、2 跳）**未按期 = 0**。
  ⇒ **trunk 的损失主体是"一小组包根本找不到路"，不是排队延迟。**
- 据此修正解释：瞬态下 O 优于 S（未按期 194→15）**不是排队更短，而是可达性**。
- 已写入 §12.21 六问直答：1 场景通过情况与假设、2 损失量级与"稳态无损失"、3 预测器不显著优于 hold_last、
  4 收益来自可达性、5 独立测试范围（**单 seed，无置信区间**）、6 缺口与原因。
- 一句话结论：**陈旧状态的可利用余量主要体现为"能不能找到路"，不是"排队多长"。**

**第十二轮补充（跨业务形态复核）**：
- 同一离线方法换场景：S1 平稳 / S2 OD 独立突发 / corridor，三者的预测器相对 hold_last 的 MAE 倍数：

  | 场景 | zero | hold_last | bounded_linear | analytic_drain |
  |---|---:|---:|---:|---:|
  | S1 中载 | ×509 | 1.00 | 0.98 | 0.89 |
  | S2 中载 | ×316 | 1.00 | 0.94 | 1.02 |
  | corridor r050 | ×318 | 1.00 | 1.17 | 0.95 |

- **状态本身极有价值（zero 差 300–500 倍），但对"最后值"之外的预测没有价值**
  （全部落在 0.89–1.17 倍）；**突发形态也没有让预测器分出高下**。
- ⇒ 加更强序列模型缺乏证据支持；要改变结论必须改**预测时距**或**状态对决策的影响强度**，
  不是模型容量。

**第十一轮补充（离线预测器比较）**：
- 从决策流重建 96/332 条（卫星,方向）观测队列序列，调**生产代码里**的四个预测器做一步预测。
- corridor：MAE zero 85 647 ≫ analytic_drain **256.5** ≈ hold_last 269.6 ≈ bounded_linear 316.1（bit）。
  trunk：bounded_linear **188.6** 总体最优；analytic_drain 172.2 总体最好但**繁忙尾部最差**。
- **"假设全空"差 300–500 倍** ⇒ 队列状态确实携带信息。
- **预测时距几乎全 < 5 ms**（5–20 ms 仅 corridor，≥20 ms 近乎为零）⇒
  **"预测更远的未来"在本场景族没有可测时间余量**，GRU/TCN/TAP-DAR 的投入判据不成立。

**第十轮补充**：
- **四臂决策流逐条比对：0 / 134 067 不同** ⇒ `hold_last` 下 S=P0=F=A 是**动作相同**，
  不只是结果接近；同一格四臂各自独立跑完，排除"配置没生效"的怀疑。
- 已发射 **P0/F/A 分岔实验**（corridor ρ=0.50，臂 now/common/candidate，预测器 `bounded_linear`），
  run-prefix `scbl`；判据事先声明：**决策差异若仍为 0，就如实报零，不换更大模型追阳性。**

**第九轮补充（账目收口）**：
- 由 VM `runs/` 直接清点：**28 个 run 目录 = 12 完成 + 8 fail-closed（kernel_call_count=0）+ 8 无 summary
  （6 在跑 + 2 已协作终止）**；真实内核启动 **23/60**，磁盘 **4.55 GB**。
- 失败三类已全部定位并记档：CSV 交错写/随机种子（已修）、`policy=delay` 不可用（缓存只覆盖 vis_k=2）、
  `Kernel.run()` 停止后归约 >1 h（**未定位**）。
- 可复算命令（标定 / trace 生成 / 发射 dry-run / 清点 / 归约出图）已写入 review §12.19。
- **未完成项照实列**：P0/F/A 未用外推预测器分离、GRU/TCN/TAP-DAR 未实现、
  第 5/6/7/10 类图未出、verified pull 未做。

**第八轮补充**：
- 图统一到**后填充窗**（正确性修正），并补出第 4 类（CDF）与第 9 类（排队时间线），共 **9 张图**。
- 后填充实测：trunk 四格 ISL 排队 **0.001–0.017 ms**，注入 289→754 Mbps（2.6×）几乎不变；
  corridor **3.04→11.80 ms**（注入 1.9×、排队 3.9×），≈1.48 MB ≈ 986 包。
- F7 时间线证伪"corridor 排队是启动瞬态"：瞬态在 2.13 s 前结束（trunk 塌到 0），
  corridor 排队持续到窗末。
- 十类图：1 部分、2/3/4/9 完整、8 部分；5/6/7/10 未出并写明原因。**未填示意数据。**

**第七轮补充**：
- 产出 5 张图（PNG+PDF）到 `review-20261017/figures/fig_scene_*`：ISL 利用率曲线、
  时延分解、注入-时延、跳数分布、队列面积构成。全部由 VM 上 `result.json.gz` 重算的 CSV 生成。
- **十类图逐类声明**：已出 2、3 完整，1、8 部分；4、5、6、7、9、10 未出并写明原因（缺闭环/预测器数据，
  或只有 2 个负载点）。**未填任何示意数据。**
- 关键机制数字（全窗）：trunk 低/中载 e2e 77–79 ms、holding ≈0；corridor 494.6→948.8 Mbps 时
  e2e **246→333 ms**，holding 面积 **29→79 Mbit·s**（ISL 数据的 18–22 倍）。
- 边界：图绑的是整段运行统计，**未按后填充窗重算**，F2/F3 绝对值偏大（含填充瞬态）。

**第六轮补充**：
- 新增两个**声明式**预测器并带测试（共 4 个）：`zero`（按名字返回 0，刻意放在 history gate 之前——
  "空队列"这个断言不需要历史；放在 gate 后会变成 no_received_history 而悄悄换了实验）、
  `analytic_drain`（按观测速率排空、且**永远不能预测增长**，无速率时回落 hold_last 并记录）。
- **corridor 稳态决定性结果**：ρ=0.95 的 stale 与 truth（ideal_current）**逐位相同**
  （capped 0.08326034302586038，holding/isl/uplink 面积全同）⇒ 真值状态在此场景没有改变任何决策。
- 机制：corridor 主导等待是 **holding（78.7e6 bit·s，ISL 数据的 18 倍）**，等的是"目的小区何时被服务"，
  不是 ISL 出口积压。与 §12.13（瞬态下 O 优于 S）合并得到：**陈旧状态的代价只在瞬态成立，稳态不成立**。
- 预算：真实内核启动 **17/60**，余 43。

**第五轮补充**：
- **S/O 首个配对结果**（同 trace、同 seed，只差 state_source）：advertised 0.038116 / 未按期 1.2573%
  → ideal_current **0.027094 / 0.0972%**（capped loss −28.9%，未按期 −92.3%）。
- **方法陷阱**：用 `hold_last` 时 S = P0 = F = A **逐位相同**（目标时刻不进计算）。
  四臂不能分离 F/A；要分离必须换 `bounded_linear`。此前若报"F/A 无增量"须按此限定。
- **corridor 场景成立**：承载链路 175 → **27**，后填充 ISL 排队 0.0038 ms → **3.04 ms**
  （≈253 包），满足事先判据。主导等待是 **holding（29.2e6 bit·s，ISL 数据的 22 倍）**。
- **自我更正**：`utilization` 分母是整个 12 s 运行；发射窗口径要 ×duration/emission。
  corridor 发射窗 **48.7%** ≈ 标称 0.50；先前"实测 1%"是分母用错，低估 34 倍。
- 预算：真实内核启动 **14/60**，余 46。

**第四轮补充（方法结论 + 两条负面结果）**：
- **后填充窗规则**：同负载两格限制到同一子窗后一致到 0.01 ms（ISL 排队）。
  先前的"0.35 s 比 0.70 s 高 2 倍"是**伪差**（填充瞬态摊薄差异）。规则：统计从 emission_start+0.20 s 起；
  跨格必须同窗；最小可用发射窗 0.35 s。**因此 0.25 s 的条件格只能当瞬态证据**（已如实记录）。
- **场景矩阵（S1/S3 低载、S1/S2 中载）**：后填充窗 ISL 排队 **0.001–0.042 ms**，
  e2e **66.0–66.7 ms 在低/中载几乎不变** ⇒ 系统处在**负载不敏感区**，
  与实测最高利用率 1–2% 一致。**当前场景不给 S/O/F/A 留余地**，不是方法失败。
- **负面结果**：`policy=delay` 39 min CPU、decisions 0 字节——边代价取自控制面缓存而 `vis_k=2`
  只覆盖 2 跳，缓存外一律 inf ⇒ 全判不可达。两格协作终止、日志保留、计入预算。**不再重试同一做法。**

**第三轮补充**：
- 窗口敏感性拿到配对两点（同负载 741–751 Mbps）：0.35 s ⇒ ISL 排队 2.82 ms / e2e 92.1 ms；
  0.70 s ⇒ 1.39 ms / 78.9 ms；两者 **ISL 队列面积几乎相同**（7.32e5 vs 7.31e5 bit·s）。
  ⇒ 窗长改变的是"启动填充瞬态被摊薄的程度"，**0.70 s 不能直接宣称稳态**，跨窗数字不可相减。
- 实测成本**超线性**：17.7k 包 0.124 s/包、21.6k 0.144、43.8k **0.210**。
  按此外推 4 臂 × 15.4k 包 ≈ 3.6 h/格 ⇒ 多 seed 矩阵不可行，必须先修。
- 拿到第一个信息条件点 O（`sc-cond-r050-truth-s111`，ideal_current）：D4 capped loss 0.027094、
  未按期 0.097%。**但同格 S 还在跑，故不做比较**。
- 预算：真实内核启动 **7/60**，余 53。

**第二轮补充（同一夜，继续推进）**：
- 已发射 S/O/F/A 条件对比（`scc-20261019-05`）：1 格 4 臂（stale/now/common/candidate，
  state_source=advertised ⇒ S、P0、F、A）+ 1 格真值臂（now + ideal_current ⇒ O），r050、0.25 s 窗。
  核实 `state_source` 合法值只有 `advertised | ideal_current`（不是我先前写的 kernel_truth）。
- **决定性离线核查**：S1 中载短窗格 175 114 次决策 × 4 方向 = 700 456 候选槽中，
  **仅 3.17% 有排队**，仅 **12.60% 的决策**至少一个候选非空；非空槽 p50=5.82 µs、max=10.54 µs，
  折合 1 Gbps 下 **727 B / 1 317 B**——一个 1500 B 包占 12 µs，所以非空槽也装不下一个整包。
  结论：本场景族里 S/O/F/A 的差别注定接近零，**可定价的状态量本身几乎不存在**，
  下一步方向应是让场景真的在共享瓶颈排队（或修参考/执行路由对齐），不是加预测器。
  详见 INDEPENDENT-REVIEW.md §12.7。
- 预算：真实内核启动 **7/60**，余 53。

- 用户点出的五项校准问题：逐星 CPU、逐网关、静态↔实际一致、窗口敏感性、控制占用两种口径 →
  前四项（第 4 项部分）+ 第 5 项已处置，证据见 INDEPENDENT-REVIEW.md §12.1。
  静态 spec 与运行 resolved_config **13 项逐字段全等**。
- 新增声明式 demand 程序（S1 平稳 / S2 OD 独立突发 / S3 相关热点）与诊断流开关（timeline 可关，
  单格产物 700 MB → 230 MB）。release 链：`2de8cdb` → `65a6f9f`（wave1 合同）→ `e81890c`（原子写 + 稳定种子）。
- 首轮实测 4 格完成（S1/S2/S3 低载 + S1 中载短窗），ISL 排队 0.29–2.82 ms，尾丢 0，
  低载三场景无差别（有效零效应）。
- **未完成**：S/O/F/A 四主条件、10 seed、预测器比较、十类图。原因是单格 0.124 s/包
  且 `Kernel.run()` 停止后归约 >1 h 未定位。9 格仍在跑（中/高载、窗口 1.20 s、缓冲 5/100 ms）。
- 预算：本轮真实内核启动 **5/60**（8 格 fail-closed 且 kernel_call_count=0 不计入）。
- 失败：tcsv 交错写 + `tuple.__hash__` 随机种子（已修，见 §12.5 与 PLAN-20261019.md）。
- 证据：批量启动器发的诊断批次**无根回执**（与 rtE/rtS 同），未做 verified pull；
  派生表在 VM 只读生成。

## 2026-10-18 校准轮（历史）

范围：仅 `worktrees/.t1-scene-calibration` / `codex/20261018-scene-calibration`；工作区级入口仍是 `topic/STATUS.md`，本文件只记本工作树任务。

**任务**：为现有 LEO 星间路由实验算出一套物理一致、成本可承受的流量配置，并做有界诊断。不训练预测器，不启动正式矩阵，不按算法胜负选场景。结论与证据写在 `.diag-ta-20261002/study-design-20261009/review-20261017/INDEPENDENT-REVIEW.md` §11（本轮唯一结论入口，未新建竞争文档）。

**交付物**：`CODE/experiment_platform/scene_load_calibration.py`（参考路径→b_e/c_e→R(ρ) 计算器，含手算自检、逐星/逐网关容量、OD 口径对比、替代路径）、`CODE/experiment_platform/analyze_scene_calibration.py`、profile `t1_scene_trunk_v1.yaml` 与其突发变体、合同 `scene_calibration_{stationary,burst}_20261018.json`、`CODE/work/WP-T1-SCENE-CAL/`。

**版本身份**：`dac3387`（v1 静态标定与 release）→ `8267451`（scope 对比/分析器）→ `8992128`（逐星容量纠正）→ `3cfdd8f`（计算面改为 1 µs 服务时间）。各版 release 见 review §11.1/§11.7。分支已推送 origin。

**已验收**：计算面纠正（决策排队 83.8%→2.6%，等待 p99 238 ms→0.67 µs；同格 12 s 仿真跑完）。

**未完成/未验证**：六档×三 seed 与平稳/突发矩阵**未运行**（用户指示暂停追加；只完成一个探针）；参考路由与执行路由不一致已量化但未纠正；接入与缓冲未做仿真敏感性；探针的 `summary.json` 在本轮结束时仍在结果归约阶段（>1 h），故**回传（verified pull）未完成**。

**预算**：真实内核启动 19/24，余 5。被平台 fail-closed、`kernel_call_count=0` 的 1 次不计入。批 A 的 17 个 run 目录保留未删、无结果、run-id 不复用。

**维护检查**：`python3 -B CODE/scripts/maintenance_check.py --repo .` → 0 error / 1 warning（外置备份与依赖锁未核验，属预期）。

---

## 分支基底历史（不代表本工作树当前结果）

# 本工作树状态：长业务与50ms邻居状态对照

范围：仅 .t1-fast-observation / codex/20261016-fast-observation，工作区入口仍是topic/STATUS.md。基于3a3bc0a创建，原负载校准工作树保持不变。

本轮适配有界诊断合同至300秒、最多100000包；新合同使用160秒、30000包上限，120秒发包、50ms邻居状态广播，6seed×4臂，bounded_linear不变。服务集合固定known_serving，属于受控队列信息实验，不是部署收益结论。40项mock测试通过，科学运行仅VM。执行前先烟测，再检查候选覆盖与资源成本。主指标是端到端时延和P95，交付率配套。

以下为分支基底历史记录，不代表本工作树当前新实验结果。

# 本工作树状态：负载校准与时间处理验证

范围：仅本 checkout，分支 `codex/20261013-load-calibration`；工作区级状态仍以工作区根 STATUS 为准。更新：2026-10-16。

## 2026-10-16 队列状态年龄实验（rtA，最新，本轮唯一任务）

- HEAD `00f756829ed62bff299990d310b097348b11fcaf`，工作树干净。改动：
  `CODE/leo_sim/config.py`（`min_observation_age_s` 字段与默认 None）、
  `CODE/leo_sim/kernel.py`（快照构建时按 `measured_at <= t0 - a` 扣留样本；
  `observation_age` 审计块用预测器自身的 `order_history`/`_latest_by_resource` 构造，
  并整体包在 try/except 里——诊断错误绝不改变决策）、
  `CODE/experiment_platform/load_calibration.py`（`time_alignment` 覆盖组新增该字段）、
  `CODE/leo_sim/tests/test_routing.py`（3 个新测试：扣留改变读入值、配置默认与解析、
  审计在无映射方向上不崩）。`test_routing.py` + `test_config.py` 58 passed。
- 合同：`CODE/work/WP-T1-COMPLETE/queue_age_main_20261016.json`（18 格，看结果前冻结：
  seeds 81–86 × a ∈ {0, 2, 6} s，服务信息固定 `known_serving`，预测器 `hold_last`，arm `stale`）。
- 结果：`a=2−a=0` = +3.50 pp [+0.58, +6.42]，`a=6−a=0` = +8.62 pp [+6.70, +10.55]，均 6/6 同向。
- **损失性质（按 rtA 独立审查更正）**：D4 未达标 7.15% → 10.66% → 15.79%，而**总体交付率几乎不变**，
  为 **96.36% / 96.21% / 96.05%**（未交付 3.64% / 3.79% / 3.95%）；新增未达标几乎全是"送达但超期"。
  上一版写的 92.85%/89.31%/84.21% 是 1−D4 的近似按期率，不是总体交付率。
- **a 是"源测量年龄下限门槛"，不是实际年龄**：a=0 的臂照样有采样/传播陈旧（匹配子集实际年龄中位约 2.09 s），
  不能称为零年龄真值，也不能写成"从 0 延迟增到 6 秒"。
- **机制核验分两层**：固定冻结历史的纯函数测试证明门槛改变读入值（9000 → 5000 bit，样本缺席而非被改写）；
  闭环配对（70.3% 读入值不同 / 86.8% 评分不同 / 64.4% 动作不同）是**已分岔轨迹**的描述，
  不是冻结同状态的机制归因。
- **裁决收窄**：限制较新队列观测的可用性明显增加超期，可归因于**队列观测可用性退化**
  （含旧值与缺观测两部分，未分离纯旧值贡献）；这支持研究状态更新与预测，
  但**不专门证明候选使用时刻对齐值得做**。
- **预算对账（独立审查指出旧账 127/134 错误）**：内核启动 **153/160**（余 7），
  全部尝试 **157/160**（余 3）；组成 rt1 30 + rt2 9 + rt3 30（失败关闭、目录已清理）+ rt4 30
  + rt7 34 + rt9 2 + rtA 18，另有 4 次入口拒绝未启内核。**对账前不再发射。**
- 两次失败根因已修复：rt8 是合同未进 release（校验拒绝、未启内核）；
  rt9 是 `resource_for(d)` 为 None 时审计调用 `.as_tuple()`（内核已启动，计入预算），
  已加"审计错误永不改变决策"的测试。

## 2026-10-16 第二批（rt7）：初始化证伪、可部署事件通告、队列年龄 pilot

- HEAD 本轮终态 `831562c6233572ea0c1d183f42236b8aeaca26a4`，工作树干净；本轮改动：
  `CODE/leo_sim/kernel.py`（advertiser 独立日程、`_serving_changed`/事件通告、计数器）、
  `CODE/leo_sim/config.py`（`event_advert_enabled` / `event_advert_min_interval_s`）、
  `CODE/experiment_platform/load_calibration.py`（允许 `time_alignment` 覆盖组）、
  `CODE/leo_sim/tests/test_routing.py`（断言周期栅格为 0/2/4、事件通告计数）。
- **周期栅格不再被初始报文平移**：`initial_sync_burst_s` 现在走独立日程，测试断言
  plain=[0,2,4] 且 burst 同时包含 0/2/4 与 0.5。上一版把它平移成 0.5/2.5/4.5。
- **事件触发通告**：`_serving_changed(sat)` 在关联变为 active 或链路 release 时调用，
  经 `event_advert_min_interval_s` 限频、窗内变更合并为下一条；计数器
  `serving_changes_seen` / `event_adverts_emitted` 已暴露。seed 71 实测 167 次变更 → 67 条报文。
- 合同：`CODE/work/WP-T1-COMPLETE/init_event_queueage_20261016.json`（33 格）。
- 烟测纪律：两次单格烟测分别被"未知顶层字段"和"未支持的覆盖组"以 fail-closed 拦下，
  均在发射批次之前，零无效内核调用；对应校验缺口已补。
- 结果与边界见工作区 `汇报材料/状态时间错位_20261016早间汇报.md` §七。

## 2026-10-16 夜间实验的实现与证据（上一批）

- HEAD `aed0dfefee6d2d3dfd7bdf2685678c32b13fc336`，工作树干净。本轮改动全部在
  `CODE/leo_sim/{routing,config,kernel}.py` 与 `CODE/leo_sim/tests/test_routing.py`。
- **干预范围固定**：新增 `_serving_source()` 作为唯一在服集合入口，由**有效策略**切换；
  四个 `choose_next_hop` 调用点、`_candidate_resource_map`、`_estimate_at_start` 全部改用它。
  修正前 known 臂 3.07% 决策全候选 fallback，修正后 1.32%。
- **新增实验面**：`control_plane.initial_sync_burst_s`（等成本初始同步，真实控制报文并计费）、
  `routing.staged_policy` + `staged_policy_at_s`（共同前缀后切换策略）。
- **新增策略** `known_serving`（统一版）；`hop_known_serving` 保留为已发布混合版身份。
- 测试：`test_routing.py` 25 项（含"每个策略端到端跑一遍"的回归，用于防止调用点 NameError 再次漏网）、
  `test_config.py` 30 项全过。
- 合同：`CODE/work/WP-T1-COMPLETE/night_startup_vs_live_20261016.json`（30 格）。
  结果与边界见工作区 `汇报材料/状态时间错位_20261016早间汇报.md`。
- 失败记录：release `fe90f3cc…` 上 30 格因 `_candidate_resource_map` 内 `NameError` 全部
  FAILED_CLOSED（内核已启动，计入调用预算）；修复后以 release `aed0dfef…` 重跑 30 格全部完成。

## 已完成

- 四臂压力诊断与三个 seed 的广播周期扫描已完成；广播周期 0.5 / 2 / 4 秒下，D4 未达标的合并计数为 53 / 221 / 691，分母均为 4541。同 seed 复用 trace，跨 seed 不同。
- 多 server 不支持的时间对齐配置现在被显式拒绝，编译默认值为单 server。
- 理想状态测试快照补齐 `local_egress_in_service_s=0`，表达本地服务器空闲；评分器与原动作断言不变。本机复核：`test_ideal_state_time.py` 14 passed / 1 skipped；`test_frozen_ledger.py` + `test_s3_review_counterexamples.py` 22 passed；`test_bp_heuristic.py` 9 passed；`test_config.py` 30 passed；`experiment_platform/tests` + `tests` 合计 **782 项可无错收集**，此前的收集失败已修。
- 多 server 与时间对齐的矛盾组合由 `resolve_config` 的 fail-loud 守卫拒绝，已直接触发验证。`t1_suite compile` 本机复现：59 格，35 份配置全部 `enabled=true, servers=1`，0 处矛盾。
- `max_duration_s` 由硬校验 12.0 改为声明值、限定 [12, 60]（两端有测试）；该硬上限正是旧曲线跑不出预热窗口的结构性原因。
- `four_direction_audit` 补回 `DECISION_ROW_KEYS`（19 → 20）。此前每条 `CODE.leo_sim run` 都以 `DecisionRowError` 失败关闭，fold/replay 入口完全不可用。
- `CODE` 全套 1948 passed / 19 failed / 8 skipped。19 个失败全部既有：所在 6 个测试文件均不引用本轮改动符号，失败原因分别是 `benchmark_decision` 输出字段缺失、`t1_suite` 运行编排、旧版本回执重验。
- 维护检查验证 158 条索引、122 份本地回执，0 errors、37 warnings。警告涉及旧证据缺失和外部备份/依赖锁未核验。

## 研究状态

旧 12 秒曲线的"周期越慢损失越大"是**冷启动**，不是状态陈旧。逐决策核查：收到第一条广告前节点没有任何状态，数据包被 `hold`（`no_downstream_isl_resource_for_hold`），等首条广告到达后 `redecision`；首次 forward 决策在 0.655 / 2.175 / 4.166 秒，正对应 0.5 / 2 / 4 秒广告周期。无状态窗口里包不丢，只是被压住错过 4 秒期限（4 秒周期 [0,4)：781 发包 / 197 未达标 25.2% / 平均时延 1.638 s；[4,8)：739 / 10 / 1.4% / 0.420 s）。

预声明预热窗口（28 秒运行、业务 [0,24)、预热 [0,12)、评估 [12,24)、三 seed）：

| 窗口 | 0.5 s | 2 s | 4 s | 4 s ÷ 0.5 s |
|---|---:|---:|---:|---:|
| 预热 [0,12) 均值 | 0.959% | 3.504% | 10.710% | **11.17×**（三个 seed 全单调） |
| 评估 [12,24) 均值 | 1.507% | 1.140% | 1.506% | **1.00×**（排序不一致） |

四臂（4 秒周期、同一 seed、同一预声明窗口）：**6489 个同观测可比决策、18829 个方向对，0 次偏好反转**，四臂选择 0 次不同。四臂的 `resource_work_s` 确实互不相同（不是静默塌缩），但修正量中位 0、p90 6.5 ms、最大 110 ms，而名次间隔中位 35 ms。

机制是活的：25%（137658 / 549357）候选槽位 `resource_work_s` > 0；isl_data 队列面积 45.4 / 42.0 / 96.6 Mbit·s（0.5 / 2 / 4 秒周期）；评估窗口候选完整率 94–98%。差异小不是因为没有拥塞，而是队列项改变不了排序。

结论：R1 须改口径为"控制面恢复期损失"；R2 在当前场景为零。要谈候选使用时刻的价值，先要造出"陈旧会造成排序翻转"的场景，判据应是 `修正量 / 名次间隔` 的分位数，而不是继续扫广播周期。

这些诊断中有 `full_replay=false` 的输出，正式入口、回放和相关门禁需按拟用路径验收。

## 下一步

1. 把名次间隔压小、队列变化率拉大：找出使 `修正量 / 名次间隔` 分位数上升的负载与拓扑条件，判据跑前定死。
2. 三个 seed 的四臂对照（本轮四臂只有 seed 7）。
3. 离线真值上界臂，把损失写成绝对量。
4. 正式路径 `t1_suite` 按拟用路径整体验收；本轮全部是 diagnostic。

此前状态的原始字节保存在工作区私有审计归档，历史代码状态也可经 Git 查询。研究主文档与运行存储盘点由工作区入口导航，不复制进本公开代码仓库。

---

## 附：main 侧 T1 状态（origin/main @ 5458e57，2026-10-20 merge 对账保留）

> 本节为 merge `origin/main` 时的原样保留；本文主体是 `codex/20261018-scene-calibration` 的场景标定任务状态。

# T1 仓库当前状态

本文是仓库级当前状态入口；工作包内 STATUS 只记录各自范围。具体版本以完整 Git commit、release-id 和 run-id 为准。

## 当前整合与运行状态

- PR #7 已通过独立审查并合入 `main`，代码合并 SHA：`857a9de4fa5c4eb07a3b466788b3a9ccba69c847`。精确 PR head `e44f0d5369256e5445443a63df2acad043418976` 的 GitHub Actions `test` run `36619082541` 成功；维护检查和 remote 协议测试已进入 CI。
- PR #8 状态/索引跟进已合入 `main`，merge SHA `c6fd2898002e5898de1c353c110673ee5d84e2af`；其 PR CI run `36625374273` 和合并后该精确 `main` SHA 的 push CI run `36627212189` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #8 不改变 T1 运行代码。
- PR #9 已通过独立审查并合入 `main`，merge SHA `d8384aa9dde425ecfcc87cff42d4b8786cf2d043`；PR head `643ee41af219bb26c3f253e20cd06c6a1af41f07` 的 CI run `36629702140` 和该精确合并 SHA 的 push CI run `36631973728` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #9 只加入脱敏索引和状态说明，不改变运行代码。
- 该 `main` SHA 已发布到隔离 T1 的不可变 release 目录。release-id：`d8384aa9dde425ecfcc87cff42d4b8786cf2d043-435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`；source tree SHA-256 `435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`，artifact SHA-256 `c76dc758575c51a28ce61eef6acece6ee35846dd0478fa0d26b04a5e60b988af`。发布器验证远端 `main` 备份身份并确认 release 安装成功；它不代表工作区资料已备份。
- 新 run `syncdiag-20260930-04` 的配置校验命令遗漏子命令必需的位置文件参数，故执行状态为 failed / exit 2。失败 receipt SHA-256 `d5a9868a80c7680433e79383e8327de4f1c4e880ca1a4535adebd0d8f9cf0af5`；回传器验证 receipt 与文件清单成功，索引 `pullback_status=VERIFIED` 只表示证据回传核验通过，不表示命令成功。失败材料保留，没有复用 run-id。
- 新 run `syncdiag-20260930-05` 在上述 release 上以 `diagnostic` 模式完成 smoke 配置校验（status `completed` / exit 0）；receipt SHA-256 `4fe28fd41c9a62ea3f28455b41ab8244849c6cde6153b06343bac3d59b68beb4`，run-manifest SHA-256 `f48b4d1d06aa9db8b7e8d7a4acf63c6320ed4735779f1a75e689d12ca15c44ce`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验结果 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM 再验、回传和索引 URI `evidence://t1/syncdiag-20260930-05` 均为 `verified`。
- PR #10 已通过独立复审并 squash 合入，head `8b197f465fbe1083431d77a481b1fef2b5a6515a`，merge SHA `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed`。PR merge-ref CI [run 36636615895](https://github.com/fuguther/leo-experiment-platform/actions/runs/36636615895) 测试了该 head 合入 base `d8384aa9dde425ecfcc87cff42d4b8786cf2d043` 的集成对象 `066fc256c982421e59db1580c3facd7d7c2b3c91`，结果 1,060 passed、2 skipped、2 warnings；该链接为 PR merge-ref 检查，不代表合并后的 main push CI。
- 接受版本已发布到隔离 T1，不可变 release-id `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed-5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`；source tree SHA-256 `5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`，artifact SHA-256 `8db3aa2bb750e2951f4b4d17253d38e1dac053387cf9d069624dd08e20872e24`。发布器的远端 main 身份、安装与 incoming 清理状态均 verified/published/cleaned。
- 新 run `syncdiag-20260930-06` 在该 release 上以 diagnostic 模式完成 smoke 配置校验（completed / exit 0）。receipt 的 protocol canonical `receipt_sha256` 字段为 `904c645984b67a3c48a53aad27051747343260ea30ea6862aab4b808e9d3b973`（不是 receipt 文件原始字节哈希）；run-manifest SHA-256 `fd36666103569df0d785b6733f043153d8fa53b4f873e59485ab724c93139f4c`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验输出 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM receipt、manifest/file-set 再验成功，本机 pullback 和部署索引 URI `evidence://t1/syncdiag-20260930-06` 为 verified。
- PR #11 已 squash 合入 `main`：head `99158c5f64120fcb1695826a4406c53f15c23757`，merge SHA `803fafcf7b51033d8b32e609750e32d7dfe7e996`；其 merge-ref CI [run 36640391596](https://github.com/fuguther/leo-experiment-platform/actions/runs/36640391596) 成功，测试合并对象 `6a2bd8b`（1060 passed、2 skipped、2 warnings）。该 PR 只更新 #06 的脱敏回传索引和状态，没有发布运行代码。
- PR #12 已通过独立复审并 squash 合入 `main`：PR head `6b9a218f77eae91a63ad42b699c8523616d91567`，merge SHA `6b4bd6b406d34f97b8f1f4c85c1a717379366536`。精确 PR head 的 CI run `36645147051` 与该 merge SHA 的 main push CI run `36650978047` 均成功，pytest 均为 1,065 passed、2 skipped、2 warnings；CI 中维护检查 0 errors、1 条范围 warning。
- 从上述精确 main merge SHA 发布的隔离 T1 release-id 为 `6b4bd6b406d34f97b8f1f4c85c1a717379366536-06c89985256898a012b5796ac362cffe0096123a30c58e01fb0b20012009f3e0`；source tree SHA-256 `06c89985256898a012b5796ac362cffe0096123a30c58e01fb0b20012009f3e0`，artifact SHA-256 `e781d0817034f995201b3748155ee1967eb5efb137765dc04df0b910caf94b44`。发布器报告远端 main 备份身份 verified、release published、incoming 清理完成。
- 新 run `syncdiag-20260930-07` 在该 release 上完成 diagnostic smoke 配置校验（completed / exit 0），验证结果内容 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。receipt canonical SHA-256 `9a257b922876bdd77cec75fca0cc9898df33894af028e67d460a751591c224a0`，receipt 原始文件 SHA-256 `b744f5cbb661d0ba24d414a3ebc6c85098db1238e71aef214afec92fb351ce6b`，manifest SHA-256 `6ad17f3d70a4af638d5737ab4783643cb928d2d4c34074d1c985a72ede7211f3`。manifest 绑定 runtime contract SHA-256 `b3b548fdba7f364d5f48ed0bcc5c77ca667f6bd4ad2c463844eca8a18923e5d0`；T1 实际环境为 Python 3.11.15/Linux/aarch64，包清单摘要 `3bc4c603b890231bfe8e27441c639d2bf0152617524b43e72dd4dfc34edbe152`。VM receipt、manifest/file-set 复验、本机 pullback 和 `evidence://t1/syncdiag-20260930-07` 均 verified。只运行配置校验，无仿真、训练、GPU 调用或正式实验。
- 在较早 PR #7 release `857a9de4fa5c4eb07a3b466788b3a9ccba69c847-27161ebf8e8db22b6ab128d7458d038d69bd63031b361f3a8a36fcae7f925a38` 的隔离 Python 前缀上运行 `CODE/scripts/remote/tests` 和维护检查测试，带有 host、release、commit、解释器和命令身份的日志记录 `64 passed in 7.86 s`；本机相同测试选择 `64 passed`。这是旧 release 的工程测试证据，不表示新 release `d8384aa9…-435da056…` 已运行同一套 64 项测试，也不是仿真、训练或科研结果。
- PR #8 对 `syncdiag-20260930-03` 添加了经核验的小索引行并更新状态；PR #9 整合了并发与 release 选择的四条索引记录；PR #10 同时整合 #04/#05 索引、runner 参数规则及 release identity 修复；PR #11 已整合 #06 的脱敏索引和状态。原始 receipt、manifest 与运行日志留在本机私有证据目录。

## 日常同步与发布

1. 进入工作树先读本文件和 `AGENTS.md`，确认仓库、owner、branch、完整 HEAD、dirty/untracked/ignored 路径及其他活动任务；执行 `python3 -B CODE/scripts/maintenance_check.py --repo .`。
2. 只从已提交、已推送、干净的完整 commit 运行 `CODE/scripts/remote/publish-release-remote.sh --commit <FULL_SHA>`。新发布写入不可变 T1 release，不再覆盖共享 `CODE/`；不得把旧 `t1-vm.sh sync` 当作新发布入口。
3. 诊断/开发使用固定 release-id 和从未用过的 run-id，经 `CODE/scripts/remote/run-release-remote.sh` 执行。`--config` 只固化输入；子命令仍须在 `--` 后取得必需文件参数，例如 `--config CODE/leo_sim/profiles/smoke.yaml -- python3 -m CODE.leo_sim config validate CODE/leo_sim/profiles/smoke.yaml`。新 runner 禁止 formal 模式；formal 继续走原有编译、独立审阅、授权和 receipt 路径。
4. 失败发布先确认没有同 release 活动上传，再检查 incoming/bootstrap。用已安装 release helper 的 quarantine 恢复器保留残留后重试；不得删失败回执、复用 run-id 或覆盖已发布 release。
5. 回传只能用 `CODE/scripts/remote/pull-release-results-remote.sh`。它先验证 VM receipt、run/release 身份和文件清单，再原子落盘并追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。验收本机证据时运行 `python3 -B CODE/scripts/maintenance_check.py --repo . --verify-evidence --evidence-root <实际本机证据根>`。

## 依赖与平台边界

- `CODE/dependencies/t1-vm-linux-aarch64/` 包含直接依赖说明、88 个固定版本 pip 解析结果和 27 个 Conda explicit URL；解释器、Linux/aarch64 与系统条件单独记录。
- 新版本化 Conda 前缀已从上述锁在 T1 重建；88 个 pip 固定版本与 27 个 Conda URL 全部对账匹配，`pip check`、核心导入和 64 项目标测试通过。重建证据保存在本任务本机私有证据目录。
- pip 锁没有 wheel SHA-256，所以结论是平台限定的版本集合可重建，不是制品字节级可复现。CUDA toolkit 未确认；本次诊断和维护/协议测试没有调用 GPU。
- release 按精确 Git 树和路径/后缀规则打包；CSV/JSON 不会仅因扩展名自动排除，数据来源和许可仍须在 `资产登记.csv` 中逐集合核对。未核实材料不得新上传到公开 GitHub。

## P2 验收边界

- 本地协议 fixtures 覆盖声明输入/配置快照、重复 run-id 拒绝、回传截断/篡改、索引并发和旧/新 release 并存；formal fixtures 覆盖配置/源码绑定与过期 authorization 拒绝。
- T1 真实 SSH 故障注入覆盖 4 KiB 上传中断和 release 安装后的 cleanup 中断；两类残留都经 quarantine 保留，已安装 release 哈希不变；同 release 重试返回 `already_present`。失败材料与哈希见本机私有治理证据。
- PR #10 已修复并测试 release basename/envelope/request identity 错配；PR #12 加入无普通 API/CLI 绕过的 runtime contract，拒绝 Python/platform/machine/包集及 pip/Conda 锁身份不匹配，并在创建 run 目录前停止。V2 授权夹具覆盖错误 run-id、授权后数据 trace 和 checkpoint 文件字节变化拒绝；这些测试不加载模型、不启动仿真或训练。真实 T1 #07 与契约匹配，manifest/receipt 身份绑定成功。完整模型加载、未登记 CUDA/toolkit 和正式科研运行不在本次验收范围。
- 本次在隔离 T1 同时运行 `syncdiag-20260930-concurrent-a/b`：二者使用同一当前 release 与相同配置快照，各自有独立 run 目录、manifest、stdout 与 receipt；并发 pullback 均验证成功，部署索引保留两条唯一 run-id 记录。随后用显式 release-id 选择旧 release `b8d8263…-401d84ab…` 做静态配置校验，再选回当前 release `857a9de4…-27161ebf…` 成功运行；没有全局可变活动指针，故此验收证明旧/新版本可独立选择且不覆盖彼此，不声称验证了全局指针切换。两 run 并发与旧/新 release 并存的本地 fixtures 仍保留。

## 文件、备份与历史限制

- topic 根 `AGENTS.md` 管跨项目边界；topic 根主方案和 `资产登记.csv` 是三端治理的唯一权威工作区文档/清单；本文件只报告仓库状态，不复制全套本机资料。它们目前只有已确认的本机权威副本。
- 尚无获准且已核实的设备外独立备份目标；GitHub 只保存本仓库已提交的公开文件，T1 release 也不代表本机资料已备份。需要目标容量、权限、用途确认后再复制、重算源/目标 SHA 并恢复样本。
- `/data/论文/leo-direct-sim` 旧 formal 根保持只读。4 个清单外 JSON 的历史 source snapshot 可关联到已知代码快照，但没有精确 run-id/receipt 绑定；不删除、不重写 receipt、不重新盖章。该旧账与新的隔离 T1 release 流程分开记录。
- 当前复核到 7 个 Git worktree。其他工作树 owner/活动进程无法全部确认；dirty、untracked 和 ignored 研究材料原样保留，没有批量归档或删除，也未声称其他工作树已统一升级。
- 状态入口持续维护；每次规则或工作状态变化更新本文件。检查器只读，不联网、不做实验、不删文件；`0 errors` 仅表示通过其列明的维护范围。
