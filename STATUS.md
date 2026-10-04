## 小区域首测回传与完整四臂执行（2026-10-02）

首 run `t1-small-region-flow-smoke-20261002-01` 已独立核验24文件，receipt `5f047d81c44ae8cfa524fc6ee84d07934de0a5a304b523a2af70e5aa541b19ea`。同一1236包trace：第1调用完整结束196.264647119 s，第2调用用98.352890119 s后被300 s四臂总cell上限终止；总2 started/1 ended/1 timeout，无完整result，不作交付或收益结论。

保持96星/99端点、5 Mbps、D/算法/四臂/控制/完整replay/source/input不变，新合同只把四臂工程总cap设1200 s，使用新run-id，不resume。根compile/validate/enforce通过；待精确新commit发布实跑。实际B+C已17 calls/1328.997056413 s；余43 calls/2271.002943587 s。1200 s上限在剩余预算内，压力候选仍未放行。

# T1 仓库当前状态

本文是仓库级当前状态入口；工作包内 STATUS 只记录各自范围。具体版本以完整 Git commit、release-id 和 run-id 为准。

## 小星座区域 pilot 当前状态（2026-10-02 18:37；仿真完成，结果输出失败）

用户授权缩小星座和业务区域后，固定 96 星/12 面/每面 8 星、800 km、53°、最低仰角 10°、max ISL 6000 km；地面区域 [20,30)°N × [100,110)°E，有 99 个正人口 1°网格。参数是工程场景假设。各星四个不同、正 MCS 容量邻居已在 t=0/2/3/4/6/8 静态核验；静态路径代理约 32.678% OD 质量需至少两条 ISL，不能当作运行竞争证据。

最新 run `t1-small-region-four-arm-20261002-02` 的四次仿真均完整结束，每次使用同一 1,236 包 trace；耗时分别 251.348708281 / 262.868283900 / 297.920742959 / 293.923033539 s，合计 1,106.060768679 s，无模拟调用超时或未结算项。单一确定性目的队列时延评分器比较 stale/now/common/candidate，计算、控制、接入、转发与下传沿用冻结设置；DDQN 训练启动数仍为 0。

**整条任务未通过**：完整轨迹在 JSON 发布阶段触及 1,200 s cell 总上限，留下 1,079,114,573 byte 的截断临时文件；正式 result 未原子落盘，谓词未验收。四组交付、时延、损失和研究收益尚不能报告。失败身份与原文件保持不变，当前只读检查可恢复对象并修复输出编码，不删数据/审计来制造成功。

证据 commit `14a4a426b4821030dc9e4f289058425b75d4a5a9`；receipt `92e943ed783c5bddf113437ce3edbb6bf5bde4b3003069a0209921742bb239b4`；25 个文件已由官方 pull 与根 independent verify-run 验证。生产链 `5c2aa5d07da33aca398818952e1e2e8cca33b81be0c95a90cc98de1ef9e13ae0`；合同 `60609b6284f57ed1ce23d34169b492104287ef0a7dcc746e3025e9fae3736fbd`。详细身份与原始首 run 历史在 criteria 的 `small_region_pilot_20261002`。

B+C 已累计 **21 calls / 2,435.057825092 模拟墙钟秒**；批次余 **39 calls / 1,164.942174908 s**，C 余 38 calls。失败和超时照计，余额不清零。约 35.87268 Mbps 的压力候选未执行、未进入 allowlist；正式实验与 main 合并未执行。后续重跑需先核对保存成本及余额，当前没有新仿真在运行。

## 同输入输出重试候选（未执行）

根已独立核对紧凑C编码与先保存逐包指标副产物的实现，4项输出/摘要测试、replay授权门和重绑后的原生输入绑定测试通过（输入绑定测试先检出旧driver哈希，再重绑，未改断言）。完整轨迹主result仍为验收必需，只有摘要不能让cell通过。新链 `21fa0527d3a09355d6b1293e0a07ff5a51d56723cd8dfc020a5d29aee1c50924`；新cell input `106186c365e92a411a459094b3fbe2b17164ac76378460e3013b488fd9aad0ac`；合同 `f20dc6bffca8e4d5b71e5a861e5d097b325072181206b79192b1bdf397b1470a`。compile/validate/enforce通过。一次新run最多4calls、cell1160秒，在实际剩余1164.942174908模拟秒内；不改科学设置、不追加压力/训练/正式运行。精确提交、发布、VM运行仍待进行，不能称结果通过。

## 三小时窗口收尾（2026-10-02，截止 16:10；PARTIAL）

