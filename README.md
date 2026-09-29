# leo-experiment-platform

LEO 路由研究的**受控实验环境**。

仓库级代码身份、发布流程和当前阻塞项见 [STATUS.md](STATUS.md)。

来源:`leo-direct-sim` @ `241627856886934cbbf2a0820982bef2018e6249`(见 `SOURCE-COMMIT.txt`)

含 PR #221 平台审计成果(F2 CLI 链路打通、独立重算的阶段分离、四份平台事实基线文档)。

---

## 文档

| 文件 | 内容 | 何时改 |
|---|---|---|
| [STATUS.md](STATUS.md) | 当前仓库状态、不可变发布/运行入口和未满足的证据门 | 发布流程、同步状态或阻塞变化时改 |
| `CHARTER.md` | 平台本体与五条不变式 | 平台本体变了才改 |
| `WORKING-MODEL.md` | 提交、复核、版本 | 工作方式变了才改 |
| `lines/` | 研究主线(登记表 + 各线) | 线的增删改判死 |

---

## 结构

```
CHARTER.md                # 平台本体与五条不变式
WORKING-MODEL.md          # 提交、复核、版本
lines/                    # 研究主线登记与各线文档

CODE/                     # 导入根,不可改名
├── leo_sim/              # 仿真核心 —— 基线源自源仓库；本分支已扩展（见「与源仓库的关系」）
├── experiment_platform/  # 编译 / 授权 / 指标重算
├── scripts/remote/       # 部署与受控执行(仅 .template,无凭据)
├── tests/                # 顶层测试
├── work/                 # 工具模块(被测试导入)
├── data/                 # geoip / traffic
└── population_map/       # 场景生成数据

ANALYSIS/                 # 指标分析 + claim schema + 平台事实基线(模块名不可改)
                          #   PLATFORM-AUDIT-REPORT / CURRENT-EVENT-TIMELINE
                          #   TRAFFIC-MODEL-SPEC / BURST-DESIGN
EXPERIMENTS/              # 契约与模板(不含实验实例)
├── templates/            #   请求模板
├── contracts/            #   运行工件合同
└── experiment-program.yaml
```

**`CODE/` 这一层不能去掉。** 源码中:

- 测试使用绝对导入 `from CODE.leo_sim import ...`、`from CODE.work.finalize_decision import ...`
- `CODE/experiment_platform/compile_experiment.py:343` 硬编码 `PROJECT_ROOT / "CODE"`

改名会同时破坏导入与编译路径。

---

## 与源仓库的关系

`leo_sim/` 是**字节级复制**。证据链要求 `code_sha256` 可复现,因此:

- 不在本仓库对复制来的源码做"顺手优化"
- 需要改动时,按 `CHARTER.md` 的边界判断是否属于平台本体

> **📌 状态更新 (2026-09-28)**:上面两条是 `main` 的规则。在当前分支
> `t1/frozen-branch-and-async-design` 上,`leo_sim/` **已不再是字节级复制**:
> T1 工作新增 `time_alignment.py` / `async_routing.py` / `inference.py`,
> 并扩展 `kernel.py`(+1218 行)、`control.py`、`counterfactual.py`;
> 相对 `origin/main` 共 21 个文件、+5218/-61 行。这是本分支的**有意分叉**,
> 交付身份与证据见 `CODE/work/WP-T1-COMPLETE/STATUS.md`(现行证据身份 `8a31496`)。
> 回 `main` 时上述规则照旧成立。

---

## 未包含

| 排除项 | 理由 |
|---|---|
| `CODE/Results/` | 运行结果,不是平台 |
| `remote.env` | 机器凭据 |
| `*_legacy_results.py` 及其测试 | 遗留结果整理器 |
| `EXPERIMENTS/EXP-*/` | 实验实例(仅保留被测试引用的一个 RUNBOOK fixture) |
| `ANALYSIS/claims/RESEARCH_CLAIMS.yaml` | claim 实例 |
| `PAPER/` | 依赖方向为 PAPER → 平台,属研究侧 |

判定规则:**契约进,实例不进。** 见 `CHARTER.md` 第三节。

---

## 验收

```
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
→ 1020 passed, 1 skipped        (本机 Python 3.14.2, 2026-09-26)
→ 1278 passed, 2 skipped        (本机, 2026-09-28, T1-COMPLETE 分支; 含本分支新增测试)
```

若在 VM 的部署树上跑,同一范围**会有 3 条失败**——原因不是代码:部署树是"父研究仓 + 平台 `CODE/`
覆盖"、**没有 `.git`**,而其中两条测试依赖 `git ls-files`,第三条依赖父仓与平台仓库里同一个实验
实例的内容一致。逐条定位见 `ANALYSIS/VM-RUN-20260926.md` §5。

