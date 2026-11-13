from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant.config import VoixConfig, charger
from assistant.main import creer_app
from assistant.reveil import Reveil, detecter, est_hallucination
from assistant.voix import Transcription
from tests.test_routeur import routeur
from tests.test_voix import FausseVoix


@pytest.mark.parametrize("texte,commande", [
    ("Akatosh, allume le globe.", "allume le globe."),
    ("Ok Akatosh allume le globe", "allume le globe"),
    ("Dis Akatoch, quelle heure est-il ?", "quelle heure est-il ?"),   # faute de transcription tolérée
    ("akatosh", ""),
])
def test_wake_word_detecte(texte, commande):
    d = detecter(texte, "akatosh")
    assert d.reveil and d.commande == commande


@pytest.mark.parametrize("texte", ["Allume le globe", "Le chat d'Akatosh dort", "Akata"])
def test_wake_word_absent(texte):
    assert not detecter(texte, "akatosh").reveil


def test_wake_word_en_deux_mots():
    assert detecter("Hey Jarvis, éteins tout", "hey jarvis").commande == "éteins tout"


def test_hallucinations():
    assert est_hallucination("Sous-titres réalisés par la communauté d'Amara.org")
    assert est_hallucination("  ") and not est_hallucination("allume le globe")


def test_fenetre_de_conversation(monkeypatch):
    import assistant.reveil as r
    temps = [100.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: temps[0])
    rv = Reveil(fenetre_s=10)
    assert rv.analyser("allume le globe", "akatosh", "c")["action"] == "ignorer"
    assert rv.analyser("Akatosh ?", "akatosh", "c")["action"] == "reveil"
    temps[0] += 5
    assert rv.analyser("allume le globe", "akatosh", "c") == {"action": "commande", "commande": "allume le globe", "fenetre_s": 10.0}
    temps[0] += 11
    assert rv.analyser("éteins le globe", "akatosh", "c")["action"] == "ignorer"
    assert rv.analyser("éteins le globe", "akatosh", "autre")["action"] == "ignorer"


def test_toujours_a_l_ecoute():
    rv = Reveil(fenetre_s=0, toujours=True)
    assert rv.analyser("allume le globe", "akatosh", "c")["action"] == "commande"
    assert rv.analyser("Sous-titres réalisés par…", "akatosh", "c")["action"] == "ignorer"


def test_api_ecoute_utilise_le_wake_word_configure(tmp_path, monkeypatch):
    r, _ = routeur()
    cfg = charger(env={"ASSISTANT_NOM": "Akatosh"}, fichier=Path("inexistant.yaml"))
    monkeypatch.setattr(VoixConfig, "whisper_present", lambda self: True)
    v = FausseVoix()

    async def entendu(audio, indices=""):
        return Transcription("Akatosh, allume le globe", "fr", 1.5, 200)
    v.transcrire = entendu
    c = TestClient(creer_app(cfg, r.registre, voix=v))
    rep = c.post("/api/voix/ecoute?conversation=x", content=b"a" * 3000).json()
    assert rep["action"] == "commande" and rep["commande"] == "allume le globe" and rep["wake_word"] == "akatosh"


def test_api_reglages(tmp_path, monkeypatch):
    import assistant.main as main
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    ecrit = {}
    monkeypatch.setattr(main, "enregistrer_reglages", lambda section, v: ecrit.update({section: v}))
    c = TestClient(creer_app(cfg, r.registre, voix=FausseVoix(), persister=True))
    rep = c.post("/api/voix/reglages", json={"fenetre_s": 30, "toujours_a_l_ecoute": True}).json()
    assert rep["fenetre_s"] == 30 and rep["toujours_a_l_ecoute"] is True
    assert ecrit == {"voix": {"fenetre_s": 30, "toujours_a_l_ecoute": True}}
    assert c.get("/api/voix").json()["fenetre_s"] == 30
    assert c.post("/api/voix/reglages", json={}).status_code == 400
    assert c.post("/api/voix/reglages", json={"fenetre_s": -1}).status_code == 422


def test_enregistrer_reglages_garde_le_reste(tmp_path):
    from assistant.config import enregistrer_reglages
    import yaml
    f = tmp_path / "config.yaml"
    f.write_text("home_assistant:\n  url: https://ha\nvoix:\n  lenteur: 1.1\n", encoding="utf-8")
    enregistrer_reglages("voix", {"fenetre_s": 20}, f)
    d = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert d == {"home_assistant": {"url": "https://ha"}, "voix": {"lenteur": 1.1, "fenetre_s": 20}}


def test_nom_repete_sur_du_bruit():
    assert detecter("Magnus Magnus Magnus Magnus Magnus Magnus", "Magnus").commande == ""
    assert detecter("Magnus, Magnus, allume le globe", "Magnus").commande == "allume le globe"


def test_mot_repete_sur_un_bruit():
    assert est_hallucination("Cough, Cough, Cough, Cough")
    assert not est_hallucination("allume allume le globe")


