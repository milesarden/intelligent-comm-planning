#!/bin/sh
set -eu
umask 077

bundle=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
python="$bundle/runtime/venv/bin/python"
if [ ! -x "$python" ] || [ ! -f "$bundle/runtime/offline-ready" ]; then
    echo "请先用 install-offline.sh 安装本地依赖。" >&2
    exit 1
fi

case "${1-}" in
    start)
        shift
        exec "$python" -B "$bundle/launcher.py" start --no-browser "$@"
        ;;
    stop)
        shift
        exec "$python" -B "$bundle/launcher.py" stop "$@"
        ;;
    *)
        echo "用法：sh radio-planning.sh start | stop" >&2
        exit 2
        ;;
esac
