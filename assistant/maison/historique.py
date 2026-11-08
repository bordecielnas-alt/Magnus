"""Historique des actions sur la maison (annulables une par une) et état connu de chaque appareil.

- actions.json : les 500 dernières actions, avec l'état de l'appareil lu juste avant (pour l'annuler).
- etats_appareils.json : le dernier état connu de chaque appareil, gardé même si Home Assistant ne répond plus.
L'origine d'une action (voix, texte, page, claude) et la phrase qui l'a demandée passent par `demande`,
un ContextVar posé par l'API avant de traiter le message.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

ACTIONS_MAX = 500
demande: ContextVar[tuple[str, str]] = ContextVar("demande", default=("texte", ""))   # (source, texte)


def maintenant() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _resume_etat(s: dict | None) -> dict | None:
    if not s:
        return None
    a = s.get("attributes") or {}
    garde = ("brightness", "color_mode", "rgb_color", "color_temp_kelvin", "current_position", "temperature",
             "hvac_mode", "volume_level", "percentage")
    return {"etat": s.get("state"), "attributs": {k: a[k] for k in garde if a.get(k) is not None}}


class Historique:
    def __init__(self, dossier: Path) -> None:
        self.f_actions = dossier / "actions.json"
        self.f_etats = dossier / "etats_appareils.json"
        self._verrou = threading.Lock()
        self.actions: list[dict] = self._lire(self.f_actions, [])
        self.etats: dict[str, dict] = self._lire(self.f_etats, {})

    @staticmethod
    def _lire(f: Path, defaut):
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return defaut

    @staticmethod
    def _ecrire(f: Path, donnees) -> None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            pass

    # --- actions ------------------------------------------------------------------------------------
    def noter(self, entity_id: str, nom: str, action: str, avant: dict | None, apres: dict | None,
              annulable: bool = True) -> dict:
        source, texte = demande.get()
        a = {"id": secrets.token_hex(4), "heure": maintenant(), "source": source, "texte": texte[:300],
             "appareil": entity_id, "nom": nom, "action": action,
             "avant": _resume_etat(avant), "apres": _resume_etat(apres),
             "annulable": bool(annulable and avant), "annulee": False}
        with self._verrou:
            self.actions.append(a)
            del self.actions[:-ACTIONS_MAX]
            self._ecrire(self.f_actions, self.actions)
        if apres:
            self.memoriser(entity_id, nom, apres.get("state"))
        return a

    def liste(self, limite: int = 50) -> list[dict]:
        return list(reversed(self.actions[-limite:]))

    def trouver(self, ident: str) -> dict | None:
        return next((a for a in self.actions if a["id"] == ident), None)

    def marquer_annulee(self, ident: str) -> None:
        with self._verrou:
            a = self.trouver(ident)
            if a:
                a["annulee"], a["annulable"] = True, False
                self._ecrire(self.f_actions, self.actions)

    # --- état connu des appareils -----------------------------------------------------------------
    def memoriser(self, entity_id: str, nom: str, etat: str | None, enregistrer: bool = True) -> None:
        if etat is None:
            return
        ancien = self.etats.get(entity_id, {})
        if ancien.get("etat") != etat or ancien.get("nom") != nom:
            self.etats[entity_id] = {"nom": nom, "etat": etat, "maj": maintenant()}
            if enregistrer:
                self._ecrire(self.f_etats, self.etats)

    def memoriser_tout(self, appareils: list[tuple[str, str, str]]) -> None:
        for eid, nom, etat in appareils:
            self.memoriser(eid, nom, etat, enregistrer=False)
        self._ecrire(self.f_etats, self.etats)

    def appareils(self) -> list[dict]:
        return [{"entity_id": i, **e} for i, e in sorted(self.etats.items(), key=lambda x: x[1].get("nom", x[0]))]
