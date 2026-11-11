"""Mode proactif : l'assistant prend la parole de lui-même, mais pas n'importe quand.

- Annonces : file gardée dans <état>/annonces.json (les 100 dernières). La page les récupère
  (GET /api/annonces?depuis=…), les affiche et les dit à voix haute si « parler » est vrai.
- Heures calmes (ex. 22:00-07:30) : seules les annonces urgentes sont dites ; les autres attendent
  et sont seulement affichées.
- Présence : si Home Assistant dit que personne n'est à la maison (entités person.*), rien n'est dit ;
  les annonces restent pour le retour (« en ton absence… »).
- Sources : conteneurs Docker qui tombent, serveur Unraid (disques, espace, parité), mises à jour
  d'images Docker (annonce avec boutons « Mettre à jour / Ignorer »), appareils restés dans un état
  trop longtemps (règles de config), rappels de l'agenda, briefing du matin.
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

    def ajouter(self, texte: str, priorite: str = "info", source: str = "", cle: str = "",
                actions: list[dict] | None = None, cible: dict | None = None) -> dict | None:
        """`cle` évite de répéter la même annonce (ex. « docker:radarr ») tant qu'elle n'est pas résolue.

        `actions` : boutons proposés sous l'annonce ([{"id": "mettre_a_jour", "libelle": "Mettre à jour"}, …]) ;
        `cible` : ce sur quoi ils portent (ex. {"type": "maj_docker", "conteneur": "plex"}). Le choix fait est
        gardé dans « decision »."""
        with self._verrou:
            if cle and any(a.get("cle") == cle and not a.get("resolue") for a in self.liste[-100:]):
                return None
            a = {"id": f"{int(time.time() * 1000):x}{secrets.token_hex(2)}",
                 "heure": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "texte": texte, "priorite": priorite if priorite in PRIORITES else "info",
                 "source": source, "cle": cle, "lue": False, "resolue": False,
                 **({"actions": actions, "cible": cible or {}, "decision": None} if actions else {})}
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

    def trouver(self, ident: str) -> dict | None:
        return next((a for a in self.liste if a["id"] == ident), None)

    def decider(self, ident: str, decision: str) -> dict | None:
        """Note le bouton choisi ; None si l'annonce est inconnue ou déjà tranchée (autre appareil, double clic)."""
        with self._verrou:
            a = self.trouver(ident)
            if not a or not a.get("actions") or a.get("decision"):
                return None
            a["decision"] = decision
            a["lue"] = True
            self._ecrire()
            return a

    def annuler_decision(self, ident: str) -> None:
        """L'action a échoué : les boutons redeviennent utilisables."""
        with self._verrou:
            a = self.trouver(ident)
            if a:
                a["decision"] = None
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


class MisesAJourIgnorees:
    """Conteneurs dont la mise à jour a été ignorée (bouton « Ignorer ») : <état>/maj_ignorees.json.

    Unraid ne dit pas quelle version est proposée : l'oubli dure tant que le conteneur n'est pas à jour,
    avec un rappel au bout de `jours` jours (la mise à jour attend peut-être depuis longtemps)."""

    def __init__(self, dossier: Path, jours: float = 30) -> None:
        self.fichier = dossier / "maj_ignorees.json"
        self.jours = jours
        try:
            self.noms: dict[str, float] = dict(json.loads(self.fichier.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            self.noms = {}

    def _ecrire(self) -> None:
        try:
            self.fichier.parent.mkdir(parents=True, exist_ok=True)
            self.fichier.write_text(json.dumps(self.noms, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def ignorer(self, nom: str, maintenant: float | None = None) -> None:
        self.noms[nom] = maintenant or time.time()
        self._ecrire()

    def est_ignoree(self, nom: str, maintenant: float | None = None) -> bool:
        quand = self.noms.get(nom)
        return quand is not None and (maintenant or time.time()) - quand < self.jours * 86400

    def garder_seulement(self, en_attente: set[str]) -> None:
        """Les conteneurs mis à jour entre-temps sont oubliés : leur prochaine mise à jour sera annoncée."""
        avant = len(self.noms)
        self.noms = {n: t for n, t in self.noms.items() if n in en_attente}
        if len(self.noms) != avant:
            self._ecrire()
