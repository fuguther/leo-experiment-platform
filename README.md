# LEO 受控实验平台

本仓库拥有可执行仿真核心、实验编排、release/run/receipt 和证据回传实现。研究问题、算法目标与科学状态以 [研究仓库 main](https://github.com/fuguther/leo-routing-domain-map/tree/main) 的 `reasoning/state.yaml` 及配套研究规则为准。

操作规则见 [AGENTS.md](AGENTS.md)，版本与运行状态查询见 [STATUS.md](STATUS.md)。

## 源码与资产职责

| 路径 | 职责 |
|---|---|
| `CODE/leo_sim/` | 拓扑、链路、队列、包事件与路由执行 |
| `CODE/experiment_platform/` | 实验编译、授权、诊断与分析入口 |
| `CODE/scripts/remote/` | 固定版本发布、运行与回传 |
| `CODE/tests/`、各模块 `tests/` | 机制、协议及回归测试 |
| `ANALYSIS/` | 测量合同、分析实现与适用边界 |
| `EXPERIMENTS/` | schema、模板及实验合同 |

`SOURCE-COMMIT.txt` 记录模拟器导入来源；实际代码身份由本仓库的精确 commit/tree 确定。`CODE/` 是 Python 导入根，不随目录整理改名。契约、实现与被测试引用的必要 fixture 进入 Git；运行实例、模型、原始数据、凭据和结果按来源、许可及证据合同另行存放。

## 平台要求

- 可复现：固定代码、配置、输入、依赖与随机化范围，验证实际可重放程度，不只凭相同 seed 承诺逐位一致。
- 可追溯：结果绑定唯一代码、输入、release、run 和 receipt 身份。
- 可干预与归因：对照固定非目标因素，验证共同前缀与干预范围；后续分支各自演化。
- 代价可量化：记录通信、计算、等待和共享服务资源成本。
- 实现修改按所属任务审阅与回归；不篡改结果，不为未明确的未来需求增设能力。

## 开发验证

```bash
python3 -B CODE/scripts/maintenance_check.py --repo .
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests CODE/scripts/tests CODE/scripts/remote/tests ANALYSIS/tests -q
```

这是代码与工程验证命令，不授予实验资格。测试结果须绑定实际提交和命令；本地通过、服务器 CI、诊断完成、formal 资格与科学结论分别报告。

## 运行能力导航

以下模块均按其固定版本的参数与输入合同使用；涉及仿真的命令只在 VM 上经授权运行，不直接在 Mac 启动：

- 决策账本：`CODE.experiment_platform.fold_decision_ledger`。
- 单动作反事实：`CODE.experiment_platform.replay_counterfactual`。
- 冻结共同前缀比较：`CODE.experiment_platform.minimal_branch_compare`。
- 计算压力诊断：`CODE.experiment_platform.cost_pressure_probe`。

能力、时间字段与条件限制见 [能力表](ANALYSIS/CAPABILITY-TABLE.md)、[测量协议](ANALYSIS/T1-MEASUREMENT-PROTOCOL.md)、[时间语义合同](ANALYSIS/TIME-SEMANTICS-QUARTET.md)。模块存在不证明相应运行成功，更不证明算法性能。
