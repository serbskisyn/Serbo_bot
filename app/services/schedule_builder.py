"""
schedule_builder.py — Dienstplan-Generator für Babyschutzhaus

Ziel: Jeder Tag wird mit je 2x Früh (FD), 2x Spät (SD), 2x Nacht (ND) besetzt.
Frei/Urlaub/Krank werden berücksichtigt. Unbesetzte Dienste werden als
OFFEN-FD / OFFEN-SD / OFFEN-ND ausgewiesen.

Wunschschichten: Jeder MA kann bis zu 3 Wünsche (Tag + Schichtart) eintragen.
Wünsche werden mit höchster Priorität eingeplant, sofern keine harte Regel
(Gesperrt, Spät→Früh, max. Konsekutiv) verletzt wird.

Springer: MA mit tagesstunden=0 bekommen KEINE automatischen Schichten.

Regeln (harte Regeln = [H], weiche Regeln = [W]):
- [H] Max. 5 aufeinanderfolgende Arbeitstage
- [H] Kein Früh direkt nach Spät (Vortag)
- [W] Max. 3 gleiche FD/SD-Schichten in Folge (im 2. Pass bis 4 gelockert)
- [H] Nachtdienste nur in Blöcken von 3–4 aufeinanderfolgenden Tagen
- [H] Nach einem vollständigen Nachtblock (>= 3 Nächte): mind. 2 Pflicht-Freitags
- [H] Innerhalb eines Nachtblocks darf keine andere Schichtart eingeplant werden
- [H] MA mit laufendem Nachtblock MÜSSEN diesen fortsetzen (Block-Vollendung)
- [W] Fairness: Gleichmäßige Verteilung aller Schichtarten über alle MA
- [W] Gleichmäßige Verteilung der Nachtblöcke über alle MA
- [W] Mindestens ein freies Wochenende pro Monat
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

NACHT_BLOCK_MIN = 3
NACHT_BLOCK_MAX = 4
NACHT_PUFFER_TAGE = 2


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
        return self.tagesstunden == 0.0

    def __post_init__(self):
        self.wochenstunden = round(self.tagesstunden * 5, 1)

    def berechne_soll(self, arbeitstage_monat: int):
        self.soll_stunden = round((self.wochenstunden / 5) * arbeitstage_monat, 1)


@dataclass
class Abwesenheit:
    name:  str
    art:   str
    datum: date


@dataclass
class Wunschschicht:
    name:       str
    tag:        int
    dienst_str: str

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
    nacht_blocks:       int   = 0
    konsekutiv_arbeits: int   = 0
    letzter_dienst:     Optional[Dienst] = None
    wunschfrei:         list[date] = field(default_factory=list)
    akt_nacht_block:    int   = 0
    # Vormonat-Letzter-Dienst (für _in_folge Grenzfall)
    vormonat_letzter:   Optional[Dienst] = None

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
            self.akt_nacht_block += 1
        else:
            if self.akt_nacht_block >= NACHT_BLOCK_MIN:
                self.nacht_blocks += 1
            self.akt_nacht_block = 0
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

        self._springer_namen: set[str] = {
            ma.name for ma in self.ma_liste if ma.ist_springer
        }
        self._wunsch_index: dict[str, list[tuple[date, Dienst]]] = {}
        # Nachtpuffer: MA → set of dates die als Pflichttfrei nach Block reserviert sind
        self._nacht_puffer: dict[str, set[date]] = {
            ma.name: set() for ma in self.ma_liste
        }

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------

    def _konsekutiv(self, ma_name: str, bis_exkl: date) -> int:
        """Anzahl aufeinanderfolgender Arbeitstage direkt vor bis_exkl."""
        count = 0
        check = bis_exkl - timedelta(days=1)
        while True:
            if check < self.tage[0]:
                # Vormonat-Konsekutiv nur einmal addieren
                count += self.states[ma_name].konsekutiv_arbeits
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
        """Basis-Check: Springer, gesperrt, konsekutiv."""
        if ma_name in self._springer_namen:
            return False
        if not self._slot_frei(ma_name, tag):
            return False
        if self._ist_gesperrt(ma_name, tag):
            return False
        if self._konsekutiv(ma_name, tag) >= 5:
            return False
        return True

    def _in_folge(self, ma_name: str, tag: date, dienst: Dienst) -> int:
        """Wie viele gleiche Dienste direkt vor 'tag' (inkl. Vormonat-Grenze)."""
        count = 0
        check = tag - timedelta(days=1)
        while True:
            if check < self.tage[0]:
                # Vormonat prüfen
                if (
                    count == 0
                    and self.states[ma_name].vormonat_letzter == dienst
                ):
                    count += 1
                break
            d = self.plan[ma_name].get(check)
            if d == dienst:
                count += 1
                check -= timedelta(days=1)
            else:
                break
        return count

    def _akt_nacht_block_len(self, ma_name: str, tag: date) -> int:
        """Anzahl aufeinanderfolgender Nachtdienste direkt VOR 'tag'."""
        return self._in_folge(ma_name, tag, Dienst.NACHT)

    def _ist_in_nacht_puffer(self, ma_name: str, tag: date) -> bool:
        """[H] Liegt 'tag' im Pflicht-Puffer nach einem vollständigen Nachtblock?"""
        return tag in self._nacht_puffer[ma_name]

    def _setze_nacht_puffer(self, ma_name: str, ab_tag: date):
        """Setzt 2 Pflicht-Freitags nach Nachtblock-Ende."""
        for offset in range(1, NACHT_PUFFER_TAGE + 1):
            p_tag = ab_tag + timedelta(days=offset - 1)
            if p_tag > self.tage[-1]:
                break
            self._nacht_puffer[ma_name].add(p_tag)
            # Slot überschreiben falls noch leer
            if self.plan[ma_name].get(p_tag) is None:
                self.plan[ma_name][p_tag] = Dienst.FREI

    def _ist_in_aktivem_nacht_block(self, ma_name: str, tag: date) -> bool:
        """True wenn MA gestern Nacht hatte (Block läuft noch)."""
        vortag = tag - timedelta(days=1)
        if vortag < self.tage[0]:
            return self.states[ma_name].vormonat_letzter == Dienst.NACHT
        return self.plan[ma_name].get(vortag) == Dienst.NACHT

    def _kann_frueh(self, ma_name: str, tag: date, locker: bool = False) -> bool:
        if not self._kann_arbeiten(ma_name, tag):
            return False
        # [H] Spät→Früh verboten
        vortag = tag - timedelta(days=1)
        vortag_d = (
            self.plan[ma_name].get(vortag)
            if vortag >= self.tage[0]
            else self.states[ma_name].vormonat_letzter
        )
        if vortag_d == Dienst.SPAET:
            return False
        # [H] Kein FD im Nacht-Puffer
        if self._ist_in_nacht_puffer(ma_name, tag):
            return False
        # [H] Kein FD während aktiven Nachtblocks
        if self._ist_in_aktivem_nacht_block(ma_name, tag):
            return False
        # [W] Max 3 FD in Folge (im lockeren Modus bis 4)
        limit = 4 if locker else 3
        if self._in_folge(ma_name, tag, Dienst.FRUEH) >= limit:
            return False
        return True

    def _kann_spaet(self, ma_name: str, tag: date, locker: bool = False) -> bool:
        if not self._kann_arbeiten(ma_name, tag):
            return False
        # [H] Kein SD im Nacht-Puffer
        if self._ist_in_nacht_puffer(ma_name, tag):
            return False
        # [H] Kein SD während aktiven Nachtblocks
        if self._ist_in_aktivem_nacht_block(ma_name, tag):
            return False
        # [W] Max 3 SD in Folge (im lockeren Modus bis 4)
        limit = 4 if locker else 3
        if self._in_folge(ma_name, tag, Dienst.SPAET) >= limit:
            return False
        return True

    def _kann_nacht(self, ma_name: str, tag: date) -> bool:
        if not self._kann_arbeiten(ma_name, tag):
            return False
        # [H] Kein ND im Nacht-Puffer
        if self._ist_in_nacht_puffer(ma_name, tag):
            return False
        # [H] Block max NACHT_BLOCK_MAX
        if self._akt_nacht_block_len(ma_name, tag) >= NACHT_BLOCK_MAX:
            return False
        return True

    def _muss_nacht_fortsetzen(self, ma_name: str, tag: date) -> bool:
        """
        [H] MA hat gestern Nacht gemacht, der Block ist noch nicht abgeschlossen
        (< NACHT_BLOCK_MAX), und der Slot ist noch frei → MUSS Nacht machen.
        """
        block_len = self._akt_nacht_block_len(ma_name, tag)
        if block_len == 0:
            return False
        if block_len >= NACHT_BLOCK_MAX:
            return False  # Block voll, darf nicht mehr
        if not self._kann_arbeiten(ma_name, tag):
            return False
        if self._ist_in_nacht_puffer(ma_name, tag):
            return False
        return True

    def _kann_dienst(self, ma_name: str, tag: date, dienst: Dienst, locker: bool = False) -> bool:
        if dienst == Dienst.FRUEH:
            return self._kann_frueh(ma_name, tag, locker)
        if dienst == Dienst.SPAET:
            return self._kann_spaet(ma_name, tag, locker)
        if dienst == Dienst.NACHT:
            return self._kann_nacht(ma_name, tag)
        return False

    def _setze_dienst(self, ma_name: str, tag: date, dienst: Dienst):
        self.plan[ma_name][tag] = dienst
        if dienst in self.ARBEITSDIENSTE:
            self.states[ma_name].add_schicht(dienst)
            # Nach Nacht-Setzen: prüfe ob Block abgeschlossen werden muss
            if dienst == Dienst.NACHT:
                block_len = self._akt_nacht_block_len(ma_name, tag + timedelta(days=1))
                # Wenn morgen max erreicht → Puffer vormerken (wird in _plan_nacht_tag gesetzt)
        else:
            # Nicht-Arbeitsdienst → Nachtblock abschließen
            block_len = self.states[ma_name].akt_nacht_block
            self.states[ma_name].add_schicht(dienst)

    def _score(self, ma_name: str, dienst: Dienst, tag: date) -> float:
        """
        Niedrigerer Score = bevorzugt.

        Faktoren:
        1. Stunden-Delta (Gewicht 3.0): MA mit hohem Minus stark bevorzugen
        2. Schichtart-Fairness (Gewicht 10.0): unterrepräsentierte Schicht bevorzugen
        3. Konsekutive Arbeitstage (Gewicht 0.5): weniger Tage am Stück bevorzugen
        4. Nachtblock-Fairness (Gewicht 2.0): weniger Nachtblöcke bevorzugen
        5. Zufallskomponente (Gewicht 0.01): Tiebreaker
        """
        s = self.states[ma_name]
        gesamt = s.gesamt_schichten
        score = 0.0

        # 1. Stunden-Delta — STARK gewichtet (war 0.8, jetzt 3.0)
        score -= s.stunden_delta * 3.0

        # 2. Schichtart-Fairness
        if gesamt > 0:
            if dienst == Dienst.FRUEH:
                ratio = s.frueh_count / gesamt
            elif dienst == Dienst.SPAET:
                ratio = s.spaet_count / gesamt
            else:
                ratio = s.nacht_count / gesamt
            score += ratio * 10.0

        # 3. Konsekutive Arbeitstage
        score += self._konsekutiv(ma_name, tag) * 0.5

        # 4. Nachtblock-Fairness
        if dienst == Dienst.NACHT:
            score += s.nacht_blocks * 2.0

        # 5. Zufallskomponente
        score += (hash(f"{ma_name}{tag}") % 100) * 0.01

        return score

    # ------------------------------------------------------------------
    # Wunsch-Index
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

    def _plan_wuensche(self):
        for ma_name, wuensche in self._wunsch_index.items():
            for wdatum, wdienst in wuensche:
                if wdatum not in self.tage:
                    continue
                if self._ist_gesperrt(ma_name, wdatum):
                    self.violations.append(
                        f"⚠️ Wunsch nicht erfüllt: {ma_name} {wdatum.strftime('%d.%m')} "
                        f"{wdienst.value} – Tag ist gesperrt"
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
                        f"{wdienst.value} – Regelkonflikt"
                    )
                    continue
                self._setze_dienst(ma_name, wdatum, wdienst)

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
            letzter = tage_plan.get(vortag)
            s.letzter_dienst = letzter
            s.vormonat_letzter = letzter
            # Vormonat-Nachtblock: war letzter Dienst Nacht?
            if letzter == Dienst.NACHT:
                # Wieviele Nächte am Ende des Vormonats?
                nb = 0
                for i in range(1, NACHT_BLOCK_MAX + 1):
                    check = erster - timedelta(days=i)
                    if tage_plan.get(check) == Dienst.NACHT:
                        nb += 1
                    else:
                        break
                s.akt_nacht_block = nb

    # ------------------------------------------------------------------
    # Nachtblock-Planung
    # ------------------------------------------------------------------

    def _plan_nacht_tag(self, tag: date) -> int:
        """
        Plant Nachtdienste für 'tag'.

        Prioritäten:
        A) MA die MÜSSEN (laufender Block, noch nicht abgeschlossen)
        B) MA die KÖNNEN und einen neuen Block starten (fairste zuerst)

        Nach dem Setzen: prüft ob Block jetzt >= MIN → Puffer setzen wenn
        der nächste Tag kein Nacht mehr wird.
        """
        bedarf = PFLICHT[Dienst.NACHT]
        bereits = sum(
            1 for ma in self.ma_liste
            if self.plan[ma.name].get(tag) == Dienst.NACHT
        )
        if bereits >= bedarf:
            return bereits

        # Gruppe A: müssen Nacht fortsetzen (harte Pflicht)
        muss_ma = []
        kann_ma = []

        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
            if self._muss_nacht_fortsetzen(ma.name, tag):
                muss_ma.append(ma)
            elif self._kann_nacht(ma.name, tag):
                kann_ma.append(ma)

        # Gruppe A zuerst (Pflicht)
        muss_ma.sort(key=lambda ma: self._score(ma.name, Dienst.NACHT, tag))
        for ma in muss_ma:
            if bereits >= bedarf:
                break
            self._setze_dienst(ma.name, tag, Dienst.NACHT)
            bereits += 1

        # Gruppe B: neue Blöcke starten
        kann_ma.sort(key=lambda ma: self._score(ma.name, Dienst.NACHT, tag))
        for ma in kann_ma:
            if bereits >= bedarf:
                break
            self._setze_dienst(ma.name, tag, Dienst.NACHT)
            bereits += 1

        # Puffer setzen: für jeden MA der heute Nacht hat und dessen Block endet
        for ma in self.ma_liste:
            if self.plan[ma.name].get(tag) != Dienst.NACHT:
                continue
            morgen = tag + timedelta(days=1)
            if morgen > self.tage[-1]:
                # Monatsende: Block-Abschluss
                block_len = self._akt_nacht_block_len(ma.name, morgen)
                if block_len >= NACHT_BLOCK_MIN:
                    self._setze_nacht_puffer(ma.name, morgen)
                continue
            # Morgen kein Nacht mehr geplant UND Block >= MIN → Puffer
            morgen_d = self.plan[ma.name].get(morgen)
            if morgen_d != Dienst.NACHT and morgen_d is not None:
                # Block wird unterbrochen → Puffer prüfen
                block_len = self._akt_nacht_block_len(ma.name, morgen)
                if block_len >= NACHT_BLOCK_MIN:
                    # Puffer bereits gesetzt durch späteres Datum, nichts tun
                    pass
            # Block-Max erreicht → nach morgen Puffer setzen
            block_len = self._akt_nacht_block_len(ma.name, morgen)
            if block_len >= NACHT_BLOCK_MAX:
                self._setze_nacht_puffer(ma.name, morgen)

        return bereits

    def _finalisiere_nacht_puffer(self):
        """
        Nach dem ersten Planungsdurchlauf: für alle MA, bei denen ein
        Nachtblock abgeschlossen wurde aber noch kein Puffer gesetzt ist,
        den Puffer nachholen.
        """
        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
            in_block = False
            block_len = 0
            for i, tag in enumerate(self.tage):
                d = self.plan[ma.name].get(tag)
                if d == Dienst.NACHT:
                    if not in_block:
                        in_block = True
                        block_len = 1
                    else:
                        block_len += 1
                else:
                    if in_block:
                        # Block endet hier
                        if block_len >= NACHT_BLOCK_MIN:
                            # Prüfe ob Puffer bereits gesetzt
                            puffer_ok = all(
                                tag + timedelta(days=offset) in self._nacht_puffer[ma.name]
                                for offset in range(NACHT_PUFFER_TAGE)
                                if tag + timedelta(days=offset) <= self.tage[-1]
                            )
                            if not puffer_ok:
                                self._setze_nacht_puffer(ma.name, tag)
                        in_block = False
                        block_len = 0

    # ------------------------------------------------------------------
    # Alle Dienste planen
    # ------------------------------------------------------------------

    def _plan_alle_dienste(self):
        offen_map = {
            Dienst.FRUEH:  Dienst.OFFEN_FD,
            Dienst.SPAET:  Dienst.OFFEN_SD,
            Dienst.NACHT:  Dienst.OFFEN_ND,
        }

        # --- Pass 1: Normaler Durchlauf (strenge Regeln) ---
        for tag in self.tage:
            # 1) Nacht
            nacht_besetzt = self._plan_nacht_tag(tag)

            fehlend_nacht = PFLICHT[Dienst.NACHT] - nacht_besetzt
            if fehlend_nacht > 0:
                self.offen.setdefault(tag, [])
                for _ in range(fehlend_nacht):
                    self.offen[tag].append(Dienst.OFFEN_ND)

            # 2) FD und SD (streng, max 3 in Folge)
            for dienst in [Dienst.FRUEH, Dienst.SPAET]:
                bedarf = PFLICHT[dienst]
                bereits = sum(
                    1 for ma in self.ma_liste
                    if self.plan[ma.name].get(tag) == dienst
                )
                if bereits >= bedarf:
                    continue

                kandidaten = [
                    ma for ma in self.ma_liste
                    if not ma.ist_springer
                    and self._kann_dienst(ma.name, tag, dienst, locker=False)
                ]
                kandidaten.sort(key=lambda ma: self._score(ma.name, dienst, tag))

                for ma in kandidaten:
                    if bereits >= bedarf:
                        break
                    self._setze_dienst(ma.name, tag, dienst)
                    bereits += 1

                # Fehlende merken (werden in Pass 2 nochmal versucht)
                fehlend = bedarf - bereits
                if fehlend > 0:
                    self.offen.setdefault(tag, [])
                    for _ in range(fehlend):
                        self.offen[tag].append(offen_map[dienst])

        # Puffer nachfinalisieren
        self._finalisiere_nacht_puffer()

        # --- Pass 2: Lockerer Durchlauf für noch offene FD/SD ---
        # Versucht MA die im 1. Pass durch Soft-Rules blockiert waren
        tage_mit_fd_offen = [
            tag for tag in self.tage
            if any(d == Dienst.OFFEN_FD for d in self.offen.get(tag, []))
        ]
        tage_mit_sd_offen = [
            tag for tag in self.tage
            if any(d == Dienst.OFFEN_SD for d in self.offen.get(tag, []))
        ]

        for tag in tage_mit_fd_offen:
            offen_count = sum(1 for d in self.offen.get(tag, []) if d == Dienst.OFFEN_FD)
            gesetzt = 0
            kandidaten = [
                ma for ma in self.ma_liste
                if not ma.ist_springer
                and self._kann_dienst(ma.name, tag, Dienst.FRUEH, locker=True)
            ]
            kandidaten.sort(key=lambda ma: self._score(ma.name, Dienst.FRUEH, tag))
            for ma in kandidaten:
                if gesetzt >= offen_count:
                    break
                self._setze_dienst(ma.name, tag, Dienst.FRUEH)
                gesetzt += 1
            # Offene Liste aktualisieren
            for _ in range(gesetzt):
                if Dienst.OFFEN_FD in self.offen.get(tag, []):
                    self.offen[tag].remove(Dienst.OFFEN_FD)
            if not self.offen.get(tag):
                self.offen.pop(tag, None)

        for tag in tage_mit_sd_offen:
            offen_count = sum(1 for d in self.offen.get(tag, []) if d == Dienst.OFFEN_SD)
            gesetzt = 0
            kandidaten = [
                ma for ma in self.ma_liste
                if not ma.ist_springer
                and self._kann_dienst(ma.name, tag, Dienst.SPAET, locker=True)
            ]
            kandidaten.sort(key=lambda ma: self._score(ma.name, Dienst.SPAET, tag))
            for ma in kandidaten:
                if gesetzt >= offen_count:
                    break
                self._setze_dienst(ma.name, tag, Dienst.SPAET)
                gesetzt += 1
            for _ in range(gesetzt):
                if Dienst.OFFEN_SD in self.offen.get(tag, []):
                    self.offen[tag].remove(Dienst.OFFEN_SD)
            if not self.offen.get(tag):
                self.offen.pop(tag, None)

        # --- Pass 3: Stunden-Ausgleich ---
        # MA mit sehr hohem Minus bekommen zusätzliche Schichten auf freien Slots
        self._pass_stunden_ausgleich()

    def _pass_stunden_ausgleich(self):
        """
        Pass 3: MA deren Ist-Stunden < 70% der Soll-Stunden sind,
        bekommen auf freien Slots nachträglich Schichten zugewiesen.
        Bevorzugt abwechselnde Schichtarten (FD/SD).
        """
        for tag in self.tage:
            # MA mit hohem Stunden-Minus sortiert (größtes Minus zuerst)
            beduerft = [
                ma for ma in self.ma_liste
                if not ma.ist_springer
                and self.states[ma.name].stunden_delta > self.states[ma.name].ma.soll_stunden * 0.30
                and self.plan[ma.name].get(tag) is None
            ]
            if not beduerft:
                continue

            beduerft.sort(key=lambda ma: -self.states[ma.name].stunden_delta)

            for ma in beduerft:
                # Bevorzuge die Schichtart die der MA am wenigsten hatte
                s = self.states[ma.name]
                gesamt = s.gesamt_schichten
                if gesamt == 0:
                    praeferenz = [Dienst.FRUEH, Dienst.SPAET]
                else:
                    ratios = {
                        Dienst.FRUEH: s.frueh_count / gesamt,
                        Dienst.SPAET: s.spaet_count / gesamt,
                    }
                    praeferenz = sorted(ratios, key=lambda d: ratios[d])

                for dienst in praeferenz:
                    if self._kann_dienst(ma.name, tag, dienst, locker=True):
                        self._setze_dienst(ma.name, tag, dienst)
                        break

    def _fill_frei(self):
        for ma in self.ma_liste:
            for tag in self.tage:
                if not ma.ist_springer:
                    if self.plan[ma.name].get(tag) is None:
                        self.plan[ma.name][tag] = Dienst.FREI

        # Wochenend-Check
        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
            hat_frei_we = False
            for tag in self.tage:
                if tag.weekday() == 5:
                    so = tag + timedelta(days=1)
                    if (
                        self.plan[ma.name].get(tag) == Dienst.FREI
                        and so <= self.tage[-1]
                        and self.plan[ma.name].get(so) == Dienst.FREI
                    ):
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
                if ist + offen_count < anzahl:
                    self.violations.append(
                        f"{tag.strftime('%d.%m')}: {dienst.value} "
                        f"nur {ist + offen_count}/{anzahl} besetzt"
                    )

        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
            for tag in self.tage:
                vortag = tag - timedelta(days=1)
                vortag_d = (
                    self.plan[ma.name].get(vortag)
                    if vortag >= self.tage[0]
                    else self.states[ma.name].vormonat_letzter
                )
                if vortag_d == Dienst.SPAET and self.plan[ma.name].get(tag) == Dienst.FRUEH:
                    self.violations.append(
                        f"{ma.name} {tag.strftime('%d.%m')}: Spät→Früh verboten"
                    )

        # Nachtblock-Validierung
        for ma in self.ma_liste:
            if ma.ist_springer:
                continue
            i = 0
            while i < len(self.tage):
                tag = self.tage[i]
                if self.plan[ma.name].get(tag) == Dienst.NACHT:
                    block_len = 0
                    j = i
                    while j < len(self.tage) and self.plan[ma.name].get(self.tage[j]) == Dienst.NACHT:
                        block_len += 1
                        j += 1
                    if block_len < NACHT_BLOCK_MIN:
                        self.violations.append(
                            f"{ma.name} ab {tag.strftime('%d.%m')}: "
                            f"Nachtblock zu kurz ({block_len}/{NACHT_BLOCK_MIN})"
                        )
                    # Puffer prüfen
                    puffer_start = self.tage[j] if j < len(self.tage) else None
                    if puffer_start and block_len >= NACHT_BLOCK_MIN:
                        for offset in range(NACHT_PUFFER_TAGE):
                            p_tag = puffer_start + timedelta(days=offset)
                            if p_tag > self.tage[-1]:
                                break
                            d_p = self.plan[ma.name].get(p_tag)
                            if d_p in self.ARBEITSDIENSTE:
                                self.violations.append(
                                    f"{ma.name} {p_tag.strftime('%d.%m')}: "
                                    f"Arbeit während Nacht-Puffer ({d_p.value})"
                                )
                    i = j
                else:
                    i += 1

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def get_report(self) -> str:
        lines = [f"=== Dienstplan {self.monat}/{self.jahr} ==="]

        springer_namen = sorted(self._springer_namen)
        if springer_namen:
            lines.append(f"\n🔄 Springer: {', '.join(springer_namen)}")

        nicht_erfuellt = [v for v in self.violations if "Wunsch nicht erfüllt" in v]
        total_wuensche = sum(len(v) for v in self._wunsch_index.values())
        if total_wuensche > 0:
            lines.append(
                f"\n🙋 Wunschschichten: {total_wuensche - len(nicht_erfuellt)}/{total_wuensche} erfüllt"
            )
        if nicht_erfuellt:
            lines.append("  Nicht erfüllte Wünsche:")
            for v in nicht_erfuellt:
                lines.append(f"  {v}")

        alle_offen = [
            f"  {tag.strftime('%d.%m')} → {d.value}"
            for tag, dienste in sorted(self.offen.items())
            for d in dienste
        ]
        if alle_offen:
            lines.append(f"\n⚠️  Offene Dienste ({len(alle_offen)}):")
            lines.extend(alle_offen)
        else:
            lines.append("\n✅ Alle Dienste vollständig besetzt")

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
