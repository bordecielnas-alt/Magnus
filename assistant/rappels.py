"""Rappels dans le chat (« rappelle-moi dans 10 minutes de sortir le linge ») : pas dans le calendrier.

Gardés dans <état>/rappels.json. À l'heure dite, le rappel devient une annonce (source « rappel »,
priorité « urgent » : affiché et dit à voix haute, même pendant les heures calmes, puisque la personne l'a
demandé). Les rendez-vous, eux, vont dans le calendrier (agenda.py).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path


def maintenant() -> datetime:
    return datetime.now().astimezone()


class Rappels:
    def __init__(self, dossier: Path) -> None:
        self.fichier = dossier / "rappels.json"
        self._verrou = threading.Lock()
        try:
            self.liste: list[dict] = json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.liste = []

    def _ecrire(self) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.liste, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.fichier)

    def ajouter(self, texte: str, quand: datetime, compte: str = "", conversation: str = "") -> dict:
        texte = " ".join(texte.split())[:300]
        if not texte:
            raise ValueError("rappel vide")
        if quand.tzinfo is None:
            quand = quand.astimezone()
        if quand < maintenant().replace(microsecond=0):
            raise ValueError("cette heure est déjà passée")
        r = {"id": secrets.token_hex(4), "texte": texte, "quand": quand.isoformat(timespec="seconds"),
             "compte": compte, "conversation": conversation, "fait": False}
        with self._verrou:
            self.liste.append(r)
            self._ecrire()
        return r

    def a_venir(self, compte: str | None = None) -> list[dict]:
        return sorted((r for r in self.liste if not r["fait"] and (compte is None or r["compte"] in (compte, ""))),
                      key=lambda r: r["quand"])

    def supprimer(self, ident: str) -> dict | None:
        with self._verrou:
            r = next((x for x in self.liste if x["id"] == ident and not x["fait"]), None)
            if r:
                self.liste.remove(r)
                self._ecrire()
        return r

    def echus(self, a: datetime | None = None) -> list[dict]:
        """Rappels arrivés à échéance : marqués faits et renvoyés (une seule fois chacun)."""
        a = a or maintenant()
        with self._verrou:
            dus = [r for r in self.liste if not r["fait"] and datetime.fromisoformat(r["quand"]) <= a]
            for r in dus:
                r["fait"] = True
            if dus:
                # on garde les 200 derniers rappels faits, pour l'historique
                faits = [r for r in self.liste if r["fait"]]
                self.liste = [r for r in self.liste if not r["fait"]] + faits[-200:]
                self._ecrire()
        return dus
