#!/bin/bash

# start.py를 실행하는 함수
run_start_py() {
    python /app/start.py
}

# 시간 유효성을 확인하는 함수
validate_time() {
    local time=$1
    if [[ ! $time =~ ^[0-9]{2}:[0-9]{2}$ ]]; then
        echo "$1 변수에 올바른 시간이 입력되지 않았습니다. (예: 08:00)"
    fi
}

# 모든 exectime 변수들을 순회하여 유효성을 확인
for i in {1..3}; do
    var="exectime$i"
    validate_time "${!var}" "$var"
done

# 이전에 실행한 시간을 저장하는 변수
last_execution=""

while true; do
    # 현재 시간을 가져옴
    current_time=$(date +%H:%M)

    # 실행될 시간을 도커 환경 변수에서 가져옴
    for i in {1..3}; do
        var="exectime$i"
        execution_time="${!var}"

        # 실행될 시간과 현재 시간이 동일한 경우에만 실행
        if [ "$current_time" == "$execution_time" ]; then
            # 이전에 실행한 시간과 현재 시간이 다른 경우에만 실행
            if [ "$current_time" != "$last_execution" ]; then
                run_start_py
                last_execution="$current_time"
            fi
        fi
    done

    # 30초마다 확인
    sleep 30
done

/bin/bash