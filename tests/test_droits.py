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
    "admin": {"nom": "Admin", "permissions": []},
    "foyer": {"nom": "Foyer", "permissions": ["maison.voir", "agenda.lire", "agenda.ecrire"]},
    "exterieur": {"nom": "Extérieur", "permissions": ["maison.voir"]},
}


def test_un_groupe_puis_les_exceptions_de_la_personne():
    assert D.effectif(GROUPES, "foyer") == {"maison.voir", "agenda.lire", "agenda.ecrire"}
    assert D.effectif(GROUPES, "exterieur", ["assistant.web"]) == {"maison.voir", "assistant.web"}
    # un retrait propre a le dernier mot, même sur ce que le groupe donne
    assert D.effectif(GROUPES, "foyer", ["agenda.ecrire"], ["agenda.ecrire"]) == {"maison.voir", "agenda.lire"}
    assert D.effectif(GROUPES, "admin", retraits=["serveur.etat"]) == set(D.PERMISSIONS)     # Admin : tout
    assert D.effectif(GROUPES, "inconnu") == set()


def test_groupes_par_defaut():
    g = D.GROUPES_DEFAUT
    assert list(g) == ["admin", "foyer", "exterieur"] and D.GROUPE_PAR_DEFAUT == "foyer"
    assert set(g["exterieur"]["permissions"]) < set(g["foyer"]["permissions"])
    assert not {p for p in g["foyer"]["permissions"] if p.startswith(("serveur.", "telegram."))}


def sec_avec_lea(tmp_path) -> Securite:
    sec = Securite(SecuriteConfig(totp_obligatoire=False), tmp_path)
    sec.creer_premier(MDP)
    sec.creer_compte("Léa")
    sec.definir_mot_de_passe("lea", "mot de passe de léa")
    return sec


def test_un_groupe_par_personne(tmp_path):
    sec = sec_avec_lea(tmp_path)
    assert sec.groupe_de("principal") == "admin" and sec.groupe_de("lea") == "foyer"
    foyer = sec.permissions("lea")
    assert {"agenda.ecrire", "automatismes.regles", "maison.piloter.chauffage", "assistant.web"} <= foyer
    assert not foyer & {"serveur.etat", "telegram.envoyer", "maison.historique", "automatismes.habitudes"}
    sec.definir_droits("lea", groupe="exterieur")
    assert sec.permissions("lea") < foyer and not sec.peut("lea", "agenda.lire")
    assert sec.permissions("principal") == set(D.PERMISSIONS) and sec.peut("machine", "serveur.redemarrer")
    assert sec.profil("lea")["type"] == "adulte"
    sec.enregistrer_groupe("", "Enfants", ["maison.voir", "maison.piloter.lumieres"], enfant=True)
    sec.definir_droits("lea", groupe="enfants")
    assert sec.profil("lea")["type"] == "enfant" and sec.profil("lea")["interdits"] == [
        "chauffage", "medias", "prises", "ventilateurs", "volets"]
    with pytest.raises(D.DroitInvalide):
        sec.peut("lea", "faute.de.frappe")


