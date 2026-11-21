# ANALYSIS 目录

这里只保留当前方法、事件时间线、场景与流量规范，以及可执行分析工具；历史审计流水和一次性结果快照不进入活动树。

| 文件 | 职责 |
|---|---|
| `CURRENT-EVENT-TIMELINE.md` | 内核发出、入队、服务、传播与交付的事件顺序 |
| `T1-MEASUREMENT-PROTOCOL.md` | T1 测量口径、门禁与不可声称项 |
| `TRAFFIC-MODEL-SPEC.md` | 流量和 OD 输入规范 |
| `BURST-DESIGN.md` | burst 变体的设计与验证边界 |
| `ASYNC-SCHEDULING-DESIGN.md` | 异步计算、安装和查询的设计合同 |
| `T1-PRESSURE-WINDOW.md` | 既有压力窗口特性记录 |
| `paired_analysis.py` | 配对证据的独立分析入口 |
| `claims/claim.schema.json` | claim 结构合同 |

历史审计结论、旧 scene 快照、废弃工作包和阶段性报告由固定 Git commit、run-id、receipt 和原始证据恢复，不再在这里保留易漂移副本。
