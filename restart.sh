#!/bin/bash

# 기존 프로세스 종료
pkill -f "python run.py"

# 재시작을 위해 start.sh 실행
./start.sh