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


# Exemple de fraze reale: Whisper scrie mai corect cuvintele pe care le-a „văzut” în context.
ASR_PROMPT = ("TARS, fă-ți actualizarea. Nimic, rămâi în repaus. Ce s-a întâmplat peste noapte? "
              "Adaugă un task pentru mâine la ora zece. Bifează-l, l-am făcut. Mă auzi? "
              "Caută pe internet. Pornește briefing-ul. Proiectul de printuri 3D, site-uri pentru firme, "
              "PassatGauge, PFA, Bambu Studio, filament PETG.")
ASR_HOTWORDS = "TARS fă-ți actualizarea repaus rămâi briefing bifează task PassatGauge PFA"


def _add_cuda_dlls():
    """Pe Windows, bibliotecile NVIDIA instalate prin pip trebuie adăugate în calea de căutare."""
    if os.name != "nt":
        return
    import site
    bases = list(site.getsitepackages()) + [site.getusersitepackages()]
    for base in bases:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin", "nvidia/cuda_nvrtc/bin"):
            d = os.path.join(base, *sub.split("/"))
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


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
        import logging
        lg = logging.getLogger("tars")
        model, device = self.cfg["whisper_model"], self.cfg["whisper_device"]
        self.gpu = False
        if device in ("auto", "cuda"):
            _add_cuda_dlls()
            try:
                import ctranslate2
                if ctranslate2.get_cuda_device_count() > 0:
                    m = "large-v3-turbo" if model in ("auto", "small") else model
                    w = WhisperModel(m, device="cuda", compute_type="float16")
                    list(w.transcribe(np.zeros(SR, dtype=np.float32), language="ro")[0])  # probă: merg DLL-urile?
                    self.whisper, self.gpu, self.desc = w, True, f"{m} pe placa video"
                    lg.info("recunoaștere: %s", self.desc)
                    return
            except Exception as e:
                lg.warning("placa video nu merge pentru recunoaștere (%s), trec pe procesor", e)
        m = "small" if model == "auto" else model
        self.whisper = WhisperModel(m, device="cpu", compute_type="int8",
                                    cpu_threads=max(2, (os.cpu_count() or 4) - 1))
        self.desc = f"{m} pe procesor"
        lg.info("recunoaștere: %s", self.desc)

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
    def record(self, start_timeout=6.0, max_seconds=30.0, silence=0.8, stop: threading.Event | None = None):
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
        beam = 5 if getattr(self, "gpu", False) else 1
        segments, _ = self.whisper.transcribe(
            audio, language="ro", beam_size=beam, best_of=1, vad_filter=True, without_timestamps=True,
            condition_on_previous_text=False, initial_prompt=ASR_PROMPT, hotwords=ASR_HOTWORDS)
        text = " ".join(s.text.strip() for s in segments).strip()
        # Whisper inventează uneori fraze pe liniște
        if re.fullmatch(r"[\W_]*", text) or re.search(
                r"subtitr|mulțumesc pentru vizionare|abonați-vă|amara\.org", text, re.I):
            return ""
        return text


