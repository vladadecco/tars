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
        self.gpu_error = ""
        if device in ("auto", "cuda"):
            _add_cuda_dlls()
            try:
                import ctranslate2
                n_gpu = ctranslate2.get_cuda_device_count()
                if n_gpu == 0:
                    self.gpu_error = "ctranslate2 nu vede nicio placă NVIDIA (driver vechi sau lipsă CUDA)"
                if n_gpu > 0:
                    m = "large-v3-turbo" if model in ("auto", "small") else model
                    # plăcile mai vechi (ex. GTX 10xx) nu știu float16 rapid: încercăm pe rând
                    for ct in ("float16", "int8_float16", "int8", "float32"):
                        try:
                            w = WhisperModel(m, device="cuda", compute_type=ct)
                            list(w.transcribe(np.zeros(SR, dtype=np.float32), language="ro")[0])  # probă
                        except Exception as e2:
                            self.gpu_error = str(e2)[:200]
                            continue
                        self.whisper, self.gpu, self.desc = w, True, f"{m} pe placa video ({ct})"
                        self.gpu_error = ""
                        lg.info("recunoaștere: %s", self.desc)
                        return
            except Exception as e:
                lg.warning("placa video nu merge pentru recunoaștere (%s), trec pe procesor", e)
                self.gpu_error = str(e)[:200]
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
        from collections import deque
        ring = deque(maxlen=25)  # ultimele ~2 secunde
        self.pre_audio = []
        while not stop.is_set():
            if ptt.is_set():
                ptt.clear()
                return "ptt"
            frame = self.mic.read(0.2)
            if frame is None:
                continue
            ring.append(frame)
            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            self.noise = 0.98 * self.noise + 0.02 * min(rms, 3000)
            if self.wake and self.wake.feed(frame):
                self.wake.reset()
                # păstrăm ce ați spus imediat după „TARS” (altfel începutul comenzii se pierdea)
                self.pre_audio = list(ring)[-18:]
                return "wake"
        return None

    # ---------------------------------------------------------------- înregistrare
    def record(self, start_timeout=6.0, max_seconds=30.0, silence=0.8, stop: threading.Event | None = None,
               pre_audio=None):
        """Înregistrează până când utilizatorul tace. Întoarce None dacă nu a vorbit."""
        threshold = max(self.noise * 2.5, 300.0)
        frames, speaking, started_at = [], False, None
        silent_for, t0 = 0.0, time.time()
        pre = []
        if pre_audio:
            frames = list(pre_audio)
            last = np.concatenate(pre_audio[-4:]).astype(np.float32)
            if float(np.sqrt(np.mean(last ** 2))) > threshold * 0.7:
                speaking, started_at = True, time.time()  # vorbiți deja, continuăm direct
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
        peak = float(np.max(np.abs(audio))) or 1.0
        if peak < 0.5:  # microfon încet (ex. cel din cameră): aducem vocea la nivel bun
            audio = audio * min(8.0, 0.8 / peak)
        return audio

    def use_cloud_asr(self) -> bool:
        eng = self.cfg["asr_engine"]
        if not self.cfg["gemini_api_key"] or time.time() < getattr(self, "_cloud_pause", 0):
            return False
        return eng == "gemini" or (eng == "auto" and not getattr(self, "gpu", False))

    def _transcribe_cloud(self, audio) -> str:
        """Transcriere prin Gemini: mult mai precisă în română decât modelul mic de pe procesor."""
        import base64, io as _io, json, wave
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        buf = _io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm)
        if len(audio) > SR * 30:
            audio = audio[: SR * 30]
        prompt = ("Transcrie exact ce spune vorbitorul, în limba română, cu diacritice. Scrie doar textul spus, "
                  "fără comentarii. Dacă nu se înțelege nimic, răspunde gol. Context, comenzi frecvente: " + ASR_PROMPT)
        body = json.dumps({
            "contents": [{"parts": [{"inlineData": {"mimeType": "audio/wav",
                                                    "data": base64.b64encode(buf.getvalue()).decode()}},
                                    {"text": prompt}]}],
            "generationConfig": {"temperature": 0},
        }).encode("utf-8")
        last = None
        for model in dict.fromkeys([self.cfg["asr_cloud_model"], "gemini-3.5-transcribe", "gemini-3.5-flash-lite"]):
            conn, r = ASR_POOL.post(f"/v1beta/models/{model}:generateContent", body,
                                       {"Content-Type": "application/json",
                                        "x-goog-api-key": self.cfg["gemini_api_key"].strip()})
            raw = r.read()
            ASR_POOL.release(conn)
            if r.status == 200:
                d = json.loads(raw)
                parts = d.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                return " ".join(p.get("text", "") for p in parts).strip()
            last = f"{model}: {r.status} {raw[:150]!r}"
            if r.status == 429:
                self._cloud_pause = time.time() + 600
                break
            if r.status not in (400, 404):
                break
        raise RuntimeError(last or "transcriere eșuată")

    def transcribe(self, audio) -> str:
        self.last_engine, self.cloud_error = "local", ""
        if self.use_cloud_asr():
            try:
                raw = self._transcribe_cloud(audio)
                text = self._clean(raw)
                if text:
                    self.last_engine = "Gemini"
                    return text
                self.cloud_error = f"Gemini a întors gol ({raw[:60]!r})"
            except Exception as e:
                import logging
                logging.getLogger("tars").warning("transcriere cloud: %s (trec pe local)", e)
                self.cloud_error = str(e)[:200]
        beam = 1  # viteză: diferența de calitate e mică, cu vocabularul de comenzi
        segments, _ = self.whisper.transcribe(
            audio, language="ro", beam_size=beam, best_of=1, vad_filter=True, without_timestamps=True,
            condition_on_previous_text=False, initial_prompt=ASR_PROMPT, hotwords=ASR_HOTWORDS)
        return self._clean(" ".join(s.text.strip() for s in segments).strip())

    @staticmethod
    def _clean(text: str) -> str:
        text = (text or "").strip().strip('"„”')
        # scoatem numele de la început („TARS, ...”), care vine din înregistrarea de dinainte
        text = re.sub(r"^\W*((h[ăaâe]i|hey|hi|ei)\W*)?(tars-ul|tars|tarz|tares|taras|stars|tarse)\b\W*", "", text, flags=re.I).strip()
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
    "La virgulă fă doar o pauză foarte scurtă, abia perceptibilă, fără să rupi fraza; "
    "la finalul frazei, o pauză de respirație firească, dar scurtă. Vorbirea curge legat. "
    "Ritm conversațional, moderat, aproximativ 130–150 de cuvinte pe minut. "
    "Accentuează cuvintele cheie, exact cum ar face un om într-o discuție reală. "
    "Pune accentul pe silaba corectă în fiecare cuvânt românesc, pronunță clar ă, â, î, ș, ț, "
    "urcă intonația la întrebări și coboar-o firesc la afirmații. "
    "Accent românesc nativ peste tot; doar numele proprii englezești (ex. WhatsApp) le pronunți englezește. "
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


