"""TARS - pornește fereastra și asistentul."""
import logging
import os
import sys
import webbrowser
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from jarvis.config import DATA_DIR  # noqa: E402

# Pornit fără consolă (pythonw): unele biblioteci scriu în consolă și s-ar opri.
if sys.stdout is None or sys.stderr is None:
    _out = open(DATA_DIR / "consola.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _out
    sys.stderr = sys.stderr or _out
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

logging.basicConfig(
    filename=DATA_DIR / "tars.log", level=logging.INFO, encoding="utf-8",
    format="%(asctime)s %(levelname)s %(threadName)s: %(message)s")
log = logging.getLogger("jarvis")

from jarvis.assistant import Assistant  # noqa: E402
from jarvis.store import now, today_str  # noqa: E402
from jarvis import sources  # noqa: E402
from jarvis.updater import local_version, wait_for_pid  # noqa: E402

SETTINGS_KEYS = ["anthropic_api_key", "model", "analysis_model", "web_search", "user_name", "city",
                 "gmail_address", "gmail_app_password", "calendar_ics_url", "news_topics",
                 "wake_word_enabled", "wake_word", "wake_word_sensitivity", "push_to_talk_hotkey", "whisper_model",
                 "whisper_device", "tts_voice", "tts_rate", "tts_pitch", "tts_voice_en", "speak_replies", "auto_briefing", "briefing_time", "humor", "mic_device", "speaker_device", "instant_ack",
                 "tts_engine", "voice_tone", "voice_accent", "gemini_api_key", "gemini_voice",
                 "elevenlabs_api_key", "elevenlabs_voice_id",
                 "always_on_top", "update_repo", "update_branch", "github_token", "auto_update"]


SECRET_KEYS = ("anthropic_api_key", "gmail_app_password", "github_token", "gemini_api_key", "elevenlabs_api_key")


GEMINI_CANDIDATES = ["Enceladus", "Arlo", "Algieba", "Charon", "Gacrux", "Sadaltager", "Iapetus"]


class Api:
    """Ce poate cere fereastra. (Atributele cu _ nu sunt expuse în JavaScript.)"""

    def __init__(self, assistant: Assistant):
        self._a = assistant
        self._window = None

    # ---------------------------------------------------------------- stare
    def get_live(self):
        a = self._a
        return {"status": a.store.status, "detail": a.store.status_detail,
                "level": a.mic.level if a.mic else 0, "ver": a.store.version, "heard": a.store.heard}

    def get_state(self, ver=-1):
        a, s = self._a, self._a.store
        if ver == s.version:
            return {"same": True}
        td = today_str()
        upcoming = []
        for ev in a.cache.get("events", []):
            upcoming.append({"kind": "event", "title": ev["titlu"], "date": ev["data"], "time": ev["ora"],
                             "end": ev["pana_la"], "where": ev["loc"]})
        for t in s.data["tasks"]:
            if not t["done"] and t["time"] and t["date"] == td and t["time"] >= now().strftime("%H:%M"):
                upcoming.append({"kind": "task", "title": t["text"], "date": t["date"], "time": t["time"], "id": t["id"]})
        for t in s.upcoming(7):
            upcoming.append({"kind": "task", "title": t["text"], "date": t["date"], "time": t["time"], "id": t["id"]})
        upcoming.sort(key=lambda x: (x["date"], x["time"] or "99"))
        start_day = now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=3)
        return {
            "ver": s.version,
            "ready": a.cfg.ready,
            "voice": a.voice_ok,
            "name": a.cfg["assistant_name"],
            "user": a.cfg["user_name"],
            "city": a.cfg["city"],
            "weather": a.cache.get("weather"),
            "today": td,
            "tasks": s.tasks_for(td),
            "overdue": s.overdue(),
            "upcoming": upcoming[:14],
            "log": s.log_since(start_day)[-60:],
            "chat": s.data["chat"][-40:],
            "briefing": s.data["briefing"],
            "projects": s.data["projects"],
            "research": list(reversed(s.data["research"])),
            "hotkey": a.cfg["push_to_talk_hotkey"],
            "wake": bool(a.ear and a.ear.wake),
            "version": local_version(),
            "update_available": a.update_available,
            "auto_update": a.cfg["auto_update"],
        }

    # ---------------------------------------------------------------- conversație
    def send_text(self, text):
        text = (text or "").strip()
        if text:
            self._a.ask(text, speak=self._a.cfg["speak_replies"])
        return True

    def mic(self):
        self._a.push_to_talk()
        return True

    def stop(self):
        self._cancel_compare = True  # oprește și seria de variante, nu doar fraza curentă
        self._a.stop_speaking()
        return True

    def briefing(self):
        self._a.run_briefing()
        return True

    def reset_chat(self):
        self._a.brain.reset()
        self._a.store.log("Am început o conversație nouă", "info")
        return True

    # ---------------------------------------------------------------- taskuri
    def add_task(self, text, day=None, time=None):
        if text and text.strip():
            t = self._a.store.add_task(text, day or None, time or "", source="fereastră")
            self._a.store.log(f"Task adăugat: „{t['text']}”", "task")
        return True

    def toggle_task(self, tid):
        t = self._a.store.find_task(tid)
        if t:
            self._a.store.set_done(tid, not t["done"])
            if t["done"]:
                self._a.store.log(f"Bifat ca făcut: „{t['text']}”", "done")
        return True

    def delete_task(self, tid):
        self._a.store.delete_task(tid)
        return True

    # ---------------------------------------------------------------- proiecte
    def project_update(self, pid, fields):
        allowed = {"name", "domain", "objective", "audience", "resources", "stage", "progress", "next", "note"}
        f = {k: v for k, v in (fields or {}).items() if k in allowed}
        if "progress" in f:
            try:
                f["progress"] = max(0, min(100, int(f["progress"])))
            except Exception:
                f.pop("progress")
        p = self._a.store.update_project(pid, **f)
        if p:
            self._a.store.log(f"Proiect „{p['name']}” actualizat", "project")
        return True

    def project_add(self, name):
        if name and name.strip():
            self._a.store.add_project(name=name.strip())
            self._a.store.log(f"Proiect nou: „{name.strip()}”", "project")
        return True

    def project_delete(self, pid):
        s = self._a.store
        p = s.find_project(pid)
        if p:
            s.data["projects"].remove(p)
            s.save()
            s.log(f"Am scos proiectul „{p['name']}”", "project")
        return True

    def project_analyze(self, pid, kind="strategie", focus=""):
        if not self._a.cfg.ready:
            return "Pune întâi cheia Claude în Setări."
        p = self._a.store.find_project(pid)
        if p:
            self._a.strategist.analyze_project(p, kind, focus or "")
        return True

    # ---------------------------------------------------------------- cercetare
    def research(self, query):
        if not self._a.cfg.ready:
            return "Pune întâi cheia Claude în Setări."
        if query and query.strip():
            self._a.strategist.research(query.strip())
        return True

    def research_delete(self, rid):
        s = self._a.store
        s.data["research"] = [r for r in s.data["research"] if r["id"] != rid]
        s.save()
        return True

    def compare_gemini_voices(self):
        """Spune aceeași frază cu vocile Gemini potrivite pentru TARS, ca să alegeți."""
        a = self._a
        if not a.mouth:
            return "Vocea nu e pornită."
        if not a.cfg["gemini_api_key"]:
            return "Puneți întâi cheia Gemini și apăsați Salvează."
        import threading

        def run():
            old_engine, old_voice = a.cfg["tts_engine"], a.cfg["gemini_voice"]
            a.cfg.data["tts_engine"] = "gemini"
            self._cancel_compare = False
            try:
                for i, v in enumerate(GEMINI_CANDIDATES, 1):
                    if self._cancel_compare:
                        break
                    a.cfg.data["gemini_voice"] = v
                    a.store.set_status("vorbeste", f"varianta {i}: {v}")
                    a.mouth.say(f"Varianta {i}. Bună ziua, domnule. Sistemele sunt pregătite, "
                                f"iar cafeaua, din păcate, rămâne în grija dumneavoastră.")
            finally:
                a.cfg.data["tts_engine"], a.cfg.data["gemini_voice"] = old_engine, old_voice
                a.idle_status()
        threading.Thread(target=run, daemon=True).start()
        return {"voices": GEMINI_CANDIDATES}

    def test_voice(self):
        a = self._a
        if not a.mouth:
            return "Vocea nu e pornită."
        import threading
        sample = ("Bună ziua, domnule. Aceasta este noua mea voce. "
                  "Spuneți-mi, vă rog, dacă sună mai natural… sau mai ajustăm puțin tonul.")
        threading.Thread(target=a.mouth.say, args=(sample,), daemon=True).start()
        return True

    def design_voice(self):
        """Creează 3 variante de voce originală pentru TARS (ElevenLabs). Durează ~20 s."""
        from jarvis import audio as A
        key = self._a.cfg["elevenlabs_api_key"]
        if not key:
            return {"error": "Puneți întâi cheia ElevenLabs și apăsați Salvează."}
        try:
            self._previews = A.design_voices(key)
        except Exception as e:
            return {"error": f"ElevenLabs a refuzat: {e}"}
        self._a.store.log(f"Am creat {len(self._previews)} variante de voce", "info")
        return {"count": len(self._previews)}

    def play_preview(self, i):
        from jarvis import audio as A
        import threading
        pv = getattr(self, "_previews", [])
        if not (0 <= int(i) < len(pv)) or not self._a.mouth:
            return False
        data = A.decode_mp3(pv[int(i)][1])
        threading.Thread(target=self._a.mouth.play, args=((data, 24000),), daemon=True).start()
        return True

    def choose_preview(self, i):
        from jarvis import audio as A
        pv = getattr(self, "_previews", [])
        if not (0 <= int(i) < len(pv)):
            return {"error": "Variantă inexistentă."}
        try:
            vid = A.save_designed_voice(self._a.cfg["elevenlabs_api_key"], pv[int(i)][0])
        except Exception as e:
            return {"error": f"Nu am putut salva vocea: {e}"}
        self._a.cfg.update({"elevenlabs_voice_id": vid, "tts_engine": "elevenlabs"})
        self._a.store.log("Vocea nouă a lui TARS e salvată", "done")
        return {"ok": True, "voice_id": vid}

    def audio_devices(self):
        try:
            from jarvis.audio import list_devices
            return list_devices()
        except Exception as e:
            return {"input": [], "output": [], "error": str(e)}

    def update_now(self):
        if not self._a.updater.configured:
            return "Pune întâi depozitul GitHub în Setări."
        self._a.update_now()
        return True

    def open_url(self, url):
        if url and url.startswith(("http://", "https://")):
            webbrowser.open(url)
        return True

    # ---------------------------------------------------------------- setări
    def get_settings(self):
        c = self._a.cfg
        out = {k: c[k] for k in SETTINGS_KEYS}
        # nu trimitem secretele înapoi în pagină; doar dacă există
        for k in SECRET_KEYS:
            out[k] = "••••••••" if c[k] else ""
        out["version"] = local_version()
        return out

    def save_settings(self, values):
        c = self._a.cfg
        before = dict(c.data)
        clean = {}
        for k in SETTINGS_KEYS:
            if k not in values:
                continue
            v = values[k]
            if k in SECRET_KEYS and (v == "••••••••"):
                continue
            clean[k] = v.strip() if isinstance(v, str) else v
        if clean.get("update_repo"):
            r = clean["update_repo"].replace("https://", "").replace("http://", "").replace("github.com/", "")
            clean["update_repo"] = r.strip("/").removesuffix(".git")
            self._a.updater.last_check = 0
        if clean.get("city") and clean["city"] != c["city"]:
            try:
                g = sources.geocode(clean["city"])
                if g:
                    clean["latitude"], clean["longitude"] = g[0], g[1]
            except Exception:
                pass
        for k in ("latitude", "longitude"):
            if k in clean:
                c.data[k] = clean.pop(k)
        try:
            clean["wake_word_sensitivity"] = float(clean.get("wake_word_sensitivity", c["wake_word_sensitivity"]))
            clean["humor"] = max(0, min(100, int(float(clean.get("humor", c["humor"])))))
        except Exception:
            clean.pop("wake_word_sensitivity", None)
        c.update(clean)
        self._a.store.log("Setări salvate", "info")
        if self._a.mouth and any(k in clean for k in ("tts_engine", "gemini_voice", "tts_voice")):
            import threading as _t
            _t.Thread(target=self._a.mouth.prepare_fillers, args=(DATA_DIR,), daemon=True).start()
        if self._window is not None and "always_on_top" in clean:
            try:
                self._window.on_top = bool(clean["always_on_top"])
            except Exception:
                pass
        self._a.idle_status()
        threading_refresh(self._a)
        if "speaker_device" in clean and clean["speaker_device"] != before.get("speaker_device") and self._a.mic:
            try:  # difuzorul se schimbă pe loc, fără repornire
                from jarvis.audio import _pick
                sd = self._a.mic.sd
                sd.default.device = (self._a.mic.device, _pick(sd, "output", clean["speaker_device"]))
                before["speaker_device"] = clean["speaker_device"]
            except Exception as e:
                self._a.store.log(f"Difuzorul nu s-a putut schimba: {e}", "error")
        voice_keys = ("mic_device", "speaker_device", "wake_word_enabled", "wake_word", "wake_word_sensitivity", "whisper_model",
                      "whisper_device", "push_to_talk_hotkey")
        return {"ok": True, "restart": any(k in clean and clean[k] != before.get(k) for k in voice_keys)}


