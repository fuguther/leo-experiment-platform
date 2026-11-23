# leo-experiment-platform

LEO 路由研究的受控实验平台。研究问题、算法目标与科学状态唯一来自
`fuguther/leo-routing-domain-map/main:reasoning/state.yaml` 及其研究规则；
本仓库只拥有可执行实现、实验合同、运行证据与工程验证。

平台当前事实和未决正确性边界见 [STATUS.md](STATUS.md)，操作规则见
[AGENTS.md](AGENTS.md)。

## 平台主线

一条可复核实验必须沿着同一条链路前进：

```text
world / topology / demand
  -> legally observed information at the decision cutoff
  -> action or policy
  -> shared service and other nodes' responses
  -> complete offered-cohort fate and outcome
  -> immutable evidence and independent verification
```

平台的长期职责是稳定提供这六个阶段以及它们之间的身份绑定：

- **world/demand**：轨道、几何、OD、人口、流量和链路物理；
- **information**：测量、发送、到达、cache 接受、策略消费与 cutoff；
- **action**：合法动作、调度、推理、执行和 fallback；
- **response**：非抢占服务、FIFO、共享控制/数据容量、rematch 与后继策略；
- **outcome**：完整 packet fate、E2E、p99、PDR、deadline 和资源成本；
- **evidence**：config/input/code/release/run/receipt 的不可变身份与独立核验。

## 平台不变式

- 可复现：固定代码、配置、输入、依赖和随机化范围，并说明实际重放程度。
- 可追溯：结果绑定唯一代码、输入、release、run 和 receipt 身份。
- 可干预：对照共享同一 world/cohort/配置，只改变声明的动作或机制。
- 可归因：区分离线真值、在线可用信息和反事实后果，不把相关性冒充机制。
- 代价可量化：通信、计算、等待、存储和共享服务成本必须进入同一比较。

## 活动代码与实验叶子

`CODE/leo_sim/` 是仿真主干；`CODE/scripts/remote/` 与
`CODE/experiment_platform/` 的 release、identity、receipt、pullback 和
V2 analysis 是证据主干。

T1、time-alignment、network-arm、population/scene calibration 以及各类
诊断驱动属于**实验叶子**。它们保留是为了复现具体实验，不得反过来定义平台
公共语义。新研究应先复用主干的 world、information、action、response、
outcome 和 evidence 接口；只有无法由配置或插件表达时才新增代码。

外部算法放在独立 policy plugin/module 中，不直接扩写仿真 kernel。当前没有
已选定的最终算法；仓库中的 DDQN、TAP-DAR、Cohort Residual、PCVR 或其他原型
只代表候选或基线，不能从“存在代码”推出科研结论。

`legacy_gateway` 编译合同和旧 receipt/schema 读取器属于兼容面：只保留验证既有
证据所需的读取能力，不再扩展旧运行链。

## 目录

```text
CODE/
├── leo_sim/                # 仿真、信息、动作、服务与 cohort 事实
├── experiment_platform/    # 实验叶子、诊断、授权和 V2 分析
├── scripts/remote/         # release/run/pullback 与维护
├── work/                   # 通用工作包 schema 与 finalization
├── data/                   # 有来源说明的输入数据
└── population_map/         # 人口场景输入

EXPERIMENTS/
├── contracts/              # 长期契约与最小夹具
└── templates/              # 请求模板
```

`CODE/` 是固定的 Python 导入根。运行实例写入 release/run 或显式输出位置，
不写回契约目录。

## 验证

```bash
python3 -B CODE/scripts/maintenance_check.py --repo .
python3 -m pytest -q
```

这些命令验证代码路径和本地行为。它们不授予 formal 运行资格，也不建立科研
结论。正式实验还必须经过编译、独立审阅、明确授权、VM 运行和 receipt 重算。

只从干净、已提交、已推送的完整 commit 构造不可变 release。所有产生科研数值
的仿真、压力测试、决策计时和训练在 VM 执行。

Canonical 发布默认要求本地 `main` HEAD、GitHub `refs/heads/main` 和发布 commit
三者精确一致；安装并完整校验后才会原子更新 VM `/data/论文/leo-t1-wt/CURRENT.json`。
VM 不要求能访问 GitHub：Mac 在 promotion 前生成自哈希 live-main proof，VM 校验
proof 与 installed release 后将其嵌入 CURRENT。proof 过期、commit/ref 不符或
release 身份不符都会拒绝 promotion。
普通 `run-release-remote.sh` 默认拒绝非 current release；复现旧证据必须显式传
`--allow-historical-release`。需要未合并诊断包时必须显式使用
`publish-release-remote.sh --development-release`，该包不会成为 current。
`CODE/experiment_platform/code_pointer.py` 仅为历史 execution-chain 身份兼容
保留，canonical release/run 路径不得导入或使用它。
旧 `push-remote.sh` 部署入口同样要求干净本地 `main` 且精确等于 live GitHub
`refs/heads/main`，并在真正远端安装前再次复核，不能作为绕过 canonical release 的旁路。

`SOURCE-COMMIT.txt` 只记录固定的上游模拟器 pin，不是平台 release 身份；平台提交由 `.deployment_commit`、release manifest 和 run receipt 绑定。旧 `t1-vm.sh` 已退役，不能被当作新发布入口。
