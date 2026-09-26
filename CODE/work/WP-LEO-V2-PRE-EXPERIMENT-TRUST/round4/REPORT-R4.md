# 第四轮：两处小修 → 本地冻结提交 → VM 验收

| 字段 | 值 |
|---|---|
| **冻结提交 SHA** | `324c8ee882f243f59718df8864e7cddcf63481b6`（分支 `freeze/pre-experiment-trust-r1`，20 个文件，工作树干净） |
| 实例提交 SHA | `1b8ef91081accc2c989e6620d1b41678d0064df2`（在其上加入已编译/已授权的两个验收案例；代码身份不变） |
| **code_sha256** | `693796433f1e98ecb8c039c4725476df9fbd48795df2a0da98d87d73a726d974` |
| **execution_chain 汇总** | `d3f283d49dad4ef716a7ccf72342a572243133901d77b46cce616bda186f1e15` |
| **冻结集合汇总哈希** | `e1b75b8b7ccd9dac3daff7a53b54423450db9a5569d6081899aa48252750e9ad`（20 文件，规则见 FREEZE-SCOPE §3） |
| 原工作树 | HEAD 仍为 `98c858f`；未提交改动与唯一研究材料保持原样（树哈希 `8edf1844…` 未变） |
| 放行表 | `release-table.json` v4 |

---

## 1. 第一项：删除"突发窗实际零包"的硬拒绝

**改动**（`CODE/leo_sim/trace.py`）：`materialization_report` 不再把"声明窗口内零包"计入 `problems`。
结构性检查全部保留（模式不支持 burst、窗口越界、倍率 ≤ 1、生成器未施加变换 —— 后两者仍抛 TraceError），
零包改为**诊断**：

- `burst.packets_inside_window == 0`、`burst.applied == false`；
- `burst.zero_packet_diagnostic`：原始样本、种子、`expected_packets_if_burst_applied / _not_applied`，
  以及一句定位：`where_discriminating_power_is_judged = "the pre-declared experiment design, not this compiler"`；
- `burst.applied_reason` ∈ `NO_PACKET_IN_WINDOW / MULTIPLIER_IS_A_NOOP / WINDOW_OBSERVED`。

**不新增门禁**：没有开关、环境变量、阈值或 allow-list（独立复核 C4 专门查过）。

**命名复现**（seed 1 / 0.16 Mbps / 8 Mbit / 窗 [10,20) / 倍率 5）：

```text
改前（round-3 状态，在 /tmp 影子树复现，代码只有这一处差异）
  TRACE COMPILE FAILED: trace materialization does not match the resolved config:
  a burst window [10.0, 20.0) is declared but ZERO of the 3 emitted packets fall
  inside it (declaration predicts 1.0 in-window packets at seed 1):
  the treatment is unobservable in this sample; raise the rate or widen the window
改后
  compile 通过；problems == []
  发出 3 包（t=21.048134 / 46.990947 / 54.847124），窗内 0 包
  transform.mismatched_probes = 0
  intensity.status = UNDECIDABLE（E_burst=1.0, E_base=0.2）
  raw_sample_preserved = {inside_window: 0, all_emitted: 3, note: "never replaced or re-drawn"}
  applied_reason = NO_PACKET_IN_WINDOW
  trace.csv sha256 = 2040b658ba482ac65a62a15a5ee8368d314d8d86507abcb5d8ed0e44af63e4f7
```

同一份改前代码留在 `/Users/lge/Desktop/topic/leo-before-shadow-zero-packet`（仓库之外），
差异见 `round4/before-after-zero-packet.diff`（恰好一个 hunk）。

**同步修正的旧测试**：`test_a_declared_burst_that_no_packet_observes_is_refused_by_compile`
→ `..._is_reported_not_refused`，断言逐条对映（原断言无一被删），并改用命名复现的夹具
（旧夹具 1e-6 Mbps 产生的是**空 trace**，判别不出本轮修的那个字段）。

---

## 2. 第二项：FREEZE-SCOPE.md 重写（由生成器产出）

`round3/FREEZE-SCOPE.md` 现由 `round4/generate_freeze_scope.py` 生成，三节分开：

| 节 | 内容 | 关键数字 |
|---|---|---|
| §1 运行代码哈希覆盖 | `receipt.code_sha256()` = `CODE/leo_sim/` 下**非递归** `*.py`，逐文件名 + 字节 | 31 个文件，聚合 `693796433f…` |
| §2 执行链哈希覆盖 | `governance.EXECUTION_CHAIN_PATHS` 的 6 个显式路径 | 聚合（sort_keys + compact JSON）`d3f283d4…` |
| §3 版本复现所需测试与其他依赖 | 8 个测试文件 + 2 个其他依赖 | 冻结集合 20 = 8（运行哈希覆盖）+ 2（执行链覆盖）+ 8（测试）+ 2（其他） |

**测试需要冻结，但不在 `code_sha256` 的覆盖范围内**，文档这样写明；**未修改哈希算法**
（`code_sha256` 的 body 与 HEAD 逐字节相同，独立复核 C9 验证）。