_PIPER_LOCK = threading.Lock()
PIPER_DIR = MODELS_DIR / "piper"


def piper_model_path(name: str):
    """Modelul Piper din modele/piper. Vocile publice (ex. ro_RO-mihai-medium) se descarcă singure;
    vocea antrenată de noi se pune aici (fișierele .onnx și .onnx.json)."""
    import urllib.request
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    onnx = PIPER_DIR / f"{name}.onnx"
    js = PIPER_DIR / f"{name}.onnx.json"
    if onnx.exists() and js.exists():
        return onnx
    m = re.match(r"^([a-z]{2,3})_([A-Z]{2})-(.+)-(x_low|low|medium|high)$", name)
    if not m:
        raise FileNotFoundError(f"Nu găsesc modelul Piper {onnx}")
    base = (f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            f"{m.group(1)}/{m.group(1)}_{m.group(2)}/{m.group(3)}/{m.group(4)}/{name}")
    for url, dest in ((base + ".onnx.json", js), (base + ".onnx", onnx)):
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        tmp.replace(dest)
    return onnx


def _edge_val(v, unit):
    """Edge cere „+0%” / „-8Hz”; din Setări poate veni „0”, „5”, „-4 %” etc."""
    s = str(v or "0").replace(" ", "").replace(",", ".")
    m = re.match(r"^([+-]?)(\d+)(?:\.\d+)?", s)
    if not m:
        return f"+0{unit}"
    return f"{m.group(1) or '+'}{m.group(2)}{unit}"


