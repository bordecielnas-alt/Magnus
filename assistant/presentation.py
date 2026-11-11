"""Carte ou texte : d'après la demande et la conversation, l'assistant répond-il par une carte ?

Quand il y a une carte, la bulle ne montre que la carte et la phrase est seulement dite à voix haute.
Les règles, de la plus forte à la plus faible :

1. « montre-moi », « affiche », « fais voir », « en carte » : carte obligatoire si le sujet en a une
   (météo, bourse, agenda, Docker, actus, appareil). Sujet inconnu : carte libre (Claude et le web).
2. Question sur un sujet à carte rapide, faite sans modèle (« quel temps demain ? », « où en est le CAC ? »,
   « j'ai quoi cette semaine ? », « docker ça va ? ») : phrase et carte. Question fermée (« il pleut
   demain ? ») : la carte arrive repliée, la phrase répond.
3. Explication (« dis-moi », « explique », « pourquoi », « raconte ») : texte seul, sans carte.
4. Suite de conversation : après une carte, « et à Lyon ? », « et demain ? », « et le DAX ? » refont
   une carte du même genre.
5. Pour le reste, Claude décide : il peut commencer sa réponse par un marqueur « [carte:meteo:Lyon] »,
   que le serveur retire et remplace par la vraie carte (jamais de chiffres écrits par le modèle).

Les règles 1 à 4 ne passent par aucun modèle : rien à attendre de plus.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from .rapports import RE_ACTUS, RE_AGENDA, RE_DOCKER, RE_METEO, Demande, indice_de, lieu_de, periode_agenda

SUITE_VALIDE_S = 600          # « et à Lyon ? » refait une carte jusqu'à 10 min après la précédente

# --- règle 1 : l'utilisateur veut voir -----------------------------------------------------------------

RE_VOIR = re.compile(
    r"^\s*(?:(?:s'il te pla[iî]t|stp)\s*,?\s*)?(?:(?:tu peux|peux[- ]tu|tu pourrais|pourrais[- ]tu)\s+)?"
    r"(?:me\s+|nous\s+)?(?:montre[rz]?|affiche[rz]?|fais(?:[- ]moi)?\s+voir|je (?:veux|voudrais) voir|"
    r"fais(?:[- ]moi)?\s+une\s+carte)(?:[- ]?(?:moi|nous))?\b", re.I)
RE_VOIR_PARTOUT = re.compile(r"\b(?:en carte|sous forme de carte|sur (?:l'|mon |l’)?[ée]cran)\b", re.I)
RE_ARTICLE = re.compile(r"^(?:(?:un peu|rapidement|vite)\s+)?(?:la |le |les |l'|l’|mes |ma |mon |un |une |des |du |de la |"
                        r"ce qu'il y a (?:sur |dans )?|ce que |o[uù] en est |o[uù] en sont |comment va |comment vont )*",
                        re.I)

# --- règle 3 : une explication, du texte ----------------------------------------------------------------

RE_EXPLIQUE = re.compile(r"^\s*(?:et\s+)?(?:dis[- ]moi|dites[- ]moi|explique|expliquez|pourquoi|raconte|racontez|"
                         r"r[ée]sume|comment (?:ça|ca) (?:marche|fonctionne))\b", re.I)

# --- règle 2 : une question sur un sujet à carte rapide -------------------------------------------------

RE_ACTION = re.compile(r"\b(?:ajoute|ajouter|mets?|mettre|cr[ée]e[rz]?|supprime|annule|d[ée]place|red[ée]marre|"
                       r"arr[êe]te|d[ée]marre|lance|allume|[ée]teins|ferme|ouvre|rappelle|programme|baisse|monte[rz]? "
                       r"(?:le|la|les)|r[èe]gle|pr[ée]viens|alerte|si|quand)\b", re.I)
RE_METEO_Q = re.compile(r"\b(?:m[ée]t[ée]o|quel temps|pr[ée]visions?|pleuvoir|pleut|pluie|neiger?|neige|parapluie|orages?)\b",
                        re.I)
RE_INTERIEUR = re.compile(r"\b(?:salon|chambre|cuisine|maison|int[ée]rieur|chauffage|thermostat|capteurs?|piscine|serre|"
                          r"garage|cave|bureau|salle)\b", re.I)
RE_BOURSE_VUE = re.compile(r"\b(?:bourse|boursiers?|march[ée]s? financiers?|crypto)\b", re.I)
RE_BOURSE_Q = re.compile(r"\b(?:la bourse|les march[ée]s)\b", re.I)
RE_AGENDA_Q = re.compile(r"(?:\bmon agenda\b|\bmon planning\b|\bmon calendrier\b|\bma semaine\b|\bmes rendez[- ]vous\b|"
                         r"\bmes rdv\b|\bj'ai quoi\b|\bj’ai quoi\b|\bqu'est[- ]ce que j'ai\b|\bj'ai (?:des |un )?(?:rendez[- ]vous|rdv)\b)",
                         re.I)
RE_AGENDA_PRECIS = re.compile(r"\b(?:quand|chez|avec|[àa] quelle heure)\b", re.I)
RE_DOCKER_Q = re.compile(r"\b(?:docker|conteneurs?|containers?|serveur|unraid|nas)\b", re.I)
RE_ETAT_Q = re.compile(r"\b(?:[ée]tat|statut|va bien|vont bien|[çc]a va|comment va|comment vont|tournent?|en ligne|"
                       r"en marche|des soucis?|un souci|des probl[eè]mes?|un probl[eè]me)\b", re.I)
RE_FERMEE = re.compile(r"(?:^\s*(?:est[- ]ce qu|va[- ]t[- ]il|y (?:a|aura)[- ]t[- ]il|faut[- ]il|dois[- ]je)|"
                       r"\bil (?:va )?(?:pleu|neig)|\bparapluie\b|\b[çc]a va\b|\bva bien\b|\bvont bien\b|"
                       r"\b(?:il|elle) (?:monte|baisse)\b)", re.I)
RE_OUVERTE = re.compile(r"\b(?:quel|quelle|quels|quelles|quoi|combien|comment|o[uù]|quand|qu'est|qu’est)\b", re.I)

# --- règle 4 : la suite d'une carte ---------------------------------------------------------------------

RE_SUITE = re.compile(r"^\s*(?:et|pareil|m[êe]me chose|idem)\b\s*(?P<reste>.*?)\s*[?.!]*\s*$", re.I)
RE_PREFIXE_SUITE = re.compile(r"^(?:pour|[àa]|sur|avec|du|de la|des|de|d'|le|la|les|l'|l’|en|au|aux)\s+", re.I)
MOTS_TEMPS = {"demain", "apres-demain", "apres demain", "aujourd'hui", "aujourd hui", "ce soir", "ce matin", "cet apres-midi",
              "ce week-end", "ce weekend", "le week-end", "cette semaine", "la semaine", "la semaine prochaine",
              "week-end", "weekend", "semaine", "semaine prochaine", "matin", "soir", "midi", "apres-midi",
              "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche", "maintenant"}
MOTS_PAS_SUJET = {"toi", "moi", "vous", "nous", "lui", "elle", "ca", "alors", "sinon", "merci", "ensuite", "apres", "puis",
                  "donc", "voila", "ok", "oui", "non", "si", "encore"}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _sa(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower().strip()


@dataclass
class Choix:
    """Ce que la demande appelle. `demande` : la carte de rapport à faire ; `voir` : l'utilisateur veut une carte
    (règle 1) ; `texte_seul` : pas de carte du tout (règle 3) ; `repliee` : question fermée ; `regle` : 1 à 4."""
    demande: Demande | None = None
    regle: int = 0
    voir: bool = False
    texte_seul: bool = False
    repliee: bool = False
    reste: str = ""            # règle 1 sans sujet connu : ce qu'il faut montrer (« le salon », « la Fed »)


def veut_voir(message: str) -> bool:
    return bool(RE_VOIR.search(message) or RE_VOIR_PARTOUT.search(message))


def sujet_vu(message: str) -> str:
    """« montre-moi la météo à Lyon » -> « la météo à Lyon » sans l'article : « météo à Lyon »."""
    m = RE_VOIR.sub("", message, count=1)
    m = RE_VOIR_PARTOUT.sub("", m)
    m = RE_ARTICLE.sub("", m.strip(" ,:?!.")).strip(" ,:?!.")
    return m[:120]


