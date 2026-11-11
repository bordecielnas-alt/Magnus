"""Rapports à la demande : « fais-moi un rapport du CAC 40 », « rapport météo », « fais le point sur Docker ».

L'assistant répond par une phrase courte (dite à voix haute) et une carte de synthèse dessinée par la page
(`static/rapports.js`). Cinq cartes toutes faites vont vite et ne doivent rien à un modèle :

- météo : Open-Meteo (sans clé), icônes dessinées par la page selon le code météo ;
- bourse : cours d'un indice (CAC 40, DAX, Nasdaq…) avec sa courbe du mois, et l'analyse du jour par Claude ;
- agenda : les rendez-vous des 7 prochains jours (CalDAV) ;
- Docker : conteneurs en marche, arrêtés ou en souci, et l'état du serveur Unraid ;
- actualités : les titres du jour, cherchés sur le web par Claude.

Pour tout autre sujet (« rapport sur la Fed »), Claude cherche sur le web et remplit une carte libre
(chiffres clés, points, sources) au format JSON ; la carte n'affiche que ce qu'il a renvoyé, et rien
si c'est le modèle de secours (sans web) qui a répondu, pour ne rien inventer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

import httpx

journal = logging.getLogger("assistant.rapports")

MODELES = {
    "meteo": "Météo",
    "bourse": "Bourse",
    "agenda": "Agenda",
    "actus": "Actualités",
    "docker": "Docker",
    "libre": "Rapport",
}

# --- reconnaître une demande de rapport ---------------------------------------------------------------

RE_RAPPORT = re.compile(
    r"\b(rapport|synth[eè]se|bilan|tableau de bord|fais(?:[- ]moi)? (?:le|un) point|"
    r"carte (?:m[ée]t[ée]o|bourse|agenda|actus?|actualit[ée]s|docker))\b", re.I)
RE_PAS_RAPPORT = re.compile(r"\b(rapport (?:qualit[ée]|de force|avec)|par rapport|bilan de sant[ée])\b", re.I)
RE_METEO = re.compile(r"\b(m[ée]t[ée]o|temps qu.il|pr[ée]visions?|pluie|neige|temp[ée]ratures?)\b", re.I)
RE_BOURSE = re.compile(r"\b(cac|sbf|bourse|boursier|indices?|march[ée]s? financiers?|nasdaq|dow jones|s ?& ?p|dax|ftse|"
                       r"nikkei|euro ?stoxx|stoxx|bitcoin|btc|ethereum|crypto|actions?|cours)\b", re.I)
RE_AGENDA = re.compile(r"\b(agenda|rendez.vous|rdv|planning|calendrier|ma semaine|mes journ[ée]es?)\b", re.I)
RE_ACTUS = re.compile(r"\b(actus?|actualit[ée]s?|news|infos?|nouvelles|presse|journal)\b", re.I)
RE_DOCKER = re.compile(r"\b(docker|conteneurs?|containers?|unraid|serveur|homelab|nas)\b", re.I)
RE_SUJET = re.compile(r"\b(?:rapport|synth[eè]se|bilan|point|tableau de bord)\s+(?:complet\s+|rapide\s+|d[ée]taill[ée]\s+)?"
                      r"(?:(?:du|de la|de l'|des|de|d'|sur (?:le |la |les |l')?|concernant (?:le |la |les |l')?)\s*)?(?P<sujet>.+)$", re.I)
RE_LIEU = re.compile(r"\b(?:[àa]|pour|sur|de|du|en)\s+(?P<lieu>[A-Za-zÀ-ÿ][\w'’ -]{1,40}?)\s*[?.!]*$", re.I)
MOTS_PAS_LIEU = {"aujourd'hui", "demain", "la semaine", "ce soir", "ce week-end", "cette semaine", "la journee",
                 "la journée", "moi", "maison", "la maison", "chez moi", "temps", "meteo", "météo"}

# indice (ou valeur) -> (symbole Yahoo Finance, nom affiché)
INDICES = [
    (r"cac ?40|cac\b", "^FCHI", "CAC 40"),
    (r"sbf ?120", "^SBF120", "SBF 120"),
    (r"euro ?stoxx|stoxx ?50", "^STOXX50E", "Euro Stoxx 50"),
    (r"\bdax\b", "^GDAXI", "DAX"),
    (r"ftse", "^FTSE", "FTSE 100"),
    (r"nasdaq", "^IXIC", "Nasdaq"),
    (r"dow ?jones|\bdow\b", "^DJI", "Dow Jones"),
    (r"s ?& ?p|sp ?500|s and p", "^GSPC", "S&P 500"),
    (r"nikkei", "^N225", "Nikkei 225"),
    (r"bitcoin|\bbtc\b", "BTC-EUR", "Bitcoin"),
    (r"ethereum|\beth\b", "ETH-EUR", "Ethereum"),
]


@dataclass
class Demande:
    type: str           # meteo, bourse, agenda, actus, docker, libre
    sujet: str = ""     # lieu (météo), indice (bourse), sujet libre
    rapide: bool = False  # bourse : le cours et la courbe seulement, sans l'analyse de Claude (pas d'attente)


def _sans_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def detecter(message: str) -> Demande | None:
    """Demande de rapport, ou None. « fais-moi un rapport météo à Lyon » -> Demande("meteo", "Lyon")."""
    m = message.strip()
    if len(m) > 200 or not RE_RAPPORT.search(m) or RE_PAS_RAPPORT.search(m):
        return None
    if RE_METEO.search(m):
        return Demande("meteo", lieu_de(m))
    if RE_DOCKER.search(m):
        return Demande("docker")
    if RE_AGENDA.search(m):
        return Demande("agenda")
    if RE_ACTUS.search(m):
        sujet = re.sub(r"^(?:les |des )?(?:actus?|actualit[ée]s?|news|infos?|nouvelles)\b\s*(?:du jour|de la journ[ée]e)?\s*"
                       r"(?:(?:sur|de|du|des|concernant)\s+(?:la |le |les |l')?)?", "", sujet_de(m), flags=re.I)
        return Demande("actus", sujet.strip())
    if RE_BOURSE.search(m) or any(re.search(p, m, re.I) for p, _, _ in INDICES):
        return Demande("bourse", sujet_de(m))
    sujet = sujet_de(m)
    return Demande("libre", sujet) if sujet else None


def sujet_de(message: str) -> str:
    x = RE_SUJET.search(message.strip())
    return (x.group("sujet") if x else "").strip(" ?.!,;:")[:120]


def lieu_de(message: str) -> str:
    x = RE_LIEU.search(message.strip())
    if not x:
        return ""
    lieu = x.group("lieu").strip()
    lieu = re.sub(r"(?:\s+(?:aujourd.hui|apr[eè]s-demain|demain|ce soir|ce matin|cet apr[eè]s-midi|cette semaine|"
                  r"ce week-end|le week-end|(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)(?: prochain)?|"
                  r"s'il te pla[iî]t|stp))+$", "", lieu, flags=re.I).strip()
    if _sans_accents(lieu) in {_sans_accents(w) for w in MOTS_PAS_LIEU} or RE_METEO.fullmatch(lieu):
        return ""
    return lieu[:1].upper() + lieu[1:]


def indice_de(sujet: str) -> tuple[str, str] | None:
    for motif, symbole, nom in INDICES:
        if re.search(motif, sujet, re.I):
            return symbole, nom
    return None


# --- météo (Open-Meteo) ---------------------------------------------------------------------------------

# code WMO -> (libellé, icône dessinée par la page)
WMO = {
    0: ("ciel dégagé", "soleil"), 1: ("peu nuageux", "soleil-nuage"), 2: ("partiellement nuageux", "soleil-nuage"),
    3: ("couvert", "nuage"), 45: ("brouillard", "brouillard"), 48: ("brouillard givrant", "brouillard"),
    51: ("bruine légère", "bruine"), 53: ("bruine", "bruine"), 55: ("bruine forte", "bruine"),
    56: ("bruine verglaçante", "bruine"), 57: ("bruine verglaçante", "bruine"),
    61: ("pluie faible", "pluie"), 63: ("pluie", "pluie"), 65: ("forte pluie", "pluie"),
    66: ("pluie verglaçante", "pluie"), 67: ("pluie verglaçante", "pluie"),
    71: ("neige faible", "neige"), 73: ("neige", "neige"), 75: ("forte neige", "neige"), 77: ("grains de neige", "neige"),
    80: ("averses", "averse"), 81: ("averses", "averse"), 82: ("fortes averses", "averse"),
    85: ("averses de neige", "neige"), 86: ("fortes averses de neige", "neige"),
    95: ("orage", "orage"), 96: ("orage avec grêle", "orage"), 99: ("orage avec grêle", "orage"),
}
JOURS_COURTS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def ciel(code, jour: bool = True) -> tuple[str, str]:
    libelle, icone = WMO.get(int(code) if isinstance(code, (int, float)) else -1, ("—", "nuage"))
    if not jour and icone in ("soleil", "soleil-nuage"):
        icone = "lune" if icone == "soleil" else "lune-nuage"
    return libelle, icone


def _r(v) -> int | None:
    return round(v) if isinstance(v, (int, float)) else None


def carte_meteo(lieu: dict, f: dict) -> dict:
    """Carte météo d'après une réponse Open-Meteo (`f`) et le lieu géocodé."""
    cur = f.get("current") or {}
    jour = bool(cur.get("is_day", 1))
    libelle, icone = ciel(cur.get("weather_code"), jour)
    h = f.get("hourly") or {}
    heures = h.get("time") or []
    maintenant = (cur.get("time") or "")[:13]
    i0 = next((i for i, t in enumerate(heures) if t[:13] >= maintenant), 0) if maintenant else 0
    prochaines = []
    for i in range(i0, min(i0 + 24, len(heures)), 3):
        _, ic = ciel((h.get("weather_code") or [None] * len(heures))[i], bool((h.get("is_day") or [1] * len(heures))[i]))
        prochaines.append({"heure": heures[i][11:13] + "h", "temp": _r((h.get("temperature_2m") or [None] * len(heures))[i]),
                           "icone": ic, "pluie": _r((h.get("precipitation_probability") or [None] * len(heures))[i])})
    d = f.get("daily") or {}
    jours = []
    for i, t in enumerate(d.get("time") or []):
        lib, ic = ciel((d.get("weather_code") or [])[i] if i < len(d.get("weather_code") or []) else None)
        dt = date.fromisoformat(t)
        nom = "Aujourd'hui" if i == 0 else "Demain" if i == 1 else f"{JOURS_COURTS[dt.weekday()]} {dt.day}"
        val = lambda k: (d.get(k) or [None] * (i + 1))[i]  # noqa: E731
        jours.append({"jour": nom, "icone": ic, "ciel": lib, "min": _r(val("temperature_2m_min")),
                      "max": _r(val("temperature_2m_max")), "pluie": _r(val("precipitation_probability_max")),
                      "pluie_mm": val("precipitation_sum")})
    nom = lieu.get("nom", "")
    lieu_txt = ", ".join(x for x in (nom, lieu.get("region") or lieu.get("pays")) if x)
    temp, ressenti = _r(cur.get("temperature_2m")), _r(cur.get("apparent_temperature"))
    resume = f"À {nom}, {temp} °C" if temp is not None else f"À {nom}"
    if ressenti is not None and temp is not None and abs(ressenti - temp) >= 2:
        resume += f" (ressenti {ressenti})"
    resume += f", {libelle}."
    if jours:
        a = jours[0]
        resume += f" Aujourd'hui entre {a['min']} et {a['max']} °C"
        resume += f", {a['pluie']} % de risque de pluie." if a["pluie"] else ", pas de pluie prévue."
    if len(jours) > 1:
        b = jours[1]
        resume += f" Demain : {b['ciel']}, {b['min']} à {b['max']} °C."
    lever = ((d.get("sunrise") or [""])[0] or "")[11:16]
    coucher = ((d.get("sunset") or [""])[0] or "")[11:16]
    return {"type": "rapport", "modele": "meteo", "titre": f"Météo · {nom}", "sous_titre": lieu_txt,
            "resume": resume, "maj": datetime.now().astimezone().isoformat(timespec="minutes"),
            "source": {"titre": "Open-Meteo", "url": "https://open-meteo.com/"},
            "demande": {"type": "meteo", "sujet": nom},
            "meteo": {"temp": temp, "ressenti": ressenti, "ciel": libelle, "icone": icone,
                      "humidite": _r(cur.get("relative_humidity_2m")), "vent": _r(cur.get("wind_speed_10m")),
                      "lever": lever, "coucher": coucher, "heures": prochaines, "jours": jours}}


