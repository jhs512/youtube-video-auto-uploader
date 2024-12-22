#!/bin/bash
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
    echo "Process ID file not found"
fi 