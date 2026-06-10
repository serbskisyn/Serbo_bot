"""
Tests for the Kicktipp AI player — HTML parsing, predictor prompt/parse,
and eligibility/deadline filtering. No live network or LLM calls.
"""
from datetime import datetime, timedelta

from app.services.kicktipp_client import (
    parse_matches, _hidden_form_fields, Match, parse_bonus_questions,
)
from app.services.kicktipp_predictor import (
    build_prompt, parse_predictions, parse_bonus_answers,
)
from app.bot import kicktipp_job


_HTML = """
<html><body><div id="kicktipp-content">
<form action="/runde/tippabgabe" method="post">
<input type="hidden" name="_charset_" value="UTF-8"/>
<input type="hidden" name="csrftoken" value="tok42"/>
<table><tbody>
<tr>
  <td>05.06.26 20:30</td><td>Bayern</td><td>Dortmund</td>
  <td><input id="s1_heimTipp" name="s1_heimTipp" value=""/>
      <input id="s1_gastTipp" name="s1_gastTipp" value=""/></td>
  <td>1.5 / 4.0 / 6.0</td>
</tr>
<tr>
  <td></td><td>Leipzig</td><td>Freiburg</td>
  <td><input id="s2_heimTipp" name="s2_heimTipp" value="2"/>
      <input id="s2_gastTipp" name="s2_gastTipp" value="1"/></td>
  <td>1,8 / 3,5 / 4,2</td>
</tr>
</tbody></table>
<input type="submit" name="submitbutton" value="submitbutton"/>
</form></div></body></html>
"""


def test_parse_matches_basic():
    ms = parse_matches(_HTML)
    assert len(ms) == 2
    assert (ms[0].home, ms[0].away) == ("Bayern", "Dortmund")
    assert ms[0].odds == (1.5, 4.0, 6.0)
    assert ms[0].field_home == "s1_heimTipp" and ms[0].field_away == "s1_gastTipp"
    assert not ms[0].has_bet


def test_parse_matches_date_carry_and_comma_odds():
    ms = parse_matches(_HTML)
    assert ms[1].kickoff == ms[0].kickoff          # blank date carried forward
    assert ms[1].odds == (1.8, 3.5, 4.2)           # comma decimals
    assert ms[1].has_bet and ms[1].existing_home == "2"


def test_hidden_form_fields_roundtrip():
    ff = _hidden_form_fields(_HTML)
    assert ff["csrftoken"] == "tok42"
    assert "submitbutton" in ff
    assert "s1_heimTipp" in ff


def test_parse_matches_empty_html():
    assert parse_matches("<html><body>nada</body></html>") == []


def test_predictor_build_prompt_includes_odds_and_news():
    ms = [Match("A", "B", None, (1.5, 4.0, 6.0), "fa", "fb")]
    p = build_prompt(ms, {"A": ["A gewinnt alles"]})
    assert "A vs B" in p and "1.50/4.00/6.00" in p and "A gewinnt alles" in p


def test_predictor_parse_clamps_and_filters():
    raw = '[{"i":0,"heim":3,"gast":1},{"i":9,"heim":1,"gast":0},{"i":0,"heim":"x"}]'
    assert parse_predictions(raw, 2) == {0: (3, 1)}        # i=9 out of range, bad dropped
    assert parse_predictions('[{"i":0,"heim":99,"gast":-3}]', 1) == {0: (9, 0)}
    assert parse_predictions("kein json", 3) == {}


_BONUS_HTML = """
<table id="tippabgabeFragen"><tbody>
<tr><td class="nw kicktipp-time">11.06.26 21:00</td>
    <td class="nw">Wer wird Weltmeister?</td>
    <td class="nw kicktipp-tippabgabe">
      <select name="fragetippForms[100].antwortIds[1]">
        <option value="">-- Nicht getippt --</option>
        <option value="10">Brasilien</option><option value="11">Argentinien</option>
      </select></td></tr>
<tr><td class="nw kicktipp-time">11.06.26 21:00</td>
    <td class="nw">Wer erreicht das Halbfinale?</td>
    <td class="nw kicktipp-tippabgabe">
      <select name="fragetippForms[200].antwortIds[1]"><option value="">-- Nicht getippt --</option><option value="10">Brasilien</option><option value="11">Argentinien</option><option value="12">Frankreich</option></select>
      <select name="fragetippForms[200].antwortIds[2]"><option value="">-- Nicht getippt --</option><option value="10">Brasilien</option><option value="11">Argentinien</option><option value="12">Frankreich</option></select>
    </td></tr>
</tbody></table>
"""


def test_parse_bonus_questions():
    qs = parse_bonus_questions(_BONUS_HTML)
    assert len(qs) == 2
    champ = qs[0]
    assert champ.text == "Wer wird Weltmeister?" and not champ.multi
    assert ("Brasilien", "10") in champ.options
    semi = qs[1]
    assert semi.multi and len(semi.fields) == 2


def test_parse_bonus_answers_maps_labels_to_values():
    qs = parse_bonus_questions(_BONUS_HTML)
    raw = '[{"qid":"100","antworten":["Argentinien"]},{"qid":"200","antworten":["Brasilien","Frankreich"]}]'
    answers = parse_bonus_answers(raw, qs)
    assert answers["fragetippForms[100].antwortIds[1]"] == "11"     # Argentinien
    assert answers["fragetippForms[200].antwortIds[1]"] == "10"     # Brasilien
    assert answers["fragetippForms[200].antwortIds[2]"] == "12"     # Frankreich


def test_parse_bonus_answers_dedupes_within_multi():
    qs = parse_bonus_questions(_BONUS_HTML)
    raw = '[{"qid":"200","antworten":["Brasilien","Brasilien","Frankreich"]}]'
    answers = parse_bonus_answers(raw, qs)
    # duplicate Brasilien must not fill both slots — second slot takes Frankreich
    assert answers["fragetippForms[200].antwortIds[1]"] == "10"
    assert answers["fragetippForms[200].antwortIds[2]"] == "12"


def test_eligibility_lookahead_and_override(monkeypatch):
    monkeypatch.setattr(kicktipp_job, "KICKTIPP_LOOKAHEAD_HOURS", 48)
    soon = datetime.now() + timedelta(hours=10)
    far = datetime.now() + timedelta(hours=200)
    m_soon = Match("A", "B", soon, None, "fa", "fb")
    m_far = Match("C", "D", far, None, "fc", "fd")
    m_bet = Match("E", "F", soon, None, "fe", "ff", existing_home="1", existing_away="0")

    elig = kicktipp_job._eligible([m_soon, m_far, m_bet], override=False)
    assert [m.home for m in elig] == ["A"]              # far excluded, already-bet excluded
    elig_ov = kicktipp_job._eligible([m_soon, m_far, m_bet], override=True)
    assert {m.home for m in elig_ov} == {"A", "E"}      # override re-includes the bet one
