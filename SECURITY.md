# Security Policy

## Supported Versions

Es wird nur `main` aktiv gewartet. Forks sind eigenverantwortlich.

## Reporting a Vulnerability

Wenn du eine Sicherheitslücke gefunden hast, melde sie bitte **nicht öffentlich**
als Issue, sondern privat per E-Mail an:

**bennoschwede@gmail.com**

Bitte enthalte:

- Reproduktionsschritte oder Proof-of-Concept
- betroffene Datei(en) / Pfad(e)
- mögliche Auswirkung (RCE, Datenleak, DoS, etc.)
- Vorschlag zur Behebung (optional)

Ich antworte in der Regel innerhalb von 7 Tagen.

## Threat Model

Der Bot läuft als **Single-User-Tool** auf einem Raspberry Pi.

### Vertrauensgrenze

- **Vertrauenswürdig:** Telegram-User in `ALLOWED_USER_IDS` (Whitelist),
  der Bot-Owner-Account.
- **Untrauenswürdig:** alle externen API-Antworten (OpenRouter / Grok / Tavily /
  Brave / GNews / Whisper-Transkript), eingehende Telegram-Nachrichten **bevor**
  sie den Injection-Guard passiert haben.

### Mitigationen (Stand: Mai 2026)

| Risiko | Mitigation | Datei |
|--------|-----------|-------|
| Prompt-Injection (Text + Voice) | 2-Stage Guard: Regex + LLM-Classifier, mit Homoglyph-Normalisierung | [app/security/injection_guard.py](app/security/injection_guard.py) |
| Unauthorized Access | Telegram-User-ID-Whitelist via `@require_whitelist`/`@guarded` Decorator | [app/bot/whitelist.py](app/bot/whitelist.py) |
| Rate / Abuse | Sliding-Window Rate-Limit pro User | [app/security/rate_limiter.py](app/security/rate_limiter.py) |
| Chart-Code-Injection (LLM gibt Python aus) | LLM erzeugt nur JSON-Spec; fester Renderer mit Allowlist (`line`/`bar`/`scatter`) | [app/agents/chart_agent.py](app/agents/chart_agent.py) |
| Audit für `/claudex` (Claude mit `--dangerously-skip-permissions`) | Rotierender Audit-Log (10 MB, 5 Backups) | [app/services/claude_runner.py](app/services/claude_runner.py) |
| Replay-Attacks (doppelte Telegram-Updates) | Dedup über `update_id` in In-Memory deque + `drop_pending_updates=True` | [app/bot/whitelist.py](app/bot/whitelist.py) |

### Bewusst akzeptierte Risiken

- `/claudex` läuft mit `--dangerously-skip-permissions` und vollem Shell-Zugriff
  im Repo-Working-Directory. Geschützt nur durch die Whitelist. Wer auf der
  Whitelist steht, **kann** den Pi vollständig kompromittieren — das ist
  Design-Intention für den Bot-Owner-Use-Case.
- Secrets liegen als Plaintext in `.env` auf dem Pi. Schutz erfolgt auf
  Filesystem-Ebene (User-Permissions, `chmod 600`).

## Nicht im Scope

- Multi-User-Deployments mit gegenseitig misstrauischen Whitelist-Mitgliedern
- Schutz vor einem kompromittierten Telegram-Account des Bot-Owners
- DoS durch Bot-Owner selbst (z.B. unendliche `/claudex`-Loops)
