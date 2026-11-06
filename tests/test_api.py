from pathlib import Path

from fastapi.testclient import TestClient

from assistant.config import charger
from assistant.main import creer_app

AUCUN_FICHIER = Path("inexistant.yaml")


def client(env=None, fichier=AUCUN_FICHIER):
    return TestClient(creer_app(charger(env=env or {}, fichier=fichier)))


def test_health():
    r = client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_info_defauts():
    info = client().get("/api/info").json()
    assert info["nom"]  # vient de defaults.yaml
    assert info["wake_word"] == info["nom"].lower()
    assert info["version"]


def test_nom_par_variable_env():
    info = client(env={"ASSISTANT_NOM": "Akatosh"}).get("/api/info").json()
    assert info["nom"] == "Akatosh"
    assert info["wake_word"] == "akatosh"


def test_wake_word_explicite():
    env = {"ASSISTANT_NOM": "Akatosh", "ASSISTANT_WAKE_WORD": "Hey Jarvis"}
    info = client(env=env).get("/api/info").json()
    assert info["wake_word"] == "hey jarvis"


def test_nom_par_fichier(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("identite:\n  nom: Nocturnal\n", encoding="utf-8")
    assert client(fichier=f).get("/api/info").json()["nom"] == "Nocturnal"


def test_env_prime_sur_fichier(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("identite:\n  nom: Nocturnal\n", encoding="utf-8")
    info = client(env={"ASSISTANT_NOM": "Akatosh"}, fichier=f).get("/api/info").json()
    assert info["nom"] == "Akatosh"


def test_page_accueil():
    r = client().get("/")
    assert r.status_code == 200
    assert "/api/info" in r.text
