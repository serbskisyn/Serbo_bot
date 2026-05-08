_active: dict[int, int] = {}  # user_id -> 1 or 2


def get_active_calendar(user_id: int) -> int:
    return _active.get(user_id, 1)


def set_active_calendar(user_id: int, num: int) -> None:
    _active[user_id] = num
