# T1 仓库当前状态

本文是仓库级当前状态入口；工作包内 STATUS 只记录各自范围。具体版本以完整 Git commit、release-id 和 run-id 为准。

## 当前研究与工程状态（2026-10-02；DESIGN_READY=true；PROBE_CODE_READY=true；COST_PROBE_READY=false；整体CODE/COST/MODEL/RELEASE仍false）

- 最新 smoke `t1-population-matching-cache-smoke-20261002-01` 绑定 release `270e95d6404ade9bb03239c0ff5e11146d72406d-b829029d393601144969499cb3c21c7a9e180801c76e0a279232761dfff9b95f`（artifact SHA `467bc180dde57ef8ff98876eb9bc3db4d56b713906ec81517421f98a7dcc330e`），receipt `46b4a29fa04907aadc477f693051c8860b2cb5106449616a3fcb97f9895a85d`、manifest `d7cdb11eb9f6dc2f44ae1c86ae9da7d404b3930496a32a36b23e8e45f96277be` 已回传核验。该cell启动1次、记录1281包、116.563399685 VM秒后在120秒墙钟门限超时，0 ended、无result；不得称四臂成功、正交付smoke或缓存已获运行提速。
- 前一 cProfile 诊断 `t1-population-cprofile-20261002-01` 的准确 manifest SHA 为 `7a811e0b02b4a14f6ff3045db62234c30570cc01f3d89f5518ce617a4b4ee6bc`（纠正旧STATUS误记）；receipt `524fd43a84021c536220ac21c63e4ceedf7ca0a50b6f53abf99cfafa54af990d`、pstats `b2b39344145028f8324fbd7158e17bc01e592ef30c8e04474e2edaa9fbcb70c6`。其30秒profile指向topology初始化重复跨轨匹配；本轮缓存只改复用，不改拓扑算法或参数。
- 根已授权一次后续 seed7 单kernel cProfile诊断：合同 SHA `8b476af17a56b7fad192ef67a79f2e09c0ad0af860b3357081b0903a70233100`，执行链 `13f15cd93dd191b2fccf87c3bab21a956a40e3ae1e451301f9178a9e5a1bccc9`，cell input `e29e10a269a60ca77c4fba26e7666fd929c845edff2067e2b4eba456c3c78715`；30秒profile/45秒cell上限/1 call，compile、validate与运行enforce已通过。下一次运行由根负责；本地不启动。
- 账本不清零：B+C累计12 calls/374.856053099秒；本批剩48 calls/3225.143946901秒，C余47 calls。两次超时及主动停止均计账；旧run不复用、不append/resume。主矩阵、训练和formal不在当前授权内。

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
