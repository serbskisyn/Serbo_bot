"""
schedule_builder.py — Dienstplan-Generator für Babyschutzhaus

Ziel: Jeder Tag wird mit je 2x Früh (FD), 2x Spät (SD), 2x Nacht (ND) besetzt.
Frei/Urlaub/Krank werden berücksichtigt. Unbesetzte Dienste werden als
OFFEN-FD / OFFEN-SD / OFFEN-ND ausgewiesen.

Wunschschichten: Jeder MA kann bis zu 3 Wünsche (Tag + Schichtart) eintragen.
Wünsche werden mit höchster Priorität eingeplant, sofern keine harte Regel
(Gesperrt, Spät→Früh, max. Konsekutiv) verletzt wird. Kann ein Wunsch nicht
erfüllt werden, wird er als Violation ausgewiesen.

Springer: MA mit tagesstunden=0 (keine festen Stunden) werden im Plan
angezeigt (Urlaub/Krank sichtbar), bekommen aber KEINE automatischen Schichten.
Sie können manuell auf offene Dienste gesetzt werden.

Regeln:
- Max. 5 aufeinanderfolgende Arbeitstage
- Kein Früh direkt nach Spät (Vortag)
- Max. 3 gleiche Schichten in Folge (Früh oder Spät)
- Nach Nachtblock: mind. 2 Freitag-Puffer (soft, überschreibbar)
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

    @property
    def ist_springer(self) -> bool:
        """Springer = keine festen Stunden (tagesstunden == 0)."""
        return self.tagesstunden == 0.0

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
class Wunschschicht:
    """
    Wunsch eines Mitarbeiters für einen bestimmten Tag und eine Schichtart.

    tag:        Tag des Monats als Integer (z.B. 5 = 5. des Monats)
    dienst_str: "Früh", "Spät" oder "Nacht"
    """
    name:       str
    tag:        int          # Tag des Monats (1–31)
    dienst_str: str          # "Früh" | "Spät" | "Nacht"

    def to_dienst(self) -> Optional["Dienst"]:
        mapping = {"Früh": Dienst.FRUEH, "Spät": Dienst.SPAET, "Nacht": Dienst.NACHT}
        return mapping.get(self.dienst_str)

    def to_date(self, jahr: int, monat: int) -> Optional[date]:
        try:
            return date(jahr, monat, self.tag)
        except ValueError:
            return None


@dataclass
class PlanungState:
    ma:                 Mitarbeiter
    ist_stunden:        float = 0.0
    arbeitstage:        int   = 0
    frueh_count:        int   = 0
    spaet_count:        int   = 0
    nacht_count:        int   = 0
    konsekutiv_arbeits: int   = 0
    letzter_dienst:     Optional[Dienst] = None
    wunschfrei:         list[date] = field(default_factory=list)

    def add_schicht(self, d: Dienst):
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
        wunschschichten:   list[Wunschschicht] | None = None,
    ):
        self.ma_liste        = mitarbeiter_liste
        self.abwesenheiten   = abwesenheiten
        self.jahr            = jahr
        self.monat           = monat
        self.vormonat_plan   = vormonat_plan or {}
        self.wunschschichten = wunschschichten or []

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

        # Springer-Namen als Set für schnellen Lookup
        self._springer_namen: set[str] = {
            ma.name for ma in self.ma_liste if ma.ist_springer
        }

        # Index: ma_name → list[(tag_date, dienst)]
        self._wunsch_index: dict[str, list[tuple[date, Dienst]]] = {}

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------

    def _konsekutiv(self, ma_name: str, bis_exkl: date) -> int:
        count = 0
        vormonat_konsekutiv = self.states[ma_name].konsekutiv_arbeits
        check = bis_exkl - timedelta(days=1)
        while True:
            if check < self.tage[0]:
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
        return self.plan[ma_name].get(tag) is None

    def _kann_arbeiten(self, ma_name: str, tag: date) -> bool:
        # Springer bekommen KEINE automatischen Schichten
        if ma_name in self._springer_namen:
            return False
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
        vortag = tag - timedelta(days=1)
        if self.plan[ma_name].get(vortag) == Dienst.SPAET:
            return False
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
        return self._kann_arbeiten(ma_name, tag)

    def _kann_dienst(self, ma_name: str, tag: date, dienst: Dienst) -> bool:
        if dienst == Dienst.FRUEH:
            return self._kann_frueh(ma_name, tag)
        if dienst == Dienst.SPAET:
            return self._kann_spaet(ma_name, tag)
        if dienst == Dienst.NACHT:
            return self._kann_nacht(ma_name, tag)
        return False

    def _in_folge(self, ma_name: str, tag: date, dienst: Dienst) -> int:
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
        self.plan[ma_name][tag] = dienst
        if dienst in self.ARBEITSDIENSTE:
            self.states[ma_name].add_schicht(dienst)

    def _score(self, ma_name: str, dienst: Dienst) -> float:
        s = self.states[ma_name]
        score = 0.0
        score -= s.stunden_delta * 0.5
        if dienst == Dienst.FRUEH:
            score += s.frueh_count * 2.0
        elif dienst == Dienst.SPAET:
            score += s.spaet_count * 2.0
        elif dienst == Dienst.NACHT:
            score += s.nacht_count * 3.0
        score += self._konsekutiv(ma_name, date.today()) * 1.0
        return score

    # ------------------------------------------------------------------
    # Wunsch-Index aufbauen
    # ------------------------------------------------------------------

    def _build_wunsch_index(self):
        ma_namen = {ma.name for ma in self.ma_liste}
        for w in self.wunschschichten:
            if w.name not in ma_namen:
                self.violations.append(
                    f"Wunsch ignoriert: Unbekannter MA '{w.name}' "
                    f"(Tag {w.tag}, {w.dienst_str})"
                )
                continue
            wdatum = w.to_date(self.jahr, self.monat)
            if wdatum is None:
                self.violations.append(
                    f"Wunsch ignoriert: {w.name} – Tag {w.tag} existiert nicht "
                    f"in {self.monat}/{self.jahr}"
                )
                continue
            wdienst = w.to_dienst()
            if wdienst is None:
                self.violations.append(
                    f"Wunsch ignoriert: {w.name} – unbekannte Schichtart '{w.dienst_str}'"
                )
                continue
            self._wunsch_index.setdefault(w.name, []).append((wdatum, wdienst))

        total = sum(len(v) for v in self._wunsch_index.values())
        logger.info("Wunsch-Index aufgebaut: %d Wünsche für %d MA", total, len(self._wunsch_index))

    # ------------------------------------------------------------------
    # Wünsche einplanen
    # ------------------------------------------------------------------

    def _plan_wuensche(self):
        for ma_name, wuensche in self._wunsch_index.items():
            for wdatum, wdienst in wuensche:
                if wdatum not in self.tage:
                    continue

                if self._ist_gesperrt(ma_name, wdatum):
                    self.violations.append(
                        f"⚠️ Wunsch nicht erfüllt: {ma_name} {wdatum.strftime('%d.%m')} "
                        f"{wdienst.value} – Tag ist gesperrt (Urlaub/Krank/Frei)"
                    )
                    continue

                existing = self.plan[ma_name].get(wdatum)
                if existing is not None and existing != Dienst.FREI:
                    self.violations.append(
                        f"⚠️ Wunsch nicht erfüllt: {ma_name} {wdatum.strftime('%d.%m')} "
                        f"{wdienst.value} – bereits als {existing.value} eingeplant"
                    )
                    continue

                puffer_war_gesetzt = existing == Dienst.FREI
                if puffer_war_gesetzt:
                    del self.plan[ma_name][wdatum]

                if not self._kann_dienst(ma_name, wdatum, wdienst):
                    if puffer_war_gesetzt:
                        self.plan[ma_name][wdatum] = Dienst.FREI
                    self.violations.append(
                        f"⚠️ Wunsch nicht erfüllt: {ma_name} {wdatum.strftime('%d.%m')} "
                        f"{wdienst.value} – Regelkonflikt (Konsekutiv/Spät→Früh/3er-Regel)"
                    )
                    continue

                self._setze_dienst(ma_name, wdatum, wdienst)
                logger.info(
                    "Wunsch erfüllt: %s %s %s",
                    ma_name, wdatum.strftime("%d.%m"), wdienst.value,
                )

    # ------------------------------------------------------------------
    # Planungsschritte
    # ------------------------------------------------------------------

    def generate(self) -> dict[str, dict[date, Dienst]]:
        self._init_states()
        self._set_abwesenheiten()
        self._init_aus_vormonat()
        self._build_wunsch_index()
        self._plan_wuensche()
        self._plan_alle_dienste()
        self._fill_frei()
        self._build_offen_plan()
        self._validate()
        return self.plan

    def _init_states(self):
        for ma in self.ma_liste:
            ma.berechne_soll(self.arbeitstage_monat)
            self.states[ma.name] = PlanungState(ma=ma)

    def _set_abwesenheiten(self):
        """Setzt Urlaub/Krank/Frei — auch für Springer."""
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
        for ma_name, tage_plan in self.vormonat_plan.items():
            if ma_name not in self.states:
                continue
            s = self.states[ma_name]
            konsekutiv = 0
            for i in range(1, 8):
                vortag = erster - timedelta(days=i)
                d = tage_plan.get(vortag)
                if d in self.ARBEITSDIENSTE:
                    konsekutiv += 1
                else:
                    break
            s.konsekutiv_arbeits = konsekutiv
            vortag = erster - timedelta(days=1)
            s.letzter_dienst = tage_plan.get(vortag)

    def _plan_alle_dienste(self):
        """Nur Nicht-Springer werden automatisch eingeplant."""
        offen_map = {
            Dienst.FRUEH:  Dienst.OFFEN_FD,
            Dienst.SPAET:  Dienst.OFFEN_SD,
            Dienst.NACHT:  Dienst.OFFEN_ND,
        }
        nacht_puffer: dict[str, set[date]] = {ma.name: set() for ma in self.ma_liste}

        for tag in self.tage:
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

                kandidaten = []
                for ma in self.ma_liste:
                    # Springer nie als Kandidat
                    if ma.ist_springer:
                        continue
                    if (dienst in (Dienst.FRUEH, Dienst.SPAET)
                            and tag in nacht_puffer[ma.name]
                            and self.plan[ma.name].get(tag) == Dienst.FREI):
                        del self.plan[ma.name][tag]
                        if kann_fn(ma.name, tag):
                            kandidaten.append(ma)
                        else:
                            self.plan[ma.name][tag] = Dienst.FREI
                    elif kann_fn(ma.name, tag):
                        kandidaten.append(ma)

                kandidaten.sort(key=lambda ma: self._score(ma.name, dienst))

                for ma in kandidaten:
                    if bereits >= bedarf:
                        break
                    war_puffer = tag in nacht_puffer[ma.name]
                    if war_puffer and self.plan[ma.name].get(tag) == Dienst.FREI:
                        del self.plan[ma.name][tag]
                        nacht_puffer[ma.name].discard(tag)
                    self._setze_dienst(ma.name, tag, dienst)
                    bereits += 1

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

                fehlend = bedarf - bereits
                if fehlend > 0:
                    if tag not in self.offen:
                        self.offen[tag] = []
                    for _ in range(fehlend):
                        self.offen[tag].append(offen_map[dienst])

    def _fill_frei(self):
        """Füllt leere Slots:
        - Reguläre MA → Frei
        - Springer → leer lassen (nur Abwesenheiten bleiben stehen)
        """
        for ma in self.ma_liste:
            for tag in self.tage:
                if ma.ist_springer:
                    # Springer: leere Slots bleiben LEER (kein "Frei" eintragen)
                    pass
                else:
                    if self.plan[ma.name].get(tag) is None:
                        self.plan[ma.name][tag] = Dienst.FREI

        # Wochenend-Check nur für reguläre MA
        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
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

        # Spät→Früh nur für reguläre MA prüfen
        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
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

        # Springer auflisten
        springer_namen = sorted(self._springer_namen)
        if springer_namen:
            lines.append(f"\n🔄 Springer (keine festen Stunden): {', '.join(springer_namen)}")
            lines.append("   → Im Plan sichtbar, manuell auf offene Dienste setzen")

        # Wünsche-Zusammenfassung
        nicht_erfuellt = [v for v in self.violations if "Wunsch nicht erfüllt" in v]
        total_wuensche = len([
            x for lst in self._wunsch_index.values() for x in lst
        ])
        if total_wuensche > 0:
            lines.append(
                f"\n🙋 Wunschschichten: {total_wuensche - len(nicht_erfuellt)}/{total_wuensche} erfüllt"
            )
        if nicht_erfuellt:
            lines.append(f"  Nicht erfüllte Wünsche:")
            for v in nicht_erfuellt:
                lines.append(f"  {v}")

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

        # MA-Übersicht (nur reguläre MA)
        lines.append("\n📊 Mitarbeiter-Übersicht:")
        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
            s = self.states[ma.name]
            delta_sign = "+" if s.stunden_delta >= 0 else ""
            lines.append(
                f"  {ma.name:15s} | "
                f"FD:{s.frueh_count:2d}  SD:{s.spaet_count:2d}  ND:{s.nacht_count:2d} | "
                f"Ist:{s.ist_stunden:6.1f}h  "
                f"Soll:{ma.soll_stunden:6.1f}h  "
                f"Δ:{delta_sign}{s.stunden_delta:.1f}h"
            )

        # Regelviols
        rule_violations = [
            v for v in self.violations
            if "nicht besetzt" not in v
            and "nur " not in v
            and "Wunsch" not in v
        ]
        if rule_violations:
            lines.append(f"\n❌ Regelverstöße ({len(rule_violations)}):")
            for v in rule_violations[:30]:
                lines.append(f"  • {v}")

        return "\n".join(lines)