- 没有新的有效四臂结果。最近 600 秒 smoke 首臂超时；随后 30 秒 VM cProfile 已独立核验 25 文件，run `t1-population-capacity-optimized-cprofile-20261002-01`，receipt `81b36f9346d9c1c715a8fe112c01f7ce1178cedab788c7eec6683ec28971fd0e`。首次容量采样约 15.307 秒，控制流量事件处理亦占显著开销；该诊断不是科研结果。
- 最后一项仅作用于指标采样的认证 GSL 零容量预筛选已通过根独立核验：8 项定向测试通过；实际 280 星/14 轨道模型、50 端点、3 窗口的完整旧/新输出逐行一致（42000 pair-window cases，439 非空窗口）；几何参数变更回退原算法。未改四邻居、区域、流量、路由评分、信息权限或模拟算力参数。
- 最后候选尚未发布或 VM 实跑。三小时截止前已不足以完成 300 秒测试及发布/回传，故不新开超出截止的任务；不得宣称全程加速或 COST_PROBE_READY。候选执行链 `7496221896d56a72ca90a693fce0071a5cb830c68a83443b781287bb541e4025`，合同 `547dfa11ddd5279418dd07c47baf3f8e64b39d74cc9289d62de90284565ace20`，科学输入身份保持 `e29e10a269a60ca77c4fba26e7666fd929c845edff2067e2b4eba456c3c78715`。
- B+C 实际累计 15 calls / 1034.379519175 模拟墙钟秒；C 余 44 calls，批次余 45 calls / 2565.620480825 秒。原生 DDQN 训练启动数为 0。没有主矩阵、正式实验、main 合并。
- 下一步先发布已接受精确提交，在同科学输入下做一次有界四臂成本/交付 smoke；失败则先量出模拟推进位置与主事件成本，不继续盲加超时上限。过门后才做竞争准入、公共时刻冻结、配对种子、真实 DDQN 训练与同检查点四臂比较、指标和可视化。
- DESIGN_READY/PROBE_CODE_READY 保持 true；COST_PROBE_READY、CODE_READY、CORE_COST_READY、MODEL_READY、FULL_COST_READY、RELEASE_READY 均 false。两项旧 receipt 兼容测试仍失败，已在旧实现中复现；不能写成全平台全绿。

## 收尾前历史阶段（2026-10-02；DESIGN_READY=true；PROBE_CODE_READY=true；COST_PROBE_READY=false；整体CODE/CORE_COST/MODEL/FULL_COST/RELEASE仍PARTIAL）

- 600秒成本smoke `t1-population-capacity-cache-smoke-20261002-01` 已回传并核验24文件：1 call/599.314139534 VM秒，0 ended、无result；receipt `4aa771c78ec36df1432070f915dbb14b62e55385352390560c5c503f310c0dcb`，manifest `b68c5395e6edb473f90bb8ca2642732c1ad6ea0fea6efc5d95647b155a46bd77`。这是首臂超时，非四臂结果或成本门通过。
- 最近 cProfile `t1-population-cached-cprofile-20261002-01` 的 receipt `8c0937d67904f3ba9ca3e5fd035fb73c6a856e001bfbfdf7c20506c29ffcb0ec`、manifest `36cc2ec46427f68b76d9ab636bcaefc869b33dc2f90725939295c6c217cf4c2b`、pstats `d6c8f72c8a5719a7807eca711fa85df5f102b0c4b9afcf81a4a48157d7aa93b8`；profile显示容量采样/MCS阈值是热点，不据cumtime推整体加速。
- 有界阈值缓存与认证不可见区间捷径已根审接受；定向与原5因果反例通过。两项legacy receipt失败已由根用旧函数复现。
- 根已重绑合同 `e2d604374ef13b24e40ce4d2784685bfb2c07ab77810908df3d3d7640211e943`，执行链 `1a651ac04149cf1b00f7510a6a7468404a8c64d10dbd1a94062d737fdfae920e`、科学输入 `e29e10a269a60ca77c4fba26e7666fd929c845edff2067e2b4eba456c3c78715` 不变；compile/validate/enforce已通过。仅授权根官方再做1 call、30秒cProfile/45秒cell诊断；主矩阵、训练、formal未授权。
- 账本不清零：B+C累计14 calls/1004.378228295秒；C余45 calls，批次余46 calls/2595.621771705秒。诊断待执行；所有研究与完整就绪门仍关闭。

## 历史工作包 B（2026-10-01；第二次结构调整 pilot 已运行并因 smoke 失败停止）

