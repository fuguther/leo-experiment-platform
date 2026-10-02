# 状态时间对齐与异步转发：完整实施任务书

## 2026-10-02 当前设计与落实顺序

> 本节是当前唯一有效的执行入口；下方原 P0–P12 是历史材料，不作为当前任务列表。用户授权本轮最多51个新增开发calls和独立≤100 VM分钟DDQN训练预算。根已接受seed7原生人口低负载四臂成本探针的PROBE_CODE子图与语义输入，允许为该唯一cell提交推送、发布不可变release并运行最多4 calls；不代表全矩阵、DDQN或CODE/COST/MODEL/RELEASE全门通过。旧contract_dev_c.yaml仍失效。FORMAL_RUN、seed1001及以后、合并main不在本轮授权内。

### 研究定义与输入冻结

- 主问题：相同地面源宿输入、完整星座、合法候选与已到达历史下，只改变资源状态查询时刻，确定性目的导向队列时延规则是否改善全网期限损失。主比较为每个评价 seed 的 `mean_loss(common)-mean_loss(candidate)`，总体为全部 offered 包；四臂各自闭环演化。背压是受限算法参照；真实训练 DDQN 是本轮必交扩展，确定性主实验可先行；DDQN未完成时整轮必须保持PARTIAL。
- 场景选择：保留完整 280 星/14 面参考几何，只对地面人口端点按纬度 `[5,50)`、经度 `[65,140)` 过滤。参考 profile `CODE/leo_sim/profiles/population_global_1deg_diagnostic.yaml` SHA-256 `05e1904cea297a7448bfb4c18bfd936ad5a60f70c06c3c23053af62f504f4efb`，不是官方全局冻结或实测。人口输入 `CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif` SHA-256 `c5742d16fc01d454e8ac5c5345a7e7716883acd28ac4d0d34c24613bc315e59a`。静态计算对 1°格内4×4个有限正人口15′计数直接相加，不作面积加权；2310格人口和 3,984,792,155.1563754，展示为四舍五入到人 3,984,792,155。按既有 gravity 权重、同格排除和条件归一化，静态 OD 距离积分 `P(≥2000km)=0.1978591556`、`P(≥3000km)=0.1039825175`。这些只是栅格静态计算，不是 trace、路由、多跳或竞争证据。
- 首probe profile `CODE/leo_sim/profiles/t1_population_region_cost_smoke.yaml` SHA-256 `31c7aae1c33a5886c2618192fe8d5a9ba0f877cf439e60e5a9dc3b5eaec5b518`明确固定广告协议v2；GPW SHA见上。持久合同SHA-256 `6b02bf9014ae2423f99b08d6951594aa87d57d5675f1857847c27b9bbf62a505`由根授权唯一seed7网络cell、四臂/4-call、无append，执行链SHA `a296d85827243f89b51eafeada4cf938132d2df65d596998c0e5e526607ec633`，cell输入SHA `70553d1dc6bce687309f18348c84c863c5213679cdc264b915ea9564f2dbf905`。根独立compile-only核对280/14、5Mbps/12,000bit/N1/1ms、D=4与物理TTL独立、窗口正确，validate=true。删除授权的合同副本和旧invalid合同仍须拒绝。`PROBE_CODE_READY=true`；`COST_PROBE_READY`待clean pushed commit、immutable release、VM依赖/GPW/实际bundle和run身份验真后再通过。旧静态R工件保留旧profile和旧哈希，不是本probe的trace/竞争/成本证据。
- 输入假设：包长1500 B；采用分段非齐次Poisson（各段强度=bit/s÷12,000），不叠加local_diurnal_cosine；低竞争/成本 smoke 用5 Mbps、两倍峰窗1秒。t=0–2 s为控制预热且计入VM墙钟；业务相对预热结束，发流[2,4) s、峰窗[2.5,3.5) s、观察至t=8 s、每包D=生成后4 s。按该积分期望15 Mbit/约1250包，但不是并发保证，不能以此宣称竞争实验或时间对齐负结果。旧 max_packets 不可沿用作截断上限。
- 主竞争负载不是固定5 Mbps。已存在但未获准的静态候选只用于验证计算器，输入字节身份与当前cost-smoke profile不同；其名义R很高，不能先验宣称预算可行或竞争成立。主负载仍须在实施阶段按冻结规则复算当前完整输入，并按 `u_e=Σ_od p_od I(e∈path_od)/C_e`（单位1/Mbps）、每个OD单一最高仰角GSL服务星及有向端口容量生成；直达包对ISL贡献0，无路径概率质量单列、不重归一化。重算期望包量、输入/内存上限及预算；若放不进预算即停止报告缺口，不截包、不减负载、不放宽门槛。实际竞争只从完整VM日志门验收。
- 所有方法冻结同一外生trace/hash并共享外部随机键；在线策略/后台/学习器只能见已到达合法信息。完整 offered 人口、物理终态、deadline损失与删失分开，观察须覆盖每个包D；11/23/42为描述性开发seed，不做确认或等效声明。

### 按依赖顺序的实施任务（主矩阵与扩展仍有未实现项；首探针代码子图已通过集中复核）

首个VM成本探针只依赖任务1、2中已接受的低负载四臂路径及合同/绑定/launch ledger，不等待任务4背压、任务5 DDQN或五执行模式全部实现；这些扩展仍OPEN，probe不代表完成。该探针只取证流程和成本，不检验竞争激活或算法收益。合同正例必须精确绑定根授权cell，删除授权副本、旧合同、错cell、append或超过4 calls均须继续拒绝。

