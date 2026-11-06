"""Inventaire des entités de Home Assistant : noms, pièces, alias et états.

Lu par l'API WebSocket (registres) et rafraîchi périodiquement. Si les registres sont
refusés (utilisateur HA non administrateur), on se contente des états : noms sans pièces.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from ..home_assistant import HomeAssistant
from .texte import mots

CMD_PIECES = "config/area_registry/list"
CMD_APPAREILS = "config/device_registry/list"
CMD_ENTITES = "config/entity_registry/list"
CMD_ETATS = "get_states"


@dataclass
class Entite:
    entity_id: str
    nom: str
    piece: str = ""
    alias: list[str] = field(default_factory=list)
    alias_piece: list[str] = field(default_factory=list)
    etat: str = ""
    attributs: dict = field(default_factory=dict)

    @property
    def domaine(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def libelle(self) -> str:
        return f"{self.nom} ({self.piece})" if self.piece and self.piece.lower() not in self.nom.lower() else self.nom

    def mots_nom(self) -> set[str]:
        m = mots(self.nom) | mots(self.entity_id.split(".", 1)[1].replace("_", " "))
        for a in self.alias:
            m |= mots(a)
        return m

    def mots_piece(self) -> set[str]:
        m = mots(self.piece)
        for a in self.alias_piece:
            m |= mots(a)
        return m


def construire(resultats: dict, domaines: set[str]) -> tuple[list[Entite], bool]:
    """Assemble les entités à partir des réponses WebSocket. Renvoie (entités, registres_lus)."""
    etats = {s["entity_id"]: s for s in (resultats.get(CMD_ETATS) or [])}
    pieces = {a["area_id"]: a for a in (resultats.get(CMD_PIECES) or [])}
    appareils = {d["id"]: d for d in (resultats.get(CMD_APPAREILS) or [])}
    registre = resultats.get(CMD_ENTITES)
    sortie: list[Entite] = []

    if registre is None:  # pas les droits : états seuls
        for eid, s in etats.items():
            if eid.split(".", 1)[0] in domaines:
                a = s.get("attributes") or {}
                sortie.append(Entite(eid, a.get("friendly_name") or eid, etat=s.get("state", ""), attributs=a))
        return sortie, False

    for r in registre:
        eid = r["entity_id"]
        if eid.split(".", 1)[0] not in domaines:
            continue
        if r.get("disabled_by") or r.get("hidden_by") or r.get("entity_category"):
            continue
        s = etats.get(eid) or {}
        a = s.get("attributes") or {}
        appareil = appareils.get(r.get("device_id") or "") or {}
        area_id = r.get("area_id") or appareil.get("area_id") or ""
        piece = pieces.get(area_id) or {}
        nom = (a.get("friendly_name") or r.get("name") or r.get("original_name")
               or appareil.get("name_by_user") or appareil.get("name") or eid)
        sortie.append(Entite(
            eid, nom, piece.get("name", ""), list(r.get("aliases") or []),
            list(piece.get("aliases") or []), s.get("state", ""), a,
        ))
    return sortie, True


class Inventaire:
    def __init__(self, ha: HomeAssistant, domaines: list[str], rafraichir_s: float = 300) -> None:
        self.ha = ha
        self.domaines = set(domaines)
        self.rafraichir_s = rafraichir_s
        self.entites: list[Entite] = []
        self.registres_lus = False
        self.lu_a = 0.0
        self.erreur = ""
        self._verrou = asyncio.Lock()

    async def obtenir(self, forcer: bool = False) -> list[Entite]:
        async with self._verrou:
            if forcer or not self.entites or time.monotonic() - self.lu_a > self.rafraichir_s:
                await self._charger()
        return self.entites

    async def _charger(self) -> None:
        if not self.ha.configure:
            self.erreur = "Home Assistant non configuré"
            return
        try:
            res = await self.ha.ws([CMD_ETATS, CMD_PIECES, CMD_APPAREILS, CMD_ENTITES])
        except ConnectionError as e:
            self.erreur = f"inventaire impossible : {e}"
            return
        self.entites, self.registres_lus = construire(res, self.domaines)
        self.lu_a = time.monotonic()
        self.erreur = ""

    def resume(self) -> dict:
        par_domaine: dict[str, int] = {}
        for e in self.entites:
            par_domaine[e.domaine] = par_domaine.get(e.domaine, 0) + 1
        return {
            "entites": len(self.entites),
            "pieces": len({e.piece for e in self.entites if e.piece}),
            "par_domaine": par_domaine,
            "registres_lus": self.registres_lus,
            "age_s": round(time.monotonic() - self.lu_a) if self.lu_a else None,
            "erreur": self.erreur,
        }
