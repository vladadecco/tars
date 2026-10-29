"""Urechile și vocea: microfon, cuvânt de trezire, recunoaștere vocală, vorbire."""
import asyncio
import io
import json
import os
import queue
import re
import threading
import time

import numpy as np

from .config import ROOT

MODELS_DIR = ROOT / "modele"
SR = 16000
FRAME = 1280  # 80 ms, cât vrea modelul de trezire


def tone(freqs, dur=0.09, vol=0.18, sr=24000):
    out = []
    for f in freqs:
        t = np.linspace(0, dur, int(sr * dur), False)
        env = np.minimum(1, np.minimum(t / 0.01, (dur - t) / 0.03))
        out.append(np.sin(2 * np.pi * f * t) * env * vol)
    return np.concatenate(out).astype(np.float32), sr


BEEP_LISTEN = tone([880, 1320])
BEEP_DONE = tone([990, 660], dur=0.07)


def _pick(sd, kind: str, wanted: str = ""):
    """Găsește un dispozitiv audio valid (kind = 'input' / 'output').
    Ordinea: numele din setări, implicitul Windows, implicitul fiecărui sistem audio, primul care merge."""
    devs = sd.query_devices()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    ok = lambda i: 0 <= i < len(devs) and devs[i][key] > 0
    if wanted:
        for i, d in enumerate(devs):
            if wanted.lower() in d["name"].lower() and d[key] > 0:
                return i
    try:
        d = sd.default.device[0 if kind == "input" else 1]
        if d is not None and ok(int(d)):
            return int(d)
    except Exception:
        pass
    apis = sd.query_hostapis()
    order = sorted(range(len(apis)), key=lambda a: 0 if "MME" in apis[a]["name"] else 1)
    for a in order:
        d = apis[a].get(f"default_{kind}_device", -1)
        if ok(d):
            return d
    for i in range(len(devs)):
        if ok(i):
            return i
    raise RuntimeError("Nu am găsit niciun " + ("microfon" if kind == "input" else "difuzor") + " în Windows.")


def list_devices():
    import sounddevice as sd
    devs = sd.query_devices()
    apis = sd.query_hostapis()
    out = {"input": [], "output": []}
    seen = {"input": set(), "output": set()}
    for d in devs:
        api = apis[d["hostapi"]]["name"]
        if "MME" not in api:  # o singură variantă din fiecare, cele mai compatibile
            continue
        for kind, key in (("input", "max_input_channels"), ("output", "max_output_channels")):
            if d[key] > 0 and d["name"] not in seen[kind]:
                seen[kind].add(d["name"])
                out[kind].append(d["name"])
    return out


class Mic:
    def __init__(self, wanted: str = "", wanted_out: str = ""):
        import sounddevice as sd
        self.sd = sd
        self.q = queue.Queue(maxsize=400)
        self.muted = False
        self.level = 0.0
        self.device = _pick(sd, "input", wanted)
        try:
            out = _pick(sd, "output", wanted_out)
        except Exception:
            out = None
        sd.default.device = (self.device, out)
        self.name = sd.query_devices(self.device)["name"]
        self._buf = np.zeros(0, dtype=np.int16)
        try:
            self.rate = SR
            self.stream = sd.InputStream(device=self.device, samplerate=SR, channels=1, dtype="int16",
                                         blocksize=FRAME, callback=self._cb)
        except Exception:
            # microfonul nu acceptă 16 kHz: înregistrăm la rata lui și convertim
            self.rate = int(sd.query_devices(self.device)["default_samplerate"])
            self.stream = sd.InputStream(device=self.device, samplerate=self.rate, channels=1, dtype="int16",
                                         blocksize=int(FRAME * self.rate / SR), callback=self._cb)
        self.stream.start()

    def _resample(self, data):
        if self.rate == SR:
            return data
        n = int(round(len(data) * SR / self.rate))
        x = np.linspace(0, len(data) - 1, n)
        return np.interp(x, np.arange(len(data)), data.astype(np.float32)).astype(np.int16)

    def _cb(self, indata, frames, t, status):
        data = self._resample(indata[:, 0].copy())
        if len(data) != FRAME:
            self._buf = np.concatenate([self._buf, data])
            if len(self._buf) < FRAME:
                return
            data, self._buf = self._buf[:FRAME], self._buf[FRAME:]
        self.level = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
        if self.muted:
            return
        try:
            self.q.put_nowait(data)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass

    def read(self, timeout=0.5):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break


