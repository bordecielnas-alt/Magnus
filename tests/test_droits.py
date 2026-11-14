"""Droits fins : catalogue de permissions, groupes et sous-groupes, réglages par personne, et leur effet sur
chaque outil (API, outils de Claude, chat, annonces). Chaque permission a au moins un test qui la refuse."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import assistant.main as main
from assistant import droits as D
from assistant import mcp_maison
from assistant.config import SecuriteConfig, charger
from assistant.securite import AccesRefuse, Securite
from tests.test_routeur import routeur

MDP = "mot de passe solide"
LAN = "192.168.1.30"


# --- calcul des droits (fonctions pures) ------------------------------------------------------------
GROUPES = {
    "famille": {"nom": "Famille", "parent": "", "permissions": ["maison.voir", "agenda.lire", "agenda.ecrire"],
                "retraits": []},
    "ados": {"nom": "Ados", "parent": "famille", "permissions": ["assistant.web"], "retraits": ["agenda.ecrire"]},
    "petits": {"nom": "Petits", "parent": "ados", "permissions": [], "retraits": ["assistant.web"]},
    "serveur": {"nom": "Serveur", "parent": "", "permissions": ["serveur.etat", "serveur.diagnostic"], "retraits": []},
}


def test_un_sous_groupe_herite_puis_ajoute_et_retire():
    assert D.effectif_groupe(GROUPES, "famille") == {"maison.voir", "agenda.lire", "agenda.ecrire"}
    assert D.effectif_groupe(GROUPES, "ados") == {"maison.voir", "agenda.lire", "assistant.web"}
    assert D.effectif_groupe(GROUPES, "petits") == {"maison.voir", "agenda.lire"}       # deux niveaux


def test_plusieurs_groupes_et_reglages_propres():
    assert D.effectif(GROUPES, ["petits", "serveur"]) == {"maison.voir", "agenda.lire", "serveur.etat",
                                                          "serveur.diagnostic"}
    # un ajout propre, puis un retrait propre qui a le dernier mot (même sur ce qu'un groupe donne)
    assert "telegram.envoyer" in D.effectif(GROUPES, ["petits"], ["telegram.envoyer"])
    assert "serveur.etat" not in D.effectif(GROUPES, ["serveur"], ["serveur.etat"], ["serveur.etat"])
    assert D.effectif(GROUPES, []) == set()
    assert D.effectif(GROUPES, ["inconnu"]) == set()


def test_boucle_coupee_et_detectee():
    g = {"a": {"parent": "b", "permissions": ["maison.voir"]}, "b": {"parent": "a", "permissions": ["agenda.lire"]}}
    assert D.effectif_groupe(g, "a") == {"maison.voir", "agenda.lire"}                   # pas de boucle infinie
    assert D.cree_une_boucle(GROUPES, "famille", "petits") and not D.cree_une_boucle(GROUPES, "petits", "serveur")


def test_permission_inconnue_refusee():
    with pytest.raises(D.DroitInvalide):
        D.valider(["serveur.tout"])
    assert D.valider(["agenda.lire", "agenda.lire"]) == ["agenda.lire"]


def test_catalogue_complet_et_categories():
    ids = [p["id"] for r in D.catalogue() for p in r["permissions"]]
    assert len(ids) == len(set(ids)) == len(D.PERMISSIONS)
    assert all(f"maison.piloter.{c}" in ids for c in D.CATEGORIES)
    assert D.interdits({"maison.piloter.lumieres"}) == ["chauffage", "medias", "prises", "ventilateurs", "volets"]


# --- comptes : groupes par défaut, migration, administration -----------------------------------------
def sec_avec_lea(tmp_path) -> Securite:
    sec = Securite(SecuriteConfig(totp_obligatoire=False), tmp_path)
    sec.creer_premier(MDP)
    sec.creer_compte("Léa")
    sec.definir_mot_de_passe("lea", "mot de passe de léa")
    return sec


def test_groupes_par_defaut_selon_le_type(tmp_path):
    sec = sec_avec_lea(tmp_path)
    assert sec.groupes_de("lea") == ["adultes"]
    adulte = sec.permissions("lea")
    assert {"agenda.ecrire", "automatismes.regles", "maison.piloter.chauffage", "assistant.web"} <= adulte
    assert not adulte & {"serveur.etat", "telegram.envoyer", "maison.historique", "automatismes.habitudes"}
    sec.definir_profil("lea", {"type": "enfant"})
    enfant = sec.permissions("lea")
    assert sec.groupes_de("lea") == ["enfants"] and enfant < adulte
    assert not enfant & {"maison.piloter.chauffage", "agenda.ecrire", "automatismes.regles", "memoire.foyer"}
    assert sec.permissions("principal") == set(D.PERMISSIONS) and sec.peut("machine", "serveur.redemarrer")
    with pytest.raises(D.DroitInvalide):
        sec.peut("lea", "faute.de.frappe")


def test_migration_des_anciens_droits(tmp_path):
    import json
    (tmp_path / "securite.json").write_text(json.dumps({"comptes": {
        "principal": {"nom": "Principal", "mdp": "x", "admin": True},
        "lea": {"nom": "Léa", "mdp": "x", "profil": {"type": "enfant", "interdits": ["lumieres"]}}}}), encoding="utf-8")
    sec = Securite(SecuriteConfig(), tmp_path)
    assert "groupes" in json.loads((tmp_path / "securite.json").read_text(encoding="utf-8"))
    # le choix explicite d'avant est gardé : pas les lumières, mais le chauffage (rendu par l'admin)
    assert sec.profil("lea")["interdits"] == ["lumieres"]
    assert sec.peut("lea", "maison.piloter.chauffage") and not sec.peut("lea", "maison.piloter.lumieres")


def test_groupes_crud_et_garde_fous(tmp_path):
    sec = sec_avec_lea(tmp_path)
    gid = sec.enregistrer_groupe("", "Ados", "enfants", ["assistant.web"], [])
    assert gid == "ados" and sec.groupes["ados"]["parent"] == "enfants"
    with pytest.raises(D.DroitInvalide):
        sec.enregistrer_groupe("adultes", "Adultes", "ados")             # boucle adultes > enfants > ados
    with pytest.raises(D.DroitInvalide):
        sec.enregistrer_groupe("", "X", "inconnu")
    with pytest.raises(D.DroitInvalide):
        sec.supprimer_groupe("enfants")                                   # a un sous-groupe
    sec.definir_droits("lea", groupes=["ados"])
    with pytest.raises(D.DroitInvalide):
        sec.supprimer_groupe("ados")                                      # a une membre
    with pytest.raises(D.DroitInvalide):
        sec.definir_droits("lea", groupes=["inconnu"])
    with pytest.raises(D.DroitInvalide):
        sec.definir_droits("lea", permissions=["pas.une.permission"])
    assert sec.groupes_de("lea") == ["ados"]                              # rien changé après une erreur
    with pytest.raises(AccesRefuse):
        sec.definir_admin("principal", False)                             # dernier administrateur
    sec.definir_admin("lea", True)
    sec.definir_admin("principal", False)
    assert sec.est_admin("lea") and not sec.est_admin("principal")


# --- outils de Claude ----------------------------------------------------------------------------------
def test_chaque_outil_disparait_sans_sa_permission(monkeypatch):
    for v in ("ASSISTANT_DOCKER", "ASSISTANT_AGENDA", "ASSISTANT_TELEGRAM"):
        monkeypatch.setenv(v, "1")
    monkeypatch.delenv("ASSISTANT_PERMISSIONS", raising=False)
    tous = {o["name"] for o in mcp_maison.outils()}
    assert set(mcp_maison.PERMISSION_OUTIL) <= tous                        # sans liste : comme avant
    for outil, perms in mcp_maison.PERMISSION_OUTIL.items():
        assert all(p in D.PERMISSIONS for p in perms), outil
        monkeypatch.setenv("ASSISTANT_PERMISSIONS", ",".join(sorted(set(D.PERMISSIONS) - set(perms))))
        assert outil not in {o["name"] for o in mcp_maison.outils()}, outil
        with pytest.raises(ValueError, match="pas permis"):
            mcp_maison.appeler(outil, {})
        monkeypatch.setenv("ASSISTANT_PERMISSIONS", perms[0])
        assert outil in {o["name"] for o in mcp_maison.outils()}, outil
    monkeypatch.setenv("ASSISTANT_PERMISSIONS", "")
    # sans aucune permission : seulement les outils personnels (ses règles, ses routines)
    assert {o["name"] for o in mcp_maison.outils()} == {"lister_routines", "lister_regles", "changer_regle"}


# --- application : un compte connecté depuis le réseau local -------------------------------------------
@pytest.fixture
def maison_de_test(tmp_path, monkeypatch):
    """Principal (admin) et Léa (adulte), Docker via Unraid simulé, Telegram et agenda branchés pour de faux."""
    from tests.test_phase6 import faux_unraid
    from assistant.docker_diag import UnraidDocker
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
    cfg.telegram.chat_id = "42"
    monkeypatch.setenv("TELEGRAM_JETON", "123:abc")
    cfg.agenda.url = "http://agenda.test/cal/"
    sec = sec_avec_lea(tmp_path)
    app = main.creer_app(cfg, r.registre, securite=sec)
    machine = TestClient(app, client=("127.0.0.1", 1), follow_redirects=False)

    def connecte(ident, mdp):
        c = TestClient(app, client=(LAN, 50000), follow_redirects=False)
        assert c.post("/api/connexion", json={"identifiant": ident, "mot_de_passe": mdp}).json()["etape"] == "fini"
        return c
    return app, sec, machine, connecte("lea", "mot de passe de léa"), connecte("principal", MDP), vus


H = {"X-Jeton-Interne": "j"}


def chat(c, message, conv="lea-1"):
    return c.post("/api/chat", json={"message": message, "conversation": conv}).json()


def test_infos_et_reglages_reserves_aux_administrateurs(maison_de_test):
    _, _, _, lea, admin, _ = maison_de_test
    for methode, url, corps in [("get", "/api/reglages", None), ("get", "/api/maison", None),
                                ("get", "/api/home-assistant", None), ("post", "/api/maison/rafraichir", None),
                                ("post", "/api/theme", {"theme": "hal"}), ("post", "/api/voix/reglages", {"fenetre_s": 5}),
                                ("get", "/api/comptes", None), ("get", "/api/droits", None), ("get", "/api/audit", None),
                                ("post", "/api/groupes", {"nom": "X"}), ("post", "/api/comptes/lea/droits", {"admin": True}),
                                ("post", "/api/telegram/test", None), ("post", "/api/redemarrer", None)]:
        r = getattr(lea, methode)(url, **({"json": corps} if corps is not None else {}))
        assert r.status_code in (403, 409), (url, r.status_code)
    assert lea.get("/admin").status_code == 303
    assert admin.get("/admin").status_code == 200 and admin.get("/api/reglages").status_code == 200
    s = lea.get("/api/securite").json()
    assert not s["admin"] and "serveur.etat" not in s["permissions"] and "agenda.lire" in s["permissions"]


def test_serveur_permission_par_permission(maison_de_test):
    _, sec, machine, lea, _, vus = maison_de_test
    assert lea.get("/api/docker").status_code == 403 and lea.get("/api/unraid").status_code == 403
    assert lea.post("/api/rapport", json={"type": "docker"}).status_code == 403
    # demandes au chat : refus clair, et Unraid n'est jamais appelé pour agir
    for phrase in ("arrête Plex", "fais le point sur les dockers", "redémarre le conteneur plex"):
        assert chat(lea, phrase)["reponse"] == main.REFUS["serveur"], phrase
    assert not any("mutation" in str(v) for v in vus)
    # outils de Claude dans cette conversation : chacun revérifié
    for url, kw in [("/interne/docker/conteneurs", {"params": {"conversation": "lea-1"}}),
                    ("/interne/unraid", {"params": {"conversation": "lea-1"}}),
                    ("/interne/docker/diagnostic", {"params": {"nom": "plex", "conversation": "lea-1"}})]:
        assert machine.get(url, headers=H, **kw).status_code == 403, url
    for action in ("demarrer", "arreter", "redemarrer", "mettre_a_jour"):
        r = machine.post("/interne/docker/redemarrer", headers=H,
                         json={"nom": "plex", "action": action, "conversation": "lea-1"})
        assert r.status_code == 403, action
    # l'état seulement : lire oui, agir non, et chaque action a sa propre permission
    sec.definir_droits("lea", permissions=["serveur.etat", "serveur.redemarrer"])
    assert lea.get("/api/docker").status_code == 200
    chat(lea, "quel est l'état des dockers ?")
    assert machine.get("/interne/docker/conteneurs", headers=H, params={"conversation": "lea-1"}).status_code == 200
    assert machine.get("/interne/docker/diagnostic", headers=H,
                       params={"nom": "plex", "conversation": "lea-1"}).status_code == 403
    ok = machine.post("/interne/docker/redemarrer", headers=H,
                      json={"nom": "plex", "action": "redemarrer", "conversation": "lea-1"})
    assert ok.status_code == 200
    assert machine.post("/interne/docker/redemarrer", headers=H,
                        json={"nom": "plex", "action": "arreter", "conversation": "lea-1"}).status_code == 403


def test_telegram(maison_de_test):
    _, sec, machine, lea, _, _ = maison_de_test
    assert chat(lea, "envoie un message de test sur Telegram")["reponse"] == main.REFUS["telegram"]
    assert machine.post("/interne/telegram", headers=H, json={"texte": "x", "conversation": "lea-1"}).status_code == 403
    sec.definir_droits("lea", permissions=["telegram.envoyer"])
    chat(lea, "bonjour")
    r = machine.post("/interne/telegram", headers=H, json={"texte": "x", "conversation": "lea-1"})
    assert r.status_code != 403


def test_agenda_lire_et_ecrire(maison_de_test):
    _, sec, machine, lea, _, _ = maison_de_test
    sec.definir_droits("lea", retraits=["agenda.lire", "agenda.ecrire"])
    assert lea.get("/api/agenda").status_code == 403
    assert chat(lea, "qu'est-ce que j'ai à l'agenda demain ?")["reponse"] == main.REFUS["agenda"]
    assert machine.get("/interne/agenda", headers=H, params={"conversation": "lea-1"}).status_code == 403
    sec.definir_droits("lea", retraits=["agenda.ecrire"])                 # lire oui, écrire non
    chat(lea, "bonjour")
    nouvel = {"titre": "Dentiste", "debut": "2030-01-02T10:00", "conversation": "lea-1"}
    assert machine.post("/interne/agenda", headers=H, json=nouvel).status_code == 403
    assert machine.post("/interne/agenda/supprimer", headers=H,
                        json={"ref": "x", "conversation": "lea-1"}).status_code == 403
    assert machine.get("/interne/agenda", headers=H, params={"conversation": "lea-1"}).status_code != 403


def test_historique_et_habitudes(maison_de_test):
    _, sec, _, lea, admin, _ = maison_de_test
    for url in ("/api/actions", "/api/appareils/etat", "/api/habitudes"):
        assert lea.get(url).status_code == 403, url
    assert lea.post("/api/actions/x/annuler").status_code == 403
    assert lea.post("/api/habitudes/accepter", json={"id": "habitude-x"}).status_code == 403
    sec.definir_droits("lea", permissions=["maison.historique"])
    assert lea.get("/api/actions").status_code == 200
    assert lea.post("/api/habitudes/accepter", json={"id": "habitude-x"}).status_code == 403   # automatismes.habitudes
    assert admin.get("/api/actions").status_code == 200


def test_rappels_regles_routines_sans_permission(maison_de_test):
    _, sec, machine, lea, _, _ = maison_de_test
    sec.definir_droits("lea", retraits=["rappels.utiliser", "automatismes.regles", "automatismes.routines"])
    assert chat(lea, "rappelle-moi dans 2 minutes d'arroser les plantes")["reponse"] == main.REFUS["rappels"]
    assert chat(lea, "crée une routine mode cinéma qui éteint le salon")["reponse"] == main.REFUS["routines"]
    quand = (datetime.now().astimezone() + timedelta(hours=1)).isoformat(timespec="seconds")
    assert machine.post("/interne/rappels", headers=H,
                        json={"quand": quand, "texte": "x", "conversation": "lea-1"}).status_code == 403
    assert machine.post("/interne/routines", headers=H, json={"nom": "cinéma", "conversation": "lea-1", "etapes": [
        {"entity_ids": ["light.salon"], "action": "eteindre"}]}).status_code == 403
    assert machine.post("/interne/regles", headers=H, json={"condition": {"type": "presence", "evenement": "part"},
                                                             "conversation": "lea-1"}).status_code == 403


def test_routine_limitee_a_ce_que_l_auteur_pilote(maison_de_test):
    _, sec, machine, lea, admin, _ = maison_de_test
    sec.definir_droits("lea", retraits=["maison.piloter.lumieres"])
    chat(lea, "bonjour")
    corps = {"nom": "cinéma", "conversation": "lea-1", "etapes": [{"entity_ids": ["light.salon"], "action": "eteindre"}]}
    assert machine.post("/interne/routines", headers=H, json=corps).status_code == 403
    corps["etapes"] = [{"entity_ids": ["cover.salon"], "action": "fermer"}]
    assert machine.post("/interne/routines", headers=H, json=corps).status_code == 200
    # la routine est à Léa : l'admin la voit (avec son auteur), un autre compte non
    assert [r["nom"] for r in lea.get("/api/routines").json()["routines"]] == ["cinéma"]
    assert admin.get("/api/routines").json()["routines"][0]["auteur"] == "Léa"
    sec.creer_compte("Tom")
    sec.definir_mot_de_passe("tom", "mot de passe de tom")
    tom = TestClient(lea.app, client=(LAN, 50001), follow_redirects=False)
    tom.post("/api/connexion", json={"identifiant": "tom", "mot_de_passe": "mot de passe de tom"})
    assert tom.get("/api/routines").json()["routines"] == []
    assert tom.delete("/api/routines/cinéma").status_code == 404
    assert machine.get("/interne/routines", headers=H, params={"conversation": "tom-1"}).status_code == 200


def test_memoire_personnelle_et_foyer(maison_de_test):
    _, sec, _, lea, _, _ = maison_de_test
    sec.definir_droits("lea", retraits=["memoire.foyer"])
    assert lea.post("/api/memoire", json={"texte": "Le code du portail change", "portee": "foyer"}).status_code == 403
    assert lea.post("/api/memoire", json={"texte": "J'aime le thé vert", "portee": "prive"}).status_code == 200
    rep = chat(lea, "retiens que la poubelle sort le mardi")
    assert "pour toi seulement" in rep["reponse"]                       # pas le droit au foyer : privé
    sec.definir_droits("lea", retraits=["memoire.utiliser", "memoire.foyer"])
    assert lea.get("/api/memoire").status_code == 403


def test_annonces_a_qui_de_droit(maison_de_test):
    app, sec, machine, lea, admin, _ = maison_de_test
    an = app.state.annonces
    an.ajouter("Rappel pour Léa : arroser", "urgent", "rappel", "rappel:1", compte="lea")
    an.ajouter("Rappel pour Principal : sortir", "urgent", "rappel", "rappel:2", compte="principal")
    an.ajouter("Le conteneur plex est arrêté.", "important", "docker", "docker:plex")
    an.ajouter("Une mise à jour de l'image de plex est disponible.", "info", "mises à jour", "maj:plex",
               [{"id": "mettre_a_jour", "libelle": "Mettre à jour"}], {"type": "maj_docker", "conteneur": "plex"})
    an.ajouter("La porte du garage est ouverte.", "important", "surveillance", "regle:porte")
    vues = lambda c: [a["texte"] for a in c.get("/api/annonces").json()["annonces"]]
    assert vues(lea) == ["Rappel pour Léa : arroser", "La porte du garage est ouverte."]
    assert "Rappel pour Léa : arroser" not in vues(admin) and len(vues(admin)) == 4
    maj = next(a for a in admin.get("/api/annonces").json()["annonces"] if a["source"] == "mises à jour")
    assert maj["actions"]
    assert lea.post(f"/api/annonces/{maj['id']}/action", json={"action": "ignorer"}).status_code == 403
    autre = an.liste[1]                                   # le rappel de Principal
    assert lea.post(f"/api/annonces/{autre['id']}/lue").status_code == 404
    sec.definir_droits("lea", permissions=["serveur.alertes"])
    assert "Le conteneur plex est arrêté." in vues(lea)
    assert all("actions" not in a for a in lea.get("/api/annonces").json()["annonces"])   # pas de bouton


def test_rappel_sonne_seulement_chez_son_auteur(maison_de_test, monkeypatch):
    app, sec, machine, lea, admin, _ = maison_de_test
    envois = []

    async def faux_envoi(self, texte):
        envois.append(texte)
    monkeypatch.setattr(main.Telegram, "envoyer", faux_envoi)
    chat(lea, "bonjour")
    quand = (datetime.now().astimezone() + timedelta(seconds=1)).isoformat(timespec="seconds")
    with machine:
        r = machine.post("/interne/rappels", headers=H, json={"quand": quand, "texte": "arroser", "conversation": "lea-1"})
        assert r.status_code == 200
        from tests.test_unraid_surveillance import attendre
        attendre(lambda: any("arroser" in a["texte"] for a in app.state.annonces.liste), 12)
    a = next(a for a in app.state.annonces.liste if "arroser" in a["texte"])
    assert a["compte"] == "lea" and a["texte"] == "Rappel pour Léa : arroser"
    assert not any("arroser" in e for e in envois)       # Léa n'a pas « telegram.recevoir »
    assert not any("arroser" in x["texte"] for x in admin.get("/api/annonces").json()["annonces"])


def test_administration_des_droits_par_api(maison_de_test):
    _, sec, _, lea, admin, _ = maison_de_test
    d = admin.get("/api/droits").json()
    assert {g["id"] for g in d["groupes"]} == {"adultes", "enfants"} and d["catalogue"]
    r = admin.post("/api/groupes", json={"nom": "Ados", "parent": "enfants", "permissions": ["assistant.web"]})
    assert r.status_code == 200 and r.json()["id"] == "ados"
    assert admin.post("/api/groupes", json={"nom": "Ados"}).status_code == 409
    assert admin.post("/api/groupes", json={"nom": "Y", "permissions": ["n.importe.quoi"]}).status_code == 422
    assert admin.put("/api/groupes/adultes", json={"nom": "Adultes", "parent": "ados"}).status_code == 422   # boucle
    c = admin.post("/api/comptes/lea/droits", json={"groupes": ["ados"], "permissions": ["serveur.etat"],
                                                     "retraits": ["agenda.lire"]}).json()
    assert c["groupes"] == ["ados"] and "serveur.etat" in c["effectives"] and "agenda.lire" not in c["effectives"]
    assert lea.get("/api/docker").status_code == 200 and lea.get("/api/agenda").status_code == 403
    assert admin.post("/api/comptes/principal/droits", json={"admin": False}).status_code == 409
    assert admin.delete("/api/groupes/ados").status_code == 422                       # Léa en est membre
    det = admin.get("/api/comptes/lea/detail").json()
    assert set(det) == {"rappels", "regles", "routines", "journal"}
    inv = admin.post("/api/comptes", json={"nom": "Tom", "groupes": ["enfants"]}).json()
    assert inv["id"] == "tom" and sec.groupes_de("tom") == ["enfants"]
    assert admin.post("/api/comptes", json={"nom": "Zoé", "groupes": ["inconnu"]}).status_code == 422
    assert "zoe" not in sec.comptes


def test_question_generale_sans_permission_web(maison_de_test):
    _, sec, _, lea, _, _ = maison_de_test
    sec.definir_droits("lea", retraits=["assistant.web"])
    assert chat(lea, "Explique-moi la photosynthèse")["reponse"] == main.REFUS["web"]
    assert lea.post("/api/rapport", json={"type": "meteo"}).status_code == 403
    assert chat(lea, "quelle heure est-il ?")["reponse"] != main.REFUS["web"]       # l'heure reste permise
