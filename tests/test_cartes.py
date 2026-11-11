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
    assert c["reglages"]["blanc"] == [2000, 6500] and len(c["reglages"]["couleurs"]) == 12
    assert {"nom": "rouge", "teinte": 0} in c["reglages"]["couleurs"]


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
    assert cles == ["route", "cartes", "delta", "fin"]
    assert lignes[1]["cartes"][0]["entity_id"] == "light.plafonnier"


# --- cercle chromatique et réglage de plusieurs appareils ------------------------------

def test_teinte_du_cercle_chromatique(tmp_path):
    app, faux = _app(tmp_path)
    c = TestClient(app)
    r = c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "teinte": 200, "saturation": 80, "conversation": "t"})
    assert r.status_code == 200 and "bleu ciel" in r.json()["texte"]
    assert faux.appels[-1] == ("light", "turn_on", {"entity_id": "light.plafonnier", "hs_color": [200, 80]})
    r = c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "teinte": 10, "conversation": "t"})
    assert faux.appels[-1][2]["hs_color"] == [10, 100] and "rouge" in r.json()["texte"]
    assert c.post("/api/maison/carte", json={"entity_id": "light.plafonnier", "teinte": 400}).status_code == 422


def test_nom_teinte():
    from assistant.maison import nom_teinte
    assert nom_teinte(355) == "rouge" and nom_teinte(120) == "vert" and nom_teinte(240, 30) == "bleu pâle"
    assert nom_teinte(60, 5) == "blanc"


def _couleur(monkeypatch, *eids):
    """Donne la couleur à ces lampes du faux Home Assistant (le temps du test)."""
    from tests import test_maison
    etats = [dict(s, attributes={**s["attributes"], "supported_color_modes": ["hs", "color_temp"]})
             if s["entity_id"] in eids else s for s in test_maison.ETATS]
    monkeypatch.setattr(test_maison, "ETATS", etats)


def test_couleur_pour_tous_seulement_les_compatibles(tmp_path, monkeypatch):
    _couleur(monkeypatch, "light.plafonnier", "light.lampe_pied")
    app, faux = _app(tmp_path)
    c = TestClient(app)
    r = c.post("/api/maison/carte/groupe", json={
        "entity_ids": ["light.plafonnier", "light.lampe_pied", "light.cuisine", "switch.prise_tv", "lock.porte"],
        "teinte": 270, "conversation": "g"})
    assert r.status_code == 200, r.text
    o = r.json()
    assert [x["entity_id"] for x in o["cartes"]] == ["light.plafonnier", "light.lampe_pied"]
    assert set(o["ecartes"]) == {"light.cuisine", "switch.prise_tv"}       # la serrure n'est même pas citée
    assert [a[2] for a in faux.appels] == [{"entity_id": "light.plafonnier", "hs_color": [270, 100]},
                                           {"entity_id": "light.lampe_pied", "hs_color": [270, 100]}]
    # un seul « annule » remet les deux lampes
    a = c.post("/api/maison/carte/annuler", json={"conversation": "g"}).json()
    assert a["ok"] and {x["entity_id"] for x in a["cartes"][0]["elements"]} == {"light.plafonnier", "light.lampe_pied"}


def test_groupe_un_choix_et_refus(tmp_path, monkeypatch):
    _couleur(monkeypatch, "light.lampe_pied")
    app, faux = _app(tmp_path)
    c = TestClient(app)
    # un seul appareil choisi dans le groupe
    assert c.post("/api/maison/carte/groupe", json={"entity_ids": ["light.lampe_pied"], "couleur": "bleu"}).status_code == 200
    assert faux.appels == [("light", "turn_on", {"entity_id": "light.lampe_pied", "color_name": "blue"})]
    # aucun compatible : refus clair, rien d'envoyé
    r = c.post("/api/maison/carte/groupe", json={"entity_ids": ["switch.prise_tv"], "teinte": 30})
    assert r.status_code == 409 and "accepte" in r.json()["detail"]
    assert c.post("/api/maison/carte/groupe", json={"entity_ids": ["lock.porte"], "allume": True}).status_code == 404
    assert c.post("/api/maison/carte/groupe", json={"entity_ids": [], "allume": True}).status_code == 422
    # allumer pour tous : tout appareil joignable
    c.post("/api/maison/carte/groupe", json={"entity_ids": ["light.cuisine", "switch.prise_tv"], "allume": True})
    assert [a[:2] for a in faux.appels[-2:]] == [("light", "turn_on"), ("switch", "turn_on")]
    assert len(faux.appels) == 3


# --- la carte renvoyée montre la valeur demandée, pas l'avant-dernière ------------------

class _HALent:
    """Home Assistant qui ne montre le nouvel état qu'après quelques lectures (comme une vraie lampe)."""

    def __init__(self, etat, lectures_avant_maj=3):
        self.etat, self.suivant, self.reste = etat, None, 0
        self.lectures_avant_maj = lectures_avant_maj

    def appliquer(self, attributs):
        self.suivant = {**self.etat, "attributes": {**self.etat["attributes"], **attributs}}
        self.reste = self.lectures_avant_maj

    async def etat_entite(self, eid):
        if self.suivant is not None:
            if self.reste <= 0:
                self.etat, self.suivant = self.suivant, None
            else:
                self.reste -= 1
        return self.etat


def test_carte_apres_attend_la_valeur_demandee(monkeypatch):
    from assistant.maison import cartes as mod
    monkeypatch.setattr(mod, "ATTENTE_REGLAGE", 12)
    dormir = asyncio.sleep
    monkeypatch.setattr(mod.asyncio, "sleep", lambda _s: dormir(0))
    ha = _HALent(LAMPE_COULEUR)
    m = type("M", (), {"ha": ha})()
    e = Entite("light.lampe_pied", "Lampe sur pied", "Salon")
    avant = carte(e, LAMPE_COULEUR)
    ha.appliquer({"brightness": 191})                     # 75 %
    c = asyncio.run(mod._carte_apres(m, e, {"intensite": 75}, avant))
    assert c["intensite"] == 75
    ha.appliquer({"color_temp_kelvin": 4000})
    c = asyncio.run(mod._carte_apres(m, e, {"kelvin": 4000}, c))
    assert c["kelvin"] == 4000


def test_atteint_tolerances():
    from assistant.maison.cartes import atteint
    lampe = {"allume": True, "intensite": 49, "kelvin": 3984, "hs": [358.0, 97.0]}
    assert atteint(lampe, {"intensite": 50}) and not atteint(lampe, {"intensite": 60})
    assert atteint(lampe, {"kelvin": 4000}) and not atteint(lampe, {"kelvin": 3000})
    assert atteint(lampe, {"teinte": 2, "saturation": 100}) and not atteint(lampe, {"teinte": 120})
    assert atteint({"allume": False}, {"allume": False}) and not atteint(lampe, {"allume": False})
    assert atteint({"ouvert": True, "position": 49}, {"position": 50})
