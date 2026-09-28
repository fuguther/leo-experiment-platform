# AGENTS.md — leo-exp-main 工作规则

> 本文件保存**稳定规则**，不记录当前 SHA / 实验进度。
> 适用目录：`/Users/lge/Desktop/topic/leo-exp-main`（T1 冻结分支与异步设计）。

## 1. 执行位置（用户 2026-09-28 指令，硬规则）

**所有实验都在 VM 上跑，不在本机跑。**

| 类型 | 位置 | 说明 |
|---|---|---|
| 代码修改、编译、单元/回归测试（pytest）、静态检查、文档 | **本机** | 不产生科研数值 |
| **实验**：`t1_suite compile/validate/run/resume/report`、`time_alignment_compare`、`execution_compare`、`benchmark_decision`、任何产生诊断/压力/计时数值的运行 | **VM（唯一位置）** | 本机不得再作为实验证据来源 |
| 只读复核、比对已拉回的产物 | 本机 | 允许 |

本机历史上已经跑过的 `out/t1/**` 产物一律视为**本机历史证据**，不得再作为新结论的依据；需要数值时必须重新在 VM 上跑。

## 2. VM 事实（已验证）

```
ssh vm                    # 192.168.200.23:12130, user liguang13, key ~/.ssh/id_ed25519_lab
host: cuda-liguang13       python: 3.12.3
env:  source /opt/anaconda3/bin/activate /data/liguang13/conda-envs/leo-i39
磁盘: /data 471G 可用
```

- **你的正式部署（只读，禁止写入）**：`/data/论文/leo-direct-sim`。
  它是 push-remote.sh 同步的部署树（不是 git 检出），含正在使用的 `CODE/Results/` 与 `EXPERIMENTS/`。
- **T1 隔离实验根（本任务唯一写入位置）**：`/data/论文/leo-t1-wt/`
  - `CODE/`：由本仓库当前提交同步过去的代码
  - `Results/`：T1 实验输出
  - `launch.json`：本次同步的 HEAD、dirty 状态、rsync 摘要

## 3. 实验流程（每次实验必须逐条遵守）

1. **提交**：本机形成 clean commit；记录 `git rev-parse HEAD` 与 `git status --short`。
2. **同步**：rsync `CODE/` → `/data/论文/leo-t1-wt/CODE/`，排除 `Results/`、`out/`、`__pycache__/`、`.pytest_cache/`、`*.log`；写出 `launch.json`（HEAD + dirty + 时间）。
3. **运行**：在 VM 上用 conda 环境执行；输出写 `/data/论文/leo-t1-wt/Results/<run-id>/`。
4. **拉回**：把产物 rsync 回本机 `out/vm/<run-id>/`（`out/` 已 gitignore）。
5. **复核**：本机只读检查产物内嵌的 `identity`（commit/dirty/执行链哈希）；若与本机提交不一致，该次实验作废重跑。
6. 任何一次实验报告必须写明：VM 主机名、执行链 SHA、run-id、拉回路径。

## 4. 技术栈与门禁（沿用 leo-vmdeploy/AGENTS.md）

- 正式实验只能走：编译 → 审阅 → 授权 → `CODE/scripts/remote/run-remote.sh` → 自然结束回执 → 分析重算。
  **本任务不做正式运行**（无授权），VM 上只做有界诊断。
- `Results/`、`out/`、`leo_sim_out/`、`remote.env` 一律 gitignore，永不入库。
- 覆盖/删除任何已跟踪路径前必须逐条列出并等用户批准。
- 失败 fail-loud，禁止静默回退；禁止删测试或放宽断言让结果"看起来绿"。
