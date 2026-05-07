"""
KontrollAgent — prüft einen fertigen Dienstplan gegen die Regeln
des DienstplanGenerators und gibt strukturiertes Feedback zurück.

Wird vom Orchestrator nach jeder generate()-Runde aufgerufen.
Bei Fehlern gibt er Constraints zurück, die generate() in der
nächsten Runde berücksichtigen soll.
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from dataclasses import dataclass, field

from app.services.schedule_builder import (
    DienstplanGenerator,
    Dienst,
    PFLICHT,
    NACHT_BLOCK_MIN,
    NACHT_PUFFER_TAGE,
)

logger = logging.getLogger(__name__)


@dataclass
class KontrollErgebnis:
    ok: bool
    fehler: list[str] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)  # Für nächste Korrektur-Runde

    def zusammenfassung(self) -> str:
        lines = []
        if self.ok:
            lines.append("✅ Kontroll-Agent: Plan regelkonform.")
        else:
            lines.append(f"❌ Kontroll-Agent: {len(self.fehler)} Fehler gefunden.")
        for f in self.fehler:
            lines.append(f"  • {f}")
        if self.warnungen:
            lines.append(f"  ⚠️ {len(self.warnungen)} Warnungen:")
            for w in self.warnungen:
                lines.append(f"    – {w}")
        return "\n".join(lines)


class KontrollAgent:
    """
    Prüft den fertigen Plan des DienstplanGenerators.

    Geprüfte Regeln:
    [H1] Mindestbesetzung pro Tag (FD/SD/ND je >= PFLICHT)
    [H2] Spät→Früh verboten
    [H3] Nachtblöcke mind. NACHT_BLOCK_MIN Tage
    [H4] Nacht-Puffer eingehalten (keine Arbeit in den 2 Tagen nach Nachtblock)
    [H5] Kein freies Wochenende verletzt (mind. 1 pro MA)
    [W1] Stunden-Delta: MA mit > 5h Abweichung vom Soll
    [W2] Wunsch-Erfüllungsrate < 70%
    """

    def run(self, gen: DienstplanGenerator) -> KontrollErgebnis:
        fehler: list[str] = []
        warnungen: list[str] = []
        constraints: dict = {}

        # [H1] Mindestbesetzung
        for tag in gen.tage:
            for dienst, anzahl in PFLICHT.items():
                ist = sum(
                    1 for ma in gen.ma_liste
                    if gen.plan[ma.name].get(tag) == dienst
                )
                offen_map = {
                    Dienst.FRUEH: Dienst.OFFEN_FD,
                    Dienst.SPAET: Dienst.OFFEN_SD,
                    Dienst.NACHT: Dienst.OFFEN_ND,
                }
                offen_count = sum(
                    1 for d in gen.offen.get(tag, [])
                    if d == offen_map[dienst]
                )
                gesamt = ist + offen_count
                if gesamt < anzahl:
                    fehler.append(
                        f"[H1] {tag.strftime('%d.%m')}: {dienst.value} "
                        f"nur {gesamt}/{anzahl} besetzt"
                    )
                    constraints.setdefault("unterbesetzt", []).append(
                        {"tag": tag.isoformat(), "dienst": dienst.value, "fehlt": anzahl - gesamt}
                    )

        # [H2] Spät→Früh
        for ma in gen.ma_liste:
            if ma.ist_springer:
                continue
            for tag in gen.tage:
                vortag = tag - timedelta(days=1)
                vortag_d = (
                    gen.plan[ma.name].get(vortag)
                    if vortag >= gen.tage[0]
                    else gen.states[ma.name].vormonat_letzter
                )
                if vortag_d == Dienst.SPAET and gen.plan[ma.name].get(tag) == Dienst.FRUEH:
                    fehler.append(f"[H2] {ma.name} {tag.strftime('%d.%m')}: Spät→Früh verboten")

        # [H3] Nachtblock-Länge
        for ma in gen.ma_liste:
            if ma.ist_springer:
                continue
            i = 0
            while i < len(gen.tage):
                tag = gen.tage[i]
                if gen.plan[ma.name].get(tag) == Dienst.NACHT:
                    block_len = 0
                    j = i
                    while j < len(gen.tage) and gen.plan[ma.name].get(gen.tage[j]) == Dienst.NACHT:
                        block_len += 1
                        j += 1
                    if block_len < NACHT_BLOCK_MIN:
                        fehler.append(
                            f"[H3] {ma.name} ab {tag.strftime('%d.%m')}: "
                            f"Nachtblock zu kurz ({block_len}/{NACHT_BLOCK_MIN})"
                        )
                    i = j
                else:
                    i += 1

        # [H4] Nacht-Puffer
        for ma in gen.ma_liste:
            if ma.ist_springer:
                continue
            i = 0
            while i < len(gen.tage):
                tag = gen.tage[i]
                if gen.plan[ma.name].get(tag) == Dienst.NACHT:
                    j = i
                    while j < len(gen.tage) and gen.plan[ma.name].get(gen.tage[j]) == Dienst.NACHT:
                        j += 1
                    block_len = j - i
                    if block_len >= NACHT_BLOCK_MIN:
                        for offset in range(NACHT_PUFFER_TAGE):
                            p_tag = gen.tage[i] + timedelta(days=block_len + offset)
                            if p_tag > gen.tage[-1]:
                                break
                            d_p = gen.plan[ma.name].get(p_tag)
                            if d_p in {Dienst.FRUEH, Dienst.SPAET, Dienst.NACHT}:
                                fehler.append(
                                    f"[H4] {ma.name} {p_tag.strftime('%d.%m')}: "
                                    f"Arbeit im Nacht-Puffer ({d_p.value})"
                                )
                    i = j
                else:
                    i += 1

        # [H5] Freies Wochenende
        for ma in gen.ma_liste:
            if ma.ist_springer:
                continue
            hat_frei_we = False
            for tag in gen.tage:
                if tag.weekday() == 5:
                    so = tag + timedelta(days=1)
                    if so > gen.tage[-1]:
                        continue
                    sa_d = gen.plan[ma.name].get(tag)
                    so_d = gen.plan[ma.name].get(so)
                    arbeit = {Dienst.FRUEH, Dienst.SPAET, Dienst.NACHT}
                    if sa_d not in arbeit and so_d not in arbeit:
                        hat_frei_we = True
                        break
            if not hat_frei_we:
                fehler.append(f"[H5] {ma.name}: kein freies Wochenende im Monat")
                constraints.setdefault("kein_freies_we", []).append(ma.name)

        # [W1] Stunden-Delta
        for ma in gen.ma_liste:
            if ma.ist_springer:
                continue
            delta = gen.states[ma.name].stunden_delta
            if abs(delta) > 5.0:
                sign = "+" if delta > 0 else ""
                warnungen.append(
                    f"[W1] {ma.name}: Δ={sign}{delta:.1f}h "
                    f"(Ist={gen.states[ma.name].ist_stunden:.1f}h / "
                    f"Soll={ma.soll_stunden:.1f}h)"
                )

        # [W2] Wunsch-Erfüllungsrate
        total_wuensche = sum(len(v) for v in gen._wunsch_index.values())
        nicht_erfuellt = sum(
            1 for v in gen.violations if "Wunsch nicht erfüllt" in v
        )
        if total_wuensche > 0:
            rate = (total_wuensche - nicht_erfuellt) / total_wuensche
            if rate < 0.7:
                warnungen.append(
                    f"[W2] Wunsch-Erfüllungsrate {rate*100:.0f}% "
                    f"({total_wuensche - nicht_erfuellt}/{total_wuensche})"
                )

        ok = len(fehler) == 0
        ergebnis = KontrollErgebnis(
            ok=ok,
            fehler=fehler,
            warnungen=warnungen,
            constraints=constraints,
        )
        logger.info(
            "KontrollAgent: ok=%s | %d Fehler | %d Warnungen",
            ok, len(fehler), len(warnungen),
        )
        return ergebnis