VOICE_STYLE = (
    "Ești vocea unui majordom digital de clasă: bărbat de vreo patruzeci și cinci de ani, bariton cald, catifelat "
    "și ușor aerisit, registru mediu spre grav, dicție impecabilă și elegantă. Vorbești calm, fără grabă, "
    "cu o politețe ușor formală, sigur pe tine, cu o ironie fină și discretă care se simte doar în voce. "
    "Rostești aproape în surdină, ca la un microfon apropiat, niciodată teatral sau declamat. "
    "Citește textul de mai jos ca un voice-over profesionist, nativ și uman, în limba română, "
    "cu accent {accent}. Livrare fluidă, caldă și naturală; evită complet tonul robotic, plat sau artificial. "
    "Respectă cu strictețe fonetica, topica și muzicalitatea limbii române. "
    "Fă pauze scurte la virgulă și pauze de respirație firești la finalul fiecărei fraze. "
    "Ritm conversațional, moderat, aproximativ 130–150 de cuvinte pe minut. "
    "Accentuează cuvintele cheie, exact cum ar face un om într-o discuție reală. "
    "Pune accentul pe silaba corectă în fiecare cuvânt românesc, pronunță clar ă, â, î, ș, ț, "
    "urcă intonația la întrebări și coboar-o firesc la afirmații. "
    "Cuvintele, numele și expresiile în engleză pronunță-le cu pronunție engleză nativă, apoi revino firesc la română. "
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


def _trim_silence(a, thr=0.012, pad=1200):
    """Taie liniștea de la capete ca bucățile lipite (română + engleză) să curgă natural."""
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a
    return a[max(0, idx[0] - pad): idx[-1] + pad]


class SpeechSession:
    """Primește textul pe bucăți și vorbește fiecare propoziție de îndată ce e gata,
    sintetizând următoarea în timp ce o redă pe cea curentă."""
    END = re.compile(r"[.!?…:;]\s")

    def __init__(self, mouth):
        self.m = mouth
        self.buf = ""
        self.texts = queue.Queue()
        self.audio = queue.Queue(maxsize=3)
        self.started = False
        self.spoken_any = False

    def _start(self):
        if self.started:
            return
        self.started = True
        self.m._stop.clear()
        self.m.speaking = True
        if self.m.mic:
            self.m.mic.muted = True
        self.t1 = threading.Thread(target=self._synth_loop, daemon=True)
        self.t2 = threading.Thread(target=self._play_loop, daemon=True)
        self.t1.start()
        self.t2.start()

    def feed(self, chunk: str):
        self.buf += chunk
        while True:
            m = self.END.search(self.buf)
            # nu tăiem fraze prea scurte (ex. „Dl.”), dar nici nu așteptăm prea mult
            if not m or (m.end() < 18 and len(self.buf) < 200):
                break
            sent, self.buf = self.buf[:m.end()].strip(), self.buf[m.end():]
            self._put(sent)

    def _put(self, sent: str):
        sent = re.sub(r"[*#`_]+", "", sent).strip()
        if not sent:
            return
        self._start()
        self.texts.put(sent)

    def _synth_loop(self):
        while True:
            t = self.texts.get()
            if t is None or self.m._stop.is_set():
                self.audio.put(None)
                return
            try:
                self.audio.put((t, self.m._synth(t)))
            except Exception as e:
                self.audio.put((t, e))

    def _play_loop(self):
        while True:
            item = self.audio.get()
            if item is None:
                return
            if self.m._stop.is_set():
                continue
            t, a = item
            self.spoken_any = True
            if isinstance(a, np.ndarray):
                self.m.sd.play(a, 24000)
                self.m.sd.wait()
            else:
                self.m._fallback(t)

    def finish(self):
        """Spune ce a mai rămas și așteaptă să termine."""
        if self.buf.strip():
            self._put(self.buf)
            self.buf = ""
        if not self.started:
            return
        self.texts.put(None)
        self.t2.join(timeout=300)
        self.m.speaking = False
        if self.m.mic:
            time.sleep(0.25)
            self.m.mic.flush()
            self.m.mic.muted = False


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
            plain = text.replace("⟦", "").replace("⟧", "")
            if engine == "gemini" and self.cfg["gemini_api_key"] and time.time() > getattr(self, "_gemini_pause", 0):
                return self._synth_gemini(plain)
            if engine == "elevenlabs" and self.cfg["elevenlabs_api_key"]:
                return self._synth_elevenlabs(plain)
        except Exception as e:
            import logging
            logging.getLogger("tars").warning("voce %s: %s (trec pe Edge)", engine, e)
        return self._synth_edge(text)

    def style_prompt(self) -> str:
        return VOICE_STYLE.format(accent=self.cfg["voice_accent"], ton=self.cfg["voice_tone"])

    def _synth_gemini(self, text: str):
        import base64, json, urllib.request, urllib.error
        body = {
            "contents": [{"parts": [{"text": self.style_prompt() + "\n\n## Transcript:\n" + text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.cfg["gemini_voice"]}}},
            },
        }
        models = [self.cfg["gemini_tts_model"], "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts",
                  "gemini-2.5-flash-preview-tts"]
        last = None
        for model in dict.fromkeys(models):  # încearcă pe rând, fără dubluri
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": self.cfg["gemini_api_key"].strip()})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:  # limita gratuită atinsă: 15 minute pe vocea de rezervă, fără așteptări
                    self._gemini_pause = time.time() + 900
                if e.code not in (400, 404):
                    raise
        else:
            raise last
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

        # bucăți: [(text, e_engleza)]; engleza merge cu o voce englezească, restul cu cea românească
        single = (not self.cfg["tts_voice_en"]) or "Multilingual" in self.cfg["tts_voice"]
        if single:  # o singură voce pentru tot (cerința: aceeași voce în ambele limbi)
            parts = [(text.replace("⟦", "").replace("⟧", ""), False)]
        else:
            parts = [(p[1:-1] if p.startswith("⟦") else p, p.startswith("⟦"))
                     for p in re.split(r"(⟦[^⟧]*⟧)", text) if re.search(r"\w", p)]
        if not parts:
            parts = [(text.replace("⟦", "").replace("⟧", ""), False)]

        async def one(t, en):
            voice = self.cfg["tts_voice_en"] if en else self.cfg["tts_voice"]
            com = edge_tts.Communicate(t.strip(), voice, rate=self.cfg["tts_rate"],
                                       pitch=self.cfg["tts_pitch_en"] if en else self.cfg["tts_pitch"])
            buf = io.BytesIO()
            try:
                async for chunk in com.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
            except Exception:
                if len(parts) == 1:
                    raise
            return buf.getvalue()

        async def go():
            return await asyncio.gather(*(one(t, en) for t, en in parts))

        out = []
        for mp3 in asyncio.run(go()):
            if not mp3:
                continue
            dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
            a = np.frombuffer(dec.samples, dtype=np.int16).astype(np.float32) / 32768.0
            out.append(_trim_silence(a))
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

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

    def session(self):
        return SpeechSession(self)

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
