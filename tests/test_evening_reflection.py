"""Tests for evening_reflection assembler."""
from datetime import date

import pytest

from app.bot import profile
from app.services import evening_reflection, todos as todos_svc


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.yaml")
    monkeypatch.setattr(evening_reflection, "_SUMMARY_DIR", tmp_path / "summaries")
    profile._store.clear()
    yield
    profile._store.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────


def test_today_de_returns_german_weekday():
    s = evening_reflection._today_de(date(2026, 5, 27))  # Wednesday
    assert "Mittwoch" in s
    assert "27.05.2026" in s


# ── End-to-end ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_empty_user_renders_safely():
    text = await evening_reflection.assemble_evening_reflection(99)
    assert "Tagesabschluss" in text
    assert "Heute noch nichts erledigt" in text


@pytest.mark.anyio
async def test_includes_done_todos_from_today():
    tid = await todos_svc.add_todo(1, "Server reboot")
    await todos_svc.mark_done(1, tid)
    text = await evening_reflection.assemble_evening_reflection(1)
    assert "Heute geschafft" in text
    assert "Server reboot" in text


@pytest.mark.anyio
async def test_includes_open_todos():
    await todos_svc.add_todo(1, "Brot kaufen")
    text = await evening_reflection.assemble_evening_reflection(1)
    assert "Noch offen" in text
    assert "Brot kaufen" in text


@pytest.mark.anyio
async def test_includes_today_decisions():
    await todos_svc.add_todo(1, "Entscheidung: Migration auf Q3", source="granola")
    text = await evening_reflection.assemble_evening_reflection(1)
    assert "Entscheidungen geloggt" in text
    assert "Migration auf Q3" in text


@pytest.mark.anyio
async def test_greeting_includes_profile_name():
    await profile.set_scalar(1, "identity", "name", "Benno")
    text = await evening_reflection.assemble_evening_reflection(1)
    assert "Benno" in text


@pytest.mark.anyio
async def test_write_day_summary_creates_file(tmp_path):
    path = await evening_reflection.write_day_summary(42, "Some body text")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Tagesabschluss" in content
    assert "Some body text" in content
