# 长期实验契约

本目录只保留当前代码或测试直接依赖的契约和最小夹具，不保存实验实例、运行结果或一次性分析脚本。

| 目录 | 内容 | 消费者 |
|---|---|---|
| `t1-complete/` | T1 主契约、开发子契约、冻结 deadline 与时间对齐研究设计 | `t1_suite`、`t1_development`、analysis tests |
| `scene-calibration/` | 场景标定输入、输出契约与标定表 | `scene_load_calibration` 测试 |
| `isl-bandwidth/` | ISL 带宽压力判定契约 | `isl_pressure_decision` 测试 |
| `run-artifact-contract.md` | run artifact 的结构边界 | release/run/pullback 链 |

历史工作包的 brief、review、decision、finalization、运行结果和场景快照不进入活动目录。其复现身份由对应 Git commit、run-id、receipt 和原始证据确定。