1. **区域输入与trace。** 改 `config.py`、`population.py::aggregate_population_array/load_population_regions`、`trace.py::_endpoints/_dst_choices/compile_trace`：先过滤再归一化源/宿分布，按固定分段调度冻结动态需求和包键。反例：边界外/半开边界格不入端点；每源目的概率和为1；运行时读取未来trace必须失败；按seed完整核对 offered hash 与人口守恒。静态R系数脚本/fixture须报告逐端口单位、路径、输入SHA；不伪称kernel轨迹。
2. **资源事件、ETA和动作规则。** 改 `kernel.py` compute/query/快照/提交路径及 `time_alignment.py::estimate_eta/score_candidates`：同一 `bounded_linear` 最近≤8条同resource+epoch、未过期已收到记录，源时刻严格递增，邻接队列差分斜率中位数、容量裁剪；仅1条为hold_last；无历史为unknown。记录t0/compute/query请求-开始-结束、候选t_entry/start/done及实际对应时刻。四臂共享映射/预测器/评分单位，仅查询时间变化。共同规则在每请求最终合法mask内有限ETA集合上用HF type7；至少2个值，否则NO_STRONG_COMMON并共享回退；seed7校准p25/median/mean/p75，平局median>mean>p25>p75；不能补0。若开发整组没有至少两个有限候选，则标NO_STRONG_COMMON、冻结median为operational baseline；不以评价seed补救。候选评分 `J=t_entry−t0+W_r(t_entry)+S_p,r+B_after_r` 全为秒；不重复计GSL末段服务/传播，不把目标自身等待加进入队。计算/查询期间本地FIFO前方工作按已知剩余服务前推；peer再决策未知保留unknown，不读未来实付等待、不求在线固定点。统一回退为合法可见候选中剩余hop最少、方向稳定破同；无可信路径则hold并计费。物理resource generation与全局topology重算版本分开：相同peer/服务实体的历史跨重算保留，实际换peer才断开；新增不换peer两tick能保留≥2记录、换peer不能混入旧记录的反例。缓存按实际拓扑依赖指纹重验，不仅按全局计数丢历史。反例：compute期间出口队列清空；目的GSL速率与ISL不同；最终mask改变拒绝非法动作；资源identity/epoch不匹配必须记错配；unknown不等于零。
3. **五模式与控制成本。** 改 `async_routing.py`、`_precompute_build/_precomputed_order/_packet_compute_required/decide_deferred`：逐模式冻结动作拥有者、推理/复用/查表和fallback计费。scope至少含(sat, ground destination ID, packet bytes/business class, topology epoch, policy/model version)；后台只读任务请求前已收到历史、已知端点目录和已观测业务。建表近似须声明；query做epoch/resource/path/final-mask校验，回退重算计费。工作按scope×候选×bin×操作展开；cache命中仍模型重推理即不算复用。async_window目标时刻实际传入评分；反例覆盖bin时刻变化、过期表、安装延迟与动作被模型覆盖。
4. **最短可见路径约束的目的背压启发式。** 保留ISL物理FIFO和GSL DRR，新增合法目的广告/字节计费。按目的d计节点commodity虚拟积压：`Qhat_n^d=Q_n^d×H_n^d`，Q以包计，只计本节点持有/排队/等待/在服的该目的包；控制包排除，发送完成后从源扣账、到邻星后入账、不重复记传播包。H是未访问、可到已知目的GSL服务点的剩余hop，含最终GSL；未知路径保留unknown。对相邻合法转发候选计算 `mu_nm×(Qhat_n^d−Qhat_m^d)`；终端GSL连虚拟sink(Q=0,H=0)，μ用目的DRR可说明的服务估计。先在原生最终合法mask中按完整首动作的剩余总hop选最小有限hop组（ISL首动作加peer剩余hop，本地deliver为1），再在该组选择最大正压力；这只是参照策略约束、不改主四臂候选、不用矩形剪枝。压力速率μ统一为资源bit/s除以包长得到packet/s。；缺广告/非正/epoch冲突走冻结hop fallback或hold，绝不把未知Q当0。名称只能是“目的/路径偏置的背压启发式”：因无每目的联合调度，不称经典BP且无经典稳定性保证。反例需证目的Q字段合法到达/过期、控制字节计费与FIFO/DRR未变，以及无竞争且本地可合法下传时不会因ISL更快而绕行。
5. **DDQN独立扩展与训练。** 真实训练、冻结的一份模型用于四臂，预测状态进入真实网络输入并由模型拥有动作；共用固定槽、缺失位，无arm标签。固定训练目标：每包结算一次：D前成功下传reward=-T/D、不可恢复丢弃reward=-1、仍在系统包于D到期reward=-1，三者互斥仅结算一次；中间0、gamma=1；物理包D后仍演化但不重开学习episode；行政截断为删失/未成熟，不造failure。网络MLP64×64 ReLU、Adam lr1e-3、batch64、replay50000、target每500 optimizer updates；epsilon按累计成熟packet transition从1线性降到0.05，前15000后固定。train seeds 301/307/311，validation313；最多3候选在同trace各验证一次，按完整offered mean loss最低选择、并列按训练seed。训练请求以等概率使用四种query rule，不输入arm名。固定观测字段/单位：packet age与remaining deadline秒/D再clip[0,1]；五logit槽顺序 `deliver,N,S,E,W`；每槽 `t_entry−t0,W,S,B_after,J` 秒/D clip[0,10]、queue/capacity和rate/reference各clip[0,1]、hops/280 clip[0,1]、legal及每数值missing位、resource identity/epoch valid；数值缺失占位0必须missing=1。旧queue奖励+50/gamma=.99不用于新目标。训练总≤100 VM分钟单列，已纳入用户授权但不能与仿真calls合并；仍需通过模型来源/训练契约/VM成本门，不承诺充分收敛。真实checkpoint来源当前未核实，绝不拿随机权重替代。
6. **全网门、配对结果与错误归因。** admission由真实事件证明≥10全网可比点、≥5同命名资源跨OD竞争点，累计≥1秒同时active_OD≥2且in_system_packets≥5，并列连续区间/窗占比；完整snapshots、在线决策、重演数各自列出。低竞争smoke只验全体人口守恒、闭环流程和正交付。按seed报全部offered loss、物理drop、D结局、删失、OD阶段、直达/ISL暴露、ETA误差、队列误差、资源映射匹配与全部成本。结论区分未激活、预测/映射不准、已激活但无闭环增益、局部收益被外部性抵消；HTML只回放实存日志。扩展已有`CODE/experiment_platform/replay_html.py`和`test_replay_html.py`，按主手册§5.1接通全网、资源竞争、逐跳决策、配对指标/成本四视图及共同时间游标；没有数据显示NOT_AVAILABLE，同快照分支与全网四臂分开展示，禁止把分化后的状态伪装相同。
7. **合同与预算防绕过门。** 新增分阶段成本探针合同/精确suite allowlist（计划名 `contract_dev_cost_probe.yaml`），旧invalid合同继续拒绝。实现必须验证完整输入/源SHA、预算、身份、allowlist；禁止普通调用绕过stage gate。首个成本探针只准seed7低竞争四臂，共4calls，需干净推送完整SHA、不可变probe release、新run-id；实际账本失败/超时照计。实际probe完成并验回执后，再将trace、scope数、候选/bin/操作/模式、最坏fallback、模型profile展开成保守分阶段上界；不从首格推算51格。累计实际 calls/秒每阶段重核；超限即停且不削核心配对/负载。DDQN未VM profile前没有DDQN预算。

### 跨层反例验收（均待新增，非已通过）

| 层 | 反例与预期 |
|---|---|
| 宏观 | 全网均值低而单命名ISL多OD同刻争用：门按该资源区间识别；直达GSL多也不删包造ISL暴露。 |
| 中观 | compute/query期间出口继续服务并清队列：只把残余工作前推一次，区分预计入队/开始/完成。 |
| 中观 | 目的commodity Q广告过期或epoch错配：未知/拒绝、bytes计费，不能把物理总出口队列称经典BP。 |
| 微观 | 目标下传速率不是ISL速率、peer尾路回到已访问节点：按目的GSL建模；非法尾路unknown。 |
| 微观 | commit最终mask变化、无信息、无物理可行候选：分别记录拒绝/共享回退/hold，不把未知填0。 |
| 跨层 | cache hit后DDQN重选、四臂分化后试图同步内部队列、包到D仍在途中：分别判复用失败并计费、独立演化、到期结算loss且物理命运继续。 |

### 矩阵、预算与阶段闸门

B历史8 calls/109.071837305 s；批次上限60/3600，最多剩52/3490.928162695；A+B旧账1170.071837305/7200不清零。每cell≤120s、并发1，失败/timeout占预算。候选最大矩阵：低竞争4 + seed7 common规则4 + 主四臂12 + 五模式最多新增12（per_packet/candidate只有配置和身份完全相同才复用主矩阵3格） + 队列压力参照3 + 同快照分支≤4（只从已有运行捕获） + DDQN四臂12 = 51；历史后最多59/60，保留1不预用。51只是call展开，不是墙钟预测。非kernel snapshot/profile先做；固定seed7低竞争四臂为唯一首个VM端到端成本探针。总成本必须经范围×候选×bin×操作、各模式/fallback与实际模型profile逐阶段保守预算证明；首格耗时不可外推。若超预算则不启动后续核心矩阵并报告缺口，不能私自删配对、截包或降负载。DDQN≤100分钟另列，必须独立设计/授权。

