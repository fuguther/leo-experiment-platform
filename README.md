# leo-experiment-platform

LEO 路由研究的受控实验环境。平台当前事实与未决正确性边界见 [STATUS.md](STATUS.md)，topic 级现态见私有 Issue #3；稳定工作规则见 [AGENTS.md](AGENTS.md)。

来源基线记录在 `SOURCE-COMMIT.txt`。`leo_sim/` 的代码身份由 run receipt 绑定，不以目录名或历史提交日期代替。

## 文档

| 文件 | 职责 |
|---|---|
| `STATUS.md` | 平台当前事实、已知缺口和下一步；不保存历史流水 |
| `CHARTER.md` | 平台本体与不变式 |
| `WORKING-MODEL.md` | 提交、复核、版本规则 |
| `lines/` | 当前研究线与能力边界 |
| `ANALYSIS/` | 当前规范、事件时间线和实验方法 |
| `EXPERIMENTS/contracts/` | 被代码或测试直接使用的稳定契约与最小夹具 |

历史工作包、一次运行结果、旧审批链和已完成实验实例不作为活动平台文件保留；需要追溯时从产生它们的 Git commit、run-id、receipt 和原始证据中读取。

## 结构

```text
CODE/
├── leo_sim/                # 仿真核心
├── experiment_platform/    # 编译、授权、分析、回放与核验
├── scripts/remote/         # release/run/pullback 执行链
├── tests/                  # 顶层跨模块测试
├── work/                   # 通用工作包 schema、复核与收口工具
├── data/                   # 有来源说明的输入数据
└── population_map/         # 人口场景输入

EXPERIMENTS/
├── contracts/              # 长期契约与最小测试夹具
├── templates/              # 请求模板
└── README.md               # EXPERIMENTS 目录边界
```

`CODE/` 是不可改名的导入根：源码使用 `from CODE...`，编译入口也依赖该目录。运行实例应写入 release/run 或显式实验输出位置，不写回契约目录。

## 平台边界

平台负责：

- 可重放的仿真事件、队列、链路、决策和指标；
- 编译、授权、release、run、receipt、pullback 与哈希链；
- 分析器接口、统计口径、回放和受控反事实能力；
- 使未来研究能够用明确 commit、输入和回执复核。

平台不负责：

- 保存每次研究的临时脚本、审查对话或工作流水；
- 把派生摘要当成原始运行证据；
- 用绿测试、目录存在或 CI 通过代替科研结论；
- 在活动代码中长期保留已被统一口径替代的一次性分析器。

## 验收

```bash
python3 -m pytest -q
```

最小闭环：

```bash
python3 -m CODE.leo_sim config validate CODE/leo_sim/profiles/smoke.yaml
python3 -m CODE.leo_sim run --config CODE/leo_sim/profiles/smoke.yaml --out out/smoke
python3 -m CODE.leo_sim receipt verify out/smoke
```

这些命令验证平台能力和证据格式，不构成正式实验或科研结论。

### 能力入口

```bash
python3 -m CODE.experiment_platform.fold_decision_ledger   --decision-log out/run-decisions.jsonl   --timeline-log out/run-timeline.jsonl   --out out/run-ledger.json

python3 -m CODE.experiment_platform.replay_counterfactual   --config CODE/leo_sim/profiles/smoke.yaml   --decision-id 18 --forced-action S --out out/replay.json
```

反事实重放要求 `learning.algorithm=none`；冻结观测与强制动作的分支点见 `minimal_branch_compare` 及其测试。

### 日常维护与 T1 同步

- 每次改动前后运行 `python3 -B CODE/scripts/maintenance_check.py --repo .`。
- 验收本机回传证据时增加 `--verify-evidence --evidence-root <实际证据根>`。
- 只从干净、已提交、已推送的完整 commit 构造不可变 release。
- 诊断/开发运行必须指定已核验 release 和新 run-id；formal 仍遵守编译、独立审阅、明确授权、运行和回执验证。
- 回传只使用 `CODE/scripts/remote/pull-release-results-remote.sh`，不得手改 receipt 或部署索引。
