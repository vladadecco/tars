"""Uneltele pe care Claude le poate folosi. Nimic nu rulează comenzi arbitrare pe PC."""
import json
import os
import subprocess
import urllib.parse
import webbrowser
from datetime import datetime, timedelta

from . import sources
from .store import now, today_str, TZ

ZILE = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]

SCHEMAS = [
    {
        "name": "briefing_noapte",
        "description": "Strânge tot ce s-a întâmplat de aseară încoace: mailuri noi, programul de azi din calendar, "
                       "taskurile de azi și cele restante, vremea, știrile importante, ce a făcut Jarvis. "
                       "Folosește-o când utilizatorul întreabă ce s-a întâmplat peste noapte, cere briefing-ul sau zice bună dimineața.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "vreme",
        "description": "Vremea acum și prognoza pe următoarele zile pentru orașul utilizatorului (sau alt oraș dat).",
        "input_schema": {"type": "object", "properties": {
            "zile": {"type": "integer", "description": "Câte zile de prognoză (1-7)", "default": 2},
            "oras": {"type": "string", "description": "Alt oraș decât cel implicit (opțional)"},
        }},
    },
    {
        "name": "mailuri",
        "description": "Mailurile primite în ultimele N ore (doar citire).",
        "input_schema": {"type": "object", "properties": {
            "ore": {"type": "integer", "default": 12},
        }},
    },
    {
        "name": "calendar",
        "description": "Evenimentele din calendarul Google pe o perioadă (doar citire).",
        "input_schema": {"type": "object", "properties": {
            "de_la": {"type": "string", "description": "Data de început YYYY-MM-DD (implicit azi)"},
            "zile": {"type": "integer", "description": "Câte zile", "default": 1},
        }},
    },
    {
        "name": "stiri",
        "description": "Titlurile de știri recente din România, opțional pe subiecte.",
        "input_schema": {"type": "object", "properties": {
            "subiecte": {"type": "string", "description": "Subiecte separate prin virgulă (opțional)"},
        }},
    },
    {
        "name": "lista_taskuri",
        "description": "Taskurile dintr-o zi (implicit azi), plus cele restante și cele din zilele următoare.",
        "input_schema": {"type": "object", "properties": {
            "data": {"type": "string", "description": "YYYY-MM-DD"},
        }},
    },
    {
        "name": "adauga_task",
        "description": "Adaugă un task/lucru de făcut. Dacă are oră, TARS îl anunță la ora respectivă (memento).",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string"},
            "data": {"type": "string", "description": "YYYY-MM-DD, implicit azi"},
            "ora": {"type": "string", "description": "HH:MM, opțional"},
        }, "required": ["text"]},
    },
    {
        "name": "bifeaza_task",
        "description": "Marchează un task ca făcut (sau nefăcut). Referința poate fi id-ul sau o bucată din text.",
        "input_schema": {"type": "object", "properties": {
            "task": {"type": "string"},
            "facut": {"type": "boolean", "default": True},
        }, "required": ["task"]},
    },
    {
        "name": "modifica_task",
        "description": "Schimbă textul, data sau ora unui task.",
        "input_schema": {"type": "object", "properties": {
            "task": {"type": "string"},
            "text_nou": {"type": "string"},
            "data_noua": {"type": "string"},
            "ora_noua": {"type": "string", "description": "HH:MM sau gol pentru a scoate ora"},
        }, "required": ["task"]},
    },
    {
        "name": "sterge_task",
        "description": "Șterge un task.",
        "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
    },
    {
        "name": "cronometru",
        "description": "Pune un memento peste N minute (ex: 'amintește-mi în 20 de minute să scot piesa din imprimantă').",
        "input_schema": {"type": "object", "properties": {
            "minute": {"type": "number"},
            "ce": {"type": "string"},
        }, "required": ["minute", "ce"]},
    },
    {
        "name": "tine_minte",
        "description": "Salvează permanent ceva ce utilizatorul vrea să ții minte (preferințe, informații).",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "uita",
        "description": "Șterge o informație ținută minte.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "lista_proiecte",
        "description": "Proiectele utilizatorului cu stadiul, progresul și pasul următor.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "actualizeaza_proiect",
        "description": "Actualizează un proiect: stadiu, progres în procente, pasul următor, sau adaugă o notă despre ce s-a făcut.",
        "input_schema": {"type": "object", "properties": {
            "proiect": {"type": "string", "description": "Numele sau o parte din nume"},
            "stadiu": {"type": "string", "description": "ex: Idee, Proiectare, În dezvoltare, Test, Lansat, Pe pauză"},
            "progres": {"type": "integer", "description": "0-100"},
            "pas_urmator": {"type": "string"},
            "nota": {"type": "string", "description": "Ce s-a mai făcut / informație nouă"},
            "obiectiv": {"type": "string"},
            "public": {"type": "string"},
            "resurse": {"type": "string"},
        }, "required": ["proiect"]},
    },
    {
        "name": "adauga_proiect",
        "description": "Adaugă un proiect nou.",
        "input_schema": {"type": "object", "properties": {
            "nume": {"type": "string"}, "domeniu": {"type": "string"}, "obiectiv": {"type": "string"},
            "public": {"type": "string"}, "resurse": {"type": "string"}, "stadiu": {"type": "string"},
        }, "required": ["nume"]},
    },
    {
        "name": "analiza_strategica",
        "description": "Pornește în fundal o analiză strategică completă a unui proiect (concept disruptiv, parteneriate, "
                       "oceane albastre, tendințe 2026/2027, riscuri, grafice de poziționare și proiecție). Durează câteva minute; "
                       "rezultatul apare în fereastră la Proiecte și TARS anunță când e gata.",
        "input_schema": {"type": "object", "properties": {
            "proiect": {"type": "string"},
            "accent": {"type": "string", "description": "Pe ce să se concentreze (opțional)"},
        }, "required": ["proiect"]},
    },
    {
        "name": "propuneri_proiect",
        "description": "Pornește în fundal propuneri rapide pentru un proiect: pașii următori, posibilități de extindere, riscuri.",
        "input_schema": {"type": "object", "properties": {"proiect": {"type": "string"}}, "required": ["proiect"]},
    },
    {
        "name": "cercetare",
        "description": "Pornește în fundal o cercetare amănunțită pe web pe o temă; raportul apare la Cercetare în fereastră. "
                       "Pentru întrebări scurte folosește direct căutarea pe web, nu asta.",
        "input_schema": {"type": "object", "properties": {"tema": {"type": "string"}}, "required": ["tema"]},
    },
    {
        "name": "setare_umor",
        "description": "Schimbă setarea de umor a lui TARS (0-100%), ca în Interstellar.",
        "input_schema": {"type": "object", "properties": {"procent": {"type": "integer"}}, "required": ["procent"]},
    },
    {
        "name": "deschide_site",
        "description": "Deschide o pagină web în browser, sau o căutare Google dacă nu e dat un link.",
        "input_schema": {"type": "object", "properties": {
            "url": {"type": "string"},
            "cautare": {"type": "string"},
        }},
    },
    {
        "name": "deschide_aplicatie",
        "description": "Pornește o aplicație de pe PC din lista permisă.",
        "input_schema": {"type": "object", "properties": {"nume": {"type": "string"}}, "required": ["nume"]},
    },
]


