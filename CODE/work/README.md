# Agent 工作区

> `WP-*` 中的 brief、review、decision 和 finalization 只绑定各自 revision 与产物哈希，不是当前 Agent 任务书。安排新工作前先读 `../../AGENT-START-HERE.md`，并针对当前产物建立或核验对应 revision。

你以导师/领导身份提出目标、边界和决策；Agent 用可交接的版本化工作包执行。

```text
CODE/work/WP-.../
├── R01/
│   ├── brief.json
│   ├── producer/
│   ├── reviews/
│   └── decision.json
└── R02/                     # 只有 REVISE 后才建立
    └── ...
```

## 真实闭环

1. 生产者创建符合 `work-package.schema.json` 的 `brief.json`，`work_id` 不变，`revision` 从 1 开始。
2. 生产产物后计算 SHA256。每个审阅者在独立 session 中生成 `review-receipt.schema.json` 回执；回执必须绑定当前 `work_id + revision` 和至少一个产物 hash。
3. `producer:*` / `P-*` 与 `reviewer:*` / `R-*` 使用不同身份和 session 命名空间。生产者不得写自己的审阅回执。
4. 决策者核对回执文件 hash、产物 hash、身份独立性和 `brief.review_roles`，然后写入符合 `decision.schema.json` 的决定。
5. `ACCEPT` 要求所有承重必需角色均有绑定当前产物的 PASS 回执，且没有任何承重 BLOCK。不按多数票。
6. 任一承重 `BLOCK` 必须导向 `REVISE` 或 `STOP`。`REVISE` 在新的 `rNN/` 中重做，不修改旧产物、回执或决定。
7. 新版 `brief.json` 填写 `parent_revision` 和 `revision_reason`，所有审阅从新产物 hash 重新开始。

## 实验执行门禁

实验设计工作包固定要求 `cold_start`、`satellite_drl`、`adversarial` 三类审阅，brief 不能通过少声明角色降低门槛。其 `artifact_hashes` 必须绑定编译目录中的 `request.json`、
`compile-report.json`、`run-manifest.json`、`analysis-request.json` 以及 manifest
引用的每个 JSON/YAML 配置。审阅不能只绑 brief 或短报告。

ACCEPT 决定先由机器重算为 finalization receipt：

```bash
python3 CODE/work/finalize_decision.py \
  --brief CODE/work/WP-.../R01/brief.json \
  --decision CODE/work/WP-.../R01/decision.json \
  --out CODE/work/WP-.../R01/finalization.json
```

`finalize_decision.py` 先执行 work-package、review-receipt 和 decision 的完整结构校验，再重新计算产物和审阅回执 hash，并要求 `brief.review_roles`
的每个角色都有当前 revision 的 PASS。失败时不产生可用回执。该回执仍不能
直接启动实验；授权器还会重算整条证据链。

`work-package.example.json` 是 r01 工作包；`review-receipt.example.json` 展示非空 hash 绑定的 PASS；`review-receipt.block.example.json` 与 `decision.example.json` 展示 BLOCK 决定；`work-package.revision.example.json` 是保留 r01 后新建的 r02。示例已统一为 `experiment-request/v2`，其 hash 链必须在任何示例改动后同步重算。

低风险抽取和格式工作可由低成本 Agent 完成。研究方向、实验公平性、claim 支撑和论文 headline 使用高判断模型及异质审阅。
