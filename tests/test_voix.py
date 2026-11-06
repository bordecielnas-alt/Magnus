import io
import math
import struct
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.config import VoixConfig, charger
from assistant.main import creer_app
from assistant.voix import Transcription, decoder_audio, texte_pour_la_voix
from tests.test_routeur import routeur


def wav_sinus(secondes=0.5, frequence=440, taux=22050) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taux)
        n = int(secondes * taux)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * frequence * i / taux)))
                               for i in range(n)))
    return b.getvalue()


def test_decodage_reechantillonne_en_16k_mono():
    x = decoder_audio(wav_sinus(0.5))
    assert abs(len(x) - 8000) < 200 and x.dtype.name == "float32" and 0.1 < abs(x).max() <= 1


def test_texte_pour_la_voix():
    t = texte_pour_la_voix("**C'est fait** : Globe — allumé à 50 %\n- light.globe\n- 20 °C")
    assert "*" not in t and "light.globe" not in t and "pour cent" in t and "degrés" in t


def test_chemins_par_defaut():
    c = VoixConfig()
    assert c.chemin_whisper().endswith("whisper-small") and c.chemin_piper().endswith(".onnx")


class FausseVoix:
    def __init__(self):
        self.indices = None

    def etat(self):
        return {"active": True}

    async def transcrire(self, audio, indices=""):
        self.indices = indices
        return Transcription("allume le globe", "fr", 1.2, 345)

    async def parler(self, texte):
        return wav_sinus(0.1), 12


def _client(tmp_path, monkeypatch):
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    monkeypatch.setattr(VoixConfig, "whisper_present", lambda self: True)
    monkeypatch.setattr(VoixConfig, "piper_presente", lambda self: True)
    v = FausseVoix()
    return TestClient(creer_app(cfg, r.registre, voix=v)), v, cfg


def test_api_transcrire(tmp_path, monkeypatch):
    c, v, cfg = _client(tmp_path, monkeypatch)
    rep = c.post("/api/voix/transcrire", content=b"x" * 3000, headers={"Content-Type": "audio/webm"}).json()
    assert rep == {"texte": "allume le globe", "duree_audio_s": 1.2, "duree_ms": 345}
    assert cfg.identite.nom in v.indices      # le nom de l'assistant aide la reconnaissance
    assert c.post("/api/voix/transcrire", content=b"").status_code == 400


def test_api_parler(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    r = c.post("/api/voix/parler", json={"texte": "Bonjour"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert r.headers["x-duree-ms"] == "12"