def test_migration_depuis_les_groupes_et_sous_groupes(tmp_path):
    import json
    (tmp_path / "securite.json").write_text(json.dumps({
        "groupes": {"adultes": {"nom": "Adultes", "parent": "", "permissions": ["maison.voir", "agenda.lire"], "retraits": []},
                    "enfants": {"nom": "Enfants", "parent": "adultes", "permissions": [], "retraits": ["agenda.lire"]},
                    "ados": {"nom": "Ados", "parent": "enfants", "permissions": ["assistant.web"], "retraits": []}},
        "comptes": {"principal": {"nom": "Principal", "mdp": "x", "admin": True},
                    "lea": {"nom": "Léa", "mdp": "x", "groupes": ["adultes"], "retraits": ["maison.voir"]},
                    "tom": {"nom": "Tom", "mdp": "x", "groupes": ["ados"]},
                    "zoe": {"nom": "Zoé", "mdp": "x", "profil": {"type": "enfant"}}}}), encoding="utf-8")
    sec = Securite(SecuriteConfig(), tmp_path)
    assert sec.groupe_de("principal") == "admin" and sec.groupe_de("lea") == "foyer"
    assert "maison.voir" not in sec.permissions("lea")                       # ses exceptions sont gardées
    assert sec.groupe_de("tom") == "ados" and sec.groupes["ados"]["permissions"] == ["assistant.web", "maison.voir"]
    assert sec.groupe_de("zoe") == "enfants" and sec.profil("zoe")["type"] == "enfant"
    assert {"admin", "foyer", "exterieur", "ados", "enfants"} == set(sec.groupes)
    assert all("parent" not in g and "retraits" not in g for g in sec.groupes.values())
    sec2 = Securite(SecuriteConfig(), tmp_path)                              # migré une seule fois
    assert sec2.groupe_de("tom") == "ados"


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
    gid = sec.enregistrer_groupe("", "Amis", ["assistant.web"])
    assert gid == "amis" and sec.groupes["amis"]["permissions"] == ["assistant.web"]
    with pytest.raises(D.DroitInvalide):
        sec.enregistrer_groupe("admin", "Admin", [])                     # Admin ne se modifie pas
    with pytest.raises(D.DroitInvalide):
        sec.enregistrer_groupe("", "X", ["pas.une.permission"])
    for fixe in ("admin", "foyer"):
        with pytest.raises(D.DroitInvalide):
            sec.supprimer_groupe(fixe)
    sec.definir_droits("lea", groupe="amis")
    with pytest.raises(D.DroitInvalide):
        sec.supprimer_groupe("amis")                                      # a une membre
    with pytest.raises(D.DroitInvalide):
        sec.definir_droits("lea", groupe="inconnu")
    with pytest.raises(D.DroitInvalide):
        sec.definir_droits("lea", permissions=["pas.une.permission"])
    assert sec.groupe_de("lea") == "amis"                                 # rien changé après une erreur
    with pytest.raises(AccesRefuse):
        sec.definir_droits("principal", groupe="foyer")                   # dernier administrateur
    with pytest.raises(AccesRefuse):
        sec.supprimer("principal")
    sec.definir_droits("lea", groupe="admin")
    sec.definir_droits("principal", groupe="foyer")
    assert sec.est_admin("lea") and not sec.est_admin("principal")
    sec.definir_droits("principal", groupe="admin")
    sec.definir_droits("lea", groupe="exterieur")
    sec.supprimer_groupe("amis")
    assert "amis" not in sec.groupes


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
    sec.definir_droits("lea", retraits=["agenda.lire", "agenda.ecrire", "agenda.personnel"])
    assert lea.get("/api/agenda").status_code == 403
    assert chat(lea, "qu'est-ce que j'ai à l'agenda demain ?")["reponse"] == main.REFUS["agenda"]
    assert machine.get("/interne/agenda", headers=H, params={"conversation": "lea-1"}).status_code == 403
    sec.definir_droits("lea", retraits=["agenda.ecrire", "agenda.personnel"])   # lire oui, écrire non
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
    assert [g["id"] for g in d["groupes"]] == ["admin", "foyer", "exterieur"] and d["catalogue"]
    assert next(g for g in d["groupes"] if g["id"] == "admin")["fige"]
    r = admin.post("/api/groupes", json={"nom": "Ados", "permissions": ["assistant.web"], "enfant": True})
    assert r.status_code == 200 and r.json()["id"] == "ados"
    assert admin.post("/api/groupes", json={"nom": "Ados"}).status_code == 409
    assert admin.post("/api/groupes", json={"nom": "Admin"}).status_code == 409
    assert admin.post("/api/groupes", json={"nom": "Y", "permissions": ["n.importe.quoi"]}).status_code == 422
    assert admin.put("/api/groupes/admin", json={"nom": "Admin", "permissions": []}).status_code == 422
    c = admin.post("/api/comptes/lea/droits", json={"groupe": "ados", "permissions": ["serveur.etat"],
                                                     "retraits": []}).json()
    assert c["groupe"] == "ados" and c["type"] == "enfant" and "serveur.etat" in c["effectives"]
    assert lea.get("/api/docker").status_code == 200 and lea.get("/api/agenda").status_code == 403
    assert admin.post("/api/comptes/principal/droits", json={"groupe": "foyer"}).status_code == 409
    assert admin.delete("/api/groupes/ados").status_code == 422                       # Léa en est membre
    det = admin.get("/api/comptes/lea/detail").json()
    assert set(det) == {"rappels", "regles", "routines", "journal"}
    inv = admin.post("/api/comptes", json={"nom": "Tom", "groupe": "exterieur"}).json()
    assert inv["id"] == "tom" and sec.groupe_de("tom") == "exterieur"
    assert admin.post("/api/comptes", json={"nom": "Zoé", "groupe": "inconnu"}).status_code == 422
    assert "zoe" not in sec.comptes
    admin.post("/api/comptes/lea/droits", json={"groupe": "admin"})
    assert lea.get("/api/droits").status_code == 200                                  # Admin = gère les comptes


