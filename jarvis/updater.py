"""Actualizare automată din GitHub: verifică, descarcă, înlocuiește codul, repornește.
Datele tale (date/), mediul Python (.venv/) și modelele (modele/) nu sunt atinse."""
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from .config import ROOT, DATA_DIR

log = logging.getLogger("tars")

PROTECTED = {"date", ".venv", "modele", ".git", "__pycache__"}
VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"


def local_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "0"


def _vkey(v: str):
    out = []
    for part in v.replace("-", ".").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return out


class Updater:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.remote = None
        self.notes = ""
        self.last_check = 0.0
        self.busy = False

    # ------------------------------------------------------------ rețea
    def _req(self, url: str, raw: bool = False, timeout: int = 30) -> bytes:
        headers = {"User-Agent": "TARS-updater", "X-GitHub-Api-Version": "2022-11-28"}
        if raw:
            headers["Accept"] = "application/vnd.github.raw"
        token = (self.cfg.get("github_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.read()

    def _file(self, name: str) -> str:
        repo, br = self.cfg["update_repo"], self.cfg["update_branch"]
        return self._req(f"https://api.github.com/repos/{repo}/contents/{name}?ref={br}", raw=True).decode("utf-8")

    @property
    def configured(self) -> bool:
        return bool((self.cfg.get("update_repo") or "").strip())

    # ------------------------------------------------------------ verificare
    def check(self) -> bool:
        """True dacă există o versiune mai nouă."""
        self.last_check = time.time()
        if not self.configured:
            return False
        self.remote = self._file("VERSION").strip()
        try:
            self.notes = self._file("NOUTATI.txt")
        except Exception:
            self.notes = ""
        return _vkey(self.remote) > _vkey(local_version())

    def latest_notes(self) -> str:
        """Prima secțiune din NOUTATI.txt (ce s-a schimbat în ultima versiune)."""
        blocks = [b.strip() for b in (self.notes or "").split("\n\n") if b.strip()]
        return blocks[0] if blocks else ""

    # ------------------------------------------------------------ aplicare
    def apply(self) -> str:
        """Descarcă și instalează. Întoarce versiunea nouă. Aruncă excepție dacă nu merge."""
        self.busy = True
        try:
            repo, br = self.cfg["update_repo"], self.cfg["update_branch"]
            data = self._req(f"https://api.github.com/repos/{repo}/zipball/{br}", timeout=180)
            zf = zipfile.ZipFile(io.BytesIO(data))
            top = zf.namelist()[0].split("/")[0]
            new_version = zf.read(f"{top}/VERSION").decode("utf-8").strip()
            old_req = (ROOT / "requirements.txt").read_bytes() if (ROOT / "requirements.txt").exists() else b""

            backup = DATA_DIR / "backup" / local_version()
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            backup.mkdir(parents=True, exist_ok=True)

            written = []
            try:
                for info in zf.infolist():
                    rel = info.filename[len(top) + 1:]
                    if not rel or info.is_dir():
                        continue
                    first = rel.split("/")[0]
                    if first in PROTECTED:
                        continue
                    dest = ROOT / rel
                    if dest.exists():
                        (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dest, backup / rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_name(dest.name + ".nou")
                    tmp.write_bytes(zf.read(info))
                    os.replace(tmp, dest)
                    written.append(rel)
            except Exception:
                # ceva a mers prost la jumătate: punem totul la loc
                for rel in written:
                    src = backup / rel
                    if src.exists():
                        shutil.copy2(src, ROOT / rel)
                    else:
                        (ROOT / rel).unlink(missing_ok=True)
                raise

            if (ROOT / "requirements.txt").read_bytes() != old_req:
                self.store.set_status("gandeste", "instalez pachetele noi")
                self._pip()
            self.ensure_models()
            log.info("actualizat %s -> %s (%d fișiere)", local_version(), new_version, len(written))
            (DATA_DIR / "actualizare.json").write_text(json.dumps({
                "from": str(backup.name), "to": new_version, "notes": self.latest_notes(), "ts": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
            return new_version
        finally:
            self.busy = False

    def _pip(self):
        py = Path(sys.executable)
        if py.name.lower() == "pythonw.exe":
            py = py.with_name("python.exe")
        flags = 0x08000000 if os.name == "nt" else 0  # fără fereastră neagră
        subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                       check=True, creationflags=flags, timeout=1800)

    def ensure_models(self):
        """Modelul pentru „TARS” nu stă în GitHub (e mare); îl descărcăm dacă lipsește."""
        target = ROOT / "modele" / "vosk-model-small-en-us-0.15"
        if target.exists():
            return
        data = urllib.request.urlopen(urllib.request.Request(VOSK_URL, headers={"User-Agent": "TARS"}), timeout=300).read()
        zipfile.ZipFile(io.BytesIO(data)).extractall(ROOT / "modele")

    # ------------------------------------------------------------ repornire
    @staticmethod
    def restart():
        """Pornește o copie nouă care așteaptă să se închidă asta, apoi ieșim."""
        args = [sys.executable, str(ROOT / "main.py"), "--dupa-actualizare", str(os.getpid())]
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen(args, cwd=str(ROOT), creationflags=flags, close_fds=True)
        os._exit(0)


def wait_for_pid(pid: int, timeout: float = 20.0):
    """Folosit la pornire după actualizare: așteaptă să se închidă versiunea veche."""
    end = time.time() + timeout
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if h:
            ctypes.windll.kernel32.WaitForSingleObject(h, int(timeout * 1000))
            ctypes.windll.kernel32.CloseHandle(h)
        return
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.3)


def pending_announcement() -> dict | None:
    """După repornire: ce s-a actualizat (o singură dată)."""
    p = DATA_DIR / "actualizare.json"
    if not p.exists():
        return None
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        info = None
    p.unlink(missing_ok=True)
    return info
