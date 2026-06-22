#!/bin/bash
# ──────────────────────────────────────────────
# Music Trend Agent - Daily Runner
# cron이 실행하는 wrapper 스크립트
# 위치: ~/music-trend-agent/run_daily.sh
# ──────────────────────────────────────────────

PROJECT_DIR="$HOME/music-trend-agent"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

echo "========================================" >> "$LOG_FILE"
echo "▶ 실행 시작: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

DATE=$(date +%Y-%m-%d)
FLAG_DIR="$PROJECT_DIR/logs/flags"
mkdir -p "$FLAG_DIR"

# 큐레이션 재설계 기간 — 수집만 유지, 분석/리포트 일시 정지
# 시선 학습용 스냅샷은 계속 쌓되 강도순 리포트는 생성 안 함.
# 재개하려면 아래를 false 로.
PAUSE_ANALYSIS=true

# 1. 스냅샷 (아직 안 됐으면 실행)
if [ ! -f "$FLAG_DIR/${DATE}_snapshot.done" ]; then
    echo "[1/3] pipeline/daily_snapshot.py 실행" >> "$LOG_FILE"
    "$VENV_PYTHON" "$PROJECT_DIR/pipeline/daily_snapshot.py" >> "$LOG_FILE" 2>&1
    if [ $? -ne 0 ]; then
        echo "❌ daily_snapshot.py 실패" >> "$LOG_FILE"
        exit 1
    fi
    touch "$FLAG_DIR/${DATE}_snapshot.done"
    echo "✅ snapshot 완료" >> "$LOG_FILE"
else
    echo "⏭️ snapshot 이미 완료 — 건너뜀" >> "$LOG_FILE"
fi

# Discovery 단계 (orchestrator 내부에서 agent별로 건너뜀 처리)
if [ "$PAUSE_ANALYSIS" != "true" ] && [ ! -f "$FLAG_DIR/${DATE}_orchestrator.done" ]; then
    echo "[2/3] pipeline/orchestrator.py 실행" >> "$LOG_FILE"
    "$VENV_PYTHON" "$PROJECT_DIR/pipeline/orchestrator.py" >> "$LOG_FILE" 2>&1
    if [ $? -ne 0 ]; then
        echo "❌ orchestrator.py 실패" >> "$LOG_FILE"
        exit 1
    fi
    touch "$FLAG_DIR/${DATE}_orchestrator.done"
    echo "✅ orchestrator 완료" >> "$LOG_FILE"
else
    echo "⏭️ orchestrator 건너뜀 (PAUSE_ANALYSIS=$PAUSE_ANALYSIS)" >> "$LOG_FILE"
fi

# Analysis 단계 (별도 플래그로 독립 재실행 가능)
if [ "$PAUSE_ANALYSIS" != "true" ] && [ ! -f "$FLAG_DIR/${DATE}_analysis.done" ]; then
    echo "[3/3] agents.analysis 실행" >> "$LOG_FILE"
    ( cd "$PROJECT_DIR" && "$VENV_PYTHON" -m agents.analysis ) >> "$LOG_FILE" 2>&1
    if [ $? -ne 0 ]; then
        echo "❌ agents.analysis 실패" >> "$LOG_FILE"
        exit 1
    fi
    touch "$FLAG_DIR/${DATE}_analysis.done"
    echo "✅ analysis 완료" >> "$LOG_FILE"
else
    echo "⏭️ analysis 건너뜀 (PAUSE_ANALYSIS=$PAUSE_ANALYSIS)" >> "$LOG_FILE"
fi

echo "" >> "$LOG_FILE"
echo "✅ 완료: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"