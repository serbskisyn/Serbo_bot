"""
Orchestrator — koordiniert alle Schedule-Agenten und den Kontroll-Loop.

Flow:
  1. MitarbeiterAgent  → ma_liste
  2. UrlaubAgent       → abwesenheiten (Urlaub)
  3. KrankAgent        → abwesenheiten (Krank + manuell)
  4. VormonatAgent     → vormonat_plan
  5. WunschAgent       → wunschschichten
  6. DienstplanGenerator.generate()
  7. KontrollAgent     → KontrollErgebnis
     └─ Fehler? → Violations als Constraint übergeben → max. MAX_RUNDEN Runden
     └─ OK / max. Runden erreicht → fertig
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from app.services.schedule_builder import (
    Abwesenheit,
    DienstplanGenerator,
    Dienst,
)
from app.agents.schedule.mitarbeiter_agent import MitarbeiterAgent
from app.agents.schedule.urlaub_agent import UrlaubAgent
from app.agents.schedule.krank_agent import KrankAgent
from app.agents.schedule.vormonat_agent import VormonatAgent
from app.agents.schedule.wunsch_agent import WunschAgent
from app.agents.schedule.kontroll_agent import KontrollAgent, KontrollErgebnis

logger = logging.getLogger(__name__)

MAX_RUNDEN = 3

# Callback-Typ für Status-Nachrichten an Telegram
StatusCallback = Callable[[str], Awaitable[None]]


@dataclass
class OrchestratorErgebnis:
    gen: DienstplanGenerator
    kontroll: KontrollErgebnis
    runden: int
    status_log: list[str] = field(default_factory=list)
    springer_namen: list[str] = field(default_factory=list)


class ScheduleOrchestrator:
    """
    Koordiniert alle Sub-Agenten und den Generierungs-Kontroll-Loop.
    status_cb: async Callable das Status-Meldungen an Telegram schickt.
    """

    def __init__(self, status_cb: StatusCallback):
        self.status_cb = status_cb

    async def run(
        self,
        monat: int,
        jahr: int,
        manuell_krank: list[Abwesenheit] | None = None,
    ) -> OrchestratorErgebnis:
        log: list[str] = []

        # ── 1. Mitarbeiter ────────────────────────────────────────────
        await self.status_cb("⏳ Lade Mitarbeiter …")
        ma_liste, springer_namen, ma_error = MitarbeiterAgent().run()
        if ma_error:
            await self.status_cb(f"⚠️ Mitarbeiterliste nicht ladbar: {ma_error}\nNutze Fallback-Liste.")
        else:
            namen_str = ", ".join(ma.name for ma in ma_liste)
            await self.status_cb(f"👥 {len(ma_liste)} Mitarbeiter geladen: {namen_str}")
        if springer_namen:
            await self.status_cb(
                f"🔄 Springer erkannt: {', '.join(springer_namen)}\n"
                "   → Werden im Plan angezeigt (nur Urlaub/Krank), aber nicht automatisch eingeplant."
            )
        log.append(f"MA: {len(ma_liste)} ({len(springer_namen)} Springer)")

        bekannte_namen = {ma.name for ma in ma_liste}

        # ── 2. Urlaub ─────────────────────────────────────────────────
        await self.status_cb("⏳ Lade Urlaubsdaten …")
        urlaub_liste, urlaub_error = UrlaubAgent().run()
        if urlaub_error:
            await self.status_cb(f"⚠️ Urlaubsdaten nicht ladbar: {urlaub_error}")
        else:
            logger.info("Urlaub geladen: %d Einträge", len(urlaub_liste))
        log.append(f"Urlaub: {len(urlaub_liste)} Einträge")

        # ── 3. Krank (Sheet + manuell) ────────────────────────────────
        await self.status_cb("⏳ Lade Krankenstand …")
        krank_liste, krank_error = KrankAgent().run(manuell=manuell_krank)
        if krank_error:
            await self.status_cb(f"⚠️ Krankenstand nicht ladbar: {krank_error}")
        elif krank_liste:
            namen_krank = list({a.name for a in krank_liste})
            await self.status_cb(
                f"🤒 Krankenstand: {len(krank_liste)} Tage ({', '.join(namen_krank)})"
            )
        else:
            await self.status_cb("ℹ️ Kein Krankenstand eingetragen.")
        log.append(f"Krank: {len(krank_liste)} Tage")

        # ── 4. Vormonat ───────────────────────────────────────────────
        await self.status_cb("⏳ Lade Vormonatsplan …")
        vormonat_plan, vormonat_info = VormonatAgent().run(jahr=jahr, monat=monat)
        if vormonat_info:
            await self.status_cb(f"ℹ️ {vormonat_info}")
        elif vormonat_plan:
            await self.status_cb(f"🗂️ Vormonats-Plan geladen ({len(vormonat_plan)} MA).")
        log.append(f"Vormonat: {len(vormonat_plan)} MA")

        # ── 5. Wunschschichten ────────────────────────────────────────
        await self.status_cb("⏳ Lade Wunschschichten …")
        wuensche, wunsch_error = WunschAgent().run(
            monat=monat, jahr=jahr, bekannte_namen=bekannte_namen
        )
        if wunsch_error:
            await self.status_cb(f"⚠️ Wunschschichten nicht ladbar: {wunsch_error}")
        elif wuensche:
            personen = list({w.name for w in wuensche})
            await self.status_cb(
                f"🙋 {len(wuensche)} Wunschschichten von {len(personen)} Personen geladen: "
                f"{', '.join(personen)}"
            )
        else:
            await self.status_cb("ℹ️ Keine Wunschschichten für diesen Monat gefunden.")
        log.append(f"Wünsche: {len(wuensche)}")

        abwesenheiten = urlaub_liste + krank_liste

        # ── 6+7. Generieren + Kontroll-Loop ──────────────────────────
        kontroll_agent = KontrollAgent()
        gen: DienstplanGenerator | None = None
        kontroll: KontrollErgebnis | None = None
        runden = 0
        violations_vorherige: list[str] = []

        for runde in range(1, MAX_RUNDEN + 1):
            runden = runde
            await self.status_cb(f"⚙️ Generiere Plan (Runde {runde}/{MAX_RUNDEN}) …")

            gen = DienstplanGenerator(
                mitarbeiter_liste=ma_liste,
                abwesenheiten=abwesenheiten,
                jahr=jahr,
                monat=monat,
                vormonat_plan=vormonat_plan,
                wunschschichten=wuensche,
            )
            # Violations aus Vorrunde als Hinweis übergeben (Logging, keine harte Steuerung)
            if violations_vorherige:
                gen.violations = list(violations_vorherige)

            gen.generate()
            kontroll = kontroll_agent.run(gen)
            log.append(f"Runde {runde}: {len(kontroll.fehler)} Fehler, {len(kontroll.warnungen)} Warnungen")

            if kontroll.ok:
                await self.status_cb(
                    f"✅ Plan regelkonform nach Runde {runde}.\n"
                    + (f"  ⚠️ {len(kontroll.warnungen)} Warnungen\n" if kontroll.warnungen else "")
                )
                break
            else:
                await self.status_cb(
                    f"🔄 Runde {runde}: {len(kontroll.fehler)} Regelfehler — starte Korrektur …\n"
                    + "\n".join(f"  • {f}" for f in kontroll.fehler[:5])
                    + ("\n  …" if len(kontroll.fehler) > 5 else "")
                )
                violations_vorherige = list(kontroll.fehler)
                if runde == MAX_RUNDEN:
                    await self.status_cb(
                        f"⚠️ Max. Runden ({MAX_RUNDEN}) erreicht — Plan mit verbleibenden "
                        f"{len(kontroll.fehler)} Fehlern wird übernommen."
                    )

        return OrchestratorErgebnis(
            gen=gen,
            kontroll=kontroll,
            runden=runden,
            status_log=log,
            springer_namen=springer_namen,
        )
