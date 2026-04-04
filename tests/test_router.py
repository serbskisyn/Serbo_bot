import pytest
from app.bot.router import route, AgentType


# ── Football ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Wer hat die letzte WM gewonnen?",
    "Was ist die aktuelle Bundesliga Tabelle?",
    "Wie viele Tore hat Lewandowski diese Saison?",
    "Champions League Ergebnisse von gestern",
    "Wer spielt heute in der Premier League?",
    "Transfer News Bayern München",
    "Ist Messi bei Barcelona oder PSG?",
])
def test_routes_to_football(text):
    assert route(text) == AgentType.FOOTBALL


# ── Chart ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Erstelle ein Balkendiagramm meiner Ausgaben",
    "Zeig mir einen Chart über die Umsatzentwicklung",
    "Kannst du eine Grafik erstellen?",
    "Ich brauche einen bar chart",
    "Visualisiere die Daten bitte",
    "Zeichne einen Graphen",
])
def test_routes_to_chart(text):
    assert route(text) == AgentType.CHART


# ── General ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Was ist die Hauptstadt von Frankreich?",
    "Erkläre mir Quantencomputing",
    "Wie wird das Wetter morgen?",
    "Schreib mir eine E-Mail",
    "Was bedeutet Machine Learning?",
])
def test_routes_to_general(text):
    assert route(text) == AgentType.GENERAL


# ── Chart hat Priorität vor Football ─────────────────────────────────────────
def test_chart_priority_over_football():
    assert route("Erstelle einen Chart der Bundesliga Tabelle") == AgentType.CHART


# ── Leerstring und Sonderzeichen ─────────────────────────────────────────────
def test_empty_string_routes_to_general():
    assert route("") == AgentType.GENERAL

def test_special_chars_routes_to_general():
    assert route("!!! ???") == AgentType.GENERAL