### 最小闭环(实跑,非"测试通过")

```
python3 -m CODE.leo_sim config validate CODE/leo_sim/profiles/smoke.yaml
  → {"status":"ok","sha256":"93431539…"}

python3 -m CODE.leo_sim run --config CODE/leo_sim/profiles/smoke.yaml --out out/smoke
  → conservation_ok: true

python3 -m CODE.leo_sim receipt verify out/smoke
  → {"status":"verified"}
```

回执中的 `code_sha256` 由 `receipt.code_sha256()` 决定,覆盖 `CODE/leo_sim/*.py`
(**非递归**,不含 `tests/`)。它与源仓库 `2416278` 的一致性是**历史事实,不是不变式**:
退役旧线(删 `q0_tiny.py` / `info_ladder_tiny.py`)之后本仓库的代码身份已经分叉。

**这个值随任何 `CODE/leo_sim/*.py` 变更而变,包括只改注释。** 因此本文不再写死一个数字
(旧版写的 `4402081f…` 早已过期,按它复核必然 FAIL)。复核一律以实跑为准:

    python3 -c "import sys;sys.path.insert(0,'.');from CODE.leo_sim import receipt;print(receipt.code_sha256())"

截至 2026-09-26:`origin/main = 1fad58f` 的 clean 身份是 `57cca0ed…`;VM 当前部署
(`/data/论文/leo-direct-sim`,部署自 R5 冻结提交 `5fb89de0`)的身份是 `0ab9bb44…`——
**两者不等**,差异只在 `trace.py`/`__main__.py` 的声明文本与两个测试文件(AST 归一化后
逐节点相同,数值常量多重集相同),但字节身份不同,所以 R5 的授权**不能在 main 上重放**。
逐条对账见 `ANALYSIS/VERSION-EVIDENCE-RECONCILIATION.md`。

### 能力入口(T1 证据链)

研究主线的「可归因」与「可干预」两条不变式,此前只有实现、没有生产入口
(`decision_ledger.build_ledger` 的 13 处调用全在测试里;`counterfactual` 无非测试调用者)。
以下两个入口补上了这一步:

```
# 可归因:把决策流与时间线折叠成每决策的十一时刻账本
python3 -m CODE.experiment_platform.fold_decision_ledger \
  --decision-log out/run-decisions.jsonl \
  --timeline-log out/run-timeline.jsonl \
  --out out/run-ledger.json

# 可干预:严格配对的反事实重放(同一 trace/config/seed,只改一个动作)
python3 -m CODE.experiment_platform.replay_counterfactual \
  --config CODE/leo_sim/profiles/smoke.yaml \
  --decision-id 18 --forced-action S --out out/replay.json
```

两者都**不修改引擎**,且都要求 `--out` 指向不存在的路径(拒绝静默覆盖)。
反事实重放要求 `learning.algorithm = none`(确定性路由器)。

### 逐包最小机制比较(T1-FROZEN-BRANCH)

`frozen` 观测与 `forced_actions` 此前在内核里**互斥**,唯一可用的近似是把反事实跑在
`refresh` 下——那是**另一个分支时刻**,无法与 frozen 基线配对。本轮解除了这条互斥:
冻结分支点就是观测时刻 `obs["t_observe"]`,强制动作必须落在该观测记录的合法集内,
否则 fail-loud。分支前状态由决策指纹与分支时刻之前的全部 timeline 行双重证明,
分支后两条队列各自独立演化。

```
# 一对分支的逐包比较:候选到达/竞争资源时刻、目标包面前工作量、真实动作代价
python3 -m CODE.experiment_platform.minimal_branch_compare \
  --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
  --decision-id 2 --forced-action S --out out/branch-compare.json

# 成本与压力探针:一次只动一个叶子,给出每档的移动量与被标记的越界移动
python3 -m CODE.experiment_platform.cost_pressure_probe \
  --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
  --out out/cost-pressure.json
```

有界的每星计算资源:`execution.compute_servers_per_satellite`(默认 0 = 无界,与历史行为
逐位相同;`>0` 时每星是确定性的 N 服务器池,等待与服务分开记录在 timeline 的
`compute_wait` 里程碑上)。它要求 `compute_delay_s > 0`,否则配置报错而不是静默无效。

**本轮改动过 `kernel.py` 与 `config.py`,因此代码身份已再次变更**(见上);任何既有授权
都不可复用,必须重新 compile + authorize。设计与对账见
`ANALYSIS/CAPABILITY-TABLE.md` / `ANALYSIS/VERSION-EVIDENCE-RECONCILIATION.md` /
`ANALYSIS/TIME-SEMANTICS-QUARTET.md` / `ANALYSIS/ASYNC-SCHEDULING-DESIGN.md`。