- 当前执行树 `.t1-dev-experiment-a`，分支 `codex/20260930-t1-experiment-a`。第二 pilot 的唯一不可变 release 绑定源码 commit `ffedada9274e7db1fdb5ea548d13ce4920c9815b`，release-id `ffedada9274e7db1fdb5ea548d13ce4920c9815b-8e624431c26c54ea52efc795fa0cc72b5d484e32708bb1a4ff5f535959753a74`；artifact SHA-256 `ae6c9845ffa2b64fe56c30c34b351f197b84382075e527fc3518c26b6280fa7d`。唯一第二 pilot run `wp-b-dev-20261001-02` 已结束 `failed` / exit 3，pullback `VERIFIED`。canonical receipt SHA `23399d84caa975b252e6a325d85be67834e1c9bd4699ded37dbcc49e224090cd`（receipt 文件 SHA `374ec812050c35721e29adc8e20f1eaec7bf7ec859cede72dc1584aebea43bd3`），manifest SHA `03b76ef8b2a816cc87d9cf22b0472f8110fa8e9f016832c89fb3c888d7c92c44`，archive SHA `8724bb437e33ca91aff9a7b46ea298f35e59ceecfd65e954d2a256f6ff1d3c48`，46 个文件；证据 URI `evidence://t1/wp-b-dev-20261001-02`。旧 run `wp-b-dev-20261001-01` 保留原身份和失败证据，没有覆盖或复用。
- 新 run 只执行 B tier 的第 1/8 格：负控全网四臂；cell 谓词结果为 `ok`，但正交付 smoke gate 未过，整体 `SMOKE_FAILED`。四臂各 60 offered、60 satellite-ingress admitted、0 delivered、0 delivered bits、0 goodput，且 `outcome_document.partition_exact=true`。每臂 60 个包最终均为 `IN_SYSTEM_AT_STOP`，不是终端丢包。D=30 的 [5,20] 人口窗各 45 包均为精确结局，deadline loss=1.0，interval-censored/not-computable 均为 0；没有已送达包，E2E latency 不可计算。该结果是场景未通过准入/烟测并在截止时删失，不是方法性能负结果。
- 实际准入重算 summary 为 admitted=0、not_valid=0、not_computable=1；门槛没有放宽。源 trace 有 6 个有向 OD、至多 5 个同时活跃 OD；但实际 route audit 仅覆盖 4 个 OD 的 40 个唯一包（每包 3 次记录），另外 eq↔Pacific 两个方向共 20 包没有路由决策记录。因此不能用 trace 的 OD/并发结构替代全网准入，不能形成配对分析或解释支路样本结论。
- 有界源码+回放诊断表明在线路径使用 `pkt.dst` 和已收到的目的服务广告，故不能概括成“没有目的输入”或“只看邻居队列”。但目的服务星的直达候选 `delivered_downlink` 没有获得有限 terminal/resource 评分映射，按当前评分规则成为 fallback；记录中的 eq→North PID1、North→eq PID20001、North→Pacific PID30001 因此分别选有限分绕行方向。PID1 在 sat1 时 N→sat2 的目的服务广告已到达，但评分标为 missing `resource_mapping/no_received_history`；W→19 得 0.2030894265 s，尾部预测 egress E→已访问 sat1。该缺陷解释这些被记录的绕行选择，尚不足以解释两个无决策 OD 与全网零交付。precomputed 代码在相同接收广告下按目标服务星的有向最短路设置首选方向；此 pilot 未运行该模式，不能称实测对照或保证它端到端送达。细节、边界及最低修复建议见 `CODE/work/WP-T1-COMPLETE/REPORT.md` 的“第二 pilot 回传与有限机制诊断”。
- 区域三个聚合网格中心为 equator G1:90:180 (0.5°N,0.5°E)、North G1:142:270 (52.5°N,90.5°E)、Pacific G1:131:336 (41.5°N,156.5°E)，North–Pacific 约 4967.35 km；如实称有限地理走廊，不称 North 邻近站或局部城市区域。离线按运行同一模型核验选定服务星 0/2/9 在 [0,50] s 无 `next_gsl_change`，均在 20 与 50 s 可见；在 0,5,…,50 s 边界按运行规则重算 N/E/S/W 拓扑后，三端点对最短路径分别为 2/4/2 hops；route audit 涉及的 sat 0/1/2/3/4/5/9/19/20 方向邻居映射在这些边界未变。几何和可见性只属结构条件，不证明广告安装、接入成功或端到端服务。
- 冻结设计未变：容器 41 格（compile cap 60），B tier 8 格/52 静态调用上界；pilot 4 格/34 次，余下 4 格/18 次仅可在 pilot 过门后于同 bundle/run append。阈值仍为 ≥10 可比较决策、≥5 竞争点、≥90% 有效查询覆盖、6 OD、≥5 同时包、≥2 同时 OD；所有指标要求从完整全网日志重算。因第一个 smoke 未过，剩余 7 格（其静态调用上界 48）未运行，本次至此停止，不再开第三 pilot。
- 本 run 四次调用均结束，无失败、timeout、unresolved；B 新 run 内层模拟墙钟 66.970945898 s、外层 elapsed 86 s。加上旧 B run 的 4 次/42.100891407 s，B 实际为 8/60 次、109.071837305 s 模拟墙钟。A+B 保守已耗记账为 A 外层 1061 s + B 内层模拟墙钟 109.071837305 s = 1170.071837305/7200 s（混合保守口径）；不把静态上界当实际耗时。每臂 compute 23.8430 s、queue wait 134.0694 s；query 23,843 次、service 0.0238430 s、queue wait 157.9124 s；background jobs 为 0。通用 control ledger 的 31.68 Mb offered、31.2 Mb delivered、0.48 Mb at stop 不能单独证明路由广告逐项安装。
- `paired-analysis.status=INCOMPLETE`、pilot gate 未通过、replay 为 `NOT_AVAILABLE`；没有离线 HTML、配对/种子完整性结论或模式总成本排名。FORMAL_RUN、确认种子 1001+、训练与 main merge 均未执行。发布前定向测试记录仍是 schema/B contract 2 passed、admission 4 passed；它们不改变本次 VM 失败结果。

## 已完成的治理发布与同步记录（保留原身份）

