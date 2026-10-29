"""Surse de date: vreme, mail, calendar, știri. Toate doar citire."""
import email
import email.header
import email.utils
import imaplib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date

from .store import TZ, now

UA = {"User-Agent": "Mozilla/5.0 (TARS asistent personal)"}


def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ------------------------------------------------------------------ vreme
WMO = {
    0: "senin", 1: "predominant senin", 2: "parțial înnorat", 3: "înnorat",
    45: "ceață", 48: "ceață cu chiciură", 51: "burniță slabă", 53: "burniță", 55: "burniță densă",
    61: "ploaie slabă", 63: "ploaie", 65: "ploaie puternică", 66: "ploaie înghețată", 67: "ploaie înghețată",
    71: "ninsoare slabă", 73: "ninsoare", 75: "ninsoare puternică", 77: "grăunțe de zăpadă",
    80: "averse slabe", 81: "averse", 82: "averse puternice", 85: "averse de ninsoare", 86: "averse de ninsoare",
    95: "furtună", 96: "furtună cu grindină", 99: "furtună cu grindină",
}


def geocode(city: str):
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": city, "count": 1, "language": "ro", "country": "RO"})
    data = json.loads(_get(url))
    if data.get("results"):
        r = data["results"][0]
        return r["latitude"], r["longitude"], r["name"]
    return None


def weather(lat: float, lon: float, days: int = 2) -> dict:
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "Europe/Bucharest",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
        "past_days": 1, "forecast_days": days,
    })
    d = json.loads(_get(url))
    cur = d.get("current", {})
    out = {
        "acum": {
            "temperatura": cur.get("temperature_2m"),
            "se_simte_ca": cur.get("apparent_temperature"),
            "cer": WMO.get(cur.get("weather_code"), "?"),
            "vant_kmh": cur.get("wind_speed_10m"),
        },
        "zile": [],
    }
    dd = d.get("daily", {})
    for i, day in enumerate(dd.get("time", [])):
        out["zile"].append({
            "data": day,
            "min": dd["temperature_2m_min"][i],
            "max": dd["temperature_2m_max"][i],
            "cer": WMO.get(dd["weather_code"][i], "?"),
            "sanse_precipitatii_pct": dd["precipitation_probability_max"][i],
            "precipitatii_mm": dd["precipitation_sum"][i],
        })
    return out


def weather_short(lat, lon) -> dict:
    """Pentru fereastră: temperatură + cer."""
    w = weather(lat, lon, 1)
    today = w["zile"][-1] if w["zile"] else {}
    return {"temp": w["acum"]["temperatura"], "cer": w["acum"]["cer"],
            "min": today.get("min"), "max": today.get("max")}


# ------------------------------------------------------------------ mail
def _dec(value) -> str:
    if not value:
        return ""
    parts = []
    for text, enc in email.header.decode_header(value):
        if isinstance(text, bytes):
            try:
                parts.append(text.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                parts.append(text.decode("utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _body_snippet(msg, limit=400) -> str:
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True) or b""
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                break
        if not text:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True) or b""
                    text = re.sub(r"<[^>]+>", " ", payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
                    break
    else:
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def mail_since(address: str, app_password: str, since: datetime, max_items: int = 30) -> list:
    """Mailurile primite de la `since` încoace. Folosește doar citire (nu le marchează citite)."""
    if not address or not app_password:
        raise RuntimeError("Mailul nu e configurat (adresă + parolă de aplicație Google în Setări).")
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        M.login(address, app_password.replace(" ", ""))
        M.select("INBOX", readonly=True)
        since_imap = (since - timedelta(days=1)).strftime("%d-%b-%Y")
        typ, ids = M.search(None, f'(SINCE "{since_imap}")')
        id_list = ids[0].split()[-max_items * 2:]
        out = []
        for mid in reversed(id_list):
            typ, data = M.fetch(mid, "(BODY.PEEK[] FLAGS)")
            if not data or not isinstance(data[0], tuple):
                continue
            flags = data[0][0].decode(errors="ignore")
            msg = email.message_from_bytes(data[0][1])
            try:
                dt = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone(TZ)
            except Exception:
                dt = None
            if dt and dt < since:
                continue
            out.append({
                "de_la": _dec(msg.get("From")),
                "subiect": _dec(msg.get("Subject")),
                "ora": dt.strftime("%d.%m %H:%M") if dt else "",
                "citit": "\\Seen" in flags,
                "inceput": _body_snippet(msg),
            })
            if len(out) >= max_items:
                break
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


# ------------------------------------------------------------------ calendar
def calendar_events(ics_url: str, start: datetime, end: datetime) -> list:
    if not ics_url:
        return []
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(_get(ics_url, timeout=20))
    events = recurring_ical_events.of(cal).between(start, end)
    out = []
    for ev in events:
        s = ev.get("DTSTART").dt
        e = ev.get("DTEND").dt if ev.get("DTEND") else None
        all_day = not isinstance(s, datetime)
        if not all_day:
            s = s.astimezone(TZ) if s.tzinfo else s.replace(tzinfo=TZ)
            if e is not None and isinstance(e, datetime):
                e = e.astimezone(TZ) if e.tzinfo else e.replace(tzinfo=TZ)
        out.append({
            "titlu": str(ev.get("SUMMARY", "(fără titlu)")),
            "data": (s if all_day else s.date()).isoformat(),
            "ora": "" if all_day else s.strftime("%H:%M"),
            "pana_la": "" if all_day or not isinstance(e, datetime) else e.strftime("%H:%M"),
            "toata_ziua": all_day,
            "loc": str(ev.get("LOCATION", "") or ""),
        })
    out.sort(key=lambda x: (x["data"], x["ora"] or "00:00"))
    return out


# ------------------------------------------------------------------ știri
def news(topics: str = "", per_topic: int = 4, since: datetime | None = None) -> list:
    """Titluri din Google News (România). Fără topic = principalele știri."""
    feeds = [("Principale", "https://news.google.com/rss?hl=ro&gl=RO&ceid=RO:ro")]
    for t in [x.strip() for x in (topics or "").split(",") if x.strip()]:
        q = urllib.parse.quote(t + " when:1d")
        feeds.append((t, f"https://news.google.com/rss/search?q={q}&hl=ro&gl=RO&ceid=RO:ro"))
    out = []
    seen = set()
    for label, url in feeds:
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        n = 0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            key = title.lower()[:60]
            if not title or key in seen:
                continue
            pub = item.findtext("pubDate")
            try:
                dt = email.utils.parsedate_to_datetime(pub).astimezone(TZ)
            except Exception:
                dt = None
            if since and dt and dt < since:
                continue
            seen.add(key)
            out.append({"subiect": label, "titlu": title, "ora": dt.strftime("%d.%m %H:%M") if dt else ""})
            n += 1
            if n >= (per_topic + 3 if label == "Principale" else per_topic):
                break
    return out