def _trim_silence(a, thr=0.012, pad=1200):
    """Taie liniștea de la capete ca bucățile lipite (română + engleză) să curgă natural."""
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a
    return a[max(0, idx[0] - pad): idx[-1] + pad]


class _Pool:
    """Conexiuni HTTPS refolosite: economisesc ~0,2 s la fiecare frază."""
    def __init__(self, host, timeout=60):
        import http.client
        self.http = http.client
        self.host = host
        self.timeout = timeout
        self.idle = []
        self.lock = threading.Lock()

    def post(self, path, body: bytes, headers: dict):
        """Întoarce (conexiune, răspuns). Conexiunea se dă înapoi cu release() după citire."""
        for attempt in range(2):
            with self.lock:
                c = self.idle.pop() if self.idle else None
            if c is None:
                c = self.http.HTTPSConnection(self.host, timeout=self.timeout)
            try:
                c.request("POST", path, body=body, headers=headers)
                return c, c.getresponse()
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
                if attempt:
                    raise

    def release(self, c, ok=True):
        if ok:
            with self.lock:
                self.idle.append(c)
        else:
            try:
                c.close()
            except Exception:
                pass


GEMINI_POOL = _Pool("generativelanguage.googleapis.com")
SPEECHIFY_POOL = _Pool("api.speechify.ai")
ASR_POOL = _Pool("generativelanguage.googleapis.com", timeout=8)  # transcrierea nu are voie să blocheze
FILLERS = ["Imediat, domnule.", "Desigur.", "O clipă.", "Da, domnule.", "Bineînțeles."]