- PR #7 已通过独立审查并合入 `main`，代码合并 SHA：`857a9de4fa5c4eb07a3b466788b3a9ccba69c847`。精确 PR head `e44f0d5369256e5445443a63df2acad043418976` 的 GitHub Actions `test` run `36619082541` 成功；维护检查和 remote 协议测试已进入 CI。
- PR #8 状态/索引跟进已合入 `main`，merge SHA `c6fd2898002e5898de1c353c110673ee5d84e2af`；其 PR CI run `36625374273` 和合并后该精确 `main` SHA 的 push CI run `36627212189` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #8 不改变 T1 运行代码。
- PR #9 已通过独立审查并合入 `main`，merge SHA `d8384aa9dde425ecfcc87cff42d4b8786cf2d043`；PR head `643ee41af219bb26c3f253e20cd06c6a1af41f07` 的 CI run `36629702140` 和该精确合并 SHA 的 push CI run `36631973728` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #9 只加入脱敏索引和状态说明，不改变运行代码。
- 该 `main` SHA 已发布到隔离 T1 的不可变 release 目录。release-id：`d8384aa9dde425ecfcc87cff42d4b8786cf2d043-435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`；source tree SHA-256 `435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`，artifact SHA-256 `c76dc758575c51a28ce61eef6acece6ee35846dd0478fa0d26b04a5e60b988af`。发布器验证远端 `main` 备份身份并确认 release 安装成功；它不代表工作区资料已备份。
- 新 run `syncdiag-20260930-04` 的配置校验命令遗漏子命令必需的位置文件参数，故执行状态为 failed / exit 2。失败 receipt SHA-256 `d5a9868a80c7680433e79383e8327de4f1c4e880ca1a4535adebd0d8f9cf0af5`；回传器验证 receipt 与文件清单成功，索引 `pullback_status=VERIFIED` 只表示证据回传核验通过，不表示命令成功。失败材料保留，没有复用 run-id。
- 新 run `syncdiag-20260930-05` 在上述 release 上以 `diagnostic` 模式完成 smoke 配置校验（status `completed` / exit 0）；receipt SHA-256 `4fe28fd41c9a62ea3f28455b41ab8244849c6cde6153b06343bac3d59b68beb4`，run-manifest SHA-256 `f48b4d1d06aa9db8b7e8d7a4acf63c6320ed4735779f1a75e689d12ca15c44ce`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验结果 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM 再验、回传和索引 URI `evidence://t1/syncdiag-20260930-05` 均为 `verified`。
- PR #10 已通过独立复审并 squash 合入，head `8b197f465fbe1083431d77a481b1fef2b5a6515a`，merge SHA `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed`。PR merge-ref CI [run 36636615895](https://github.com/fuguther/leo-experiment-platform/actions/runs/36636615895) 测试了该 head 合入 base `d8384aa9dde425ecfcc87cff42d4b8786cf2d043` 的集成对象 `066fc256c982421e59db1580c3facd7d7c2b3c91`，结果 1,060 passed、2 skipped、2 warnings；该链接为 PR merge-ref 检查，不代表合并后的 main push CI。
- 接受版本已发布到隔离 T1，不可变 release-id `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed-5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`；source tree SHA-256 `5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`，artifact SHA-256 `8db3aa2bb750e2951f4b4d17253d38e1dac053387cf9d069624dd08e20872e24`。发布器的远端 main 身份、安装与 incoming 清理状态均 verified/published/cleaned。
- 新 run `syncdiag-20260930-06` 在该 release 上以 diagnostic 模式完成 smoke 配置校验（completed / exit 0）。receipt 的 protocol canonical `receipt_sha256` 字段为 `904c645984b67a3c48a53aad27051747343260ea30ea6862aab4b808e9d3b973`（不是 receipt 文件原始字节哈希）；run-manifest SHA-256 `fd36666103569df0d785b6733f043153d8fa53b4f873e59485ab724c93139f4c`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验输出 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM receipt、manifest/file-set 再验成功，本机 pullback 和部署索引 URI `evidence://t1/syncdiag-20260930-06` 为 verified。
- PR #11 已 squash 合入 `main`：head `99158c5f64120fcb1695826a4406c53f15c23757`，merge SHA `803fafcf7b51033d8b32e609750e32d7dfe7e996`；其 merge-ref CI [run 36640391596](https://github.com/fuguther/leo-experiment-platform/actions/runs/36640391596) 成功，测试合并对象 `6a2bd8b`（1060 passed、2 skipped、2 warnings）。该 PR 只更新 #06 的脱敏回传索引和状态，没有发布运行代码。
- PR #12 已通过独立复审并 squash 合入 `main`：PR head `6b9a218f77eae91a63ad42b699c8523616d91567`，merge SHA `6b4bd6b406d34f97b8f1f4c85c1a717379366536`。精确 PR head 的 CI run `36645147051` 与该 merge SHA 的 main push CI run `36650978047` 均成功，pytest 均为 1,065 passed、2 skipped、2 warnings；CI 中维护检查 0 errors、1 条范围 warning。
- 从上述精确 main merge SHA 发布的隔离 T1 release-id 为 `6b4bd6b406d34f97b8f1f4c85c1a717379366536-06c89985256898a012b5796ac362cffe0096123a30c58e01fb0b20012009f3e0`；source tree SHA-256 `06c89985256898a012b5796ac362cffe0096123a30c58e01fb0b20012009f3e0`，artifact SHA-256 `e781d0817034f995201b3748155ee1967eb5efb137765dc04df0b910caf94b44`。发布器报告远端 main 备份身份 verified、release published、incoming 清理完成。
- 新 run `syncdiag-20260930-07` 在该 release 上完成 diagnostic smoke 配置校验（completed / exit 0），验证结果内容 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。receipt canonical SHA-256 `9a257b922876bdd77cec75fca0cc9898df33894af028e67d460a751591c224a0`，receipt 原始文件 SHA-256 `b744f5cbb661d0ba24d414a3ebc6c85098db1238e71aef214afec92fb351ce6b`，manifest SHA-256 `6ad17f3d70a4af638d5737ab4783643cb928d2d4c34074d1c985a72ede7211f3`。manifest 绑定 runtime contract SHA-256 `b3b548fdba7f364d5f48ed0bcc5c77ca667f6bd4ad2c463844eca8a18923e5d0`；T1 实际环境为 Python 3.11.15/Linux/aarch64，包清单摘要 `3bc4c603b890231bfe8e27441c639d2bf0152617524b43e72dd4dfc34edbe152`。VM receipt、manifest/file-set 复验、本机 pullback 和 `evidence://t1/syncdiag-20260930-07` 均 verified。只运行配置校验，无仿真、训练、GPU 调用或正式实验。
- 在较早 PR #7 release `857a9de4fa5c4eb07a3b466788b3a9ccba69c847-27161ebf8e8db22b6ab128d7458d038d69bd63031b361f3a8a36fcae7f925a38` 的隔离 Python 前缀上运行 `CODE/scripts/remote/tests` 和维护检查测试，带有 host、release、commit、解释器和命令身份的日志记录 `64 passed in 7.86 s`；本机相同测试选择 `64 passed`。这是旧 release 的工程测试证据，不表示新 release `d8384aa9…-435da056…` 已运行同一套 64 项测试，也不是仿真、训练或科研结果。
- PR #8 对 `syncdiag-20260930-03` 添加了经核验的小索引行并更新状态；PR #9 整合了并发与 release 选择的四条索引记录；PR #10 同时整合 #04/#05 索引、runner 参数规则及 release identity 修复；PR #11 已整合 #06 的脱敏索引和状态。原始 receipt、manifest 与运行日志留在本机私有证据目录。

