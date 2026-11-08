"""Mémoire : souvenirs foyer / privés, validation, recherche, historique, et dans la conversation."""

from pathlib import Path

from fastapi.testclient import TestClient

from assistant.memoire import Memoire


def test_cycle_d_un_souvenir(tmp_path):
    m = Memoire(tmp_path)
    s = m.ajouter("La fille de Benjamin s'appelle Léa", "foyer", "principal")
    assert s["statut"] == "propose" and m.chercher("comment s'appelle ma fille ?", "principal") == []   # pas encore validé
    m.changer_statut(s["id"], "valide", "principal")
    assert m.chercher("Léa a quel âge ?", "autre")[0]["id"] == s["id"]          # foyer : visible par tous
    m.modifier(s["id"], "La fille de Benjamin s'appelle Léa, elle a 7 ans", None, "principal")
    assert [h["action"] for h in m.historique(s["id"])] == ["modifié", "validé", "proposé"]
    assert m.ajouter("la fille de benjamin s'appelle léa, elle a 7 ans", "foyer", "x")["id"] == s["id"]  # pas de doublon


def test_souvenir_prive(tmp_path):
    m = Memoire(tmp_path)
    s = m.ajouter("Benjamin n'aime pas la lumière froide", "prive", "principal", "valide")
    assert m.chercher("mets une lumière froide", "principal")[0]["id"] == s["id"]
    assert m.chercher("mets une lumière froide", "invite") == []
    assert m.chercher("mets une lumière froide", "principal", prive=False) == []   # appareil partagé
    assert all(x["id"] != s["id"] for x in m.liste("invite"))


def client_machine(tmp_path):
    from assistant.config import SecuriteConfig, charger
    from assistant.main import creer_app
    from assistant.securite import Securite
    from tests.test_routeur import routeur
    r, rapide = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    a = creer_app(cfg, r.registre, securite=Securite(SecuriteConfig(), tmp_path))
    return TestClient(a, client=("127.0.0.1", 1)), rapide


def test_retiens_puis_oui(tmp_path):
    c, modele = client_machine(tmp_path)
    r = c.post("/api/chat", json={"message": "Retiens que le code du portail est sur le frigo", "conversation": "k1"}).json()
    assert r["reponse"].startswith("Je retiens « Le code du portail est sur le frigo », pour toute la maison")
    assert c.get("/api/memoire").json()["a_valider"] == 1
    assert c.post("/api/chat", json={"message": "oui", "conversation": "k1"}).json()["reponse"] == "C'est retenu."
    assert c.get("/api/memoire").json()["souvenirs"][0]["statut"] == "valide"
    # le souvenir est donné au modèle quand il est utile
    c.post("/api/chat", json={"message": "où est le code du portail ?", "conversation": "k2", "role": "rapide"})
    systeme = " ".join(m.content for m in modele.appels[-1][0] if m.role == "system")
    assert "frigo" in systeme


def test_retiens_puis_non_et_ecran(tmp_path):
    c, _ = client_machine(tmp_path)
    c.post("/api/chat", json={"message": "Souviens-toi que je préfère le thé", "conversation": "k3"})
    assert c.post("/api/chat", json={"message": "non", "conversation": "k3"}).json()["reponse"].startswith("D'accord")
    assert c.get("/api/memoire").json()["souvenirs"] == []
    s = c.post("/api/memoire", json={"texte": "Les poubelles sortent le mardi soir", "portee": "foyer"}).json()
    assert s["statut"] == "valide"
    assert c.patch(f"/api/memoire/{s['id']}", json={"texte": "Les poubelles sortent le lundi soir"}).status_code == 200
    assert len(c.get(f"/api/memoire/{s['id']}/historique").json()["historique"]) == 2
    assert c.delete(f"/api/memoire/{s['id']}").status_code == 200
    assert c.get("/api/memoire").json()["souvenirs"] == []


def test_fait_perso_propose_discretement(tmp_path):
    c, _ = client_machine(tmp_path)
    c.post("/api/chat", json={"message": "je suis allergique aux arachides", "conversation": "k4", "role": "rapide"})
    s = c.get("/api/memoire").json()["souvenirs"]
    assert s[0]["statut"] == "propose" and s[0]["portee"] == "prive" and "arachides" in s[0]["texte"]


def test_correction_retenue(tmp_path):
    c, _ = client_machine(tmp_path)
    r = c.post("/api/chat", json={"message": "non, à l'avenir mets plutôt une lumière chaude le soir", "conversation": "k5"}).json()
    assert r["reponse"].startswith("Compris. Je retiens pour la suite")
    assert c.post("/api/chat", json={"message": "oui", "conversation": "k5"}).json()["reponse"] == "C'est retenu."
    s = c.get("/api/memoire").json()["souvenirs"][0]
    assert s["statut"] == "valide" and s["portee"] == "prive" and "lumière chaude" in s["texte"]


def test_retient_mal_ecrit_par_whisper(tmp_path):
    c, _ = client_machine(tmp_path)
    r = c.post("/api/chat", json={"message": "retient que j'ai 10 disques durs", "conversation": "w1"}).json()
    assert r["reponse"].startswith("Je retiens") and "10 disques durs" in r["reponse"]


def test_mise_a_jour_validee_remplace_l_ancien(tmp_path):
    m = Memoire(tmp_path)
    a = m.ajouter("Benjamin a 10 disques durs", "prive", "b", "valide")
    n = m.ajouter("Benjamin a 8 disques durs", "prive", "b", "propose", "claude", "c1", remplace=a["id"])
    m.changer_statut(n["id"], "valide", "b")
    assert [x["texte"] for x in m.liste("b", ("valide",))] == ["Benjamin a 8 disques durs"]
    assert m.historique(a["id"])[0]["action"] == "remplacé"


def test_renommer_le_compte_met_a_jour_les_souvenirs(tmp_path):
    m = Memoire(tmp_path)
    m.ajouter("Principal : j'ai 10 disques durs", "prive", "principal", "valide")
    assert m.renommer_compte("principal", "Principal", "Benjamin") == 1
    assert m.liste("principal", ("valide",))[0]["texte"] == "Benjamin : j'ai 10 disques durs"


def test_tous_les_souvenirs_et_la_note_pour_claude(tmp_path):
    c, modele = client_machine(tmp_path)
    c.post("/api/memoire", json={"texte": "Benjamin a 10 disques durs", "portee": "foyer"})
    c.post("/api/chat", json={"message": "combien de HDD dans la tour ?", "conversation": "w2", "role": "rapide"})
    systeme = " ".join(x.content for x in modele.appels[-1][0] if x.role == "system")
    assert "10 disques durs" in systeme and "ne dis jamais « noté »" in systeme      # même sans mot en commun


def test_creer_une_routine_n_allume_rien(tmp_path):
    from assistant.config import SecuriteConfig, charger
    from assistant.main import creer_app
    from assistant.securite import Securite
    from tests.test_home_assistant import CFG_HA
    from tests.test_maison import FauxHA
    from tests.test_routeur import routeur
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml")).model_copy(update={"home_assistant": CFG_HA})
    ha = FauxHA()
    c = TestClient(creer_app(cfg, r.registre, ha, securite=Securite(SecuriteConfig(), tmp_path)), client=("127.0.0.1", 1))
    rep = c.post("/api/chat", json={"message": "Crée une routine qui met le plafonnier en rouge", "conversation": "w3"}).json()
    assert ha.appels == [] and rep["route"]["role"] == "raisonnement"
