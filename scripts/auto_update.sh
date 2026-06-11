#!/bin/bash
# Auto-Pull und Neustart wenn neue Commits auf main vorhanden sind

REPO_DIR="/home/pi/Serbo_bot"
LOG="$REPO_DIR/logs/auto_update.log"
SERVICE="serbo_bot"

cd "$REPO_DIR" || exit 1

git fetch origin main --quiet 2>&1

# Only redeploy when origin/main is AHEAD of us (genuine new commits to pull).
# Counting HEAD..origin/main avoids the restart loop when the local checkout is
# merely AHEAD of origin (e.g. local commits not yet pushed) — that's not an update.
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)

if [ "$BEHIND" -gt 0 ]; then
    git pull --quiet 2>&1
    sudo systemctl restart "$SERVICE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Update: $BEHIND neue Commit(s) → $(git rev-parse --short HEAD) — Bot neu gestartet" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Kein Update ($(git rev-parse --short HEAD))" >> "$LOG"
fi
