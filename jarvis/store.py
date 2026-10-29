"""Tot ce ține minte TARS între porniri: taskuri, jurnal, conversație, notițe."""
import json
import threading
import uuid
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from .config import DATA_DIR

TZ = ZoneInfo("Europe/Bucharest")
STORE_PATH = DATA_DIR / "jarvis_date.json"


def now() -> datetime:
    return datetime.now(TZ)


def today_str() -> str:
    return now().date().isoformat()


class Store:
    def __init__(self):
        self._lock = threading.RLock()
        self.data = {
            "tasks": [],        # {id, text, date, time, done, done_at, created, source, reminded}
            "log": [],          # {ts, text, kind}
            "chat": [],         # {ts, role, text}
            "notes": [],        # {ts, text}  - lucruri de ținut minte
            "briefing": {"date": "", "text": "", "ts": ""},
            "last_seen": "",    # ultima dată când a rulat TARS
            "projects": None,   # {id, name, domain, objective, audience, resources, stage, progress, next, notes[], analyses[]}
            "research": [],     # {id, ts, query, text, status}
        }
        if STORE_PATH.exists():
            try:
                self.data.update(json.loads(STORE_PATH.read_text(encoding="utf-8")))
            except Exception:
                backup = STORE_PATH.with_suffix(".corupt.json")
                STORE_PATH.replace(backup)
        if self.data.get("projects") is None:
            from .seed import SEED_PROJECTS
            self.data["projects"] = [dict(p, id=uuid.uuid4().hex[:6], notes=[], analyses=[],
                                          updated=now().isoformat(timespec="seconds"))
                                     for p in SEED_PROJECTS]
        # Status live (nu se salvează)
        self.status = "pornire"
        self.status_detail = ""
        self.heard = ""
        self.version = 0

    # ---------- persistență ----------
    def save(self):
        with self._lock:
            self.version += 1
            # păstrăm fișierul mic
            self.data["log"] = self.data["log"][-400:]
            self.data["chat"] = self.data["chat"][-200:]
            cutoff = (now().date() - timedelta(days=30)).isoformat()
            self.data["tasks"] = [
                t for t in self.data["tasks"]
                if not (t.get("done") and (t.get("date") or "9999") < cutoff)
            ]
            tmp = STORE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(STORE_PATH)

    def set_status(self, status: str, detail: str = ""):
        self.status = status
        self.status_detail = detail
        self.version += 1

    # ---------- jurnal ----------
    def log(self, text: str, kind: str = "info"):
        with self._lock:
            self.data["log"].append({"ts": now().isoformat(timespec="seconds"), "text": text, "kind": kind})
            self.save()

    def log_since(self, since: datetime):
        out = []
        for e in self.data["log"]:
            try:
                if datetime.fromisoformat(e["ts"]) >= since:
                    out.append(e)
            except Exception:
                pass
        return out

    # ---------- conversație ----------
    def chat(self, role: str, text: str):
        with self._lock:
            self.data["chat"].append({"ts": now().isoformat(timespec="seconds"), "role": role, "text": text})
            self.save()

    # ---------- taskuri ----------
    def add_task(self, text: str, day: str | None = None, time: str | None = None, source: str = "voce") -> dict:
        with self._lock:
            task = {
                "id": uuid.uuid4().hex[:6],
                "text": text.strip(),
                "date": day or today_str(),
                "time": time or "",
                "done": False,
                "done_at": "",
                "created": now().isoformat(timespec="seconds"),
                "source": source,
                "reminded": False,
            }
            self.data["tasks"].append(task)
            self.save()
            return task

    def find_task(self, ref: str) -> dict | None:
        ref = (ref or "").strip().lower()
        if not ref:
            return None
        for t in self.data["tasks"]:
            if t["id"] == ref:
                return t
        # potrivire după text, preferând taskurile nefăcute
        candidates = [t for t in self.data["tasks"] if ref in t["text"].lower()]
        candidates.sort(key=lambda t: (t["done"], t["date"]))
        return candidates[0] if candidates else None

    def set_done(self, ref: str, done: bool = True) -> dict | None:
        with self._lock:
            t = self.find_task(ref)
            if not t:
                return None
            t["done"] = done
            t["done_at"] = now().isoformat(timespec="seconds") if done else ""
            self.save()
            return t

    def delete_task(self, ref: str) -> dict | None:
        with self._lock:
            t = self.find_task(ref)
            if t:
                self.data["tasks"].remove(t)
                self.save()
            return t

    def update_task(self, ref: str, text=None, day=None, time=None) -> dict | None:
        with self._lock:
            t = self.find_task(ref)
            if not t:
                return None
            if text:
                t["text"] = text
            if day:
                t["date"] = day
            if time is not None:
                t["time"] = time
                t["reminded"] = False
            self.save()
            return t

    def tasks_for(self, day: str) -> list:
        return sorted(
            [t for t in self.data["tasks"] if t["date"] == day],
            key=lambda t: (t["done"], t["time"] or "99:99"),
        )

    def overdue(self) -> list:
        td = today_str()
        return [t for t in self.data["tasks"] if not t["done"] and t["date"] < td]

    def upcoming(self, days: int = 7) -> list:
        td = now().date()
        end = (td + timedelta(days=days)).isoformat()
        return sorted(
            [t for t in self.data["tasks"] if not t["done"] and td.isoformat() < t["date"] <= end],
            key=lambda t: (t["date"], t["time"] or "99:99"),
        )

    # ---------- notițe ----------
    def add_note(self, text: str):
        with self._lock:
            self.data["notes"].append({"ts": now().isoformat(timespec="seconds"), "text": text})
            self.save()

    def remove_note(self, ref: str) -> bool:
        with self._lock:
            ref = ref.lower()
            for n in self.data["notes"]:
                if ref in n["text"].lower():
                    self.data["notes"].remove(n)
                    self.save()
                    return True
            return False

    # ---------- proiecte ----------
    def find_project(self, ref: str) -> dict | None:
        ref = (ref or "").strip().lower()
        if not ref:
            return None
        for p in self.data["projects"]:
            if p["id"] == ref or p["name"].lower() == ref:
                return p
        for p in self.data["projects"]:
            hay = (p["name"] + " " + p.get("domain", "") + " " + " ".join(p.get("aliases", []))).lower()
            if ref in hay or any(w in hay for w in ref.split() if len(w) > 3):
                return p
        return None

    def add_project(self, **fields) -> dict:
        with self._lock:
            p = {"id": uuid.uuid4().hex[:6], "name": "", "domain": "", "objective": "", "audience": "",
                 "resources": "", "stage": "Idee", "progress": 0, "next": "", "aliases": [],
                 "notes": [], "analyses": [], "updated": now().isoformat(timespec="seconds")}
            p.update({k: v for k, v in fields.items() if v not in (None, "")})
            self.data["projects"].append(p)
            self.save()
            return p

    def update_project(self, ref: str, **fields) -> dict | None:
        with self._lock:
            p = self.find_project(ref)
            if not p:
                return None
            note = fields.pop("note", None)
            for k, v in fields.items():
                if v not in (None, ""):
                    p[k] = v
            if note:
                p["notes"].append({"ts": now().isoformat(timespec="seconds"), "text": note})
                p["notes"] = p["notes"][-50:]
            p["updated"] = now().isoformat(timespec="seconds")
            self.save()
            return p

    def add_analysis(self, project_id: str, analysis: dict):
        with self._lock:
            for p in self.data["projects"]:
                if p["id"] == project_id:
                    p["analyses"] = [a for a in p["analyses"] if a.get("id") != analysis["id"]]
                    p["analyses"].append(analysis)
                    p["analyses"] = p["analyses"][-6:]
                    self.save()
                    return

    # ---------- cercetare ----------
    def upsert_research(self, item: dict):
        with self._lock:
            self.data["research"] = [r for r in self.data["research"] if r["id"] != item["id"]]
            self.data["research"].append(item)
            self.data["research"] = self.data["research"][-30:]
            self.save()

    # ---------- briefing ----------
    def set_briefing(self, text: str):
        with self._lock:
            self.data["briefing"] = {"date": today_str(), "text": text, "ts": now().isoformat(timespec="seconds")}
            self.save()
