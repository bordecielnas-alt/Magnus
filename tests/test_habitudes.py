"""Habitudes repérées, automatisation HA proposée, routines."""

from datetime import datetime, timedelta

import pytest

from assistant.habitudes import Routines, automatisation, detecter


def actions_du_soir(n_jours, heure=23, minute=5):
    base = datetime(2026, 11, 8, tzinfo=datetime.now().astimezone().tzinfo)
    return [{"appareil": "light.salon", "nom": "Salon", "action": "eteindre",
             "heure": (base - timedelta(days=j)).replace(hour=heure, minute=minute + (j % 3) * 7).isoformat()}
            for j in range(1, n_jours + 1)]


def test_detection():
    maintenant = datetime(2026, 11, 9, tzinfo=datetime.now().astimezone().tzinfo)
    h = detecter(actions_du_soir(5) + actions_du_soir(1, heure=14), maintenant=maintenant)
    assert len(h) == 1 and h[0]["appareil"] == "light.salon" and h[0]["jours"] == 5
    assert h[0]["heure"].startswith("23:")
    assert detecter(actions_du_soir(2), maintenant=maintenant) == []        # pas assez de jours


def test_automatisation_ha():
    ident, conf = automatisation({"appareil": "light.salon", "nom": "Salon", "action": "eteindre", "heure": "23:10"})
    assert ident.startswith("assistant_") and conf["triggers"][0]["at"] == "23:10:00"
    assert conf["actions"][0] == {"action": "light.turn_off", "target": {"entity_id": "light.salon"}}
    with pytest.raises(ValueError):
        automatisation({"appareil": "climate.salon", "nom": "x", "action": "regler", "heure": "07:00"})


def test_routines(tmp_path):
    r = Routines(tmp_path)
    r.enregistrer("Mode cinéma", [{"entity_ids": ["cover.volet"], "action": "fermer"},
                                  {"entity_ids": ["light.salon"], "action": "regler", "valeur": 20}])
    assert r.trouver("lance le mode cinéma")["nom"] == "Mode cinéma"
    assert r.trouver("Mode cinema")["etapes"][1]["valeur"] == 20.0
    assert r.trouver("allume le salon") is None
    assert Routines(tmp_path).trouver("mode cinéma")                          # gardée sur disque
    with pytest.raises(ValueError):
        r.enregistrer("Pirate", [{"entity_ids": ["lock.porte"], "action": "deverrouiller"}])


def test_routine_lancee_depuis_le_chat(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from assistant.config import SecuriteConfig, charger
    from assistant.main import creer_app
    from assistant.securite import Securite
    from tests.test_home_assistant import CFG_HA
    from tests.test_maison import FauxHA
    from tests.test_routeur import routeur
    Routines(tmp_path).enregistrer("soirée", [{"entity_ids": ["light.plafonnier"], "action": "eteindre"}])
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml")).model_copy(update={"home_assistant": CFG_HA})
    ha = FauxHA()
    c = TestClient(creer_app(cfg, r.registre, ha, securite=Securite(SecuriteConfig(), tmp_path)), client=("127.0.0.1", 1))
    rep = c.post("/api/chat", json={"message": "lance la routine soirée", "conversation": "z"}).json()
    assert rep["reponse"] == "Soirée : c'est lancé."
    assert ("light", "turn_off", {"entity_id": "light.plafonnier"}) in ha.appels
