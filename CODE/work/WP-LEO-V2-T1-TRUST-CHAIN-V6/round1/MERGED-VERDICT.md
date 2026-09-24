# Round 1 复核合并结论（三份回执）

> reviewed_sha = `652a6c124ed956636c74a7095ce65ffd48216bc7`
> criteria_sha256 = `3a1872c2c392f89310e887e5415fe337fa851915bac8a0df35d8145178b52748`
> 三个独立角色各自 clone 出该 SHA 的固定检出重跑，证据不混用。

## 判定

| 判据 | cold_start | satellite_drl | adversarial | 合并 |
|---|---|---|---|---|
| C1 | PASS\* | **FAIL\*** | PASS | \* 三方一致：**判据的 check 命令本身不成立** |
| C3/C4/C5 | PASS | PASS | PASS | PASS |
| C6 | PASS | PASS | PASS | PASS |
| C7 | PASS | PASS | PASS | PASS（措辞偏差） |
| C8 | PASS | PASS | **FAIL** | **FAIL** |
| C9 | PASS | PASS | **FAIL** | **FAIL** |
| C10/C11/C12 | PASS | PASS | PASS | PASS |
| **C13** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |

**overall: FAIL**

## 三方独立收敛的发现（不是我一个人说的）

1. **C13 真回归**（3/3）—— `652a6c1` 把 V5 报错文案改写，破坏了钉住旧串的安全测试。**已由 `af22f8c` 修复。**
2. **C1 的 check 命令本身不成立**（3/3）—— 它点名 `out/smoke`，而该产物绑定的 `code_sha256=ffcad9fc…`
   **在冻结时刻（c8eeca1=4402081f…）就已经过期**。这条 check 对**任何**提交都不成立。
   命题本身成立（两 SHA 各跑新鲜 V5 运行 → 均 v5/28 键/verified），**是判据写错了，不是代码错了**。
3. **版本家族只补了 `receipt.py` 一处**（3/3 独立发现）
   - `CODE/experiment_platform/v2_analysis.py:445` → V6 落 else → `V2AnalysisError: receipt schema is not a supported formal branch`
   - `CODE/experiment_platform/v2_analysis.py:489` → 治理见证 receipt_schema 硬编码为 V5
   - `CODE/scripts/remote/remote_job.py:145` → `ValueError: leo_sim_v2 formal runs must produce receipt/v5`
   - `CODE/scripts/remote/remote_job.py:168` → 会写 v6，与 489 行的 v5 期望必然对不上
   **净效果：正式运行的 T1 证据仍不可得 —— B4b/B4c 只在手工本地 CLI 生效。**
4. **verify 不重算流身份**（2/3）—— `receipt.py:1212-1216` 只匹配 64 位十六进制**形状**。
   把 `decision_log_sha256` 改成 `'a'*64` 或事后改动/删除日志文件，**verify 仍返回 verified**。
   → V6 的流绑定是**自陈**，不是可复核证据。
5. **非正式运行也升 V6**（2/3）—— 非正式 `validate=None`，但 `write_run` 照样收流身份：
   含 rogue 键的流、乃至 **0 字节空流**，都能产出宣称 `decision-rows/v1` 的 V6 回执且 verified。
   **作者拒绝正式空流时给的理由，在非正式路径原样成立却被留开。**
6. 其他：`--help` 陈旧文案（`__main__.py:699` "forbidden for formal runs"）；拒绝路径留下
   `manifest.json`+`trace.csv` 使同一 run_id 无法重跑；19 键契约**只冻结顶层**（nested 键全部被接受）；
   timeline 流**根本没有行键契约**（回执里只有 `decision_stream_contract`）。

## ⚠️ 对我自己一处失实声明的更正

`652a6c1` 的提交信息里我写了：

    C1  V5 回执验证行为不变          -> verified（28 键）

**这句话对 `out/smoke` 不成立**（实测 `FAILED: leo_sim code sha mismatch`）。三方复核都指出了这一点。
当时成立的是「**用当前代码新生成的** V5 回执 verify 通过」——与我写下的不是同一件事。

`652a6c1` 已推送且被三个复核角色 pin 为 reviewed_sha，**不改写历史**（改写会毁掉被审 SHA 与复核链）。
本文件即为记录在案的更正。

## Round 2 范围（依三方收敛证据）

| # | 修什么 | 依据 |
|---|---|---|
| R1 | 版本家族推广到 `v2_analysis.py`(2 处) + `remote_job.py`(2 处) | 3/3 独立发现；不修则目标未达成 |
| R2 | 收紧 V6 签发：仅在行契约已挂载（正式）时才升 V6 | 2/3 |
| R3 | 重写 C1 的 check（每次重生成产物） | 3/3 |
| R4 | 修 `--help` 陈旧文案 | 2/3 |
| R5 | 记录并更正提交信息失实（本文件） | 3/3 |

**不在 round 2 内**（另开任务）：verify 重算流身份、nested 契约、timeline 行契约、
拒绝路径残留、远程链路支持 `--decision-log`。
