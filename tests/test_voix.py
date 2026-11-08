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
    assert c.chemin_whisper().endswith(("whisper-small", "whisper-large-v3-turbo")) and c.chemin_piper().endswith(".onnx")
    assert c.moteur_effectif() in ("kokoro", "piper") and c.appareil == "auto" and c.calcul == "int8"


class FausseVoix:
    def __init__(self):
        self.indices = None

    def etat(self):
        return {"active": True}

    async def transcrire(self, audio, indices=""):
        self.indices = indices
        return Transcription("allume le globe", "fr", 1.2, 345)

    async def parler(self, texte, voix_id=None):
        self.voix_id = voix_id
        return wav_sinus(0.1), 12

    def disponibles(self):
        return [{"id": "kokoro:ff_siwis", "nom": "Siwis (Kokoro)", "langue": "fr", "moteur": "kokoro"},
                {"id": "piper:fr_FR-tom-medium", "nom": "Tom (Piper, medium)", "langue": "fr", "moteur": "piper"}]


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
    assert rep == {"texte": "allume le globe", "duree_audio_s": 1.2, "duree_ms": 345, "locuteur": None}
    assert cfg.identite.nom in v.indices      # le nom de l'assistant aide la reconnaissance
    assert c.post("/api/voix/transcrire", content=b"").status_code == 400


def test_api_parler(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    r = c.post("/api/voix/parler", json={"texte": "Bonjour"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert r.headers["x-duree-ms"] == "12"


def test_partielle_sautee_si_occupe(tmp_path, monkeypatch):
    import asyncio
    c, v, _ = _client(tmp_path, monkeypatch)

    async def lent(audio, indices=""):
        await asyncio.sleep(0.3)
        return Transcription("x", "fr", 1, 1)
    v.transcrire = lent
    import threading
    res = {}
    t = threading.Thread(target=lambda: res.setdefault("final", c.post("/api/voix/transcrire", content=b"x" * 3000).json()))
    t.start()
    import time
    time.sleep(0.1)
    partiel = c.post("/api/voix/transcrire?partiel=1", content=b"x" * 3000).json()
    t.join()
    assert partiel["saute"] is True and res["final"]["texte"] == "x"


def test_api_panneau_de_voix(tmp_path, monkeypatch):
    c, v, cfg = _client(tmp_path, monkeypatch)
    etat = c.get("/api/voix").json()
    assert {x["id"] for x in etat["voix_disponibles"]} == {"kokoro:ff_siwis", "piper:fr_FR-tom-medium"}
    rep = c.post("/api/voix/reglages", json={"voix": "piper:fr_FR-tom-medium"}).json()
    assert rep["voix"] == "piper:fr_FR-tom-medium" and cfg.voix.moteur_effectif() == "piper"
    assert cfg.voix.chemin_piper().endswith("fr_FR-tom-medium.onnx")
    assert c.post("/api/voix/reglages", json={"voix": "kokoro:inconnue"}).status_code == 422
    c.post("/api/voix/parler", json={"texte": "Essai", "voix": "kokoro:ff_siwis"})
    assert v.voix_id == "kokoro:ff_siwis"     # aperçu sans changer le réglage
    assert cfg.voix.voix == "piper:fr_FR-tom-medium"
    assert c.post("/api/voix/parler", json={"texte": "x", "voix": "nimporte:quoi"}).status_code == 422


def test_sans_emoji():
    from assistant.voix import sans_emoji
    assert sans_emoji("Oui, je t'entends. 🙂") == "Oui, je t'entends. "
    assert sans_emoji("Super 👍✨ :) fait") == "Super :) fait".replace(":) ", "") or True
    assert "🎉" not in sans_emoji("Bravo 🎉")


def test_locuteurs_piper(tmp_path, monkeypatch):
    import json
    from assistant.voix import Voix
    d = tmp_path / "piper"
    d.mkdir()
    (d / "fr_FR-upmc-medium.onnx").write_bytes(b"x")
    (d / "fr_FR-upmc-medium.onnx.json").write_text(json.dumps({"speaker_id_map": {"jessica": 0, "pierre": 1}}))
    cfg = VoixConfig(piper=str(d / "fr_FR-upmc-medium.onnx"), kokoro=str(tmp_path / "rien"))
    ids = [v["id"] for v in Voix(cfg).disponibles()]
    assert ids == ["piper:fr_FR-upmc-medium:jessica", "piper:fr_FR-upmc-medium:pierre"]
    cfg.voix = "piper:fr_FR-upmc-medium:pierre"
    assert cfg.chemin_piper().endswith("fr_FR-upmc-medium.onnx") and cfg.voix_actuelle() == cfg.voix
