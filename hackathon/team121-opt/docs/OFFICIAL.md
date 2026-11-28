# 复现官方对局

从 hackathon/team121-opt 执行 README 中的命令。`tools/series.py` 同种子双席位，`--pairs opt/bot002 opt/v2 opt/bot001 opt/bot003` 指定对手；`--pairs v2/bot002 v2/bot001` 单独建立旧版基准。输出每场的比分、领地、星格、报废、非法指令、超时、过程退出和计时。面积包含开局九格。使用单任务 --jobs 1 可减少本机调度对耗时的影响。生成器参数和地图哈希随结果保存。引擎按真实机器时间决定超时，同地图重跑存在时间预算导致的路径差异。

官方 SDK 齐备时用 --mode http。它执行原生 Match+ProcessBot+SDK，并用官方 verify_replay 复算日志和回放。只含 engine 和 bots 时仍可用 --mode engine 跑原版对手和原版 Game，或者 --mode http-compat 检查 HTTP 全链路。后者启动本项目的兼容主机，输出区分三种模式。tools/aggregate.py 汇总同一种模式；tools/verification.py 校验归档。

训练使用 41、53、67、97 等种子。初次独立评估预选 2027、2063、2089、2111、2137、2179、2203、2221、2267、2293，覆盖生成器所有五种风格。四场配对要求同一对手、同一地图上新旧版本的两个席位都存在。训练消融观察实际对局、扩大搜索范围和边界换线的收益，并分别记录每个版本。
