"""Dirijorul: leagă vocea, creierul, programul zilnic și fereastra."""
import logging
import re
import queue
import threading
import time
import traceback
from datetime import datetime, timedelta

from .brain import Brain
from .config import Config, DATA_DIR
from .store import Store, now, today_str
from .strategy import Strategist
from .tools import Tools
from .updater import Updater, pending_announcement, local_version
from . import sources

log = logging.getLogger("jarvis")


STOP_RE = re.compile(r"^(nimic|gata|stop|mersi|mulțumesc|mulțam|lasă|las-o|ajunge|pa|repaus|"
                     r"rămâi în repaus|treci în repaus|du-te în repaus|liniște|taci)\b")


def is_stop_phrase(low: str) -> bool:
    """„Nimic, rămâi în repaus” / „gata, mersi” etc. - scurte și cu sens de oprire."""
    low = low.replace("ţ", "ț").replace("ş", "ș")
    words = re.findall(r"\w+", low)
    if not words or len(words) > 9:
        return False
    return bool(STOP_RE.search(low)) or "repaus" in words


class Assistant:
    def __init__(self):
        self.cfg = Config()
        self.store = Store()
        self.tools = Tools(self.cfg, self.store)
        self.brain = Brain(self.cfg, self.store, self.tools)
        self.strategist = Strategist(self.cfg, self.store, self.brain.client, self.notify)
        self.tools.strategist = self.strategist
        self.tools.assistant = self
        self.end_followup = False
        self.updater = Updater(self.cfg, self.store)
        self.update_available = ""
        self.requests = queue.Queue()
        self.ptt = threading.Event()
        self.stop_event = threading.Event()
        self.mic = self.ear = self.mouth = None
        self.voice_ok = False
        self.cache = {"weather": None, "events": [], "updated": ""}
        self._busy = threading.Event()
        self._brief_lock = threading.Lock()
        self._started = time.time()

    # ------------------------------------------------------------ pornire
    def start(self):
        self.store.set_status("pornire", "pornesc")
        threading.Thread(target=self._worker, daemon=True, name="worker").start()
        threading.Thread(target=self._scheduler, daemon=True, name="scheduler").start()
        threading.Thread(target=self._voice_init_and_loop, daemon=True, name="voice").start()
        self._hotkey()

    def _hotkey(self):
        try:
            import keyboard
            keyboard.add_hotkey(self.cfg["push_to_talk_hotkey"], self.push_to_talk)
        except Exception as e:
            log.warning("scurtătura nu a pornit: %s", e)

    def idle_status(self):
        if not self.cfg.ready:
            self.store.set_status("setari", "pune cheia Claude în Setări")
        elif self.voice_ok and self.ear and self.ear.wake:
            self.store.set_status("asteapta", "spune „TARS” sau scrie mai jos")
        elif self.voice_ok:
            self.store.set_status("asteapta", f"apasă microfonul sau {self.cfg['push_to_talk_hotkey']}")
        else:
            self.store.set_status("asteapta", "scrie mai jos (vocea nu e pornită)")

    # ------------------------------------------------------------ cereri
    def ask(self, text: str, speak: bool = True, briefing: bool = False):
        done = threading.Event()
        self.requests.put(("ask", text, speak, briefing, done))
        return done

    def notify(self, text: str):
        self.requests.put(("say", text, True, False, threading.Event()))

    def push_to_talk(self):
        if self.mouth and self.mouth.speaking:
            self.mouth.stop()
        self.ptt.set()

    def stop_speaking(self):
        if self.mouth:
            self.mouth.stop()
        # golim ce mai era la rând de spus
        try:
            while True:
                item = self.requests.get_nowait()
                if item[0] != "say":
                    self.requests.put(item)
                    break
                item[4].set()
        except queue.Empty:
            pass

    def _speak(self, text: str):
        if self.cfg["speak_replies"] and self.mouth:
            self.store.set_status("vorbeste", "")
            self.mouth.say(text)

    def _worker(self):
        while True:
            kind, text, speak, briefing, done = self.requests.get()
            self._busy.set()
            try:
                if kind == "say":
                    self.store.chat("jarvis", text)
                    if speak:
                        self._speak(text)
                    continue
                if not self.cfg.ready:
                    self.store.chat("jarvis", "Am nevoie de cheia Claude ca să gândesc. O pui din Setări, rotița de sus.")
                    continue
                self.store.chat("tu", text)
                self.store.set_status("gandeste", "mă gândesc")
                sess = self.mouth.session() if (speak and self.cfg["speak_replies"] and self.mouth) else None

                def on_text(chunk, sess=sess):
                    if sess is not None:
                        if not sess.started:
                            self.store.set_status("vorbeste", "")
                        sess.feed(chunk)

                reply = self.brain.ask(text, on_status=lambda s: self.store.set_status("gandeste", s), on_text=on_text)
                self.store.chat("jarvis", reply)
                if briefing:
                    self.store.set_briefing(reply)
                if sess is not None:
                    sess.finish()
                    if not sess.spoken_any:
                        self._speak(reply)
            except Exception as e:
                log.error("eroare: %s\n%s", e, traceback.format_exc())
                msg = self._friendly_error(e)
                self.store.chat("jarvis", msg)
                self.store.log(msg, "error")
                if speak:
                    self._speak(msg)
            finally:
                self._busy.clear()
                done.set()
                self.idle_status()

    @staticmethod
    def _friendly_error(e) -> str:
        s = f"{type(e).__name__}: {e}"
        if "authentication" in s.lower() or "401" in s:
            return "Cheia Claude nu e bună. Verific-o în Setări."
        if "credit" in s.lower() or "billing" in s.lower():
            return "Contul Claude API nu mai are credit."
        if "connect" in s.lower() or "timeout" in s.lower():
            return "Nu am internet sau serverul nu răspunde. Încerc din nou când îmi spui."
        return f"Ceva n-a mers: {s[:160]}"

    # ------------------------------------------------------------ voce
    def _voice_init_and_loop(self):
        try:
            from .audio import Mic, Ear, Mouth, BEEP_LISTEN, BEEP_DONE
            self.mic = Mic(self.cfg["mic_device"], self.cfg["speaker_device"])
            self.store.log(f"Microfon: {self.mic.name}", "info")
            self.mouth = Mouth(self.cfg, self.mic)
            self.ear = Ear(self.cfg, self.mic, on_status=self.store.set_status)
            self.ear.load()
            self.voice_ok = True
            self.beeps = (BEEP_LISTEN, BEEP_DONE)
        except Exception as e:
            log.error("vocea nu a pornit: %s\n%s", e, traceback.format_exc())
            self.store.log(f"Vocea nu a pornit: {e}", "error")
            # vorbitul tot poate merge chiar dacă microfonul nu
            if self.mouth is None:
                try:
                    from .audio import Mouth
                    self.mouth = Mouth(self.cfg, None)
                except Exception:
                    pass
            self.idle_status()
            return
        self.idle_status()
        self._announce_update()
        self._maybe_start_briefing()
        while not self.stop_event.is_set():
            try:
                self._voice_round()
            except Exception as e:
                log.error("bucla vocii: %s\n%s", e, traceback.format_exc())
                time.sleep(1)

    def _voice_round(self):
        trig = self.ear.wait_trigger(self.ptt, self.stop_event)
        if not trig:
            return
        if self._busy.is_set() and self.mouth.speaking:
            self.mouth.stop()
        follow = 0
        while True:
            self.store.set_status("asculta", "te ascult")
            if follow == 0:
                if trig == "ptt":
                    self.mic.flush()
                # la „TARS” păstrăm ce ai spus deja în aceeași suflare
                self.mouth.play(self.beeps[0])
            audio = self.ear.record(start_timeout=6.0 if follow == 0 else float(self.cfg["follow_up_seconds"]))
            if audio is None:
                self.idle_status()
                return
            self.store.set_status("gandeste", "transcriu")
            text = self.ear.transcribe(audio)
            if not text:
                self.idle_status()
                return
            self.store.heard = text
            low = text.lower().strip(" .!?")
            if is_stop_phrase(low):
                self.mouth.play(self.beeps[1])
                self.idle_status()
                return
            self.end_followup = False
            done = self.ask(text)
            done.wait()
            if self.end_followup:
                self.idle_status()
                return
            follow += 1
            if follow > 6:
                self.idle_status()
                return

    # ------------------------------------------------------------ program zilnic
    def _maybe_start_briefing(self):
        with self._brief_lock:
            self._maybe_start_briefing_locked()

    def _maybe_start_briefing_locked(self):
        if not (self.cfg["auto_briefing"] and self.cfg.ready):
            return
        n = now()
        if self.store.data["briefing"].get("date") == today_str():
            return
        try:
            hh, mm = [int(x) for x in self.cfg["briefing_time"].split(":")]
        except Exception:
            hh, mm = 7, 0
        if n.hour < 13 and (n.hour, n.minute) >= (hh, mm):
            self.store.data["briefing"]["date"] = today_str()  # să nu pornească de două ori
            self.ask("Bună dimineața. Fă-mi briefing-ul: ce s-a întâmplat peste noapte și ce am azi.", briefing=True)

    def run_briefing(self):
        self.ask("Fă-mi briefing-ul: ce s-a întâmplat de aseară și ce am azi.", briefing=True)

    def _scheduler(self):
        last_cache = 0
        while not self.stop_event.is_set():
            try:
                n = now()
                # mementouri
                for t in list(self.store.data["tasks"]):
                    if t["done"] or t.get("reminded") or not t.get("time") or t["date"] != today_str():
                        continue
                    if t["time"] <= n.strftime("%H:%M"):
                        t["reminded"] = True
                        self.store.save()
                        self.store.log(f"Memento: {t['text']}", "reminder")
                        self.notify(f"Memento, {self.cfg['user_name']}: {t['text']}.")
                if time.time() - self._started > 25:
                    self._maybe_start_briefing()
                # actualizări: la ~2 minute după pornire, apoi la 6 ore
                if (self.updater.configured and time.time() - self._started > 120
                        and time.time() - self.updater.last_check > 6 * 3600):
                    self._check_update(auto=True)
                # date pentru fereastră
                if time.time() - last_cache > 600:
                    last_cache = time.time()
                    self.refresh_cache()
                self.store.data["last_seen"] = n.isoformat(timespec="seconds")
            except Exception as e:
                log.error("program: %s", e)
            self.stop_event.wait(20)

    # ------------------------------------------------------------ actualizări
    def _announce_update(self):
        info = pending_announcement()
        if info:
            notes = info.get("notes") or ""
            self.store.log(f"Actualizat la versiunea {info.get('to')}", "done")
            self.notify(f"M-am actualizat, domnule. {notes}".strip())

    def _idle(self) -> bool:
        return (not self._busy.is_set() and self.requests.empty()
                and not (self.mouth and self.mouth.speaking) and self.store.status in ("asteapta", "setari"))

    def _check_update(self, auto: bool):
        try:
            if not self.updater.check():
                self.update_available = ""
                return False
        except Exception as e:
            log.warning("verificare actualizare: %s", e)
            if not auto:
                self.store.log(f"Nu am putut verifica actualizările: {e}", "error")
            return False
        self.update_available = self.updater.remote
        self.store.version += 1
        if auto and not self.cfg["auto_update"]:
            self.store.log(f"Versiune nouă disponibilă: {self.updater.remote}", "info")
            return True
        if auto:
            # așteptăm să fie liniște (maxim o oră), ca să nu-l întrerupem
            for _ in range(180):
                if self._idle():
                    break
                time.sleep(20)
            else:
                return True
        self._do_update()
        return True

    def _do_update(self):
        self.store.log(f"Instalez versiunea {self.updater.remote}", "info")
        done = threading.Event()
        self.requests.put(("say", "Am o versiune nouă. Mă actualizez, revin în câteva secunde.", True, False, done))
        done.wait(30)
        try:
            self.store.set_status("gandeste", "mă actualizez")
            self.updater.apply()
        except Exception as e:
            log.error("actualizare: %s", e)
            self.store.log(f"Actualizarea a eșuat, am rămas pe versiunea veche: {e}", "error")
            self.notify("Actualizarea nu a reușit. Am rămas pe versiunea veche, totul funcționează.")
            self.idle_status()
            return
        self.store.save()
        self.updater.restart()

    def update_now(self):
        threading.Thread(target=self._check_update, kwargs={"auto": False}, daemon=True).start()

    def refresh_cache(self):
        try:
            self.cache["weather"] = sources.weather_short(self.cfg["latitude"], self.cfg["longitude"])
        except Exception as e:
            log.warning("vreme: %s", e)
        if self.cfg["calendar_ics_url"]:
            try:
                start = now().replace(hour=0, minute=0, second=0, microsecond=0)
                self.cache["events"] = sources.calendar_events(self.cfg["calendar_ics_url"], start, start + timedelta(days=3))
            except Exception as e:
                log.warning("calendar: %s", e)
        self.cache["updated"] = now().strftime("%H:%M")
        self.store.version += 1