| 闸门 | 放行条件 | 当前 |
|---|---|---|
| DESIGN_READY | 本设计经根集中裁决 | true；仅逻辑准备通过 |
| PROBE_CODE_READY | 根独立验收仅首probe真实调用依赖子图与语义输入；未用扩展不阻塞 | true；仅seed7四臂低竞争单cell，不等于整体CODE_READY |
| CODE_READY | 所有按顺序实现项、必需反例和相关回归完成 | false；主矩阵、背压、五模式和DDQN扩展仍未完成 |
| COST_PROBE_READY | 根授权合同中唯一seed7低竞争四臂4-call cell；clean full-SHA push、immutable release、VM依赖/GPW/bundle/argv/seed验真、新run-id与one-use ledger齐备后通过 | false；release/runtime/run验真待执行 |
| MODEL_READY / DDQN_COST_READY | 真checkpoint来源/契约通过且模型、查询与模式VM实测成本 | false；未找到已审checkpoint，成本未测 |
| FULL_COST_READY | deterministic core和获准DDQN范围的上界合计在原预算内 | false |
| RELEASE_READY | 完整主矩阵/DDQN的发布门；首成本探针仅受单独COST_PROBE_READY授权 | false；不得执行主矩阵或训练 |

### 后续验收命令（实施后运行；本轮未执行）

```sh
python3 -m pytest CODE/leo_sim/tests/test_population.py CODE/leo_sim/tests/test_trace.py -q
python3 -m pytest CODE/leo_sim/tests/test_time_alignment.py CODE/leo_sim/tests/test_eta_terms.py CODE/leo_sim/tests/test_time_alignment_online.py -q
python3 -m pytest CODE/leo_sim/tests/test_control.py CODE/leo_sim/tests/test_routing.py CODE/leo_sim/tests/test_async_routing.py -q
python3 -m pytest CODE/experiment_platform/tests/test_t1_admission.py CODE/experiment_platform/tests/test_time_alignment_compare.py CODE/experiment_platform/tests/test_execution_compare.py CODE/experiment_platform/tests/test_t1_suite.py -q
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests -q
```

通过软件测试不等于VM成本、场景激活或研究效果成立。
### 自动推进顺序与完成口径

已接受的首probe子图先按“合同测试→clean commit/push→不可变release→VM runtime验真→唯一seed7低负载四臂4-call成本probe→pullback和人口/成本核验”完成；此步不等待BP/DDQN/五模式，也不运行主矩阵。之后才复算完整主输入R/trace/实体和scope成本，按CORE_COST_READY决定能否继续common校准、确定性配对、模式/BP/分支和DDQN。每阶段按既定门与预算继续；DDQN或核心配对未完成整轮记PARTIAL，正式确认仍未授权。

---

> 给执行 AI：按 P0→P12 连续实施。使用 `superpowers:executing-plans` 组织实施；若你有获授权的子代理，可使用 `superpowers:subagent-driven-development`。阶段验收是自动继续条件，不是向用户索要下一步指令的暂停点。用本文件的复选框跟踪进度。

**目标：** 在现有 LEO 平台上补齐四组状态时间比较、真实包率与有限计算资源、异步方案更新，以及统一的诊断和实验交付链，使用户能够判断时间对齐的价值和逐包/异步执行的收益成本。

**架构：** 复用现有离散事件内核、控制广告、计算资源、单动作重放、指标重算和回执。新增受权限约束的预测模块、统一评分器、后台方案更新器，以及共用的实验驱动。理想未来真值只进入隔离的离线诊断，不能进入在线策略。

**技术栈：** 现有 Python / SimPy / pytest / YAML / JSONL；DDQN 延用现有学习模块及其环境依赖。不为本任务更换仿真框架、重写全部内核或新增 Web 控制台。

## 0. 交接指令、范围与完成定义

### 0.1 你必须持续做到什么

本任务交付的是**完整实现、自动化验收、有界诊断结果、后续正式实验包和最终报告**，不是只写方案或只修一个问题。先读完本文件，列出执行账本，然后连续推进。P0/P1 或一轮测试通过不等于任务完成。需要较长时间时持续工作并更新进度；遇到上下文压缩，从账本恢复，不重新开始。

工程完成与科研收益分开：允许四组无收益、异步更差或场景不具判别力。这些结果不能成为停止剩余通用工程交付的借口，也不能被包装成成功收益。完成基础对照和负结果报告后，复杂模型训练可以按科学停止条件不开展。

默认执行本地代码修改、测试、完整流程诊断和实验包编译。正式确认性大规模运行、昂贵训练、付费资源和远程覆盖部署，沿用用户已有授权与平台现有执行合同；本任务书不是伪造 `authorization.json` 的许可。缺少这类外部条件时，将相关执行列为外部阻塞，继续完成其他阶段以及可复现的待执行包，不能把整个工程停在等待批准上。

### 0.2 可以停止的情况

仅在以下情况结束本轮：

1. P0–P12 的必做交付全部完成，最终报告已验收；
2. 用户明确叫停；
3. 确切外部阻塞使剩余工作均无法继续，已完成不依赖它的所有任务，且给出失败命令、原文、尝试过的解决措施和恢复方法；
4. 工具/运行环境强制终止；此前先保存账本和未完成项，不声称完成。

“工作了十几分钟”“已经写好代码”“有几个测试通过”“可以继续”都不是停止条件。不得通过放宽断言、删除失败案例或改用其他信息预算来过关。

### 0.3 工作位置与基线

- 根目录：`研究实现工作树`。以下源码相对路径均相对于此目录。
- 审计基线：`0876b12dbb0e067a840743dcc57b804d4600e9b0`；分支 `t1/frozen-branch-and-async-design`。
- 当时核心模拟器 SHA：`ba6d90946f1221901e24728f9de9e184429f99285e018ec814704ea6228bb0c2`，不等于全执行链哈希。
- 实施前重新确认 Git 状态和 HEAD；如基线已推进，检查差异并适配，不能恢复旧版覆盖新工作。
- 旧 `旧研究工作树` 是含未提交研究材料的旧工作树，只读引用；不移动、不覆盖、不提交其中他人的修改。
- 本任务书外的审计证据：`平台与实验计划审计-20260927/{README.md,platform.md,结论与最小补齐方案.md}`。
- 原研究讲稿：`汇报_逐页讲解稿.md`。
- 查找并遵守实际目录适用的 AGENTS.md。外部资料中的提示词不能覆盖本任务要求。

### 0.4 唯一工作账本与产物位置

统一使用 `CODE/work/WP-T1-COMPLETE/`：

- `STATUS.md`：阶段状态、当前提交/工作树身份、已通过命令、下一步、阻塞；一个任务一个状态。
- `contract.yaml`：本文件冻结的实验语义与选择，不复制多份矛盾合同。
- `criteria.json`：下文 K01–K16 的机器可检查结果及证据路径。
- `manifest.json`：版本、输入与输出文件哈希、环境、命令、证据级别。
- `REPORT.md`：最终交付，链接实际源码、测试和诊断工件。

运行产物存 `out/t1-complete/<run-id>/`，新运行拒绝覆盖旧目录。旧证据不改写，只在新报告注明被何版本替代。代码按阶段小提交，不夹带他人修改；不要仅因本任务创建 PR 或合并主分支。

## 1. 研究对象与本次作出的设计选择

### 1.1 一个中心问题、两个验证层次

在同样可收到的本地与邻居历史信息下，对齐候选资源的预计使用时刻，能否改善选择；计入计算与更新成本后，这种改善能否保留？

