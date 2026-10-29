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
    "model": "claude-sonnet-5-5",
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
    "wake_word_enabled": True,
    "wake_word": "tars",          # "tars" (Hey TARS) sau "jarvis" (Hey Jarvis)
    "wake_word_sensitivity": 0.5,
    "push_to_talk_hotkey": "ctrl+alt+t",
    "whisper_model": "small",
    "whisper_device": "cpu",
    "tts_voice": "ro-RO-EmilNeural",
    "tts_rate": "+8%",
    "speak_replies": True,
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
