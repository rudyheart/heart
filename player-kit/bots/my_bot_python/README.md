# Python 策略模板

在 `strategy.py` 中实现 `Strategy.act`。将额外依赖写入 `requirements.txt`，无额外依赖时可留空。

规则常量来自 `engine.rules`。`InitState` 提供开局地图和出生点，`TurnState` 提供当前局面及双方状态。每回合从 `Strategy.act` 返回 `U`、`D`、`L`、`R` 之一，裁判按起步格确定步数。

在材料包根目录使用 Python 3.9 或以上版本运行下面的命令。额外依赖需安装在同一个 Python 环境。

```sh
python -m engine my_bot_python bot002
```

提交时将模板内容放到本队仓库根目录，保留 `bot.json`、`README.md`、`strategy.py`、`requirements.txt`。在 README 说明策略、依赖和代码来源，推送到 `master` 前，先用实际提交目录跑一局。