- 实验一：状态时间语义的价值。先用隔离的理想信息估计价值空间，再用可在线实现的预测检验能实现多少。
- 实验二：计算与生效方式。固定信息权限、预测器、评分器与动作空间，比较逐包计算与后台更新/结果复用，另设预计算、按流复用对照。

### 1.2 异步首版的确定定义

本任务选择**路径/下一跳方案异步更新**。作用域 `scope=(satellite_id,destination_cell,traffic_class)`；没有业务分类时 `traffic_class="default"`。每个方案输出对下一跳方向的排序，可以供该作用域的后续多个包查询。可跨流复用，但不以“必须覆盖多条流”作为有效性门。

首版不改变输入业务，不做源端限速或发送速率控制，也不做概率分流。这是为了隔离“每包重算”与“后台更新后复用”的成本差别，不是声称其他调度形式无价值。不要再次停下来询问路径/分流/速率三选一。

异步提供两种预声明的方案：

1. `async_point`：对预计安装后窗口中点生成一份方向排序；
2. `async_window`：相同输入、相同预测器，在有效窗口分成四个等宽时间段，分别生成方向排序，包按使用时刻查询对应段。

必须同时报告两者，才能区分“复用减少计算”与“预测后续一批包经历的负载变化”。四段是本次工程默认设计值，不是文献结论；开发期可做 1/4/8 段敏感性，确认比较前固定。

### 1.3 研究时间轴

分别记录以下时间，不复用一个字段承载多个意义：

| 字段 | 意义 |
|---|---|
| `state_measured_at` | 每项广告状态在来源处实际测量的时刻 |
| `received_at` | 本星收到该条状态的时刻 |
| `snapshot_at` | 请求计算时冻结可见信息的时刻 |
| `compute_requested_at` | 计算任务进入计算服务队列的时刻 |
| `compute_started_at` | 实际获得服务资源的时刻 |
| `compute_finished_at` | 算法服务完成的时刻 |
| `installed_at` | 异步方案原子生效的时刻；逐包臂不伪造方案安装 |
| `prediction_target_at` | 本次查询要预测的资源时刻 |
| `resource_entered_at` | 目标包实际进入命名资源队列的时刻 |

首版在线策略统一在请求时冻结输入，计算排队期间不悄悄刷新。已有 `t_decision_start` 保持兼容，但明确它对应请求/冻结时刻。保留旧 `t_measure` 时必须标明旧语义，邻居真实测量时间仍取各条广告。

预测不会改变原始 `state_measured_at`。信息年龄不是包额外经历的一段时延，不得重复加进 E2E。

### 1.4 目标资源与候选定义

候选为本星合法下一跳方向；研究预测对象是候选邻星的**指定有向出口**，不是整星总队列。候选资源选择必须只依赖 t0 允许信息和预先固定的后续规则，不得到事后挑最有利出口。

对每个候选 `a`，在 t0 可见的拓扑/广告上用统一确定性后续规则指定 `resource_id=(peer_sat,egress_direction)`。首版沿用可见拓扑最短剩余路径及稳定方向破同，不从全局缓存偷取下一跳。无可见下游路径时返回明确缺失原因。

实际后续路由仍正常演化。如果真正出口与预计出口不同，保留该包 E2E/命运并记 `resource_mismatch`；该条不能进入“同一资源预测误差”统计，也不能从总体动作代价中删除。下游直接投递单列为 downlink 资源；主 ISL 机制统计单列，不混池。

主要预测时刻为包在邻星完成必要处理/保持后进入该有向出口的时刻；同时保留 `peer_arrival` 作为诊断。两者不能混用。

## 2. 对照、评分与信息权限

### 2.1 在线四组

所有组同一 `snapshot_at=t0`、同一收到的历史记录、同一候选集、同一资源映射、同一评分器。采用相同的提交合法性重验和失败处理。

| arm | 输入资源状态对应时间 | 用途 |
|---|---|---|
| `stale` | 原样使用最后已收到的测量值 | 基准 |
| `now` | 用同一历史信息推演到 t0 | 真正的当前补偿 |
| `common` | 推演到所有候选共用的 t0+h | 共同未来对照 |
| `candidate` | 推演到各候选的预计资源使用时刻 | 待检验方案 |

`refresh` 单独标为执行语义诊断，不能重命名成 `now`。同一计算调用先算全部候选的 ETA，再查询各组，避免每组拥有不同候选集。不同组真实成本允许不同，但实验一理想阶段设计算成本为零，净成本实验单独计入。

共同未来开发候选：候选预计使用时刻偏移的均值、中位数，以及开发样本偏移分布的 25/50/75 分位数固定 h。先在开发集按主损失选定，破同优先中位数、再按配置顺序；冻结为 `common_strong`，确认数据上不再选择。

### 2.2 隔离的理想信息诊断

`oracle_now/common/candidate` 仅供离线价值空间比较。在相同分支起点分别强制每个候选并完整重演；各候选自己的分支提供其资源在对应查询时刻的真值，不能从一条基线轨迹复制所谓所有动作的未来真值。

零成本诊断支持 `frozen + compute_delay_s=0`，语义为同一仿真时刻冻结、选择、提交；不得用任意 epsilon 代替零，不得切回 refresh。同刻事件有固定顺序并写入合同。

未来状态输入只能包含命名资源在查询时刻的状态，不能包含该动作的最终 E2E、交付命运或其他候选最终结果。最终结果仅由独立评估器计算后悔值。

离线查询允许从完整分支事件轨迹重建时刻真值；必须剔除目标包自身及它按 FIFO 排在后方的普通包。控制优先未来插队单独按事件处理。不要把查询时刻队列总比特直接断言为目标包全部等待。

### 2.3 简单预测器与统一评分

首先实现有物理边界的简单预测，不先训练复杂网络：

- `hold_last`：保持最后值；
- `bounded_linear`：最多使用最近 8 条已收到且源时间严格递增的同资源广告，对队列增量斜率取相邻差分中位数，预测 `max(0,q_last+slope*(target-t_last))`，按可知队列容量截断；只有一条有效记录时回退 hold_last；
- 不从内核读取邻星未广告的到达率、实时队列或真实未来链路。若新增广告字段，所有在线臂同时接收并计入增加的控制比特。

首版 ETA = 已知计算排队与配置服务时长 + 本地等待估计 + 发送 + 传播 + 可知的邻星处理时长。未知保持时间不伪装成已知，给出 `eta_method` 与误差；每个加数对应不重叠区间。不使用实际 future arrival 生成在线查询时刻。

首版评分采用可审的秒量纲启发式：本地预计等待/发送/传播/邻星处理 + 目标有向出口预测工作量/可知服务率 + 固定可见拓扑剩余传播代价。它是评分估计，不是实际 E2E 等式。四组仅更换预测查询时刻，其余项完全一致；不临时复刻一个不匹配的 capacity 评分器。

队列缺失、速率不可知、无资源映射返回 `missing`，不当 0、不混为 +∞ 的可用数值。对所有臂使用同一保守后备方向排序并计数，保留该样本。若主场景大部分请求都走后备，判定场景没有充分激活被研究机制，不能宣称算法无效或有效。

### 2.4 在线权限边界

策略函数只能接收不可变 `ObservationSnapshot`：本地可观测队列/计算池、已收到广告历史、当前合法方向、允许的几何/链路参数。给它的对象中不能含 Kernel、truth/audit sink、完整 trace 或其他星缓存引用。

建立三个不同类型：`ObservationSnapshot`、`ResourcePrediction`、`BranchOutcome`；在线接口禁止接受 `TruthSample`/`BranchOutcome`。测试用 trap 对象确保试图访问未来或邻星缓存时失败。来源清单与未来信息负测试同时存在，不能只靠文档声明。