class Tools:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def schemas(self):
        out = [dict(s) for s in SCHEMAS]
        apps = ", ".join(self.cfg["apps"].keys())
        for s in out:
            if s["name"] == "deschide_aplicatie":
                s["description"] = s["description"] + f" Aplicații permise: {apps}."
        return out

    # ------------------------------------------------------------
    def run(self, name: str, args: dict) -> str:
        fn = getattr(self, "t_" + name, None)
        if not fn:
            return f"Eroare: unealtă necunoscută {name}"
        try:
            result = fn(**(args or {}))
        except TypeError as e:
            return f"Eroare la parametri: {e}"
        except Exception as e:
            return f"Eroare: {type(e).__name__}: {e}"
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    # ------------------------------------------------------------ helpers
    def night_start(self) -> datetime:
        """Începutul 'nopții': ultima oară când a fost pornit TARS seara, sau ora din setări de ieri."""
        n = now()
        h = int(self.cfg["night_start_hour"])
        start = n.replace(hour=h, minute=0, second=0, microsecond=0)
        if start > n:
            start -= timedelta(days=1)
        return start

    def _coords(self, oras=None):
        if oras:
            g = sources.geocode(oras)
            if g:
                return g[0], g[1], g[2]
        return self.cfg["latitude"], self.cfg["longitude"], self.cfg["city"]

    # ------------------------------------------------------------ unelte
    def t_briefing_noapte(self):
        since = self.night_start()
        n = now()
        out = {"de_la": since.strftime("%d.%m %H:%M"), "acum": n.strftime("%A %d.%m %H:%M")}
        try:
            lat, lon, city = self._coords()
            out["vreme"] = {"oras": city, **sources.weather(lat, lon, 2)}
        except Exception as e:
            out["vreme"] = f"indisponibilă: {e}"
        try:
            out["mailuri_noi"] = sources.mail_since(self.cfg["gmail_address"], self.cfg["gmail_app_password"], since)
        except Exception as e:
            out["mailuri_noi"] = f"indisponibil: {e}"
        try:
            start = n.replace(hour=0, minute=0, second=0, microsecond=0)
            out["calendar_azi_maine"] = sources.calendar_events(self.cfg["calendar_ics_url"], start, start + timedelta(days=2)) \
                if self.cfg["calendar_ics_url"] else "calendarul nu e configurat"
        except Exception as e:
            out["calendar_azi_maine"] = f"indisponibil: {e}"
        try:
            out["stiri"] = sources.news(self.cfg["news_topics"], per_topic=3, since=since - timedelta(hours=3))
        except Exception as e:
            out["stiri"] = f"indisponibile: {e}"
        out["taskuri_azi"] = self.store.tasks_for(today_str())
        out["taskuri_restante"] = self.store.overdue()
        out["ce_a_facut_tars"] = [e["text"] for e in self.store.log_since(since)][-20:]
        out["proiecte"] = [{"nume": p["name"], "stadiu": p["stage"], "progres": p["progress"], "pas_urmator": p["next"]}
                           for p in self.store.data["projects"]]
        return out

    def t_vreme(self, zile=2, oras=None):
        lat, lon, city = self._coords(oras)
        return {"oras": city, **sources.weather(lat, lon, max(1, min(int(zile), 7)))}

    def t_mailuri(self, ore=12):
        return sources.mail_since(self.cfg["gmail_address"], self.cfg["gmail_app_password"],
                                  now() - timedelta(hours=int(ore)))

    def t_calendar(self, de_la=None, zile=1):
        if not self.cfg["calendar_ics_url"]:
            return "Calendarul nu e configurat. Se pune adresa secretă iCal în Setări."
        start = datetime.fromisoformat(de_la).replace(tzinfo=TZ) if de_la else now().replace(hour=0, minute=0, second=0, microsecond=0)
        return sources.calendar_events(self.cfg["calendar_ics_url"], start, start + timedelta(days=max(1, int(zile))))

    def t_stiri(self, subiecte=None):
        return sources.news(subiecte if subiecte else self.cfg["news_topics"], per_topic=4,
                            since=now() - timedelta(hours=24))

    def t_lista_taskuri(self, data=None):
        day = data or today_str()
        return {"zi": day, "taskuri": self.store.tasks_for(day),
                "restante": self.store.overdue(), "urmatoarele_zile": self.store.upcoming(7)}

    def t_adauga_task(self, text, data=None, ora=None):
        t = self.store.add_task(text, data, ora or "")
        when = t["date"] + (f" la {t['time']}" if t["time"] else "")
        self.store.log(f"Am adăugat taskul „{t['text']}” ({when})", "task")
        return {"ok": True, "task": t}

    def t_bifeaza_task(self, task, facut=True):
        t = self.store.set_done(task, bool(facut))
        if not t:
            return "Nu am găsit taskul. Cere lista ca să vezi ce există."
        self.store.log(("Bifat ca făcut: " if facut else "Redeschis: ") + f"„{t['text']}”", "done" if facut else "task")
        return {"ok": True, "task": t}

    def t_modifica_task(self, task, text_nou=None, data_noua=None, ora_noua=None):
        t = self.store.update_task(task, text_nou, data_noua, ora_noua)
        if not t:
            return "Nu am găsit taskul."
        self.store.log(f"Am modificat taskul „{t['text']}”", "task")
        return {"ok": True, "task": t}

    def t_sterge_task(self, task):
        t = self.store.delete_task(task)
        if not t:
            return "Nu am găsit taskul."
        self.store.log(f"Am șters taskul „{t['text']}”", "task")
        return {"ok": True}

    def t_cronometru(self, minute, ce):
        when = now() + timedelta(minutes=float(minute))
        t = self.store.add_task(ce, when.date().isoformat(), when.strftime("%H:%M"), source="cronometru")
        self.store.log(f"Memento la {t['time']}: {ce}", "task")
        return {"ok": True, "ora": t["time"]}

    def t_tine_minte(self, text):
        self.store.add_note(text)
        self.store.log(f"Țin minte: {text}", "info")
        return {"ok": True}

    def t_uita(self, text):
        ok = self.store.remove_note(text)
        return {"ok": ok}

    def _project_brief(self, p):
        return {k: p.get(k) for k in ("id", "name", "domain", "objective", "audience", "resources",
                                      "stage", "progress", "next", "updated")} | {
            "note_recente": [n["text"] for n in p.get("notes", [])[-3:]],
            "analize": [{"tip": a["kind"], "data": a["ts"][:10], "stare": a["status"]} for a in p.get("analyses", [])],
        }

    def t_lista_proiecte(self):
        return [self._project_brief(p) for p in self.store.data["projects"]]

    def t_actualizeaza_proiect(self, proiect, stadiu=None, progres=None, pas_urmator=None, nota=None,
                               obiectiv=None, public=None, resurse=None):
        fields = {"stage": stadiu, "next": pas_urmator, "note": nota, "objective": obiectiv,
                  "audience": public, "resources": resurse}
        if progres is not None:
            fields["progress"] = max(0, min(100, int(progres)))
        p = self.store.update_project(proiect, **fields)
        if not p:
            return "Nu am găsit proiectul. Proiecte: " + ", ".join(x["name"] for x in self.store.data["projects"])
        what = ", ".join(x for x in [stadiu and f"stadiu {stadiu}", progres is not None and f"{progres}%",
                                     pas_urmator and "pas următor nou", nota and "notă nouă"] if x)
        self.store.log(f"Proiect „{p['name']}” actualizat ({what})", "project")
        return {"ok": True, "proiect": self._project_brief(p)}

    def t_adauga_proiect(self, nume, domeniu=None, obiectiv=None, public=None, resurse=None, stadiu=None):
        p = self.store.add_project(name=nume, domain=domeniu, objective=obiectiv, audience=public,
                                   resources=resurse, stage=stadiu or "Idee")
        self.store.log(f"Proiect nou: „{nume}”", "project")
        return {"ok": True, "id": p["id"]}

    def t_analiza_strategica(self, proiect, accent=None):
        p = self.store.find_project(proiect)
        if not p:
            return "Nu am găsit proiectul."
        self.strategist.analyze_project(p, "strategie", accent or "")
        return {"ok": True, "pornit": True, "proiect": p["name"], "durata": "câteva minute"}

    def t_propuneri_proiect(self, proiect):
        p = self.store.find_project(proiect)
        if not p:
            return "Nu am găsit proiectul."
        self.strategist.analyze_project(p, "propuneri")
        return {"ok": True, "pornit": True, "proiect": p["name"], "durata": "un minut"}

    def t_cercetare(self, tema):
        self.strategist.research(tema)
        return {"ok": True, "pornit": True, "durata": "1-3 minute"}

    def t_setare_umor(self, procent):
        v = max(0, min(100, int(procent)))
        self.cfg.update({"humor": v})
        self.store.log(f"Setarea de umor: {v}%", "info")
        return {"ok": True, "umor": v}

    def t_deschide_site(self, url=None, cautare=None):
        if url:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
        elif cautare:
            url = "https://www.google.com/search?q=" + urllib.parse.quote(cautare)
        else:
            return "Trebuie un link sau o căutare."
        webbrowser.open(url)
        self.store.log(f"Am deschis {url}", "action")
        return {"ok": True}

    def t_deschide_aplicatie(self, nume):
        apps = {k.lower(): v for k, v in self.cfg["apps"].items()}
        cmd = apps.get(nume.lower().strip())
        if not cmd:
            return f"Aplicația nu e în lista permisă. Permise: {', '.join(apps)}. Se pot adăuga din config.json."
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", cmd], creationflags=0x08000000)
        else:
            subprocess.Popen([cmd])
        self.store.log(f"Am pornit {nume}", "action")
        return {"ok": True}
