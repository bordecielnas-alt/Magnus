"""Agenda CalDAV (Radicale, Nextcloud, iCloud avec mot de passe d'application…) : lire et ajouter.

Un seul calendrier, donné par son URL (ex. http://192.168.1.2:5232/benjamin/agenda/). Identifiant dans
la config, mot de passe dans le fichier de secrets (AGENDA_MOT_DE_PASSE), jamais dans config.yaml.
Lecture : REPORT calendar-query sur une plage (RFC 4791). Ajout : PUT d'un .ics (un VEVENT, avec rappel).
Lecture des iCalendar volontairement simple : DTSTART/DTEND (date, UTC, TZID), SUMMARY, LOCATION.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

LOCAL = datetime.now().astimezone().tzinfo


def _deplier(ics: str) -> list[str]:
    """Lignes iCalendar « pliées » (suite commençant par un espace) remises bout à bout."""
    lignes: list[str] = []
    for l in ics.replace("\r\n", "\n").split("\n"):
        if l[:1] in (" ", "\t") and lignes:
            lignes[-1] += l[1:]
        elif l:
            lignes.append(l)
    return lignes


def _quand(prop: str, valeur: str) -> tuple[datetime, bool]:
    """(début en heure locale, journée entière ?)"""
    if "VALUE=DATE" in prop.upper() or re.fullmatch(r"\d{8}", valeur):
        d = datetime.strptime(valeur[:8], "%Y%m%d")
        return d.replace(tzinfo=LOCAL), True
    m = re.search(r"TZID=([^;:]+)", prop)
    dt = datetime.strptime(valeur.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if valeur.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc)
    elif m:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(m.group(1)))
        except ZoneInfoNotFoundError:
            dt = dt.replace(tzinfo=LOCAL)
    else:
        dt = dt.replace(tzinfo=LOCAL)
    return dt.astimezone(LOCAL), False


def _texte(v: str) -> str:
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def lire_evenements(ics: str) -> list[dict]:
    evts, cur = [], None
    for l in _deplier(ics):
        if l == "BEGIN:VEVENT":
            cur = {}
        elif l == "END:VEVENT" and cur is not None:
            if "debut" in cur:
                evts.append(cur)
            cur = None
        elif cur is not None and ":" in l:
            prop, val = l.split(":", 1)
            nom = prop.split(";", 1)[0].upper()
            if nom == "DTSTART":
                cur["debut"], cur["journee"] = _quand(prop, val)
            elif nom == "DTEND":
                cur["fin"] = _quand(prop, val)[0]
            elif nom == "SUMMARY":
                cur["titre"] = _texte(val)
            elif nom == "LOCATION":
                cur["lieu"] = _texte(val)
            elif nom == "UID":
                cur["uid"] = val
    return evts


def _ics_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ics_evenement(titre: str, debut: datetime, fin: datetime, lieu: str = "", rappel_min: int = 15,
                  journee: bool = False) -> tuple[str, str]:
    uid = f"{uuid.uuid4()}@assistant"
    echapper = lambda t: t.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")  # noqa: E731
    if journee:
        dates = [f"DTSTART;VALUE=DATE:{debut:%Y%m%d}", f"DTEND;VALUE=DATE:{(debut + timedelta(days=1)):%Y%m%d}"]
    else:
        dates = [f"DTSTART:{_ics_utc(debut)}", f"DTEND:{_ics_utc(fin)}"]
    lignes = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//assistant//agenda//FR", "BEGIN:VEVENT",
              f"UID:{uid}", f"DTSTAMP:{_ics_utc(datetime.now(timezone.utc))}", *dates, f"SUMMARY:{echapper(titre)}"]
    if lieu:
        lignes.append(f"LOCATION:{echapper(lieu)}")
    if rappel_min and not journee:
        lignes += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{echapper(titre)}",
                   f"TRIGGER:-PT{int(rappel_min)}M", "END:VALARM"]
    lignes += ["END:VEVENT", "END:VCALENDAR"]
    return uid, "\r\n".join(lignes) + "\r\n"


class Agenda:
    def __init__(self, url: str, utilisateur: str = "", mot_de_passe: str = "", timeout: float = 10,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url.rstrip("/") + "/" if url else ""
        self.auth = (utilisateur, mot_de_passe) if utilisateur else None
        self.timeout, self._transport = timeout, transport

    @property
    def configure(self) -> bool:
        return bool(self.url)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(auth=self.auth, timeout=self.timeout, transport=self._transport)

    async def evenements(self, debut: datetime, fin: datetime) -> list[dict]:
        corps = f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-data/></d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
    <c:time-range start="{_ics_utc(debut)}" end="{_ics_utc(fin)}"/>
  </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""
        async with self._client() as c:
            r = await c.request("REPORT", self.url, content=corps.encode(),
                                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
        if r.status_code == 401:
            raise PermissionError("agenda : identifiant ou mot de passe refusé")
        r.raise_for_status()
        donnees = re.findall(r"<[^>]*calendar-data[^>]*>(.*?)</[^>]*calendar-data>", r.text, re.S)
        import html
        evts = [e for d in donnees for e in lire_evenements(html.unescape(d))]
        # récurrences non développées : on ne garde que ce qui tombe dans la plage demandée
        evts = [e for e in evts if e["debut"] < fin and e.get("fin", e["debut"]) >= debut]
        return sorted(evts, key=lambda e: e["debut"])

    async def ajouter(self, titre: str, debut: datetime, fin: datetime | None = None, lieu: str = "",
                      rappel_min: int = 15, journee: bool = False) -> str:
        fin = fin or debut + timedelta(hours=1)
        uid, ics = ics_evenement(titre, debut, fin, lieu, rappel_min, journee)
        async with self._client() as c:
            r = await c.put(self.url + uid.split("@")[0] + ".ics", content=ics.encode(),
                            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"})
        if r.status_code == 401:
            raise PermissionError("agenda : identifiant ou mot de passe refusé")
        r.raise_for_status()
        return uid


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def decrire(e: dict, aujourd_hui: date | None = None) -> str:
    j = aujourd_hui or datetime.now(LOCAL).date()
    d = e["debut"].date()
    jour = "aujourd'hui" if d == j else "demain" if d == j + timedelta(days=1) else f"{JOURS[d.weekday()]} {d.day}"
    heure = "" if e.get("journee") else f" à {e['debut']:%H:%M}".replace(":", "h")
    lieu = f" ({e['lieu']})" if e.get("lieu") else ""
    return f"{jour}{heure} : {e.get('titre', 'sans titre')}{lieu}"