## 日常同步与发布

1. 进入工作树先读本文件和 `AGENTS.md`，确认仓库、owner、branch、完整 HEAD、dirty/untracked/ignored 路径及其他活动任务；执行 `python3 -B CODE/scripts/maintenance_check.py --repo .`。
2. 只从已提交、已推送、干净的完整 commit 运行 `CODE/scripts/remote/publish-release-remote.sh --commit <FULL_SHA>`。新发布写入不可变 T1 release，不再覆盖共享 `CODE/`；不得把旧 `t1-vm.sh sync` 当作新发布入口。
3. 诊断/开发使用固定 release-id 和从未用过的 run-id，经 `CODE/scripts/remote/run-release-remote.sh` 执行。`--config` 只固化输入；子命令仍须在 `--` 后取得必需文件参数，例如 `--config CODE/leo_sim/profiles/smoke.yaml -- python3 -m CODE.leo_sim config validate CODE/leo_sim/profiles/smoke.yaml`。新 runner 禁止 formal 模式；formal 继续走原有编译、独立审阅、授权和 receipt 路径。
4. 失败发布先确认没有同 release 活动上传，再检查 incoming/bootstrap。用已安装 release helper 的 quarantine 恢复器保留残留后重试；不得删失败回执、复用 run-id 或覆盖已发布 release。
5. 回传只能用 `CODE/scripts/remote/pull-release-results-remote.sh`。它先验证 VM receipt、run/release 身份和文件清单，再原子落盘并追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。验收本机证据时运行 `python3 -B CODE/scripts/maintenance_check.py --repo . --verify-evidence --evidence-root <实际本机证据根>`。

## 依赖与平台边界

- `CODE/dependencies/t1-vm-linux-aarch64/` 包含直接依赖说明、88 个固定版本 pip 解析结果和 27 个 Conda explicit URL；解释器、Linux/aarch64 与系统条件单独记录。
- 新版本化 Conda 前缀已从上述锁在 T1 重建；88 个 pip 固定版本与 27 个 Conda URL 全部对账匹配，`pip check`、核心导入和 64 项目标测试通过。重建证据保存在本任务本机私有证据目录。
- pip 锁没有 wheel SHA-256，所以结论是平台限定的版本集合可重建，不是制品字节级可复现。CUDA toolkit 未确认；本次诊断和维护/协议测试没有调用 GPU。
- release 按精确 Git 树和路径/后缀规则打包；CSV/JSON 不会仅因扩展名自动排除，数据来源和许可仍须在 `资产登记.csv` 中逐集合核对。未核实材料不得新上传到公开 GitHub。

## P2 验收边界

- 本地协议 fixtures 覆盖声明输入/配置快照、重复 run-id 拒绝、回传截断/篡改、索引并发和旧/新 release 并存；formal fixtures 覆盖配置/源码绑定与过期 authorization 拒绝。
- T1 真实 SSH 故障注入覆盖 4 KiB 上传中断和 release 安装后的 cleanup 中断；两类残留都经 quarantine 保留，已安装 release 哈希不变；同 release 重试返回 `already_present`。失败材料与哈希见本机私有治理证据。
- PR #10 已修复并测试 release basename/envelope/request identity 错配；PR #12 加入无普通 API/CLI 绕过的 runtime contract，拒绝 Python/platform/machine/包集及 pip/Conda 锁身份不匹配，并在创建 run 目录前停止。V2 授权夹具覆盖错误 run-id、授权后数据 trace 和 checkpoint 文件字节变化拒绝；这些测试不加载模型、不启动仿真或训练。真实 T1 #07 与契约匹配，manifest/receipt 身份绑定成功。完整模型加载、未登记 CUDA/toolkit 和正式科研运行不在本次验收范围。
- 本次在隔离 T1 同时运行 `syncdiag-20260930-concurrent-a/b`：二者使用同一当前 release 与相同配置快照，各自有独立 run 目录、manifest、stdout 与 receipt；并发 pullback 均验证成功，部署索引保留两条唯一 run-id 记录。随后用显式 release-id 选择旧 release `b8d8263…-401d84ab…` 做静态配置校验，再选回当前 release `857a9de4…-27161ebf…` 成功运行；没有全局可变活动指针，故此验收证明旧/新版本可独立选择且不覆盖彼此，不声称验证了全局指针切换。两 run 并发与旧/新 release 并存的本地 fixtures 仍保留。