def _sujet_carte(texte: str, message: str, rapide: bool) -> Demande | None:
    """Le sujet à carte de `texte` (météo, Docker, agenda, actus, bourse), ou None."""
    if RE_METEO.search(texte) and not RE_INTERIEUR.search(texte):
        return Demande("meteo", lieu_de(RE_VOIR_PARTOUT.sub("", message)))
    if RE_DOCKER.search(texte):
        return Demande("docker")
    if RE_AGENDA.search(texte):
        return Demande("agenda", periode_agenda(message))
    if RE_ACTUS.search(texte) and not indice_de(texte):
        sujet = re.sub(r"^(?:les |des )?(?:actus?|actualit[ée]s?|news|infos?|nouvelles)\b\s*(?:du jour|de la journ[ée]e)?\s*"
                       r"(?:(?:sur|de|du|des|concernant)\s+(?:la |le |les |l')?)?", "", texte, flags=re.I)
        return Demande("actus", sujet.strip(" ?.!"))
    i = indice_de(texte)
    if i or RE_BOURSE_VUE.search(texte):
        return Demande("bourse", i[1] if i else "", rapide=rapide)
    return None


def fermee(message: str) -> bool:
    """« il pleut demain ? », « docker ça va ? » : une question qui attend oui ou non."""
    m = message.strip()
    return not RE_OUVERTE.search(m) and bool(RE_FERMEE.search(m))


