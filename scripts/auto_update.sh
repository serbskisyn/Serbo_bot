#!/bin/bash
# Auto-Pull und Neustart wenn neue Commits auf main vorhanden sind

REPO_DIR="/home/pi/Serbo_bot"
LOG="$REPO_DIR/logs/auto_update.log"
SERVICE="serbo_bot"

cd "$REPO_DIR" || exit 1

git fetch origin main --quiet 2>&1

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    git pull --quiet 2>&1
    sudo systemctl restart "$SERVICE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Update: $(git rev-parse --short HEAD) — Bot neu gestartet" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Kein Update ($(git rev-parse --short HEAD))" >> "$LOG"
fi
