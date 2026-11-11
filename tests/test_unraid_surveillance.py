"""Surveillance d'Unraid : espace libre, parité, mises à jour d'images Docker et leurs boutons."""

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from assistant.docker_diag import UnraidDocker, age_jours
from assistant.proactif import MisesAJourIgnorees
from tests.test_phase6 import ARRAY

URL = "https://192.168.1.50:4436/graphql"


def faux_unraid(vus, array=None, statuts=True, maj=("plex",)):
    """Faux Unraid : `statuts=False` imite une version sans containerUpdateStatuses (repli sur isUpdateAvailable)."""
    def repondre(req: httpx.Request) -> httpx.Response:
        corps = json.loads(req.content)
        q = corps["query"]
        vus.append((req.headers.get("x-api-key"), q, corps.get("variables")))
        if "array" in q:
            return httpx.Response(200, json={"data": array or ARRAY})
        if "containerUpdateStatuses" in q:
            if not statuts:
                return httpx.Response(400, json={"errors": [{"message": "Cannot query field \"containerUpdateStatuses\""}]})
            return httpx.Response(200, json={"data": {"docker": {"containerUpdateStatuses": [
                {"name": n, "updateStatus": "UPDATE_AVAILABLE" if n in maj else "UP_TO_DATE"} for n in ("plex", "radarr")]}}})
        if "isUpdateAvailable" in q:
            return httpx.Response(200, json={"data": {"docker": {"containers": [
                {"names": ["/" + n], "isUpdateAvailable": n in maj} for n in ("plex", "radarr")]}}})
        if "updateContainer" in q:
            if req.headers.get("x-api-key") != "cle-actions":
                return httpx.Response(200, json={"errors": [{"message": "Forbidden resource"}]})
            return httpx.Response(200, json={"data": {"docker": {"updateContainer": {"id": "x:1", "state": "RUNNING", "status": "Up 2 seconds"}}}})
        return httpx.Response(200, json={"data": {"docker": {"containers": [
            {"id": "x:1", "names": ["/plex"], "state": "RUNNING", "status": "Up", "autoStart": True, "image": "plex"},
            {"id": "x:2", "names": ["/radarr"], "state": "RUNNING", "status": "Up", "autoStart": True, "image": "radarr"}]}}})
    return httpx.MockTransport(repondre)


def test_age_jours():
    maintenant = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    assert age_jours("2026-11-01", maintenant) == 10
    assert age_jours("2026-09-01T03:00:00Z", maintenant) == 71
    assert age_jours("", maintenant) is None and age_jours("pas une date", maintenant) is None


def test_espace_libre_et_parite_trop_vieille():
    array = copy.deepcopy(ARRAY)
    vieux = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    array["array"]["parityCheckStatus"]["date"] = vieux
    array["array"]["capacity"]["kilobytes"]["free"] = "1000000000"          # 5 % de 20 To
    d = UnraidDocker(URL, "cle-lecture", transport=faux_unraid([], array))
    s = asyncio.run(d.serveur())
    alertes = " | ".join(s["alertes"])
    assert "parité : dernier contrôle il y a 60 jours" in alertes
    assert "array : plus que 5 % d'espace libre" in alertes and s["capacite_to"]["libre_pct"] == 5
    assert s["parite"]["age_jours"] == 60
    # seuils réglables, 0 = jamais
    s = asyncio.run(d.serveur(plein_pct=0, libre_min_pct=0, parite_max_jours=0))
    assert not any("parité : dernier" in a or "espace libre" in a or "plein à" in a for a in s["alertes"])
    # contrôle en cours : pas d'alerte d'âge
    array["array"]["parityCheckStatus"]["running"] = True
    assert not any("dernier contrôle" in a for a in asyncio.run(d.serveur())["alertes"])


def test_mises_a_jour_deux_schemas():
    assert asyncio.run(UnraidDocker(URL, "c", transport=faux_unraid([])).mises_a_jour()) == ["plex"]
    vus = []
    ancien = UnraidDocker(URL, "c", transport=faux_unraid(vus, statuts=False, maj=("plex", "radarr")))
    assert asyncio.run(ancien.mises_a_jour()) == ["plex", "radarr"]
    assert "isUpdateAvailable" in vus[-1][1]


def test_mettre_a_jour_avec_la_cle_actions_seulement():
    vus = []
    d = UnraidDocker(URL, "cle-lecture", transport=faux_unraid(vus))
    with pytest.raises(PermissionError):
        asyncio.run(d.mettre_a_jour("plex", ""))
    r = asyncio.run(d.mettre_a_jour("plex", "cle-actions"))
    assert r == {"nom": "plex", "etat": "running", "statut": "Up 2 seconds"}
    assert vus[-1][0] == "cle-actions" and "updateContainer" in vus[-1][1] and vus[-1][2] == {"id": "x:1"}


