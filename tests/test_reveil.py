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