## 3. 文件与接口安排

优先扩展已有模块，不重复实现 receipt、路由引擎或指标重算。下列新增文件名是计划目标，目前未实现；P0 若发现同职责现成模块，复用并在账本记录映射。

| 文件 | 职责 |
|---|---|
| `CODE/leo_sim/time_alignment.py` | 不可变快照、资源标识、ETA、简单预测、统一评分；纯函数，不读内核真值 |
| `CODE/leo_sim/async_routing.py` | scope、触发合并、计算任务、版本安装、时间段表、查询与失效 |
| `CODE/leo_sim/kernel.py` | 接入冻结/零成本、公共计算资源、异步查询、事件输出 |
| `CODE/leo_sim/{config,decision_ledger,receipt,counterfactual}.py` | 配置、时间账本、版本兼容、反事实配对 |
| `CODE/leo_sim/learning.py` 与 `kernel.py` 的策略适配入口 | 复用现有固定 DDQN 检查点推理，隔离训练状态 |
| `CODE/experiment_platform/time_alignment_compare.py` | 离线各候选真值轨迹、四组评分、真实动作代价与诊断 |
| `CODE/experiment_platform/execution_compare.py` | 四类执行方式/异步两种预测语义的统一矩阵驱动 |
| `CODE/experiment_platform/benchmark_decision.py` | 完整决策路径实测耗时与并发吞吐 |
| `CODE/experiment_platform/t1_suite.py` | compile/validate/run/resume/report 入口，预算与失败处理 |
| `CODE/experiment_platform/control_reach_probe.py` | 修正单位、代码身份与来源元数据 |
| `CODE/leo_sim/tests/test_time_alignment.py` | 时间、信息权限、预测/评分的行为测试 |
| `CODE/leo_sim/tests/test_async_routing.py` | 后台更新、共享资源、安装版本与继续转发 |
| `CODE/experiment_platform/tests/test_time_alignment_compare.py` | 四组、分支、缺失和真值泄漏测试 |
| `CODE/experiment_platform/tests/test_execution_compare.py` | 公平矩阵、统计单位、成本与命运记账 |
| `CODE/experiment_platform/tests/test_t1_suite.py` | 预算、恢复、工件身份与失败退出 |

接口固定为以下概念；实现具体 dataclass 时字段增加可以，但不能改变含义：

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class ResourceKey:
    satellite: int
    direction: str
    kind: Literal['isl', 'downlink']

@dataclass(frozen=True)
class StateSample:
    resource: ResourceKey
    measured_at: float
    received_at: float
    queue_bits: float
    rate_bps: float | None

@dataclass(frozen=True)
class ResourcePrediction:
    resource: ResourceKey
    snapshot_at: float
    target_at: float
    predicted_bits: float | None
    method: str
    missing_reason: str | None
```

`make_snapshot` 只由内核显式提供允许字段；`predict_resource(history,snapshot_at,target_at,method)` 为纯函数；`score_candidates(snapshot,predictions,etas)` 返回稳定方向排序及分项代价；`build_schedule(snapshot,install_estimate,window_s,bins)` 返回版本候选表；`lookup_schedule(scope,now,legal,path)` 只查表并做合法性/防环过滤，不调用预测器或重算评分。

### 3.1 配置默认值与验证规则

在 `config.py` 新增命名空间 `time_alignment` 和 `async_routing`，更新白名单、默认值、配置哈希与错误信息。默认 `time_alignment.enabled=false`、`async_routing.enabled=false`，确保旧配置行为不变。新值均是工程诊断假设，写入 resolved config，不当真实星载参数。

```yaml
time_alignment:
  enabled: true
  arm: candidate                 # stale | now | common | candidate
  predictor: bounded_linear     # hold_last | bounded_linear
  history_limit: 8
  common_rule: median_eta        # median_eta | mean_eta | fixed_horizon
  common_horizon_s: null         # fixed_horizon 时必须为有限非负数
  execution_mode: per_packet     # per_packet | per_flow | precomputed | async_point | async_window
  per_flow_ttl_s: 0.5
  query_delay_s: 0.000001        # 暂定 1 us，实测后另建已标定配置
async_routing:
  enabled: false                # 两个 async 模式时必须为 true
  update_interval_s: 0.5
  valid_window_s: 1.0
  install_delay_s: 0.001
  window_bins: 4
  trigger: periodic
  max_pending_per_scope: 1
execution:
  decision_observation_mode: frozen
  compute_delay_s: 0.0001       # 未标定的诊断情景，不是 DDQN 实测
  compute_servers_per_satellite: 2
