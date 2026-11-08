"""Historique des actions annulables et état connu des appareils."""

import asyncio

from assistant.config import MaisonConfig
from assistant.maison import Maison
from assistant.maison.alias import AliasAppris
from assistant.maison.historique import Historique, demande
from tests.test_maison import FauxHA


def maison(tmp_path):
    ha = FauxHA()
    return Maison(MaisonConfig(), ha, AliasAppris(tmp_path / "a.json"), historique=Historique(tmp_path)), ha


def test_action_notee_puis_annulee(tmp_path):
    m, ha = maison(tmp_path)

    async def scenario():
        demande.set(("voix", "allume la lampe sur pied"))
        await m.traiter("allume la lampe sur pied", "c1")
        return m.historique.liste()
    actions = asyncio.run(scenario())
    a = actions[0]
    assert a["appareil"] == "light.lampe_pied" and a["action"] == "allumer" and a["source"] == "voix"
    assert a["texte"] == "allume la lampe sur pied" and a["avant"]["etat"] == "off" and a["annulable"]
    ok, msg = asyncio.run(m.annuler_action(a["id"]))
    assert ok and ha.appels[-1] == ("light", "turn_off", {"entity_id": "light.lampe_pied"})
    assert asyncio.run(m.annuler_action(a["id"])) == (False, "déjà annulée")
    assert m.historique.liste()[0]["action"] == "annulation"
    # gardé sur disque
    assert Historique(tmp_path).trouver(a["id"])["annulee"] is True


def test_etat_de_tous_les_appareils_garde_en_memoire(tmp_path):
    m, _ = maison(tmp_path)
    app = {x["entity_id"]: x for x in asyncio.run(m.etats_appareils())}
    assert app["light.plafonnier"]["etat"] == "on" and app["cover.volet_chambre"]["etat"] == "open"
    assert {x["entity_id"] for x in Historique(tmp_path).appareils()} == set(app)


def test_api_actions(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from assistant.config import charger
    from assistant.main import creer_app
    from tests.test_routeur import routeur
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    c = TestClient(creer_app(cfg, r.registre))
    assert c.get("/api/actions").json() == {"actions": []} or "actions" in c.get("/api/actions").json()
    assert c.post("/api/actions/inconnu/annuler").status_code == 404
    assert "appareils" in c.get("/api/appareils/etat").json()
