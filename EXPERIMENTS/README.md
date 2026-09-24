# 实验区

> 本目录下的 `EXP-*` 是逐 revision 生成的不可变证据快照，不是“当前实验仍获授权”或“当前应继续运行”的状态入口。执行前必须对当前 checkout 重算 manifest、finalization、authorization 和 receipt；当前路线从 `../AGENT-START-HERE.md` 进入。

Agent 不在这里手写完整配置。先提交一个紧凑请求，再按 runtime family 使用下文对应的 canonical 编译入口：通用/legacy 兼容工件使用 `CODE/experiment_platform/compile_experiment.py`，本仓可正式执行的 `leo_sim_v2` 使用 `python3 -m CODE.leo_sim experiment compile`。两者不得混用。

```text
EXP-.../
├── request.json
├── compile-report.json
├── run-manifest.json
├── analysis-request.json
├── resolved/             # legacy: *.config.json；leo_sim_v2: *.leo-sim.yaml
├── RUNBOOK.md            # 运行族边界与后续步骤；不能替代授权
├── attempts/
└── raw/                 # 默认不进 Git
```

`RUNS.csv` 当前只有表头，是保留但未启用的派生索引；为空不表示没有实验，也不得由 Agent 手工补写。运行是否存在、是否完成及其科研资格只由 manifest、run identity、effective receipt、artifact manifest 和 claim gate 判定。每个 arm×seed 必须有独立 resolved config 与 hash，禁止从目录名解析科研参数。

`authorization.json` 只能由 `CODE/experiment_platform/authorize_experiment.py` 根据 ACCEPTED 工作包
finalization 生成。它是可重算的执行凭证，不是可手写的状态标记；任何被绑定的
request、manifest、analysis、config、brief、decision 或 review receipt 变化都会使其失效。
