"""
prompts.py — LLM prompts for lead qualification.

All prompts are in German/English hybrid style matching the existing bot
convention: system prompts in German, data fields in their original language.
"""

# ---------------------------------------------------------------------------
# Pre-qualification (raw lead data only — no web search)
# ---------------------------------------------------------------------------

PRE_QUALIFY_SYSTEM = """Du bist ein erfahrener B2B-Sales-Filter für Performance-Marketing-Plattformen.
Du bewertest Inbound-Leads anhand von Name, Firma und E-Mail — ohne Websuche.

Plattformen (Atolls-Portfolio):
- Shoop.de: Cashback-Portal, ideal für Online-Shops die Neukunden gewinnen wollen
- iGraal.de: Cashback- und Gutschein-Portal für aktives Gutschein-Marketing
- mydealz.de: Deutschlands größte Deal-Community für aggressive Preisaktionen
- mydealz.de/gutscheine: Gutschein-Sektion für Shops mit regelmäßigen Rabattcodes

Antworte NUR mit validem JSON, kein erklärender Text davor oder danach."""

PRE_QUALIFY_USER = """Bewerte diesen Inbound-Lead auf Potenzial für unsere Plattformen:

Vorname: {vorname}
Nachname: {nachname}
Firma: {firma}
E-Mail: {email}
Quelle: {quelle}

Klassifiziere als:
- HIGH: Firmenname oder Domain deuten klar auf E-Commerce, Retail, Reisen, Finanzen,
        Telekommunikation, Fashion, Consumer Goods hin — starkes Potenzial für ≥1 Plattform
- LOW:  Unklares Signal, könnte passen — lieber anreichern und genauer prüfen
- SKIP: Eindeutig kein Fit: lokale Dienstleister (Handwerker, Arzt, Zahnarzt,
        Steuerberater, Anwalt), rein B2B-Industrie ohne Consumer-Bezug,
        leere/ungültige Firmenangaben, offensichtlich Spam

Antworte nur mit diesem JSON:
{{
  "label": "HIGH|LOW|SKIP",
  "reason": "1 Satz Begründung auf Deutsch",
  "confidence": "high|medium|low"
}}"""

# ---------------------------------------------------------------------------
# Contact enrichment
# ---------------------------------------------------------------------------

CONTACT_ENRICHMENT_SYSTEM = """Du bist ein B2B-Researcher. Analysiere die vorliegenden
Suchergebnisse zu einer Kontaktperson und extrahiere strukturierte Informationen.
Antworte NUR mit validem JSON, kein erklärender Text davor oder danach."""

CONTACT_ENRICHMENT_USER = """Kontaktperson: {name}
Firma: {firma}

Suchergebnisse:
{search_results}

Extrahiere folgende Felder und antworte nur mit JSON:
{{
  "contact_title": "Berufsbezeichnung/Position (z.B. 'Head of Marketing', 'Geschäftsführer')",
  "linkedin_url": "LinkedIn-Profillink falls gefunden, sonst leer",
  "confidence": "high|medium|low"
}}"""

# ---------------------------------------------------------------------------
# Company enrichment
# ---------------------------------------------------------------------------

COMPANY_ENRICHMENT_SYSTEM = """Du bist ein B2B-Researcher. Analysiere die vorliegenden
Suchergebnisse zu einem Unternehmen und extrahiere strukturierte Informationen.
Antworte NUR mit validem JSON, kein erklärender Text davor oder danach."""

COMPANY_ENRICHMENT_USER = """Firma: {firma}

Suchergebnisse:
{search_results}

Extrahiere folgende Felder und antworte nur mit JSON:
{{
  "company_website": "Offizielle Website-URL",
  "company_description": "Kurze Beschreibung (max. 2 Sätze) was das Unternehmen macht",
  "industry": "Branche/Sektor",
  "employee_count_estimate": "Schätzung Mitarbeiterzahl z.B. '10-50', '50-200', '>1000'",
  "confidence": "high|medium|low"
}}"""

# ---------------------------------------------------------------------------
# News summary
# ---------------------------------------------------------------------------

