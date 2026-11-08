"""Habitudes repérées dans l'historique des actions, et routines (suites d'actions existantes).

Habitude : la même action sur le même appareil, à peu près à la même heure (±30 min), au moins
`jours_min` jours différents sur les `fenetre_jours` derniers jours. Elle est seulement proposée ;
l'automatisation Home Assistant n'est créée qu'après un « oui » explicite.

Routine : un nom (« mode cinéma ») et une liste d'étapes {entity_ids, action, valeur?, couleur?}
qui passent par le même chemin que les autres actions (domaines autorisés, actions sensibles refusées,
historique annulable). Jamais de code libre.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ACTIONS_ROUTINE = ("allumer", "eteindre", "ouvrir", "fermer", "regler")
SERVICES = {("allumer", "light"): "light.turn_on", ("eteindre", "light"): "light.turn_off",
            ("allumer", "switch"): "switch.turn_on", ("eteindre", "switch"): "switch.turn_off",
            ("allumer", "fan"): "fan.turn_on", ("eteindre", "fan"): "fan.turn_off",
            ("allumer", "media_player"): "media_player.turn_on", ("eteindre", "media_player"): "media_player.turn_off",
            ("ouvrir", "cover"): "cover.open_cover", ("fermer", "cover"): "cover.close_cover"}


def detecter(actions: list[dict], jours_min: int = 4, fenetre_jours: int = 21,
             maintenant: datetime | None = None) -> list[dict]:
    """Habitudes dans l'historique (liste d'actions de maison.historique, du plus ancien au plus récent)."""
    maintenant = maintenant or datetime.now().astimezone()
    debut = maintenant - timedelta(days=fenetre_jours)
    groupes: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    noms: dict[str, str] = {}
    for a in actions:
        if a.get("action") not in SERVICES_ACTIONS or a.get("annulee"):
            continue
        try:
            h = datetime.fromisoformat(a["heure"])
        except (KeyError, ValueError):
            continue
        if h < debut:
            continue
        groupes[(a["appareil"], a["action"])].append(h)
        noms[a["appareil"]] = a.get("nom", a["appareil"])
    sortie = []
    for (appareil, action), heures in groupes.items():
        minutes = sorted(h.hour * 60 + h.minute for h in heures)
        # la plus grande grappe de moments à moins de 30 min les uns des autres (même jour compté une fois)
        meilleur: list[datetime] = []
        for m in minutes:
            proches = [h for h in heures if abs((h.hour * 60 + h.minute) - m) <= 30]
            if len({h.date() for h in proches}) > len({h.date() for h in meilleur}):
                meilleur = proches
        jours = {h.date() for h in meilleur}
        if len(jours) >= jours_min:
            moyenne = round(sum(h.hour * 60 + h.minute for h in meilleur) / len(meilleur) / 5) * 5
            heure = f"{moyenne // 60:02d}:{moyenne % 60:02d}"
            sortie.append({"id": f"{appareil}|{action}|{heure}", "appareil": appareil, "nom": noms[appareil],
                           "action": action, "heure": heure, "jours": len(jours),
                           "texte": f"{noms[appareil]} : {VERBES.get(action, action)} vers {heure.replace(':', 'h')} "
                                    f"({len(jours)} jours sur les {fenetre_jours} derniers)"})
    return sorted(sortie, key=lambda x: -x["jours"])


VERBES = {"allumer": "allumé", "eteindre": "éteint", "ouvrir": "ouvert", "fermer": "fermé"}
SERVICES_ACTIONS = {a for a, _ in SERVICES}


def automatisation(h: dict) -> tuple[str, dict]:
    """(identifiant, configuration) d'une automatisation Home Assistant pour cette habitude."""
    domaine = h["appareil"].split(".", 1)[0]
    service = SERVICES.get((h["action"], domaine))
    if not service:
        raise ValueError(f"pas d'automatisation possible pour {h['action']} sur {domaine}")
    ident = "assistant_" + re.sub(r"[^a-z0-9]+", "_", f"{h['appareil']}_{h['action']}_{h['heure']}".lower()).strip("_")
    return ident, {
        "id": ident,
        "alias": f"{h['nom']} : {VERBES.get(h['action'], h['action'])} à {h['heure']} (proposé par l'assistant)",
        "description": "Habitude repérée par l'assistant et acceptée.",
        "mode": "single",
        "triggers": [{"trigger": "time", "at": f"{h['heure']}:00"}],
        "conditions": [],
        "actions": [{"action": service, "target": {"entity_id": h["appareil"]}}],
    }


class Routines:
    def __init__(self, dossier: Path) -> None:
        self.fichier = dossier / "routines.json"
        self._verrou = threading.Lock()
        try:
            self.liste: dict[str, dict] = json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.liste = {}

    def _ecrire(self) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.liste, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.fichier)

    @staticmethod
    def cle(nom: str) -> str:
        import unicodedata
        t = "".join(c for c in unicodedata.normalize("NFD", nom.lower()) if unicodedata.category(c) != "Mn")
        return re.sub(r"[^a-z0-9]+", " ", t).strip()

    def enregistrer(self, nom: str, etapes: list[dict], compte: str = "") -> dict:
        if not nom.strip() or not etapes or len(etapes) > 20:
            raise ValueError("une routine a un nom et de 1 à 20 étapes")
        propres = []
        for e in etapes:
            if e.get("action") not in ACTIONS_ROUTINE or not e.get("entity_ids"):
                raise ValueError(f"étape invalide : {e}")
            propres.append({"entity_ids": list(e["entity_ids"])[:30], "action": e["action"],
                            **({"valeur": float(e["valeur"])} if e.get("valeur") is not None else {}),
                            **({"couleur": str(e["couleur"])[:30]} if e.get("couleur") else {})})
        r = {"nom": nom.strip()[:60], "etapes": propres, "compte": compte,
             "maj": datetime.now().astimezone().isoformat(timespec="seconds")}
        with self._verrou:
            self.liste[self.cle(nom)] = r
            self._ecrire()
        return r

    def supprimer(self, nom: str) -> bool:
        with self._verrou:
            ok = self.liste.pop(self.cle(nom), None) is not None
            if ok:
                self._ecrire()
        return ok

    def trouver(self, texte: str) -> dict | None:
        """« lance le mode cinéma », « mode cinéma », « active la routine soirée »."""
        t = self.cle(texte)
        t = re.sub(r"^(lance|lancer|active|activer|demarre|mets|passe en|passe|declenche)( la routine| le)?\s+", "", t)
        t = re.sub(r"^(la routine|routine)\s+", "", t)
        return self.liste.get(t) or next((r for k, r in self.liste.items() if t and (t == k or t == "mode " + k)), None)