def test_ignorees(tmp_path):
    m = MisesAJourIgnorees(tmp_path, jours=30)
    m.ignorer("plex", maintenant=1000)
    assert m.est_ignoree("plex", maintenant=1000 + 86400) and not m.est_ignoree("radarr")
    assert not m.est_ignoree("plex", maintenant=1000 + 31 * 86400)      # rappel au bout de 30 jours
    assert MisesAJourIgnorees(tmp_path).noms == {"plex": 1000}           # gardé sur disque
    m.garder_seulement({"radarr"})                                       # plex mis à jour entre-temps : oublié
    assert m.noms == {}


def app_de_test(tmp_path, monkeypatch, vus, cle_actions="cle-actions", **faux):
    from fastapi.testclient import TestClient

    import assistant.main as main
    from assistant.config import SecuriteConfig, charger
    from assistant.securite import Securite
    from tests.test_routeur import routeur
    monkeypatch.setenv("UNRAID_CLE", "cle-lecture")
    monkeypatch.setenv("UNRAID_CLE_ACTIONS", cle_actions)

    class Faux(UnraidDocker):
        def __init__(self, url, cle, timeout=8):
            super().__init__(url, cle, timeout, faux_unraid(vus, **faux))
    monkeypatch.setattr(main, "UnraidDocker", Faux)
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    cfg.docker.unraid_url = URL
    app = main.creer_app(cfg, r.registre, securite=Securite(SecuriteConfig(), tmp_path))
    return app, TestClient(app, client=("127.0.0.1", 1))


def test_annonce_mettre_a_jour(tmp_path, monkeypatch):
    vus = []
    app, c = app_de_test(tmp_path, monkeypatch, vus)
    veille = {}
    asyncio.run(app.state.surveiller_une_fois(veille))
    asyncio.run(app.state.surveiller_une_fois(veille))          # relu toutes les 6 h seulement, et sans doublon
    maj = [a for a in app.state.annonces.liste if a["cle"].startswith("maj:")]
    assert [a["texte"] for a in maj] == ["Une mise à jour de l'image de plex est disponible."]
    assert sum("containerUpdateStatuses" in v[1] for v in vus) == 1
    a = next(x for x in c.get("/api/annonces").json()["annonces"] if x["id"] == maj[0]["id"])
    assert [b["id"] for b in a["actions"]] == ["mettre_a_jour", "ignorer"] and a["cible"]["conteneur"] == "plex"
    assert not any("updateContainer" in v[1] for v in vus)                  # rien sans le bouton
    r = c.post(f"/api/annonces/{a['id']}/action", json={"action": "mettre_a_jour"})
    assert r.status_code == 200 and r.json()["texte"] == "C'est fait : plex est à jour et tourne."
    assert any("updateContainer" in v[1] and v[0] == "cle-actions" for v in vus)
    assert app.state.annonces.trouver(a["id"])["resolue"]
    deux = c.post(f"/api/annonces/{a['id']}/action", json={"action": "mettre_a_jour"})       # double clic, autre appareil
    assert deux.status_code == 409 and "déjà mis à jour" in deux.json()["detail"]
    assert c.get("/api/unraid").json()["mises_a_jour"] == ["plex"]


def test_annonce_ignorer(tmp_path, monkeypatch):
    vus = []
    app, c = app_de_test(tmp_path, monkeypatch, vus)
    asyncio.run(app.state.surveiller_une_fois({}))
    a = next(x for x in app.state.annonces.liste if x["cle"] == "maj:plex")
    assert c.post(f"/api/annonces/{a['id']}/action", json={"action": "supprimer"}).status_code == 422
    r = c.post(f"/api/annonces/{a['id']}/action", json={"action": "ignorer"})
    assert r.status_code == 200 and "j'ignore" in r.json()["texte"]
    assert not any("updateContainer" in v[1] for v in vus)
    asyncio.run(app.state.surveiller_une_fois({}))                    # plus annoncée tant qu'elle est ignorée
    assert len([x for x in app.state.annonces.liste if x["cle"] == "maj:plex"]) == 1


def test_annonce_sans_cle_actions(tmp_path, monkeypatch):
    app, c = app_de_test(tmp_path, monkeypatch, [], cle_actions="")
    asyncio.run(app.state.surveiller_une_fois({}))
    a = next(x for x in app.state.annonces.liste if x["cle"] == "maj:plex")
    r = c.post(f"/api/annonces/{a['id']}/action", json={"action": "mettre_a_jour"})
    assert r.status_code == 409 and "clé" in r.json()["detail"]
    assert not app.state.annonces.trouver(a["id"])["decision"]           # les boutons restent utilisables


def test_mise_a_jour_faite_ailleurs_close_l_annonce(tmp_path, monkeypatch):
    app, c = app_de_test(tmp_path, monkeypatch, [])
    asyncio.run(app.state.surveiller_une_fois({}))
    a = next(x for x in app.state.annonces.liste if x["cle"] == "maj:plex")

    async def rien(self):
        return []
    monkeypatch.setattr(UnraidDocker, "mises_a_jour", rien)        # mis à jour depuis la page Docker d'Unraid
    asyncio.run(app.state.surveiller_une_fois({}))
    assert app.state.annonces.trouver(a["id"])["resolue"]
    assert c.post(f"/api/annonces/{a['id']}/action", json={"action": "mettre_a_jour"}).status_code == 409
