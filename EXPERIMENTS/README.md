# 实验契约区

活动仓库只保存实验请求模板、正式 run-artifact 契约和长期测试契约。历史 `EXP-*` 实例、attempts、resolved 配置和 raw 输出不进入活动树；需要追溯时使用产生它们的 Git commit、run-id、manifest、receipt 和原始证据。

```text
EXPERIMENTS/
├── contracts/              # 长期契约与最小测试夹具
├── templates/              # 请求模板
└── README.md
```

## 执行边界

- 通用/legacy 兼容工件使用 `CODE/experiment_platform/compile_experiment.py`。
- 当前 `leo_sim_v2` 使用 `python3 -m CODE.leo_sim experiment compile`。
- `authorization.json` 只能由 `CODE/experiment_platform/authorize_experiment.py` 根据已接受的工作包 finalization 生成。
- 每个 arm×seed 必须有独立 resolved config 和 hash；参数只能从合同读取，不能从目录名推断。
- 运行是否存在、是否完成以及是否具有科研资格，只由 manifest、run identity、effective receipt、artifact manifest 和 claim gate 判定。
