"""Mode proactif : l'assistant prend la parole de lui-même, mais pas n'importe quand.

- Annonces : file gardée dans <état>/annonces.json (les 100 dernières). La page les récupère
  (GET /api/annonces?depuis=…), les affiche et les dit à voix haute si « parler » est vrai.
- Heures calmes (ex. 22:00-07:30) : seules les annonces urgentes sont dites ; les autres attendent
  et sont seulement affichées.
- Présence : si Home Assistant dit que personne n'est à la maison (entités person.*), rien n'est dit ;
  les annonces restent pour le retour (« en ton absence… »).
- Sources : conteneurs Docker qui tombent, appareils restés dans un état trop longtemps (règles de
  config), rappels de l'agenda, briefing du matin.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, time as heure_t
from pathlib import Path

PRIORITES = ("info", "important", "urgent")


def dans_heures_calmes(plage: str, maintenant: datetime | None = None) -> bool:
    """« 22:00-07:30 » (peut passer minuit). Vide = jamais calme."""
    if not plage or "-" not in plage:
        return False
    d, f = (heure_t.fromisoformat(x.strip()) for x in plage.split("-", 1))
    t = (maintenant or datetime.now()).time()
    return d <= t < f if d <= f else (t >= d or t < f)


class Annonces:
    def __init__(self, dossier: Path, heures_calmes: str = "") -> None:
        self.fichier = dossier / "annonces.json"
        self.heures_calmes = heures_calmes
        self._verrou = threading.Lock()
        self.presents: bool | None = None          # None = inconnu (on considère présent)
        try:
            self.liste: list[dict] = json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.liste = []

    def _ecrire(self) -> None:
        try:
            self.fichier.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.fichier.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.liste[-100:], ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.fichier)
        except OSError:
            pass

    def ajouter(self, texte: str, priorite: str = "info", source: str = "", cle: str = "") -> dict | None:
        """`cle` évite de répéter la même annonce (ex. « docker:radarr ») tant qu'elle n'est pas résolue."""
        with self._verrou:
            if cle and any(a.get("cle") == cle and not a.get("resolue") for a in self.liste[-100:]):
                return None
            a = {"id": f"{int(time.time() * 1000):x}{secrets.token_hex(2)}",
                 "heure": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "texte": texte, "priorite": priorite if priorite in PRIORITES else "info",
                 "source": source, "cle": cle, "lue": False, "resolue": False}
            self.liste.append(a)
            self.liste = self.liste[-100:]
            self._ecrire()
            return a

    def resoudre(self, cle: str) -> None:
        """Le problème a disparu : une nouvelle annonce pourra être faite s'il revient."""
        with self._verrou:
            for a in self.liste:
                if a.get("cle") == cle:
                    a["resolue"] = True
            self._ecrire()

    def marquer_lue(self, ident: str) -> bool:
        with self._verrou:
            for a in self.liste:
                if a["id"] == ident:
                    a["lue"] = True
                    self._ecrire()
                    return True
        return False

    def parler(self, a: dict, maintenant: datetime | None = None) -> bool:
        """Faut-il la dire à voix haute maintenant ?"""
        if a["priorite"] == "urgent":
            return True
        if self.presents is False:
            return False
        return not dans_heures_calmes(self.heures_calmes, maintenant)

    def depuis(self, ident: str = "", maintenant: datetime | None = None) -> list[dict]:
        ids = [a["id"] for a in self.liste]
        debut = ids.index(ident) + 1 if ident in ids else 0
        if not ident:      # première visite de la page : seulement les non lues des dernières 24 h
            limite = time.time() - 86400
            choix = [a for a in self.liste if not a["lue"]
                     and datetime.fromisoformat(a["heure"]).timestamp() > limite]
        else:
            choix = self.liste[debut:]
        return [{**a, "parler": self.parler(a, maintenant)} for a in choix]
