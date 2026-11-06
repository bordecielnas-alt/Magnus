"""Comprendre une commande de maison : action, cible, valeur, couleur.

Règles en français d'abord (instantané) ; sinon un petit modèle extrait la commande en JSON.
Les suites de conversation (« éteins-le », « la cuisine aussi », « plus fort », « remets comme
avant ») sont reconnues ici et complétées par le contexte dans Maison.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..fournisseurs import Fournisseur, FournisseurIndisponible, Message
from .texte import normaliser

# « meme » = même action que la dernière (« la cuisine aussi ») ; « relatif » = plus / moins ;
# « restaurer » = remettre l'état d'avant la dernière action.
ACTIONS = ("allumer", "eteindre", "ouvrir", "fermer", "regler", "etat")
ACTIONS_CONTEXTE = ("meme", "relatif", "restaurer", "inventaire")


@dataclass
class Commande:
    action: str                   # une de ACTIONS ou ACTIONS_CONTEXTE
    cible: str                    # texte qui désigne l'appareil ; vide = « le même qu'avant »
    valeur: float | None = None   # pourcentage, température… ; pour « relatif », +1 ou -1
    tous: bool = False            # « toutes les lumières du salon »
    couleur: str | None = None    # nom de couleur HA (red, blue…) ou None
    kelvin: int | None = None     # température de blanc

    @property
    def vers_contexte(self) -> bool:
        return not self.cible


# --- couleurs ------------------------------------------------------------------

COULEURS = {
    "rouge": "red", "bleu": "blue", "bleue": "blue", "vert": "green", "verte": "green", "jaune": "yellow",
    "orange": "orange", "violet": "purple", "violette": "purple", "rose": "pink", "cyan": "cyan",
    "turquoise": "turquoise", "magenta": "magenta", "indigo": "indigo", "or": "gold", "dore": "gold",
}
BLANCS = {"blanc chaud": 2700, "blanche chaude": 2700, "jaune chaud": 2700, "blanc neutre": 4000,
          "blanc froid": 6000, "blanche froide": 6000, "blanc": 4000, "blanche": 4000}
_NOMS_COULEURS = sorted(list(BLANCS) + list(COULEURS), key=len, reverse=True)
_RE_COULEUR = re.compile(
    r"\s*\b(?:en|au|à|a)\s+(?:couleur\s+|lumi[èe]re\s+)?(" + "|".join(map(re.escape, _NOMS_COULEURS)) + r")\b",
    re.IGNORECASE,
)


def extraire_couleur(reste: str) -> tuple[str, str | None, int | None]:
    """Retire « en rouge » / « en blanc chaud » du texte. Renvoie (reste, couleur HA, kelvin)."""
    m = _RE_COULEUR.search(normaliser_doux(reste))
    if not m:
        return reste, None, None
    nom = normaliser(m.group(1))
    sans = (reste[: m.start()] + reste[m.end():]).strip()
    if nom in BLANCS:
        return sans, None, BLANCS[nom]
    return sans, COULEURS.get(nom), None


def normaliser_doux(t: str) -> str:
    """Minuscules sans accents, même longueur que le texte (positions conservées)."""
    import unicodedata
    return "".join((unicodedata.normalize("NFD", c)[0] if c.isalpha() else c) for c in t).lower()


# --- verbes ----------------------------------------------------------------------

_POLI = r"(?:(?:peux[- ]tu|tu peux|pourrais[- ]tu|merci de|veux[- ]tu|est[- ]ce que tu peux)\s+)?"
# Terminaisons de verbe à l'impératif / infinitif / présent : « Fermeture » n'est pas « ferme »
_T = r"(?:e|es|ez|er|ons|ent)?"
_VERBES = [
    ("allumer", rf"allum{_T}|activ{_T}|d[ée]marr{_T}|lanc{_T}|mets? en marche"),
    ("eteindre", rf"[ée]teins?|[ée]teindre|[ée]teign(?:ez|ons|ent)|coup{_T}|d[ée]sactiv{_T}|arr[êeé]t{_T}|stopp?{_T}"),
    ("ouvrir", rf"ouvr(?:e|es|ez|ir|ons|ent)?|l[èe]v{_T} (?:le|les) (?:volets?|stores?)|remont{_T}|mont{_T} (?:le|les) (?:volets?|stores?)"),
    ("fermer", rf"ferm{_T}|baiss{_T} (?:le|les) (?:volets?|stores?)|descend(?:s|ez|re|ons|ent)?"),
    ("regler", rf"r[éeè]gl{_T}|(?:re)?mets?|(?:re)?mettre|mont{_T}|baiss{_T}|augment{_T}|diminu{_T}|(?:re)?pass{_T}"),
]
_RE_ORDRE = [(a, re.compile(rf"^\s*{_POLI}(?:{v})\b\s*(.*)$", re.IGNORECASE)) for a, v in _VERBES]
_RE_VALEUR = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\w.,])\s*(%|pour ?cent|degr[ée]s?|°)?", re.IGNORECASE)
_RE_ETAT = re.compile(
    r"^\s*(?:est[- ]ce que\s+)?(.+?)\s+(?:est|sont)(?:[- ](?:elle|il|elles|ils))?\s+"
    r"(allum\w*|[ée]teint\w*|ouvert\w*|ferm\w*|en marche|arr[êeé]t\w*)\s*\??\s*$"
    r"|^\s*(?:quel est l.[ée]tat|[ée]tat|quel est le r[ée]glage)\s+(?:de |du |des |d.)?(.+?)\s*\??\s*$"
    r"|^\s*(?:[àa] combien (?:est|sont)|quelle est la (?:luminosit[ée]|temp[ée]rature|position|couleur) (?:de |du |des |d.))\s*(.+?)"
    r"(?:\s+en\s+(?:luminosit[ée]|temp[ée]rature|position))?\s*\??\s*$",
    re.IGNORECASE,
)
_RE_TOUS = re.compile(r"\b(tou(?:te)?s?)\b", re.IGNORECASE)
_RE_PRONOM = re.compile(r"^[-\s]*(?:le|la|les|l'|l’|lui|leur|ça|ca|cela)?[\s.!?]*$", re.IGNORECASE)

# Suites de conversation
_RE_AUSSI = re.compile(
    r"^\s*(?:et\s+)?(?:(?:pareil|idem|m[êe]me chose)\s+(?:pour|dans|avec)\s+(?P<a>.+?)"
    r"|(?P<b>.+?)\s+aussi)\s*[.!?]*$", re.IGNORECASE)
_RE_RELATIF = re.compile(
    r"^\s*(?:(?:un\s+(?:peu|poil)|encore|beaucoup)\s+)?(?P<sens>plus|moins)"
    r"(?:\s*(?:fort|lumineux|lumineuse|clair|claire|haut|chaud|vite|de lumi[èe]re))?\s*[.!?]*$"
    r"|^\s*(?P<v>augmente|monte|baisse|diminue)\s*(?:-?(?:le|la|les))?(?:\s+(?:un peu|encore))?\s*[.!?]*$",
    re.IGNORECASE)
_RE_RESTAURER = re.compile(
    r"^\s*(?:(?:re)?mets?|r[ée]tablis|reviens|remet)?\s*(?:-?(?:le|la|les)\s*)?(?:(?:tout|ça|ca)\s+)?"
    r"(?:comme\s+(?:avant|il\s+[ée]tait|elle\s+[ée]tait|c.[ée]tait|tout\s+[àa]\s+l.heure)|[àa]\s+l.[ée]tat\s+pr[ée]c[ée]dent)"
    r"|^\s*annule(?:\s+(?:[çc]a|la derni[èe]re action|ce que tu viens de faire))?\s*[.!?]*$"
    r"|^\s*(?:reviens|retourne|retour|on revient)\s+(?:en\s+)?arri[èe]re\s*[.!?]*$"
    r"|^\s*(?:d[ée]fais|undo)(?:\s+[çc]a)?\s*[.!?]*$"
    r"|^\s*(?:reviens|remets?|r[ée]tablis)(?:\s*-?(?:le|la|les|tout|[çc]a))?\s*[.!?]*$",
    re.IGNORECASE)

# Fautes de frappe sur le verbe : forme « canonique » (sans accents, lettres doublées réduites)
# -> verbe correct. « etteins », « alume », « éttein » sont reconnus.
_VERBES_CANONIQUES = [
    ("alum", "allume"), ("eteign", "éteins"), ("etein", "éteins"), ("etei", "éteins"),
    ("ouvr", "ouvre"), ("ferm", "ferme"), ("regl", "règle"), ("coup", "coupe"),
    ("desactiv", "désactive"), ("activ", "active"), ("demar", "démarre"), ("aret", "arrête"),
]
_TERMINAISONS = {"", "e", "es", "ez", "er", "s", "ns", "nt", "ent", "r", "re", "ons"}


def _canon(mot: str) -> str:
    return re.sub(r"(.)\1+", r"\1", normaliser(mot))


def corriger_verbe(texte: str) -> str:
    """Remplace un premier mot mal tapé par le verbe reconnu ; sinon renvoie le texte tel quel."""
    m = re.match(r"\s*([^\s-]+)(.*)$", texte, re.DOTALL)
    if not m:
        return texte
    c = _canon(m.group(1))
    for prefixe, verbe in _VERBES_CANONIQUES:
        # seulement des terminaisons de verbe : « activité » ne devient pas « active »
        if c.startswith(prefixe) and c[len(prefixe):] in _TERMINAISONS:
            return verbe + m.group(2)
    return texte


_RE_INVENTAIRE = re.compile(
    r"^\s*(?:(?:fais|donne|montre|affiche|dis)(?:-?moi)?\s*(?:l.inventaire|la liste)|liste|inventaire)\s+(?:de |des |du |d.)?(?P<quoi>[^?]*?)"
    r"(?:\s+(?:du|de la|de l.|des|dans|au|à la|a la)\s+(?P<ou>[^?]+?))?\s*[.!?]*$"
    r"|^\s*quels? (?:sont les |)(?P<quoi2>appareils|lumi[èe]res|lampes|prises|volets|[ée]quipements)"
    r"(?:\s+(?:as-tu|connais-tu|tu connais|tu vois|y a-t-il|il y a|sont allum[ée]e?s|vois-tu))?"
    r"(?:\s+(?:du|de la|de l.|des|dans|au|à la|a la)\s+(?P<ou2>[^?]+?))?\s*[.!?]*$",
    re.IGNORECASE)


def par_regles(texte: str, avec_contexte: bool = False) -> Commande | None:
    """Commande reconnue par les règles. `avec_contexte` : accepte aussi les suites de conversation
    (« éteins-le », « la cuisine aussi », « plus fort », « remets comme avant »)."""
    t = corriger_verbe(texte.strip().rstrip("!."))
    m = _RE_INVENTAIRE.match(t)
    if m and (m.group("quoi") is not None or m.group("quoi2")):
        quoi = (m.group("quoi") or m.group("quoi2") or "").strip()
        ou = (m.group("ou") or m.group("ou2") or "").strip()
        return Commande("inventaire", " ".join(x for x in (quoi, ou) if x) or "appareils")
    if _RE_RESTAURER.match(t):
        return Commande("restaurer", "")   # même sans contexte : la maison répondra qu'il n'y a rien à annuler
    if avec_contexte:
        # « remets la lampe bureau » (sans couleur ni valeur) = la remettre comme avant
        if re.match(r"^\s*remets?\s+(?!.*\b(?:en|au)\s)(?!.*\s[àa]\s)(?!.*\d)\S.*$", t, re.IGNORECASE):
            return Commande("restaurer", "")
        m = _RE_RELATIF.match(t)
        if m:
            sens = (m.group("sens") or "").lower()
            haut = sens == "plus" or (m.group("v") or "").lower() in ("augmente", "monte")
            return Commande("relatif", "", 1.0 if haut else -1.0)
    m = _RE_ETAT.match(t)
    if m:
        cible = (m.group(1) or m.group(3) or m.group(4) or "").strip()
        return Commande("etat", cible) if cible else None
    for action, r in _RE_ORDRE:
        m = r.match(t)
        if not m:
            continue
        reste = m.group(1).strip()
        reste, couleur, kelvin = extraire_couleur(reste)
        valeur = None
        v = _RE_VALEUR.search(reste)
        if v:
            valeur = float(v.group(1).replace(",", "."))
            reste = (reste[: v.start()] + reste[v.end():]).strip()
            reste = re.sub(r"\s+(?:à|a|au|sur|de)\s*$", "", reste).strip()
        if action == "regler" and valeur is None and not couleur and not kelvin:
            continue  # « mets la musique » sans valeur : pas un réglage
        if (couleur or kelvin) and action == "regler":
            action = "allumer"  # « mets le globe en rouge » = allumer en rouge
        if _RE_PRONOM.match(reste):
            if not avec_contexte:
                return None
            reste = ""
        tous = bool(_RE_TOUS.search(reste))
        return Commande(action, reste, valeur, tous, couleur, kelvin)
    if avec_contexte:
        m = _RE_AUSSI.match(t)
        if m:
            cible = (m.group("a") or m.group("b") or "").strip()
            if cible:
                return Commande("meme", cible, tous=bool(_RE_TOUS.search(cible)))
    return None


CONSIGNE_EXTRACTION = (
    "Tu extrais une commande domotique d'une phrase en français. Réponds UNIQUEMENT par un objet JSON : "
    '{"action": "allumer|eteindre|ouvrir|fermer|regler|etat|aucune", "cible": "appareil ou pièce visé", '
    '"valeur": nombre ou null, "couleur": "nom de couleur en français" ou null, "tous": true|false}. '
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
    # le petit modèle invente parfois une cible absente de la phrase (« plus fort » -> « Lampe Cristal »)
    from .texte import mots
    if not (mots(cible) & mots(texte)):
        return None
    valeur = d.get("valeur")
    try:
        valeur = float(valeur) if valeur is not None else None
    except (TypeError, ValueError):
        valeur = None
    couleur = kelvin = None
    if d.get("couleur"):
        nom = normaliser(str(d["couleur"]))
        couleur, kelvin = COULEURS.get(nom), BLANCS.get(nom)
        if (couleur or kelvin) and action == "regler":
            action = "allumer"
    return Commande(action, cible, valeur, bool(d.get("tous")), couleur, kelvin)