路径、数量、**行号**与全部哈希由生成器从实际函数与 `git status` 读出（行号用 `function_lines()`
从源码解析，避免上一版写死 `receipt.py:152-158` 而失效），并新增自检：文档列出的文件集合
按 `code_sha256()` 自己的算法必须复现出该函数的返回值，否则生成器直接退出。

---

## 3. 独立复核（作者自检不算复核）

四份回执 + 一份冷启动回执，全部由**独立子代理**在各自上下文里跑代码产出：

| 回执 | 对象 | 结论 |
|---|---|---|
| `round4/review-A-zero-packet.json` | 零包硬拒绝的删除 | **PASS**（C1–C8）；发现 1 个真缺陷 + 4 条弱点 |
| `round4/review-A2-delta.json` | 上述缺陷的修复 | **PASS**（D1–D6） |
| `round4/review-B-freeze-scope.json` | FREEZE-SCOPE.md 第一版 | **FAIL**（C2：行号引用失效）+ 3 条弱点 |
| `round4/review-B2-delta.json` | 行号修复 | **PASS**（D1–D6），并用"在副本里插入 3 行看行号是否 +3"证伪了旧写法 |
| `round4/coldstart-review.json` | 冻结提交 `324c8ee8` 的全新检出 | **PASS**（F1–F9 全 PASS，reviewed_sha 与冻结提交逐字一致，criteria_sha256 = `e24d2529…b36c`） |

**复核发现并已修的真缺陷**：`raw_sample_preserved.all_emitted` 曾写成 `len(inside)`，
与 `inside_window` 恒等 —— 对 3 包的 trace 报 0。现由调用方传入发出包数。

**复核发现的其余弱点及处置**：行号引用（已改为动态）、文档措辞与手写策略文本的矛盾（已改写）、
执行链聚合的 JSON 分隔符未写明（已写明）、`_r4/shadow` 全平台副本留在仓库内的可追溯性隐患
（已移出仓库到 `/Users/lge/Desktop/topic/leo-before-shadow-zero-packet`）、
扫描测试文档称 400 而循环到 230（已改为 `SCAN_SEEDS = 400` 并同时断言种子 222 与 227）、
`applied` 语义重叠（新增 `applied_reason`）。

**独立性披露**：复核者是**同族模型的独立会话**，不是人工或机构评审（平台已知限制 L13/L20）。
它们的价值在于**独立重跑**（包括在 /tmp 副本上做对抗实验），不在于身份。

### 3.1 冷启动复核的判定与它查出的限制

**判定**：在 `/Users/lge/Desktop/topic/leo-coldstart`（冻结提交 `324c8ee8` 的全新检出）中，
F1–F9 全部 PASS。要点：

- `git status --porcelain --untracked-files=all`（以及 `--ignored`）在复核开始前为空；
- 全量测试**跑了两次**：`982 passed, 1 skipped`（618.17s / 598.14s，计数一致）；
  第二次挂了一个 **python audit hook**，任何对原工作树的非 `.git` 访问都会抛错 —— 实测 `outside=0, blocked=0`，
  即**冻结代码与它的测试可证明不读原工作树**；
- `code_sha256() = 693796433f…`，并用"在 `git archive` 副本里给 trace.py 加一行注释 → 哈希变化、
  动 tests/profiles → 哈希不变"证明它是**算出来的**而不是写死的；
- 命名的零包案例由复核者**自己的 harness** 复现（不是跑作者的测试）；
- `git show --name-only 324c8ee8` 与 FREEZE-SCOPE §3 的 20 个路径**集合相等**（双向差集为空）。

**它查出、本轮未修复的限制**（记入放行表 R22；修复会改变 `code_sha256` 从而作废已复核的冻结提交）：

1. `verify_burst_transform()` 只在 3 个经度 × 6 个时刻做点检 —— 复核者构造了一个"只在
   `{0, 90, −180}` 之外泄漏突发"的 `_rate_multiplier`，trace **仍然编译通过**。
   因此它是**必要条件检查，不是全经度证明**；其 docstring 的 "for every longitude" 表述强于实现。
2. 同族残留：`mode=uniform` + `burst_multiplier=5.0`（**不声明窗口**）仍被静默保留且从不生效 ——
   第一轮只修了"带窗口"的那一半（L18 家族）。
3. 冻结提交的**树**里仍有既有（非本次加入）的 superseded 授权：
   `CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R01/authorization.json`（绑定 `daf695af…` ≠ `69379643…`）。
   本次提交**没有**加入任何 `authorization.json`（`git show --name-only 324c8ee8` 零命中），
   但继承自 HEAD 的这棵树里确实躺着一份过期授权。
4. 20 文件汇总哈希的规则只写在 `round4/generate_freeze_scope.py`，而该文件被文档自身排除在冻结之外 ——
   冷启动读者无法只凭冻结产物重算该值。
5. `materialization_report(resolved, rows)` 不传 `declared_cells` 时对 `mlab_auto` 配置抛错
   （`compile_trace` 会传，直接调用者会撞上）。
