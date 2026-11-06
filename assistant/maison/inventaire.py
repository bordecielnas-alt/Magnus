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
    nom_appareil: str = ""        # nom de l'appareil HA (souvent celui qu'on utilise : « L3 »)

    @property
    def domaine(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def libelle(self) -> str:
        return f"{self.nom} ({self.piece})" if self.piece and self.piece.lower() not in self.nom.lower() else self.nom

    def mots_nom(self) -> set[str]:
        m = mots(self.nom) | mots(self.entity_id.split(".", 1)[1].replace("_", " ")) | mots(self.nom_appareil)
        for a in self.alias:
            m |= mots(a)
        return m

    def mots_piece(self) -> set[str]:
        m = mots(self.piece)
        for a in self.alias_piece:
            m |= mots(a)
        return m


@dataclass
class Ecartee:
    entity_id: str
    nom: str
    raison: str   # « type non autorisé (script) », « masquée », « désactivée », « réglage interne »


def construire(resultats: dict, domaines: set[str]) -> tuple[list[Entite], bool]:
    """Assemble les entités à partir des réponses WebSocket. Renvoie (entités, registres_lus)."""
    entites, registres, _ = construire_detail(resultats, domaines)
    return entites, registres


def construire_detail(resultats: dict, domaines: set[str]) -> tuple[list[Entite], bool, list[Ecartee]]:
    """Comme construire, avec en plus les entités écartées (pour expliquer un « introuvable »)."""
    ecartees: list[Ecartee] = []
    etats = {s["entity_id"]: s for s in (resultats.get(CMD_ETATS) or [])}
    pieces = {a["area_id"]: a for a in (resultats.get(CMD_PIECES) or [])}
    appareils = {d["id"]: d for d in (resultats.get(CMD_APPAREILS) or [])}
    registre = resultats.get(CMD_ENTITES)
    sortie: list[Entite] = []

    def depuis_etat(eid: str, s: dict) -> None:
        a = s.get("attributes") or {}
        nom = a.get("friendly_name") or eid
        if eid.split(".", 1)[0] in domaines:
            sortie.append(Entite(eid, nom, etat=s.get("state", ""), attributs=a))
        else:
            ecartees.append(Ecartee(eid, nom, f"type non autorisé ({eid.split('.', 1)[0]})"))

    if registre is None:  # pas les droits : états seuls
        for eid, s in etats.items():
            depuis_etat(eid, s)
        return sortie, False, ecartees

    vus = set()
    for r in registre:
        eid = r["entity_id"]
        vus.add(eid)
        nom_reg = r.get("name") or r.get("original_name") or (etats.get(eid, {}).get("attributes") or {}).get("friendly_name") or eid
        if eid.split(".", 1)[0] not in domaines:
            ecartees.append(Ecartee(eid, nom_reg, f"type non autorisé ({eid.split('.', 1)[0]})"))
            continue
        if r.get("disabled_by") or r.get("hidden_by") or r.get("entity_category"):
            raison = "désactivée" if r.get("disabled_by") else "masquée" if r.get("hidden_by") else "réglage interne"
            ecartees.append(Ecartee(eid, nom_reg, raison))
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
            appareil.get("name_by_user") or appareil.get("name") or "",
        ))
    # entités sans unique_id (définies en YAML : groupes, modèles…) : absentes du registre
    for eid, s in etats.items():
        if eid not in vus:
            depuis_etat(eid, s)
    return sortie, True, ecartees


class Inventaire:
    def __init__(self, ha: HomeAssistant, domaines: list[str], rafraichir_s: float = 300) -> None:
        self.ha = ha
        self.domaines = set(domaines)
        self.rafraichir_s = rafraichir_s
        self.entites: list[Entite] = []
        self.ecartees: list[Ecartee] = []
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
        self.entites, self.registres_lus, self.ecartees = construire_detail(res, self.domaines)
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
