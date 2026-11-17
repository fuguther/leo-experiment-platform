# 版本与运行状态查询

本文件提供状态查询入口，不以历史任务快照代表当前仓库或 VM 现态。操作规则见 [AGENTS.md](AGENTS.md)，平台导航见 [README.md](README.md)。

## 代码与 CI

```bash
git status --short --branch
git rev-parse HEAD
git worktree list --porcelain
```

远端 main、PR head、检查项和日志从 GitHub 实查；缓存 ref 相等不能证明远端新鲜度。每次验证记录实际 commit/tree、命令与结果，不在入口累积测试数量。

## 运行与证据

- 当前活动任务与授权从所属任务记录及实际进程核对，不按遗留“正在运行”段落判断。
- 发布、运行和回传按 AGENTS 中既有脚本执行；版本、输入、运行环境、自然终态与回执逐项绑定。
- `ANALYSIS/DEPLOYMENT-INDEX.jsonl` 是证据定位索引；索引行或 VERIFIED 不等于命令成功、原始证据完整或科学结论成立。
- 缺失原件、回执和未核实依赖仅限制相应结论，不阻断无关理论研究。
- 研究问题与命题恢复自 GitHub 研究仓库的正式状态。实现仓库不另设研究主线登记。
