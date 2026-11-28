# team121 曝光领地参赛策略

Python 3.9+，只依赖标准库。将本目录作为一个 Bot 目录传给官方 player-kit，入口是 strategy.py 中的 Strategy。可从 player-kit 根目录运行 python -m engine ../hackathon/team121-opt/bots/team121_agent bot002 --seed 42。源代码基于 team121 已公开的 v2 工程约定，在优化分支独立实现。采用带朝向的回合搜索、逐子步碰撞检验、外沿泛洪、双方露边计分和短程截断。