## 文件、备份与历史限制

- topic 根 `AGENTS.md` 管跨项目边界；topic 根主方案和 `资产登记.csv` 是三端治理的唯一权威工作区文档/清单；本文件只报告仓库状态，不复制全套本机资料。它们目前只有已确认的本机权威副本。
- 尚无获准且已核实的设备外独立备份目标；GitHub 只保存本仓库已提交的公开文件，T1 release 也不代表本机资料已备份。需要目标容量、权限、用途确认后再复制、重算源/目标 SHA 并恢复样本。
- `/data/论文/leo-direct-sim` 旧 formal 根保持只读。4 个清单外 JSON 的历史 source snapshot 可关联到已知代码快照，但没有精确 run-id/receipt 绑定；不删除、不重写 receipt、不重新盖章。该旧账与新的隔离 T1 release 流程分开记录。
- 当前复核到 7 个 Git worktree。其他工作树 owner/活动进程无法全部确认；dirty、untracked 和 ignored 研究材料原样保留，没有批量归档或删除，也未声称其他工作树已统一升级。
- 状态入口持续维护；每次规则或工作状态变化更新本文件。检查器只读，不联网、不做实验、不删文件；`0 errors` 仅表示通过其列明的维护范围。


## 状态时间错位首轮60分钟诊断（2026-10-03，ta60-mech-20261003 分支追加记录）

- 新增隔离工作树 `.t1-ta60-20261003`（分支 `ta60-mech-20261003`），未触碰其他工作树的 dirty 文件；提交 `d131d22550e53fdd8037c1e1f59471597ed5fce5`，父提交 `7502c3f26302260844fcaf8df3692c3d5efcc848`。
- 只改一个文件 `CODE/experiment_platform/scripted_scenarios.py`（纯新增 v2 场景 + `ScriptedGeometry` 支持显式 cell 表）。旧场景归一化配置哈希改动前后同为 `589fbaa87b3858470effa7e62366605b2fff6da642c4882db996765b2ce19d18`，与已发布 v1 运行一致，证明旧 fixture 语义未变。
- 发布不可变 release `d131d22550e53fdd8037c1e1f59471597ed5fce5-353d39329ca7f668ee61dc7348e806ab7e369bac846072705f111c7549ab8843`；`remote_backup_pending`（未推送远端，仅开发/诊断）。
- 三次诊断运行 `ta-mech-{flat_v2,drain_v2,cross_v2}-20261003-02` 全部 completed/exit 0，入口墙钟 3–4 s，输出 11.9–15.7 万字节；已官方回传核验并追加 3 条 `evidence://t1/<run-id>`，本机证据根 `.diag-ta-20261002/ta60-evidence-20261003/`。
- 失败批次 `ta-mech-{flat_v2,drain_v2,cross_v2}-20261003-01` 因入口未用 `-m` 报 `ModuleNotFoundError: No module named 'CODE'`，receipt 保留，未复用 run-id。
- **P0 进展**：`resource_mapping` 与 `no_received_history` 已从四臂 missing 列表消失，`resource_mismatch=0`、`valid_pairs=2`。但四臂**仍全部 fallback**，剩余缺口为 `local_egress_wait_s`、`resource_service_rate`，两者均定位到 `experiment_platform/time_alignment_compare.py::build_snapshot`（551–694）：ISL `status=="ok"` 分支未写 `resource_rate[direction]`，且从未传 `local_egress_in_service_s`；已核验审计行 `observation` 块不含任何 in-service 字段，故 P0.1 是**内核审计行补输出 + 平台补读取**的两文件改动。
- **实测事实（不是时间补偿收益）**：`flat_v2` 负对照两分支逐位对称 2.914003/2.914003 s；`drain_v2` 4.843002 vs 13.053003 s；`cross_v2` 1.644003 vs 4.271003 s（Δloss 0.589000）。四臂均未产生不同选择。
- 方法学发现：`--decision-id first_forward` 在 `drain_v2`/`cross_v2` 选中竞争包 pid=1（decision_id=3/2）而非目标包 pid=10；下一轮须显式指定 decision-id 并回读 `target_pid` 自检。
- 本批**未记录峰值 RSS 与内核调用数**；`replay.captured=false` 延续。完整一页汇报见工作区 `状态时间错位实验_首轮60分钟执行报告_20261003.md`，主文档 §10.8 已同步更新。

## 状态时间错位第二轮60分钟诊断（2026-10-03，ta60-mech-20261003 分支追加记录）

