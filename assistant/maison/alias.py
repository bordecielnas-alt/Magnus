"""Alias appris : « lumière du salon » -> [light.plafonnier]. Fichier JSON lisible et modifiable."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .texte import normaliser


class AliasAppris:
    def __init__(self, chemin: Path) -> None:
        self.chemin = chemin
        self.donnees: dict[str, dict] = {}
        if chemin.is_file():
            try:
                self.donnees = json.loads(chemin.read_text(encoding="utf-8")).get("alias", {})
            except (OSError, ValueError):
                self.donnees = {}

    def table(self) -> dict[str, list[str]]:
        return {k: v["entites"] for k, v in self.donnees.items()}

    def apprendre(self, phrase: str, entites: list[str], source: str = "question") -> str:
        cle = normaliser(phrase)
        self.donnees[cle] = {"entites": entites, "source": source, "date": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._ecrire()
        return cle

    def oublier(self, cle: str) -> bool:
        if self.donnees.pop(normaliser(cle), None) is None:
            return False
        self._ecrire()
        return True

    def liste(self) -> list[dict]:
        return [{"phrase": k, **v} for k, v in sorted(self.donnees.items())]

    def _ecrire(self) -> None:
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        texte = json.dumps({"alias": self.donnees}, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.chemin.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texte)
        os.replace(tmp, self.chemin)
