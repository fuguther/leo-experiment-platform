## 状态

- 阶段:DRAFT / READY / BLOCKED
- owner:
- base SHA:
- head SHA:
- write set(改动路径):

## 合并门

对照 `WORKING-MODEL.md` 第三节,三项必须全部可判定:

- [ ] CI 对 **exact head SHA** 真实跑完且 success
      (`cancelled` / `skipped` / `in_progress` 一律不算)
- [ ] 全部判据 PASS,或仅剩非阻塞 backlog(见 `BACKLOG.md`)
- [ ] 已与最新 `origin/main` 对账,无冲突、无落后

## 证据

- 改了什么:
- 为什么:
- 真实 passed / failed / skipped:
- 复现命令:

## 判据结果

| 判据 | 结果 | 复现命令 |
|---|---|---|
| C1 | | |

## 非阻塞 backlog

集外发现一律进 `BACKLOG.md`,**不在本 PR 阻塞合并。**

## blocker / 恢复条件

无则填「无」。
