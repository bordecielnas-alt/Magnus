"""Phase 6 : état d'Unraid, redémarrage confirmé, notifications Telegram."""

import asyncio
import json

import httpx
import pytest

from assistant.docker_diag import UnraidDocker
from assistant.telegram import Telegram

ARRAY = {"array": {
    "state": "STARTED",
    "capacity": {"kilobytes": {"free": "4000000000", "used": "16000000000", "total": "20000000000"}},
    "parityCheckStatus": {"status": "COMPLETED", "errors": 0, "progress": 100, "running": False, "date": "2026-11-01"},
    "parities": [{"name": "parity", "device": "sdb", "status": "DISK_OK", "temp": 34, "numErrors": 0,
                  "warning": 45, "critical": 55, "isSpinning": True, "type": "PARITY"}],
    "disks": [{"name": "disk1", "device": "sdc", "status": "DISK_OK", "temp": 51, "numErrors": 0, "warning": 45,
               "critical": 55, "isSpinning": True, "type": "DATA", "fsSize": "1000", "fsFree": "20", "fsUsed": "980"},
              {"name": "disk2", "device": "sdd", "status": "DISK_DSBL", "temp": None, "numErrors": 12, "warning": 45,
               "critical": 55, "isSpinning": False, "type": "DATA", "fsSize": None, "fsFree": None, "fsUsed": None},
              {"name": "disk3", "device": "", "status": "DISK_NP"}],
    "caches": []},
    "disks": [{"name": "WD", "device": "/dev/sdc", "smartStatus": "OK", "temperature": 51},
              {"name": "WD", "device": "/dev/sdd", "smartStatus": "UNKNOWN", "temperature": None}]}


def faux_unraid(vus):
    def repondre(req: httpx.Request) -> httpx.Response:
        corps = json.loads(req.content)
        vus.append((req.headers.get("x-api-key"), corps["query"].split("{")[0].strip(), corps.get("variables")))
        q = corps["query"]
        if "array" in q:
            return httpx.Response(200, json={"data": ARRAY})
        if "restart" in q:
            if req.headers.get("x-api-key") != "cle-actions":
                return httpx.Response(200, json={"errors": [{"message": "Forbidden resource"}]})
            return httpx.Response(200, json={"data": {"docker": {"restart": {"id": "x:1", "state": "RUNNING", "status": "Up 1 second"}}}})
        if "updateContainer" in q:
            return httpx.Response(200, json={"data": {"docker": {"updateContainer": {"id": "x:1", "state": "RUNNING", "status": "Up"}}}})
        for m in ("start", "stop"):
            if f"{m}(" in q:
                return httpx.Response(200, json={"data": {"docker": {m: {"id": "x:1", "state": "RUNNING" if m == "start" else "EXITED", "status": "Up"}}}})
        return httpx.Response(200, json={"data": {"docker": {"containers": [
            {"id": "x:1", "names": ["/plex"], "state": "RUNNING", "status": "Up", "autoStart": True, "image": "plex"}]}}})
    return httpx.MockTransport(repondre)


def test_etat_serveur_et_alertes():
    d = UnraidDocker("https://192.168.1.50:4436/graphql", "cle-lecture", transport=faux_unraid([]))
    s = asyncio.run(d.serveur())
    assert s["array"] == "STARTED" and len(s["disques"]) == 3            # emplacement vide ignoré
    alertes = " | ".join(s["alertes"])
    assert "disk1 : 51 °C (chaud)" in alertes and "disk1 : plein à 98 %" in alertes
    assert "disk2 : état DISK_DSBL" in alertes and "disk2 : 12 erreur" in alertes and "SMART UNKNOWN" in alertes
    assert s["capacite_to"]["total"] == round(20000000000 / 1024 ** 3, 2)


def test_redemarrage_avec_la_cle_actions_seulement():
    vus = []
    d = UnraidDocker("https://192.168.1.50:4436/graphql", "cle-lecture", transport=faux_unraid(vus))
    with pytest.raises(PermissionError):
        asyncio.run(d.redemarrer("plex", ""))
    r = asyncio.run(d.redemarrer("plex", "cle-actions"))
    assert r["nom"] == "plex" and r["etat"] == "running"
    assert vus[-1][0] == "cle-actions" and vus[-1][2] == {"id": "x:1"}


