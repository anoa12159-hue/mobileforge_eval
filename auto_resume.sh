#!/bin/bash
# 等待当前实验跑完，然后自动 resume 失败的 run
# 用法: 在 tmux 中运行: bash auto_resume.sh

STATUS_FILE="experiments/20260428_181902_mobileforge-compare/status.json"
cd "$(dirname "$0")"
source env.sh

echo "[auto_resume] 监控实验状态，等待所有 generating 任务完成..."

while true; do
    generating=$(python3 -c "
import json, sys
s = json.load(open('$STATUS_FILE'))
print(s.get('counts', {}).get('generating', 0))
" 2>/dev/null)

    if [ "$generating" = "0" ] || [ -z "$generating" ]; then
        echo "[auto_resume] 没有正在运行的任务了 (generating=$generating)"
        break
    fi

    echo "[auto_resume] $(date '+%H:%M:%S') 还有 $generating 个任务在运行，60s 后再检查..."
    sleep 60
done

echo "[auto_resume] 开始 resume 跑失败的 run..."
python -m evaluator run -c exp_config.yaml --resume
