"""Cartes du chat : construites par le serveur d'après l'état réel, gestes directs sans modèle."""

import asyncio
import json

from fastapi.testclient import TestClient

from assistant.maison.cartes import cartes_pour, carte
from assistant.maison.inventaire import Entite
from tests.test_maison import maison  # noqa: F401 - fixture
from tests.test_outils_claude import _app

LAMPE_COULEUR = {"entity_id": "light.lampe_pied", "state": "on", "attributes": {
    "brightness": 102, "color_mode": "color_temp", "color_temp_kelvin": 2700,
    "supported_color_modes": ["color_temp", "hs"], "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500}}


def test_carte_lumiere_couleur():
    c = carte(Entite("light.lampe_pied", "Lampe sur pied", "Salon"), LAMPE_COULEUR)
    assert c["type"] == "lumiere" and c["allume"] and c["intensite"] == 40 and c["kelvin"] == 2700
    assert c["reglages"]["blanc"] == [2000, 6500] and "rouge" in c["reglages"]["couleurs"]


def test_carte_lumiere_simple_et_eteinte():
    s = {"state": "off", "attributes": {"supported_color_modes": ["onoff"]}}
    c = carte(Entite("light.x", "Veilleuse"), s)
    assert not c["allume"] and c["intensite"] is None
    assert c["reglages"] == {"intensite": False, "blanc": None, "couleurs": None}


def test_carte_indisponible_et_types():
    assert carte(Entite("light.x", "X"), {"state": "unavailable", "attributes": {}})["disponible"] is False
    assert carte(Entite("switch.p", "Prise"), {"state": "on"})["type"] == "interrupteur"
    assert carte(Entite("cover.v", "Volet"), {"state": "open", "attributes": {"current_position": 30}})["position"] == 30
    assert carte(Entite("climate.s", "Thermostat"), {"state": "heat"}) is None   # pas encore de carte


def test_plusieurs_appareils_une_carte_groupe_sans_serrure(maison):  # noqa: F811
    m, _ = maison
    cartes = asyncio.run(cartes_pour(m, ["light.plafonnier", "light.lampe_pied", "lock.porte", "inconnu.x"]))
    assert len(cartes) == 1 and cartes[0]["type"] == "groupe" and cartes[0]["titre"] == "Salon"
    assert [c["entity_id"] for c in cartes[0]["elements"]] == ["light.plafonnier", "light.lampe_pied"]
    assert asyncio.run(cartes_pour(m, ["lock.porte"])) == []


# --- API -------------------------------------------------------------------------------

def test_geste_intensite_couleur_blanc_et_annuler(tmp_path):
    app, faux = _app(tmp_path)
    c = TestClient(app)
    r = c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "intensite": 30, "conversation": "k"})
    assert r.status_code == 200 and r.json()["carte"]["type"] == "lumiere"
    assert faux.appels[-1] == ("light", "turn_on", {"entity_id": "light.plafonnier", "brightness_pct": 30})
    c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "couleur": "bleu", "conversation": "k"})
    assert faux.appels[-1][2]["color_name"] == "blue"
    c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "kelvin": 2700, "conversation": "k"})
    assert faux.appels[-1][2]["color_temp_kelvin"] == 2700
    c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "allume": False, "conversation": "k"})
    assert faux.appels[-1][1] == "turn_off"
    a = c.post("/api/maison/carte/annuler", json={"conversation": "k"}).json()
    assert a["ok"] and "remis" in a["texte"] and a["cartes"][0]["entity_id"] == "light.plafonnier"


def test_geste_refuse(tmp_path):
    app, faux = _app(tmp_path)
    c = TestClient(app)
    assert c.post("/api/maison/carte", json={"entity_id": "lock.porte", "allume": True}).status_code == 404
    assert c.post("/api/maison/carte", json={"entity_id": "light.inconnue", "allume": True}).status_code == 404
    assert c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "couleur": "fuchsia"}).status_code == 400
    assert c.post("/api/maison/carte", json={"entity_id": "light.plafonnier"}).status_code == 400
    assert c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "intensite": 300}).status_code == 422
    assert faux.appels == []


def test_commande_par_les_regles_renvoie_une_carte(tmp_path):
    app, _ = _app(tmp_path)
    c = TestClient(app)
    r = c.post("/api/chat", json={"message": "allume la lampe sur pied", "conversation": "r"}).json()
    assert r["fournisseur"] == "maison" and r["cartes"][0]["entity_id"] == "light.lampe_pied"
    lignes = [json.loads(x) for x in c.post("/api/chat", json={
        "message": "éteins le plafonnier", "conversation": "r", "flux": True}).text.splitlines()]
    cles = [next(iter(x)) for x in lignes]
    assert cles == ["route", "delta", "cartes", "fin"]
    assert lignes[2]["cartes"][0]["entity_id"] == "light.plafonnier"
