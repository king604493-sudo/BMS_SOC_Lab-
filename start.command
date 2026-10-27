#!/bin/bash
set -e
cd "$(dirname "$0")"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  echo '需要 Python 3.10 或以上版本。推荐通过 python.org 安装 Python 3.12。'
  read -r -p '按回车退出……' _
  exit 1
fi
if [ ! -f .venv/bin/python ]; then
  echo '首次运行：创建本地 Python 环境……'
  "$PYTHON" -m venv .venv
fi
if ! .venv/bin/python -c 'import streamlit,pandas,numpy,matplotlib,openpyxl' >/dev/null 2>&1; then
  echo '首次运行：安装依赖（需要联网；安装完以后可以离线运行）……'
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
printf '\n打开浏览器访问：http://127.0.0.1:8501\n关闭本窗口即可停止运行。\n'
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
