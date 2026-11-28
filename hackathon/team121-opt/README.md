# 曝光领地 · team121 优化版

参赛策略在 `bots/team121_agent/`，包含官方清单、依赖文件、README 和完整源码。仅依赖 Python 标准库，支持比赛要求的 Python 3.9+。旧 v2 的策略源码保存在 `baselines/v2/bots/team121_agent`，未修改。运行下列命令时进入本 README 所在目录。

```sh
python tools/verify_recovered.py
python -m unittest discover -s tests -v
python -m unittest discover -s baselines/v2/tests -v
python tools/series.py --pairs opt/bot002 opt/v2 --seeds 41 --mode engine --jobs 1 --out rerun/engine
python tools/aggregate.py rerun/engine
python tools/verification.py rerun/engine
```

官方 SDK 齐备时，从仓库中的 `player-kit` 目录运行：

```sh
python -m engine ../hackathon/team121-opt/bots/team121_agent bot002 --seed 42
python -m engine bot002 ../hackathon/team121-opt/bots/team121_agent --seed 42
```

对局运行器支持三种模式。`engine` 直接调用原版对手和官方 Game，在进程内构造回合状态，测量策略调用耗时。`http` 由官方 Match、ProcessBot、SDK 启动两端，并逐帧校验回放。`http-compat` 供缺少 sdk/ 的材料使用，仍由官方 Match 执裁，双方通过本包的本地 HTTP 主机与裁判通信。三种模式各建独立输出目录。归档包含明确的模式标记、实际地图指纹、逐回合数据、终局和错误状态。

[实测结果](docs/RESULTS.md) · [官方规则审计](docs/AUDIT.md) · [策略设计](docs/STRATEGY.md) · [正式对战](docs/OFFICIAL.md) · [变更记录](CHANGELOG.md)

## 复核随包对战证据

在本目录运行以下命令，提取证据并调用官方规则复算。玩家计时取决于本机负载，同图重赛可能选择不同路径；已归档回放可按原输入精确复核。

```sh
python tools/unpack_evidence.py rerun/archived
python tools/verification.py rerun/archived/holdout-engine
python tools/verification.py rerun/archived/protocol-http-compat
```

[Codex 续做入口](CODEX_HANDOFF.md)记录当前基线、复现命令及待验收项。
