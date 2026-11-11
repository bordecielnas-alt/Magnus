"""Cartes du chat : l'état réel d'un appareil, dessiné par la page sous la réponse.

Les cartes sont construites ici, par le serveur, à partir de l'état lu dans Home Assistant au moment
de la réponse : ni Claude ni un autre modèle n'en écrit le contenu, donc rien d'inventé. Une carte
apparaît quand une demande a touché ou lu des appareils (règles, Assist, outils de Claude).

Un geste sur une carte (interrupteur, curseur, couleur) repasse par Maison._executer : mêmes
appareils autorisés, mêmes refus des actions sensibles, même historique, même « annule ».
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import TEINTES
from .commande import COULEURS, Commande

if TYPE_CHECKING:
    from . import Maison, ReponseMaison
    from .inventaire import Entite

MAX_CARTES = 8                 # au-delà, la carte « groupe » ne montre que les premiers
MODES_COULEUR = {"hs", "rgb", "rgbw", "rgbww", "xy"}
MODES_INTENSITE = MODES_COULEUR | {"brightness", "color_temp", "white"}
# couleurs proposées sur la carte : les 12 teintes du cercle chromatique (la roue de la carte donne
# toutes les autres). Envoyées en teinte/saturation (hs_color), pas par leur nom.
PALETTE = [{"nom": nom, "teinte": t} for t, nom in TEINTES]
MAX_GROUPE = 20                # appareils réglés d'un coup depuis une carte « groupe »


def _pourcent(brightness) -> int | None:
    return round(brightness * 100 / 255) if isinstance(brightness, (int, float)) else None


def carte(e: Entite, s: dict | None) -> dict | None:
    """Carte d'un appareil d'après son état HA (`s`), ou None si ce type n'a pas (encore) de carte."""
    d = e.domaine
    etat = (s or {}).get("state")
    a = (s or {}).get("attributes") or {}
    base = {"entity_id": e.entity_id, "nom": e.nom, "piece": e.piece,
            "disponible": etat not in (None, "unavailable", "unknown")}
    if d == "light":
        modes = set(a.get("supported_color_modes") or [])
        kmin, kmax = a.get("min_color_temp_kelvin"), a.get("max_color_temp_kelvin")
        allume = etat == "on"
        return {**base, "type": "lumiere", "allume": allume,
                "intensite": _pourcent(a.get("brightness")) if allume else None,
                "kelvin": a.get("color_temp_kelvin") if allume and a.get("color_mode") == "color_temp" else None,
                "rgb": a.get("rgb_color") if allume and a.get("color_mode") in MODES_COULEUR else None,
                "hs": a.get("hs_color") if allume and a.get("color_mode") in MODES_COULEUR else None,
                "reglages": {
                    # sans supported_color_modes (vieille intégration), on propose l'intensité par prudence
                    "intensite": not modes or bool(modes & MODES_INTENSITE),
                    "blanc": [kmin or 2200, kmax or 6500] if "color_temp" in modes else None,
                    "couleurs": PALETTE if modes & MODES_COULEUR else None,
                }}
    if d in ("switch", "fan", "media_player"):
        return {**base, "type": "interrupteur", "allume": etat not in ("off", "standby", "idle", None)
                and base["disponible"]}
    if d == "cover":
        pos = a.get("current_position")
        return {**base, "type": "volet", "ouvert": etat in ("open", "opening"), "position": pos,
                "reglages": {"position": pos is not None}}
    return None


async def cartes_pour(maison: Maison, entity_ids: list[str]) -> list[dict]:
    """Cartes des appareils touchés par une demande. Plusieurs : une carte « groupe »."""
    if not entity_ids:
        return []
    connus = {e.entity_id: e for e in await maison.inventaire.obtenir()}
    vus, cartes = set(), []
    for eid in entity_ids:
        e = connus.get(eid)
        if not e or eid in vus or maison.masque(e) or maison.sensible(e) or maison.interdit(e):
            continue          # appareil caché en ce moment, ou à piloter depuis Home Assistant
        vus.add(eid)
        c = carte(e, await maison.ha.etat_entite(eid))
        if c:
            cartes.append(c)
        if len(cartes) >= MAX_CARTES:
            break
    if len(cartes) <= 1:
        return cartes
    pieces = {c["piece"] for c in cartes}
    titre = pieces.pop() if len(pieces) == 1 and "" not in pieces else f"{len(cartes)} appareils"
    return [{"type": "groupe", "titre": titre, "elements": cartes}]


