from collections import defaultdict, deque

# Max. Nachrichten pro User im Verlauf (User + Assistant zusammen)
MAX_HISTORY = 20

# { user_id: deque([{"role": ..., "content": ...}, ...]) }
_histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))


def get_history(user_id: int) -> list[dict]:
    return list(_histories[user_id])


def add_message(user_id: int, role: str, content: str) -> None:
    _histories[user_id].append({"role": role, "content": content})


def clear_history(user_id: int) -> None:
    _histories[user_id].clear()
