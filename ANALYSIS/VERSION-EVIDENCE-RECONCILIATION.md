# 版本与证据对账（2026-09-26）

**取证方式**：全部数值都在本机或 VM 上实跑得到，命令逐条列在 §7。凡未实跑的推断都标"未核实"。

**起点**：`origin/main = 1fad58f9014443b9b7943f8f354eff16f29bd6aa`——**与预期一致，主分支仍是 1fad58f**（`git fetch origin --prune` 后复核，2026-09-26）。本轮工作在它的干净检出（`git worktree add --detach … 1fad58f`）中进行，原工作树的未提交内容已先落盘备份（§6）。

---

## 1. 结论：四个身份必须分开写，任何一句话只用一个

`code_sha256` 覆盖 `CODE/leo_sim/*.py` 的**原始字节**（`receipt.py` 的 `code_sha256()`，非递归、含注释）。同一个仓库在不同工作树上、不同提交上、不同部署上就是四个不同的身份。**混用是这一节要消灭的主要错误。**

| id | 身份 | `code_sha256` | 它到底是什么 | 证据位置 |
|---|---|---|---|---|
| **A** | **旧 R5 冻结** | `0ab9bb44…` | 平台冻结提交 `5fb89de0` 的 leo_sim 码。**R5 回执绑定的就是它**，R1--R5 十格验收跑在它上面 | `EXPERIMENTS/EXP-LEO-V2-ACCEPT-R5/run-manifest.json`；VM 各 run 的 `receipt.json`；本机对 `d29a8cb`/`5fb89de0` 重算 |
| **B** | **实验平台 main** | `57cca0ed…` | `origin/main = 1fad58f` 的干净检出。比 A 多了 R6/R7/R8 的**声明收紧**（AST 归一化后逐节点相同） | 本机对 `1fad58f` 重算；CI run `36138702506` success |
| **C** | **本轮提交** | `2c17718d…` | 分支 `t1/frozen-branch-and-async-design`，提交 **`a49ce82c19329c86c50f96ce2bfa97fe0bbc62c7`**。B + 本轮全部改动：解除 frozen×forced 互斥、`compute_servers_per_satellite`、`queue_enter backlog_before`、两个新 CLI、脚本化场景 | `git archive a49ce82 CODE/leo_sim` 后重算（**不是**对工作树重算） |
| **D** | **当前 VM 诊断部署** | `2c17718d…` | **已与 C 对齐**：部署提交 `leo-vmdeploy@ab15b8f`，推送摘要 `source_tree_sha256 = 0288580a…`（本地与远端相同） | VM 上实跑同一函数；`.deployment_commit` |
| **E** | 原工作树 `98c858f`（chore 分支，clean） | `2a114890…` | 另一条线，E0--E3 交付集冻结于此 | `ANALYSIS/ARRIVAL-TIME-T1-20260923/00e` + 本机复核 |
| — | `README.md` 曾引用的身份 | `4402081f…` | **已过期**（c8eeca1 时代） | 见 §4 |

**必须分开说的四句话**：

1. **R5 回执（A）与它的验收运行自洽**：十格验收的 `code_sha256` 逐个核对过，都是 `0ab9bb44…`。**但它不是 main，也不是本轮提交。**
2. **实验平台 main（B）从未在 VM 上跑过。** 它与 A 的差异只在声明文本，语义等价，**但字节身份不同**，所以 A 的授权不能在 B 上重放（§3、§7.5）。
3. **本轮提交（C）是在 B 之上的功能改动**，身份再次变更。**任何既有授权对 C 一律失效**，包括 A 的十格验收（§5）。
4. **当前 VM 诊断部署（D）已与 C 对齐**（`ab15b8f` / `2c17718d`），但 D 上跑的是**诊断**：没有回执、`research_eligible` 为假、不在任何信任链里。**"VM 上有本轮证据"与"本轮代码在 VM 上正式跑过"仍是两句不同的话**——后者要重新 compile + authorize + 走 `run-remote.sh --authorization`。

**一句话**：VM 上跑的是 R5 冻结版（`0ab9bb44`），R5 回执与 VM **自洽**；**main 从未在 VM 上跑过**——main 比 R5 多了 R6/R7/R8 的声明收紧，因此字节身份不同。

---

## 2. 血缘：main 是怎么来的，R5 冻结停在哪儿

```
E0-E3 交付集（未跟踪）         R5 冻结           R6/R7/R8           main
code 2a114890                 code 0ab9bb44     code 57cca0ed      code 57cca0ed
98c858f ──┐                   5fb89de ──51f8ebe─eec0db9─d44667f─┐
          └──────────────────▶  …                              ├─▶ cc0363a (freeze/pre-experiment-trust-r5)
                                                                 │      tree 8fc9eaff
                                        d29a8cb (R1-R5 实例) ─────┘      │
                                                                        │ squash
                                                                        ▼
                                                        1fad58f (main, 单亲提交)
                                                              tree 3958618f
```

