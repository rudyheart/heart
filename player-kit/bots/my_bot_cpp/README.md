# C++ 策略模板

在 `strategy.hpp` 中实现 `Strategy`，包含 `bot_sdk.hpp` 并使用 `bot_sdk::` 命名空间下的状态类型。新增源码时，在 `CMakeLists.txt` 中用 `target_sources(bot PRIVATE ...)` 登记。

使用 Python 3.9 或以上版本、CMake 3.16 或以上版本和支持 C++17 的本机编译工具链。把 `cmake` 和编译工具加入 PATH。Windows 使用 MinGW 时，工具链需包含 `g++` 和 `mingw32-make`。在材料包根目录运行下面的命令。

```sh
python -m engine my_bot_cpp bot002
```

提交时将模板内容放到本队仓库根目录，保留 `bot.json`、`README.md`、`strategy.hpp`、`CMakeLists.txt`。在 README 说明策略、依赖和代码来源，推送到 `master` 前，先用实际提交目录跑一局。
