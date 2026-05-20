"""
country_mapping.py — Mappt Freitext-Länderangaben aus Sheet-Spalte H auf Pepper-ISO-Codes.

Pepper Intelligence kennt nur diese 11 Country-Codes (lowercase):
    uk, de, fr, nl, pl, es, mx, us, at, be, se

Sheet enthält Freitext: "Poland", "Germany", "DE", "Deutschland", "United Kingdom",
"USA", etc. — alles wird normalisiert.
"""
from __future__ import annotations

# Pepper-unterstützte ISO-Codes
PEPPER_COUNTRIES: tuple[str, ...] = ("uk", "de", "fr", "nl", "pl", "es", "mx", "us", "at", "be", "se")


# Alle bekannten Aliase → ISO-Code. Lowercase-Matching.
_COUNTRY_ALIASES: dict[str, str] = {
    # United Kingdom
    "uk": "uk", "gb": "uk",
    "united kingdom": "uk", "great britain": "uk", "england": "uk",
    "britain": "uk", "vereinigtes königreich": "uk", "großbritannien": "uk",
    # Germany
    "de": "de",
    "germany": "de", "deutschland": "de", "ger": "de",
    # France
    "fr": "fr",
    "france": "fr", "frankreich": "fr",
    # Netherlands
    "nl": "nl",
    "netherlands": "nl", "holland": "nl", "niederlande": "nl",
    "the netherlands": "nl",
    # Poland
    "pl": "pl",
    "poland": "pl", "polska": "pl", "polen": "pl",
    # Spain
    "es": "es",
    "spain": "es", "españa": "es", "espana": "es", "spanien": "es",
    # Mexico
    "mx": "mx",
    "mexico": "mx", "méxico": "mx", "mexiko": "mx",
    # United States
    "us": "us", "usa": "us",
    "united states": "us", "united states of america": "us",
    "vereinigte staaten": "us", "america": "us",
    # Austria
    "at": "at",
    "austria": "at", "österreich": "at", "oesterreich": "at",
    # Belgium
    "be": "be",
    "belgium": "be", "belgië": "be", "belgique": "be", "belgien": "be",
    # Sweden
    "se": "se",
    "sweden": "se", "sverige": "se", "schweden": "se",
}


def to_pepper_code(country_text: str) -> str | None:
    """Mappt Freitext-Land auf einen Pepper-ISO-Code oder None wenn nicht unterstützt.

    Beispiele:
      "Poland"        → "pl"
      "Deutschland"   → "de"
      "USA"           → "us"
      "Japan"         → None  (kein Pepper-Markt)
    """
    if not country_text:
        return None
    key = country_text.strip().lower()
    # Komma-/Slash-getrennte Listen: ersten Eintrag nehmen
    for sep in (",", "/", "&", "|", ";"):
        if sep in key:
            key = key.split(sep, 1)[0].strip()
            break
    return _COUNTRY_ALIASES.get(key)


def other_pepper_countries(target: str | None) -> tuple[str, ...]:
    """Alle Pepper-Länder außer dem Zielland."""
    if not target:
        return PEPPER_COUNTRIES
    return tuple(c for c in PEPPER_COUNTRIES if c != target)
