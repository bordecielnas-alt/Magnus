"""Voix : transcription (faster-whisper) et synthèse (Piper), 100 % locales.

Les modèles se chargent au premier usage (quelques secondes) puis restent en mémoire.
Les calculs tournent dans un thread pour ne pas bloquer l'orchestrateur.
"""

from __future__ import annotations

import asyncio
import io
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


class Voix:
    def __init__(self, cfg: VoixConfig) -> None:
        self.cfg = cfg
        self._whisper = None
        self._piper = None
        self._verrou_w = threading.Lock()
        self._verrou_p = threading.Lock()
        self.erreur = ""

    # --- état ------------------------------------------------------------------------
    def etat(self) -> dict:
        return {
            "active": self.cfg.active,
            "whisper": {"modele": self.cfg.chemin_whisper(), "present": self.cfg.whisper_present(),
                        "charge": self._whisper is not None, "appareil": self.cfg.appareil},
            "piper": {"voix": self.cfg.chemin_piper(), "presente": self.cfg.piper_presente(),
                      "chargee": self._piper is not None},
            "erreur": self.erreur,
        }

    # --- transcription -------------------------------------------------------------------
    def _modele_whisper(self):
        with self._verrou_w:
            if self._whisper is None:
                from faster_whisper import WhisperModel
                self._whisper = WhisperModel(self.cfg.chemin_whisper(), device=self.cfg.appareil,
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

    def _parler(self, texte: str) -> tuple[bytes, int]:
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
            await asyncio.to_thread(self._voix_piper)
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
