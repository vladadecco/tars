"""Analize strategice și cercetare, rulate în fundal cu Claude + căutare pe web."""
import json
import re
import threading
import uuid

from .store import now

CONSULTANT_PROMPT = """Acționează ca un Consultant Strategic de Elită, Data Scientist și Inovator Disruptiv. \
Scopul tău este să analizezi proiectul meu și să dezvolți o strategie complet inedită, unică pe piață, \
căutând proactiv oportunități și avantaje competitive masive pentru noi.

Iată detaliile proiectului:
- Nume/Domeniu proiect: {name} – {domain}
- Obiectivul principal: {objective}
- Publicul țintă: {audience}
- Resurse/Tehnologii actuale: {resources}
- Stadiu actual: {stage} ({progress}%), pasul următor: {next}
- Note recente: {notes}
{focus}
Cerințe pentru livrabil (răspunde structurat, ton analitic, vizionar și persuasiv):

## 1. Conceptul inedit și unic (The Unfair Advantage)
- 3 abordări sau funcționalități disruptive, pe care concurența nu le folosește. Inovație radicală, nu îmbunătățiri incrementale.
- Pentru fiecare: avantajul strategic concret (financiar, operațional sau de marketing).

## 2. Promovare și oportunități
- Parteneriate strategice neevidente (cross-industry) care ar multiplica impactul.
- Simulare a pieței actuale: nișe neexploatate / „oceane albastre” din domeniu.

## 3. Research și insight-uri bazate pe date
- Tendințe macro și micro relevante pentru 2026/2027 în acest sector.
- Riscuri ascunse (reglementări, schimbări de comportament) și cum le transformăm în oportunități.

## 4. Plan de acțiune pe 30 de zile, în micro-pași
- Spart în micro-pași (fiecare de 15–60 de minute), ordonați, făcuți de o singură persoană cu resursele de mai sus,
  care are și un job salariat. Primul pas trebuie să poată fi făcut azi, în cinci minute.

Reguli:
- Scrie ca un coleg de echipă capabil, de la egal la egal, nu ca un profesor. Fără ton de lecție.
- Format ușor de scanat: fără introducere sau concluzie lungă, direct la subiect. Propoziții scurte, liste,
  **bold** pe cuvintele-cheie, spațiu liber între blocuri, un emoji la începutul fiecărui punct important ca ancoră vizuală.
  Nimic din ce ar fi un bloc mare de text.
- Folosește căutarea pe web pentru cifre, concurenți și reglementări reale (România / UE unde contează).
- Nu inventa cifre. Orice număr vine dintr-o sursă găsită sau e marcat clar „(estimare)” cu raționamentul în paranteză.
- Fii extrem de specific, evită clișeele de business; tactici brute, direct aplicabile.
- Scrie în română, în Markdown (titluri ##, liste, tabele unde ajută).

La final, adaugă OBLIGATORIU un bloc ```json cu datele pentru graficele din fereastră, exact în forma:
{{"pozitionare": [{{"nume": "Noi", "cota_piata": 2, "diferentiere": 9}}, {{"nume": "Concurent A", "cota_piata": 35, "diferentiere": 4}}],
 "proiectie": [{{"an": 2026, "valoare": 0, "unitate": "…", "eticheta": "…"}}, …],
 "titlu_proiectie": "…", "estimari": true}}
- "pozitionare": 3–7 jucători reali din piață + „Noi”; cota_piata în % (estimată dacă nu există date), diferentiere 1–10.
- "proiectie": 4–6 ani consecutivi pentru dimensiunea oportunității (piață adresabilă sau venit posibil), cu unitatea (ex. „mil. €”, „clienți”, „lei/lună”).
- "estimari": true dacă oricare dintre valori e estimare.
"""

PROPOSALS_PROMPT = """Ești consultantul de încredere al lui {user}. Proiectul:
- {name} – {domain}
- Obiectiv: {objective}; public: {audience}; resurse: {resources}
- Stadiu: {stage} ({progress}%), pas următor: {next}
- Note: {notes}

Dă-i în română, în Markdown, scurt și concret:
Ton de coleg de echipă, de la egal la egal; fără lecții. Direct la subiect, propoziții scurte, liste,
**bold** pe cuvintele-cheie, un emoji la începutul fiecărui punct, spațiu între blocuri.
## Ce urmează (3 micro-pași, în ordine; primul se poate face azi în 5 minute)
## Posibilități de extindere (3–5 idei, fiecare cu de ce merită și primul pas)
## Ce ar putea bloca proiectul (2–3 riscuri + cum le ocolește)
Caută pe web doar dacă ai nevoie de o informație actuală. Nu inventa cifre."""

