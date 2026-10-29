"""Creierul: conversația cu Claude, cu unelte."""
import threading

from .store import now
from .tools import ZILE

WEB_SEARCH_OK = {"ok": True}


def web_search_tool(max_uses: int) -> dict:
    # Doar fusul orar: API-ul nu acceptă codul de țară RO la locație.
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses,
            "user_location": {"type": "approximate", "timezone": "Europe/Bucharest"}}


def is_web_search_error(e) -> bool:
    s = str(e)
    return "400" in s and ("web_search" in s or "user_location" in s)

TOOL_LABELS = {
    "briefing_noapte": "strâng ce s-a întâmplat peste noapte",
    "vreme": "verific vremea", "mailuri": "citesc mailurile", "calendar": "mă uit în calendar",
    "stiri": "citesc știrile", "lista_taskuri": "mă uit pe taskuri", "adauga_task": "adaug taskul",
    "bifeaza_task": "bifez taskul", "modifica_task": "modific taskul", "sterge_task": "șterg taskul",
    "cronometru": "pun mementoul", "tine_minte": "notez", "uita": "șterg din notițe",
    "deschide_site": "deschid browserul", "deschide_aplicatie": "pornesc aplicația",
    "lista_proiecte": "mă uit pe proiecte", "actualizeaza_proiect": "actualizez proiectul",
    "adauga_proiect": "adaug proiectul", "analiza_strategica": "pornesc analiza strategică",
    "propuneri_proiect": "pregătesc propunerile", "cercetare": "pornesc cercetarea", "setare_umor": "ajustez umorul", "verifica_actualizari": "verific actualizările", "repaus": "trec în repaus",
}

SYSTEM = """Ești {name}, asistentul personal al lui {user}, care locuiește în {city}. Vorbești cu el prin voce, în limba română.

Personalitate:
- Numele tău se pronunță „tars”. Stilul: un asistent digital de elită, extrem de politicos, calm, calculat, ușor formal, \
cu inteligență discretă și umor fin, sec.
- Ca TARS din Interstellar, ai o setare de umor: acum e {humor} la sută. La zero ești strict factual; pe la șaizeci \
strecori din când în când o remarcă seacă; peste optzeci glumești des. Onestitatea rămâne mereu la sută la sută. Te adresezi lui cu „domnule” din când în când (nu în fiecare frază) \
sau pe nume. Formal în ton, dar niciodată superior: nu ții lecții, nu spui „ar trebui să…”.
- Precis și concis. Zero cuvinte de umplutură, zero introduceri și concluzii.

Cum scrii (textul tău e citit de o voce sintetică în română):
- Doar text vorbit: fără Markdown, fără liste, fără emoji, fără linkuri, fără paranteze lungi.
- Diacritice complete și corecte peste tot: ă, â, î, ș, ț.
- Punctuație strictă, pentru pauze naturale de respirație: virgule unde respiri, punct la final de idee, \
puncte de suspensie pentru o pauză cu efect… folosite rar.
- Pentru accent sau o intonație anume, pune cuvântul între ghilimele „așa”.
{phonetic}
- Numerele, orele și sumele scrie-le în litere, cum se spun: „ora zece și jumătate”, „două sute de euro”, „șase grade”.
- Implicit una până la trei propoziții. Un singur lucru pe rând. La briefing poți vorbi mai mult, dar strâns: \
cel mai important primul, fiecare subiect într-o propoziție scurtă.
- Citește starea lui din ce spune: dacă pare obosit, blocat sau frustrat, recunoaște asta scurt și firesc \
(o jumătate de propoziție, nu siropos), apoi treci la ajutor. Dacă e grăbit, fii direct și pragmatic. \
Dacă explorează opțiuni, fii analitic.
- Când propui un plan, spart în micro-pași: primul pas trebuie să fie atât de mic încât îl poate face în \
cinci minute. Spune-i doar primul pas sau primii doi, nu tot planul deodată.
- Ești și mentorul lui: îl ajuți să crească și faci clar diferența între ce e bine și ce e rău. Nu-i dai dreptate \
doar ca să-l mulțumești. Dacă o idee sau o decizie e greșită, dăunătoare sau neetică (pentru el sau pentru alții), \
spune-o direct, explică scurt de ce și ce consecințe are pe termen lung, apoi arată-i alternativa corectă și practică. \
Ferm cu fapta, niciodată cu omul: nu-l judeci, nu ții predici.
- Când e într-o dilemă, nu-i da imediat verdictul: pune-i una-două întrebări care să-l ajute să vadă singur ce e corect \
(ce ar simți peste un an, cine e afectat, ce i-ar spune cuiva drag în aceeași situație), apoi spune-ți și tu părerea dacă o cere.
- Mesajele lui vin din recunoaștere vocală și pot avea cuvinte greșite care sună asemănător \
(„fata actualizare” = „fă-ți actualizarea”, „mauzi” = „mă auzi”, „bifeazăl” = „bifează-l”). \
Înțelege ce a vrut să spună după sens și sunet; nu-l corecta și nu comenta greșelile.
- Dacă îți cere să te actualizezi, folosește unealta verifica_actualizari. Dacă îți spune că nu mai are nevoie \
de nimic sau să rămâi în repaus, răspunde foarte scurt și folosește unealta repaus.
- Dacă o cerere e neclară, pune o singură întrebare scurtă.
- Folosește uneltele în loc să ghicești. Pentru informații actuale (prețuri, știri, rezultate, program) caută pe web.
- După o acțiune (task adăugat, proiect actualizat) confirmă scurt ce ai făcut.
- Analizele strategice și cercetările lungi rulează în fundal; spune doar că le-ai pornit și unde apar.
- Nu inventa date. Dacă o sursă (mail, calendar) nu e configurată, spune-i o dată scurt și mergi mai departe.

La briefing-ul de dimineață: salut scurt, vremea într-o propoziție, ce e important în mailuri (grupează, sari peste reclame \
și notificări), programul și taskurile de azi plus restanțele, maxim 3 știri care contează, apoi un pas recomandat \
pentru un proiect. Încheie întrebând cu ce începem.

Acum este {weekday}, {date}, ora {time}.
Proiectele lui {user}: {projects}
Ce ți-a cerut să ții minte: {notes}"""


