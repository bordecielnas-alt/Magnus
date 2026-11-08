"""Journal d'audit : qui a fait quoi, d'où, et quand (connexions, actions sur la maison, réglages).

Une ligne JSON par événement dans <état>/audit.jsonl ; au-delà de 5 Mo, le fichier est renommé
en audit.1.jsonl (une seule génération gardée). Les secrets n'y sont jamais écrits.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

TAILLE_MAX = 5 * 1024 * 1024


class Audit:
    def __init__(self, dossier: Path) -> None:
        self.fichier = dossier / "audit.jsonl"
        self._verrou = threading.Lock()

    def noter(self, type_: str, compte: str = "", ip: str = "", **detail) -> None:
        ligne = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": type_, "compte": compte, "ip": ip}
        ligne.update({k: v for k, v in detail.items() if v not in (None, "")})
        texte = json.dumps(ligne, ensure_ascii=False) + "\n"
        with self._verrou:
            try:
                self.fichier.parent.mkdir(parents=True, exist_ok=True)
                if self.fichier.is_file() and self.fichier.stat().st_size > TAILLE_MAX:
                    os.replace(self.fichier, self.fichier.with_name("audit.1.jsonl"))
                with open(self.fichier, "a", encoding="utf-8") as f:
                    f.write(texte)
            except OSError:
                pass   # le journal ne doit jamais empêcher l'assistant de répondre

    def derniers(self, n: int = 200, type_: str = "") -> list[dict]:
        if not self.fichier.is_file():
            return []
        with self._verrou, open(self.fichier, encoding="utf-8") as f:
            lignes = deque(f, maxlen=n * 5 if type_ else n)
        sortie = []
        for l in reversed(lignes):
            try:
                e = json.loads(l)
            except ValueError:
                continue
            if not type_ or e.get("type", "").startswith(type_):
                sortie.append(e)
            if len(sortie) >= n:
                break
        return sortie
