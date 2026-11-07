from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from assistant import reglages as R
from assistant.config import DEFAUTS, _lire_yaml, charger
from assistant.main import creer_app
from tests.test_routeur import routeur
from tests.test_voix import FausseVoix

DEF = _lire_yaml(DEFAUTS)


def test_appliquer_ecrit_et_valide(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("home_assistant:\n  url: https://ha\n", encoding="utf-8")
    cfg, _ = R.appliquer({"identite": {"nom": "Akatosh"}, "roles": {"rapide": "claude"}}, DEF, f)
    assert cfg.identite.nom == "Akatosh" and cfg.roles["rapide"] == "claude"
    d = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert d == {"home_assistant": {"url": "https://ha"}, "identite": {"nom": "Akatosh"}, "roles": {"rapide": "claude"}}


@pytest.mark.parametrize("changement", [
    {"serveur": {"port": 1}},                                  # section non modifiable
    {"identite": {"langue": "en"}},                            # champ non modifiable
    {"roles": {"rapide": "inexistant"}},                       # rôle vers fournisseur inconnu
    {"fournisseurs": {"ollama-local": {"type": "x"}}},         # champ de fournisseur interdit
    {"fournisseurs": {"pirate": {"url": "http://x"}}},         # fournisseur inconnu
    {"identite": {"nom": ""}},                                 # nom vide
    {"fournisseurs": {"ollama-local": {"url": ""}}},           # openai sans url
])
def test_appliquer_refuse(tmp_path, changement):
    f = tmp_path / "config.yaml"
    with pytest.raises(R.ReglageInvalide):
        R.appliquer(changement, DEF, f)
    assert not f.exists()                                      # rien écrit si invalide


def test_secret(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# commentaire\nAUTRE=1\nHA_JETON=ancien\n", encoding="utf-8")
    monkeypatch.delenv("HA_JETON", raising=False)
    R.ecrire_secret("HA_JETON", "nouveau", f)
    assert f.read_text(encoding="utf-8") == "# commentaire\nAUTRE=1\nHA_JETON=nouveau\n"
    with pytest.raises(R.ReglageInvalide):
        R.ecrire_secret("PATH", "x", f)
    with pytest.raises(R.ReglageInvalide):
        R.ecrire_secret("HA_JETON", "a\nINJECTE=1", f)


def test_vue_sans_secret(monkeypatch):
    monkeypatch.setenv("HA_JETON", "tres-secret")
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    v = R.vue(cfg)
    assert v["home_assistant"]["jeton_defini"] is True and "tres-secret" not in str(v)


def test_api_reglages(tmp_path, monkeypatch):
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    f = tmp_path / "config.yaml"
    monkeypatch.setattr(R, "chemin_config_utilisateur", lambda: f)
    c = TestClient(creer_app(cfg, r.registre, voix=FausseVoix()))
    d = c.get("/api/reglages").json()
    assert d["reglages"]["identite"]["nom"] and "etat" in d and d["voix_disponibles"]
    rep = c.post("/api/reglages", json={"voix": {"fenetre_s": 25}}).json()
    assert rep["redemarrage_necessaire"] is False and cfg.voix.fenetre_s == 25   # appliqué tout de suite
    rep = c.post("/api/reglages", json={"identite": {"nom": "Akatosh"}}).json()
    assert rep["redemarrage_necessaire"] is True
    assert c.post("/api/reglages", json={"serveur": {"port": 1}}).status_code == 422
    assert c.post("/api/redemarrer").status_code in (200, 409)
