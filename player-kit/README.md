# 选手材料包

## 赛题和 Bot 的任务

你要编写一个 Bot，在 41×41 的棋盘上控制一个**束斑**。束斑是电子束在基板上的落点，可以理解为你的棋子。每回合，裁判把当前局面发给双方，双方各选一个移动方向，裁判收集回复并统一结算。

已占领的格子构成己方的**图形区**。开局时双方各有一块 3×3 的校准区，束斑从本方校准区中心出发。走到己方图形区外时，束斑沿途留下**轮廓**。带着轮廓回到己方图形区就会**闭合**，裁判按规则将轮廓格和围住的可占领格划入己方图形区。普通格价值 1 分，关键图形格价值 3 分，得分还要扣除图形区的周长成本。

撞到缺陷、走出棋盘或让任一方的束斑扫过自己的轮廓，都会导致自己的束斑报废。报废后停机 5 回合，在出生点重启。正常结束时，裁判依次比较第 600 回合后的得分（高者胜）、累计领先回合数（多者胜）和累计响应时间（少者胜）。

## 先跑一局

安装 Python 3.9 或以上版本。在材料包根目录打开终端运行以下命令。这个目录包含 `engine/` 和 `bots/`，从本项目仓库进入时先运行 `cd player-kit`。

```sh
python -m engine my_bot_python bot002 --seed 42
```

`my_bot_python` 是可直接运行的模板，`bot002` 是随包提供的贪心基准 Bot。命令把第一个 Bot 放在 0 号座位，第二个放在 1 号座位。`--seed 42` 固定地图种子，便于复现同一张地图。省略 `--seed` 时随机选取种子。

一局结算后，终端会输出完成回合数、双方得分、响应错误次数、胜负和回放文件路径。正常打满 600 回合时命令返回 `0`，提前技术判负或启动失败时返回 `1`。

试跑 C++ 或 Java 模板时，将命令里的 `my_bot_python` 换成对应的目录名。本地引擎会在临时目录构建 C++ 和 Java Bot，再启动对局。

| 模板 | 除 Python 之外的本地依赖 |
| --- | --- |
| `my_bot_python` | 模板使用 Python 标准库 |
| `my_bot_cpp` | CMake 3.16 或以上版本、支持 C++17 的编译工具链。将 `cmake` 加入 `PATH`，Windows 使用 MinGW 时还需加入 `g++` 和 `mingw32-make` |
| `my_bot_java` | JDK 17 和 Maven。将 `mvn` 加入 `PATH`，并设置 `JAVA_HOME` 或将 JDK 的 `bin/` 加入 `PATH` |

### 查看回放

在浏览器打开 [replay.html](replay.html)，可以先观看随包提供的基准 Bot 对局。每局结算后，本地引擎会把回放保存到 `replays/`。刷新页面并从下拉列表选择新对局，或用 **打开 JSON** 载入终端显示的文件。可以逐回合查看棋盘、指令、事件和比分。

## 从模板创建自己的 Bot

1. 在材料包根目录运行 `git -C bots clone "<本队仓库地址>"`，把队伍仓库克隆到 `bots/` 下。以下用 `bots/team001/` 举例。
2. 从下表选一种语言，将对应模板目录里的**文件**复制到本队仓库根目录。保留模板中的 `bot.json` 和 `README.md`。`bot.json` 里的 `language` 决定平台如何构建 Bot，队伍的 `README.md` 用于说明策略、使用方法、依赖和代码来源。
3. 编辑本队仓库里的策略文件。平台和本地引擎都通过 SDK 调用这个 `Strategy` 类。

| 语言 | 模板策略文件 | `bot.json` 的 `language` | 还需保留的文件 |
| --- | --- | --- | --- |
| Python | [bots/my_bot_python/strategy.py](bots/my_bot_python/strategy.py) | `python` | `requirements.txt`，无额外依赖时可留空 |
| C++ | [bots/my_bot_cpp/strategy.hpp](bots/my_bot_cpp/strategy.hpp) | `cpp` | `CMakeLists.txt`，新增源文件时用 `target_sources(bot PRIVATE ...)` 登记 |
| Java | [bots/my_bot_java/Strategy.java](bots/my_bot_java/Strategy.java) | `java` | `pom.xml`，新增依赖在这里声明。辅助 `.java` 文件与 `Strategy.java` 放在同一层 |

### 要实现的策略接口