def test_question_generale_sans_permission_web(maison_de_test):
    _, sec, _, lea, _, _ = maison_de_test
    sec.definir_droits("lea", retraits=["assistant.web"])
    assert chat(lea, "Explique-moi la photosynthèse")["reponse"] == main.REFUS["web"]
    assert lea.post("/api/rapport", json={"type": "meteo"}).status_code == 403
    assert chat(lea, "quelle heure est-il ?")["reponse"] != main.REFUS["web"]       # l'heure reste permise


def test_l_administrateur_voit_et_modifie_tout(maison_de_test, monkeypatch):
    app, sec, machine, lea, admin, _ = maison_de_test
    # souvenir privé de Léa : invisible pour un autre compte, visible et modifiable par l'administrateur
    sv = lea.post("/api/memoire", json={"texte": "J'aime le thé vert", "portee": "prive"}).json()
    vus = admin.get("/api/memoire").json()["souvenirs"]
    mien = next(x for x in vus if x["id"] == sv["id"])
    assert mien["auteur"] == "Léa" and mien["a_moi"] is False
    assert admin.patch(f"/api/memoire/{sv['id']}", json={"texte": "Léa aime le thé vert"}).status_code == 200
    assert admin.delete(f"/api/memoire/{sv['id']}").status_code == 200
    sec.creer_compte("Tom")
    sec.definir_mot_de_passe("tom", "mot de passe de tom")
    tom = TestClient(app, client=(LAN, 50001), follow_redirects=False)
    tom.post("/api/connexion", json={"identifiant": "tom", "mot_de_passe": "mot de passe de tom"})
    sv2 = lea.post("/api/memoire", json={"texte": "Mon code de casier est secret", "portee": "prive"}).json()
    assert all(x["id"] != sv2["id"] for x in tom.get("/api/memoire").json()["souvenirs"])
    assert tom.delete(f"/api/memoire/{sv2['id']}").status_code == 404
    # règles et routines de Léa : l'administrateur les voit (avec l'auteur) et peut les changer
    chat(lea, "bonjour")
    corps = {"nom": "cinéma", "conversation": "lea-1", "etapes": [{"entity_ids": ["cover.salon"], "action": "fermer"}]}
    assert machine.post("/interne/routines", headers=H, json=corps).status_code == 200
    assert admin.delete("/api/routines/cinéma").status_code == 200
    # automatisations créées depuis une habitude : listées pour l'administrateur, supprimables
    import json as js
    (sec.fichier.parent / "automatisations.json").write_text(js.dumps({"assistant_x": {
        "id": "assistant_x", "alias": "Salon : allumer à 07:00", "compte": "lea", "cree": "2026-11-14T10:00:00"}}),
        encoding="utf-8")
    assert admin.get("/api/automatisations").json()["automatisations"][0]["auteur"] == "Léa"
    assert tom.get("/api/automatisations").json()["automatisations"] == []
    assert tom.delete("/api/automatisations/assistant_x").status_code == 404

    async def supprimee(self, ident):
        return True
    monkeypatch.setattr(main.HomeAssistant, "supprimer_automatisation", supprimee)
    assert admin.delete("/api/automatisations/assistant_x").status_code == 200
    assert admin.get("/api/automatisations").json()["automatisations"] == []