def _commande(allume: bool | None = None, intensite: float | None = None, kelvin: int | None = None,
              couleur: str | None = None, position: float | None = None, teinte: float | None = None,
              saturation: float | None = None) -> Commande:
    """Le geste d'une carte en commande de maison (un seul réglage à la fois)."""
    if allume is False:
        return Commande("eteindre", "")
    if teinte is not None:
        s = 100.0 if saturation is None else saturation
        return Commande("allumer", "", intensite, hs=(float(teinte) % 360, max(0.0, min(100.0, float(s)))))
    if couleur:
        nom = COULEURS.get(couleur.lower())
        if not nom:
            raise ValueError(f"couleur inconnue : {couleur}")
        return Commande("allumer", "", intensite, couleur=nom)
    if kelvin:
        return Commande("allumer", "", intensite, kelvin=int(max(1500, min(9000, kelvin))))
    if position is not None:
        return Commande("regler", "", max(0, min(100, position)))
    if intensite is not None:
        return Commande("regler" if intensite > 0 else "eteindre", "", max(1, min(100, intensite)))
    if allume:
        return Commande("allumer", "")
    raise ValueError("rien à régler")


def compatible(c: dict, champs: dict) -> bool:
    """La carte `c` accepte-t-elle ce réglage ? (une prise n'a pas de couleur, une ampoule blanche non plus)"""
    if not c.get("disponible"):
        return False
    reg = c.get("reglages") or {}
    if "teinte" in champs or "couleur" in champs:
        return bool(reg.get("couleurs"))
    if "kelvin" in champs:
        return bool(reg.get("blanc"))
    if "position" in champs:
        return bool(reg.get("position"))
    if "intensite" in champs and champs["intensite"] > 0:
        return bool(reg.get("intensite"))
    return True       # allumer / éteindre : tout appareil joignable


async def regler(maison: Maison, entity_id: str, conversation: str = "", **champs) -> tuple[ReponseMaison | None, dict | None]:
    """Un geste sur une carte. Renvoie (réponse de Maison, carte à jour) ; (None, None) si l'appareil
    n'est pas pilotable (inconnu, hors des domaines autorisés, caché ou sensible)."""
    connus = {e.entity_id: e for e in await maison.inventaire.obtenir()}
    e = connus.get(entity_id)
    if not e or maison.masque(e) or maison.sensible(e):
        return None, None
    cmd = _commande(**champs)
    r = await maison._executer(cmd, [e], conversation)
    return r, carte(e, await maison.ha.etat_entite(entity_id))


async def regler_plusieurs(maison: Maison, entity_ids: list[str], conversation: str = "",
                           **champs) -> tuple[ReponseMaison | None, list[dict], list[str]]:
    """Le même geste sur plusieurs appareils d'une carte « groupe » (la couleur pour tous, par exemple).
    Seuls les appareils compatibles sont réglés, en une seule action : un « annule » les remet tous.
    Renvoie (réponse, cartes à jour des appareils réglés, appareils écartés car incompatibles)."""
    cmd = _commande(**champs)
    connus = {e.entity_id: e for e in await maison.inventaire.obtenir()}
    cibles, ecartes = [], []
    for eid in dict.fromkeys(entity_ids):
        e = connus.get(eid)
        if not e or maison.masque(e) or maison.sensible(e):
            continue
        c = carte(e, await maison.ha.etat_entite(eid))
        (cibles if c and compatible(c, champs) else ecartes).append(e)
    if not cibles:
        return None, [], [e.entity_id for e in ecartes]
    r = await maison._executer(cmd, cibles, conversation)
    cartes = [c for e in cibles if (c := carte(e, await maison.ha.etat_entite(e.entity_id)))]
    return r, cartes, [e.entity_id for e in ecartes]