# --- bourse (Yahoo Finance, sans clé) -----------------------------------------------------------------------

def _pct(a, b) -> float | None:
    return round((a - b) / b * 100, 2) if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b else None


def _fr(v: float | None, dec: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{dec}f}".replace(",", " ").replace(".", ",")


def donnees_bourse(nom: str, symbole: str, chart: dict) -> dict:
    """Cours, variations et courbe d'après la réponse « chart » de Yahoo Finance (range=1mo, interval=1d)."""
    res = ((chart.get("chart") or {}).get("result") or [None])[0] or {}
    meta = res.get("meta") or {}
    closes = [c for c in (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []) if c is not None]
    cours = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    if cours is None:
        raise ValueError(f"pas de cours pour {symbole}")
    veille = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
    debut = closes[0] if closes else None
    var_j, var_m = _pct(cours, veille), _pct(cours, debut)
    devise = meta.get("currency") or ""
    unite = {"EUR": " €", "USD": " $", "GBP": " £", "JPY": " ¥"}.get(devise, "") if not symbole.startswith("^") else " pts"
    ts = meta.get("regularMarketTime")
    return {"nom": nom, "symbole": symbole, "cours": cours, "unite": unite.strip(), "var_jour": var_j, "var_mois": var_m,
            "haut_mois": max(closes) if closes else None, "bas_mois": min(closes) if closes else None,
            "serie": [round(c, 2) for c in closes][-31:],
            "heure": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes") if ts else ""}


def chiffres_bourse(b: dict) -> list[dict]:
    t = lambda v: "hausse" if (v or 0) > 0 else "baisse" if (v or 0) < 0 else "stable"  # noqa: E731
    s = lambda v: ("+" if (v or 0) > 0 else "") + _fr(v) + " %" if v is not None else "—"  # noqa: E731
    u = f" {b['unite']}" if b["unite"] else ""
    return [{"libelle": "Cours", "valeur": _fr(b["cours"]) + u, "tendance": ""},
            {"libelle": "Sur la journée", "valeur": s(b["var_jour"]), "tendance": t(b["var_jour"])},
            {"libelle": "Sur un mois", "valeur": s(b["var_mois"]), "tendance": t(b["var_mois"])},
            {"libelle": "Plus haut / bas du mois", "valeur": f"{_fr(b['haut_mois'], 0)} / {_fr(b['bas_mois'], 0)}", "tendance": ""}]


# --- carte libre (Claude + web) ------------------------------------------------------------------------------

CONSIGNE_JSON = """Tu prépares une carte de synthèse affichée dans une application. Cherche sur le web les informations
les plus récentes, puis réponds UNIQUEMENT par un objet JSON valide, sans texte autour ni bloc de code, de la forme :
{"titre": "titre court", "sous_titre": "période ou contexte, ex. « au 11 novembre 2026, 17h35 »",
 "resume": "une à deux phrases à dire à voix haute, sans liste",
 "chiffres": [{"libelle": "…", "valeur": "…", "tendance": "hausse|baisse|stable|"}],
 "points": ["un fait marquant par phrase"],
 "articles": [{"titre": "…", "texte": "une phrase", "source": "nom du média", "url": "https://…"}],
 "sources": [{"titre": "…", "url": "https://…"}]}
Au plus 6 chiffres, 6 points, 6 articles, 4 sources ; laisse une liste vide si elle ne sert pas.
N'écris que des faits trouvés : pas d'estimation présentée comme un fait. Pas d'emoji. En français."""

CONSIGNES = {
    "actus": "Rapport : les 5 actualités principales en France aujourd'hui ({date}). Remplis « articles » (une par actualité) "
             "et laisse « chiffres » vide.",
    "actus_sujet": "Rapport : les dernières actualités sur « {sujet} » ({date}). Remplis « articles » (une par actualité).",
    "bourse": "Rapport boursier sur {sujet} pour la séance du {date} : ce qui a fait bouger le marché et les valeurs "
              "marquantes (plus fortes hausses et baisses). Remplis « points » (3 à 5) et « sources » ; "
              "« chiffres » : seulement les valeurs marquantes avec leur variation.",
    "libre": "Rapport de synthèse sur : {sujet}. Nous sommes le {date}. Donne les chiffres clés s'il y en a, "
             "les points importants et les sources.",
}


def _texte(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _url(v) -> str:
    v = str(v or "").strip()
    return v[:500] if re.match(r"^https?://[^\s\"'<>]+$", v) else ""


def lire_json(texte: str) -> dict:
    """Objet JSON renvoyé par le modèle (tolère un bloc de code ou du texte autour), nettoyé et borné."""
    t = re.sub(r"^```(?:json)?|```$", "", texte.strip(), flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("pas de JSON dans la réponse")
    o = json.loads(t[i:j + 1])
    if not isinstance(o, dict):
        raise ValueError("JSON inattendu")
    liste = lambda k: o.get(k) if isinstance(o.get(k), list) else []  # noqa: E731
    return {
        "titre": _texte(o.get("titre"), 80), "sous_titre": _texte(o.get("sous_titre"), 120),
        "resume": _texte(o.get("resume"), 400),
        "chiffres": [{"libelle": _texte(c.get("libelle"), 60), "valeur": _texte(c.get("valeur"), 40),
                      "tendance": c.get("tendance") if c.get("tendance") in ("hausse", "baisse", "stable") else ""}
                     for c in liste("chiffres")[:6] if isinstance(c, dict) and c.get("libelle")],
        "points": [_texte(p, 300) for p in liste("points")[:6] if isinstance(p, str) and p.strip()],
        "articles": [{"titre": _texte(a.get("titre"), 160), "texte": _texte(a.get("texte"), 300),
                      "source": _texte(a.get("source"), 60), "url": _url(a.get("url"))}
                     for a in liste("articles")[:6] if isinstance(a, dict) and a.get("titre")],
        "sources": [{"titre": _texte(s.get("titre"), 80) or _url(s.get("url")), "url": _url(s.get("url"))}
                    for s in liste("sources")[:4] if isinstance(s, dict) and _url(s.get("url"))],
    }


# --- le service -----------------------------------------------------------------------------------------------

DemanderJSON = Callable[[str], Awaitable[str]]   # consigne -> texte du modèle (Claude avec le web) ; lève si secours


class RapportIndisponible(Exception):
    """Le rapport ne peut pas être fait (service non configuré, lieu inconnu…) : le message est pour l'utilisateur."""


class Rapports:
    def __init__(self, ville: str = "", demander_json: DemanderJSON | None = None, agenda=None, docker=None,
                 timeout: float = 10, transport: httpx.AsyncBaseTransport | None = None,
                 url_meteo: str = "https://api.open-meteo.com/v1/forecast",
                 url_geo: str = "https://geocoding-api.open-meteo.com/v1/search",
                 url_bourse: str = "https://query1.finance.yahoo.com/v8/finance/chart/") -> None:
        self.ville = ville
        self.demander_json = demander_json
        self.agenda = agenda
        self.docker = docker
        self.timeout = timeout
        self.transport = transport
        self.url_meteo, self.url_geo, self.url_bourse = url_meteo, url_geo, url_bourse
        self._geo: dict[str, dict] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport,
                                 headers={"User-Agent": "Mozilla/5.0 (assistant homelab)"})

    async def faire(self, d: Demande) -> tuple[str, dict]:
        """(phrase à dire, carte). Lève RapportIndisponible avec un message lisible."""
        debut = time.perf_counter()
        f = {"meteo": self.meteo, "bourse": self.bourse, "agenda": self.rapport_agenda, "actus": self.actus,
             "docker": self.rapport_docker, "libre": self.libre}.get(d.type)
        if not f:
            raise RapportIndisponible(f"je ne sais pas faire de rapport « {d.type} »")
        carte = await (self.bourse(d.sujet, d.rapide) if d.type == "bourse" else f(d.sujet))
        journal.info("rapport %s « %s » en %d ms", d.type, d.sujet, (time.perf_counter() - debut) * 1000)
        return carte.get("resume") or carte.get("titre", ""), carte

    # météo
    async def geocoder(self, nom: str) -> dict:
        cle = _sans_accents(nom)
        if cle in self._geo:
            return self._geo[cle]
        async with self._client() as c:
            r = await c.get(self.url_geo, params={"name": nom, "count": 1, "language": "fr", "format": "json"})
            r.raise_for_status()
        res = (r.json().get("results") or [None])[0]
        if not res:
            raise RapportIndisponible(f"je ne trouve pas la ville « {nom} »")
        lieu = {"nom": res.get("name", nom), "lat": res["latitude"], "lon": res["longitude"],
                "region": res.get("admin1", ""), "pays": res.get("country", "")}
        self._geo[cle] = lieu
        return lieu

    async def meteo(self, sujet: str) -> dict:
        nom = sujet or self.ville
        if not nom:
            raise RapportIndisponible("pour quelle ville ? Dis par exemple « rapport météo à Lyon » "
                                      "(ou règle la ville dans proactif.ville).")
        lieu = await self.geocoder(nom)
        async with self._client() as c:
            r = await c.get(self.url_meteo, params={
                "latitude": lieu["lat"], "longitude": lieu["lon"], "timezone": "auto", "forecast_days": 5,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
                "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                         "precipitation_sum,sunrise,sunset"})
            r.raise_for_status()
        return carte_meteo(lieu, r.json())

    # bourse
    async def bourse(self, sujet: str, rapide: bool = False) -> dict:
        if not indice_de(sujet) and re.fullmatch(r"(?:la |les )?(?:bourse|march[ée]s?|indices?|bourse de paris)"
                                                  r"(?: financiers?| du jour| aujourd.hui)?", sujet.strip(), re.I):
            sujet = ""
        trouve = indice_de(sujet or "cac 40")
        symbole, nom = trouve if trouve else (None, sujet)

        async def cours():
            if not symbole:
                return None
            try:
                async with self._client() as c:
                    r = await c.get(self.url_bourse + symbole, params={"range": "1mo", "interval": "1d"})
                    r.raise_for_status()
                return donnees_bourse(nom, symbole, r.json())
            except Exception as e:  # noqa: BLE001 - la carte se fait sans la courbe
                journal.info("cours de %s indisponible : %s", symbole, e)
                return None

        async def analyse():
            if rapide:
                return None
            try:
                return await self._json(CONSIGNES["bourse"].format(sujet=nom, date=_date_du_jour()))
            except RapportIndisponible:
                return None

        b, a = await asyncio.gather(cours(), analyse())
        if not b and not a:
            raise RapportIndisponible(f"je n'arrive pas à obtenir les données de {nom} pour le moment.")
        carte = {"type": "rapport", "modele": "bourse", "titre": f"Bourse · {nom}",
                 "sous_titre": (a or {}).get("sous_titre") or "", "resume": "",
                 "maj": datetime.now().astimezone().isoformat(timespec="minutes"),
                 "demande": {"type": "bourse", "sujet": nom},
                 "chiffres": [], "points": (a or {}).get("points", []), "articles": [],
                 "sources": (a or {}).get("sources", [])}
        if b:
            carte["bourse"] = {"serie": b["serie"], "var_jour": b["var_jour"], "heure": b["heure"]}
            carte["chiffres"] = chiffres_bourse(b) + [c for c in (a or {}).get("chiffres", [])][:4]
            if not carte["sous_titre"] and b["heure"]:
                carte["sous_titre"] = "cours du " + datetime.fromisoformat(b["heure"]).strftime("%d/%m à %Hh%M")
            sens = "monte" if (b["var_jour"] or 0) > 0 else "recule" if (b["var_jour"] or 0) < 0 else "est stable"
            carte["resume"] = (f"Le {nom} {sens}" + (f" de {_fr(abs(b['var_jour']))} %" if b["var_jour"] else "")
                               + f", à {_fr(b['cours'])}{' ' + b['unite'] if b['unite'] else ''}.")
            carte["source"] = {"titre": "Yahoo Finance", "url": f"https://finance.yahoo.com/quote/{symbole}"}
            if a and a.get("resume"):
                carte["resume"] += " " + a["resume"]
        else:
            carte["chiffres"] = a["chiffres"]
            carte["resume"] = a.get("resume") or f"Voici le point sur {nom}."
        return carte

    # agenda
    async def rapport_agenda(self, sujet: str = "") -> dict:
        if not self.agenda or not self.agenda.configure:
            raise RapportIndisponible("l'agenda n'est pas configuré (agenda.url).")
        maintenant = datetime.now().astimezone()
        debut = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        evts = await self.agenda.evenements(debut, debut + timedelta(days=7))
        return carte_agenda(evts, maintenant)

    # docker
    async def rapport_docker(self, sujet: str = "") -> dict:
        if not self.docker or not self.docker.configure:
            raise RapportIndisponible("le diagnostic Docker n'est pas configuré (docker.url).")
        liste = await self.docker.conteneurs()
        problemes = {c["nom"] for c in await self.docker.problemes()}
        serveur = None
        if hasattr(self.docker, "serveur"):
            try:
                serveur = await self.docker.serveur()
            except Exception as e:  # noqa: BLE001 - la carte se fait sans l'état du serveur
                journal.info("état du serveur indisponible : %s", e)
        return carte_docker(liste, problemes, serveur)

    # actualités et sujet libre : Claude cherche sur le web
    async def actus(self, sujet: str = "") -> dict:
        consigne = (CONSIGNES["actus_sujet"] if sujet else CONSIGNES["actus"]).format(sujet=sujet, date=_date_du_jour())
        o = await self._json(consigne)
        return self._carte_libre(o, "actus", sujet, "Actualités" + (f" · {sujet}" if sujet else ""))

    async def libre(self, sujet: str) -> dict:
        o = await self._json(CONSIGNES["libre"].format(sujet=sujet, date=_date_du_jour()))
        return self._carte_libre(o, "libre", sujet, sujet[:1].upper() + sujet[1:])

    def _carte_libre(self, o: dict, modele: str, sujet: str, titre: str) -> dict:
        return {"type": "rapport", "modele": modele, "titre": o["titre"] or titre, "sous_titre": o["sous_titre"],
                "resume": o["resume"] or f"Voici le rapport : {o['titre'] or titre}.",
                "maj": datetime.now().astimezone().isoformat(timespec="minutes"),
                "demande": {"type": modele, "sujet": sujet},
                **{k: o[k] for k in ("chiffres", "points", "articles", "sources")}}

    async def _json(self, consigne: str) -> dict:
        if not self.demander_json:
            raise RapportIndisponible("aucun modèle avec accès au web n'est configuré.")
        try:
            texte = await self.demander_json(consigne)
        except RapportIndisponible:
            raise
        except Exception as e:  # noqa: BLE001
            journal.info("rapport : modèle indisponible : %s", e)
            raise RapportIndisponible("Claude ne répond pas pour le moment, réessaie dans un instant.") from e
        try:
            return lire_json(texte)
        except (ValueError, json.JSONDecodeError) as e:
            journal.info("rapport : JSON illisible (%s) : %s", e, texte[:200])
            raise RapportIndisponible("la synthèse reçue est illisible, réessaie.") from e


def _date_du_jour() -> str:
    from .agenda import JOURS
    d = datetime.now()
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
            "novembre", "décembre"][d.month - 1]
    return f"{JOURS[d.weekday()]} {d.day} {mois} {d.year}"


def carte_agenda(evts: list[dict], maintenant: datetime) -> dict:
    from .agenda import JOURS
    j0 = maintenant.date()
    jours: dict[str, list] = {}
    a_venir = []
    for e in sorted(evts, key=lambda x: x["debut"]):
        d = e["debut"].date()
        nom = ("Aujourd'hui" if d == j0 else "Demain" if d == j0 + timedelta(days=1)
               else f"{JOURS[d.weekday()].capitalize()} {d.day}")
        heure = "" if e.get("journee") else e["debut"].strftime("%Hh%M")
        jours.setdefault(nom, []).append({"heure": heure, "titre": e.get("titre", "sans titre"), "lieu": e.get("lieu", "")})
        if e.get("journee") or e["debut"] >= maintenant:
            a_venir.append(e)
    n = len(evts)
    if not n:
        resume = "Rien à l'agenda pour les 7 prochains jours."
    else:
        from .agenda import decrire
        resume = f"{n} rendez-vous sur les 7 prochains jours" + (f" ; le prochain : {decrire(a_venir[0], j0)}." if a_venir else ".")
    aujourd = len(jours.get("Aujourd'hui", []))
    return {"type": "rapport", "modele": "agenda", "titre": "Agenda · 7 jours",
            "sous_titre": f"{aujourd} aujourd'hui · {n} cette semaine", "resume": resume,
            "maj": maintenant.isoformat(timespec="minutes"), "demande": {"type": "agenda", "sujet": ""},
            "agenda": [{"jour": k, "evenements": v} for k, v in jours.items()]}


def carte_docker(liste: list[dict], problemes: set[str], serveur: dict | None) -> dict:
    en_marche = [c for c in liste if c.get("etat") == "running"]
    arretes = [c for c in liste if c.get("etat") != "running" and c["nom"] not in problemes]
    souci = [c for c in liste if c["nom"] in problemes]
    resume = f"{len(en_marche)} conteneurs en marche sur {len(liste)}"
    resume += (f", {len(souci)} en souci : {', '.join(c['nom'] for c in souci[:5])}." if souci else ", aucun en souci.")
    if serveur and serveur.get("alertes"):
        resume += f" Serveur : {len(serveur['alertes'])} alerte(s)."
    conteneurs = [{"nom": c["nom"], "etat": "souci" if c["nom"] in problemes else "marche" if c.get("etat") == "running"
                   else "arret", "statut": (c.get("statut") or c.get("etat") or "")[:60]}
                  for c in souci + en_marche + arretes]
    carte = {"type": "rapport", "modele": "docker", "titre": "Docker · conteneurs",
             "sous_titre": f"{len(liste)} conteneurs", "resume": resume,
             "maj": datetime.now().astimezone().isoformat(timespec="minutes"), "demande": {"type": "docker", "sujet": ""},
             "chiffres": [{"libelle": "En marche", "valeur": str(len(en_marche)), "tendance": ""},
                          {"libelle": "Arrêtés", "valeur": str(len(arretes)), "tendance": ""},
                          {"libelle": "En souci", "valeur": str(len(souci)), "tendance": "baisse" if souci else ""}],
             "docker": {"conteneurs": conteneurs}}
    if serveur:
        cap = serveur.get("capacite_to") or {}
        carte["docker"]["serveur"] = {
            "array": serveur.get("array"), "alertes": (serveur.get("alertes") or [])[:8],
            "utilise_to": cap.get("utilise"), "total_to": cap.get("total"),
            "disques": [{"nom": d.get("nom"), "temperature": d.get("temperature"), "utilise_pct": d.get("utilise_pct")}
                        for d in (serveur.get("disques") or [])[:12]]}
    return carte