def threading_refresh(a):
    import threading
    threading.Thread(target=a.refresh_cache, daemon=True).start()


def window_geometry(webview, cfg):
    """Fereastră pe portret. Dacă există un monitor vertical, o pune pe el, pe toată înălțimea."""
    w, h = int(cfg["window_width"]), int(cfg["window_height"])
    try:
        screens = webview.screens
    except Exception:
        screens = []
    if screens:
        vertical = [s for s in screens if s.height > s.width]
        if vertical:
            s = vertical[0]
            return dict(width=s.width, height=s.height - 40, x=s.x, y=s.y, screen=s)
        s = screens[0]
        h = min(h, s.height - 70)
        w = min(w, int(h * 0.56))  # păstrăm raportul de portret
        return dict(width=w, height=h, x=s.x + s.width - w - 16, y=s.y + 12, screen=s)
    return dict(width=w, height=h)


def main():
    if "--dupa-actualizare" in sys.argv:
        try:
            wait_for_pid(int(sys.argv[sys.argv.index("--dupa-actualizare") + 1]))
        except Exception:
            pass
        import time
        time.sleep(4)  # lăsăm procesele vechi ale ferestrei (WebView2) să elibereze folderul
    import webview
    # după o actualizare, golim memoria ferestrei ca să nu arate pagina veche
    try:
        mark = DATA_DIR / "ui_version.txt"
        if not mark.exists() or mark.read_text(encoding="utf-8").strip() != local_version():
            import shutil
            shutil.rmtree(DATA_DIR / "webview", ignore_errors=True)
            mark.write_text(local_version(), encoding="utf-8")
    except Exception:
        pass
    a = Assistant()
    api = Api(a)
    geo = window_geometry(webview, a.cfg)
    window = webview.create_window(
        "TARS", url=str(ROOT / "ui" / "index.html"), js_api=api,
        min_size=(380, 640), background_color="#03070d", on_top=bool(a.cfg["always_on_top"]),
        text_select=True, **geo)
    api._window = window
    a.start()
    log.info("TARS pornit")
    webview.start(debug=bool(os.environ.get("JARVIS_DEBUG")), private_mode=False,
                  storage_path=str(DATA_DIR / "webview"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log.error("TARS s-a oprit la pornire:\n%s", traceback.format_exc())
        # încă o încercare, cu un folder nou pentru fereastră (dacă cel vechi era blocat)
        try:
            import shutil
            shutil.rmtree(DATA_DIR / "webview", ignore_errors=True)
            main()
        except Exception:
            log.error("A doua pornire a eșuat:\n%s", traceback.format_exc())
            raise
