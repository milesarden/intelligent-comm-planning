#!/bin/sh
set -eu
umask 077

if [ "$#" -ne 1 ]; then
    echo "用法：sh install-offline.sh /本地路径/wheelhouse" >&2
    exit 2
fi
bundle=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
wheels=$(CDPATH= cd -- "$1" && pwd -P)
if [ "$(uname -s)" != Linux ]; then
    echo "请在目标 Linux 系统中安装。" >&2
    exit 1
fi

requirements="$bundle/app/algorithm-service/requirements.txt"
if [ ! -f "$bundle/launcher.py" ] || [ ! -f "$requirements" ]; then
    echo "请从完整的 Linux 交付包目录运行安装脚本。" >&2
    exit 1
fi
if ! find "$wheels" -type f -name '*.whl' -print -quit | grep -q .; then
    echo "本地依赖目录中没有 wheel 文件。" >&2
    exit 1
fi

python=${RADIOPLANNING_SYSTEM_PYTHON:-python3.12}
"$python" -B -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and sys.implementation.name == "cpython" else "需要 CPython 3.12")'
venv="$bundle/runtime/venv"
ready="$bundle/runtime/offline-ready"
if [ -f "$ready" ] && [ -x "$venv/bin/python" ]; then
    echo "本目录已经完成离线安装。"
    exit 0
fi
"$python" -B -m venv "$venv"
PIP_CONFIG_FILE=/dev/null "$venv/bin/python" -B -m pip --isolated --disable-pip-version-check --no-cache-dir install \
    --no-index --only-binary=:all: --find-links "$wheels" -r "$requirements"
PIP_CONFIG_FILE=/dev/null "$venv/bin/python" -B -m pip --isolated --disable-pip-version-check check
printf '%s\n' "CPython 3.12" > "$ready"
echo "离线安装完成。使用 sh radio-planning.sh start 启动系统。"