实跑事实：

| 事实 | 证据 |
|---|---|
| `1fad58f` 是**单亲提交**（squash），不是 merge commit | `git rev-list --parents -n 1 1fad58f` → `1fad58f bd4a9e0` |
| `cc0363a` 的树与 `1fad58f` 的树**只差一个文件** | `git diff --stat 1fad58f cc0363a` → 仅 `lines/TIME-SEMANTICS.md`（+5/−24，即 #5 的能力表订正） |
| 两棵树的 **CODE/ 完全相同** | `git diff --stat 1fad58f cc0363a -- CODE/` → 空 |
| `5fb89de0`（R5 冻结）是 `cc0363a` 的祖先 | `git merge-base --is-ancestor 5fb89de0 cc0363a` → 真 |
| `98c858f`（chore 分支）也是 `cc0363a` 的祖先 | `git log --oneline cc0363a` 中可见；`98c858f` 的**全部路径都存在于 1fad58f**（缺失 0 个） |
| `R1/R2` 的冻结点 `324c8ee8`/`4f06dfc`、`R3/R4` 的 `b38ded0e`/`9fd4f867` **不在** `cc0363a` 里 | `git merge-base --is-ancestor` 均为假；它们在 `leo-r3clone`…`leo-r8clone` 各自的分支上 |

**因此**：main 的代码 = R5 冻结 + R6/R7/R8 的声明收紧。

---

## 3. main 与 VM/R5 的差异：只有声明文本，但字节身份必须变

`git diff d29a8cb 1fad58f` 共 9 个文件，其中**只有 4 个是代码/测试**：

| 文件 | 变更量 | 实测性质 |
|---|---|---|
| `CODE/leo_sim/trace.py` | +50/−24 | **全部是注释、docstring 与一条错误消息文本**（"the generator does not apply…" → "the burst multiplier function does not return…"），另加诊断 reason 文案 |
| `CODE/leo_sim/__main__.py` | +3/−1 | **一处注释文本** |
| `CODE/leo_sim/tests/test_burst_transform.py` | +35/−? | 测试 |
| `CODE/leo_sim/tests/test_pre_experiment_trust_round2.py` | +5/−? | 测试 |

**语义等价的机械证明**（本机实跑，脚本见 §7.4）：

```
CODE/leo_sim/trace.py:    AST with ALL string literals normalized identical = True
                          numeric-literal multiset identical = True (n_a=152, n_b=152)
CODE/leo_sim/__main__.py: AST with ALL string literals normalized identical = True
                          numeric-literal multiset identical = True (n_a=70, n_b=70)
```

即：把**所有字符串字面量归一化**后两版 AST 逐节点相同，数值字面量多重集也相同 → **控制流与数值常量一字未动**。

**但这不改变一个硬事实**：`receipt.code_sha256()` 覆盖 `CODE/leo_sim/*.py` 的**原始字节**（`receipt.py:160-167`，非递归）。注释改了，身份就变了。于是：

```
R5 manifest bound code_sha256 : 0ab9bb447bbe170f311684c3dae6da317785333312e06533f6a0c377335ccab0
main 1fad58f live code_sha256 : 57cca0eda33994844cf072c91914545708b53837957d829af248ab9ad1a8b011
identity gate would pass      : False
```

**后果（必须随证据一起转述）**：把 R5 的授权拿到 main 上用，会被身份闸门拒绝——`CODE/experiment_platform/authorize_experiment.py:365-366`（`"V2 runtime code changed after compilation"`）。这不是缺陷，是设计：闸门钉的是**字节身份**，不是语义等价。

---

## 4. `README.md` 的 `4402081f` 是过期值

`README.md` 写"本仓库的代码身份已分叉为 `4402081f…`"。实跑复核：`4402081f` 是 **chore 分支时代**冻结点（`c8eeca1` / `0e3f583` / `05fd5f0`）的身份；R8 在那之上新增 `pullback.py`/`recompute.py`、删 `q0_tiny.py`/`info_ladder_tiny.py`、改 `decision_ledger.py`/`receipt.py`/`matrix.py`/`trace.py`，身份变为 `57cca0ed`，而 README 未回填。

**任何人按 README 复核 `code_sha256` 都必然 FAIL。** 本轮已修正（§8）。

---

## 5. 本轮改动使身份再次变更（必须显式记录）

为修通"冻结观测 × 单次候选动作干预"，本轮修改了 `CODE/leo_sim/kernel.py`（解除互斥 + 在冻结分支点解析强制动作）。因此：