class SpeechSession:
    """Primește textul pe bucăți și vorbește fiecare propoziție de îndată ce e gata,
    sintetizând următoarea în timp ce o redă pe cea curentă."""
    END = re.compile(r"[.!?…:;]\s")
    FIRST = re.compile(r"[.!?…:;,]\s")

    def __init__(self, mouth):
        self.m = mouth
        self.buf = ""
        self.texts = queue.Queue()
        self.audio = queue.Queue(maxsize=3)
        self.started = False
        self.spoken_any = False
        self.put_any = False
        self.first_audio_at = None
        self.first_put_at = None

    def _start(self):
        if self.started:
            return
        self.started = True
        self.m._stop.clear()
        self.m.speaking = True
        if self.m.mic:
            self.m.mic.muted = True
        self._first_synth = True
        self.t1 = threading.Thread(target=self._synth_loop, daemon=True)
        self.t2 = threading.Thread(target=self._play_loop, daemon=True)
        self.t1.start()
        self.t2.start()

    def feed(self, chunk: str):
        self.buf += chunk
        while True:
            first = not self.put_any
            need = 12 if first else 18  # nu tăiem bucăți prea scurte (ex. „Dl.”)
            m = next((x for x in (self.FIRST if first else self.END).finditer(self.buf) if x.end() >= need), None)
            if not m:
                break
            sent, self.buf = self.buf[:m.end()].strip(), self.buf[m.end():]
            self._put(sent)

    def _put(self, sent: str):
        sent = re.sub(r"[*#`_]+", "", sent).strip()
        if not sent:
            return
        first = not self.put_any
        self.put_any = True
        if first:
            self.first_put_at = time.time()
        self._start()
        self.texts.put(sent)

    def _synth_loop(self):
        while True:
            t = self.texts.get()
            if t is None or self.m._stop.is_set():
                self.audio.put(None)
                return
            try:
                # prima frază: voce în flux (pornește înainte să fie gata toată fraza)
                gen = self.m._synth_stream(t) if self._first_synth else None
                self._first_synth = False
                self.audio.put((t, gen if gen is not None else self.m._synth(t)))
            except Exception as e:
                self.audio.put((t, e))

    def _play_loop(self):
        """Redă totul printr-un singur flux audio: fără goluri între bucăți."""
        wait = getattr(self.m, "busy_until", 0) - time.time()
        if wait > 0:  # se mai aude confirmarea „Imediat, domnule.”
            time.sleep(min(wait, 3))
        out = None
        try:
            out = self.m.sd.OutputStream(samplerate=24000, channels=1, dtype="float32")
            out.start()
        except Exception:
            out = None
        try:
            while True:
                item = self.audio.get()
                if item is None:
                    return
                if self.m._stop.is_set():
                    continue
                t, a = item
                self.spoken_any = True
                if self.first_audio_at is None:
                    self.first_audio_at = time.time()
                if isinstance(a, np.ndarray):
                    a = _trim_silence(a, thr=0.006, pad=2400)  # fără liniște lungă la capete, dar fără să tăiem din cuvinte
                    if out is not None:
                        self._write(out, a)
                    else:
                        self.m.sd.play(a, 24000)
                        self.m.sd.wait()
                elif hasattr(a, "__next__"):
                    started = False
                    got = False
                    for chunk in a:
                        if self.m._stop.is_set():
                            break
                        if not started:  # tăiem liniștea de la începutul fluxului
                            idx = np.where(np.abs(chunk) > 0.006)[0]
                            if len(idx) == 0:
                                continue
                            chunk = chunk[max(0, idx[0] - 2400):]
                            started = True
                        got = True
                        if out is not None:
                            self._write(out, chunk)
                        else:
                            self.m.sd.play(chunk, 24000)
                            self.m.sd.wait()
                    if not got:
                        self.m._fallback(t)
                else:
                    self.m._fallback(t)
        finally:
            if out is not None:
                try:
                    if not self.m._stop.is_set():
                        time.sleep(0.15)  # lasă ultima bucată să se audă complet
                    out.stop()
                    out.close()
                except Exception:
                    pass

    def _write(self, out, a):
        step = 2400  # bucăți de 0,1 s: butonul de oprire răspunde imediat
        for i in range(0, len(a), step):
            if self.m._stop.is_set():
                return
            out.write(a[i:i + step].reshape(-1, 1).astype(np.float32))

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
            if engine == "piper":
                return self._synth_piper(plain)
            if engine == "speechify" and self.cfg["speechify_api_key"] and time.time() > getattr(self, "_speechify_pause", 0):
                return np.concatenate(list(self._speechify_gen(plain)) or [np.zeros(0, dtype=np.float32)])
        except Exception as e:
            import logging
            logging.getLogger("tars").warning("voce %s: %s (trec pe Edge)", engine, e)
        return self._synth_edge(text)

    def play_stream(self, gen) -> bool:
        """Redă bucăți de sunet pe măsură ce sosesc. False dacă nu a venit nimic."""
        got = False
        try:
            with self.sd.OutputStream(samplerate=24000, channels=1, dtype="float32") as out:
                for chunk in gen:
                    if self._stop.is_set():
                        break
                    if len(chunk):
                        got = True
                        out.write(chunk.reshape(-1, 1))
        except Exception as e:
            import logging
            logging.getLogger("tars").warning("redare în flux: %s", e)
        return got

    def _synth_stream(self, text: str):
        """Generator de sunet în flux pentru Gemini/Speechify; None pentru celelalte motoare."""
        if (self.cfg["tts_engine"] == "speechify" and self.cfg["speechify_api_key"]
                and time.time() > getattr(self, "_speechify_pause", 0)):
            try:
                return self._speechify_gen(text.replace("⟦", "").replace("⟧", ""))
            except Exception as e:
                import logging
                logging.getLogger("tars").warning("speechify: %s", e)
                return None
        if not (self.cfg["tts_engine"] == "gemini" and self.cfg["gemini_api_key"]
                and time.time() > getattr(self, "_gemini_pause", 0)):
            return None
        plain = text.replace("⟦", "").replace("⟧", "")
        import base64, json
        body = json.dumps(self._gemini_body(plain)).encode("utf-8")
        model = self.cfg["gemini_tts_model"]
        conn, r = GEMINI_POOL.post(
            f"/v1beta/models/{model}:streamGenerateContent?alt=sse", body,
            {"Content-Type": "application/json", "x-goog-api-key": self.cfg["gemini_api_key"].strip()})
        if r.status != 200:
            raw = r.read()
            GEMINI_POOL.release(conn)
            if r.status == 429:
                self._gemini_pause = time.time() + 900
            if self._lang_rejected(r.status, raw):
                return self._synth_stream(text)
            return None

        def gen():
            ok = True
            try:
                for raw in r:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    d = json.loads(line[5:])
                    for p in d.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                        if "inlineData" in p:
                            pcm = base64.b64decode(p["inlineData"]["data"])
                            yield np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            except Exception:
                ok = False
            finally:
                GEMINI_POOL.release(conn, ok)
        return gen()

    def _gemini_body(self, text: str, variant: str | None = None) -> dict:
        """Variante (alegeți în Setări după test):
        studio = exact ca în AI Studio; lang = + cod de limbă ro-RO; nota = + notă de regie în română."""
        v = variant or self.cfg["voice_variant"]
        prefix = ""
        if v == "nota":
            prefix = ("## Notă pentru actor:\nVorbitor nativ român din România. Accent românesc curat, "
                      "fără nicio urmă de accent englezesc. Ton calm, cald, firesc.\n\n")
        body = {
            "contents": [{"parts": [{"text": prefix + "## Transcript:\n" + text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "temperature": 1.35 if v == "studio" else 1.0,
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.cfg["gemini_voice"]}}},
            },
        }
        if v in ("lang", "nota") and not getattr(self, "_no_lang", False):
            body["generationConfig"]["speechConfig"]["languageCode"] = "ro-RO"
        return body

    def synth_variant(self, text: str, variant: str):
        """Pentru testul A/B din Setări: aceeași frază, altă variantă de cerere."""
        import base64, json
        conn, r = GEMINI_POOL.post(
            f"/v1beta/models/{self.cfg['gemini_tts_model']}:generateContent",
            json.dumps(self._gemini_body(text, variant)).encode("utf-8"),
            {"Content-Type": "application/json", "x-goog-api-key": self.cfg["gemini_api_key"].strip()})
        raw = r.read()
        GEMINI_POOL.release(conn)
        if r.status != 200:
            raise RuntimeError(f"Gemini {r.status}: {raw[:200]!r}")
        part = json.loads(raw)["candidates"][0]["content"]["parts"][0]["inlineData"]
        return np.frombuffer(base64.b64decode(part["data"]), dtype=np.int16).astype(np.float32) / 32768.0

    def _lang_rejected(self, status, raw: bytes) -> bool:
        if status == 400 and b"anguage" in raw and not getattr(self, "_no_lang", False):
            self._no_lang = True
            return True
        return False

    def _voice_key(self):
        e = self.cfg["tts_engine"]
        if e == "gemini":
            return f"gemini_{self.cfg['gemini_voice']}"
        if e == "piper":
            return f"piper_{self.cfg['piper_voice']}_{self.cfg['piper_speaker']}_{self.cfg['piper_speed']}"
        if e == "speechify":
            return f"speechify_{self.cfg['speechify_voice']}"
        return f"{e}_{self.cfg['tts_voice']}"

    # ---- confirmări instant („Imediat, domnule.”), sintetizate o dată și ținute pe disc ----
    def filler(self):
        import random
        key = self._voice_key()
        cache = getattr(self, "_fillers", {})
        if cache.get("key") != key:
            return None
        return random.choice(cache["audio"]) if cache.get("audio") else None

    def prepare_fillers(self, data_dir):
        key = self._voice_key()
        path = data_dir / f"confirmari_v3_{re.sub(r'[^A-Za-z0-9_-]', '_', key)}.npz"  # v3: regenerate în română
        audio = []
        try:
            if path.exists():
                z = np.load(path)
                audio = [z[k] for k in z.files]
            else:
                for f in FILLERS:
                    audio.append(_trim_silence(self._synth(f)))
                np.savez(path, *audio)
        except Exception as e:
            import logging
            logging.getLogger("tars").warning("confirmări: %s", e)
        self._fillers = {"key": key, "audio": audio}

    def style_prompt(self) -> str:
        return VOICE_STYLE.format(accent=self.cfg["voice_accent"], ton=self.cfg["voice_tone"])

    def _synth_gemini(self, text: str):
        import base64, json, urllib.request, urllib.error
        body = self._gemini_body(text)
        try:  # drumul rapid: conexiune deja deschisă
            conn, r = GEMINI_POOL.post(
                f"/v1beta/models/{self.cfg['gemini_tts_model']}:generateContent", json.dumps(body).encode("utf-8"),
                {"Content-Type": "application/json", "x-goog-api-key": self.cfg["gemini_api_key"].strip()})
            raw = r.read()
            GEMINI_POOL.release(conn)
            if self._lang_rejected(r.status, raw):
                return self._synth_gemini(text)
            if r.status == 200:
                part = json.loads(raw)["candidates"][0]["content"]["parts"][0]["inlineData"]
                return np.frombuffer(base64.b64decode(part["data"]), dtype=np.int16).astype(np.float32) / 32768.0
            if r.status == 429:
                self._gemini_pause = time.time() + 900
                raise RuntimeError("limita Gemini")
        except RuntimeError:
            raise
        except Exception:
            pass
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

    # ---- Speechify (Simba 3.5): română, în flux, PCM 24 kHz ----
    def _speechify_voice(self):
        want = (self.cfg["speechify_voice"] or "alexandru").strip()
        cache = getattr(self, "_sp_voice", None)
        if cache and cache[0] == want:
            return cache[1]
        import json, urllib.request
        vid = want
        try:
            req = urllib.request.Request("https://api.speechify.ai/v1/voices", headers={
                "Authorization": "Bearer " + self.cfg["speechify_api_key"].strip()})
            with urllib.request.urlopen(req, timeout=15) as r:
                voices = json.loads(r.read())
            if isinstance(voices, dict):
                voices = voices.get("voices") or voices.get("data") or []
            match = [v for v in voices if want.lower() in
                     {str(v.get("id", "")).lower(), str(v.get("display_name", v.get("name", ""))).lower()}]
            ro = [v for v in match if "ro-ro" in json.dumps(v).lower() or '"ro"' in json.dumps(v).lower()]
            if ro or match:
                vid = (ro or match)[0]["id"]
        except Exception as e:
            import logging
            logging.getLogger("tars").warning("speechify voci: %s", e)
        self._sp_voice = (want, vid)
        return vid

    def _speechify_gen(self, text: str):
        import json
        body = json.dumps({"input": text, "voice_id": self._speechify_voice(),
                           "model": self.cfg["speechify_model"] or "simba-3.5",
                           "language": "ro-RO"}).encode("utf-8")
        conn, r = SPEECHIFY_POOL.post("/v1/audio/stream", body, {
            "Content-Type": "application/json", "Accept": "audio/pcm",
            "Authorization": "Bearer " + self.cfg["speechify_api_key"].strip()})
        if r.status != 200:
            raw = r.read()
            SPEECHIFY_POOL.release(conn)
            if r.status in (402, 429):
                self._speechify_pause = time.time() + 900
            raise RuntimeError(f"Speechify {r.status}: {raw[:200]!r}")

        def gen():
            ok, rest = True, b""
            try:
                while True:
                    b = r.read(4800)
                    if not b:
                        break
                    b = rest + b
                    n = len(b) - len(b) % 2
                    rest = b[n:]
                    if n:
                        yield np.frombuffer(b[:n], dtype="<i2").astype(np.float32) / 32768.0
            except Exception:
                ok = False
            finally:
                SPEECHIFY_POOL.release(conn, ok)
        return gen()

    # ---- Piper: voce locală (ONNX), pe procesor, fără internet ----
    def _piper(self):
        name = (self.cfg["piper_voice"] or "ro_RO-mihai-medium").strip()
        cur = getattr(self, "_piper_voice", None)
        if cur and cur[0] == name:
            return cur[1]
        with _PIPER_LOCK:
            cur = getattr(self, "_piper_voice", None)
            if cur and cur[0] == name:
                return cur[1]
            from piper import PiperVoice
            path = piper_model_path(name)
            voice = PiperVoice.load(str(path))
            self._piper_voice = (name, voice)
            return voice

    def _synth_piper(self, text: str):
        from piper import SynthesisConfig
        voice = self._piper()
        try:
            speed = float(str(self.cfg["piper_speed"] or 1.1).replace(",", "."))
        except ValueError:
            speed = 1.1
        sid = str(self.cfg["piper_speaker"] or "").strip()
        conf = SynthesisConfig(speaker_id=int(sid) if sid.isdigit() else None,
                               length_scale=max(0.6, min(1.6, speed)), noise_scale=0.6, noise_w_scale=0.7)
        chunks = list(voice.synthesize(text, conf))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        a = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        sr = chunks[0].sample_rate
        if sr != 24000:  # tot TARS redă la 24 kHz
            n = int(round(len(a) * 24000 / sr))
            a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
        return a

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
            com = edge_tts.Communicate(t.strip(), voice, rate=_edge_val(self.cfg["tts_rate"], "%"),
                                       pitch=_edge_val(self.cfg["tts_pitch_en"] if en else self.cfg["tts_pitch"], "Hz"))
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
