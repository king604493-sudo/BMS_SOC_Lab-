#!/bin/bash
set -e
cd "$(dirname "$0")"
echo '准备仿真示例文件……'
python3 make_demo.py
printf '示例文件已经准备好。\n之后双击 start.command 即可运行。\n'
read -r -p '按回车结束……' _