| 状态 | code_sha256 |
|---|---|
| main @1fad58f（本轮之前） | `57cca0ed…` |
| main + frozen×forced 组合修复 | `cf7d2add…`（中间态） |
| main + 有界每星计算资源（中间态，未提交；曾部署到 VM） | `ff8ba667…` |
| main + 本轮全部改动（**提交 `a49ce82`**） | **`2c17718d…`** |

VM 已于 2026-09-26 部署到 `2c17718d…`（部署提交 `leo-vmdeploy@ab15b8f`），
实跑记录见 `ANALYSIS/VM-RUN-20260926.md`。
**中间态的两个身份（`cf7d2add…` / `ff8ba667…`）不对应任何提交**，引用时必须说明这一点。

**这不使任何既有结论失效，但使任何既有授权失效。** 与 gates 里 `T1-COMPUTE-DELAY-PASS` 已记录的 `known_consequence`（新增 config key 改变每一个配置的 `config_sha256` → 既有 `run-manifest.json`/`authorization.json` 不可复用）同一机制。**本轮的任何 VM 正式运行都必须重新 compile + authorize**，不得复用 R5 的授权。

---

## 6. 哪些改动经过 VM 验证、哪些只经本地或 CI

### 6.1 VM 上真实跑过的（全部 `status=success`，host `cuda-liguang13`）

来源：VM `/data/论文/leo-direct-sim/.remote_runtime/launches/*.json` + 各 run 的 `receipt.json`。

| 时间（+08:00） | run | 部署提交（父仓） | 回执 code_sha256 | 回执 schema |
|---|---|---|---|---|
| 2026-08-30（24 格） | `EXP-20260829-GLOBAL-PRESSURE-BRACKET-R02-*` | `31aff1d6` / `b3a66d20` | — | v5 |
| 2026-09-24 14:05–14:07（4 格） | `EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R06-*` | `046eea35` | `9dea5140…` | v5 |
| 2026-09-25 17:56 | `ACCEPT-R1-{control,treatment}-s7` | `80d935f8` | `693796433f1e98ec…` | v5 / v6 |
| 2026-09-25 17:59 | `ACCEPT-R2-*` | `82e47354` | `40e0eeb65a5f6aaf…` | v5 / v6 |
| 2026-09-25 19:30 | `ACCEPT-R3-*` | `2ea6984f` | `2f74241f4832e26e…` | v5 / v6 |
| 2026-09-25 19:44–19:45 | `ACCEPT-R4-*` | `66f24a7b` | `c856562bb6e4de49…` | v5 / v6 |
| 2026-09-25 20:01–20:02 | `ACCEPT-R5-*` | `870d6757` | `0ab9bb447bbe170f…` | v5 / v6 |

**必须同时转述的三条**（来自回执原文，不是推断）：
1. 全部 ACCEPT 回执 `research_eligible = false`、`routing_label = analysis_upper_bound` → 这些是**平台链路验收**，不是研究性能实验；
2. VM 部署的 `source_git_commit` 是**父研究仓** `leo-vmdeploy` 的提交（`80d935f8`…`870d6757`），**不在平台仓对象库里**（`git cat-file -t 870d6757…` 在平台仓 → `fatal: could not get object info`）。平台侧对应提交是 `324c8ee8`/`4f06dfc`/`b38ded0e`/`9fd4f867`/`5fb89de0`，经部署提交信息记录；
3. 部署源仓库 `/Users/lge/Desktop/topic/leo-direct-sim`（`leo-vmdeploy` 的 origin）**本机已不存在**；`leo-vmdeploy` 自身 `main` 比其 origin 领先 5 个提交（即 5 次 R1–R5 部署提交）。

### 6.2 只经 CI 验证的

CI 定义：`.github/workflows/test.yml`，触发器只有 **push 到 main** 与 **PR 到 main**，Python 3.11，依赖与 VM 对齐（`simpy==4.0.1 numpy==1.24.3 pyyaml pillow==12.0.0`）。

| 目标 | run id | 结论 |
|---|---|---|
| main @ `1fad58f`（push） | `36138702506` | **success**（14m46s，2026-09-25T13:04:43Z） |
| PR #6 分支 `freeze/pre-experiment-trust-r5` | `36136634350` / `36135382538` / `36134114136` / `36133532014` | 3 success + **1 failure**（最后一次成功） |
| main @ `bd4a9e0`（#5） | `35998631980` | success |

**注意**：`chore/retire-dead-lines-q0-tiny`（原工作树所在分支）**没有任何 CI run**（`gh run list --branch chore/retire-dead-lines-q0-tiny` 为空）。原因不是失败，而是该分支从未开 PR，工作流也不响应分支 push。它的内容经 #6 squash 进了 main，但**它自己的 13 个提交从未被 CI 验证过**。

### 6.3 只经本地验证的（且**未跟踪、未提交**）

