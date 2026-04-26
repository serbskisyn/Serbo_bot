"""
schedule_builder.py — Dienstplan-Generator für Babyschutzhaus
Regelwerk: siehe Projektdokumentation
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
    ma:                  Mitarbeiter
    ist_stunden:         float = 0.0
    arbeitstage:         int   = 0
    frueh_count:         int   = 0
    spaet_count:         int   = 0
    nacht_count:         int   = 0
    konsekutiv_arbeits:  int   = 0
    letzter_dienst:      Optional[Dienst] = None
    nacht_block_start:   Optional[date]   = None
    nacht_block_len:     int   = 0
    freies_wochenende:   bool  = False
    wunschfrei:          list[date] = field(default_factory=list)

    def add_dienst(self, d: Dienst, tag: date):
        if d in (Dienst.FRUEH, Dienst.SPAET, Dienst.NACHT):
            self.ist_stunden += SCHICHT_STUNDEN[d]
            self.arbeitstage += 1
            self.konsekutiv_arbeits += 1
            if d == Dienst.FRUEH:
                self.frueh_count += 1
            elif d == Dienst.SPAET:
                self.spaet_count += 1
            else:
                self.nacht_count += 1
        elif d == Dienst.FREI:
            self.konsekutiv_arbeits = 0
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
        # offen speichert pro Tag eine LISTE offener Dienste
        self.offen: dict[date, list[Dienst]] = {}
        self.states: dict[str, PlanungState] = {}
        self.violations: list[str] = []

    def generate(self) -> dict[str, dict[date, Dienst]]:
        self._init_states()
        self._set_abwesenheiten()
        self._init_aus_vormonat()
        self._plan_nachtdienste()
        self._plan_tagdienste()
        self._fill_frei()
        self._build_offen_plan()
        self._validate()
        return self.plan

    def _build_offen_plan(self):
        """Schreibt offene Dienste aus self.offen in self.plan['offen'].
        Pro Tag wird nur der schwerste offene Dienst angezeigt
        (OFFEN-ND > OFFEN-FD > OFFEN-SD), aber alle werden in violations erfasst.
        """
        if not self.offen:
            return
        self.plan["offen"] = {}
        prioritaet = [Dienst.OFFEN_ND, Dienst.OFFEN_FD, Dienst.OFFEN_SD]
        for tag, dienste in self.offen.items():
            # Alle offenen Dienste als Violation loggen
            for d in dienste:
                self.violations.append(
                    f"{tag.strftime('%d.%m')}: {d.value} nicht besetzt"
                )
            # Wichtigsten Dienst in die Zelle schreiben
            for p in prioritaet:
                if p in dienste:
                    self.plan["offen"][tag] = p
                    break
            else:
                self.plan["offen"][tag] = dienste[0]

    def get_report(self) -> str:
        lines = [f"=== Dienstplan {self.monat}/{self.jahr} ===\n"]
        alle_offen = []
        for tag, dienste in sorted(self.offen.items()):
            for d in dienste:
                alle_offen.append(f"  {tag.strftime('%d.%m')} → {d.value}")
        if alle_offen:
            lines.append(f"⚠️ Offene Dienste ({len(alle_offen)}):")
            lines.extend(alle_offen)
        else:
            lines.append("✅ Alle Dienste besetzt")
        lines.append("\n📊 Mitarbeiter-Übersicht:")
        for ma in self.ma_liste:
            s = self.states[ma.name]
            lines.append(
                f"  {ma.name:15s} | F:{s.frueh_count:2d} S:{s.spaet_count:2d} "
                f"N:{s.nacht_count:2d} | Ist:{s.ist_stunden:6.1f}h "
                f"Soll:{ma.soll_stunden:6.1f}h Δ:{s.stunden_delta:+.1f}h"
            )
        rule_violations = [v for v in self.violations if "nicht besetzt" not in v]
        if rule_violations:
            lines.append(f"\n❌ Regelverstöße ({len(rule_violations)}):")
            for v in rule_violations[:20]:
                lines.append(f"  • {v}")
        return "\n".join(lines)

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
        if not self.vormonat_plan:
            return
        erster = self.tage[0]
        for i in range(5, 0, -1):
            vortag = erster - timedelta(days=i)
            for ma_name, tage_plan in self.vormonat_plan.items():
                d = tage_plan.get(vortag)
                if d and ma_name in self.states:
                    s = self.states[ma_name]
                    if d in (Dienst.FRUEH, Dienst.SPAET, Dienst.NACHT):
                        s.konsekutiv_arbeits += 1
                    else:
                        s.konsekutiv_arbeits = 0
                    s.letzter_dienst = d

    def _ist_gesperrt(self, ma_name: str, tag: date) -> bool:
        d = self.plan.get(ma_name, {}).get(tag)
        return d in (Dienst.URLAUB, Dienst.KRANK, Dienst.FREI,
                     Dienst.BT, Dienst.TEAM, Dienst.SUPERVISION)

    def _kann_nacht(self, ma_name: str, tag: date) -> bool:
        if self._ist_gesperrt(ma_name, tag):
            return False
        if self.plan[ma_name].get(tag) is not None:
            return False
        if self.states[ma_name].konsekutiv_arbeits >= 5:
            return False
        return True

    def _plan_nachtdienste(self):
        tag = self.tage[0]
        while tag <= self.tage[-1]:
            bedarf = PFLICHT[Dienst.NACHT]
            bereits_belegt = sum(
                1 for ma in self.ma_liste
                if self.plan[ma.name].get(tag) == Dienst.NACHT
            )
            if bereits_belegt >= bedarf:
                tag += timedelta(days=1)
                continue

            kandidaten = [ma for ma in self.ma_liste if self._kann_nacht(ma.name, tag)]
            kandidaten.sort(key=lambda ma: (
                self.states[ma.name].nacht_count,
                -self.states[ma.name].stunden_delta,
            ))

            for ma in kandidaten:
                if bereits_belegt >= bedarf:
                    break
                block_tage = []
                block_ok = True
                for offset in range(3):
                    bt = tag + timedelta(days=offset)
                    if bt > self.tage[-1]:
                        break
                    if self._ist_gesperrt(ma.name, bt):
                        block_ok = False
                        break
                    block_tage.append(bt)

                if not block_ok or len(block_tage) < 1:
                    continue

                for bt in block_tage:
                    self.plan[ma.name][bt] = Dienst.NACHT
                    self.states[ma.name].add_dienst(Dienst.NACHT, bt)

                for offset in range(1, 3):
                    ft = block_tage[-1] + timedelta(days=offset)
                    if ft <= self.tage[-1] and not self._ist_gesperrt(ma.name, ft):
                        self.plan[ma.name][ft] = Dienst.FREI
                        self.states[ma.name].add_dienst(Dienst.FREI, ft)

                bereits_belegt += 1

            tag += timedelta(days=1)

    def _kann_tagdienst(self, ma_name: str, tag: date, dienst: Dienst) -> bool:
        if self._ist_gesperrt(ma_name, tag):
            return False
        if self.plan[ma_name].get(tag) is not None:
            return False
        s = self.states[ma_name]
        if s.konsekutiv_arbeits >= 5:
            return False
        if dienst == Dienst.FRUEH:
            vortag = tag - timedelta(days=1)
            if self.plan[ma_name].get(vortag) == Dienst.SPAET:
                return False
        if dienst in (Dienst.FRUEH, Dienst.SPAET):
            zaehler = 0
            check = tag - timedelta(days=1)
            while check >= self.tage[0]:
                if self.plan[ma_name].get(check) == dienst:
                    zaehler += 1
                    check -= timedelta(days=1)
                else:
                    break
            if zaehler >= 3:
                return False
        return True

    def _score_tagdienst(self, ma_name: str, dienst: Dienst) -> float:
        s = self.states[ma_name]
        score = 0.0
        score -= s.stunden_delta * 0.5
        if dienst == Dienst.FRUEH:
            score += s.frueh_count * 2
        elif dienst == Dienst.SPAET:
            score += s.spaet_count * 2
        score += s.konsekutiv_arbeits * 1.5
        return score

    def _plan_tagdienste(self):
        offen_map = {Dienst.FRUEH: Dienst.OFFEN_FD, Dienst.SPAET: Dienst.OFFEN_SD}
        for tag in self.tage:
            for dienst in (Dienst.FRUEH, Dienst.SPAET):
                bedarf = PFLICHT[dienst]
                bereits = sum(
                    1 for ma in self.ma_liste
                    if self.plan[ma.name].get(tag) == dienst
                )
                kandidaten = [
                    ma for ma in self.ma_liste
                    if self._kann_tagdienst(ma.name, tag, dienst)
                ]
                kandidaten.sort(key=lambda ma: self._score_tagdienst(ma.name, dienst))

                for ma in kandidaten:
                    if bereits >= bedarf:
                        break
                    self.plan[ma.name][tag] = dienst
                    self.states[ma.name].add_dienst(dienst, tag)
                    bereits += 1

                # Fehlende Dienste korrekt in self.offen sammeln
                fehlend = bedarf - bereits
                if fehlend > 0:
                    if tag not in self.offen:
                        self.offen[tag] = []
                    for _ in range(fehlend):
                        self.offen[tag].append(offen_map[dienst])

    def _fill_frei(self):
        for ma in self.ma_liste:
            s = self.states[ma.name]
            for tag in self.tage:
                if self.plan[ma.name].get(tag) is None:
                    self.plan[ma.name][tag] = Dienst.FREI
                    s.konsekutiv_arbeits = 0

            hat_frei_we = False
            for tag in self.tage:
                if tag.weekday() == 5:
                    so = tag + timedelta(days=1)
                    if (self.plan[ma.name].get(tag) == Dienst.FREI and
                            so <= self.tage[-1] and
                            self.plan[ma.name].get(so) == Dienst.FREI):
                        hat_frei_we = True
                        break
            if not hat_frei_we:
                self.violations.append(
                    f"{ma.name}: kein vollständiges freies Wochenende im Monat"
                )

    def _validate(self):
        for tag in self.tage:
            for dienst, anzahl in PFLICHT.items():
                ist = sum(
                    1 for ma in self.ma_liste
                    if self.plan[ma.name].get(tag) == dienst
                )
                offen_count = sum(
                    1 for d in self.offen.get(tag, [])
                    if d == {Dienst.FRUEH: Dienst.OFFEN_FD,
                             Dienst.SPAET: Dienst.OFFEN_SD,
                             Dienst.NACHT: Dienst.OFFEN_ND}.get(dienst)
                )
                gesamt = ist + offen_count
                if gesamt < anzahl:
                    self.violations.append(
                        f"{tag.strftime('%d.%m')}: {dienst.value} nur {gesamt}/{anzahl} besetzt"
                    )
        for ma in self.ma_liste:
            for tag in self.tage:
                vortag = tag - timedelta(days=1)
                if (self.plan[ma.name].get(vortag) == Dienst.SPAET and
                        self.plan[ma.name].get(tag) == Dienst.FRUEH):
                    self.violations.append(
                        f"{ma.name} {tag.strftime('%d.%m')}: Spät→Früh verboten"
                    )
