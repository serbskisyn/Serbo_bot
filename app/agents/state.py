from typing import TypedDict


class BotState(TypedDict):
    user_id: int
    text: str
    agent: str
    response: str
    messages: list
