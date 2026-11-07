from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant.config import SecuriteConfig, charger
from assistant.main import creer_app
from assistant.securite import Securite, hacher, verifier
from tests.test_routeur import routeur

LAN, AILLEURS, NPM = "192.168.1.30", "203.0.113.9", "192.168.1.2"


def app(tmp_path, **cfg_sec):
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    sec = Securite(SecuriteConfig(**cfg_sec), tmp_path)
    return creer_app(cfg, r.registre, securite=sec), sec


def client(application, ip):
    return TestClient(application, client=(ip, 50000), follow_redirects=False)


def test_hachage():
    h = hacher("un bon mot de passe")
    assert verifier("un bon mot de passe", h) and not verifier("autre", h) and "un bon" not in h


def test_machine_elle_meme_sans_mot_de_passe(tmp_path):
    a, _ = app(tmp_path)
    assert client(a, "127.0.0.1").get("/api/info").status_code == 200


def test_reseau_externe_refuse(tmp_path):
    a, _ = app(tmp_path)
    assert client(a, AILLEURS).get("/api/info").status_code == 403
    assert client(a, AILLEURS).get("/health").status_code == 403


def test_lan_doit_se_connecter(tmp_path):
    a, sec = app(tmp_path)
    c = client(a, LAN)
    assert c.get("/api/info").status_code == 401
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/connexion")
    assert c.get("/connexion").status_code == 200
    # premier mot de passe : refusé depuis le LAN, accepté depuis la machine
    assert c.post("/api/securite/mot-de-passe", json={"nouveau": "correct horse battery"}).status_code == 403
    assert client(a, "127.0.0.1").post("/api/securite/mot-de-passe", json={"nouveau": "correct horse battery"}).status_code == 200
    assert c.post("/api/connexion", json={"mot_de_passe": "faux"}).status_code == 401
    assert c.post("/api/connexion", json={"mot_de_passe": "correct horse battery"}).status_code == 200
    assert c.get("/api/info").status_code == 200          # cookie de session gardé par le client
    assert c.get("/api/securite").json()["connecte"] is True


def test_anti_force_brute(tmp_path):
    a, sec = app(tmp_path, essais_max=3)
    sec.definir_mot_de_passe("correct horse battery")
    c = client(a, LAN)
    for _ in range(3):
        assert c.post("/api/connexion", json={"mot_de_passe": "faux"}).status_code == 401
    r = c.post("/api/connexion", json={"mot_de_passe": "correct horse battery"})
    assert r.status_code == 429 and "Trop" in r.json()["detail"]


def test_session_falsifiee_ou_ancien_mot_de_passe(tmp_path):
    a, sec = app(tmp_path)
    sec.definir_mot_de_passe("correct horse battery")
    jeton = sec.creer_session()
    assert sec.session_valide(jeton) and not sec.session_valide(jeton[:-1] + "0")
    sec.definir_mot_de_passe("un autre mot de passe")
    assert not sec.session_valide(jeton)                   # changer le mot de passe ferme les sessions


def test_derriere_npm(tmp_path):
    a, sec = app(tmp_path, proxies_de_confiance=[NPM + "/32"])
    sec.definir_mot_de_passe("correct horse battery")
    c = client(a, NPM)
    # le client réel est sur Internet : refusé même si NPM est sur le LAN
    assert c.get("/api/info", headers={"X-Forwarded-For": AILLEURS}).status_code == 403
    # client réel sur le LAN : connexion, cookie « Secure » si NPM dit https
    r = c.post("/api/connexion", json={"mot_de_passe": "correct horse battery"},
               headers={"X-Forwarded-For": LAN, "X-Forwarded-Proto": "https"})
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()


def test_x_forwarded_for_ignore_sans_proxy_de_confiance(tmp_path):
    a, _ = app(tmp_path)
    # un client externe ne peut pas se faire passer pour la machine en envoyant l'en-tête
    assert client(a, AILLEURS).get("/api/info", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_api_interne_seulement_depuis_la_machine(tmp_path):
    a, sec = app(tmp_path)
    sec.definir_mot_de_passe("correct horse battery")
    assert client(a, LAN).get("/interne/maison/appareils").status_code == 403
    assert client(a, "127.0.0.1").get("/interne/maison/appareils").status_code == 403   # jeton manquant


def test_reseau_invalide_refuse_dans_la_config():
    with pytest.raises(ValueError):
        SecuriteConfig(reseaux_autorises=["pas un réseau"])
