# VM 实测验收记录与它抓到的三个缺陷

## 1. 结论

**R11b 关闭**：用 R2 版本在 VM 上完成 部署 → 授权 → 运行 → 回执 → 拉回 → 本地重验 → 既有正式资格分析，
两条最小链路（零决策 / 非零决策）全部 success，分析入口返回 `VERIFIED`。

## 2. 部署源：本机就有规范工作区

前几轮"没有 VM"的结论错了两层：VM 本身可用；而且**本机就有规范部署源**
`/Users/lge/Desktop/topic/leo-direct-sim`（父研究仓库，含 `LITERATURE/PAPER/AGENTS.md/DECISIONS.md/NOTES.md/LICENSE`，
被部署的 `046eea35` 就在它的历史里）。从它 clone 出的部署树满足 `deployment_guard` 的完整工作区契约。
`leo-experiment-platform` 的 `SOURCE-COMMIT.txt` 写的 `24162785…` 正是该仓库 HEAD，不是失效指针。

## 3. 两轮 VM 实测

| | 第一轮（R1） | 第二轮（R2） |
|---|---|---|
| 冻结提交 | `324c8ee882f243f59718df8864e7cddcf63481b6` | `4f06dfcd0d492e9cdaa7a2fd34faf61f0903056a` |
| code_sha256 | `693796433f…` | `40e0eeb65a5f6aafa85f4a252e4cb07ec469c6be1227f7fec5b9d807c6828100` |
| 部署提交 | `80d935f8b35f90345dda961a86e675bc15ed3161` | `82e47354d1939410e5a6e83f826ac6dca428dc34` |
| source_tree_sha256 | `50ce71bc9b3d0ac149e3132a12f2e1f426b9cf35a80638507bfa632feb42bcb5` | `4b6d6e0692b26ebece7d72bf720b5df0072d8316d05a3013f48dcd683c24ee17` |
| control | success / exit 0 / **v5** | success / exit 0 / **v5** |
| treatment | success / exit 0 / **v6** | success / exit 0 / **v6** |
| 本地拉回门 | v6 通过、**v5 被误拒**（见缺陷 1、2） | **两条都通过** |
| 分析入口 | 被拒（见缺陷 3） | **VERIFIED / 2 runs** |

R2 的 launch nonce：control `50e5369c3760c57abe48dda6e39c8081`、treatment `0fd79c4ba9cb4728c396efb352b3e4f9`。

两条 run 的回执：`code_sha256 = 40e0eeb65a5f…`、`research_eligible = true`、`verification_errors = []`、
`source_git_commit = 82e47354…`。
分析 manifest：`schema leo-sim-v2-analysis/v1`、`status VERIFIED`、
`authorization_verification = strict_recomputed`（**不是** bound_posterior 回退）、
`analysis_mode = posterior_governed_runtime`（本地 Python 3.14 vs VM 3.11 的既定路径）、
`claim_status = READY_FOR_INDEPENDENT_CLAIM_REVIEW`、`verified_run_ids` 两条俱全。

## 4. VM 实跑抓到、本地夹具抓不到的三个缺陷（R2 已修）

1. **`pullback.py` 的治理回执 schema 常量写错**：写成 `leo-sim-v2-governance-receipt/v2`，
   而生产者 `remote_job.V2_GOVERNANCE_SCHEMA` 与 `v2_analysis.GOVERNANCE_SCHEMA_V2` 都是
   `leo-sim-governance-receipt/v2`。本地每个夹具都用本模块自己的常量去构造，于是**全部与错字自洽**，
   测试全绿；只有真实 VM 结果能暴露。修法：常量改正，并新增断言把它与两个生产者钉在一起。
2. **外部启动见证按错的名字查找**：代码找 `<launch_nonce>.json`，而 `pull-results-remote.sh`
   落盘的是 `<run_id>.json`，所以**每一个真实拉回结果都被误拒**。修法：两种拼写都接受。
3. **编译器与分析器的 design accounting 不一致**：编译器在 `design_accounting` 里新增
   `one_change_policy_check`，分析器仍做整体相等比较 →"带 design 块的请求编译通过、分析期必炸"，
   正是平台自己记录的 **S-2**。修法：分析器对它**能重算的字段**仍要求逐字相等，
   对无法重算（需要已解析叶子路径）的扩展做**形状校验**，并拒绝其他未知字段。

这三条都写进了回归测试：`test_pullback_authority.py` +2 条、`test_design_accounting_extension.py` +4 条。

> 教训（写给下一轮）：**本地夹具自洽 ≠ 契约正确**。凡是"生产者—消费者"共享一个常量或一个字段集的地方，
> 断言必须指向**生产者**，不能指向被测算模块自己的常量；而端到端必须在真机上至少跑一次。

## 5. R2 同时修掉的冷启动复核 R22 限制

- `verify_burst_transform` 的经度覆盖由 3 个点改为 **1 度网格 360 点（2160 探针）**；
  复核者构造的"只在 `{0,90,−180}` 之外泄漏"的变换现在会被抓住（已加参数化测试）。
- `demand.burst_multiplier` 在从不施加突发的模式下（无窗口）不再静默保留：非默认值即拒绝。
- `materialization_report` 对 `mlab_auto/population_gravity` 给出**可操作**的错误（要求传 `declared_cells`）。

## 6. 仍然未关闭的（如实）

- 部署树是"父工作区 + R2 `CODE/` 覆盖"，提交落在父仓库（`82e47354`），不是平台仓库自己的提交；
  两侧 `code_sha256` 相同（`40e0eeb65a5f…`），但 git 提交身份不同，这一点必须随证据一起转述。
- 父仓库独有的 12 个 `CODE/` 文件（legacy 结果分类/整理工具、`info_ladder_tiny.py` 等）随之从 VM 工作区移除，
  可从 `/data/论文/leo-direct-sim/.remote_runtime/deploy-backups` 取回。
- `SOURCE-COMMIT.txt` 指向父仓库 HEAD `24162785…`（该提交不在平台仓库对象库中）——这是**血缘指针，不是缺陷**。
- 冻结树内仍有一份既有的 superseded 授权（`WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R01/authorization.json`，非本次加入）；
  平台的门会在使用时按 `code_sha256` 复核拒绝它。
- 冷启动检出用 git worktree 而非独立 clone；`verify_burst_transform` 仍是**点检**（现在是 360 点），
  不是全经度证明；TensorFlow 缺失导致 1 条测试 skip。
- 本轮只做平台链路验收，**未启动任何研究性能实验**。
