# Team121 — Exposure Territory Bot v2.0

目标：为 2026 软件部黑客松「曝光领地」提供一套更具进攻性的 Python 策略。版本 2.0 在原型基础上，吸收了公开圈地赛冠军/亚军源码中验证有效的策略思想，并按你提供的官方规则摘要重新实现。

> **重要边界：没有“必胜”算法。** 这个版本尚未在官方 `player-kit.zip` 和裁判引擎中完成实战回归，因为本轮对话没有收到该压缩包。工程内 8 项测试是本项目的合成单元测试，不等同于官方 6 项引擎一致性测试，也不能证明已兼容真实 SDK。

## v2.0 改了什么

1. **从矩形升级为多形状闭合搜索**：搜索 2、3、4 段正交折线路线，允许从己方领地内部进入边缘后出门，不再要求矩形底边必须完整落在现有领地边界上。
2. **精确模拟圈地 flood-fill**：候选路线先做轻量筛选，再按「己方领地 + 己方旧轮廓 + 候选新轮廓」做边界泛洪，得到真正可圈的格子。
3. **按真实加速格计算对手到达时间**：旧版把 BFS 距离统一除以 2，等价于假设对手每回合全图走两格。新版在 `(x,y,heading)` 状态上做回合 BFS，仅当对手回合起点位于加速区时才走两步，并遵守不能掉头、缺陷和对手自有轮廓约束。
4. **动态防守**：自己在领地外时，每回合重新计算对手到己方轮廓的最短到达回合；对手可能先切线时，放弃旧计划并优先回家。
5. **主动切线**：发现对手已有未闭合轮廓时，尝试在对手回家前规划短路径切断；下一回合会重新评估，不盲目执行长追击计划。
6. **分数模型适配曝光领地**：关键图形格按 3 分、普通格按 1 分，抢走对方领地按净分差双倍收益估算，同时把己方/对方露边扣分纳入精确局部评分。
7. **时限保护**：候选搜索使用软时间预算；所有异常最终回退为合法方向。合成测试板上的 `act()` 观测约 65–113ms，实际裁判机器耗时仍须实测。

## 为什么没有直接移植网页游戏冠军 Bot

参考网页第 1 期「圈地」的确与本赛题同属画线闭合玩法，但规则不同：对方是 40×40 单挑 / 48×48 四人混战、无缺陷、无加速、单格移动、按领地格数排名；本赛题是 41×41 双人、有缺陷和加速区、关键格不同面值、按净分并扣露边，且有 5 回合重启机制。因此不能直接运行对方二进制或照搬它的估值函数。

本版本借鉴的是算法方向：多段路径搜索、精确 flood-fill 估值、基于实际移动规则的敌方到达时间、风险下的提前回家、以及切断对手长线。具体地形、评分、加速和通信仍按本赛题适配。

源码分析记录见 `SOURCE_REVIEW.md`；策略流程见 `STRATEGY_DESIGN.md`。

## 目录

```text
exposure-territory-engine/
├── README.md
├── STRATEGY_DESIGN.md
├── SOURCE_REVIEW.md
├── bots/team121_agent/
│   ├── __init__.py
│   └── strategy.py
├── tests/test_strategy_core.py
└── tools/run_sweep.ps1
```

## 接入官方 player-kit

1. 解压官方 `player-kit.zip`，确认 `sdk/`、`engine/` 和 `bots/my_bot_python/` 存在。
2. 复制 `bots/team121_agent/strategy.py` 到官方 Python 模板目录，按模板入口调整 import。
3. **保留官方模板的 HTTP 服务入口、`bot.json`、README 和 SDK 文件**；不要用本工程替换通信层。
4. 确认模板确实以 `Strategy(InitState)` 初始化，并逐回合调用 `act(TurnState)`。
5. 在官方 `player-kit` 目录运行：

```powershell
python -m unittest discover tests -v
python -m engine team121 bot002 --seed 42
python -m engine bot002 team121 --seed 42
```

两边均需达到 600/600 回合、0 错误。之后至少再测 30 个不同 seed，记录胜局、平均净分、死亡次数和响应时间 P50/P95/P99。

## 多种子回归（Windows PowerShell）

```powershell
.\tools\run_sweep.ps1 -PlayerKitPath "D:\hackathon\player-kit" -StartSeed 1 -Count 30
```

脚本按两个座位分别对战 `bot002`。它只是基础回归，不代表对其他队伍的胜率。

## 目前仍需官方材料包确认

- `map.calib` 是中心坐标还是其他结构；
- `owner` / `contour` 的空值和归属编码；
- `Beam.alive`、`restart_in` 的确切状态时序；
- `Game.copy().step()` 的真实行为及每回合额外耗时；
- 双步移动时，中间 substep 闭合后第二 substep 的真实结算行为。

在这几项确认前，不要直接把本版本提交为最终参赛版本。
