# team121 Codex 续做入口

## 当前进度

项目是「曝光领地」team121 Bot，目标是在官方 200ms 请求时限内尽量提高终局胜率。使用 `Madelining/heart` 的 `hackathon/2026-exposure-territory-team121-opt` 分支。首个已发布候选为 `a73033b1993c596d0e0e5b65245f9f65f8a515c1`。策略位于当前目录的 `bots/team121_agent/`，旧 v2 对照在 `baselines/v2/bots/team121_agent/`。仓库根目录的 `player-kit` 包含原版规则引擎、地图生成器、裁判协议、Python 模板和 bot001/002/003。

候选版加入了带朝向的回合 BFS、双子步移动、外沿泛洪、全局露边扣分、短程截断和远端边界搜索。在 10 张独立地图、同图交换两席的引擎测试中，新版对 bot002 为 19/20 胜，旧 v2 为 16/20 胜。新版对旧 v2 为 14/20 胜，对 bot003 为 11/20 胜。新版 80 局均完成 600 回合，48,000 次策略调用零非法方向、零超时、零进程异常，最大耗时 54.72ms。另完成两场 600 回合兼容 HTTP 测试，官方裁判回放已复核。完整指标与实验限制见 [结果](docs/RESULTS.md)。

## 启动一次复核

在仓库根目录执行：

```sh
cd hackathon/team121-opt
python -m unittest discover -s tests -v
python -m unittest discover -s baselines/v2/tests -v
python tools/unpack_evidence.py rerun/archived
python tools/verification.py rerun/archived/holdout-engine
python tools/verification.py rerun/archived/protocol-http-compat
```

## 下一项验收

先检查 `player-kit/sdk/` 是否已经补齐。具备原生 SDK 后运行两边换席的原生 HTTP 对战，并使用官方 `verify_replay` 核对非法指令、超时和最终比分。已有两场 HTTP 使用项目的兼容主机。后续策略优先分析 bot003 的失败回放，使用训练地图试验，并在另一批独立地图确认收益。保留当前候选和旧 v2 作对照，所有发布留在独立优化分支。