```

约束：间隔和窗口为正数，延迟有限且非负，bins 为正整数；async_point 强制 1 段，async_window 使用配置值。`enabled` 与 execution_mode 矛盾时拒绝，不默默回退。允许计算时间长于更新周期，采用 P7 的触发合并，不能以“一个周期算不完”拒绝本来要研究的高压力情景。`query_delay_s` 由全部执行模式的公共查询服务承担，须记录其资源约束；实现首版为每星单服务台，所有模式相同，默认值扫描含 0 和基准值，不能只向某一臂收费。

开发扫描：更新周期 `{0.1,0.5,2.0}` s；有效窗口固定为该周期的 2 倍；包长 `{372,891,1500}` B；有限计算池 `{1,2,4}`；服务时长以诊断 0.1 ms 和已标定值分别成组。根据 P10 顺序逐项扫描，不全部笛卡尔积。正式候选值只由开发集选择并冻结，不通过确认结果反选。

## 4. 分阶段实施任务

### P0 — 基线、范围与执行账本

- [ ] 读 `CHARTER.md`、`WORKING-MODEL.md`、当前研究线、审计报告以及本任务书。
- [ ] 执行并记录 `git status --short`、`git rev-parse HEAD`、`git worktree list`、Python/依赖版本；记录已有 dirty 文件归属。
- [ ] 确认当前checkout可安全写入；如被其他任务占用，用同一核实提交建隔离工作区，禁止 reset/stash 他人文件。
- [ ] 创建 0.4 中账本，登记 K01–K16；记录接口/路径适配。不重复找用户确认已在本计划确定的选择。
- [ ] 复跑现有三组测试，保存结果为基线；失败先定位，不把失败状态作为正常起点静默继续。

```sh
python3 -m pytest CODE/leo_sim/tests/test_compute_delay.py CODE/leo_sim/tests/test_frozen_observation.py CODE/experiment_platform/tests/test_minimal_branch_compare.py -q
```

历史参考为 46 passed；实际数量随合法新增测试变化。完成即进入 P1。

### P1 — 修复已知口径错误并统一合同

修改 `control_reach_probe.py`：新字段 `ctrl_isl_occupied_s` 保存现有秒值；若还要比特量，按实际服务的控制比特独立累计。提升诊断 schema，旧 schema 读取时显式转换并记录来源，不能悄悄换单位。

- [ ] 加回归测试：模拟占用 0.02 s 时新字段为 0.02，报告不能显示为 0.02 bit；control bytes 与 occupied seconds 分别有单位。
- [ ] 所有新诊断工件携带 Git commit、dirty/diff identity、核心代码哈希、实验驱动哈希、配置与 trace 哈希和 runtime。
- [ ] 修改 `TIME-SEMANTICS-QUARTET.md`、`ASYNC-SCHEDULING-DESIGN.md`、`lines/TIME-SEMANTICS.md` 和 `CAPABILITY-TABLE.md`，删除互相冲突的现行表述；历史结论保留来源并标为历史。
- [ ] 移除“chosen 不变化就不是异步”的残留判据；把逐位相等更正为实际实现的容差一致。
- [ ] `contract.yaml` 固定本任务第 1/2 节语义，记录设计值属于工程选择，不冒充实测。

验收：单位回归通过；现行文件不再把 refresh 写成预测当前补偿；原始研究附件未被修改。

### P2 — 统一时间事件、有限计算资源与零成本冻结

- [ ] 保持历史默认路径行为；为新路径记录 `compute_request/start/finish` 事件及稳定 `compute_job_id`，在分配 decision_id 前也能关联 pid/scope。
- [ ] 请求时冻结；排队期间不刷新；服务开始后占用同一个每星计算池。逐包、异步、按流缓存 miss 均竞争该池，不能各建免费池。
- [ ] 允许零服务时长的 frozen 诊断；同刻请求、冻结、提交排序确定。更新相关配置校验及旧“必须大于零”测试，保留针对真实语义的替代验收。
- [ ] 请求/服务/安装的并发策略确定：每星 FIFO 等待，同刻按任务序号；完成事件先于由其触发的安装，同刻 packet query 在已发生安装后读取版本。把优先级写入合同并加夹具。
- [ ] 不双重计算：`compute_wait=started-requested`；`compute_service=finished-started`；安装延迟单列；node_process 与上述阶段互不覆盖。

必须通过的手算夹具：一个服务台、两个同时请求、每任务 0.1 s，则开始时刻 `[0,0.1]`，完成 `[0.1,0.2]`；两个服务台完成 `[0.1,0.1]`。零成本 frozen 在 t0 提交，仍不读取 t0 后广告。默认无界路径与旧基线一致。

### P3 — 允许信息、候选资源、预测与共同评分

- [ ] 构造只含已接收记录的快照；历史按资源/来源测量时刻排序去重，乱序接收不让旧样本覆盖较新源状态，TTL 用既有语义。
- [ ] 实现第 2.3 节两个预测器、ETA 分项、稳定评分和 missing 后备；为每个候选输出 resource_id、预测时间、方法、来源时间戳和评分分项。
- [ ] 历史至少一条时可运行；两条以上才估斜率。负时间跨度、NaN、负队列、非正有效速率按明确错误/缺失处理，不能静默变成零。
- [ ] 如需要新增广告信息，只增加最小字段，实际序列化成本进控制流量；所有在线臂同预算。禁止从对端内核直接拿所需字段。
- [ ] 编写资源选择反例：邻星总积压很高但目标出口为空，不得给空出口施加总队列等待。

纯函数核心验收示意（按实际模块名落地为测试）：

```python
def test_projection_keeps_source_timestamp():
    r = ResourceKey(2, 'E', 'isl')
    h = (StateSample(r, 0.0, 0.2, 800.0, 8000.0),
         StateSample(r, 1.0, 1.2, 1600.0, 8000.0))
    p = predict_resource(h, snapshot_at=1.2, target_at=2.0,
                         method='bounded_linear')
    assert p.predicted_bits == 2400.0
    assert h[-1].measured_at == 1.0
    assert p.target_at == 2.0
```

其他必测：同资源恒值时三个预测时刻输出相同；未接收样本不进入快照；真值 trap 访问失败；缺失所有臂统一后备；破同规则一致且与臂名称无关。

### P4 — 四组理想诊断与单动作闭环代价表

- [ ] 扩展现有单动作重放，按每个合法候选独立重跑；分叉前核对日志与时间线，不重复造另一个仿真器。
- [ ] 导出候选目标资源完整变化轨迹供只读 oracle 查询；统一定义同刻“目标包入队前”取样顺序，目标包自身永不进入预测标签。
- [ ] 实现 O0/理想当前/强共同未来/候选资源时刻四组的统一离线评分入口，并输出每组所选动作、各候选最终结果、损失与 regret。
- [ ] 不能把 chosen 分支的真值扩充成未选动作真值；最终结果表与策略输入结构隔离。
- [ ] 对照同一 trace/config/seed、后续规则与外生随机过程。首轮 `ge_enabled=false`；以后开启随机链路时使用按链路/时间等物理索引生成的共享外生过程，不仅宣称“seed 一样”。
- [ ] 分支配对不满足则该对记 invalid，含原因和计数，不默默丢弃；强制非法方向应 fail-loud。

必须有：等长空队列零差异；确定性竞争可手算代价差；未来队列改变排名例；目标后到包不增加其 FIFO 前方工作；控制非抢占优先例；预计出口与实际出口不同例；失败/未完成候选仍入结果表。用测试明确“知道每候选最终结果的最优动作”仅是评估器，绝不能成为预测器。

### P5 — 可部署四组执行与误差分解

- [ ] 将 `stale/now/common/candidate` 接入实际转发，使用同一纯函数、合法集与提交规则。
- [ ] 新方案在网络中多次决策时必须整段重新运行，让队列由策略自然产生，不用固定基线未来负载充当新策略结果。
- [ ] 记录每次输入快照哈希、允许信息来源、查询时刻与候选排序，能回放单次决策。
- [ ] 离线诊断完成 2×2：真实 ETA/估计 ETA × 真值状态/预测状态；显式标明“真实 ETA”只存在于诊断。
- [ ] 一跳固定干预、单星应用、多星应用分别标记；不能以单包换动作结论代替全网收益。

验收：四臂同一 t0 可见输入一致；未来 trace/真值不能进入可部署函数；恒状态预测退化一致；关闭新功能旧测试回归通过。

### P6 — 包粒度、完整计算基准与实际压力

- [ ] 保留 8 Mbit 原夹具并明确用途；新增 372/891/1500 B 固定包长压力配置，平均 1471.64 B 仅在有原始分布时照分布重放，否则保留解析换算，不能造分布。
- [ ] 所有场景写清输入 Mbps 是入口总量、单 OD 还是单链路，记录每星真实转发请求率、重决策次数、包长分布；不能把总入口率直接当每星计算请求率。
- [ ] 复用原研究资料中的来源账本并核对具体引用，将每个参数标为 measured / published_proxy / assumed / derived，附原始文件或来源、适用场景和单位。100 Mbps 仍标情景输入，终端测速不能改名为单星内部负载；没有原始包长分布时只做固定包长敏感性。
- [ ] 控制重传、hold 后重试与每跳请求均计数，不假定每个端到端包只计算一次。
- [ ] `benchmark_decision` 测完整路径：构造观测→特征/预测→评分/推理→掩码→选动作；分开只推理 microbenchmark。用单调高精度时钟，记录预热、线程、并发、批大小、设备、库版本。
- [ ] 默认预热 100 次，测量 5 轮、每轮至少 1000 次；慢设备每轮至少 5 s，单轮上限 60 s，不能把不完整轮混为同样样本。输出 p50/p95/p99、持续请求/秒、样本数、计时空调用基线。
- [ ] 模拟使用配置服务时间或实测分布采样，两种来源分开。没有星载硬件只称本机/VM 实测，不称星载实测。
- [ ] 压力验证有限池 N=1/2/4，负对照 N=0（无界）。默认无界不能作为逐包压力结论。

解析负载 `rho=lambda*t_service/N` 用于构造情景，不替代实际瞬态排队。验证受控请求流 rho=0.25/0.75/1.25 时，过载队列确有积压且守恒；不能断言所有现实链路都服从该简化服务模型。真实细粒度包数太多时先缩短观测窗口/节点规模，不能静默合包后仍声称逐包验证。

### P7 — 真正异步更新器

- [ ] 实现 `scope` 状态机 `UNINITIALIZED → COMPUTING → INSTALL_PENDING → ACTIVE`；ACTIVE 期间允许一项后台更新，不能阻塞旧表查询。
- [ ] 周期触发首版必做；同 scope 忙时仅记一次 pending 更新并合并触发原因，完成后若 pending 则用新的允许快照再提交；禁止无限堆积旧任务。
- [ ] 新版本仅在 `compute_finished_at + install_delay_s` 安装，不能在请求/epoch 开始时直接写安装。安装是原子换表；已进入链路队列/在途包不被拉回改路。
- [ ] 本次计算通过公共计算池；`async_window` 的四次预测/评分总成本实际记账，不按单点计算免费处理。查询成本单独测量和注入，不重复收取完整推理成本。
- [ ] 每个 packet query 记录 scope/version/bin/action；新版本可与旧版动作完全一致，不作为失败。版本单调，较旧完成结果不得覆盖较新已装版本。
- [ ] 初始无表、过期、全方向非法时采用与全部臂一致的可见拓扑安全后备，计入查询/后备成本并触发更新；无合法后备则沿用既有 hold，不悄悄重算昂贵模型。
- [ ] 查询过滤已访问路径，避免按目的地表带来环路；跨包路径差异只通过共同合法性过滤处理，不重新调用完整评分器。
- [ ] 有效窗口从实际安装时开始计时。预测生成时使用可知的预计完成/安装时刻；若实际安装偏晚，不回写已经计算的预测目标来伪造准确性，记录偏移；过期窗口结果丢弃并合并触发重算。

必测时间例：旧 v1 已生效；t=1 请求更新、计算服务 2 s、安装 0.5 s；t=1.5 和 3.2 的包仍查 v1，t=3.5 安装后包查 v2。还要测试计算排队导致的安装延迟、同刻顺序、空窗无包、持续突发合并、旧结果晚到、安装期间拓扑失效、同动作新版本、过期后备。比较低负载与高负载时模型调用数，不能只看 `#packets/#installs` 判断正确性。