class FauxAgenda:
    """Agenda CalDAV simulé : les événements de chaque adresse, gardés en mémoire."""
    contenus: dict = {}

    def __init__(self, url, utilisateur="", mot_de_passe=""):
        self.url, self.mdp = url, mot_de_passe
        self.configure = bool(url)

    async def evenements(self, debut, fin):
        if self.mdp == "faux":
            raise PermissionError("agenda : identifiant ou mot de passe refusé")
        return [e for e in self.contenus.get(self.url, []) if debut <= e["debut"] <= fin]

    async def ajouter(self, titre, debut, fin=None, lieu="", rappel_min=15, journee=False):
        self.contenus.setdefault(self.url, []).append({"titre": titre, "debut": debut, "href": f"{self.url}{titre}.ics"})
        return titre

    async def supprimer(self, href):
        self.contenus[self.url] = [e for e in self.contenus.get(self.url, []) if e["href"] != href]


def test_agenda_personnel(maison_de_test, monkeypatch):
    app, sec, machine, lea, admin, _ = maison_de_test
    FauxAgenda.contenus = {}
    monkeypatch.setattr(main, "Agenda", FauxAgenda)
    sec.definir_droits("lea", retraits=["agenda.lire", "agenda.ecrire"])        # pas l'agenda familial
    assert lea.get("/api/compte/outils").json()["agenda"]["permis"]
    assert lea.post("/api/compte/agenda", json={"url": "http://cal/lea/", "utilisateur": "lea",
                                                 "mot_de_passe": "faux"}).status_code == 422     # essayé avant d'enregistrer
    r = lea.post("/api/compte/agenda", json={"url": "http://cal/lea/", "utilisateur": "lea", "mot_de_passe": "bon"})
    assert r.status_code == 200 and r.json()["branche"]
    assert "bon" not in str(lea.get("/api/compte/outils").json())                 # le mot de passe ne revient jamais
    chat(lea, "bonjour")
    debut = (datetime.now().astimezone() + timedelta(days=1)).replace(microsecond=0).isoformat()
    ok = machine.post("/interne/agenda", headers=H, json={"titre": "Dentiste", "debut": debut, "conversation": "lea-1"})
    assert ok.status_code == 200 and ok.json()["agenda"] == "perso"
    famille = machine.post("/interne/agenda", headers=H, json={"titre": "X", "debut": debut, "agenda": "famille",
                                                              "conversation": "lea-1"})
    assert famille.status_code == 403                                             # pas le droit d'écrire au familial
    lus = machine.get("/interne/agenda", headers=H, params={"conversation": "lea-1"}).json()
    assert [e["titre"] for e in lus] == ["Dentiste"]
    assert [e["titre"] for e in lea.get("/api/agenda").json()["evenements"]] == ["Dentiste"]
    # personne d'autre ne le voit : l'administrateur n'a pas d'agenda personnel branché
    assert admin.get("/api/compte/outils").json()["agenda"]["url"] == ""
    ref = lus[0]["ref"]
    assert machine.post("/interne/agenda/supprimer", headers=H, json={"ref": ref, "conversation": "lea-1"}).status_code == 200
    # sans le droit « agenda personnel » : ni écran ni outil
    sec.definir_droits("lea", retraits=["agenda.lire", "agenda.ecrire", "agenda.personnel"])
    assert lea.post("/api/compte/agenda", json={"url": "http://cal/lea/"}).status_code == 403
    assert lea.get("/api/agenda").status_code == 403