@pytest.mark.parametrize("texte,commande", [
    ("Hal, allume le globe.", "allume le globe."),
    ("Hall, quelle heure est-il ?", "quelle heure est-il ?"),
    ("Dis HAL 9000, éteins tout", "éteins tout"),
    ("Halle ?", "?"),
])
def test_theme_hal_variantes(texte, commande):
    d = detecter(texte, ["hal", "hall", "halle", "hale"])
    assert d.reveil and d.commande.strip(" ?") == commande.strip(" ?")


def test_theme_hal_ignore_les_phrases_ordinaires():
    assert not detecter("Allume le hall d'entrée", ["hal", "hall", "halle", "hale"]).reveil


def test_theme_change_le_mot_de_reveil(monkeypatch):
    import assistant.main as main
    r, _ = routeur()
    cfg = charger(env={"ASSISTANT_NOM": "Akatosh"}, fichier=Path("inexistant.yaml"))
    assert cfg.identite.theme == "orbe"
    ecrit = {}
    monkeypatch.setattr(main, "enregistrer_reglages", lambda section, v: ecrit.update({section: v}))
    monkeypatch.setattr(VoixConfig, "whisper_present", lambda self: True)
    v = FausseVoix()
    entendu_texte = ["Hall, allume le globe"]

    async def entendu(audio, indices=""):
        return Transcription(entendu_texte[0], "fr", 1.5, 200)
    v.transcrire = entendu
    c = TestClient(creer_app(cfg, r.registre, voix=v, persister=True))
    assert c.get("/api/info").json()["wake_word"] == "akatosh"
    assert c.post("/api/voix/ecoute?conversation=x", content=b"a" * 3000).json()["action"] == "ignorer"
    rep = c.post("/api/theme", json={"theme": "hal"}).json()
    assert rep == {"theme": "hal", "wake_word": "hal"} and ecrit == {"identite": {"theme": "hal"}}
    info = c.get("/api/info").json()
    assert info["theme"] == "hal" and info["wake_word"] == "hal"
    assert info["nom"] == "Hal" and info["nom_configure"] == "Akatosh"
    assert cfg.consigne_effective.startswith("Tu es Hal") or "Hal" in cfg.consigne_effective
    rep = c.post("/api/voix/ecoute?conversation=y", content=b"a" * 3000).json()
    assert rep["action"] == "commande" and rep["commande"] == "allume le globe"
    c.post("/api/theme", json={"theme": "orbe"})
    info = c.get("/api/info").json()
    assert info["wake_word"] == "akatosh" and info["nom"] == "Akatosh"
    assert c.post("/api/theme", json={"theme": "autre"}).status_code == 422


def test_bruit_mal_transcrit_ignore():
    from assistant.reveil import Reveil, semble_charabia
    assert semble_charabia("Escortrack, SIL, SALVAF") and semble_charabia("Etant, L1, Binder")
    for vrai in ("Oui", "état adminer", "allume le globe", "Démar Adminer", "combien de stockage", "Globe, lampe"):
        assert not semble_charabia(vrai), vrai
    r = Reveil(10)
    assert r.analyser("Magnus, Escortrack, SIL, SALVAF", "Magnus", "c")["action"] == "reveil"   # on écoute la suite
    assert r.analyser("Etant, L1, Binder", "Magnus", "c")["action"] == "ignorer"
    assert r.analyser("état de Plex", "Magnus", "c")["action"] == "commande"


def test_theme_garde_apres_redemarrage(tmp_path, monkeypatch):
    """Le serveur lancé par run() reçoit sa config : le thème choisi doit quand même être écrit
    dans config.yaml, sinon il revient à l'orbe au redémarrage du conteneur."""
    import uvicorn
    from fastapi.testclient import TestClient
    from assistant import main as M
    from assistant.config import charger

    fichier = tmp_path / "config.yaml"
    monkeypatch.setenv("ASSISTANT_CONFIG", str(fichier))
    monkeypatch.setattr(M, "charger_secrets", lambda: None)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: apps.append(app))
    M.run()
    TestClient(apps[0]).post("/api/theme", json={"theme": "hal"})
    assert charger(env={}, fichier=fichier).identite.theme == "hal"   # relu comme au redémarrage


def test_theme_garde_apres_redemarrage_conteneur(tmp_path, monkeypatch):
    """Le conteneur lance « python -m assistant » (assistant/__main__.py), pas main.run() :
    ce chemin-là aussi doit écrire le thème dans config.yaml."""
    import logging.config
    import runpy
    import uvicorn
    from fastapi.testclient import TestClient
    from assistant import config as C

    fichier = tmp_path / "config.yaml"
    monkeypatch.setenv("ASSISTANT_CONFIG", str(fichier))
    monkeypatch.setenv("ASSISTANT_ETAT", str(tmp_path))
    monkeypatch.setattr(C, "charger_secrets", lambda: None)
    monkeypatch.setattr(logging.config, "dictConfig", lambda c: None)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: apps.append(app))
    runpy.run_module("assistant.__main__", run_name="__main__")
    TestClient(apps[0]).post("/api/theme", json={"theme": "hal"})
    assert C.charger(env={}, fichier=fichier).identite.theme == "hal"   # relu comme au redémarrage