### P8 — 公平执行方式矩阵与 DDQN 接口

在同一信息预算、输入 trace、合法性与后备规则下实现：

| 模式 | 计算与复用规则 |
|---|---|
| `per_packet` | 每次路由请求进行完整计算并等待公共计算池 |
| `per_flow` | `(sat,src,dst,traffic_class)` 缓存；miss/TTL 到期/非法时重新计算，命中只查询；miss 非免费 |
| `precomputed` | 不用动态业务队列，根据允许的已知轨道/拓扑离线生成路由表；计入初始构建/安装与更新开销，不能读取未来业务 |
| `async_point` | 按 scope 后台生成单点排序并更新 |
| `async_window` | 按 scope 后台生成四段排序并更新 |

`per_flow` 默认 TTL 与异步更新周期相同，另做开发敏感性；不要故意设劣势缓存参数。先在同一固定预测臂下比较执行机制，再与状态时间语义做有界交叉，不一开始做全部笛卡尔积。

- [ ] 为冻结快照增加确定性 policy adapter，使固定 DDQN 检查点可做只推理（epsilon=0、关闭更新、固定标准化与动作掩码）。不全局解除 learner 禁令后假设一切安全。
- [ ] 分支工具允许经检查的只推理适配器，确认前缀动作一致和检查点哈希；在线训练/随机探索仍拒绝。
- [ ] 如果无有效训练检查点，用固定参数小模型验证接口/耗时且明确不能作策略性能结论；通用确定性评分器仍完成全流程。不启动长训练来填空。
- [ ] DDQN 与简单评分器的模型能力不同，单独列比较；机制归因主实验不能悄悄同时替换预测器或策略模型。

验收：可配置五模式；模型调用/查询/后备/安装计数与事件对齐；异步旧表转发期真实可见；同计算资源预算；DDQN 推理与 learner 训练边界测试通过，或缺依赖项有精确外部阻塞并不冒称实现验证完成。

### P9 — 统一指标、统计、失败与停止规则

以 `scenario×trace×seed` 为独立重复/区组；同一组的不同臂配对。包不是独立重复，相同 trace 精确重跑不增加 n。

机制比较预定义主损失 `L`：有业务截止时间时使用原截止时间 D；无截止时间时，先在开发基准所有合法候选交付样本中计算 E2E p95，取 D=2×p95 并冻结；开发集无交付则使用该场景预先声明的测量时域作为 D，同时标明弱可解释性。每个目标包都需具有至少 D 的后续观察窗口，不足则延长统一排空或标行政删失，不能当作超时失败。

`L=min(delay,D)/D`；丢包或观察完整但 D 内未交付取 1。原始命运、实际已交付 delay、超时与窗口未完成仍分开报告，禁止仅靠此合成量隐藏劣化。各 arm regret = 所选候选 L − 合法候选中最小 L。主对比为 common_strong regret − candidate regret；工程规划最低实质差异设为 0.01 个归一化损失单位，并报告 0.005/0.02 敏感性。这些是预先声明的设计阈值，不是实测或行业标准。

系统层报告 E2E mean/p50/p90/p95/p99（注明仅交付子集）、offered/admitted/delivered、每类丢包与未完成、有效载荷吞吐、队列积压与面积、控制比特与占用秒数、计算调用/服务/排队/查询/安装、回退率、方案年龄、模拟 wall time。吞吐分母与统计窗口固定，测量窗口生成的包和暖机/排空包分开归属。

- [ ] 按独立 run/trace/seed 计算配对差，再对区组作 10000 次固定随机种子的 bootstrap 区间；n 很小时仅描述，不给强确认性结论。
- [ ] 开发种子 7/11/23/42；确认种子从 1001 开始连续取，不与开发共用 trace。正式样本数由开发配对差方差和目标精度预估并在运行前冻结，不能运行到显著才停。提供功效/精度报告，不把默认四个种子称为充分。
- [ ] 样本数规划有明确默认算法：先用开发独立区组配对差标准差 s，目标 95% 区间半宽 0.005，初估 `n=max(20,ceil((1.96*s/0.005)**2))`，再用开发分布重采样核查达到该精度的频率；这是精度规划近似，不保证功效。四种子方差不稳定时先追加开发种子 43–54，只用于规划。n 超过 100 时报告所需成本和精度不足，不能擅自截成 100 后宣称满足精度，也不能读取确认结果再重估。确认包可以保留更大 n 的待执行矩阵；本次本地有界诊断不承担该确认运行。
- [ ] 主比较仅一个；其他比较报告效应量并标探索性，或在确认合同中预先固定多重比较校正。
- [ ] 因种子/截止时间/信息不足导致失败不静默删；缺失与失配率纳入报告。

科学停止：理想信息未呈现可用价值时不启动复杂预测训练；预测误差吞掉收益则报告实现边界；计算成本吞掉收益则报告净收益不足。仍完成通用实现、负对照、异步基本机制验收和最终负结果报告，不为出正结果不断换场景。

### P10 — 有界诊断场景、自动矩阵与恢复

新增 `t1_suite`，必须支持以下 CLI（本阶段结束才可执行；不能在其实现前把帮助成功当完成）：

```sh
python3 -m CODE.experiment_platform.t1_suite compile --contract CODE/work/WP-T1-COMPLETE/contract.yaml --out out/t1-complete/compiled
python3 -m CODE.experiment_platform.t1_suite validate --bundle out/t1-complete/compiled
python3 -m CODE.experiment_platform.t1_suite run --bundle out/t1-complete/compiled --tier acceptance --out out/t1-complete/acceptance
python3 -m CODE.experiment_platform.t1_suite resume --run-dir out/t1-complete/acceptance
python3 -m CODE.experiment_platform.t1_suite report --run-dir out/t1-complete/acceptance
```

