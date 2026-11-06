"""Comprendre une commande de maison : action, cible, valeur.

Règles en français d'abord (instantané) ; sinon un petit modèle extrait la commande en JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..fournisseurs import Fournisseur, FournisseurIndisponible, Message
from .texte import normaliser

ACTIONS = ("allumer", "eteindre", "ouvrir", "fermer", "regler", "etat")


@dataclass
class Commande:
    action: str           # une de ACTIONS
    cible: str            # texte qui désigne l'appareil (« la lumière du salon »)
    valeur: float | None = None   # pourcentage, température…
    tous: bool = False    # « toutes les lumières du salon »


_POLI = r"(?:(?:peux[- ]tu|tu peux|pourrais[- ]tu|merci de|veux[- ]tu|est[- ]ce que tu peux)\s+)?"
_VERBES = [
    ("allumer", r"allum\w*|active\w*|d[ée]marr\w*|lanc\w*|mets? en marche"),
    ("eteindre", r"[ée]tei\w*|[ée]teign\w*|coup\w*|d[ée]sactiv\w*|arr[êeé]t\w*|stopp?\w*"),
    ("ouvrir", r"ouvr\w*|l[èe]v\w* (?:le|les) (?:volets?|stores?)|remont\w*|mont\w* (?:le|les) (?:volets?|stores?)"),
    ("fermer", r"ferm\w*|baiss\w* (?:le|les) (?:volets?|stores?)|descend\w*"),
    ("regler", r"r[éeè]gl\w*|mets?|monte\w*|baiss\w*|augment\w*|diminu\w*|passe\w*"),
]
_RE_ORDRE = [(a, re.compile(rf"^\s*{_POLI}(?:{v})\b\s*(.*)$", re.IGNORECASE)) for a, v in _VERBES]
_RE_VALEUR = re.compile(r"(\d+(?:[.,]\d+)?)\s*(%|pour ?cent|degr[ée]s?|°)?", re.IGNORECASE)
_RE_ETAT = re.compile(
    r"^\s*(?:est[- ]ce que\s+)?(.+?)\s+(?:est|sont)(?:[- ](?:elle|il|elles|ils))?\s+"
    r"(allum\w*|[ée]teint\w*|ouvert\w*|ferm\w*|en marche|arr[êeé]t\w*)\s*\??\s*$"
    r"|^\s*(?:quel est l.[ée]tat|[ée]tat)\s+(?:de |du |des |d.)?(.+?)\s*\??\s*$",
    re.IGNORECASE,
)
_RE_TOUS = re.compile(r"\b(tou(?:te)?s?)\b", re.IGNORECASE)


def par_regles(texte: str) -> Commande | None:
    t = texte.strip().rstrip("!.")
    m = _RE_ETAT.match(t)
    if m:
        cible = (m.group(1) or m.group(3) or "").strip()
        return Commande("etat", cible) if cible else None
    for action, r in _RE_ORDRE:
        m = r.match(t)
        if not m:
            continue
        reste = m.group(1).strip()
        valeur = None
        v = _RE_VALEUR.search(reste)
        if v:
            valeur = float(v.group(1).replace(",", "."))
            reste = (reste[: v.start()] + reste[v.end():]).strip()
            reste = re.sub(r"\s+(?:à|a|au|sur|de)\s*$", "", reste).strip()
        if action == "regler" and valeur is None:
            continue  # « mets la musique » sans valeur : pas un réglage
        if not reste:
            return None
        tous = bool(_RE_TOUS.search(reste))
        return Commande(action, reste, valeur, tous)
    return None


CONSIGNE_EXTRACTION = (
    "Tu extrais une commande domotique d'une phrase en français. Réponds UNIQUEMENT par un objet JSON : "
    '{"action": "allumer|eteindre|ouvrir|fermer|regler|etat|aucune", "cible": "appareil ou pièce visé", '
    '"valeur": nombre ou null, "tous": true|false}. '
    'Si la phrase n\'est pas une commande ou une question sur un appareil de la maison, action = "aucune".'
)


async def par_modele(texte: str, modele: Fournisseur) -> Commande | None:
    msgs = [Message("system", CONSIGNE_EXTRACTION), Message("user", texte)]
    try:
        rep = await modele.repondre(msgs, options={"temperature": 0, "max_tokens": 80})
    except FournisseurIndisponible:
        return None
    m = re.search(r"\{.*\}", rep, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    action = normaliser(str(d.get("action", ""))).replace(" ", "")
    cible = str(d.get("cible") or "").strip()
    if action not in ACTIONS or not cible:
        return None
    valeur = d.get("valeur")
    try:
        valeur = float(valeur) if valeur is not None else None
    except (TypeError, ValueError):
        valeur = None
    return Commande(action, cible, valeur, bool(d.get("tous")))
