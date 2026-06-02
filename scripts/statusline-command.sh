#!/usr/bin/env bash
# Claude Code status line — user@host  cwd  branch  model  $cost  ctx▓▓▓░  5h▓░░░  7d▓░░░

export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"

input=$(cat)

cwd=$(echo "$input"     | jq -r '.cwd // ""')
model=$(echo "$input"   | jq -r '.model.display_name // ""')
ctx_pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
cost=$(echo "$input"    | jq -r '.cost.total_cost_usd // 0')
rl5=$(echo "$input"     | jq -r '.rate_limits.five_hour.used_percentage // 0')
rl7=$(echo "$input"     | jq -r '.rate_limits.seven_day.used_percentage // 0')
rl5_reset=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // 0')
rl7_reset=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // 0')

# Shorten home dir to ~
short_cwd="${cwd/#$HOME/\~}"

# Git branch
branch=""
if git -C "$cwd" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    branch=$(git -C "$cwd" -c gc.auto=0 symbolic-ref --short HEAD 2>/dev/null \
             || git -C "$cwd" -c gc.auto=0 rev-parse --short HEAD 2>/dev/null)
fi

# Filled bar: 8 chars wide, █ = filled, ░ = empty
make_bar() {
    local pct=${1:-0}
    local filled=$(awk "BEGIN{v=int($pct/100*8); print (v>8?8:(v<0?0:v))}")
    local empty=$(( 8 - filled ))
    local bar=""
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done
    printf '%s' "$bar"
}

# Color a bar green/yellow/red based on percentage
color_bar() {
    local pct=${1:-0}
    local bar=$(make_bar "$pct")
    local int_pct=$(awk "BEGIN{printf \"%d\", $pct}")
    if   [ "$int_pct" -ge 80 ]; then printf '\033[31m%s\033[0m' "$bar"   # red
    elif [ "$int_pct" -ge 50 ]; then printf '\033[33m%s\033[0m' "$bar"   # yellow
    else                              printf '\033[32m%s\033[0m' "$bar"   # green
    fi
}

# Time until a unix-timestamp reset, formatted as "2h13m" / "45m" / "—"
time_until() {
    local target=${1:-0}
    [ "$target" = "0" ] && { printf '—'; return; }
    local now=$(date +%s)
    local diff=$(( target - now ))
    [ "$diff" -le 0 ] && { printf '0m'; return; }
    local h=$(( diff / 3600 ))
    local m=$(( (diff % 3600) / 60 ))
    if [ "$h" -gt 0 ]; then printf '%dh%02dm' "$h" "$m"; else printf '%dm' "$m"; fi
}

# Format cost
cost_fmt=$(awk "BEGIN{printf \"\$%.2f\", $cost}")

# Assemble parts
user_host=$(printf '\033[32m%s@%s\033[0m' "$(whoami)" "$(hostname -s)")
dir_part=$(printf '\033[34m%s\033[0m' "$short_cwd")

parts="$user_host  $dir_part"

if [ -n "$branch" ]; then
    parts="$parts  $(printf '\033[33m(%s)\033[0m' "$branch")"
fi

[ -n "$model" ]    && parts="$parts  $model"
[ "$cost" != "0" ] && parts="$parts  $cost_fmt"

parts="$parts  Context-Window:$(color_bar "$ctx_pct")"
parts="$parts  5h:$(color_bar "$rl5") $(printf '\033[90m%s\033[0m' "$(time_until "$rl5_reset")")"
parts="$parts  7d:$(color_bar "$rl7") $(printf '\033[90m%s\033[0m' "$(time_until "$rl7_reset")")"

printf '%b\n' "$parts"