class VoskWake:
    """„TARS” sau „Hey TARS” - recunoaștere cu vocabular restrâns (model englez mic, rulează local).
    Se declanșează când „tars” (cu sau fără hey) rămâne stabil câteva cadre la rând (anti alarme false)."""
    PHRASE = re.compile(r"^(?:.*\s)?((hey|hi) )?tars\b")

    def __init__(self, sensitivity: float = 0.5):
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        path = MODELS_DIR / "vosk-model-small-en-us-0.15"
        if not path.exists():
            raise RuntimeError(f"lipsește modelul {path}")
        self._model = Model(str(path))
        self._KR = KaldiRecognizer
        self._grammar = json.dumps(["hey tars", "hi tars", "tars", "hey", "hi", "stars", "cars", "jarvis", "[unk]"])
        # 0.3 = sensibil (4 cadre), 0.5 = echilibrat (6), 0.7 = strict (8)
        self.need = max(3, round(2 + float(sensitivity) * 8))
        self.reset()

    def reset(self):
        self.rec = self._KR(self._model, SR, self._grammar)
        self.streak = 0
        self.frames = 0

    def feed(self, frame) -> bool:
        self.frames += 1
        done = self.rec.AcceptWaveform(frame.tobytes())
        txt = json.loads(self.rec.Result())["text"] if done else json.loads(self.rec.PartialResult())["partial"]
        hit = bool(self.PHRASE.search(txt))
        if done and hit:
            return True
        self.streak = self.streak + 1 if hit else 0
        if self.streak >= self.need:
            return True
        if done or self.frames > 400:  # ~30 s fără pauză: curățăm
            self.reset()
        return False


class OwwWake:
    """„Hey Jarvis” - modelul inclus în openWakeWord."""

    def __init__(self, threshold: float = 0.5):
        import openwakeword
        from openwakeword.model import Model
        path = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models", "hey_jarvis_v0.1.onnx")
        self.m = Model(wakeword_model_paths=[path])
        self.thr = threshold

    def reset(self):
        self.m.reset()

    def feed(self, frame) -> bool:
        return max(self.m.predict(frame).values()) >= self.thr


class Ear:
    def __init__(self, cfg, mic: Mic, on_status=None):
        self.cfg = cfg
        self.mic = mic
        self.on_status = on_status or (lambda *a: None)
        self.wake = None
        self.whisper = None
        self.noise = 150.0

    # ---------------------------------------------------------------- modele
    def load(self):
        if self.cfg["wake_word_enabled"]:
            try:
                if self.cfg["wake_word"] == "jarvis":
                    self.wake = OwwWake(float(self.cfg["wake_word_sensitivity"]))
                else:
                    self.wake = VoskWake(float(self.cfg["wake_word_sensitivity"]))
            except Exception as e:
                self.wake = None
                self.on_status("eroare", f"Cuvântul de trezire nu a pornit: {e}")
        self.on_status("pornire", "încarc recunoașterea vocală (prima dată se descarcă, durează)")
        from faster_whisper import WhisperModel
        device = self.cfg["whisper_device"]
        try:
            self.whisper = WhisperModel(self.cfg["whisper_model"], device=device,
                                        compute_type="int8" if device == "cpu" else "float16")
        except Exception:
            self.whisper = WhisperModel(self.cfg["whisper_model"], device="cpu", compute_type="int8")

    # ---------------------------------------------------------------- așteptare
    def wait_trigger(self, ptt: threading.Event, stop: threading.Event) -> str | None:
        """Așteaptă cuvântul de trezire sau butonul / scurtătura de vorbit."""
        if self.wake:
            self.wake.reset()
        while not stop.is_set():
            if ptt.is_set():
                ptt.clear()
                return "ptt"
            frame = self.mic.read(0.2)
            if frame is None:
                continue
            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            self.noise = 0.98 * self.noise + 0.02 * min(rms, 3000)
            if self.wake and self.wake.feed(frame):
                self.wake.reset()
                return "wake"
        return None

    # ---------------------------------------------------------------- înregistrare
    def record(self, start_timeout=6.0, max_seconds=30.0, silence=1.1, stop: threading.Event | None = None):
        """Înregistrează până când utilizatorul tace. Întoarce None dacă nu a vorbit."""
        threshold = max(self.noise * 2.8, 350.0)
        frames, speaking, started_at = [], False, None
        silent_for, t0 = 0.0, time.time()
        pre = []
        while True:
            if stop is not None and stop.is_set():
                return None
            f = self.mic.read(0.3)
            if f is None:
                if time.time() - t0 > start_timeout and not speaking:
                    return None
                continue
            rms = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
            dt = len(f) / SR
            if not speaking:
                pre.append(f)
                pre = pre[-5:]
                if rms > threshold:
                    speaking, started_at = True, time.time()
                    frames.extend(pre)
                elif time.time() - t0 > start_timeout:
                    return None
                continue
            frames.append(f)
            silent_for = silent_for + dt if rms < threshold * 0.7 else 0.0
            if silent_for >= silence or time.time() - started_at > max_seconds:
                break
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        if len(audio) < SR * 0.4:
            return None
        return audio

    def transcribe(self, audio) -> str:
        segments, _ = self.whisper.transcribe(
            audio, language="ro", beam_size=5, vad_filter=True,
            initial_prompt="Salut TARS. Ce s-a întâmplat peste noapte? Adaugă un task pentru mâine la ora zece. "
                           "Proiectul de printuri 3D, site-uri, PassatGauge, PFA.")
        text = " ".join(s.text.strip() for s in segments).strip()
        # Whisper inventează uneori fraze pe liniște
        if re.fullmatch(r"[\W_]*", text) or re.search(
                r"subtitr|mulțumesc pentru vizionare|abonați-vă|amara\.org", text, re.I):
            return ""
        return text