场景按目的分三类，全部保留阴性：

1. 手算机制组：空队列等路径、确定性下游突发、控制优先、计算期链路失效、缺广告死端、目标资源改变、跨版本查询；事件规模小，能手算。
2. 星座可判别性组：从既有真实几何 profile 出发，开发期固定扫描 `vis_k∈{2,4}` 与 offered 倍率 `{1,4}`，其余相同；保留成本。选点只依赖 t0 至少两个合法方向和预声明等距/稳定排序，不以未来是否双交付作挑样标准。每 run 按决策序号稳定抽取最多 12 点，全部候选完整命运入表。
3. 压力/执行组：真实字节包长参数，周期后台更新，受控热点/稳态业务两种；使用同 trace，finite N，报告真正每星请求率。受控计算请求流与星座 E2E 两种证据分开。

验收 tier：每类至少一份小配置，种子 7/11/23；能触发对应机制是 fixture 通过条件，不是科学结论。必须跑通五执行模式与四在线状态臂的基本入口。完整交叉只在一份小 fixture 上验证配置，不据该小 fixture 声称泛化。

开发 tier 按顺序筛：先校准能观测请求/负载的窗口；再单因素包率、N、更新周期、负载变化速度；最后对“包率×算力”和“更新周期×变化速度”做小规模二因素交互。不要把所有参数一次全排列。候选范围及选择规则必须在读取各臂性能前写入编译矩阵。

默认本地预算：单 cell 120 s wall time、峰值内存上限依据机器可用内存的 50%、单矩阵最多 120 cell、总诊断 2 h；先 dry-run 估事件量和磁盘。超过预算会标 BUDGET_EXCEEDED，保存已完成单元，降低仅工程验收的时长/节点规模再编译一个**新身份**的诊断矩阵。不得改动确认性矩阵降低 n 后仍称原设计完成。预算上限是防止失控，不能把阶段一的预算耗尽当所有任务完成。

恢复必须核对每个 cell 输入/代码/配置哈希；成功且校验通过的跳过，失败 cell 单独重试；代码变动后旧输出不得挂到新身份。运行捕获 stdout/stderr/退出码、资源与失败原因。诊断参数和正式候选参数清楚分开。

### P11 — 证据链、回归与正式待执行包

- [ ] 输出两条事件流并按既有 receipt v6 规则绑定；新增 schema/字段若影响校验必须升级版本并加旧版本兼容测试，不能旁路验证器。
- [ ] 完整执行链 manifest 覆盖新预测、异步、驱动、评分、分析、配置、checkpoint 和环境；仅核心 code_sha256 不足以代表新实验。
- [ ] 跑本任务新测试、受影响旧测试，然后一次全套平台测试。失败写根因与修复；新修改后只重跑相关项及必要最终回归，不为了“全绿次数”循环消耗资源。
- [ ] 实际运行 compile→validate→acceptance→report→离线重验。故意篡改复制工件、缺流、错 SHA、旧授权和不完整 cell 应拒绝。不得改动原始证据来做篡改测试。
- [ ] 若现有授权允许远端验收，先读部署清单确认目的环境与当前任务无冲突，再 exact commit 部署→诊断→拉回→本地复核；否则产出明确命令与输入包，标 `REMOTE_NOT_EXECUTED`，不自动覆盖正在用的 VM。
- [ ] 正式包包含冻结矩阵、开发/确认分离、样本量理由、效应阈值、信息权限、成本来源、场景有效性和失败规则；通过现有编译入口验证兼容性。不能自行签署独立审核或授权。

基础回归命令：

```sh
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
```

历史1020通过不是本次预期数字；新报告必须给实际输出、提交和环境。完整测试若因部署树/依赖失败，分别报告，不能把它们自动划为不重要。

### P12 — 最终交付与收尾

- [ ] 更新唯一 STATUS 与 criteria，逐条链接证据；每项采用 IMPLEMENTED/TESTED/DIAGNOSTIC_RUN/FORMAL_RUN 分层，不以一种状态覆盖全部。
- [ ] 写 REPORT：用户问题→实现→验证→能支持的判断→不能支持的判断；正负结果都列，提供复现/恢复命令。
- [ ] 更新能力表、当前时间语义合同、异步设计和 README，删除现行文档中的矛盾说法。保留旧研究材料，明确历史身份。
- [ ] 核查 diff 与工作区，提交本任务改动；不提交大批未筛选 out 数据或敏感配置。将必要小验收工件或其哈希清单纳入可追溯交付。
- [ ] 最后回复用户：完成阶段、实际测试/运行、研究判断、外部阻塞、报告路径。不能只说“代码写好了，是否继续”。

## 5. K01–K16 验收清单

| 编号 | 通过条件 | 证据 |
|---|---|---|
| K01 | 精确基线、dirty 保护、执行链身份可追踪 | STATUS/manifest/Git diff |
| K02 | 秒/比特错误修复且 schema 可辨 | 单位测试与新工件 |
| K03 | 测量/接收/请求/服务/完成/安装不混淆 | 手算时间测试与事件流 |
| K04 | zero-cost frozen 正确且默认路径兼容 | 零成本/旧模式回归 |
| K05 | 四组同输入权限、资源、候选及评分 | 快照一致性、trap、破同测试 |
| K06 | 共同未来及候选时刻生成真实存在 | 预测输出与运行事件，非仅文档 |
| K07 | 每候选闭环重跑、真假值输入分离 | 分支产物、泄漏负测试 |
| K08 | 命运/缺失/资源错配全部报告 | 守恒、结果表、筛选计数 |
| K09 | 包长和每星请求率可复算，finite池启用 | trace/profile/压力产物 |
| K10 | 完整耗时与纯推理、实测与注入分别标注 | benchmark报告和环境 |
| K11 | 后台更新时旧表真正在转发，完成后才安装 | 跨版本手算测试及事件 |
| K12 | 五执行模式公平、调用和查询成本分开 | 模式矩阵与计算池记录 |
| K13 | DDQN固定推理与训练边界明确 | 接口测试/checkpoint身份或外部阻塞证据 |
| K14 | 统计单元正确、开发确认分离、负结果可终止 | 分析测试/冻结矩阵/样本量报告 |
| K15 | 端到端流水线及恢复/拒绝路径实跑 | acceptance、report、receipt复验 |
| K16 | 回归、文档、能力表与最终报告一致 | 最终测试原文、diff、REPORT |

核心项 K01–K12/K14–K16 未完成不能宣称工程整体完成。K13 缺硬件/框架/检查点时准确列外部阻塞，不把简单评分器结果当 DDQN 结果。没有正式/远端执行授权时工程可交付、研究结果仍为诊断，必须明确两种状态。

## 6. 继续执行的纪律

每完成一个阶段：更新账本→运行该阶段验收→失败则修复→通过后进入下一阶段。只把有证据的不可自行解决事项升级给用户。路径命名、模块拆分、普通依赖修复、测试修复和本计划已有默认参数，不需要每次询问。

不要以“为了严谨”无限增加审查轮次，也不要把“连续完成”理解为必须制造正结果。最多一次实现自检加一次独立/交叉复核；具体失败修复后只重审受影响判据。新增非阻塞改进进 BACKLOG，不扩张本次完成标准。

这份任务书的终点是：从一个明确命令开始，可以复现正确时间语义下的四组比较、有限算力压力和异步复用诊断，得到完整命运/成本/证据报告；收益是否存在由结果决定。
