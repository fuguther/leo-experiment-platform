# WP-T1-COMPLETE — 状态账本

> 计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`
> 交付：`REPORT.md`（含"第二轮复审返工 S1–S7"节）；判据：`criteria.json`；语义合同：`contract.yaml`。
> **执行位置硬规则（AGENTS.md）**：所有实验只在 VM 上跑；本机只做代码/测试/只读复核。

## 当前工作包 B 状态（2026-10-01；发布前冻结点）

- 工作树 `.t1-dev-experiment-a`，分支 `codex/20260930-t1-experiment-a`；本包变更尚未提交，正在按精确源码身份准备发布。没有修改 `leo-exp-main` 或其他工作树。
- 当前合同 `contract_dev_b.yaml` SHA-256：`dbe2d2636066e394bacecbc1c55e1b50f2b79e078544ebc0595765b8296af9b2`。最终合同编译/校验：42 格容器（仅静态打包），B tier 9 格 / 57 静态调用上界；pilot 为 5 格 / 39 次，余下 4 格 / 18 次，只有同一冻结身份、同一 bundle/run 的 pilot gate 通过后才 append，不独立重跑 pilot。
- B 运行硬限额：40 个 B cell、60 次实际内核调用、累计模拟墙钟 3600 s、每格 120 s、并发 1。容器级编译格数上限 60 与 B 实际运行 40 格门禁分开；本矩阵只选 B tier 的 9 格。
- 24 星/3 轨道面、550 km/53°平台配置以及平台原 N/E/S/W 方向、动态可见性/拓扑与完整路由过程保留。区域仅限三个地面站点和六条有向 OD；路径不限制在区域，仍可经区域外卫星。负对照为六 OD、3 Mbps 稳态；竞争情景在六 OD 上经历 3→10 Mbps 增长、12 s 热点方向转移、16 s 消退，20 s 停止发流、仿真到 50 s。该流量是冻结的合成开发工作负载。
- 全网四信息臂（stale/now/common/candidate）各保留完整四方向候选和 mask；全网日志按每臂分别复核 ≥10 个可比较点、≥5 个竞争点及并发覆盖。四个解释性完整快照分支只用于机制解释，`max_branches=4` 指抽样干预数，不替代准入计数。五执行模式保留 `precomputed`，报告预计算范围，不把零前台 job 写成零生命周期成本。
- 主损失 D=30 s，目标 population [5,20] s、观察结束 50 s；完整逐包人口以全部 offered 为分母，明确 delivered、terminal loss 与行政删失，D=30 可从原始逐包记录重算。三个竞争种子 11/23/42 仅作开发区组描述，不作显著性推断；阴性对照 seed 7 保留。
- 最新定向实现测试 25 passed (0.18 s)，覆盖回放、结构准入、pilot gate、报告、六 OD 生成、模拟调用账本、四方向审计；修改 Python 文件 `py_compile` 通过。最终静态 compile/validate：42 容器格、9 B 格/57 调用，valid=true。WP-A 核心套件此前另有 102 passed；该长回归早于最后一轮 B gate/回放更改，不能替代此处最新定向验证。
- 当前没有 B VM run：实际 B 调用 0，B 模拟墙钟 0；release 未发布，预留唯一 run-id `wp-b-dev-20261001-01`。A 的 1061 s 外层 VM elapsed 保守计入跨包累计墙钟。修正后的历史 A 四出口结构调用上界为 83（旧冻结估算 78 使用了三出口假设），其真实内部调用数未知；不改 A 回执或声称 A 重跑。
- 当前阻塞：无。剩余步骤依次为更新冻结前账本 → 发布干净 commit → 唯一 B run（pilot 与剩余格同 run append）→ 按精确身份拉回验 receipt/manifest/文件集 → 独立只读复核 → 更新实际结果账本。FORMAL_RUN、确认种子、训练明确排除。
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