VOICE_STYLE = (
    "Ești vocea unui asistent digital rafinat: bărbat de vreo patruzeci și cinci de ani, bariton cald și catifelat, "
    "registru mediu spre grav, dicție impecabilă, calm, sigur pe el, cu un umor discret și sec în voce. "
    "Citește textul de mai jos ca un voice-over profesionist, nativ și uman, în limba română, "
    "cu accent {accent}. Livrare fluidă, caldă și naturală; evită complet tonul robotic, plat sau artificial. "
    "Respectă cu strictețe fonetica, topica și muzicalitatea limbii române. "
    "Fă pauze scurte la virgulă și pauze de respirație firești la finalul fiecărei fraze. "
    "Ritm conversațional, moderat, aproximativ 130–150 de cuvinte pe minut. "
    "Accentuează cuvintele cheie, exact cum ar face un om într-o discuție reală. "
    "Pune accentul pe silaba corectă în fiecare cuvânt românesc, pronunță clar ă, â, î, ș, ț, "
    "urcă intonația la întrebări și coboar-o firesc la afirmații. "
    "Ton: {ton}, cu variații subtile de volum și înălțime. "
    "Citește doar textul, fără să adaugi sau să schimbi nimic:")


TARS_VOICE_DESCRIPTION = (
    "A native Romanian-speaking male voice, around 45 years old: a refined, articulate digital butler. "
    "Smooth, warm baritone, mid-low pitch, slightly soft and airy, with crisp, precise diction. "
    "Calm, composed and unhurried, with quiet confidence and an understated dry wit. "
    "Very natural and human, intimate close-mic studio recording, perfect native Romanian pronunciation and intonation.")

TARS_VOICE_SAMPLE = (
    "Bună dimineața, domnule. Afară sunt nouă grade și un cer înnorat, deci o geacă subțire ar fi o alegere înțeleaptă. "
    "Aveți trei mesaje noi, dintre care unul chiar merită atenția dumneavoastră. "
    "Iar proiectul de printuri 3D a mai făcut un pas înainte. Cu ce începem astăzi?")


def design_voices(api_key: str, description: str = TARS_VOICE_DESCRIPTION):
    """Cere la ElevenLabs câteva variante de voce originală după descriere. Întoarce [(id, mp3_bytes)]."""
    import base64, json, urllib.request
    body = {"voice_description": description, "model_id": "eleven_multilingual_ttv_v2",
            "text": TARS_VOICE_SAMPLE, "guidance_scale": 6}
    req = urllib.request.Request("https://api.elevenlabs.io/v1/text-to-voice/design",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "xi-api-key": api_key.strip()})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return [(p["generated_voice_id"], base64.b64decode(p["audio_base_64"])) for p in data.get("previews", [])]


def save_designed_voice(api_key: str, generated_voice_id: str, name: str = "TARS") -> str:
    import json, urllib.request
    body = {"voice_name": name, "voice_description": TARS_VOICE_DESCRIPTION[:1000],
            "generated_voice_id": generated_voice_id}
    req = urllib.request.Request("https://api.elevenlabs.io/v1/text-to-voice",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "xi-api-key": api_key.strip()})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["voice_id"]


