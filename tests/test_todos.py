"""Tests for the async SQLite ToDo store + date parsing."""
from datetime import date, timedelta

import pytest

from app.services import todos as todos_svc


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")


# ── Date parsing ─────────────────────────────────────────────────────────────


def test_parse_date_heute():
    assert todos_svc.parse_date("heute") == date.today().isoformat()


def test_parse_date_morgen():
    assert todos_svc.parse_date("morgen") == (date.today() + timedelta(days=1)).isoformat()


def test_parse_date_uebermorgen():
    assert todos_svc.parse_date("übermorgen") == (date.today() + timedelta(days=2)).isoformat()


def test_parse_date_iso():
    assert todos_svc.parse_date("2026-12-31") == "2026-12-31"


def test_parse_date_german_short():
    today = date.today()
    iso = todos_svc.parse_date("30.06")
    # Should fall in current or next year, with month=6 day=30
    assert iso is not None
    parsed = date.fromisoformat(iso)
    assert parsed.month == 6 and parsed.day == 30
    assert parsed >= today  # never returns a past date


def test_parse_date_invalid_returns_none():
    assert todos_svc.parse_date("Brot kaufen") is None
    assert todos_svc.parse_date("99.99") is None
    assert todos_svc.parse_date("") is None


def test_extract_trailing_date_strips_hint():
    text, due = todos_svc._extract_trailing_date("Deck vorbereiten morgen")
    assert text == "Deck vorbereiten"
    assert due == (date.today() + timedelta(days=1)).isoformat()


def test_extract_trailing_date_no_hint_returns_input():
    text, due = todos_svc._extract_trailing_date("Brot kaufen")
    assert text == "Brot kaufen"
    assert due is None


# ── CRUD ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_add_and_list():
    tid = await todos_svc.add_todo(1, "Deck vorbereiten", due_date="2026-12-31")
    assert tid > 0
    rows = await todos_svc.list_todos(1, scope="all")
    assert len(rows) == 1
    assert rows[0]["text"] == "Deck vorbereiten"
    assert rows[0]["due_date"] == "2026-12-31"
    assert rows[0]["priority"] > 0


@pytest.mark.anyio
async def test_mention_existing_bumps_count():
    tid = await todos_svc.add_todo(1, "Brot kaufen")
    mid = await todos_svc.mention_existing(1, "BROT KAUFEN")
    assert mid == tid
    t = await todos_svc.get_todo(1, tid)
    assert t["mention_count"] == 2


@pytest.mark.anyio
async def test_mention_existing_returns_none_when_no_match():
    await todos_svc.add_todo(1, "Brot kaufen")
    mid = await todos_svc.mention_existing(1, "Milch kaufen")
    assert mid is None


@pytest.mark.anyio
async def test_mark_done():
    tid = await todos_svc.add_todo(1, "X")
    assert await todos_svc.mark_done(1, tid) is True
    rows = await todos_svc.list_todos(1, scope="all")
    assert len(rows) == 0  # done todos are not in the open list


@pytest.mark.anyio
async def test_mark_done_wrong_user_fails():
    tid = await todos_svc.add_todo(1, "X")
    assert await todos_svc.mark_done(99, tid) is False


@pytest.mark.anyio
async def test_drop_todo():
    tid = await todos_svc.add_todo(1, "X")
    assert await todos_svc.drop_todo(1, tid) is True
    rows = await todos_svc.list_todos(1, scope="all")
    assert len(rows) == 0


@pytest.mark.anyio
async def test_snooze_moves_out_of_today_view():
    tid = await todos_svc.add_todo(1, "later", due_date=date.today().isoformat())
    until = await todos_svc.snooze_todo(1, tid, days=3)
    assert until is not None
    today_rows = await todos_svc.list_todos(1, scope="today")
    assert len(today_rows) == 0


@pytest.mark.anyio
async def test_snoozed_wakes_up_in_list():
    """Snoozed todos with past snoozed_until should auto-wake to 'open'."""
    tid = await todos_svc.add_todo(1, "X")
    # Manually backdate snoozed_until
    import aiosqlite
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    async with aiosqlite.connect(todos_svc.TODOS_DB) as db:
        await db.execute(
            "UPDATE todos SET status = 'snoozed', snoozed_until = ? WHERE id = ?",
            (yesterday, tid),
        )
        await db.commit()
    rows = await todos_svc.list_todos(1, scope="all")
    assert len(rows) == 1  # should have been woken up


@pytest.mark.anyio
async def test_list_scopes():
    today = date.today()
    await todos_svc.add_todo(1, "today", due_date=today.isoformat())
    await todos_svc.add_todo(1, "next_week", due_date=(today + timedelta(days=5)).isoformat())
    await todos_svc.add_todo(1, "next_month", due_date=(today + timedelta(days=30)).isoformat())
    await todos_svc.add_todo(1, "no_date")

    today_rows = await todos_svc.list_todos(1, scope="today")
    week_rows = await todos_svc.list_todos(1, scope="week")
    all_rows = await todos_svc.list_todos(1, scope="all")

    today_texts = {r["text"] for r in today_rows}
    week_texts = {r["text"] for r in week_rows}
    all_texts = {r["text"] for r in all_rows}

    assert today_texts == {"today", "no_date"}
    assert week_texts == {"today", "next_week", "no_date"}
    assert all_texts == {"today", "next_week", "next_month", "no_date"}


@pytest.mark.anyio
async def test_stats():
    a = await todos_svc.add_todo(1, "open1")
    await todos_svc.add_todo(1, "open2")
    await todos_svc.mark_done(1, a)

    s = await todos_svc.stats(1)
    assert s["open"] == 1
    assert s["done"] == 1


# ── Priority ordering ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_priority_overdue_above_far_future():
    today = date.today()
    far = await todos_svc.add_todo(1, "far_future", due_date=(today + timedelta(days=30)).isoformat())
    overdue = await todos_svc.add_todo(1, "overdue", due_date=(today - timedelta(days=2)).isoformat())
    rows = await todos_svc.list_todos(1, scope="all")
    assert rows[0]["id"] == overdue
    assert rows[-1]["id"] == far


@pytest.mark.anyio
async def test_priority_more_mentions_higher_score():
    a = await todos_svc.add_todo(1, "A")
    b = await todos_svc.add_todo(1, "B")
    for _ in range(4):
        await todos_svc.mention_existing(1, "A")
    rows = await todos_svc.list_todos(1, scope="all")
    # A has more mentions → higher priority
    assert rows[0]["id"] == a
    assert rows[1]["id"] == b


# ── Isolation ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_users_isolated():
    await todos_svc.add_todo(1, "user1")
    await todos_svc.add_todo(2, "user2")
    r1 = await todos_svc.list_todos(1, scope="all")
    r2 = await todos_svc.list_todos(2, scope="all")
    assert len(r1) == 1 and r1[0]["text"] == "user1"
    assert len(r2) == 1 and r2[0]["text"] == "user2"
