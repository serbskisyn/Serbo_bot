from typing import TypedDict


class BotState(TypedDict, total=False):
    user_id: int
    text: str
    agent: str
    response: str
    messages: list
    chart_bytes: bytes