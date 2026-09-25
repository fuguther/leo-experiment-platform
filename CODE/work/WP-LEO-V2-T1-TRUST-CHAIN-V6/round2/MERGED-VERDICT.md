# Round 2 复核合并结论（三份回执）

> reviewed_sha = `4d5e28b2bd2286397be67b9a4eecd4c81a5ae9a3`
> criteria_sha256 = `ac14ea4c607f5ba020ba3c4cf7d3a75b89818fb38094e8f76a8dfec41de56301`
> 三份回执：`review-cold_start.json` / `review-satellite_drl.json` / `review-adversarial.json`
> 三角色各自 clone 冻结 SHA 的纯净检出重跑，证据不跨提交混用。

## 判定：**3/3 一致 PASS，18/18 条无 FAIL**

## 核心结论：T1 证据**真的**端到端进链

`satellite_drl` 用仓库自身机制跑通全链（只替换 VM 专属常量，被审代码原样执行）：

```
真授权 -> 两个 arm 各跑真·正式 V6 运行（双流, exit 0, 31 键, verify verified）
      -> 真 remote_job.build_v2_governance_receipt (receipt_schema=v6)
      -> VM launch witness
      -> v2_analysis.analyze()

status=VERIFIED · claim_status=READY_FOR_INDEPENDENT_CLAIM_REVIEW
analysis_mode=current_external_witness · authorization_verification=strict_recomputed
evidence_class=[v2_external_witness, v2_external_witness]
```

同一批工件交给父提交 `98f7412` → 在分派处抛 `receipt schema is not a supported formal branch`。
**修复是承重的，不是改字面量。**

`adversarial` 另做全仓收口：消费运行回执 schema 的只有 `verify_receipt_dir`（家族常量）、
`v2_analysis:445`、`remote_job:152`，**全部已家族化**；`_DecisionLogWriter` 全仓仅一处实例化，
**不存在第二条铸造 V6 的路径**。这回答了 round 1 留下的"还有没有第五、第六处分派点"——没有。

## 我在本轮的两处失实声明（记录在案）

1. **`652a6c1` 提交信息**写「C1 → verified（28 键）」——对 `out/smoke` 不成立（实测 code sha mismatch）。
   round 1 三方都指出了。见 `round1/MERGED-VERDICT.md`。
2. **`4d5e28b` 提交信息**写「help 与类注释更新」——**只改了 `_TimelineLogWriter` 的类注释，
   没有改那个变假的 `_DecisionLogWriter.__init__` 注释**。`cold_start` 指出：
   「按 check 判 PASS，按 statement 字面应 FAIL」。

两处均已记录，**均不改写历史**（被审 SHA 已被三个角色 pin，改写会毁掉复核链）。

## 残留清单（合并两轮；全部非判据项，全部移交下一任务）

| # | 内容 | 来源 | 影响 |
|---|---|---|---|
| **S-1** | `execution.available_capacity_interval_s` 默认 `None` → `link_available_windows` 为空 → **只要有 ISL 服务，`isl_pressure` 必炸** | round2 新发现（既有） | **T1 分析阶段不可达**——B4 达成自身目标 ≠ T1 现在能跑 |
| **S-2** | `matrix.py` 只校验 `trace_seed/phase/controlled_signature`，不校验 trace 身份，与 `v2_analysis` 的配对契约不一致 | round2 新发现（既有） | 仓库自带 matrix 测试请求会 pairing mismatch |
| **S-3** | `verify_receipt_dir` 不重算流身份（改 `'a'*64` 仍 verified） | round1 | V6 的流绑定是**声明**；闭合目前靠 fold 侧比对 |
| **S-4** | 空流拒绝在 `close()` 之后 → 留占位空文件 + `trace.csv`/`manifest.json`，同路径无法重跑 | round1 + round2 | 运维 |
| **S-5** | `__main__.py:68-73` 注释为假（**本轮新造**） | round2 | 文档；改它需第 3 轮，**刻意留** |
| **S-6** | 19 键契约只冻结顶层；nested 键未约束 | round1 | 契约强度 |
| **S-7** | timeline 流没有行契约（回执只有 `decision_stream_contract`） | round1 | 不对称 |
| **S-8** | VM 正式路线 `formal_command` 仍不传两条流 → **正式远端目前产不出 V6** | round1 + round2 | 本轮只修掉"V6 会被拒"的断点，**没修"正式路线能产出 V6"** |
| **S-9** | `experiment-program.yaml:137` 的历史证据叙述含 "(formal runs refuse --decision-log)" | round2 | **刻意不改**：那是某次历史实验的事实记录，改它等于篡改记录 |

## 刻意的边界：什么修了、什么没修

**修（声称描述当前行为，且不触及任何判据 `touches`）**
- `ANALYSIS/T1-MEASUREMENT-PROTOCOL.md` §3.3 第 3 条（本任务的立项依据，已被本任务推翻）
- `EXPERIMENTS/contracts/run-artifact-contract.md` 的 receipt schema
- `ANALYSIS/PLATFORM-AUDIT-REPORT.md` N1

**不修**
- `__main__.py` 注释（S-5）：**代码，触及 C6/C7/C16/C17/C18 的 touches → 需第 3 轮 → 用户规定上限两轮**
- `experiment-program.yaml:137`（S-9）：**历史记录，改了就是篡改**

> 界线的原则：**文档在 `touches` 之外可以修；代码在之内必须走复核；历史记录不改。**
> 用的是同一条规则，不是"哪个方便"。

## 判据质量问题（`cold_start` 提出，供下一轮收紧）

1. **C18 的 check 弱于 statement** —— 它只查 `--help`，而 statement 说的是"陈旧文案"；
   `__main__.py` 注释与两处文档都不在 check 覆盖内。**这是本轮判据的真实缺陷。**
2. **C1/C10 未写明装置** —— 仓库里不存在 v3/v4 样本（97 个回执全是 v5×94 + v6×3），
   必须靠 `_convert_run_to_legacy_v1` 现场造；且"逐字相同"只有"每树现场生成再比较验证行为"
   这一读法可判定（同产物跨树必因 `code_sha256` 一边 verified 一边 mismatch）。
3. **C6 的字面在当前仓库不可执行**（无匹配的真实授权格），只能靠合成 formal 单元。
4. **C16/C17 的两个证据都是源码子串断言**，行为证据靠外部探针。