def implicite(message: str, sans_action: bool = True) -> Demande | None:
    """Règle 2 : une question sur un sujet dont la carte se fait vite et sans modèle."""
    m = message.strip()
    if len(m) > 160 or (sans_action and RE_ACTION.search(m)):
        return None
    if RE_METEO_Q.search(m) and not RE_INTERIEUR.search(m):
        return Demande("meteo", lieu_de(m))
    i = indice_de(m)
    if i or RE_BOURSE_Q.search(m):
        return Demande("bourse", i[1] if i else "", rapide=True)
    if RE_AGENDA_Q.search(m) and not RE_AGENDA_PRECIS.search(m):
        return Demande("agenda", periode_agenda(m))
    if RE_DOCKER_Q.search(m) and RE_ETAT_Q.search(m) and not re.search(r"\b(?:pourquoi|logs?)\b", m, re.I):
        return Demande("docker")
    return None


def suite(message: str, precedente: Demande) -> Demande | None:
    """Règle 4 : « et à Lyon ? » après une carte météo -> la météo à Lyon ; « et demain ? » -> la même."""
    m = RE_SUITE.match(message)
    if not m or len(message) > 70 or RE_ACTION.search(message) or RE_EXPLIQUE.search(message):
        return None
    reste = m.group("reste")
    while (r := RE_PREFIXE_SUITE.sub("", reste, count=1)) != reste:
        reste = r
    reste = reste.strip(" ,;:?!.")
    cle = _sa(reste)
    if cle in MOTS_PAS_SUJET or (cle.split() or [""])[0] in MOTS_PAS_SUJET:
        return None
    temps = not cle or cle in MOTS_TEMPS or any(cle.startswith(t + " ") or cle.endswith(" " + t) for t in MOTS_TEMPS)
    t = precedente.type
    if t == "meteo":
        lieu = lieu_de(message)
        if not lieu and not temps:
            lieu = reste[:1].upper() + reste[1:]
        return Demande("meteo", lieu or precedente.sujet)
    if t == "bourse":
        i = indice_de(reste)
        return Demande("bourse", i[1] if i else (precedente.sujet if temps else reste), rapide=precedente.rapide)
    if t == "agenda" and (p := periode_agenda(message)):
        return Demande("agenda", p)              # « et le mois prochain ? »
    if t in ("agenda", "docker"):
        return Demande(t) if temps or not cle else None
    if t in ("actus", "libre"):
        return Demande(t, precedente.sujet if temps else reste)
    return None


