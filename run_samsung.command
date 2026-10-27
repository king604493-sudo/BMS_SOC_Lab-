#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python3 -m pip install -r requirements.txt
mkdir -p datasets/30t_measured
if ! find datasets/30t_measured -iname '*.mat' -o -iname '*.zip' | grep -q .; then
  echo "[1/2] 正在下载真实三星30T文件（同型号C/20及动态工况）..."
  python3 data_sources/download_30t_focus.py --output datasets/30t_measured || {
    echo '下载失败，请手动从 https://data.mendeley.com/datasets/9xyvy2njj3/2 下载25℃的C20和Mixed文件，放进 datasets/30t_measured';
    read -r -p '按回车键退出'
    exit 2;
  }
fi
echo '[2/2] 运行双EKF在线OCV对照'
python3 data_sources/samsung30t_pipeline.py --data datasets/30t_measured --output outputs/samsung30t_validation
open outputs/samsung30t_validation || true
