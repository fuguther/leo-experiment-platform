# leo-experiment-platform

LEO 路由研究的**受控实验环境**。

来源:`leo-direct-sim` @ `241627856886934cbbf2a0820982bef2018e6249`(见 `SOURCE-COMMIT.txt`)

含 PR #221 平台审计成果(F2 CLI 链路打通、独立重算的阶段分离、四份平台事实基线文档)。

---

## 文档

| 文件 | 内容 | 何时改 |
|---|---|---|
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
├── leo_sim/              # 仿真核心 —— 与源仓库字节级一致
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
→ 917 passed, 1 skipped
```

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
退役旧线(删 `q0_tiny.py` / `info_ladder_tiny.py`)之后本仓库的代码身份已分叉为
`4402081f…`,不再等于源仓库。运行回执的证据链在**本仓库内部**仍然自洽可复现。

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