def choisir(message: str, precedente: Demande | None = None, exclu: bool = False) -> Choix:
    """Applique les règles 1 à 4. `exclu` : la demande est autre chose (règle, rappel, routine) : pas de carte
    implicite. Règle 5 (Claude décide) : voir NOTE_CARTE et lire_marqueur."""
    m = message.strip()
    if veut_voir(m):
        reste = sujet_vu(m)
        d = _sujet_carte(reste or m, m, rapide=False)
        if d is None and not reste and precedente:
            d = precedente
        return Choix(d, 1, voir=True, reste=reste)
    if RE_EXPLIQUE.search(m):
        return Choix(texte_seul=True, regle=3)
    if exclu:
        return Choix()
    if precedente and (d := suite(m, precedente)):
        return Choix(d, 4, repliee=fermee(m))
    if d := implicite(m):
        return Choix(d, 2, repliee=fermee(m))
    return Choix()


# --- la phrase dite : répondre à la question, pas seulement résumer la carte ----------------------------

def _jour_demande(message: str, aujourdhui: date | None = None) -> str | None:
    """Le jour dont parle la question : « Demain », « Aujourd'hui », « +2 » ou un jour de la semaine."""
    s = _sa(message)
    a = aujourdhui or date.today()
    if re.search(r"\bapres[- ]demain\b", s):
        return "+2"
    if re.search(r"\bdemain\b", s):
        return "Demain"
    if re.search(r"\baujourd.?hui\b|\bce soir\b|\bcet apres[- ]midi\b|\bce matin\b", s):
        return "Aujourd'hui"
    for i, j in enumerate(JOURS):
        if re.search(rf"\b{j}\b", s):
            ecart = (i - a.weekday()) % 7
            return "Aujourd'hui" if ecart == 0 else "Demain" if ecart == 1 else j
    return None


def _trouver_jour(jours: list[dict], cle: str | None, cle_nom: str = "jour") -> dict | None:
    if not cle or not jours:
        return None
    if cle == "+2":
        return jours[2] if len(jours) > 2 else None
    for j in jours:
        nom = _sa(j.get(cle_nom, ""))
        if nom == _sa(cle) or (cle in JOURS and nom.startswith(cle[:3])):
            return j
    return None


def phrase(message: str, carte: dict, resume: str, repliee: bool = False, aujourdhui: date | None = None) -> str:
    """La phrase à dire pour cette carte : le jour demandé (« et samedi ? ») plutôt que le résumé général."""
    modele = carte.get("modele")
    cle = _jour_demande(message, aujourdhui)
    if modele == "meteo":
        m = carte.get("meteo") or {}
        j = _trouver_jour(m.get("jours") or [], cle or ("Aujourd'hui" if repliee else None))
        if j:
            lieu = (carte.get("demande") or {}).get("sujet") or ""
            nom = j["jour"] if j["jour"] in ("Aujourd'hui", "Demain") else _jour_long(j["jour"])
            p = f"{nom}{' à ' + lieu if lieu else ''} : {j.get('ciel', '')}"
            if j.get("min") is not None and j.get("max") is not None:
                p += f", {j['min']} à {j['max']} °C"
            p += f", {j['pluie']} % de risque de pluie." if j.get("pluie") else ", pas de pluie prévue."
            return p
    if modele == "agenda" and cle and cle != "+2":
        j = _trouver_jour(carte.get("agenda") or [], cle)
        nom = cle if cle in ("Aujourd'hui", "Demain") else cle.capitalize()
        if j:
            evts = j.get("evenements") or []
            return f"{nom} : " + ", ".join(f"{e['heure'] + ' ' if e.get('heure') else ''}{e['titre']}" for e in evts) + "."
        return f"{nom}, rien à l'agenda."
    return resume


