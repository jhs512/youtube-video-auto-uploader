#!/bin/bash

# MAC/Linux용 환경 변수 설정
if [[ "$OSTYPE" == "darwin"* ]] || [[ "$OSTYPE" == "linux-gnu"* ]]; then
    export PATH="$HOME/env/bin:$PATH"
else
    # Windows용 (Git Bash 등에서 실행 시)
    export PATH="env/Scripts:$PATH"
fi

# 가상환경 활성화
source env/bin/activate

# 프로그램 실행
python run.py