| 对象 | 内容 | 自述边界 |
|---|---|---|
| `ANALYSIS/ARRIVAL-TIME-T1-20260923/`（61 文件） | E0 只读取证 / E1 物理时间与 ETA / E2 压力窗 / **E2-F 三因素** / E3 反事实与信息价值 / §R5 现实一致性 | README §4：「**全部结果为本地诊断或合成诊断，无一条达到 formally governed VM，均不可升级为论文证据**」；「**O2（共同未来真值）未记录**，故主指标 Regret(O2strong)−Regret(O3) 尚未执行」 |
| `CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml`、`linkgen_generic_rf_mcs_1g.yaml`、`linkgen_published_optical_100g.yaml` | E1/E2 用的新 profile | README §5.1：这三个 profile 是未跟踪文件，**只按 commit 复现必然失败** |
| `CODE/work/WP-LEO-V2-PRE-TRUST-REPRO/`、`CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/` 等 | 流程工件 | — |

这些文件的代码身份是 `98c858f` clean = `2a114890…`，**介于 R5 与 main 之间但不等于任何一个**。

---

## 7. 复现命令

### 7.1 主分支身份
```bash
cd /Users/lge/Desktop/topic/leo-exp-main
git rev-parse HEAD                      # 1fad58f9014443b9b7943f8f354eff16f29bd6aa
python3 -c "import sys;sys.path.insert(0,'.');from CODE.leo_sim import receipt;print(receipt.code_sha256())"
```

### 7.2 VM 当前身份与部署源
```bash
ssh vm 'cat "/data/论文/leo-direct-sim/.deployment_commit"'
ssh vm 'cd "/data/论文/leo-direct-sim" && python3 -c "
import hashlib,pathlib
h=hashlib.sha256()
for p in sorted(pathlib.Path(\"CODE/leo_sim\").glob(\"*.py\")):
    h.update(p.name.encode()); h.update(hashlib.sha256(p.read_bytes()).digest())
print(h.hexdigest())"'
```

### 7.3 R5 回执与 VM 的一致性
```bash
ssh vm 'python3 -c "
import json;print(json.load(open(\"/data/论文/leo-direct-sim/CODE/Results/EXP-LEO-V2-ACCEPT-R5-treatment-s7/receipt.json\"))[\"code_sha256\"])"'
shasum -a 256 EXPERIMENTS/EXP-LEO-V2-ACCEPT-R5/run-manifest.json
# a7fbc8fcbf4cd09846bd7cde89a18bee1d54829b4f4de6b7f484cf2d82c65780 = authorization.json 绑定的值
```

### 7.4 语义等价证明
```bash
# 见本轮脚本 astcmp2.py：把两版 AST 的所有字符串字面量替换为占位符后比较
```

### 7.5 VM 运行史
```bash
ssh vm 'python3 - <<PY
import json,pathlib
d=pathlib.Path("/data/论文/leo-direct-sim/.remote_runtime/launches")
for f in sorted(d.glob("*.json")):
    j=json.loads(f.read_text())
    print(j.get("launched_at"), j.get("run_id"), j.get("status"), (j.get("source_git_commit") or "?")[:8])
PY'
```

---

## 8. 本轮对文档的订正

| 文件 | 订正 |
|---|---|
| `README.md` | `4402081f…` → `57cca0ed…`，并注明"该值随任何 `CODE/leo_sim/*.py` 变更而变；复核时以实跑为准" |
| `lines/TIME-SEMANTICS.md` | 能力表两处 ❌（`build_ledger` 无生产入口 / `counterfactual` 无生产入口）与实测计数已过期 → 改为 ✅，并指向 `CODE/experiment_platform/fold_decision_ledger.py` 与 `replay_counterfactual.py`；修正 Markdown 表头重复；"四条不变式" → "五条"；补上 `platform_frozen = blocked` 这一被漏掉的前置门 |

---

## 9. 尚未闭合的（如实）

1. **main 从未在 VM 上跑过。** VM 停在 R5（`0ab9bb44`）；main 是 `57cca0ed`，本轮改动后是 `cf7d2add`。要让 main 系在 VM 上有证据，必须重新走 compile → authorize → deploy → run。
2. **父研究仓 `leo-direct-sim` 本机缺失**，`leo-vmdeploy` 的 origin 是死指针；部署链的下一次复现需要一个可达的父仓。
3. **`EXPERIMENTS/experiment-program.yaml` 的 `external_state_recheck.current.origin_main` 不等于 1fad58f**（该文件记录的是更早的部署状态），且其 `authority` 引用的三个文件在本仓不存在。本文件不改 yaml（它属门禁权威，应由 owner 评审后更新），只登记。
4. **E0–E3 交付集不在任何提交里**，其结论"不可升级为论文证据"是它自己的声明，本轮未复核其内部正确性。