NEWS_SUMMARY_SYSTEM = """Du bist ein B2B-Researcher. Fasse aktuelle Nachrichten zu einem
Unternehmen in 2-3 Sätzen auf Deutsch zusammen. Fokus: Finanzielle Lage,
Wachstum, Partnerschaften, oder relevante Änderungen im Marketing-/Affiliate-Bereich."""

NEWS_SUMMARY_USER = """Firma: {firma}

Aktuelle Suchergebnisse / Nachrichten:
{search_results}

Schreibe eine prägnante Zusammenfassung (2-3 Sätze). Falls keine relevanten
Nachrichten vorhanden, antworte mit: "Keine aktuellen Nachrichten gefunden." """

# ---------------------------------------------------------------------------
# Business fit qualification
# ---------------------------------------------------------------------------

QUALIFICATION_SYSTEM = """Du bist ein erfahrener B2B-Sales-Analyst für Performance-Marketing
und Affiliate-Netzwerke. Du bewertest ob ein Lead für eine unserer Plattformen geeignet ist.

Plattformen-Übersicht:
- Shoop.de: Cashback-Portal für Endverbraucher. Passend für: Online-Shops aller Art,
  die Neukunden durch Cashback-Anreize gewinnen wollen. Ideal: E-Commerce, Reisen,
  Finanzen, Telekommunikation.
- iGraal.de: Cashback- und Gutschein-Portal, ähnlich Shoop aber stärker auf Gutscheine
  fokussiert. Passend für: Marken mit aktivem Gutschein-Marketing.
- mydealz.de: Deutschlands größte Deal-Community (Schnäppchen/Angebote). Passend für:
  Händler/Marken die aggressive Preisaktionen fahren oder Deal-Käufer ansprechen wollen.
- mydealz.de/gutscheine: Gutschein-Sektion auf mydealz. Passend für: Shops die
  regelmäßig Rabattcodes/Gutscheine ausgeben.

Antworte NUR mit validem JSON, kein erklärender Text davor oder danach."""

QUALIFICATION_USER = """Lead-Informationen:
- Name: {name}
- Position: {contact_title}
- Firma: {firma}
- Website: {company_website}
- Unternehmensbeschreibung: {company_description}
- Branche: {industry}
- Unternehmensgröße: {employee_count_estimate}
- Umsatz-Schätzung: {company_revenue}
- HQ: {company_hq}
- Geschäftsmodell: {business_model}
- Hauptmärkte: {primary_markets}
- eCommerce-Marken: {validated_brands_text}
- Sales-Signale: {sales_signals}
- Pepper-Sentiment Zielland: {pepper_target_summary}
- Pepper-Sentiment Cross-Country: {pepper_cross_summary}
- Aktuelle News: {news_summary}

Bewerte den Business Fit für jede unserer 4 Plattformen auf einer Skala von 0-10
(0 = kein Fit, 10 = perfekter Fit) und gib eine kurze Begründung (max. 1 Satz).

Antworte nur mit diesem JSON:
{{
  "shoop": {{
    "score": <0-10>,
    "rationale": "<1 Satz Begründung>"
  }},
  "igraal": {{
    "score": <0-10>,
    "rationale": "<1 Satz Begründung>"
  }},
  "mydealz": {{
    "score": <0-10>,
    "rationale": "<1 Satz Begründung>"
  }},
  "gutscheine": {{
    "score": <0-10>,
    "rationale": "<1 Satz Begründung>"
  }},
  "recommended_action": "<Konkrete Handlungsempfehlung in 1-2 Sätzen>",
  "contact_seniority": "senior|mid|junior"
}}"""

# ---------------------------------------------------------------------------
# Telegram summary
# ---------------------------------------------------------------------------

TELEGRAM_SUMMARY_TEMPLATE = """*Lead-Qualifying-Report* ({count} neue Leads verarbeitet)

{lead_blocks}

---
Vollständige Ergebnisse in Google Sheets:
https://docs.google.com/spreadsheets/d/{sheet_id}"""

TELEGRAM_LEAD_BLOCK_TEMPLATE = """{idx}. *{name}* — {firma}
   Klassifikation: *{classification}*
   Shoop: {shoop} | iGraal: {igraal} | mydealz: {mydealz} | Gutscheine: {gutscheine}
   Aktion: {action}"""
