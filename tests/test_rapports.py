"""Rapports à la demande : reconnaissance, cartes toutes faites (données simulées), carte libre, API."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.config import charger
from assistant.fournisseurs import Registre
from assistant.main import creer_app
from assistant.rapports import (Demande, RapportIndisponible, Rapports, carte_agenda, carte_docker, detecter,
                                lire_json)
from tests.test_routeur import Classeur


@pytest.mark.parametrize("message, attendu", [
    ("fais moi un rapport du CAC 40", ("bourse", "CAC 40")),
    ("Fais-moi un rapport météo", ("meteo", "")),
    ("rapport météo à Lyon", ("meteo", "Lyon")),
    ("fais moi un rapport météo pour Saint-Étienne demain", ("meteo", "Saint-Étienne")),
    ("rapport météo de la semaine", ("meteo", "")),
    ("fais le point sur docker", ("docker", "")),
    ("bilan de mes conteneurs", ("docker", "")),
    ("rapport de mon agenda", ("agenda", "")),
    ("fais moi un rapport des actualités du jour", ("actus", "")),
    ("rapport des actus sur la Fed", ("actus", "Fed")),
    ("synthèse du nasdaq", ("bourse", "nasdaq")),
    ("fais moi un rapport sur la guerre commerciale", ("libre", "guerre commerciale")),
])
def test_detecter(message, attendu):
    d = detecter(message)
    assert d is not None and (d.type, d.sujet) == attendu


@pytest.mark.parametrize("message", ["allume la lumière", "par rapport à hier il fait froid", "rappelle-moi demain",
                                     "quel temps fait-il ?", "fais moi un rapport"])
def test_pas_un_rapport(message):
    assert detecter(message) is None


def test_lire_json_tolere_et_nettoie():
    t = 'Voici :\n```json\n{"titre": "Fed", "resume": "Taux inchangés.", "chiffres": [{"libelle": "Taux", ' \
        '"valeur": "4,25 %", "tendance": "monte"}], "points": ["a", 3], "sources": [{"titre": "x", "url": "javascript:alert(1)"},' \
        ' {"titre": "Le Monde", "url": "https://lemonde.fr/a"}]}\n```'
    o = lire_json(t)
    assert o["titre"] == "Fed" and o["chiffres"][0]["tendance"] == "" and o["points"] == ["a"]
    assert o["sources"] == [{"titre": "Le Monde", "url": "https://lemonde.fr/a"}]
    with pytest.raises(ValueError):
        lire_json("Désolé, je ne peux pas.")


# --- météo et bourse, services simulés ------------------------------------------------------------------

GEO = {"results": [{"name": "Lyon", "latitude": 45.75, "longitude": 4.85, "admin1": "Auvergne-Rhône-Alpes", "country": "France"}]}
HEURES = [f"2026-11-11T{h:02d}:00" for h in range(24)] + [f"2026-11-12T{h:02d}:00" for h in range(24)]
METEO = {
    "current": {"time": "2026-11-11T09:15", "temperature_2m": 11.6, "apparent_temperature": 8.9, "relative_humidity_2m": 81,
                "weather_code": 61, "wind_speed_10m": 14.2, "is_day": 1},
    "hourly": {"time": HEURES, "temperature_2m": [10 + (i % 12) / 2 for i in range(48)],
               "weather_code": [61] * 48, "precipitation_probability": [60] * 48, "is_day": [1] * 48},
    "daily": {"time": ["2026-11-11", "2026-11-12", "2026-11-13"], "weather_code": [61, 2, 0],
              "temperature_2m_max": [14.2, 12.8, 13], "temperature_2m_min": [7.9, 5.1, 4],
              "precipitation_probability_max": [70, 10, 0], "precipitation_sum": [4.2, 0, 0],
              "sunrise": ["2026-11-11T07:38"], "sunset": ["2026-11-11T17:12"]},
}
CHART = {"chart": {"result": [{"meta": {"currency": "EUR", "symbol": "^FCHI", "regularMarketPrice": 8123.4,
                                         "regularMarketTime": 1794412800},
                               "timestamp": [1, 2, 3, 4],
                               "indicators": {"quote": [{"close": [7900.0, 8000.0, None, 8050.0, 8123.4]}]}}]}}


def _transport(vus):
    def repondre(req: httpx.Request):
        vus.append(str(req.url))
        if "geocoding" in req.url.host:
            return httpx.Response(200, json=GEO if "Lyon" in str(req.url) else {})
        if "open-meteo" in req.url.host:
            return httpx.Response(200, json=METEO)
        if "yahoo" in req.url.host:
            return httpx.Response(200, json=CHART)
        return httpx.Response(404)
    return httpx.MockTransport(repondre)


def test_carte_meteo():
    vus = []
    r = Rapports("Lyon", transport=_transport(vus))
    texte, c = asyncio.run(r.faire(Demande("meteo", "")))
    m = c["meteo"]
    assert c["type"] == "rapport" and c["modele"] == "meteo" and c["titre"] == "Météo · Lyon"
    assert m["temp"] == 12 and m["ressenti"] == 9 and m["icone"] == "pluie" and m["ciel"] == "pluie faible"
    assert m["heures"][0]["heure"] == "09h" and len(m["heures"]) == 8 and m["lever"] == "07:38"
    assert [j["jour"] for j in m["jours"]][:2] == ["Aujourd'hui", "Demain"] and m["jours"][2]["icone"] == "soleil"
    assert texte.startswith("À Lyon, 12 °C (ressenti 9), pluie faible.") and "70 %" in texte and "Demain" in texte
    # géocodage retenu : la 2e demande ne le refait pas
    asyncio.run(r.faire(Demande("meteo", "lyon")))
    assert sum("geocoding" in u for u in vus) == 1


def test_meteo_sans_ville_ou_ville_inconnue():
    r = Rapports("", transport=_transport([]))
    with pytest.raises(RapportIndisponible, match="quelle ville"):
        asyncio.run(r.faire(Demande("meteo", "")))
    with pytest.raises(RapportIndisponible, match="Atlantide"):
        asyncio.run(r.faire(Demande("meteo", "Atlantide")))


def test_carte_bourse_cours_et_analyse():
    consignes = []

    async def claude(consigne):
        consignes.append(consigne)
        return json.dumps({"titre": "CAC", "sous_titre": "séance du 11/11", "resume": "Le luxe tire l'indice.",
                           "chiffres": [{"libelle": "LVMH", "valeur": "+3,1 %", "tendance": "hausse"}],
                           "points": ["Le luxe rebondit."], "sources": [{"titre": "Boursorama", "url": "https://boursorama.com"}]})
    vus = []
    texte, c = asyncio.run(Rapports(demander_json=claude, transport=_transport(vus)).faire(Demande("bourse", "CAC 40")))
    assert any("%5EFCHI" in u or "^FCHI" in u for u in vus) and "CAC 40" in consignes[0]
    assert c["modele"] == "bourse" and c["bourse"]["serie"] == [7900.0, 8000.0, 8050.0, 8123.4]
    assert c["chiffres"][0] == {"libelle": "Cours", "valeur": "8 123,40 pts", "tendance": ""}
    assert c["chiffres"][1]["tendance"] == "hausse" and c["chiffres"][1]["valeur"] == "+0,91 %"
    assert c["chiffres"][-1]["libelle"] == "LVMH" and c["points"] == ["Le luxe rebondit."]
    assert texte == "Le CAC 40 monte de 0,91 %, à 8 123,40 pts. Le luxe tire l'indice."


def test_bourse_sans_claude_garde_le_cours_et_sans_rien_echoue():
    _, c = asyncio.run(Rapports(transport=_transport([])).faire(Demande("bourse", "la bourse")))
    assert c["titre"] == "Bourse · CAC 40" and c["points"] == [] and c["chiffres"]
    panne = httpx.MockTransport(lambda req: httpx.Response(500))
    with pytest.raises(RapportIndisponible):
        asyncio.run(Rapports(transport=panne).faire(Demande("bourse", "nasdaq")))


def test_carte_libre_et_actus():
    async def claude(consigne):
        return '{"titre": "Actualités du jour", "resume": "Trois sujets dominent.", "articles": [{"titre": "A", ' \
               '"texte": "a", "source": "AFP", "url": "https://afp.com/a"}]}'
    texte, c = asyncio.run(Rapports(demander_json=claude).faire(Demande("actus")))
    assert c["modele"] == "actus" and c["articles"][0]["source"] == "AFP" and texte == "Trois sujets dominent."
    assert c["demande"] == {"type": "actus", "sujet": ""}

    async def secours(consigne):
        raise RapportIndisponible("Claude ne répond pas")
    with pytest.raises(RapportIndisponible):
        asyncio.run(Rapports(demander_json=secours).faire(Demande("libre", "la Fed")))


def test_carte_agenda():
    maintenant = datetime(2026, 11, 11, 9, 0).astimezone()
    evts = [{"debut": maintenant + timedelta(hours=5), "titre": "Dentiste", "lieu": "Lyon"},
            {"debut": maintenant - timedelta(hours=1), "titre": "Café"},
            {"debut": (maintenant + timedelta(days=1)).replace(hour=0), "titre": "Anniversaire", "journee": True}]
    c = carte_agenda(evts, maintenant)
    assert [j["jour"] for j in c["agenda"]] == ["Aujourd'hui", "Demain"]
    assert c["agenda"][0]["evenements"][1] == {"heure": "14h00", "titre": "Dentiste", "lieu": "Lyon"}
    assert c["resume"].startswith("3 rendez-vous") and "Dentiste" in c["resume"]
    assert carte_agenda([], maintenant)["resume"].startswith("Rien")


def test_carte_docker():
    liste = [{"nom": "plex", "etat": "running", "statut": "Up 3 days"}, {"nom": "nginx", "etat": "exited", "statut": "Exited (1)"},
             {"nom": "vieux", "etat": "exited", "statut": "Exited (0)"}]
    c = carte_docker(liste, {"nginx"}, {"array": "STARTED", "alertes": ["disk1 : 52 °C (chaud)"],
                                        "capacite_to": {"utilise": 6.1, "total": 12}, "disques": []})
    assert [x["etat"] for x in c["docker"]["conteneurs"]] == ["souci", "marche", "arret"]
    assert "1 en souci : nginx" in c["resume"] and "1 alerte" in c["resume"]
    assert c["docker"]["serveur"]["total_to"] == 12


# --- dans le chat et par l'API ---------------------------------------------------------------------------

def _app(reponse_claude):
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    claude = Classeur("claude", reponse_claude)
    reg = Registre({"r": Classeur("rapide", "Salut"), "c": claude}, {"rapide": "r", "raisonnement": "c", "secours": "r"})
    return creer_app(cfg, reg), claude


def test_chat_rapport_libre_renvoie_une_carte():
    app, claude = _app('{"titre": "La Fed", "resume": "Taux inchangés à 4,25 %.", "points": ["Pause attendue."]}')
    c = TestClient(app)
    r = c.post("/api/chat", json={"message": "fais-moi un rapport sur la Fed", "conversation": "x"}).json()
    assert r["reponse"] == "Taux inchangés à 4,25 %." and r["fournisseur"] == "rapport"
    assert r["cartes"][0]["type"] == "rapport" and r["cartes"][0]["points"] == ["Pause attendue."]
    assert "JSON" in claude.appels[0][0][0].content
    lignes = [json.loads(x) for x in c.post("/api/chat", json={
        "message": "fais-moi un rapport sur la Fed", "flux": True}).text.splitlines()]
    assert [next(iter(x)) for x in lignes] == ["route", "cartes", "delta", "fin"]


def test_chat_rapport_secours_ne_fait_pas_de_carte():
    app, claude = _app("pas du JSON")
    claude.panne = True       # Claude en panne : le secours (sans web) répondrait, il est refusé
    r = TestClient(app).post("/api/chat", json={"message": "rapport sur la Fed"}).json()
    assert "cartes" not in r and "Claude ne répond pas" in r["reponse"]


def test_api_rapport():
    app, _ = _app('{"titre": "Actus", "resume": "ok", "articles": []}')
    c = TestClient(app)
    assert {m["type"] for m in c.get("/api/rapports").json()["modeles"]} >= {"meteo", "bourse", "agenda", "docker", "actus"}
    assert c.post("/api/rapport", json={"type": "inconnu"}).status_code == 400
    assert c.post("/api/rapport", json={"type": "libre"}).status_code == 400
    assert c.post("/api/rapport", json={"type": "actus"}).json()["cartes"][0]["modele"] == "actus"
    r = c.post("/api/rapport", json={"type": "agenda"}).json()
    assert r["cartes"] == [] and "pas configuré" in r["texte"]
