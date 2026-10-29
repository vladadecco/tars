"""Proiectele cu care pornește TARS la prima instalare (doar dacă nu există deja proiecte).
Se adaugă și se corectează din fereastră sau prin voce („TARS, proiect nou…”)."""

SEED_PROJECTS = [
    {
        "name": "TARS – asistent personal",
        "aliases": ["tars", "jarvis", "asistent"],
        "domain": "Asistent vocal personal în română pe PC",
        "objective": "Briefing zilnic, taskuri, proiecte și cercetare prin voce",
        "audience": "Uz personal",
        "resources": "PC Windows, Claude API, recunoaștere vocală locală",
        "stage": "Prototip",
        "progress": 40,
        "next": "Instalat și testat pe PC",
    },
]
