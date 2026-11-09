"""Rappels dans le chat : créés, gardés, annoncés une seule fois à l'heure dite."""

from datetime import datetime, timedelta

import pytest

from assistant.rappels import Rappels


def test_cycle(tmp_path):
    r = Rappels(tmp_path)
    maintenant = datetime.now().astimezone()
    a = r.ajouter("sortir le linge", maintenant + timedelta(seconds=30), "principal", "c1")
    r.ajouter("appeler maman", maintenant + timedelta(hours=2), "principal")
    assert [x["texte"] for x in r.a_venir("principal")] == ["sortir le linge", "appeler maman"]
    assert r.echus(maintenant) == []
    assert [x["id"] for x in r.echus(maintenant + timedelta(minutes=1))] == [a["id"]]
    assert r.echus(maintenant + timedelta(minutes=1)) == []                 # une seule fois
    assert [x["texte"] for x in Rappels(tmp_path).a_venir()] == ["appeler maman"]   # gardé sur disque
    with pytest.raises(ValueError):
        r.ajouter("trop tard", maintenant - timedelta(minutes=5))


def test_api_interne(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import assistant.main as main
    from assistant.config import SecuriteConfig, charger
    from assistant.securite import Securite
    from tests.test_routeur import routeur
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "j")
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    c = TestClient(main.creer_app(cfg, r.registre, securite=Securite(SecuriteConfig(), tmp_path)), client=("127.0.0.1", 1))
    h = {"X-Jeton-Interne": "j"}
    quand = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
    rep = c.post("/interne/rappels", json={"quand": quand, "texte": "sortir le linge", "conversation": "x"}, headers=h).json()
    assert rep["ok"] and c.get("/api/rappels").json()["rappels"][0]["texte"] == "sortir le linge"
    assert c.delete(f"/interne/rappels/{rep['id']}", headers=h).status_code == 200
    assert c.get("/api/rappels").json()["rappels"] == []
