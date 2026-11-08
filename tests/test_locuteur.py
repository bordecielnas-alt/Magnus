"""Reconnaissance de qui parle : caractéristiques, enregistrement, identification (modèle factice)."""

import numpy as np

from assistant.locuteur import Locuteurs, fbank


def voix(f0, duree=2.0, bruit=0.0, graine=0):
    """Signal « vocal » synthétique : fondamentale + harmoniques (assez différent d'une voix à l'autre)."""
    t = np.arange(int(16000 * duree)) / 16000
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    return (0.3 * x / 3 + bruit * np.random.default_rng(graine).standard_normal(len(t))).astype(np.float32)


class FausseSession:
    """Remplace ONNX : empreinte = spectre moyen (suffit à séparer deux fondamentales)."""
    def get_inputs(self):
        return [type("I", (), {"name": "feats"})()]

    def run(self, _, entrees):
        f = entrees["feats"][0]
        return [np.exp(f).mean(axis=0)[None, :]]


def locuteurs(tmp_path):
    loc = Locuteurs(tmp_path / "modele.onnx", tmp_path, seuil=0.5, ecart=0.01)
    (tmp_path / "modele.onnx").write_bytes(b"x")
    loc._session = FausseSession()
    return loc


def test_fbank_forme():
    f = fbank(voix(120))
    assert f.shape[1] == 80 and abs(float(f.mean())) < 1e-3     # moyenne retirée


def test_enregistrer_et_reconnaitre(tmp_path):
    loc = locuteurs(tmp_path)
    for g in range(3):
        loc.enregistrer("benjamin", voix(110, bruit=0.01, graine=g))
        loc.enregistrer("elodie", voix(230, bruit=0.01, graine=10 + g))
    assert loc.nombre("benjamin") == 3
    assert loc.identifier(voix(112, bruit=0.01, graine=99))["compte"] == "benjamin"
    assert loc.identifier(voix(225, bruit=0.01, graine=98))["compte"] == "elodie"
    assert loc.identifier(voix(110, duree=0.5)) is None                  # trop court
    # gardé sur disque, jamais l'audio
    assert Locuteurs(tmp_path / "modele.onnx", tmp_path).nombre("elodie") == 3
    loc.oublier("elodie")
    assert loc.nombre("elodie") == 0


def test_phrase_trop_courte_refusee(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        locuteurs(tmp_path).enregistrer("x", voix(110, duree=0.4))