def _jour_long(court: str) -> str:
    """« sam. 14 » -> « Samedi »."""
    for j in JOURS:
        if _sa(court).startswith(j[:3]):
            return j.capitalize()
    return court


# --- règle 5 : Claude décide, par un marqueur en tête de réponse ---------------------------------------

NOTE_CARTE = """Cartes : l'application sait afficher une carte avec les vraies données pour la météo, la bourse, \
l'agenda (7 jours) et Docker (conteneurs). Si ta réponse porte surtout sur l'un de ces sujets et qu'une carte aiderait \
(par exemple « je sors à vélo cet après-midi, ça passe ? » appelle la météo), commence ta réponse EXACTEMENT par un \
marqueur, puis UNE phrase courte à dire à voix haute qui annonce la carte, sans chiffres ni prévision : les vraies \
données seront lues juste après. Marqueurs : [carte:meteo:Ville] (sans ville : [carte:meteo]), [carte:bourse:CAC 40], \
[carte:agenda], [carte:docker]. Aucun marqueur pour une discussion, une explication, ou si une carte n'apporte rien."""

NOTE_VOIR = """L'utilisateur veut VOIR la réponse sous forme de carte. Si un des marqueurs de carte convient, utilise-le. \
Pour un appareil de la maison (lampe, prise, volet…), lis son état avec l'outil d'état : sa carte s'affiche alors \
toute seule, ne dis jamais que tu n'as pas de carte pour un appareil. Sinon, dis en une phrase que tu n'as pas de \
carte pour ça, puis réponds normalement."""

RE_MARQUEUR = re.compile(r"^\s*\[\s*carte\s*:\s*(m[ée]t[ée]o|bourse|agenda|docker)\s*(?::\s*([^\]\n]{0,60}))?\]\s*", re.I)


RE_MARQUEUR_ABIME = re.compile(r"^\s*\[\s*carte\s*:?([^\]\n]{0,80})\]\s*", re.I)


def lire_marqueur(texte: str) -> tuple[Demande | None, str]:
    """« [carte:meteo:Lyon] Voici… » -> (Demande("meteo", "Lyon"), « Voici… »)."""
    m = RE_MARQUEUR.match(texte)
    if not m:
        # marqueur abîmé (« [carte:meteoouai] ») : jamais affiché ni lu ; la carte est refaite si le type se lit
        b = RE_MARQUEUR_ABIME.match(texte)
        if not b:
            return None, texte
        brut = _sa(b.group(1))
        for t in ("meteo", "bourse", "agenda", "docker"):
            if brut.startswith(t):
                reste = texte[b.end():]
                return Demande(t, lieu_de(reste) if t == "meteo" else "", rapide=t == "bourse"), reste
        return None, texte[b.end():]
    t = _sa(m.group(1))
    return Demande(t, (m.group(2) or "").strip(), rapide=t == "bourse"), texte[m.end():]


def marqueur_possible(debut: str) -> bool:
    """Le début d'une réponse en flux peut-il encore devenir un marqueur ? (on attend avant de l'afficher)
    Un marqueur complet est lu par lire_marqueur avant cet appel."""
    brut = debut.lstrip()
    s = brut.lower().replace(" ", "")
    if not s:
        return True
    if len(brut) > 90 or "\n" in brut or "]" in s:
        return False
    return s.startswith("[carte") or "[carte".startswith(s)


def masquer_texte(texte: str, cartes: list[dict] | None) -> bool:
    """Carte affichée : le texte est caché (il reste dit à voix haute). Sauf s'il pose une question ou s'il
    est long (une carte d'appareil sous une règle proposée, une confirmation) : alors on garde les deux."""
    if not cartes:
        return False
    if any(c.get("type") == "rapport" for c in cartes):
        return True
    t = texte.strip()
    return len(t) <= 300 and not t.endswith("?") and "oui ou non" not in t.lower()