def decode_mp3(mp3: bytes):
    import miniaudio
    dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
    return np.frombuffer(dec.samples, dtype=np.int16).astype(np.float32) / 32768.0


class Mouth:
    def __init__(self, cfg, mic: Mic | None = None):
        import sounddevice as sd
        self.sd = sd
        self.cfg = cfg
        self.mic = mic
        self._stop = threading.Event()
        self.speaking = False

    def play(self, sound):
        data, sr = sound
        try:
            self.sd.play(data, sr)
            self.sd.wait()
        except Exception:
            pass

    def _synth(self, text: str):
        engine = self.cfg["tts_engine"]
        try:
            if engine == "gemini" and self.cfg["gemini_api_key"]:
                return self._synth_gemini(text)
            if engine == "elevenlabs" and self.cfg["elevenlabs_api_key"]:
                return self._synth_elevenlabs(text)
        except Exception as e:
            import logging
            logging.getLogger("tars").warning("voce %s: %s (trec pe Edge)", engine, e)
        return self._synth_edge(text)

    def style_prompt(self) -> str:
        return VOICE_STYLE.format(accent=self.cfg["voice_accent"], ton=self.cfg["voice_tone"])

    def _synth_gemini(self, text: str):
        import base64, json, urllib.request
        model = self.cfg["gemini_tts_model"]
        body = {
            "contents": [{"parts": [{"text": self.style_prompt() + "\n\n" + text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.cfg["gemini_voice"]}}},
            },
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.cfg["gemini_api_key"].strip()})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])  # PCM 24 kHz, 16 biți, mono
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    def _synth_elevenlabs(self, text: str):
        import json, urllib.request
        vid = self.cfg["elevenlabs_voice_id"].strip()
        body = {"text": text, "model_id": self.cfg["elevenlabs_model"], "language_code": "ro",
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35,
                                   "use_speaker_boost": True}}
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=pcm_24000",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "xi-api-key": self.cfg["elevenlabs_api_key"].strip()})
        with urllib.request.urlopen(req, timeout=60) as r:
            pcm = r.read()
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    def _synth_edge(self, text: str):
        import edge_tts
        import miniaudio

        async def go():
            com = edge_tts.Communicate(text, self.cfg["tts_voice"], rate=self.cfg["tts_rate"])
            buf = io.BytesIO()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        mp3 = asyncio.run(go())
        dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
        return np.frombuffer(dec.samples, dtype=np.int16).astype(np.float32) / 32768.0

    @staticmethod
    def _chunks(text: str):
        sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
        chunk = ""
        for s in sentences:
            if len(chunk) + len(s) < 220:
                chunk = (chunk + " " + s).strip()
            else:
                if chunk:
                    yield chunk
                chunk = s
        if chunk:
            yield chunk

    def stop(self):
        self._stop.set()
        try:
            self.sd.stop()
        except Exception:
            pass

    def say(self, text: str):
        if not text:
            return
        self._stop.clear()
        self.speaking = True
        if self.mic:
            self.mic.muted = True
        try:
            parts = list(self._chunks(text))
            results = [None] * len(parts)
            ready = [threading.Event() for _ in parts]

            def synth_all():
                for i, p in enumerate(parts):
                    if self._stop.is_set():
                        break
                    try:
                        results[i] = self._synth(p)
                    except Exception as e:
                        results[i] = e
                    ready[i].set()
                for r in ready:
                    r.set()

            threading.Thread(target=synth_all, daemon=True).start()
            for i in range(len(parts)):
                ready[i].wait(timeout=30)
                if self._stop.is_set():
                    break
                r = results[i]
                if isinstance(r, np.ndarray):
                    self.sd.play(r, 24000)
                    self.sd.wait()
                else:
                    self._fallback(parts[i])
        finally:
            self.speaking = False
            if self.mic:
                time.sleep(0.25)  # să nu se audă pe sine
                self.mic.flush()
                self.mic.muted = False

    def _fallback(self, text: str):
        """Fără internet: vocea Windows (dacă există una românească instalată)."""
        try:
            import pyttsx3
            eng = pyttsx3.init()
            for v in eng.getProperty("voices"):
                if "ro" in (v.id + v.name).lower() or "romanian" in v.name.lower():
                    eng.setProperty("voice", v.id)
                    break
            eng.say(text)
            eng.runAndWait()
        except Exception:
            pass
