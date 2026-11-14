import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant import totp
from assistant.config import SecuriteConfig, charger
from assistant.main import creer_app
from assistant.securite import NOM_COOKIE, AccesRefuse, Securite, hacher, verifier
from tests.test_routeur import routeur

LAN, AILLEURS, NPM = "192.168.1.30", "203.0.113.9", "192.168.1.2"
MDP = "correct horse battery"


def app(tmp_path, **cfg_sec):
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    sec = Securite(SecuriteConfig(**cfg_sec), tmp_path)
    return creer_app(cfg, r.registre, securite=sec), sec


def client(application, ip):
    return TestClient(application, client=(ip, 50000), follow_redirects=False)


def code_suivant(secret: str) -> str:
    """Code du pas suivant : celui du pas courant a pu déjà servir (anti-rejeu)."""
    return totp.code_actuel(secret, time.time() + totp.PERIODE)


def configurer_totp(c, jeton):
    """Première connexion : QR code puis code, renvoie (secret, codes de secours)."""
    prep = c.post("/api/connexion/totp/preparer", json={"jeton": jeton}).json()
    assert prep["qr_svg"].lstrip().startswith("<svg") and prep["uri"].startswith("otpauth://totp/")
    r = c.post("/api/connexion/totp/activer", json={"jeton": jeton, "code": totp.code_actuel(prep["secret"])})
    assert r.status_code == 200
    return prep["secret"], r.json()["codes_de_secours"]


# --- TOTP -------------------------------------------------------------------------------------
def test_totp_rfc6238():
    # vecteur de test de la RFC 6238 (SHA-1, clé « 12345678901234567890 »), sur 6 chiffres
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_actuel(secret, 59) == "287082"
    assert totp.verifier(secret, "287082", instant=59) == 1
    assert totp.verifier(secret, "287082", dernier_pas=1, instant=59) is None    # un code ne sert qu'une fois
    assert totp.verifier(secret, "000000", instant=59) is None


def test_hachage():
    h = hacher("un bon mot de passe")
    assert verifier("un bon mot de passe", h) and not verifier("autre", h) and "un bon" not in h


# --- réseau --------------------------------------------------------------------------------------
def test_machine_elle_meme_sans_mot_de_passe(tmp_path):
    a, _ = app(tmp_path)
    assert client(a, "127.0.0.1").get("/api/info").status_code == 200


def test_reseau_externe_refuse(tmp_path):
    a, _ = app(tmp_path)
    assert client(a, AILLEURS).get("/api/info").status_code == 403
    assert client(a, AILLEURS).get("/health").status_code == 403


# --- connexion en deux étapes -----------------------------------------------------------------
def test_premiere_connexion_configure_le_totp(tmp_path):
    a, sec = app(tmp_path)
    c = client(a, LAN)
    assert c.get("/api/info").status_code == 401
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/connexion")
    assert c.get("/connexion").status_code == 200
    # premier compte : refusé depuis le LAN, accepté depuis la machine
    assert c.post("/api/securite/mot-de-passe", json={"nouveau": MDP}).status_code == 403
    assert client(a, "127.0.0.1").post("/api/securite/mot-de-passe", json={"nouveau": MDP}).status_code == 200
    assert c.post("/api/connexion", json={"mot_de_passe": "faux"}).status_code == 401
    r = c.post("/api/connexion", json={"mot_de_passe": MDP})
    assert r.status_code == 200 and r.json()["etape"] == "configurer_totp"
    assert c.get("/api/info").status_code == 401                 # pas encore de session
    secret, codes = configurer_totp(c, r.json()["jeton"])
    assert len(codes) == 10
    assert c.get("/api/info").status_code == 200                 # cookie de session gardé par le client
    etat = c.get("/api/securite").json()
    assert etat["connecte"] and etat["compte"] == "principal" and etat["admin"] and etat["totp"]

    # nouvel appareil : mot de passe puis code
    c2 = client(a, LAN)
    r = c2.post("/api/connexion", json={"identifiant": "Principal", "mot_de_passe": MDP})
    assert r.json()["etape"] == "totp"
    jeton = r.json()["jeton"]
    assert c2.post("/api/connexion/code", json={"jeton": jeton, "code": "000000"}).status_code == 401
    assert c2.post("/api/connexion/code", json={"jeton": jeton, "code": code_suivant(secret)}).status_code == 200
    assert c2.get("/api/info").status_code == 200

    # code de secours : une seule fois
    c3, c4 = client(a, LAN), client(a, LAN)
    j3 = c3.post("/api/connexion", json={"mot_de_passe": MDP}).json()["jeton"]
    r = c3.post("/api/connexion/code", json={"jeton": j3, "code": codes[0].upper()})
    assert r.status_code == 200 and r.json()["secours_restants"] == 9
    j4 = c4.post("/api/connexion", json={"mot_de_passe": MDP}).json()["jeton"]
    assert c4.post("/api/connexion/code", json={"jeton": j4, "code": codes[0]}).status_code == 401