SDK 是材料包提供的开发工具，负责收发裁判消息，并将输入转为策略可以直接读取的对象。你的代码通过 `Strategy` 读取这些对象并决定方向。

每局开始时，SDK 用 `InitState` 创建一个 `Strategy` 实例。可以在构造函数里保存本方座位、预处理地图。之后 SDK 每回合把 `TurnState` 传给同一个实例的 `act` 方法，可以在实例成员中保存本局的策略数据。

| 语言 | 构造函数 | 回合方法 |
| --- | --- | --- |
| Python | `__init__(self, init: InitState)` | `act(self, state: TurnState) -> str` |
| C++ | `Strategy(const bot_sdk::InitState& init)` | `std::string act(const bot_sdk::TurnState& state)` |
| Java | `public Strategy(InitState init)` | `public String act(TurnState state)` |

Python 的状态类型从 `sdk` 导入。C++ 在 `strategy.hpp` 中包含 `bot_sdk.hpp`，构造函数和 `act` 放在 `public` 下。Java 的策略类使用 `package bot`，状态类型从 `bot.sdk` 导入。

`act` 返回一个长度为 1 的方向字符串。`U` 向上，`D` 向下，`L` 向左，`R` 向右。每回合的整次请求往返限时 200 毫秒，停机时仍须返回一个有效方向，此时指令仅用于计时和错误判定。在场时，收到反向指令或没有收到有效指令，束斑沿用当前方向。格式正确、按时返回的反向指令算有效回复。

### 写一个能运行的策略（Python）

把下面的代码放进本队仓库的 `strategy.py`。这个策略在己方图形区内优先直走，离开后优先左转，尝试绕回己方图形区闭合。每次选择方向时，它根据起步格计算步数，逐格检查棋盘边界、缺陷和己方轮廓。

坐标原点 `(0, 0)` 在左上角，`x` 向右递增，`y` 向下递增。束斑在 `(10, 12)` 朝上时返回 `R`，会尝试走到 `(11, 12)`。如果 `(10, 12)` 属于加速区，第一步结算后仍可移动时还会尝试走到 `(12, 12)`。

```python
from engine.rules import DIRS, DX, DY, OPP
from sdk import InitState, TurnState


LEFT = {"U": "L", "L": "D", "D": "R", "R": "U"}


class Strategy:
    def __init__(self, init: InitState):
        self.seat = init.seat

    def act(self, state: TurnState) -> str:
        me = state.me
        if not me.alive:
            return me.dir

        board = state.map
        steps = board.move_steps(me.x, me.y)
        mine = str(self.seat)
        in_territory = state.owner[me.y][me.x] == mine
        preferred = me.dir if in_territory else LEFT[me.dir]
        directions = [preferred] + [d for d in DIRS if d not in (preferred, OPP[me.dir])]

        for direction in directions:
            x, y = me.x, me.y
            for _ in range(steps):
                x += DX[direction]
                y += DY[direction]
                if not (0 <= x < board.size and 0 <= y < board.size):
                    break
                if board.terrain[y][x] == "#" or state.contour[y][x] == mine:
                    break
            else:
                # 这个方向的每一步都通过了检查。
                return direction

        return me.dir
```

代码用 `state.owner` 判断当前格是否归己方所有，用 `state.contour` 检查前方格子上的轮廓。如果逐格检查后没有找到可选方向，仍返回当前朝向，让裁判照常结算。

## 策略输入类型

以下字段表采用 Python 和 JSON 的字段名。C++ 的 `InitState`、`TurnState`、`Beam`、`Point` 和 `BoostZone` 直接读成员，`GameMap` 用同名方法读取。Java 通过访问器读取，例如 `state.turn()`，表中标出了改用驼峰命名的字段。

### `InitState` · 每局开局设置

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `seat` | 整数 | 本方座位，取 `0` 或 `1`。对手座位为 `1 - seat`。每局都从开局数据读取座位 |
| `max_turns`（Java `maxTurns()`） | 整数 | 本局回合上限，初赛为 `600` |
| `map` | `GameMap` | 本局共用的静态地图 |

### `GameMap` · 静态地图

