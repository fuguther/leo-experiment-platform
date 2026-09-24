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
→ 917 passed, 1 skipped, 1 warning in 314.40s (0:05:14)
```

`CODE/leo_sim/` 的 `code_sha256()` 与源仓库 `2416278` 一致(`ffcad9fc…`)—— 既有运行回执的证据链可复现。