def test_jeton_d_etape_ne_donne_pas_de_session(tmp_path):
    a, sec = app(tmp_path)
    sec.creer_premier(MDP)
    c = client(a, LAN)
    jeton = c.post("/api/connexion", json={"mot_de_passe": MDP}).json()["jeton"]
    c.cookies.set(NOM_COOKIE, jeton)
    assert c.get("/api/info").status_code == 401
    # et un jeton « configurer » ne sert pas à l'étape « code »
    with pytest.raises(AccesRefuse):
        sec.lire_etape(jeton, "totp")


def test_sans_totp_obligatoire(tmp_path):
    a, sec = app(tmp_path, totp_obligatoire=False)
    sec.creer_premier(MDP)
    c = client(a, LAN)
    assert c.post("/api/connexion", json={"mot_de_passe": MDP}).json()["etape"] == "fini"
    assert c.get("/api/info").status_code == 200


def test_anti_force_brute(tmp_path):
    a, sec = app(tmp_path, essais_max=3)
    sec.creer_premier(MDP)
    c = client(a, LAN)
    for _ in range(3):
        assert c.post("/api/connexion", json={"mot_de_passe": "faux"}).status_code == 401
    r = c.post("/api/connexion", json={"mot_de_passe": MDP})
    assert r.status_code == 429 and "Trop" in r.json()["detail"]


def test_session_falsifiee_ou_ancien_mot_de_passe(tmp_path):
    a, sec = app(tmp_path)
    compte = sec.creer_premier(MDP)
    jeton = sec.creer_session(compte)
    falsifie = jeton[:-1] + ("1" if jeton[-1] != "1" else "2")
    assert sec.session(jeton) == compte and not sec.session_valide(falsifie)
    sec.definir_mot_de_passe(compte, "un autre mot de passe")
    assert not sec.session_valide(jeton)                   # changer le mot de passe ferme les sessions


def test_migration_ancien_mot_de_passe(tmp_path):
    import json
    (tmp_path / "securite.json").write_text(json.dumps({"cle": "ab" * 32, "mot_de_passe": hacher(MDP)}))
    sec = Securite(SecuriteConfig(), tmp_path)
    assert sec.trouver("") == "principal" and sec.est_admin("principal")
    assert sec.connexion(LAN, "", MDP) == ("configurer_totp", "principal")


# --- comptes et invitations ---------------------------------------------------------------------
def test_invitation_deuxieme_compte(tmp_path):
    a, sec = app(tmp_path)
    sec.creer_premier(MDP)
    machine = client(a, "127.0.0.1")
    r = machine.post("/api/comptes", json={"nom": "Élodie"})
    assert r.status_code == 200 and r.json()["id"] == "elodie"
    lien = r.json()["lien"]
    jeton = lien.split("invitation=")[1]
    assert machine.post("/api/comptes", json={"nom": "elodie"}).status_code == 409

    c = client(a, LAN)
    assert c.post("/api/invitation", json={"invitation": jeton[:-2] + "xx", "mot_de_passe": MDP}).status_code == 401
    r = c.post("/api/invitation", json={"invitation": jeton, "mot_de_passe": "mot de passe d'elodie"})
    assert r.status_code == 200 and r.json()["etape"] == "configurer_totp"
    configurer_totp(c, r.json()["jeton"])
    etat = c.get("/api/securite").json()
    assert etat["compte"] == "elodie" and not etat["admin"]
    # l'invitation ne sert qu'une fois ; un non-admin ne gère pas les comptes
    assert client(a, LAN).post("/api/invitation", json={"invitation": jeton, "mot_de_passe": MDP}).status_code == 401
    assert c.get("/api/comptes").status_code == 403
    assert c.get("/api/audit").status_code == 403
    # avec plusieurs comptes, l'identifiant devient nécessaire
    assert client(a, LAN).post("/api/connexion", json={"mot_de_passe": MDP}).status_code == 401
    assert client(a, LAN).post("/api/connexion", json={"identifiant": "principal", "mot_de_passe": MDP}).status_code == 200

    # réinitialisation par l'admin : ferme ses sessions
    assert machine.post("/api/comptes/elodie/invitation").status_code == 200
    assert c.get("/api/info").status_code == 401
    assert machine.delete("/api/comptes/elodie").status_code == 200
    assert machine.delete("/api/comptes/principal").status_code == 409     # dernier administrateur
    types = [e["type"] for e in machine.get("/api/audit").json()["evenements"]]
    assert {"compte_créé", "invitation_acceptée", "totp_activé", "connexion", "compte_supprimé"} <= set(types)