网格用行字符串表示，行和列下标都从 `0` 开始。读取 `(x, y)` 时先用 `y` 取行，再用 `x` 取字符。比如 `(10, 12)` 的地形是 `terrain[12][10]`。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | 字符串 | 地图编号 |
| `stage` | 字符串 | 赛段，初赛为 `prelim` |
| `size` | 整数 | 棋盘边长，初赛为 `41` |
| `terrain` | 行字符串序列 | `.` 普通格，`#` 缺陷格（不可通行），`*` 关键图形格 |
| `calib` | 两个 `Point` | 按座位顺序记录双方出生点，每个点是该方 3×3 校准区的中心 |
| `boost_zones`（Java `boostZones()`） | 三个 `BoostZone` | 整局可用的加速区。每个区有一个标记点 `marker` 和覆盖格集合 `cells`，两者的坐标均为 `Point` |

`Point` 有整数坐标 `x` 和 `y`。Python 可用 `point.x`、`point.y` 或解包 `x, y = point`，C++ 用 `point.x` 和 `point.y`，Java 用 `point.x()` 和 `point.y()`。HTTP JSON 用 `[x, y]` 表示一个点。

本回合尝试走几格由**起步格**决定。起步格属于任一加速区的 `cells` 就尝试走 2 格，否则尝试走 1 格。两步使用同一个方向，每步独立结算。

### `TurnState` · 行动前的动态局面

第 1 回合收到的是开局局面，之后收到的是上一回合结算后的快照。`beams`、`score`、`lead_turns`、`time_us` 都有两个元素，固定按 `[0 号方, 1 号方]` 排列。比如 `seat` 为 `1` 时，本方得分是 `score` 的第 2 个元素。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `turn` | 整数 | 本次要执行的回合号，从 `1` 开始 |
| `owner` | 行字符串序列 | 每格的归属。`.` 无主，`0` 和 `1` 表示归属方 |
| `contour` | 行字符串序列 | 每格的轮廓。`.` 无轮廓，`0` 和 `1` 表示轮廓所属方 |
| `beams` | 两个 `Beam` | 双方束斑的状态 |
| `score` | 两个整数 | 双方当前得分，已扣除周长成本 |
| `lead_turns`（Java `leadTurns()`） | 两个整数 | 双方得分严格领先的累计回合数 |
| `time_us`（Java `timeUs()`） | 两个整数 | 裁判已累计的双方回合请求往返时间，单位为微秒 |

`terrain`、`owner` 和 `contour` 是同一张棋盘上的三层信息。例如同一格可以是 `*` 地形、`.` 归属和 `1` 轮廓，表示无主的关键图形格上有 1 号方的轮廓。读取归属或轮廓时，比较字符 `'0'`、`'1'`。

### `Beam` · 单个束斑

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `seat` | 整数 | 束斑所属座位 `0` 或 `1` |
| `x`、`y` | 整数 | 束斑坐标。停机时保留最后记录的位置 |
| `dir` | Python `str`（长度为 1），C++ 和 Java `char` | 当前朝向 `U`、`D`、`L` 或 `R`。开局时 0 号方朝 `U`，1 号方朝 `D` |
| `alive` | Python、C++ `bool`，Java `boolean` | 束斑是否在场，停机时为假 |
| `speed` | 整数 | 上次结算记录的步数设置，取 `1` 或 `2`。开局、报废和重启时设为 `1`。要计算本回合步数，用当前坐标查询地图 |
| `restart_in`（Java `restartIn()`） | 整数 | 距重启还剩的停机回合数。在场时为 `0`，为 `1` 时将于本回合末重启 |

### 三种语言怎样读取这些值

下面的 `init` 是构造时保存的开局设置，`state` 是本轮 `act` 的参数，`me` 是表中第一行取出的己方束斑。

| 读取内容 | Python | C++ | Java |
| --- | --- | --- | --- |
| 本方束斑 | `state.me` | `state.beams[init.seat]` | `state.beams().get(init.seat())` |
| 对方束斑 | `state.enemy` | `state.beams[1 - init.seat]` | `state.beams().get(1 - init.seat())` |
| 本方得分 | `state.score[state.seat]` | `state.score[init.seat]` | `state.score().get(init.seat())` |
| `(x, y)` 的地形 | `state.map.terrain[y][x]` | `init.map.terrain()[y][x]` | `init.map().terrain().get(y).charAt(x)` |
| `(x, y)` 的归属 | `state.owner[y][x]` | `state.owner[y][x]` | `state.owner().get(y).charAt(x)` |
| 本回合尝试步数 | `state.map.move_steps(me.x, me.y)` | `init.map.move_steps(me.x, me.y)` | `init.map().moveSteps(me.x(), me.y())` |
| 将当前朝向作为回复 | `return me.dir` | `return std::string(1, me.dir);` | `return String.valueOf(me.dir());` |

