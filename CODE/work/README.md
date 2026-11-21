# Agent 工作区工具

`CODE/work/` 只保存通用工作包协议和收口工具：

- `work-package.schema.json`、`review-receipt.schema.json`、`decision.schema.json`
- `finalize_decision.py`
- 对应示例和 `tests/`

`WP-*` 实例不进入活动平台树。具体任务的 brief、review、decision、finalization 和运行材料由 Issue、Git 历史、run-id 和原始证据保存。需要复现旧结果时，从产生它的固定 commit 读取，不在 main 持续维护第二份历史目录。
