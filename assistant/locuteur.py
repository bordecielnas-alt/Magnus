"""Qui parle ? Empreinte vocale (WeSpeaker ResNet34, ONNX) comparée aux voix enregistrées par compte.

- Chaque personne enregistre quelques phrases (⚙ Mon compte) : la moyenne des empreintes est gardée
  dans <état>/voix_comptes.json (jamais l'audio).
- À chaque phrase dite, l'empreinte est comparée (cosinus) ; au-dessus du seuil et assez nettement
  devant le deuxième, la phrase est attribuée à ce compte.
- Sert à appliquer le bon profil (ton, pièce, préférences, rappels) et les limites de la personne
  (ex. un enfant reconnu sur la tablette d'un parent ne touche pas au chauffage). Jamais à donner des
  droits : une voix ne peut que retirer des droits à la session connectée, pas en ajouter.
Caractéristiques : fbank 80 bandes façon Kaldi (25 ms / 10 ms, Hamming, pré-accentuation 0,97), moyenne
retirée, comme à l'entraînement du modèle.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import numpy as np

FREQ = 16000
TRAME, PAS, NFFT, BANDES = 400, 160, 512, 80
DUREE_MIN_S = 1.0              # en dessous, l'empreinte n'est pas fiable
ECHANTILLONS_MAX = 8           # phrases gardées par compte pour la moyenne


def _banc_mel() -> np.ndarray:
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)  # noqa: E731 - échelle de Kaldi
    bas, haut = mel(20.0), mel(FREQ / 2)
    centres = np.linspace(bas, haut, BANDES + 2)
    freqs = mel(np.arange(NFFT // 2 + 1) * FREQ / NFFT)
    banc = np.zeros((BANDES, NFFT // 2 + 1), dtype=np.float32)
    for i in range(BANDES):
        g, c, d = centres[i], centres[i + 1], centres[i + 2]
        monte = (freqs - g) / (c - g)
        descend = (d - freqs) / (d - c)
        banc[i] = np.maximum(0.0, np.minimum(monte, descend))
    return banc


BANC = _banc_mel()
FENETRE = np.hamming(TRAME).astype(np.float32)


def fbank(audio: np.ndarray) -> np.ndarray:
    """audio float32 mono 16 kHz (-1..1) -> (trames, 80), moyenne retirée."""
    x = audio.astype(np.float32) * 32768.0
    if len(x) < TRAME:
        return np.zeros((0, BANDES), dtype=np.float32)
    n = 1 + (len(x) - TRAME) // PAS
    idx = np.arange(TRAME)[None, :] + PAS * np.arange(n)[:, None]
    trames = x[idx]
    trames = trames - trames.mean(axis=1, keepdims=True)                  # composante continue
    trames = np.concatenate([trames[:, :1] * (1 - 0.97), trames[:, 1:] - 0.97 * trames[:, :-1]], axis=1)
    trames *= FENETRE
    spectre = np.abs(np.fft.rfft(trames, n=NFFT)) ** 2
    f = np.log(np.maximum(spectre @ BANC.T, np.finfo(np.float32).eps))
    return (f - f.mean(axis=0, keepdims=True)).astype(np.float32)


def cosinus(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class Locuteurs:
    def __init__(self, modele: Path, dossier_etat: Path, seuil: float = 0.5, ecart: float = 0.08) -> None:
        self.modele = modele
        self.fichier = dossier_etat / "voix_comptes.json"
        self.seuil, self.ecart = seuil, ecart
        self._session = None
        self._verrou = threading.Lock()
        try:
            self.voix: dict[str, dict] = json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.voix = {}

    @property
    def disponible(self) -> bool:
        return self.modele.is_file()

    def _onnx(self):
        with self._verrou:
            if self._session is None:
                import onnxruntime as ort
                self._session = ort.InferenceSession(str(self.modele), providers=["CPUExecutionProvider"])
            return self._session

    def empreinte(self, audio: np.ndarray) -> np.ndarray | None:
        if len(audio) < DUREE_MIN_S * FREQ:
            return None
        f = fbank(audio)
        s = self._onnx()
        e = s.run(None, {s.get_inputs()[0].name: f[None, :, :]})[0][0]
        return (e / (np.linalg.norm(e) + 1e-9)).astype(np.float32)

    def _ecrire(self) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.voix), encoding="utf-8")
        os.replace(tmp, self.fichier)

    # --- enregistrement par compte -----------------------------------------------------------------
    def enregistrer(self, compte: str, audio: np.ndarray) -> int:
        e = self.empreinte(audio)
        if e is None:
            raise ValueError(f"phrase trop courte (au moins {DUREE_MIN_S:g} s de parole)")
        v = self.voix.setdefault(compte, {"echantillons": []})
        v["echantillons"] = (v["echantillons"] + [e.round(5).tolist()])[-ECHANTILLONS_MAX:]
        self._ecrire()
        return len(v["echantillons"])

    def oublier(self, compte: str) -> None:
        if self.voix.pop(compte, None) is not None:
            self._ecrire()

    def nombre(self, compte: str) -> int:
        return len(self.voix.get(compte, {}).get("echantillons", []))

    def _moyenne(self, compte: str) -> np.ndarray | None:
        ech = self.voix.get(compte, {}).get("echantillons") or []
        if not ech:
            return None
        m = np.mean(np.array(ech, dtype=np.float32), axis=0)
        return m / (np.linalg.norm(m) + 1e-9)

    # --- identification ------------------------------------------------------------------------------
    def identifier(self, audio: np.ndarray, comptes: list[str] | None = None) -> dict | None:
        """{"compte", "score"} si une voix enregistrée correspond nettement, sinon None."""
        if not self.voix or not self.disponible:
            return None
        e = self.empreinte(audio)
        if e is None:
            return None
        notes = sorted(((cosinus(e, m), c) for c in self.voix if (comptes is None or c in comptes)
                        and (m := self._moyenne(c)) is not None), reverse=True)
        if not notes:
            return None
        score, compte = notes[0]
        deuxieme = notes[1][0] if len(notes) > 1 else -1.0
        if score >= self.seuil and score - deuxieme >= self.ecart:
            return {"compte": compte, "score": round(score, 3)}
        return None