def test_telegram_personnel(maison_de_test, monkeypatch):
    app, sec, machine, lea, admin, _ = maison_de_test
    envois, recus = [], {"texte": ""}

    async def envoyer(self, texte, chat_id=""):
        envois.append((str(chat_id or self.chat_id), texte))

    async def trouver(self, code=""):
        return {"id": "77", "nom": "Léa"} if code and code in recus["texte"] else None

    async def nom(self):
        return "magnus_bot"
    monkeypatch.setattr(main.Telegram, "envoyer", envoyer)
    monkeypatch.setattr(main.Telegram, "trouver_chat", trouver)
    monkeypatch.setattr(main.Telegram, "nom_du_bot", nom)
    assert lea.post("/api/compte/telegram/code").status_code == 403             # pas « telegram.recevoir »
    sec.definir_droits("lea", permissions=["telegram.recevoir"])
    assert lea.post("/api/compte/telegram/relier").status_code == 409           # pas encore de code
    code = lea.post("/api/compte/telegram/code").json()
    assert code["bot"] == "magnus_bot" and len(code["code"]) == 6
    assert lea.post("/api/compte/telegram/relier").status_code == 404           # le bot n'a rien reçu
    recus["texte"] = "/start " + code["code"]
    assert lea.post("/api/compte/telegram/relier").json()["nom"] == "Léa"
    assert envois[-1][0] == "77"
    # son rappel part sur SON Telegram, pas sur celui de la maison (42)
    chat(lea, "bonjour")
    quand = (datetime.now().astimezone() + timedelta(seconds=1)).isoformat(timespec="seconds")
    with machine:
        machine.post("/interne/rappels", headers=H, json={"quand": quand, "texte": "arroser", "conversation": "lea-1"})
        from tests.test_unraid_surveillance import attendre
        attendre(lambda: any("arroser" in t for _, t in envois), 12)
    assert [c for c, t in envois if "arroser" in t] == ["77"]
    # envoyer un message : vers son Telegram à elle, seulement avec « telegram.envoyer »
    assert machine.post("/interne/telegram", headers=H, json={"texte": "coucou", "conversation": "lea-1"}).status_code == 403
    sec.definir_droits("lea", permissions=["telegram.recevoir", "telegram.envoyer"])
    chat(lea, "bonjour")
    assert machine.post("/interne/telegram", headers=H, json={"texte": "coucou", "conversation": "lea-1"}).status_code == 200
    assert envois[-1] == ("77", "coucou")
    # délié : plus rien pour elle (jamais le Telegram de la maison à la place)
    assert lea.delete("/api/compte/telegram").status_code == 200
    assert machine.post("/interne/telegram", headers=H, json={"texte": "x", "conversation": "lea-1"}).status_code == 409


def test_theme_voix_et_ecoute_propres_a_chaque_personne(maison_de_test):
    app, sec, machine, lea, admin, _ = maison_de_test
    maison_info = admin.get("/api/info").json()
    assert lea.post("/api/theme", json={"theme": "hal"}).json() == {"theme": "hal", "wake_word": "hal"}
    i = lea.get("/api/info").json()
    assert (i["theme"], i["nom"], i["wake_word"]) == ("hal", "Hal", "hal")
    assert admin.get("/api/info").json() == maison_info                         # rien changé pour les autres
    assert lea.post("/api/voix/reglages", json={"fenetre_s": 5, "toujours_a_l_ecoute": True}).status_code == 200
    v = lea.get("/api/voix").json()
    assert v["fenetre_s"] == 5 and v["toujours_a_l_ecoute"] is True and v["theme"] == "hal"
    va = admin.get("/api/voix").json()
    assert va["fenetre_s"] != 5 and va["toujours_a_l_ecoute"] is False
    assert lea.post("/api/voix/reglages", json={"voix": "inconnue"}).status_code == 422
    assert sec.comptes["lea"]["assistant"] == {"theme": "hal", "fenetre_s": 5, "toujours_a_l_ecoute": True}
    # l'écoute de sa conversation suit ses réglages
    p = lea.post("/api/voix/prolonger", json={"conversation": "lea-v"}).json()
    assert p["toujours"] is True and p["fenetre_s"] is None          # toujours à l'écoute : pas de fenêtre


def test_reveil_par_conversation():
    from assistant.reveil import Reveil
    r = Reveil(30, False)
    r.regler("a", 0, True)
    assert r.eveille("a") and not r.eveille("b")
    r.regler("b", 5, False)
    r.prolonger("b")
    assert 0 < r.restant_s("b") <= 5 and r.restant_s("a") is None


def test_consigne_au_nom_du_theme():
    from assistant.main import DemandeChat, _messages
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    d = DemandeChat(message="bonjour")
    assert "Hal" in _messages(cfg, d, nom="Hal")[0].content
    assert cfg.identite.nom in _messages(cfg, d)[0].content