| 字段 | Python | C++ | Java |
| --- | --- | --- | --- |
| `terrain`、`owner`、`contour` | `tuple[str, ...]` | `std::vector<std::string>` | `List<String>` |
| `calib` | `tuple[Point, Point]` | `std::array<Point, 2>` | `List<Point>` |
| `boost_zones` | `tuple[BoostZone, ...]` | `std::vector<BoostZone>` | `List<BoostZone>` |
| `BoostZone.cells` | `tuple[Point, ...]` | `std::vector<Point>` | `Set<Point>` |
| `beams` | `tuple[Beam, Beam]` | `std::array<Beam, 2>` | `List<Beam>` |
| `score`、`lead_turns`、`time_us` | `tuple[int, int]` | `std::array<int, 2>` | `List<Integer>` |

### Python 的本地推演

Python 还可以用 `state.seat` 读取自己的座位，用 `state.game` 获取从快照还原的本地 `Game`。每次尝试先执行 `sim = state.game.copy()`，再用 `sim.step([cmd0, cmd1])` 按座位顺序模拟双方指令。可以将对手的指令设为 `None` 来模拟对手保持朝向，用 `sim.beams[state.seat].alive` 或 `sim.score(state.seat)` 查看结果。

推演中的 `sim.owner` 和 `sim.trail` 是一维整数列表。用 `sim.idx(x, y)` 取下标，`-1` 表示无主或无轮廓，`0` 和 `1` 表示对应座位。Python 模板的 `safe` 方法使用了这套推演接口。

## 验证并提交

在材料包根目录，用实际提交目录和基准 Bot 对战。`python -m engine P1 P2` 的两个位置均可填 `bots/` 下的目录名，或包含 `bot.json` 的目录路径。

```sh
python -m engine team001 bot002 --seed 42
python -m engine bot002 team001 --seed 42
```

用同一个种子交换双方顺序，可以在同一张地图上检查 Bot 拿到两个座位时的表现。若 Python 策略声明了额外依赖，先在运行引擎的 Python 环境安装，例如 `python -m pip install -r bots/team001/requirements.txt`。

确认终端输出 `完成 600/600 回合` 且本方错误次数为 `0`，再看回放是否符合策略预期。按赛事规章，将验证过的代码推送到本队仓库的 `master` 分支即为提交。提交根目录中的 `bot.json` 和非空 `README.md` 使用 UTF-8 编码。

## HTTP 消息与时限

本地引擎自动启动两个 Bot 进程，各自监听 `127.0.0.1` 的一个端口。同一局发给双方的请求路径使用相同的 `run_id`。

| 裁判请求 | 成功回复 | 超时或失败 |
| --- | --- | --- |
| `GET /v1/health` | HTTP `200`，JSON 包含 `"ready": true` | 启动后 10 秒内未就绪，技术判负 |
| `POST /v1/matches/{run_id}/init` | 2 秒内返回 HTTP `2xx`，SDK 返回 `204` | 记 1 次错误 |
| `POST /v1/matches/{run_id}/turns/{turn}` | 回合时限内返回 HTTP `200` 和回合指令 JSON | 超时、格式无效或连接失败，记 1 次错误 |
| `POST /v1/matches/{run_id}/end` | 结束通知时限为 1 秒，SDK 返回 `204` | 按已确定的结果结算 |

请求体使用 UTF-8 JSON。开局消息包含 `type: "init"` 和 `InitState` 的字段，地图还可以有可选的显示名称 `name`、`en`。回合消息包含 `type: "turn"` 和 `TurnState` 的回合字段。回合回复的 `turn` 必须是与请求相同的整数，`cmd` 为方向字符串。

```json
{"turn": 1, "cmd": "R"}
```

结束消息包含 `type: "end"`、已结算的回合数 `turn`、胜方 `winner`（`0`、`1` 或 `null`）、结束原因 `reason` 及双方的 `score`、`lead_turns`、`time_us`。Python SDK 支持可选的 `end(self, message: dict) -> None` 方法接收这条消息。

一方在单局累计 10 次错误，或在开局、回合阶段程序退出，都会触发技术判负。