6. `SOURCE-COMMIT.txt` 指向对象库中不存在的提交 `24162785…`（既有文件）。
7. 冷启动检出是 **git worktree 而非独立 clone**（共享对象库）；"不依赖未跟踪文件"的结论由
   audit hook 与空 `git status` 支撑，不是由物理隔离支撑。
8. 测试运行不完全 hermetic（产生 gitignored 的 `.pytest_cache/` 与空 `out/`）。
9. 本机缺 TensorFlow，1 条学习相关测试被 skip（环境依赖，不是缺文件）。

---

## 4. 本地冻结提交

```text
324c8ee8  fix: 冻结前两处小修（零包突发不再拒绝编译；FREEZE-SCOPE 由生成器产出）
          20 files changed, 3365 insertions(+), 68 deletions(-)
1b8ef91   chore: 编译并授权 VM 验收的两个最小案例（实例，非平台代码）
          14 files added
```

- 只 `git add` 明确列出的 20 个路径，**未使用** `git add . / -A`；
- 提交后工作树 `git status --porcelain --untracked-files=all` 为空；
- 不含 `ANALYSIS/`、`CODE/Results/`、任何 `authorization.json`、任何研究材料；
- **未推送、未创建 PR**；
- 原工作树的未提交改动与研究材料未动（HEAD 仍为 `98c858f`）。

---

## 5. 冻结文件范围（修正后）

见 `round3/FREEZE-SCOPE.md`（生成器产出）。三处与上一版的差别：
(1) 行号改为从源码读出；(2) 明确区分"生成的 §1–§3"与"人工维护的 §4–§5"；
(3) 新增生成器自检（文件集合必须复现 `code_sha256()`）。
上一版留档为 `round3/FREEZE-SCOPE-r1-superseded.md`。

---

## 6. VM 验收：连接可用，部署契约受阻

**先更正一条此前的错误结论**：前几轮写"没有 VM"，依据只是本机没有 `/data`。
本轮实测 `ssh vm` 可用（`cuda-liguang13`，Linux aarch64），tmux 3.4、conda 环境
`leo-i39`（Python 3.11.15）、471 GB 空闲、`/data/论文/leo-direct-sim` 存在、
`CODE/Results` 有 36 个实验目录（含一份真实的 R06 正式运行：`formal_run.json` +
`governance_receipt.json`，回执 v5/28 键）、`.remote_runtime/launches` 有大量见证文件、
`deployment.json` 合法（`schema_version=2`）。**连接这一项是通的。**

**受阻的是部署**：

```text
bash CODE/scripts/remote/push-remote.sh
-> ValueError: required deployment directory missing or unsafe: <clone>/LITERATURE
   (exit 1，未对远端做任何改动)
```

`deployment_guard.source_paths()` 要求完整工作区：目录 `ANALYSIS/CODE/EXPERIMENTS/LITERATURE/PAPER`，
顶层文件 `.deployment_commit/.gitignore/AGENTS.md/DECISIONS.md/LICENSE/NOTES.md/README.md`。
本仓库**缺** `LITERATURE/`、`PAPER/`、`AGENTS.md`、`DECISIONS.md`、`LICENSE`、`NOTES.md`。

**为什么不能绕过**：

1. `deployment_guard.install()` 会把远端的 `ANALYSIS/CODE/EXPERIMENTS/LITERATURE/PAPER`
   与那些顶层文件整体 `os.replace` 进 `deploy-backups` —— 从这个子集仓库成功部署一次，
   等于**删掉 VM 上的 LITERATURE / PAPER / NOTES.md / DECISIONS.md / AGENTS.md**。
   守卫在任何远端改动之前拒绝，是正确行为。
2. 手工 rsync 也不行：`remote_job.prepare_launch` 每次正式运行都调用
   `deployment_guard.verify_receipt()`，用 `source_tree_sha256` 重算整个已部署工作区；
   绕过 `push-remote.sh` 的拷贝会在运行时被拒。**这是设计上的 fail-closed，不是可以绕开的障碍。**

**VM 上的身份**：`source_git_commit = 046eea3549f7089118d85efc3300b6d20e6a3c21`（本仓库历史中不存在）、
`source_tree_sha256 = 5a0ce168…`、`code_sha256 = 9dea5140…`、部署时 880 个文件、`git_dirty=false`。
VM 的 `CODE/leo_sim` 有 31 个 `*.py` 但**没有** `pullback.py` / `recompute.py` —— 同一包布局的**另一条血脉**。

**因此本轮没有做**（如实列出）：没有部署；没有手工拷贝；没有在 VM 上跑正式运行
（两个验收案例是按 `693796433f…` 编译并授权的，而 VM 部署的是 `9dea5140…`，
拿它跑既会被 fail-closed 拒绝，也测不到本次冻结）；**没有复用任何旧授权**。

**关闭 R11b 需要什么**：把冻结提交**叠加在完整研究工作区之上**检出并提交
（即 `LITERATURE/PAPER/AGENTS.md/DECISIONS.md/NOTES.md/LICENSE` 齐备的那棵树），
使 `push-remote.sh` 能通过守卫完成 staging 与 `install`。

证据：`round4/vm-connection-report.json`。

```text
本轮停止于平台链路验收，未启动任何研究性能实验。
```
