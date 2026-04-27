"""
schedule_builder.py — Dienstplan-Generator für Babyschutzhaus

Ziel: Jeder Tag wird mit je 2x Früh (FD), 2x Spät (SD), 2x Nacht (ND) besetzt.
Frei/Urlaub/Krank werden berücksichtigt. Unbesetzte Dienste werden als
OFFEN-FD / OFFEN-SD / OFFEN-ND ausgewiesen.

Regeln:
- Max. 5 aufeinanderfolgende Arbeitstage
- Kein Früh direkt nach Spät (Vortag)
- Max. 3 gleiche Schichten in Folge (Früh oder Spät)
- Nach Nachtblock: mind. 2 Freitag-Puffer
- Mindestens ein freies Wochenende pro Monat
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

class Dienst(str, Enum):
    FRUEH       = "Früh"
    SPAET       = "Spät"
    NACHT       = "Nacht"
    FREI        = "Frei"
    URLAUB      = "Urlaub"
    KRANK       = "krank"
    BT          = "BT"
    TEAM        = "Team"
    SUPERVISION = "Supervision"
    OFFEN_FD    = "OFFEN-FD"
    OFFEN_SD    = "OFFEN-SD"
    OFFEN_ND    = "OFFEN-ND"

SCHICHT_STUNDEN: dict[Dienst, float] = {
    Dienst.FRUEH: 7.5,
    Dienst.SPAET: 7.0,
    Dienst.NACHT: 9.0,
}

FARBEN: dict[str, str] = {
    Dienst.FRUEH:       "FFFFFF",
    Dienst.SPAET:       "E2EFD9",
    Dienst.NACHT:       "C55A11",
    Dienst.FREI:        "D8D8D8",
    Dienst.URLAUB:      "92D050",
    Dienst.KRANK:       "FF0000",
    Dienst.BT:          "EAD1DC",
    Dienst.TEAM:        "FFD965",
    Dienst.SUPERVISION: "FFD965",
    Dienst.OFFEN_FD:    "FFC000",
    Dienst.OFFEN_SD:    "FFC000",
    Dienst.OFFEN_ND:    "FFC000",
}

PFLICHT = {Dienst.FRUEH: 2, Dienst.SPAET: 2, Dienst.NACHT: 2}

FEIERTAGE_BERLIN: dict[tuple[int, int], str] = {
    (1,  1):  "Neujahr",
    (5,  1):  "Tag der Arbeit",
    (10, 3):  "Tag der Deutschen Einheit",
    (12, 25): "1. Weihnachtstag",
    (12, 26): "2. Weihnachtstag",
}

def get_feiertage(jahr: int, monat: int) -> list[date]:
    result = []
    for (m, d), _ in FEIERTAGE_BERLIN.items():
        if m == monat:
            try:
                result.append(date(jahr, m, d))
            except ValueError:
                pass
    return result


# ---------------------------------------------------------------------------
# Datenstrukturen
# ---------------------------------------------------------------------------

@dataclass
class Mitarbeiter:
    name:          str
    tagesstunden:  float
    wochenstunden: float = 0.0
    soll_stunden:  float = 0.0

    def __post_init__(self):
        self.wochenstunden = round(self.tagesstunden * 5, 1)

    def berechne_soll(self, arbeitstage_monat: int):
        self.soll_stunden = round((self.wochenstunden / 5) * arbeitstage_monat, 1)


@dataclass
class Abwesenheit:
    name:  str
    art:   str   # U=Urlaub, F=Frei/FA, K=Krank
    datum: date


@dataclass
class PlanungState:
    ma:                 Mitarbeiter
    ist_stunden:        float = 0.0
    arbeitstage:        int   = 0
    frueh_count:        int   = 0
    spaet_count:        int   = 0
    nacht_count:        int   = 0
    # BUG-FIX: konsekutiv_arbeits wird jetzt bei jedem Dienst-Lookup
    # aus dem tatsächlichen Plan neu berechnet (siehe _konsekutiv)
    # Der Counter hier ist nur noch für Vormonat-Initialisierung.
    konsekutiv_arbeits: int   = 0
    letzter_dienst:     Optional[Dienst] = None
    wunschfrei:         list[date] = field(default_factory=list)

    def add_schicht(self, d: Dienst):
        """Zählt nur echte Arbeitsdienste."""
        if d == Dienst.FRUEH:
            self.frueh_count += 1
            self.ist_stunden += SCHICHT_STUNDEN[Dienst.FRUEH]
            self.arbeitstage += 1
        elif d == Dienst.SPAET:
            self.spaet_count += 1
            self.ist_stunden += SCHICHT_STUNDEN[Dienst.SPAET]
            self.arbeitstage += 1
        elif d == Dienst.NACHT:
            self.nacht_count += 1
            self.ist_stunden += SCHICHT_STUNDEN[Dienst.NACHT]
            self.arbeitstage += 1
        self.letzter_dienst = d

    @property
    def stunden_delta(self) -> float:
        return self.ma.soll_stunden - self.ist_stunden

    @property
    def gesamt_schichten(self) -> int:
        return self.frueh_count + self.spaet_count + self.nacht_count


# ---------------------------------------------------------------------------
# Haupt-Generator
# ---------------------------------------------------------------------------

class DienstplanGenerator:

    ARBEITSDIENSTE = {Dienst.FRUEH, Dienst.SPAET, Dienst.NACHT}
    GESPERRT_DIENSTE = {
        Dienst.URLAUB, Dienst.KRANK, Dienst.FREI,
        Dienst.BT, Dienst.TEAM, Dienst.SUPERVISION,
    }

    def __init__(
        self,
        mitarbeiter_liste: list[Mitarbeiter],
        abwesenheiten:     list[Abwesenheit],
        jahr:              int,
        monat:             int,
        vormonat_plan:     dict[str, dict[date, Dienst]] | None = None,
    ):
        self.ma_liste      = mitarbeiter_liste
        self.abwesenheiten = abwesenheiten
        self.jahr          = jahr
        self.monat         = monat
        self.vormonat_plan = vormonat_plan or {}

        _, letzter_tag = calendar.monthrange(jahr, monat)
        self.tage = [date(jahr, monat, d) for d in range(1, letzter_tag + 1)]
        self.feiertage = get_feiertage(jahr, monat)

        self.arbeitstage_monat = sum(
            1 for t in self.tage
            if t.weekday() < 5 and t not in self.feiertage
        )

        self.plan: dict[str, dict[date, Dienst]] = {
            ma.name: {} for ma in self.ma_liste
        }
        self.offen: dict[date, list[Dienst]] = {}
        self.states: dict[str, PlanungState] = {}
        self.violations: list[str] = []

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------

    def _konsekutiv(self, ma_name: str, bis_exkl: date) -> int:
        """
        Berechnet aufeinanderfolgende Arbeitstage rückwärts aus dem Plan.
        BUG-FIX: statt einen fehleranfälligen Counter mitzuführen,
        lesen wir direkt aus self.plan – damit ist der Wert immer korrekt.
        """
        count = 0
        # Vormonat-Initialisierung
        vormonat_konsekutiv = self.states[ma_name].konsekutiv_arbeits
        check = bis_exkl - timedelta(days=1)
        while True:
            if check < self.tage[0]:
                # Im Vormonat weiterlesen (vereinfacht: nutze vormonat_konsekutiv)
                count += vormonat_konsekutiv
                break
            d = self.plan[ma_name].get(check)
            if d in self.ARBEITSDIENSTE:
                count += 1
                check -= timedelta(days=1)
            else:
                break
        return count

    def _ist_gesperrt(self, ma_name: str, tag: date) -> bool:
        return self.plan[ma_name].get(tag) in self.GESPERRT_DIENSTE

    def _slot_frei(self, ma_name: str, tag: date) -> bool:
        """True wenn der Tag weder gesperrt noch bereits vergeben ist."""
        return self.plan[ma_name].get(tag) is None

    def _kann_arbeiten(self, ma_name: str, tag: date) -> bool:
        """Basischeck: nicht gesperrt, kein Eintrag, max 5 konsekutiv."""
        if not self._slot_frei(ma_name, tag):
            return False
        if self._ist_gesperrt(ma_name, tag):
            return False
        if self._konsekutiv(ma_name, tag) >= 5:
            return False
        return True

    def _kann_frueh(self, ma_name: str, tag: date) -> bool:
        if not self._kann_arbeiten(ma_name, tag):
            return False
        # Kein Früh direkt nach Spät
        vortag = tag - timedelta(days=1)
        if self.plan[ma_name].get(vortag) == Dienst.SPAET:
            return False
        # Max 3 gleiche in Folge
        if self._in_folge(ma_name, tag, Dienst.FRUEH) >= 3:
            return False
        return True

    def _kann_spaet(self, ma_name: str, tag: date) -> bool:
        if not self._kann_arbeiten(ma_name, tag):
            return False
        if self._in_folge(ma_name, tag, Dienst.SPAET) >= 3:
            return False
        return True

    def _kann_nacht(self, ma_name: str, tag: date) -> bool:
        if not self._kann_arbeiten(ma_name, tag):
            return False
        return True

    def _in_folge(self, ma_name: str, tag: date, dienst: Dienst) -> int:
        """Wie viele aufeinanderfolgende *gleiche* Dienste liegen direkt vor tag."""
        count = 0
        check = tag - timedelta(days=1)
        while check >= self.tage[0]:
            if self.plan[ma_name].get(check) == dienst:
                count += 1
                check -= timedelta(days=1)
            else:
                break
        return count

    def _setze_dienst(self, ma_name: str, tag: date, dienst: Dienst):
        """Trägt einen Dienst ein und aktualisiert den State."""
        self.plan[ma_name][tag] = dienst
        if dienst in self.ARBEITSDIENSTE:
            self.states[ma_name].add_schicht(dienst)

    # ------------------------------------------------------------------
    # Score-Funktionen (niedriger Score = bevorzugt)
    # ------------------------------------------------------------------

    def _score(self, ma_name: str, dienst: Dienst) -> float:
        s = self.states[ma_name]
        score = 0.0
        # Wer weniger Stunden hat, soll bevorzugt werden
        score -= s.stunden_delta * 0.5          # hohes Delta (Unterst.) → niedrigerer Score
        # Wer diesen Diensttyp schon oft hat, wird deprioritiert
        if dienst == Dienst.FRUEH:
            score += s.frueh_count * 2.0
        elif dienst == Dienst.SPAET:
            score += s.spaet_count * 2.0
        elif dienst == Dienst.NACHT:
            score += s.nacht_count * 3.0
        # Viele konsekutive Tage → lieber jemand anderen
        score += self._konsekutiv(ma_name, date.today()) * 1.0
        return score

    # ------------------------------------------------------------------
    # Planungsschritte
    # ------------------------------------------------------------------

    def generate(self) -> dict[str, dict[date, Dienst]]:
        self._init_states()
        self._set_abwesenheiten()
        self._init_aus_vormonat()
        self._plan_alle_dienste()      # FD + SD + ND in einem Pass
        self._fill_frei()
        self._build_offen_plan()
        self._validate()
        return self.plan

    def _init_states(self):
        for ma in self.ma_liste:
            ma.berechne_soll(self.arbeitstage_monat)
            self.states[ma.name] = PlanungState(ma=ma)

    def _set_abwesenheiten(self):
        art_map = {"U": Dienst.URLAUB, "F": Dienst.FREI, "K": Dienst.KRANK}
        for ab in self.abwesenheiten:
            if ab.datum.year == self.jahr and ab.datum.month == self.monat:
                dienst = art_map.get(ab.art.upper(), Dienst.URLAUB)
                if ab.name in self.plan:
                    self.plan[ab.name][ab.datum] = dienst

    def _init_aus_vormonat(self):
        """Liest die letzten Tage des Vormonats um konsekutive Zählung korrekt zu starten."""
        if not self.vormonat_plan:
            return
        erster = self.tage[0]
        for ma_name, tage_plan in self.vormonat_plan.items():
            if ma_name not in self.states:
                continue
            s = self.states[ma_name]
            konsekutiv = 0
            for i in range(1, 8):  # max 7 Tage zurückschauen
                vortag = erster - timedelta(days=i)
                d = tage_plan.get(vortag)
                if d in self.ARBEITSDIENSTE:
                    konsekutiv += 1
                else:
                    break
            s.konsekutiv_arbeits = konsekutiv
            # letzter Dienst für Spät→Früh-Check
            vortag = erster - timedelta(days=1)
            s.letzter_dienst = tage_plan.get(vortag)

    def _plan_alle_dienste(self):
        """
        Kernlogik: iteriert über jeden Tag und belegt FD, SD, ND.

        BUG-FIX 1: Nacht-Frei-Puffer darf den konsekutiv-Counter nicht
        erhöhen (Frei = kein Arbeitstag) und muss überschreibbar bleiben
        wenn später ein anderer Dienst für denselben Tag geplant wird.

        BUG-FIX 2: Score-Funktion deprioritiert korrekt: wer weniger
        Stunden hat (stunden_delta hoch) soll mehr Dienste bekommen →
        niedrigerer Score.

        BUG-FIX 3: Nacht-Frei wird als SOFT-Puffer eingetragen – kann
        von FD/SD überschrieben werden wenn kein anderer Kandidat verfügbar.
        """
        offen_map = {
            Dienst.FRUEH:  Dienst.OFFEN_FD,
            Dienst.SPAET:  Dienst.OFFEN_SD,
            Dienst.NACHT:  Dienst.OFFEN_ND,
        }

        # Nacht-Puffer: ma_name → set of dates die als weicher Puffer markiert sind
        nacht_puffer: dict[str, set[date]] = {ma.name: set() for ma in self.ma_liste}

        for tag in self.tage:
            # ---- Nachtdienste zuerst (Blöcke zunächst ignorieren, tageweise belegen) ----
            for dienst, kann_fn, bedarf in [
                (Dienst.NACHT, self._kann_nacht, PFLICHT[Dienst.NACHT]),
                (Dienst.FRUEH, self._kann_frueh, PFLICHT[Dienst.FRUEH]),
                (Dienst.SPAET, self._kann_spaet, PFLICHT[Dienst.SPAET]),
            ]:
                bereits = sum(
                    1 for ma in self.ma_liste
                    if self.plan[ma.name].get(tag) == dienst
                )

                if bereits >= bedarf:
                    continue

                # Kandidaten ermitteln
                kandidaten = []
                for ma in self.ma_liste:
                    # Weicher Puffer darf für FD/SD überschrieben werden
                    if (dienst in (Dienst.FRUEH, Dienst.SPAET)
                            and tag in nacht_puffer[ma.name]
                            and self.plan[ma.name].get(tag) == Dienst.FREI):
                        # Slot temporär freigeben für Kandidaten-Prüfung
                        del self.plan[ma.name][tag]
                        if kann_fn(ma.name, tag):
                            kandidaten.append(ma)
                        else:
                            # Puffer wiederherstellen
                            self.plan[ma.name][tag] = Dienst.FREI
                    elif kann_fn(ma.name, tag):
                        kandidaten.append(ma)

                kandidaten.sort(key=lambda ma: self._score(ma.name, dienst))

                for ma in kandidaten:
                    if bereits >= bedarf:
                        break
                    # Wenn Puffer-Frei überschrieben wird, Stunden-Counter nicht doppeln
                    war_puffer = tag in nacht_puffer[ma.name]
                    if war_puffer and self.plan[ma.name].get(tag) == Dienst.FREI:
                        del self.plan[ma.name][tag]  # Puffer wegräumen
                        nacht_puffer[ma.name].discard(tag)

                    self._setze_dienst(ma.name, tag, dienst)
                    bereits += 1

                # Nach Nacht: 2-Tage Soft-Puffer eintragen
                if dienst == Dienst.NACHT:
                    for ma in self.ma_liste:
                        if self.plan[ma.name].get(tag) == Dienst.NACHT:
                            for offset in (1, 2):
                                puff_tag = tag + timedelta(days=offset)
                                if puff_tag > self.tage[-1]:
                                    break
                                if self._slot_frei(ma.name, puff_tag):
                                    self.plan[ma.name][puff_tag] = Dienst.FREI
                                    nacht_puffer[ma.name].add(puff_tag)

                # Fehlende Dienste als OFFEN markieren
                fehlend = bedarf - bereits
                if fehlend > 0:
                    if tag not in self.offen:
                        self.offen[tag] = []
                    for _ in range(fehlend):
                        self.offen[tag].append(offen_map[dienst])

    def _fill_frei(self):
        """Füllt alle noch leeren Slots mit Frei."""
        for ma in self.ma_liste:
            for tag in self.tage:
                if self.plan[ma.name].get(tag) is None:
                    self.plan[ma.name][tag] = Dienst.FREI

        # Prüfe freies Wochenende
        for ma in self.ma_liste:
            hat_frei_we = False
            for tag in self.tage:
                if tag.weekday() == 5:
                    so = tag + timedelta(days=1)
                    if (self.plan[ma.name].get(tag) == Dienst.FREI
                            and so <= self.tage[-1]
                            and self.plan[ma.name].get(so) == Dienst.FREI):
                        hat_frei_we = True
                        break
            if not hat_frei_we:
                self.violations.append(
                    f"{ma.name}: kein vollständiges freies Wochenende im Monat"
                )

    def _build_offen_plan(self):
        """Schreibt offene Dienste in self.plan['offen'] (ein Eintrag pro Tag)."""
        if not self.offen:
            return
        self.plan["offen"] = {}
        prioritaet = [Dienst.OFFEN_ND, Dienst.OFFEN_FD, Dienst.OFFEN_SD]
        for tag, dienste in self.offen.items():
            for p in prioritaet:
                if p in dienste:
                    self.plan["offen"][tag] = p
                    break
            else:
                self.plan["offen"][tag] = dienste[0]

    def _validate(self):
        """Prüft alle Regeln und schreibt Violations."""
        offen_map_rev = {
            Dienst.FRUEH: Dienst.OFFEN_FD,
            Dienst.SPAET: Dienst.OFFEN_SD,
            Dienst.NACHT: Dienst.OFFEN_ND,
        }
        for tag in self.tage:
            for dienst, anzahl in PFLICHT.items():
                ist = sum(
                    1 for ma in self.ma_liste
                    if self.plan[ma.name].get(tag) == dienst
                )
                offen_count = sum(
                    1 for d in self.offen.get(tag, [])
                    if d == offen_map_rev[dienst]
                )
                gesamt = ist + offen_count
                if gesamt < anzahl:
                    self.violations.append(
                        f"{tag.strftime('%d.%m')}: {dienst.value} "
                        f"nur {gesamt}/{anzahl} besetzt"
                    )

        for ma in self.ma_liste:
            for tag in self.tage:
                vortag = tag - timedelta(days=1)
                if (self.plan[ma.name].get(vortag) == Dienst.SPAET
                        and self.plan[ma.name].get(tag) == Dienst.FRUEH):
                    self.violations.append(
                        f"{ma.name} {tag.strftime('%d.%m')}: Spät→Früh verboten"
                    )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def get_report(self) -> str:
        lines = [f"=== Dienstplan {self.monat}/{self.jahr} ==="]

        # Offene Dienste
        alle_offen = []
        for tag, dienste in sorted(self.offen.items()):
            for d in dienste:
                alle_offen.append(f"  {tag.strftime('%d.%m')} → {d.value}")
        if alle_offen:
            lines.append(f"\n⚠️  Offene Dienste ({len(alle_offen)}):")
            lines.extend(alle_offen)
        else:
            lines.append("\n✅ Alle Dienste vollständig besetzt")

        # MA-Übersicht
        lines.append("\n📊 Mitarbeiter-Übersicht:")
        for ma in self.ma_liste:
            s = self.states[ma.name]
            delta_sign = "+" if s.stunden_delta >= 0 else ""
            lines.append(
                f"  {ma.name:15s} | "
                f"FD:{s.frueh_count:2d}  SD:{s.spaet_count:2d}  ND:{s.nacht_count:2d} | "
                f"Ist:{s.ist_stunden:6.1f}h  "
                f"Soll:{ma.soll_stunden:6.1f}h  "
                f"Δ:{delta_sign}{s.stunden_delta:.1f}h"
            )

        # Regelverletzungen (ohne offene Dienste)
        rule_violations = [v for v in self.violations
                           if "nicht besetzt" not in v and "nur " not in v]
        if rule_violations:
            lines.append(f"\n❌ Regelverstöße ({len(rule_violations)}):")
            for v in rule_violations[:30]:
                lines.append(f"  • {v}")

        return "\n".join(lines)
