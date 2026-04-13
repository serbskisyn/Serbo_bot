from typing import TypedDict


class BotState(TypedDict, total=False):
    user_id:    int
    text:       str
    agent:      str
    response:   str
    messages:   list
    chart_bytes: bytes
    # Smart Routing
    topic:      str    # letzter stabiler Intent: football / web / general / chart
    confidence: float  # 0.0–1.0 Supervisor-Sicherheit
