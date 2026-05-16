import json
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chart_agent import _parse_spec, _render, generate_chart


def test_no_dangerous_calls_in_module():
    """Security: kein exec/eval/compile mehr in chart_agent.py."""
    src = pathlib.Path("app/agents/chart_agent.py").read_text()
    assert "exec(" not in src
    assert "eval(" not in src
    assert "compile(" not in src


def test_parse_spec_plain_json():
    spec = _parse_spec('{"type": "line", "series": []}')
    assert spec == {"type": "line", "series": []}


def test_parse_spec_in_markdown_fence():
    raw = '```json\n{"type": "bar", "title": "Test", "series": []}\n```'
    spec = _parse_spec(raw)
    assert spec == {"type": "bar", "title": "Test", "series": []}


def test_parse_spec_with_surrounding_text():
    raw = 'Hier ist das JSON:\n{"type": "scatter", "series": []}\nFertig.'
    spec = _parse_spec(raw)
    assert spec["type"] == "scatter"


def test_parse_spec_invalid_returns_none():
    assert _parse_spec("nicht parsebar") is None


def test_render_line_chart(tmp_path):
    spec = {
        "type": "line",
        "title": "SPY vs QQQ",
        "xlabel": "Tag",
        "ylabel": "Preis",
        "series": [
            {"label": "SPY", "x": [1, 2, 3], "y": [10, 12, 11]},
            {"label": "QQQ", "x": [1, 2, 3], "y": [9, 11, 13]},
        ],
    }
    out = tmp_path / "chart.png"
    assert _render(spec, str(out)) is True
    assert out.exists() and out.stat().st_size > 200
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_unknown_type_fails(tmp_path):
    spec = {"type": "pie", "series": [{"x": [1], "y": [1]}]}
    out = tmp_path / "chart.png"
    assert _render(spec, str(out)) is False


def test_render_empty_series_fails(tmp_path):
    spec = {"type": "line", "series": []}
    out = tmp_path / "chart.png"
    assert _render(spec, str(out)) is False


@pytest.mark.anyio
async def test_generate_chart_spy_vs_qqq():
    """Akzeptanztest: 'Zeig mir SPY vs QQQ' liefert ein PNG."""
    spec = {
        "type": "line",
        "title": "SPY vs QQQ",
        "series": [
            {"label": "SPY", "x": [1, 2, 3], "y": [450, 455, 460]},
            {"label": "QQQ", "x": [1, 2, 3], "y": [380, 385, 390]},
        ],
    }
    with patch("app.agents.chart_agent.ask_llm",
               AsyncMock(return_value=json.dumps(spec))):
        png = await generate_chart("Zeig mir SPY vs QQQ")
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.anyio
async def test_generate_chart_returns_none_on_parse_failure():
    with patch("app.agents.chart_agent.ask_llm",
               AsyncMock(return_value="Nope, kein JSON hier.")):
        png = await generate_chart("baue irgendwas")
    assert png is None