RESEARCH_PROMPT = """Fă o cercetare pe web pentru {user} pe tema de mai jos și scrie un raport în română, în Markdown ușor de scanat
(propoziții scurte, liste, **bold** pe cuvintele-cheie, un emoji la început de punct, fără blocuri mari de text):
un rezumat de 3–4 rânduri la început, apoi descoperirile pe secțiuni, cu cifre și nume concrete, apoi
„Ce înseamnă pentru proiectele lui” dacă e cazul. Nu inventa cifre; spune când informația lipsește.
Proiectele lui: {projects}

Tema: {query}"""


def _extract(resp):
    """Text + surse din răspunsul Claude."""
    parts, sources = [], []
    seen = set()
    for block in resp.content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"title": getattr(c, "title", "") or url, "url": url})
    return "".join(parts), sources


def _charts(text: str):
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        return text, None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return text, None
    return (text[:m.start()] + text[m.end():]).strip(), data


class Strategist:
    def __init__(self, cfg, store, get_client, notify):
        self.cfg = cfg
        self.store = store
        self.get_client = get_client
        self.notify = notify  # notify(text_de_spus)

    def _run_claude(self, prompt: str, max_tokens: int = 12000, searches: int = 10):
        client = self.get_client()
        model = self.cfg.get("analysis_model") or self.cfg["model"]
        tools = []
        if self.cfg["web_search"]:
            tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": searches,
                          "user_location": {"type": "approximate", "country": "RO",
                                            "timezone": "Europe/Bucharest", "city": self.cfg["city"]}})
        messages = [{"role": "user", "content": prompt}]
        text_all, sources_all = "", []
        for _ in range(6):
            kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
            if tools:
                kwargs["tools"] = tools
            resp = client.messages.create(**kwargs)
            text, src = _extract(resp)
            text_all += text
            sources_all += [s for s in src if s not in sources_all]
            if resp.stop_reason == "pause_turn":
                if messages[-1]["role"] == "assistant":
                    messages[-1] = {"role": "assistant", "content": list(messages[-1]["content"]) + list(resp.content)}
                else:
                    messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        return text_all.strip(), sources_all

    # ------------------------------------------------------------ proiecte
    def analyze_project(self, project: dict, kind: str = "strategie", focus: str = "") -> dict:
        aid = uuid.uuid4().hex[:6]
        analysis = {"id": aid, "kind": kind, "ts": now().isoformat(timespec="seconds"),
                    "status": "lucrez", "text": "", "charts": None, "sources": [], "focus": focus}
        self.store.add_analysis(project["id"], analysis)
        label = "Analiza strategică" if kind == "strategie" else "Propunerile"
        self.store.log(f"{label} pentru „{project['name']}” a pornit", "research")

        def work():
            try:
                notes = "; ".join(n["text"] for n in project.get("notes", [])[-5:]) or "-"
                fields = dict(project, notes=notes, user=self.cfg["user_name"],
                              focus=(f"- Accent cerut: {focus}\n" if focus else ""))
                if kind == "strategie":
                    text, sources = self._run_claude(CONSULTANT_PROMPT.format(**fields), 16000, 12)
                    text, charts = _charts(text)
                else:
                    text, sources = self._run_claude(PROPOSALS_PROMPT.format(**fields), 4000, 4)
                    charts = None
                analysis.update(status="gata", text=text, charts=charts, sources=sources)
                self.store.add_analysis(project["id"], analysis)
                self.store.log(f"{label} pentru „{project['name']}” e gata", "done")
                self.notify(f"{label} pentru {project['name']} e gata. O găsești la Proiecte.")
            except Exception as e:
                analysis.update(status="eroare", text=f"Nu a mers: {e}")
                self.store.add_analysis(project["id"], analysis)
                self.store.log(f"{label} pentru „{project['name']}” a eșuat: {e}", "error")

        threading.Thread(target=work, daemon=True).start()
        return analysis

    # ------------------------------------------------------------ cercetare
    def research(self, query: str) -> dict:
        item = {"id": uuid.uuid4().hex[:6], "ts": now().isoformat(timespec="seconds"),
                "query": query, "status": "lucrez", "text": "", "sources": []}
        self.store.upsert_research(item)
        self.store.log(f"Cercetare pornită: {query}", "research")

        def work():
            try:
                projects = "; ".join(p["name"] for p in self.store.data["projects"])
                text, sources = self._run_claude(
                    RESEARCH_PROMPT.format(user=self.cfg["user_name"], projects=projects, query=query), 8000, 10)
                item.update(status="gata", text=text, sources=sources)
                self.store.upsert_research(item)
                self.store.log(f"Cercetarea „{query}” e gata", "done")
                self.notify(f"Cercetarea despre {query} e gata.")
            except Exception as e:
                item.update(status="eroare", text=f"Nu a mers: {e}")
                self.store.upsert_research(item)
                self.store.log(f"Cercetarea „{query}” a eșuat: {e}", "error")

        threading.Thread(target=work, daemon=True).start()
        return item