- 功能提交 `1c62f19f824bf38cb9c6775be972ab898f379dbb`；索引提交 `31558e63f4ac30c8d35f672714287e30477e90bd`；release `31558e63f4ac30c8d35f672714287e30477e90bd-7d27b4242534b821d4feeea4c6e64e337fb605dfd60e6c94deeb460cc1086f0f`（`remote_backup_pending`）。
- 改动 3 个文件：`CODE/leo_sim/kernel.py`（`_observation_at_start` 在冻结时刻输出 `local_egress_in_service_s`，无法认证的方向省略不写 0）、`CODE/experiment_platform/time_alignment_compare.py`（ISL 分支补 `resource_rate`；历史按代际过滤并优先 `advertised_isl_work_ahead_bits_proxy`；接通 `capture_replay` 与 `--decision-pid`）、`CODE/experiment_platform/tests/test_time_alignment_compare.py`（fixture 补新字段 + 新增缺失信息回退测试 + 修正一处**预存在**的测试调用缺陷）。
- 测试：`test_time_alignment_compare.py` **18/18 通过**（改动前 8 failed / 9 passed）。其中排序测试在原 HEAD `7502c3f` 复现失败（断言第 90 行），根因是该测试给 `score_snapshot_at` 传了显式时刻，按该函数契约会**覆盖臂的查询时刻**，四臂按构造相同；仅修正调用，断言原样保留。
- 三次有效格全部 completed/exit 0、官方回传核验、`replay.captured=true`：`ta-flat-v2-full-20261003-01`（28,228,668 B，5 s）、`ta-drain-v2-full-20261003-01`（28,713,113 B，4 s）、`ta-cross-v2-full-20261003-01`（48,526,227 B，6 s）。目标分别为 decision 3/4/12，pid 均为 10；三格 `resource_mismatch=0`、`valid_pairs=2`。
- **结果：三格均为有效零/负结果**。flat_v2 过门（四臂对称一致、两分支代价逐位相同）；drain_v2 四臂全选 W 且与神谕一致（regret=0）；cross_v2 四臂全选 W、regret=0.20675，神谕选 E。**四臂从未做出不同选择，未测到任何时间补偿收益**；分支代价差不是补偿收益。
- cross_v2 的 2×2 分解：ETA 换真值无变化，队列/前方工作量换真值则 regret 由 0.20675 归零 → 误差唯一落在前方工作量预测。理想信息臂 common/candidate 可选 E，合法预测器无一臂做到。
- 未记账项：内核逐次调用数、峰值 RSS（manifest 仅写 `cpu_count`）。完整记录使单格输出放大约 200–430 倍，后续排期须用新口径。
- 一页汇报见工作区 `状态时间错位实验_第二轮60分钟执行报告_20261003.md`；主文档 §10.8 已同步为唯一当前状态；首轮报告保持历史身份。

## 状态时间错位第三轮60分钟诊断（2026-10-03，ta60-mech-20261003 分支追加记录）

- 三个功能提交：`1627f81`（线上/离线口径统一）、`7a4b180`（H1 格单服务器 10 ms 服务）、`f7dc736`（**臂查询时刻修复**）；release `f7dc736fb005d00010425dc867a2ff2b1754723a-d7fbaea29e53a2af24018dd3ca141e78db1ad695c00ae331ea1b5ff6c573df0e`（`remote_backup_pending`）。
- 改动 `CODE/leo_sim/time_alignment.py`（新增共用纯函数 `project_advertised_peer_processing`）、`CODE/leo_sim/kernel.py`（该方法改为委派；`_observation_at_start` 输出 `compute_state`/`query_state`）、`CODE/experiment_platform/time_alignment_compare.py`（读查询池状态；邻星处理改用共享投影，不再用配置 0 冒充；**在线四臂不再用显式时刻覆盖臂的查询时刻**）、`CODE/experiment_platform/scripted_scenarios.py`（新增 `h1_visible`、`h1_visible_shift`）、测试文件。
- 测试：`test_time_alignment_compare.py` **19/19**；`test_time_alignment_online.py`+`test_eta_terms.py` **27/27**。断言未放宽；测试 fixture 补齐内核新输出的字段，并新增“缺字段必须回退而非写 0”的检查。
- **找到“四臂相同”的真正根因**：在线四臂循环 `score_snapshot_at(snap, snap.snapshot_at + horizon)` 的显式时刻覆盖臂查询时刻。第二轮的中位斜率 0 是必要背景，本轮覆盖是充分扼杀条件。修复后 `h1_visible` 四臂预测值随臂移动。
- **首次取得四臂可区分的 H1 实测**：`h1_visible`（decision 246，pid=10）stale/now 选 E（regret 0.182499），common/candidate 选 W（regret 0）；E 实际时延 4.810203 s vs W 3.270002 s → 时间补偿避免 **1.5402 s / 0.182499 归一化损失**。
- **相位稳健性未通过**：`h1_visible_shift` 四臂全选 E、全部错选、regret 均 0.057499、零恢复 → 效果依赖精确发包相位。
- flat 负对照修复后仍对称（四臂 E=W=2.202002、预测 work 全 0、两分支 2.914003 s）。
- 有效格：`ta-third-{flat_v2,h1_visible,h1_visible_shift}-20261003-03`，全部 completed/exit 0、官方回传 `VERIFIED`、`replay.captured=true`；输出 28.2/139.5/139.8 MB，墙钟 5/13/13 s。准入失败格 `…-01`、中间格 `…-02` 身份保留。
- **仍未满足**：峰值 RSS 口径与内核逐次调用记账本轮未实现（runner manifest 仍只有 `cpu_count`）。
- 一页汇报见 `状态时间错位实验_第三轮60分钟执行报告_20261003.md`；主文档 §10.8 已同步为唯一当前状态；前两轮报告保持历史身份。

