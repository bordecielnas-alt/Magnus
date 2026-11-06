"""Voix : transcription (faster-whisper) et synthèse (Piper), 100 % locales.

Les modèles se chargent au premier usage (quelques secondes) puis restent en mémoire.
Les calculs tournent dans un thread pour ne pas bloquer l'orchestrateur.
"""

from __future__ import annotations

import asyncio
import glob
import io
import os
import sys
import re
import threading
import time
import wave
from dataclasses import dataclass

from .config import VoixConfig


@dataclass
class Transcription:
    texte: str
    langue: str
    duree_audio_s: float
    duree_ms: int


def preparer_cuda() -> None:
    """Windows : rend visibles les DLL CUDA installées par pip (nvidia-cublas-cu12, nvidia-cudnn-cu12).
    Sous Linux (conteneur), les bibliothèques sont trouvées sans aide."""
    if os.name != "nt":
        return
    for d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
        try:
            os.add_dll_directory(d)
        except OSError:
            continue
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def appareil_effectif(demande: str) -> str:
    if demande != "auto":
        return demande
    try:
        preparer_cuda()
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


class Voix:
    def __init__(self, cfg: VoixConfig) -> None:
        self.cfg = cfg
        self._whisper = None
        self._piper = None
        self._kokoro = None
        self.appareil = ""
        self._verrou_w = threading.Lock()
        self._verrou_p = threading.Lock()
        self.erreur = ""

    # --- état ------------------------------------------------------------------------
    def etat(self) -> dict:
        moteur = self.cfg.moteur_effectif()
        return {
            "active": self.cfg.active,
            "whisper": {"modele": os.path.basename(self.cfg.chemin_whisper()), "present": self.cfg.whisper_present(),
                        "charge": self._whisper is not None, "appareil": self.appareil or self.cfg.appareil},
            # « piper » garde son nom pour la page Outils : c'est la synthèse, quel que soit le moteur
            "piper": {"voix": moteur + (f" ({self.cfg.voix_kokoro})" if moteur == "kokoro" else ""),
                      "presente": self.cfg.synthese_presente(),
                      "chargee": (self._kokoro if moteur == "kokoro" else self._piper) is not None},
            "moteur": moteur,
            "erreur": self.erreur,
        }

    # --- transcription -------------------------------------------------------------------
    def _modele_whisper(self):
        with self._verrou_w:
            if self._whisper is None:
                self.appareil = appareil_effectif(self.cfg.appareil)
                from faster_whisper import WhisperModel
                self._whisper = WhisperModel(self.cfg.chemin_whisper(), device=self.appareil,
                                             compute_type=self.cfg.calcul)
            return self._whisper

    def _transcrire(self, audio: bytes, indices: str) -> Transcription:
        debut = time.perf_counter()
        modele = self._modele_whisper()
        segments, info = modele.transcribe(
            decoder_audio(audio), language=self.cfg.langue, beam_size=1, vad_filter=True,
            initial_prompt=indices or None, condition_on_previous_text=False,
        )
        texte = " ".join(s.text.strip() for s in segments).strip()
        return Transcription(texte, info.language, round(info.duration, 2),
                             round((time.perf_counter() - debut) * 1000))

    async def transcrire(self, audio: bytes, indices: str = "") -> Transcription:
        """`indices` : mots probables (nom de l'assistant, appareils, pièces) pour mieux les reconnaître."""
        return await asyncio.to_thread(self._transcrire, audio, indices)

    # --- synthèse ------------------------------------------------------------------------
    def _voix_piper(self):
        with self._verrou_p:
            if self._piper is None:
                from piper import PiperVoice
                self._piper = PiperVoice.load(self.cfg.chemin_piper())
            return self._piper

    def _voix_kokoro(self):
        with self._verrou_p:
            if self._kokoro is None:
                from kokoro_onnx import Kokoro
                d = self.cfg.chemin_kokoro()
                self._kokoro = Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))
            return self._kokoro

    def _voix(self):
        return self._voix_kokoro() if self.cfg.moteur_effectif() == "kokoro" else self._voix_piper()

    def _parler(self, texte: str) -> tuple[bytes, int]:
        if self.cfg.moteur_effectif() == "kokoro":
            return self._parler_kokoro(texte)
        return self._parler_piper(texte)

    def _parler_kokoro(self, texte: str) -> tuple[bytes, int]:
        import numpy as np
        debut = time.perf_counter()
        echantillons, taux = self._voix_kokoro().create(
            texte_pour_la_voix(texte), voice=self.cfg.voix_kokoro, speed=1 / self.cfg.lenteur,
            lang="fr-fr" if self.cfg.langue == "fr" else self.cfg.langue)
        tampon = io.BytesIO()
        with wave.open(tampon, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(taux)
            w.writeframes((np.clip(echantillons, -1, 1) * 32767).astype("<i2").tobytes())
        return tampon.getvalue(), round((time.perf_counter() - debut) * 1000)

    def _parler_piper(self, texte: str) -> tuple[bytes, int]:
        from piper import SynthesisConfig
        debut = time.perf_counter()
        voix = self._voix_piper()
        tampon = io.BytesIO()
        with wave.open(tampon, "wb") as w:
            voix.synthesize_wav(texte_pour_la_voix(texte), w,
                                syn_config=SynthesisConfig(length_scale=self.cfg.lenteur))
        return tampon.getvalue(), round((time.perf_counter() - debut) * 1000)

    async def parler(self, texte: str) -> tuple[bytes, int]:
        """WAV (16 bits mono) et durée de synthèse en ms."""
        return await asyncio.to_thread(self._parler, texte)

    async def prechauffer(self) -> None:
        """Charge les modèles en arrière-plan pour que la première phrase ne soit pas lente."""
        try:
            await asyncio.to_thread(self._modele_whisper)
            await asyncio.to_thread(self._voix)
            self.erreur = ""
        except Exception as e:  # noqa: BLE001 - affiché dans Outils, ne doit pas arrêter l'assistant
            self.erreur = f"{e.__class__.__name__} : {e}"


def decoder_audio(audio: bytes, frequence: int = 16000):
    """N'importe quel format audio (webm/opus du navigateur, wav…) -> numpy float32 mono 16 kHz.

    Fait ici plutôt que par faster-whisper, dont le décodeur passe à PyAV une option
    retirée des versions récentes."""
    import av
    import numpy as np

    morceaux = []
    with av.open(io.BytesIO(audio), mode="r") as conteneur:
        flux = next(s for s in conteneur.streams if s.type == "audio")
        reech = av.AudioResampler(format="s16", layout="mono", rate=frequence)
        for trame in conteneur.decode(flux):
            for t in reech.resample(trame):
                morceaux.append(t.to_ndarray().reshape(-1))
        for t in reech.resample(None):
            morceaux.append(t.to_ndarray().reshape(-1))
    if not morceaux:
        return np.zeros(0, dtype=np.float32)
    return (np.concatenate(morceaux).astype(np.float32) / 32768.0)


def texte_pour_la_voix(texte: str) -> str:
    """Retire ce qui se lit mal : markdown, listes, symboles, identifiants techniques."""
    t = re.sub(r"[*_`#>]+", "", texte)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"\b[a-z_]+\.[a-z0-9_]+\b", "", t)          # light.plafonnier
    t = t.replace("—", ",").replace("%", " pour cent").replace("°C", " degrés")
    t = re.sub(r"\s*\n+\s*", ". ", t)
    return re.sub(r"\s{2,}", " ", t).strip()