def test_telegram():
    vus = []

    def repondre(req: httpx.Request) -> httpx.Response:
        vus.append((req.url.path, json.loads(req.content)))
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"message": {"chat": {"id": 42, "type": "private", "first_name": "Benjamin"}}}]})
        return httpx.Response(200, json={"ok": True, "result": {}})
    t = Telegram("123:abc", transport=httpx.MockTransport(repondre))
    assert not t.configure
    assert asyncio.run(t.trouver_chat()) == {"id": "42", "nom": "Benjamin"}
    t.chat_id = "42"
    asyncio.run(t.envoyer("Disque disk2 en erreur"))
    assert vus[-1] == ("/bot123:abc/sendMessage", {"chat_id": "42", "text": "Disque disk2 en erreur",
                                                    "disable_web_page_preview": True})
    refus = Telegram("x", "1", transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"})))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        asyncio.run(refus.envoyer("x"))


def test_redemarrage_confirme_dans_la_conversation(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import assistant.main as main
    from assistant.config import SecuriteConfig, charger
    from assistant.securite import Securite
    from tests.test_routeur import routeur
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "j")
    monkeypatch.setenv("UNRAID_CLE", "cle-lecture")
    monkeypatch.setenv("UNRAID_CLE_ACTIONS", "cle-actions")
    vus = []
    class Faux(UnraidDocker):
        def __init__(self, url, cle, timeout=8):
            super().__init__(url, cle, timeout, faux_unraid(vus))
    monkeypatch.setattr(main, "UnraidDocker", Faux)
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    cfg.docker.unraid_url = "https://192.168.1.50:4436/graphql"
    c = TestClient(main.creer_app(cfg, r.registre, securite=Securite(SecuriteConfig(), tmp_path)), client=("127.0.0.1", 1))
    c.post("/api/chat", json={"message": "bonjour", "conversation": "d1"})          # la conversation a un compte
    h = {"X-Jeton-Interne": "j"}
    diag = c.get("/interne/docker/diagnostic", params={"nom": "plex"}, headers=h).json()
    assert diag["redemarrage_possible"] is True and diag["causes_probables"] == []
    assert "pas de journaux" in diag["remarque"]
    assert diag["etat"] == "en marche" and diag["statut"] == "en marche"          # jamais « running » / « Up » au modèle
    assert c.get("/interne/docker/conteneurs", headers=h).json()[0]["statut"] == "en marche"
    assert c.post("/interne/docker/redemarrer", json={"nom": "plex", "conversation": "d1"}, headers=h).status_code == 200
    assert not any("restart" in str(v) for v in vus)                                  # rien avant le oui
    rep = c.post("/api/chat", json={"message": "oui", "conversation": "d1"}).json()
    assert rep["reponse"].startswith("C'est fait, plex a redémarré.") and "Up" not in rep["reponse"]
    assert c.post("/interne/docker/redemarrer", json={"nom": "plex", "conversation": "d1"}, headers=h).status_code == 200
    assert c.post("/api/chat", json={"message": "non", "conversation": "d1"}).json()["reponse"] == "D'accord, je ne redémarre rien."
    assert c.get("/api/unraid").json()["array"] == "STARTED"


def test_choisir_conteneur_approximatif():
    from assistant.docker_diag import choisir_conteneur, normaliser_nom
    liste = [{"nom": n} for n in ("adminer", "MeTube", "plex", "Plex-Meta-Manager", "binhex-radarr", "sonarr")]
    assert normaliser_nom("le docker MiTube") == "mitube" and normaliser_nom("Dockerplex") == "plex"
    assert choisir_conteneur("MiTube", liste)["nom"] == "MeTube"              # transcription vocale
    assert choisir_conteneur("le conteneur Adminer", liste)["nom"] == "adminer"
    assert choisir_conteneur("Dockerplex", liste)["nom"] == "plex"            # exact avant « inclus »
    assert choisir_conteneur("radarr", liste)["nom"] == "binhex-radarr"
    assert choisir_conteneur("arr", liste) is None                            # ambigu : radarr ou sonarr
    assert choisir_conteneur("Omar", liste) is None                           # rien d'approchant


def test_demarrer_arreter_et_redemarrer_sans_restart():
    vus = []
    d = UnraidDocker("https://192.168.1.50:4436/graphql", "cle-lecture", transport=faux_unraid(vus))
    assert asyncio.run(d.agir("plex", "demarrer", "cle-actions"))["etat"] == "running"
    assert asyncio.run(d.agir("plex", "arreter", "cle-actions"))["etat"] == "exited"
    assert vus[-1][0] == "cle-actions"
    with pytest.raises(ValueError):
        asyncio.run(d.agir("plex", "supprimer", "cle-actions"))

    # API Unraid sans « restart » : arrêt puis démarrage
    requetes = []

    def repondre(req):
        q = json.loads(req.content)["query"]
        requetes.append(q)
        if "restart" in q:
            return httpx.Response(400, json={"errors": [{"message": 'Cannot query field "restart" on type "DockerMutations".'}]})
        if "start" in q or "stop" in q:
            m = "start" if "start(" in q else "stop"
            return httpx.Response(200, json={"data": {"docker": {m: {"id": "x:1", "state": "RUNNING", "status": "Up"}}}})
        return httpx.Response(200, json={"data": {"docker": {"containers": [
            {"id": "x:1", "names": ["/plex"], "state": "RUNNING", "status": "Up", "image": "plex"}]}}})
    d2 = UnraidDocker("https://192.168.1.50:4436/graphql", "k", transport=httpx.MockTransport(repondre))
    r = asyncio.run(d2.redemarrer("plex", "cle-actions"))
    assert r["etat"] == "running"
    assert ["stop(" in q for q in requetes if "mutation" in q] == [False, True, False]


def test_allumer_un_conteneur_passe_par_docker(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import assistant.main as main
    from assistant.config import SecuriteConfig, charger
    from assistant.securite import Securite
    from tests.test_routeur import routeur
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "j")
    monkeypatch.setenv("UNRAID_CLE", "cle-lecture")
    monkeypatch.setenv("UNRAID_CLE_ACTIONS", "cle-actions")
    vus = []

    class Faux(UnraidDocker):
        def __init__(self, url, cle, timeout=8):
            super().__init__(url, cle, timeout, faux_unraid(vus))
    monkeypatch.setattr(main, "UnraidDocker", Faux)
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    cfg.docker.unraid_url = "https://192.168.1.50:4436/graphql"
    c = TestClient(main.creer_app(cfg, r.registre, securite=Securite(SecuriteConfig(), tmp_path)), client=("127.0.0.1", 1))
    for phrase in ("allume le docker MiTube", "quel est l'état des dockers ?", "Magnus, quel est l'état de Plex ?",
                   "éteins le conteneur Adminer"):
        rep = c.post("/api/chat", json={"message": phrase, "conversation": "d2"}).json()
        assert rep["route"]["raison"] == "demande Docker", phrase
    assert c.post("/api/chat", json={"message": "allume le salon", "conversation": "d2"}).json()["route"]["raison"] != "demande Docker"
    # démarrer après le oui seulement
    h = {"X-Jeton-Interne": "j"}
    en_attente = c.post("/interne/docker/redemarrer", json={"nom": "plex", "action": "demarrer", "conversation": "d2"},
                        headers=h).json()["en_attente"]
    assert "Je démarre plex ?" in en_attente and not any("start" in str(v) for v in vus)
    assert c.post("/api/chat", json={"message": "oui", "conversation": "d2"}).json()["reponse"].startswith("C'est fait, plex est démarré.")
    c.post("/interne/docker/redemarrer", json={"nom": "plex", "action": "arreter", "conversation": "d2"}, headers=h)
    assert c.post("/api/chat", json={"message": "non", "conversation": "d2"}).json()["reponse"] == "D'accord, je n'arrête rien."
    # « oui » dans une phrase (« Magnus, oui, je veux que tu l'arrêtes ») vaut confirmation
    c.post("/interne/docker/redemarrer", json={"nom": "plex", "action": "arreter", "conversation": "d2"}, headers=h)
    rep = c.post("/api/chat", json={"message": "oui, je veux que tu éteignes plex", "conversation": "d2"}).json()
    assert rep["reponse"].startswith("C'est fait, plex est arrêté.")
    # mise à jour d'image à la voix : préparée, puis faite après le oui seulement
    assert c.post("/api/chat", json={"message": "mets à jour le conteneur N8N", "conversation": "d2"}).json()["route"]["raison"] == "demande Docker"
    en_attente = c.post("/interne/docker/redemarrer", json={"nom": "plex", "action": "mettre_a_jour", "conversation": "d2"},
                        headers=h).json()["en_attente"]
    assert "Je mets à jour plex ?" in en_attente and not any("updateContainer" in str(v) for v in vus)
    assert c.post("/api/chat", json={"message": "oui", "conversation": "d2"}).json()["reponse"] == "C'est fait, plex est à jour et tourne."
    assert any(v[0] == "cle-actions" and v[2] == {"id": "x:1"} for v in vus[-2:])
    c.post("/interne/docker/redemarrer", json={"nom": "plex", "action": "mettre_a_jour", "conversation": "d2"}, headers=h)
    assert c.post("/api/chat", json={"message": "non", "conversation": "d2"}).json()["reponse"] == "D'accord, je ne mets rien à jour."
    # fenêtre d'écoute relancée après la réponse
    assert c.post("/api/voix/prolonger", json={"conversation": "d2"}).json()["fenetre_s"] > 0
    # Telegram : outil réservé aux administrateurs, pas configuré ici
    assert c.post("/interne/telegram", json={"texte": "test", "conversation": "d2"}, headers=h).status_code == 503
