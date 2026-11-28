# Java 策略模板

在 `Strategy.java` 中实现 `bot.Strategy`，从 `bot.sdk` 导入状态类型。辅助 `.java` 文件和 `Strategy.java` 放在同一层，由构建工具复制到 Maven 源码目录。新增依赖时，在 `pom.xml` 中声明。

使用 Python 3.9 或以上版本、JDK 17 和 Maven。把 `mvn` 加入 PATH，并设置 `JAVA_HOME` 指向 JDK 或把 JDK 的 `bin/` 加入 PATH。首次构建由 Maven 下载依赖。在材料包根目录运行下面的命令。

```sh
python -m engine my_bot_java bot002
```

提交时将模板内容放到本队仓库根目录，保留 `bot.json`、`README.md`、`pom.xml` 和 `Strategy.java`。在 README 说明策略、依赖和代码来源，推送到 `master` 前，先用实际提交目录跑一局。