def test_audit_sans_secret(tmp_path):
    a, sec = app(tmp_path)
    sec.creer_premier(MDP)
    client(a, LAN).post("/api/connexion", json={"identifiant": "principal", "mot_de_passe": "mauvais secret"})
    texte = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "refusé" in texte and "mauvais secret" not in texte


# --- proxies --------------------------------------------------------------------------------------
def test_derriere_npm(tmp_path):
    a, sec = app(tmp_path, proxies_de_confiance=[NPM + "/32"], totp_obligatoire=False)
    sec.creer_premier(MDP)
    c = client(a, NPM)
    # le client réel est sur Internet : refusé même si NPM est sur le LAN
    assert c.get("/api/info", headers={"X-Forwarded-For": AILLEURS}).status_code == 403
    # client réel sur le LAN : connexion, cookie « Secure » si NPM dit https
    r = c.post("/api/connexion", json={"mot_de_passe": MDP},
               headers={"X-Forwarded-For": LAN, "X-Forwarded-Proto": "https"})
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()


def test_x_forwarded_for_ignore_sans_proxy_de_confiance(tmp_path):
    a, _ = app(tmp_path)
    # un client externe ne peut pas se faire passer pour la machine en envoyant l'en-tête
    assert client(a, AILLEURS).get("/api/info", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_api_interne_seulement_depuis_la_machine(tmp_path):
    a, sec = app(tmp_path)
    sec.creer_premier(MDP)
    assert client(a, LAN).get("/interne/maison/appareils").status_code == 403
    assert client(a, "127.0.0.1").get("/interne/maison/appareils").status_code == 403   # jeton manquant


def test_reseau_invalide_refuse_dans_la_config():
    with pytest.raises(ValueError):
        SecuriteConfig(reseaux_autorises=["pas un réseau"])


def test_derriere_cloudflare_et_npm(tmp_path):
    a, sec = app(tmp_path, proxies_de_confiance=[NPM + "/32"])
    sec.creer_premier(MDP)
    c = client(a, NPM)
    # visiteur sur Internet via Cloudflare : son IP réelle est dans CF-Connecting-IP -> refusé
    h = {"X-Forwarded-For": AILLEURS + ", 172.70.1.1", "CF-Connecting-IP": AILLEURS}
    assert c.get("/api/info", headers=h).status_code == 403
    # sans proxy de confiance, l'en-tête Cloudflare n'est pas cru
    a2, _ = app(tmp_path / "b")
    assert client(a2, AILLEURS).get("/api/info", headers={"CF-Connecting-IP": "127.0.0.1"}).status_code == 403


def test_icones_visibles_avant_connexion(tmp_path):
    a, sec = app(tmp_path)
    sec.creer_premier(MDP)
    c = client(a, LAN)
    assert c.get("/favicon.ico").status_code == 200
    assert c.get("/static/icones/icone-192.png").status_code == 200
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["icons"][0]["src"] == "/static/icones/icone-192.png"
    assert c.get("/static/index.html").status_code == 303            # le reste de /static demande la connexion


# --- profils ------------------------------------------------------------------------------------------
def test_profil_et_consigne(tmp_path):
    from assistant.main import note_profil
    a, sec = app(tmp_path, totp_obligatoire=False)
    sec.creer_premier(MDP)
    c = client(a, LAN)
    c.post("/api/connexion", json={"mot_de_passe": MDP})
    assert c.get("/api/compte/profil").json()["profil"]["ton"] == "normal"
    p = c.post("/api/compte/profil", json={"ton": "concis", "piece": "Salon", "consigne": "tutoie-moi"}).json()["profil"]
    assert p["ton"] == "concis" and p["piece"] == "Salon"
    note = note_profil("Principal", p)
    assert "Principal" in note and "brève" in note and "Salon" in note and "tutoie-moi" in note
    # un compte non administrateur ne change pas son type
    machine = client(a, "127.0.0.1")
    lien = machine.post("/api/comptes", json={"nom": "Invité"}).json()["lien"]
    c2 = client(a, LAN)
    c2.post("/api/invitation", json={"invitation": lien.split("invitation=")[1], "mot_de_passe": "mot de passe invité"})
    c2.post("/api/connexion", json={"identifiant": "invite", "mot_de_passe": "mot de passe invité"})
    assert c2.post("/api/compte/profil", json={"type": "enfant"}).status_code == 403
    # « parler comme à un enfant » : une case du groupe
    sec.enregistrer_groupe("", "Enfants", ["assistant.web"], enfant=True)
    assert machine.post("/api/comptes/invite/droits", json={"groupe": "enfants"}).json()["type"] == "enfant"
    assert "enfant" in note_profil("Invité", sec.profil("invite"))


# --- ce qui est révélé selon le contexte (t305) -------------------------------------------------------
def test_contexte_et_masques(tmp_path):
    a, sec = app(tmp_path, totp_obligatoire=False, proxies_de_confiance=[NPM + "/32"],
                 reseaux_autorises=["0.0.0.0/0"])
    sec.creer_premier(MDP)
    lan = client(a, LAN)
    lan.post("/api/connexion", json={"mot_de_passe": MDP})
    ctx = lan.get("/api/appareil").json()
    assert ctx == {"distant": False, "partage": False, "enfant": False, "masques": []}
    assert lan.get("/api/actions").status_code == 200
    # tablette commune : historique masqué
    assert lan.post("/api/appareil", json={"partage": True}).json()["partage"] is True
    assert set(lan.get("/api/appareil").json()["masques"]) == {"etat_sensible", "historique", "personnel"}
    assert lan.get("/api/actions").status_code == 403
    lan.post("/api/appareil", json={"partage": False})
    assert lan.get("/api/actions").status_code == 200
    # depuis Internet (via Cloudflare + NPM) : état des appareils sensibles masqué
    web = client(a, NPM)
    h = {"CF-Connecting-IP": "93.184.216.34"}       # adresse publique (AILLEURS est une plage de test)
    web.post("/api/connexion", json={"mot_de_passe": MDP}, headers=h)
    assert web.get("/api/appareil", headers=h).json()["masques"] == ["etat_sensible"]


def test_matrice_invalide_refusee():
    from assistant.config import RevelationConfig
    with pytest.raises(ValueError):
        RevelationConfig(masquer={"distant": ["mot_de_passe"]})


def test_scripts_jamais_perimes(tmp_path):
    a, _ = app(tmp_path)
    c = client(a, "127.0.0.1")
    assert c.get("/static/reglages.js").headers["cache-control"] == "no-cache"
    assert c.get("/").headers["cache-control"] == "no-cache"


def test_scripts_avec_version_dans_l_adresse(tmp_path):
    from assistant import __version__
    a, _ = app(tmp_path)
    page = client(a, "127.0.0.1").get("/").text
    assert f'src="/static/reglages.js?v={__version__}"' in page and f'src="/static/orbe.js?v={__version__}"' in page


def test_se_renommer(tmp_path):
    a, sec = app(tmp_path, totp_obligatoire=False)
    sec.creer_premier(MDP)
    c = client(a, LAN)
    c.post("/api/connexion", json={"mot_de_passe": MDP})
    r = c.post("/api/compte/nom", json={"nom": "Benjamin"}).json()
    assert r["nom"] == "Benjamin" and r["identifiant"] == "principal"
    assert c.get("/api/securite").json()["compte_nom"] == "Benjamin"
    assert c.post("/api/connexion", json={"identifiant": "Benjamin", "mot_de_passe": MDP}).status_code == 200
