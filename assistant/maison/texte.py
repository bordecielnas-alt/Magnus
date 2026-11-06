"""Normalisation du français pour comparer des noms d'appareils et de pièces."""

from __future__ import annotations

import re
import unicodedata

MOTS_VIDES = {
    "le", "la", "les", "l", "un", "une", "des", "du", "de", "d", "au", "aux", "a", "en", "dans",
    "sur", "pour", "et", "est", "ce", "cette", "ces", "mon", "ma", "mes", "ton", "ta", "tes",
    "notre", "nos", "son", "sa", "ses", "elle", "il", "ils", "elles", "moi", "stp", "svp",
    "plait", "s", "y", "ca", "qu", "que", "quoi", "piece",
}


def sans_accents(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def normaliser(t: str) -> str:
    t = sans_accents(t.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def mots(t: str) -> set[str]:
    """Mots significatifs (sans articles), au singulier approximatif."""
    sortie = set()
    for m in normaliser(t).split():
        if m in MOTS_VIDES:
            continue
        if len(m) > 3 and m.endswith(("s", "x")) and not m.endswith("ss"):
            m = m[:-1]
        sortie.add(m)
    return sortie
