# 无线网络智能规划系统 Linux 离线部署

使用与交付包匹配的银河麒麟系统、CPU 架构、本地浏览器和 CPython 3.12。请准备同架构、同 Python ABI 的本地 wheel 集合，包括 `app/algorithm-service/requirements.txt` 中各项及其传递依赖。由交付方在相同银河麒麟版本上实际运行后确认依赖及系统兼容性。

将 Linux 交付包解压到当前用户拥有且可写的本地目录。运行目录应位于支持 Linux 用户权限和文件锁的本地文件系统。使用普通用户，从解压目录执行以下命令。

```sh
sh install-offline.sh /path/to/wheelhouse
sh radio-planning.sh start
```

可以通过环境变量 `RADIOPLANNING_SYSTEM_PYTHON` 指定受控的 CPython 3.12 路径。离线安装只从给定目录解析 wheel，虚拟环境保存在 `runtime/venv`。

在同一台设备的浏览器中打开启动命令打印的回环地址。地址也保存在 `app/data/runtime/访问地址.txt`。首次启动由程序初始化本地数据库。后续启动使用既有数据库，请保管整个 `app/data/runtime`。

```sh
sh radio-planning.sh stop
```

部署验收可核对首次启动和数据库初始化、规划或故障作业执行与回读、停止后重新启动、已保存记录保留。启动日志位于 `app/data/runtime/logs/launcher.log`。
