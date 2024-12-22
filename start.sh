#!/bin/bash
source env/Scripts/activate
nohup python run.py > youtube_uploader.log 2>&1 &
echo $! > process.pid
echo "YouTube Uploader started with PID $(cat process.pid)" 