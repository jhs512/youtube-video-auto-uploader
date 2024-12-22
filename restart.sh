#!/bin/bash

# 현재 실행 중인 프로세스 종료
if [ -f process.pid ]; then
    pid=$(cat process.pid)
    echo "Gracefully stopping YouTube Uploader (PID: $pid)..."
    kill -SIGINT $pid
    
    # 프로세스가 자연스럽게 종료되기를 기다림
    while kill -0 $pid 2>/dev/null; do
        echo "Waiting for the current upload to finish..."
        sleep 5
    done
    
    rm process.pid
    echo "YouTube Uploader stopped successfully"
else
    echo "No running process found"
fi

# 잠시 대기
sleep 2

# 프로그램 재시작
source env/Scripts/activate
nohup python run.py > youtube_uploader.log 2>&1 &
echo $! > process.pid
echo "YouTube Uploader restarted with PID $(cat process.pid)" 