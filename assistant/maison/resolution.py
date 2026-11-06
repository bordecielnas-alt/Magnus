"""Trouver les entités visées par une cible en français (« la lumière du salon »)."""

from __future__ import annotations

from dataclasses import dataclass

from .inventaire import Entite
from .texte import mots, normaliser

# mot de la phrase -> domaines Home Assistant qu'il évoque
INDICES_DOMAINE: dict[str, set[str]] = {
    **{m: {"light", "switch"} for m in ("lumiere", "lampe", "plafonnier", "spot", "ampoule", "eclairage",
                                         "lustre", "applique", "led", "ruban", "guirlande", "veilleuse")},
    **{m: {"switch"} for m in ("prise", "interrupteur")},
    **{m: {"cover"} for m in ("volet", "store", "rideau", "portail", "porte", "garage", "velux", "fenetre")},
    **{m: {"climate"} for m in ("chauffage", "radiateur", "thermostat", "clim", "climatisation", "temperature")},
    **{m: {"media_player"} for m in ("tele", "tv", "television", "enceinte", "musique", "son", "radio",
                                      "chromecast", "sonos", "volume")},
    **{m: {"fan"} for m in ("ventilateur", "ventilo")},
}

# actions possibles par domaine
ACTIONS_DOMAINE: dict[str, set[str]] = {
    "light": {"allumer", "eteindre", "regler", "etat"},
    "switch": {"allumer", "eteindre", "etat"},
    "fan": {"allumer", "eteindre", "regler", "etat"},
    "cover": {"ouvrir", "fermer", "regler", "etat", "allumer", "eteindre"},
    "climate": {"allumer", "eteindre", "regler", "etat"},
    "media_player": {"allumer", "eteindre", "regler", "etat"},
}

SEUIL = 2.0      # score minimal pour retenir une entité
ECART = 0.6      # en dessous de cet écart avec le meilleur, c'est ambigu


@dataclass
class Resultat:
    entites: list[Entite]         # entités retenues (une, ou plusieurs si « toutes »)
    ambigues: list[Entite]        # candidats si la cible est ambiguë
    raison: str = ""


MOTS_LUMIERE = {m for m, d in INDICES_DOMAINE.items() if d == {"light", "switch"}}


def score(e: Entite, cible_mots: set[str], domaines_evoques: set[str]) -> float:
    s = 0.0
    if domaines_evoques:
        if e.domaine not in domaines_evoques:
            return 0.0
        # « lumière » n'évoque une prise que si la prise porte un nom de lumière (« Prise lampe »)
        if (e.domaine == "switch" and cible_mots & MOTS_LUMIERE and not cible_mots & {"prise", "interrupteur"}
                and not e.mots_nom() & MOTS_LUMIERE):
            return 0.0
        s += 1.0
    piece = e.mots_piece()
    if piece and piece & cible_mots:
        s += 2.0 * len(piece & cible_mots) / len(piece)
    nom = e.mots_nom() - piece
    specifiques = cible_mots - set(INDICES_DOMAINE)
    # Un mot générique (« lumière ») compte peu s'il y a des mots précis (« salon ») ;
    # s'il n'y en a pas (« le plafonnier »), le mot générique sert de nom.
    for w in nom & cible_mots:
        s += 0.75 if specifiques and w in INDICES_DOMAINE else 2.5 / max(1, len(specifiques) or len(cible_mots))
    # le nom contient la pièce (« Lampe salon ») sans que la pièce soit renseignée
    if not piece and nom & specifiques:
        s += 0.5
    return s


def resoudre(cible: str, action: str, entites: list[Entite], tous: bool = False,
             alias_appris: dict[str, list[str]] | None = None) -> Resultat:
    cle = normaliser(cible)
    if alias_appris and cle in alias_appris:
        ids = set(alias_appris[cle])
        retenues = [e for e in entites if e.entity_id in ids]
        if retenues:
            return Resultat(retenues, [], "alias appris")
    cible_mots = mots(cible) - {"tou", "tout", "toute"}
    evoques: set[str] = set()
    for m in cible_mots:
        evoques |= INDICES_DOMAINE.get(m, set())
    compatibles = [e for e in entites if action in ACTIONS_DOMAINE.get(e.domaine, set())]
    notes = sorted(((score(e, cible_mots, evoques), e) for e in compatibles), key=lambda x: -x[0])
    notes = [(s, e) for s, e in notes if s >= SEUIL]
    if not notes:
        return Resultat([], [], "aucun appareil ne correspond")
    meilleur = notes[0][0]
    proches = [e for s, e in notes if meilleur - s < ECART]
    if tous:
        return Resultat(proches, [], "tous")
    if len(proches) == 1:
        return Resultat(proches, [], "correspondance")
    # une lumière « principale » (domaine light) l'emporte sur une prise de même score
    lumieres = [e for e in proches if e.domaine == "light"]
    if len(lumieres) == 1 and evoques == {"light", "switch"}:
        return Resultat(lumieres, [], "correspondance")
    return Resultat([], proches[:5], "ambigu")