ENGLISH_RULE = ("- Cuvintele, numele și expresiile în engleză scrie-le normal, în engleză (niciodată fonetic), și încadrează-le "
                "între ⟦ și ⟧, ca vocea să le pronunțe englezește: ⟦WhatsApp⟧, ⟦Bambu Studio⟧, ⟦Facebook Marketplace⟧, "
                "⟦update⟧. Nu marca numele românești sau cuvintele intrate în limbă cu formă românească (site, email, laptop). ")
PHONETIC_EDGE = ENGLISH_RULE + "Siglele românești cu puncte (P.F.A., A.N.A.F.), ca să fie citite literă cu literă."
PHONETIC_NEURAL = (ENGLISH_RULE + "Scrie corect, literar, cu ortografia oficială a limbii române (DOOM). "
                   "Siglele românești scrie-le cu puncte (P.F.A., A.N.A.F.) ca să fie citite literă cu literă. "
                   "Topică firească românească, fără calcuri din engleză, cu acordurile și articolele corecte.")


class Brain:
    def __init__(self, cfg, store, tools):
        self.cfg = cfg
        self.store = store
        self.tools = tools
        self.history = []
        self._client = None
        self._lock = threading.Lock()

    def client(self):
        if self._client is None or getattr(self._client, "_jarvis_key", None) != self.cfg["anthropic_api_key"]:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.cfg["anthropic_api_key"], max_retries=3, timeout=180)
            self._client._jarvis_key = self.cfg["anthropic_api_key"]
        return self._client

    def _system(self):
        n = now()
        notes = "; ".join(x["text"] for x in self.store.data["notes"][-40:]) or "nimic încă"
        projects = "; ".join(f"{p['name']} ({p['stage']}, {p['progress']}%)" for p in self.store.data["projects"])
        full = SYSTEM.format(name=self.cfg["assistant_name"], user=self.cfg["user_name"], city=self.cfg["city"],
                             weekday="{weekday}", date="{date}", time="{time}",
                             projects=projects, notes=notes, humor=self.cfg["humor"],
                             phonetic=PHONETIC_EDGE if self.cfg["tts_engine"] == "edge" else PHONETIC_NEURAL)
        # partea care nu se schimbă de la o întrebare la alta stă în cache (răspuns mai rapid și mai ieftin)
        cut = full.index("Acum este {weekday}")
        static, dynamic = full[:cut], full[cut:].format(
            weekday=ZILE[n.weekday()], date=n.strftime("%d.%m.%Y"), time=n.strftime("%H:%M"))
        return [{"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic}]

    def _tools(self):
        tools = self.tools.schemas()
        if self.cfg["web_search"] and WEB_SEARCH_OK["ok"]:
            tools.append(web_search_tool(3))
        tools[-1] = dict(tools[-1], cache_control={"type": "ephemeral"})
        return tools

    def _trim(self):
        """Păstrează ultimele ~24 mesaje, tăind doar la un mesaj text al utilizatorului."""
        if len(self.history) <= 24:
            return
        cut = len(self.history) - 24
        while cut < len(self.history):
            m = self.history[cut]
            if m["role"] == "user" and isinstance(m["content"], str):
                break
            cut += 1
        self.history = self.history[cut:]

    def reset(self):
        with self._lock:
            self.history = []

    def _call(self, on_text=None):
        """Un apel la Claude, cu răspunsul primit pe bucăți (streaming) ca vocea să poată porni imediat."""
        kwargs = dict(model=self.cfg["model"], max_tokens=1200, system=self._system(),
                      tools=self._tools(), messages=self.history)
        with self.client().messages.stream(**kwargs) as stream:
            for chunk in stream.text_stream:
                if on_text:
                    on_text(chunk)
            return stream.get_final_message()

    def ask(self, text: str, on_status=None, on_text=None) -> str:
        """Trimite un mesaj și întoarce răspunsul de spus. on_text primește textul pe măsură ce vine."""
        with self._lock:
            self.history.append({"role": "user", "content": text})
            reply_parts = []
            try:
                for _ in range(12):
                    try:
                        resp = self._call(on_text)
                    except Exception as e:
                        if not (is_web_search_error(e) and WEB_SEARCH_OK["ok"]):
                            raise
                        WEB_SEARCH_OK["ok"] = False  # merg mai departe fără căutare pe web
                        resp = self._call(on_text)
                    if self.history and self.history[-1]["role"] == "assistant":
                        # continuare după pause_turn: lipim în același mesaj
                        self.history[-1] = {"role": "assistant",
                                            "content": list(self.history[-1]["content"]) + list(resp.content)}
                    else:
                        self.history.append({"role": "assistant", "content": resp.content})
                    texts = [b.text for b in resp.content if b.type == "text"]
                    if resp.stop_reason == "tool_use":
                        reply_parts.extend(texts)
                        results = []
                        for b in resp.content:
                            if b.type == "tool_use":
                                if on_status:
                                    on_status(TOOL_LABELS.get(b.name, b.name))
                                out = self.tools.run(b.name, b.input)
                                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out[:60000]})
                        self.history.append({"role": "user", "content": results})
                        continue
                    if resp.stop_reason == "pause_turn":
                        if on_status:
                            on_status("caut pe internet")
                        continue
                    reply_parts.extend(texts)
                    break
            except Exception as e:
                # nu lăsăm istoricul într-o stare stricată (tool_use fără rezultat)
                self.history = []
                raise e
            finally:
                self._trim()
            import re
            reply = re.sub(r"\s+", " ", " ".join(reply_parts)).strip()
            reply = re.sub(r"[*#`_]+", "", reply)  # fără urme de Markdown în voce
            reply = reply.replace("⟦", "").replace("⟧", "")  # marcajele de engleză sunt doar pentru voce
            return reply or "Gata."