## 状态时间错位第四轮60分钟诊断（2026-10-04，ta60-mech-20261003 分支追加记录）

- 功能提交 `1ad5bd87dae7a86c2167c1fe6c3365d06edf0f31`；release `1ad5bd87dae7a86c2167c1fe6c3365d06edf0f31-9c4251f2b8ba846d39ca031ef001f05140f0c1b90d65b2390322a74505b4c501`（`remote_backup_pending`）。
- 新增 `CODE/experiment_platform/run_with_accounting.py`（包裹真实 compare 驱动、原样透传 argv）：实测峰值 RSS（bytes，单进程范围，Linux 内核高水位 VmHWM，非采样）与内核逐次调用列表；`ru_maxrss` 单位随平台记录。新增 `scripted_scenarios` 冻结相位 6.05/6.45/6.55 及事前声明。新增真实四臂循环回归测试。
- **记账结果**（连续三轮缺口已补齐）：四格峰值 RSS **495,312,896 / 496,160,768 / 497,291,264 / 497,946,624 bytes**；内核调用**每格 3 次**（1 基线 + 2 强制候选分支，rows=147，各约 0.28/0.39/0.28 s）；墙钟 9.796–10.108 s；输出 139.3–139.7 MB。
- **四个冻结相位全部有效**（`valid_pairs=2`、`resource_mismatch=0`、四臂零 fallback）：恢复量 6.05→1.122201 s、6.30→1.540201 s、6.45→1.740201 s、6.55→0（stale 本来正确）。**事前声明三发三中**（6.55 的 A 斜率量级有偏差，方向与结构一致）。
- **“相位依赖”解释并验证为交叉时刻边界**：t_cross=6.350890 s；t0=6.062/6.312 在交叉前（now 选 E），t0=6.462 在交叉后（now 改选 W）；6.55 窗口移至 3.0–6.5 后 stale 已正确。属未运行点上的预测性验证。
- **3 点窗对照**（离线变体，另记身份，不新增仿真）：四个新相位上**零差异**；仅在第三轮 5.80 旧观测上改变结果（恢复 0.704401 s），独立复现根代理 §14.4 的离线发现，但**不是普适修复**。离线 8 点重评分与 VM 结果逐值相同（变体实现自校验通过）。
- 测试：`test_time_alignment_compare.py` 20/20（含新回归项）。
- 一页汇报见 `状态时间错位实验_第四轮60分钟执行报告_20261004.md`；主文档 §10.8 已同步；前三轮报告保持历史身份。

## 状态时间错位第五轮60分钟诊断（2026-10-04，ta60-mech-20261003 分支追加记录）

- 新增 `CODE/experiment_platform/network_arm_run.py`（闭环整业务驱动）与 `scripted_scenarios` 的 `net_h1` / `net_h1_h8`（173 包冻结到达表 + time_alignment 块）；`build(name, arm=...)` 支持按臂替换并各自重新解析校验与取哈希；`run_with_accounting` 增加 `--driver {compare,network}` 分派。
- 功能提交 `04398fee`（闭环驱动与冻结业务）、`9474d1e5`、`d5da80a0`（记账行字段修正，仅影响报告口径）；release `d5da80a076fc2bb5f4f689a98e6c26eea8944f3f-fe96e3288cf491d586194a22b29d0ee4e14a3a95b699d81027f500a4ea1b0ea5`（`remote_backup_pending`）。
- 冻结到达表核验：146 背景包（83 A + 63 B）＋ 27 探针包（100 kbit，2.50+0.26k，末包 9.26 s，PID 400..426）＝ **173 offered**，与 §15.5 声明一致。
- **主结论：无有意义净收益。** L(stale)=0.599084535 最低；now −0.004231940、common(3点) −0.007341310 更差；common(8点) +0.000126728（≈0.02%）且 229/402 fallback。
- **拥塞转移已证实**：3 点 common 探针 −0.0569、背景 +0.0192；背景为分母主体（146/173）→ 净负。逐 PID：stale→now 25 改善/44 恶化；stale→common(3点) 43/76；now→common(3点) 44/78。
- **窗口灵敏度**：common 3点→8点全体 L −0.007468038、探针 +0.06164802、背景 −0.02024978、fallback 217→229 → 符号依赖窗口；**未据此选窗**。
- 173 包全部交付、0 丢包、0 删失；四臂 fate_counts 仅 DELIVERED:173 → 差异全为期限损失。闭环审计覆盖 stale/now 402/575、common 3点 390/563、common 8点 402/575；common 两变体 fallback 217/229。
- 成本：每臂 1 次内核调用、墙钟 0.92–1.02 s、峰值 RSS 439–448 MB、输出约 182 KB。失败/中断：首次 h8 发布因工作树 dirty 被拒（已提交该修正），重试时一次 SSH 超时（新 run-id `-02` 成功，未复用）。
- 一页汇报见 `状态时间错位实验_第五轮60分钟执行报告_20261004.md`；主文档 §10.8 已同步；前四轮报告保持历史身份。