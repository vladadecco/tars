"""Setările lui TARS: se țin în date/config.json, lângă program."""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "date"
DATA_DIR.mkdir(exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    # Creierul
    "anthropic_api_key": "",
    "model": "claude-haiku-5-5",           # conversație: rapid. Sonnet = mai profund, dar mai lent
    "analysis_model": "claude-opus-5-5",   # pentru analize strategice și cercetare
    "web_search": True,
    # Utilizatorul
    "user_name": "Vlad",
    "assistant_name": "TARS",
    "humor": 60,   # setarea de umor, în procente
    "city": "Bumbești-Jiu",
    "latitude": 45.1786,
    "longitude": 23.3814,
    "timezone": "Europe/Bucharest",
    # Mail (Gmail prin IMAP, doar citire) - parolă de aplicație Google
    "gmail_address": "",
    "gmail_app_password": "",
    # Calendar Google - adresa secretă în format iCal (doar citire)
    "calendar_ics_url": "",
    # Știri
    "news_topics": "România, Gorj, tehnologie, auto",
    # Voce
    "mic_device": "",            # gol = automat; altfel o bucată din numele microfonului
    "speaker_device": "",
    "wake_word_enabled": True,
    "wake_word": "tars",          # "tars" (Hey TARS) sau "jarvis" (Hey Jarvis)
    "wake_word_sensitivity": 0.5,
    "push_to_talk_hotkey": "ctrl+alt+t",
    "whisper_model": "auto",       # auto = large-v3-turbo pe placa video, small pe procesor
    "whisper_device": "auto",
    "tts_engine": "edge",          # edge (gratuit) / gemini (natural, cu stil) / elevenlabs (foarte natural)
    "voice_tone": "prietenos și cald",
    "voice_accent": "neutru, din România",
    "gemini_api_key": "",
    "gemini_tts_model": "gemini-3.8-flash-tts",   # gratuit pe cheia din AI Studio
    "gemini_voice": "Enceladus",
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "JBFqnCBsd6RMkjVDRZzb",
    "elevenlabs_model": "eleven_multilingual_v2",   # sau eleven_v3 (mai expresiv)
    "tts_voice": "ro-RO-EmilNeural",
    "tts_rate": "-4%",
    "tts_pitch": "-8Hz",           # vocea standard puțin mai gravă
    "tts_voice_en": "",            # gol = aceeași voce și pentru engleză
    "tts_pitch_en": "-6Hz",
    "speak_replies": True,
    "instant_ack": True,           # „Imediat, domnule.” imediat după ce termini de vorbit
    "follow_up_seconds": 6,
    # Briefing
    "auto_briefing": True,
    "briefing_time": "07:00",
    "night_start_hour": 21,
    # Actualizări automate din GitHub
    "update_repo": "vladadecco/tars",
    "update_branch": "main",
    "github_token": "",           # doar pentru depozit privat (token cu drept de citire)
    "auto_update": True,
    # Fereastră
    "window_width": 470,
    "window_height": 960,
    "always_on_top": False,
    # Aplicații pe care le poate deschide (nume -> comandă)
    "apps": {
        "chrome": "chrome",
        "edge": "msedge",
        "notepad": "notepad",
        "calculator": "calc",
        "explorer": "explorer",
        "paint": "mspaint",
        "word": "winword",
        "excel": "excel",
    },
}

_lock = threading.Lock()


class Config:
    def __init__(self):
        self.data = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                self.data.update(saved)
            except Exception:
                pass
        if self.data.get("model") == "claude-sonnet-5-5" and not self.data.get("_model_ales"):
            self.data["model"] = DEFAULTS["model"]  # o singură dată: trecem pe modelul rapid
            self.data["_model_ales"] = True
        if self.data.get("tts_voice_en") == "en-GB-RyanNeural" and not self.data.get("_o_voce"):
            self.data["tts_voice_en"] = ""
            self.data["_o_voce"] = True
        if not self.data.get("_enceladus"):  # vocea aleasă de Vlad
            self.data["gemini_voice"] = "Enceladus"
            self.data["_enceladus"] = True
        if not self.data.get("_asr_auto"):
            if self.data.get("whisper_model") == "small":
                self.data["whisper_model"] = "auto"
            if self.data.get("whisper_device") == "cpu":
                self.data["whisper_device"] = "auto"
            self.data["_asr_auto"] = True
        if self.data.get("gemini_tts_model") == "gemini-2.5-flash-preview-tts":
            self.data["gemini_tts_model"] = DEFAULTS["gemini_tts_model"]
        if self.data.get("tts_rate") in ("+8%", "+0%"):  # ritm calm, ~130-150 cuvinte/minut
            self.data["tts_rate"] = DEFAULTS["tts_rate"]
        # Cheia poate veni și din variabila de mediu
        if not self.data.get("anthropic_api_key") and os.environ.get("ANTHROPIC_API_KEY"):
            self.data["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, values: dict):
        with _lock:
            for k, v in values.items():
                if k in DEFAULTS:
                    self.data[k] = v
            self.save()

    def save(self):
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    @property
    def ready(self) -> bool:
        return bool(self.data.get("anthropic_api_key"))
