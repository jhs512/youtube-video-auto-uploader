#!/bin/bash

# 가상환경 활성화 (bin이 있으면 사용, 없으면 Scripts 사용)
if [ -f "env/bin/activate" ]; then
    source env/bin/activate
else
    source env/Scripts/activate
fi

# 프로그램 실행
python run.py