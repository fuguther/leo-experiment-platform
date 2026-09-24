# leo-experiment-platform

LEO 路由研究的**受控实验环境**。

来源:`leo-direct-sim` @ `549acd8b7cb7a1bb573e7146653f55273999086d`(见 `SOURCE-COMMIT.txt`)

---

## 文档

| 文件 | 内容 | 何时改 |
|---|---|---|
| `CHARTER.md` | 平台本体与五条不变式 | 平台本体变了才改 |
| `WORKING-MODEL.md` | 提交、复核、版本 | 工作方式变了才改 |
| `lines/` | 研究主线(登记表 + 各线) | 线的增删改判死 |

---

## 结构

```
CODE/                     # 导入根,不可改名
├── leo_sim/              # 仿真核心 —— 与源仓库字节级一致
├── experiment_platform/  # 编译 / 授权 / 指标重算
├── scripts/remote/       # 部署与受控执行(仅 .template,无凭据)
├── tests/                # 顶层测试
├── work/                 # 工具模块(被测试导入)
├── data/                 # geoip / traffic
└── population_map/       # 场景生成数据
```

**`CODE/` 这一层不能去掉。** 源码中:

- 测试使用绝对导入 `from CODE.leo_sim import ...`、`from CODE.work.finalize_decision import ...`
- `CODE/experiment_platform/compile_experiment.py:343` 硬编码 `PROJECT_ROOT / "CODE"`

改名会同时破坏导入与编译路径。

---

## 与源仓库的关系

`leo_sim/` 是**字节级复制**。证据链要求 `code_sha256` 可复现,因此:

- 不在本仓库对复制来的源码做"顺手优化"
- 需要改动时,按 `CHARTER.md` 的边界判断是否属于平台本体

---

## 未包含

`CODE/Results/`(运行结果)、`remote.env`(凭据)、遗留结果整理器及其测试、`work/` 中的审计 work package。

运行结果与实验实例不属于平台 —— 见 `CHARTER.md` 第三节。
