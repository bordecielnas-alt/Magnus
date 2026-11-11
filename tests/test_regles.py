"""Règles « si… alors… » : validation, relecture, déclenchement sur front, pause, création à la voix."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from assistant.regles import Regles, decrire, normaliser


def temp(v):
    return {"sensor.temp_salon": {"entity_id": "sensor.temp_salon", "state": str(v),
                                  "attributes": {"friendly_name": "Température salon", "unit_of_measurement": "°C"}}}


def presence(*chez_soi):
    return {f"person.{n}": {"entity_id": f"person.{n}", "state": "home" if n in chez_soi else "not_home"}
            for n in ("benjamin", "lea")}


def test_validation():
    with pytest.raises(ValueError):
        normaliser({"type": "meteo"}, [{"entity_ids": ["light.x"], "action": "allumer"}])
    with pytest.raises(ValueError):                       # ni action ni annonce
        normaliser({"type": "presence", "evenement": "part"}, [])
    with pytest.raises(ValueError):
        normaliser({"type": "seuil", "entity_id": "sensor.t", "comparaison": "<"}, [], "froid")
    with pytest.raises(ValueError):
        normaliser({"type": "presence", "evenement": "arrive"}, [{"entity_ids": ["lock.porte"], "action": "deverrouiller"}])
    with pytest.raises(ValueError):
        normaliser({"type": "presence", "evenement": "arrive"}, [], "coucou", "le soir")
    c, _, ann, plage = normaliser({"type": "seuil", "entity_id": "sensor.t", "comparaison": "sous", "valeur": "19"},
                                  [], " Il  fait froid ", "22:00-7:00")
    assert c["comparaison"] == "<" and c["valeur"] == 19.0 and ann == "Il fait froid" and plage == "22:00-07:00"


def test_relecture():
    noms = {"sensor.temp_salon": "la température du salon", "light.entree": "Entrée", "cover.volet": "Volet"}
    r = {"condition": {"type": "seuil", "entity_id": "sensor.temp_salon", "comparaison": "<", "valeur": 19.5},
         "actions": [{"entity_ids": ["light.entree"], "action": "regler", "valeur": 30}], "annonce": "Il fait frais."}
    assert decrire(r, noms, {"sensor.temp_salon": "°C"}) == (
        "Quand la température du salon passe sous 19,5 °C, je règle Entrée à 30 % et je préviens : « Il fait frais. ».")
    r = {"condition": {"type": "presence", "evenement": "part"}, "plage": "22:00-07:00",
         "actions": [{"entity_ids": ["light.entree"], "action": "eteindre"}, {"entity_ids": ["cover.volet"], "action": "fermer"}]}
    assert decrire(r, noms) == "Quand tout le monde est parti, entre 22h00 et 07h00, j'éteins Entrée et je ferme Volet."
    r = {"condition": {"type": "etat", "entity_id": "cover.volet", "etat": "open", "minutes": 10}, "annonce": "Volet ouvert"}
    assert decrire(r, noms).startswith("Quand Volet reste ouvert plus de 10 minutes, je préviens")


def test_seuil_sur_front_avec_marge(tmp_path):
    rg = Regles(tmp_path)
    r = rg.enregistrer({"condition": {"type": "seuil", "entity_id": "sensor.temp_salon", "comparaison": "<", "valeur": 19},
                        "annonce": "Il fait froid au salon."})
    assert rg.a_declencher(temp(18)) == ([], [])          # premier passage : mémorisé, pas déclenché
    rg.vraies.clear()
    assert rg.a_declencher(temp(20)) == ([], [])
    assert rg.a_declencher(temp(18.8))[0] == [r]          # passe sous 19 : déclenché
    assert rg.a_declencher(temp(18.5))[0] == []           # reste dessous : pas de répétition
    assert rg.a_declencher(temp(19.2)) == ([], [])        # oscille autour du seuil : toujours rien
    assert rg.a_declencher(temp(19.6))[1] == [r]          # vraiment remonté : réarmé
    assert rg.a_declencher(temp(18.9))[0] == [r]
    assert rg.a_declencher({}) == ([], [])                # capteur absent : on ne sait pas, rien ne change


def test_presence_plage_et_pause(tmp_path):
    rg = Regles(tmp_path)
    part = rg.enregistrer({"condition": {"type": "presence", "evenement": "part"},
                           "actions": [{"entity_ids": ["light.plafonnier"], "action": "eteindre"}]})
    lea = rg.enregistrer({"condition": {"type": "presence", "evenement": "arrive", "entity_id": "person.lea"},
                          "annonce": "Léa est rentrée.", "plage": "18:00-23:00"})
    midi, soir = datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 19, 0)
    rg.a_declencher(presence("benjamin"), midi)
    assert rg.a_declencher(presence("benjamin", "lea"), midi)[0] == []      # Léa arrive à midi : hors créneau
    assert rg.a_declencher(presence("lea"), soir)[0] == []                   # quelqu'un reste
    assert rg.a_declencher(presence(), soir)[0] == [part]                    # le dernier part
    assert rg.a_declencher(presence("lea"), soir)[0] == [lea]                # Léa revient le soir
    rg.activer(part["id"], False)
    rg.a_declencher(presence(), soir)
    assert rg.a_declencher(presence(), soir)[0] == []
    rechargees = Regles(tmp_path)                                            # gardé sur disque
    assert [x["active"] for x in rechargees.liste] == [False, True]
    rg.activer(part["id"], True)
    assert rg.a_declencher(presence(), soir)[0] == []                        # reprise : pas sur un état déjà vrai
    assert rg.supprimer(lea["id"]) and len(Regles(tmp_path).liste) == 1


def test_etat_qui_dure(tmp_path):
    rg = Regles(tmp_path)
    r = rg.enregistrer({"condition": {"type": "etat", "entity_id": "cover.garage", "etat": "open", "minutes": 10},
                        "annonce": "Le garage est ouvert."})

    def garage(etat, il_y_a):
        quand = (datetime.now(timezone.utc) - timedelta(minutes=il_y_a)).isoformat()
        return {"cover.garage": {"entity_id": "cover.garage", "state": etat, "last_changed": quand}}
    rg.a_declencher(garage("closed", 60))
    assert rg.a_declencher(garage("open", 3))[0] == []
    assert rg.a_declencher(garage("open", 11))[0] == [r]
    assert rg.a_declencher(garage("open", 30))[0] == []


def app_regles(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import assistant.main as main
    from assistant.config import SecuriteConfig, charger
    from assistant.securite import Securite
    from tests.test_home_assistant import CFG_HA
    from tests.test_maison import ETATS, FauxHA
    from tests.test_routeur import routeur

    capteurs = [{"entity_id": "sensor.temp_salon", "state": "20.5",
                 "attributes": {"friendly_name": "Température salon", "unit_of_measurement": "°C",
                                "device_class": "temperature"}},
                {"entity_id": "person.benjamin", "state": "home", "attributes": {"friendly_name": "Benjamin"}}]

    class HA(FauxHA):
        async def ws(self, commandes):
            r = await super().ws(commandes)
            if main_cmd in r:
                r[main_cmd] = ETATS + capteurs
            return r

        async def etat_entite(self, eid):
            return next((s for s in ETATS + capteurs if s["entity_id"] == eid), None)
    from assistant.maison.inventaire import CMD_ETATS as main_cmd
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "j")
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml")).model_copy(update={"home_assistant": CFG_HA})
    ha = HA()
    app = main.creer_app(cfg, r.registre, ha, securite=Securite(SecuriteConfig(), tmp_path))
    return app, TestClient(app, client=("127.0.0.1", 1)), ha


def test_regle_creee_a_la_voix_puis_mise_en_pause(tmp_path, monkeypatch):
    app, c, ha = app_regles(tmp_path, monkeypatch)
    h = {"X-Jeton-Interne": "j"}
    c.post("/api/chat", json={"message": "si la température du salon passe sous 19, allume le plafonnier",
                              "conversation": "x"})
    assert ha.appels == []                                 # pas pris pour « allume le plafonnier »
    capteurs = c.get("/interne/capteurs", params={"filtre": "température salon"}, headers=h).json()
    assert capteurs[0]["entity_id"] == "sensor.temp_salon" and capteurs[0]["unite"] == "°C"
    assert "person.benjamin" not in [x["entity_id"] for x in capteurs]
    # refus clairs : capteur inconnu, action sensible
    assert c.post("/interne/regles", headers=h, json={
        "condition": {"type": "seuil", "entity_id": "sensor.inconnu", "comparaison": "<", "valeur": 19},
        "annonce": "froid", "conversation": "x"}).status_code == 422
    assert "sensible" in c.post("/interne/regles", headers=h, json={
        "condition": {"type": "presence", "evenement": "part"},
        "actions": [{"entity_ids": ["climate.salon"], "action": "eteindre"}], "conversation": "x"}).json()["detail"]
    rep = c.post("/interne/regles", headers=h, json={
        "condition": {"type": "seuil", "entity_id": "sensor.temp_salon", "comparaison": "<", "valeur": 19},
        "actions": [{"entity_ids": ["light.plafonnier"], "action": "allumer"}], "conversation": "x"}).json()
    assert rep["a_dire"] == "Je crée cette règle : Quand Température salon passe sous 19 °C, j'allume Plafonnier. D'accord ?"
    assert c.get("/api/regles").json()["regles"] == []      # rien avant le oui
    fin = c.post("/api/chat", json={"message": "oui", "conversation": "x"}).json()
    assert fin["reponse"].startswith("C'est enregistré")
    regles = c.get("/api/regles").json()["regles"]
    assert len(regles) == 1 and regles[0]["active"] and regles[0]["texte"].startswith("Quand Température salon")
    # « non » : rien n'est gardé
    c.post("/interne/regles", headers=h, json={"condition": {"type": "presence", "evenement": "part"},
                                               "annonce": "Maison vide.", "conversation": "x"})
    assert c.post("/api/chat", json={"message": "non", "conversation": "x"}).json()["reponse"].startswith("D'accord")
    assert len(c.get("/api/regles").json()["regles"]) == 1

    # déclenchement : 20,5 °C (mémorisé) puis 18 °C
    ident = regles[0]["id"]
    evaluer = app.state.evaluer_regles_une_fois
    asyncio.run(evaluer(temp(20.5)))
    asyncio.run(evaluer(temp(18)))
    assert ("light", "turn_on", {"entity_id": "light.plafonnier"}) in ha.appels
    assert c.get("/api/regles").json()["regles"][0]["derniere"]

    # pause depuis la page, puis reprise à la voix (outil changer_regle)
    assert c.post(f"/api/regles/{ident}", json={"active": False}).json()["active"] is False
    ha.appels.clear()
    asyncio.run(evaluer(temp(20)))
    asyncio.run(evaluer(temp(18)))
    assert ha.appels == []
    assert c.get("/interne/regles", headers=h).json()[0]["active"] is False
    assert c.post(f"/interne/regles/{ident}", headers=h, json={"active": True}).json()["active"] is True
    assert c.delete(f"/api/regles/{ident}").status_code == 200
    assert c.get("/api/regles").json()["regles"] == []


def test_annonce_d_une_regle(tmp_path, monkeypatch):
    app, _, _ = app_regles(tmp_path, monkeypatch)
    rg = app.state.regles
    rg.enregistrer({"condition": {"type": "presence", "evenement": "part"}, "annonce": "Tout le monde est parti."})
    evaluer = app.state.evaluer_regles_une_fois
    asyncio.run(evaluer(presence("benjamin")))
    asyncio.run(evaluer(presence()))
    asyncio.run(evaluer(presence()))
    textes = [a["texte"] for a in app.state.annonces.liste]
    assert textes == ["Tout le monde est parti."]
    asyncio.run(evaluer(presence("lea")))                 # quelqu'un revient : l'annonce pourra être refaite
    asyncio.run(evaluer(presence()))
    assert len(app.state.annonces.liste) == 2


def test_phrases_reconnues():
    from assistant.main import RE_CREER_REGLE, RE_GERER_REGLE
    oui = ["si la température du salon descend sous 19 degrés, allume le radiateur",
           "quand tout le monde part, éteins toutes les lumières",
           "préviens-moi si la porte du garage reste ouverte plus de 10 minutes",
           "allume la lumière de l'entrée dès que quelqu'un arrive",
           "Magnus, crée une règle pour le salon"]
    non = ["allume le salon", "si tu peux, allume le salon", "s'il te plaît, éteins tout",
           "quand est-ce que le salon a été allumé ?", "rappelle-moi demain de sortir le linge"]
    assert all(RE_CREER_REGLE.search(t) for t in oui)
    assert RE_GERER_REGLE.search("mets la règle du garage en pause") and RE_GERER_REGLE.search("quelles règles as-tu ?")
    assert not RE_GERER_REGLE.search("règle la lumière du salon à 40") and not RE_CREER_REGLE.search("règle le salon à 40")
    assert not any(RE_CREER_REGLE.search(t) for t in non)
