"""Point d'entrée HTTP de l'orchestrateur."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import Config, charger, charger_secrets, chemin_alias, dossier_etat, enregistrer_reglages
from .fournisseurs import FournisseurIndisponible, Message, Registre, Reponse
from .home_assistant import HomeAssistant
from .maison import CATEGORIES_DROITS, Maison, domaines_interdits, interdits, masques, piece_preferee
from .maison.cartes import MAX_GROUPE, cartes_pour, regler as regler_carte, regler_plusieurs
from .maison.historique import Historique
from .maison.historique import demande as demande_en_cours
from .maison.alias import AliasAppris
from .routeur import COMPLEXE, SIMPLE, Decision, Routeur
from . import reglages as R
from .reveil import Reveil
from .agenda import JOURS, Agenda, decrire
from .audit import Audit
from .rappels import Rappels
from .telegram import Telegram
from .habitudes import Routines, automatisation, detecter
from .regles import Regles, decrire as decrire_regle, normaliser as normaliser_regle
from .memoire import Memoire
from .fil import Fil
from .proactif import Annonces, MisesAJourIgnorees
from .rapports import CONSIGNE_JSON, MODELES as MODELES_RAPPORT, Demande, RapportIndisponible, Rapports
from .rapports import detecter as detecter_rapport, sujet_de as sujet_rapport
from .presentation import (NOTE_CARTE, NOTE_VOIR, RE_MARQUEUR, SUITE_VALIDE_S, Choix, choisir as choisir_carte,
                           lire_marqueur, marqueur_possible, masquer_texte, phrase as phrase_carte)
from .docker_diag import ETATS_FR, Docker, UnraidDocker, causes_probables, normaliser_nom, statut_lisible
from .locuteur import Locuteurs
from .diagnostic import Diagnostic, GestionnaireJournal
from . import droits as D
from .securite import COMPTE_MACHINE, NOM_COOKIE, AccesRefuse, Securite, est_locale
from .voix import Voix, sans_emoji

STATIC = Path(__file__).resolve().parent / "static"
journal = logging.getLogger("uvicorn.error")  # s'affiche dans la console de l'assistant
HISTORIQUE_MAX = 20
ROLE_AUTO = "auto"
NOTE_HA_ECHEC = (
    "Home Assistant n'a pas pu traiter ce message ({code}). Si le message concerne un appareil, "
    "réponds seulement « Pas compris, rien n'a été fait. » Sinon, réponds normalement."
)
# Style imposé à tous les modèles (s'ajoute à la consigne de personnalité)
NOTE_STYLE = (
    "Style : une ou deux phrases courtes, comme à l'oral. Aucun émoji ni smiley, aucune liste ni mise en forme "
    "sauf si on la demande, pas de formule de politesse, pas de précision non demandée. "
    "Ne parle jamais de tes limites ni de ton fonctionnement sauf si on te le demande."
)


class Tour(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class DemandeChat(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    historique: list[Tour] = Field(default_factory=list)
    role: str = ROLE_AUTO   # "auto" = le routeur choisit
    flux: bool = False
    conversation: str = Field(default="", max_length=64)   # identifiant choisi par la page
    source: Literal["voix", "texte", "page"] = "texte"       # pour l'historique des actions
    appareil: str = Field(default="", max_length=40)         # page qui pose la question (fil partagé)


class Action(BaseModel):
    """Action demandée par l'outil « agir » du serveur MCP maison."""
    entity_ids: list[str] = Field(min_length=1, max_length=30)
    action: Literal["allumer", "eteindre", "ouvrir", "fermer", "regler"]
    valeur: float | None = None
    couleur: str | None = None
    conversation: str = ""


class Conv(BaseModel):
    conversation: str = ""


class DemandeRapport(BaseModel):
    type: str = Field(min_length=3, max_length=20)
    sujet: str = Field(default="", max_length=120)


class ReglageCarte(BaseModel):
    """Geste sur une carte du chat : un seul réglage à la fois (interrupteur, curseur ou couleur)."""
    entity_id: str = Field(min_length=3, max_length=120)
    allume: bool | None = None
    intensite: float | None = Field(default=None, ge=0, le=100)
    kelvin: int | None = Field(default=None, ge=1000, le=10000)
    couleur: str | None = Field(default=None, max_length=30)
    position: float | None = Field(default=None, ge=0, le=100)
    teinte: float | None = Field(default=None, ge=0, le=360)          # cercle chromatique (degrés)
    saturation: float | None = Field(default=None, ge=0, le=100)
    conversation: str = Field(default="", max_length=64)


class ReglageGroupe(ReglageCarte):
    """Le même geste sur plusieurs appareils d'une carte « groupe » : seuls les compatibles sont réglés."""
    entity_id: str = ""
    entity_ids: list[str] = Field(min_length=1, max_length=MAX_GROUPE)


class ReglagesVoix(BaseModel):
    """Réglages modifiables depuis la page (⚙) : écoute continue et voix."""
    fenetre_s: float | None = Field(default=None, ge=0, le=3600)
    toujours_a_l_ecoute: bool | None = None
    voix: str | None = Field(default=None, max_length=120)


class Theme(BaseModel):
    """Thème de la page : l'orbe (mot de réveil = le nom) ou l'œil rouge (mot de réveil « hal »)."""
    theme: Literal["orbe", "hal"]


class Connexion(BaseModel):
    identifiant: str = Field(default="", max_length=60)
    mot_de_passe: str = Field(min_length=1, max_length=200)


class Etape(BaseModel):
    jeton: str = Field(min_length=10, max_length=400)
    code: str = Field(default="", max_length=20)


class Invitation(BaseModel):
    invitation: str = Field(min_length=10, max_length=200)
    mot_de_passe: str = Field(min_length=10, max_length=200)


class NouveauMotDePasse(BaseModel):
    nouveau: str = Field(min_length=10, max_length=200)
    actuel: str | None = Field(default=None, max_length=200)


COOKIE_APPAREIL = "assistant_appareil"     # « partage » : tablette commune, on en dit moins


class AppareilPartage(BaseModel):
    partage: bool


ETATS_FR_COURTS = {"on": "allumé", "off": "éteint", "open": "ouvert", "closed": "fermé",
                   "unavailable": "injoignable", "home": "à la maison", "not_home": "absent"}
RE_BRIEFING = re.compile(r"^\s*(fais[- ]moi |donne[- ]moi |lance |le )?(le |mon )?(briefing|r[ée]sum[ée] du (jour|matin))\b", re.I)
RE_OUI = re.compile(r"^\s*(oui|ouais|d.accord|ok|vas-y|retiens(-le)?|c.est (bon|ça)|exact)\b[\s!.]*\w{0,10}[\s!.]*$", re.I)
# Réponse à une question de l'assistant, même dans une phrase (« oui, je veux que tu éteignes adminer »)
RE_OUI_DEBUT = re.compile(r"^\s*(?:\w+,\s*)?(oui|ouais|d.accord|ok|vas-y|exact|c.est (bon|ça))\b(?!.*\b(non|pas)\b)", re.I)
RE_NON_DEBUT = re.compile(r"^\s*(?:\w+,\s*)?(non|nan|surtout pas|pas la peine|laisse tomber|annule)\b", re.I)
RE_NON = re.compile(r"^\s*(non|nan|oublie(-le)?|pas la peine|laisse tomber)\b[\s!.]*\w{0,10}[\s!.]*$", re.I)
RE_RETIENS = re.compile(r"^\s*(?:\w+,\s*)?(?:retien[st]?|retenir|souviens-toi|souviens toi|rappelle-toi|n.oublie pas|"
                        r"note|m[ée]morise|garde en m[ée]moire|enregistre)"
                        r"(?: bien)?(?: que| qu.| de| :)?\s+(?P<fait>.{3,400})$", re.I)
RE_CORRECTION = re.compile(r"^\s*non\s*[,!.]?\s*(?:en fait|plut[ôo]t|je pr[ée]f[èe]re|je voulais dire|"
                           r"ce n.est pas [çc]a|la prochaine fois|d[ée]sormais|[àa] l.avenir)\s*[,:]?\s*(?P<fait>.{3,300})$", re.I)
RE_CREER_ROUTINE = re.compile(r"\b(cr[ée]+[ers]?|fais|faire|ajoute[rz]?|enregistre[rz]?|programme[rz]?|"
                              r"modifie[rz]?|change[rz]?)\b.{0,20}\broutines?\b", re.I)
_VERBES_REGLE = (r"(?:allume[rz]?|[ée]tein[st]|[ée]teindre|ouvre|ferme|r[èe]gle|mets|monte|baisse|coupe|lance|"
                 r"pr[ée]viens(?:[- ]moi)?|avertis(?:[- ]moi)?|dis(?:[- ]moi)?|annonce|signale|envoie)")
RE_CREER_REGLE = re.compile(
    r"^\s*(?:\w+,\s*)?(?:"
    r"(?:cr[ée]+[ers]?|ajoute[rz]?|fais|programme[rz]?|enregistre[rz]?)\b.{0,15}\b(?:une|la|les|des|cette|nouvelle)\s+r[èe]gles?\b"
    r"|(?:si(?!\s+(?:tu\s+(?:peux|veux|le\s+veux)|possible|besoin|[çc]a\s+te|vous))|s.il(?!\s+(?:te|vous)\s+pla)|s.ils|quand|lorsque?|lorsqu.|d[èe]s que?|d[èe]s qu.|(?:[àa] )?chaque fois que?|(?:[àa] )?chaque fois qu.)"
    r"\b.{4,200}?(?:,|\balors\b)\s*(?:alors\s+)?(?:tu\s+)?" + _VERBES_REGLE + r"\b"
    r"|" + _VERBES_REGLE + r"\b.{2,150}?\b(?:d[èe]s que?|d[èe]s qu.|(?:[àa] )?chaque fois que?|lorsque?|lorsqu.|quand)\b"
    r"|(?:pr[ée]viens|avertis)[- ]moi\b.{0,40}?\bsi\b)", re.I)
RE_GERER_REGLE = re.compile(r"\b(?:la|les|une|des|mes|ma|ta|tes|cette|ces|nouvelles?|quelles?)\s+r[èe]gles?\b", re.I)
NOTE_REGLES = (
    "Règles « si… alors… » : quand la personne veut que quelque chose se fasse tout seul quand une condition arrive "
    "(présence, seuil d'un capteur, état d'un appareil, éventuellement dans un créneau horaire), cherche les capteurs "
    "avec lister_capteurs et les appareils avec lister_appareils, puis appelle proposer_regle. Rien n'est enregistré "
    "tant que la personne n'a pas dit oui : termine ta réponse par la phrase « a_dire » renvoyée, mot pour mot, sans "
    "rien ajouter. Conditions possibles : présence (quelqu'un arrive, tout le monde part, une personne arrive ou part), "
    "seuil (passe sous / au-dessus d'une valeur), état (devient X, ou reste X plus de N minutes). Si la condition n'entre "
    "pas dans ces cas (météo, heure seule, coucher du soleil…), dis-le simplement. Pour mettre une règle en pause ou la "
    "reprendre : lister_regles puis changer_regle. Tu ne supprimes pas de règle : c'est dans Réglages > Règles."
)
RE_SUJET_MEMOIRE = re.compile(r"\b(retien[st]?|retenir|souviens|rappelle-toi|oublie|m[ée]moire|souvenir|corrige|"
                              r"en fait|plus que|n.est plus|maintenant j|j.ai (vendu|achet[ée]|perdu|chang[ée])|"
                              r"je n.ai plus|d[ée]sormais)\b", re.I)
NOTE_MEMOIRE = (
    "Mémoire : tu ne retiens rien toi-même et ne dis jamais « noté » ou « je m'en souviendrai ». Les souvenirs "
    "validés sont ci-dessus. Si on te donne une information à garder, ou une correction d'un souvenir (ex. « j'en ai "
    "vendu deux »), utilise proposer_souvenir si tu l'as (avec remplace pour une mise à jour) et demande « Je retiens "
    "« … » ? » ; sinon, dis qu'il suffit de dire « retiens que … »."
)
RE_PRIVE = re.compile(r"\b(pour moi( seulement| seul)?|c.est priv[ée]|entre nous|perso(nnel)?)\b", re.I)
RE_PERSO = re.compile(r"\b(je|j.|mon|ma|mes|moi)\b", re.I)
RE_FAIT_PERSO = re.compile(r"\b(je m.appelle|ma (femme|fille|m[èe]re|s[œo]eur|compagne|copine)|mon (mari|fils|p[èe]re|fr[èe]re|"
                           r"compagnon|copain|chien|chat)|je (suis allergique|d[ée]teste|pr[ée]f[èe]re|n.aime pas|"
                           r"travaille|bosse)|j.(adore|aime pas|aime bien|habite)|mon anniversaire)\b", re.I)
RE_AGENDA = re.compile(r"\b(agenda|calendrier|rendez.vous|rdv|r[ée]union|rappelle-moi|rappel|"
                       r"qu.est-ce que j.ai|ai-je quelque chose|programme de (la journ[ée]e|demain|la semaine)|"
                       r"note (dans|sur) l.agenda|anniversaire)\b", re.I)
NOTE_OUTILS_AGENDA = (
    "Tu as aussi lire_agenda et ajouter_evenement (agenda familial et/ou agenda personnel de la personne ; un "
    "nouvel événement va dans son agenda personnel s'il existe, sauf si elle parle de l'agenda de la famille). "
    "Nous sommes le {date}. Pour « rappelle-moi "
    "de … demain à 9 h », ajoute un événement à cette heure. Résume l'agenda en phrases courtes, sans liste."
)
RE_RAPPEL = re.compile(r"\b(rappel|rappelle[sz]?(-| )moi|me rappelle[rs]?|m.e?rappelle|pr[ée]viens[- ]moi)\b", re.I)
NOTE_RAPPELS = (
    "Rappels : « rappelle-moi… », « mets un rappel… » = creer_rappel (dans le chat, à l'heure dite). Un rendez-vous "
    "(médecin, réunion, anniversaire…) va dans l'agenda avec ajouter_evenement. Si tu ne sais pas lequel, demande. "
    "Nous sommes le {date}."
)
RE_SUITE_AGENDA = re.compile(r"\b(supprime|efface|annule|enl[èe]ve|retire|d[ée]place|d[ée]cale|change|modifie|"
                             r"celui|celle|ce|cet|le premier|le deuxi[èe]me|oui|non)\b", re.I)
RE_DOCKER = re.compile(r"\b(dockers?\w*|conteneurs?|containers?|unraid|serveur|plex|nginx|npm|logs?|journaux|disques?( durs?)?|"
                       r"hdd|array|parit[ée]|smart|stockage|espace (libre|disque)|"
                       r"plant[ée]s?|crash|red[ée]marr\w*|ne (r[ée]pond|marche|fonctionne) plus)\b", re.I)
# Désigne sans ambiguïté le serveur ou un conteneur : passe avant les commandes de maison
# (« allume le docker MeTube », « quel est l'état des dockers », « Dockerplex » mal transcrit)
RE_DOCKER_FORT = re.compile(r"\b(dockers?\w*|conteneurs?|containers?|unraid|serveur)\b", re.I)
RE_TELEGRAM = re.compile(r"\bt[ée]l[ée]gram\b", re.I)
NOTE_TELEGRAM = ("Tu as aussi envoyer_telegram : envoie un message court sur le Telegram de la maison (alertes). "
                 "Seulement quand on te le demande ; dis ensuite en une phrase ce qui est parti.")
NOTE_OUTILS_DOCKER = (
    "Tu as aussi lister_conteneurs et diagnostiquer_conteneur (serveur Unraid, lecture seule). Pour un service "
    "qui ne marche pas, diagnostique d'abord, puis donne la cause probable et un correctif concret que la "
    "personne appliquera elle-même. etat_serveur donne les disques, la parité et les alertes. Pour redémarrer un "
    "conteneur, utilise demander_redemarrage puis demande « Je redémarre <nom> ? » : c'est le « oui » de la personne "
    "qui le fait. Tu ne peux rien faire d'autre : ne prétends jamais avoir agi. "
    "Si on te demande pourquoi un conteneur plante (s'arrête, redémarre en boucle, ne répond plus) : appelle "
    "diagnostiquer_conteneur, puis réponds en deux ou trois phrases simples : la cause probable (appuie-toi sur "
    "causes_probables et cite la ligne de journal qui le montre), et ce qu'il faudrait corriger. Si un redémarrage "
    "peut aider et que redemarrage_possible est vrai, appelle demander_redemarrage et termine par « Je redémarre "
    "<nom> ? ». Si la cause reviendra au redémarrage (disque plein, config invalide…), dis-le au lieu de le proposer. "
    "Pour allumer (démarrer) ou éteindre (arrêter) un conteneur, appelle demander_redemarrage avec l'action "
    "« demarrer » ou « arreter », puis termine par « Je démarre <nom> ? » ou « J'arrête <nom> ? ». "
    "Pour mettre à jour un conteneur (nouvelle image), appelle demander_redemarrage avec l'action "
    "« mettre_a_jour », puis termine par « Je mets à jour <nom> ? » : tu peux le faire, ne renvoie pas vers Unraid. "
    "Dis les états comme on les dit à l'oral (« Plex tourne depuis deux heures », « Radarr est arrêté depuis "
    "cinq minutes ») : jamais de statut Docker brut en anglais ni entre parenthèses."
)


class SouvenirPropose(BaseModel):
    texte: str = Field(min_length=2, max_length=500)
    portee: Literal["foyer", "prive"] = "prive"
    remplace: int | None = None
    conversation: str = Field(default="", max_length=64)


class NouveauNom(BaseModel):
    nom: str = Field(min_length=1, max_length=40)


class EtapeRoutine(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=30)
    action: Literal["allumer", "eteindre", "ouvrir", "fermer", "regler"]
    valeur: float | None = None
    couleur: str | None = Field(default=None, max_length=30)


class NouvelleRoutine(BaseModel):
    nom: str = Field(min_length=2, max_length=60)
    etapes: list[EtapeRoutine] = Field(min_length=1, max_length=20)
    conversation: str = Field(default="", max_length=64)


class PropositionRegle(BaseModel):
    condition: dict
    actions: list[EtapeRoutine] = Field(default_factory=list, max_length=10)
    annonce: str = Field(default="", max_length=200)
    plage: str = Field(default="", max_length=20)
    conversation: str = Field(default="", max_length=64)


class ChangementRegle(BaseModel):
    active: bool


class ChoixHabitude(BaseModel):
    id: str = Field(min_length=3, max_length=200)


class NouveauSouvenir(BaseModel):
    texte: str = Field(min_length=2, max_length=500)
    portee: Literal["foyer", "prive"] = "foyer"


class ModifSouvenir(BaseModel):
    texte: str | None = Field(default=None, min_length=2, max_length=500)
    portee: Literal["foyer", "prive"] | None = None


class SuppressionEvenement(BaseModel):
    ref: str = Field(min_length=1, max_length=500)
    conversation: str = Field(default="", max_length=64)


class NouveauRappel(BaseModel):
    quand: str = Field(min_length=8, max_length=40)
    texte: str = Field(min_length=1, max_length=300)
    conversation: str = Field(default="", max_length=64)


class ChoixAnnonce(BaseModel):
    action: str = Field(min_length=1, max_length=40)


class MessageTelegram(BaseModel):
    texte: str = Field(min_length=1, max_length=1000)
    conversation: str = Field(default="", max_length=64)


def erreur_docker_courte(e: BaseException) -> str:
    """Une raison d'échec à dire à voix haute : jamais d'URL, de lien ni de message technique entier."""
    import httpx
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (502, 503, 504):
            return "le serveur Unraid n'a pas répondu à temps"
        return f"le serveur Unraid a répondu par une erreur {code}"
    if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)):
        return "le serveur Unraid n'a pas répondu à temps"
    if isinstance(e, httpx.HTTPError):
        return "le serveur Unraid est injoignable"
    if isinstance(e, PermissionError):
        return "la clé Unraid n'a pas les droits pour ça"
    texte = re.sub(r"https?://\S+", "", str(e)).split("\n")[0].strip(" .:")
    return texte[:120] or e.__class__.__name__


def attente_depassee(e: BaseException) -> bool:
    """Unraid a lâché la requête (proxy à 60 s) alors que la mise à jour continue peut-être de son côté."""
    import httpx
    return (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (502, 504)) or \
        isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError))


class DemandeRedemarrage(BaseModel):
    nom: str = Field(min_length=1, max_length=100)
    action: Literal["redemarrer", "demarrer", "arreter", "mettre_a_jour"] = "redemarrer"
    conversation: str = Field(default="", max_length=64)


class NouvelEvenement(BaseModel):
    titre: str = Field(min_length=1, max_length=200)
    debut: str = Field(min_length=8, max_length=40)
    fin: str = Field(default="", max_length=40)
    lieu: str = Field(default="", max_length=200)
    journee: bool = False
    conversation: str = Field(default="", max_length=64)
    agenda: Literal["", "perso", "famille"] = ""


class Profil(BaseModel):
    ton: Literal["concis", "normal", "chaleureux"] | None = None
    consigne: str | None = Field(default=None, max_length=500)
    piece: str | None = Field(default=None, max_length=40)
    type: Literal["adulte", "enfant"] | None = None       # modifiable par un administrateur seulement
    # droits : catégories d'appareils que la personne ne pilote pas (administrateur seulement)
    interdits: list[Literal["lumieres", "prises", "volets", "chauffage", "medias", "ventilateurs"]] | None = None


CHAMPS_ADMIN = {"type", "interdits"}
NOMS_DROITS = {"lumieres": "les lumières", "prises": "les prises", "volets": "les volets", "chauffage": "le chauffage",
               "medias": "la télé et la musique", "ventilateurs": "les ventilateurs"}


TONS = {"concis": "Réponds de la façon la plus brève possible, sans formule de politesse.",
        "normal": "",
        "chaleureux": "Sois chaleureux et un peu plus détaillé, avec une touche d'humour légère."}
NOTE_ENFANT = ("Tu parles à un enfant : phrases simples, ton bienveillant, aucun contenu inadapté à son âge ; "
               "pour un sujet délicat, propose d'en parler à un adulte.")


def note_profil(nom: str, p: dict) -> str:
    morceaux = [f"La personne qui te parle s'appelle {nom}."] if nom else []
    if p.get("type") == "enfant":
        morceaux.append(NOTE_ENFANT)
    if TONS.get(p.get("ton", "normal")):
        morceaux.append(TONS[p["ton"]])
    if p.get("piece"):
        morceaux.append(f"Sa pièce habituelle : {p['piece']} (pièce par défaut quand il n'en précise pas).")
    if p.get("consigne"):
        morceaux.append(f"Ses préférences : {p['consigne']}")
    if p.get("interdits"):
        noms = ", ".join(NOMS_DROITS.get(c, c) for c in p["interdits"])
        morceaux.append(f"Elle n'a pas le droit de piloter : {noms}. Si elle le demande, dis-lui simplement "
                        "que ce n'est pas permis pour elle, sans le faire.")
    return " ".join(morceaux)


class NouveauCompte(BaseModel):
    nom: str = Field(min_length=1, max_length=40)
    admin: bool = False                     # ancien écran : = groupe « admin »
    groupe: str = Field(default="", max_length=32)


class MonAgenda(BaseModel):
    """⚙ Mon compte : son agenda CalDAV (url vide = débrancher ; mot de passe vide = garder l'actuel)."""
    url: str = Field(default="", max_length=500)
    utilisateur: str = Field(default="", max_length=100)
    mot_de_passe: str = Field(default="", max_length=200)


class DroitsCompte(BaseModel):
    """Page d'administration : le groupe d'une personne, et ses exceptions (ajouts, retraits)."""
    groupe: str | None = Field(default=None, max_length=32)
    permissions: list[str] | None = None
    retraits: list[str] | None = None


class Groupe(BaseModel):
    nom: str = Field(min_length=1, max_length=40)
    permissions: list[str] = []
    enfant: bool = False                     # parler comme à un enfant


PERMISSION_DOCKER = {"redemarrer": "serveur.redemarrer", "demarrer": "serveur.demarrer_arreter",
                     "arreter": "serveur.demarrer_arreter", "mettre_a_jour": "serveur.mettre_a_jour"}
# Phrases de refus : une demande comprise mais pas permise est dite comme telle (jamais « pas compris »).
REFUS = {
    "serveur": "Tu n'as pas accès au serveur : demande à un administrateur.",
    "telegram": "Tu n'as pas accès à Telegram : demande à un administrateur.",
    "agenda": "Tu n'as pas accès à l'agenda familial : demande à un administrateur.",
    "regles": "Tu n'as pas le droit de créer des règles : demande à un administrateur.",
    "routines": "Tu n'as pas le droit de créer des routines : demande à un administrateur.",
    "rappels": "Tu n'as pas le droit de créer des rappels : demande à un administrateur.",
    "maison": "Tu n'as pas accès à la maison : demande à un administrateur.",
    "web": "Tu n'as pas accès aux questions générales : demande à un administrateur.",
}


PAGE_DIAGNOSTIC = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Diagnostic</title>
<style>:root{color-scheme:light dark}body{margin:0;padding:16px;font:13px/1.45 ui-monospace,monospace;background:Canvas;color:CanvasText}
nav{position:sticky;top:0;background:Canvas;padding:8px 0;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
a{color:#0f7c86;font:600 15px system-ui,sans-serif}pre{white-space:pre-wrap;word-break:break-word;margin:0}</style></head>
<body><nav><a href="#" id="copier">Tout copier</a><a href="?brut=1">Texte brut</a><a href="?jours=7">7 jours</a><span id="etat"></span></nav>
<pre id="t">{texte}</pre>
<script>document.getElementById('copier').onclick=async e=>{e.preventDefault();const t=document.getElementById('t').textContent,
s=document.getElementById('etat');try{await navigator.clipboard.writeText(t);s.textContent='Copié ('+t.length+' caractères)'}
catch(x){const r=document.createRange();r.selectNodeContents(document.getElementById('t'));getSelection().removeAllRanges();
getSelection().addRange(r);s.textContent='Sélectionné : fais Copier'}};</script></body></html>"""

# Accessibles sans être connecté (le reste demande une session hors de la machine elle-même)
CHEMINS_PUBLICS = {"/health", "/connexion", "/api/connexion", "/api/connexion/code", "/api/connexion/totp/preparer",
                   "/api/connexion/totp/activer", "/api/invitation", "/api/securite", "/api/securite/mot-de-passe",
                   "/favicon.ico", "/manifest.webmanifest", "/apple-touch-icon.png"}
# Icônes : visibles avant la connexion (onglet, écran d'accueil du téléphone)
PREFIXES_PUBLICS = ("/static/icones/",)


class Secret(BaseModel):
    nom: str = Field(max_length=40)
    valeur: str = Field(min_length=1, max_length=4000)


class TelechargementOllama(BaseModel):
    modele: str = Field(min_length=1, max_length=120, pattern=r"^[\w.:/-]+$")


class Parole(BaseModel):
    """Texte à lire à voix haute ; `voix` facultative pour un aperçu (« Écouter »)."""
    texte: str = Field(min_length=1, max_length=4000)
    voix: str | None = Field(default=None, max_length=120)


# Les petits modèles imitent les réponses « C'est fait : … » vues dans l'historique :
# une telle phrase sortie d'un modèle est une invention (le modèle n'a aucun accès à la maison).
RE_FAUSSE_ACTION = re.compile(
    r"^\s*(?:c.est (?:fait|remis|allum|[ée]teint|r[ée]gl|ouvert|ferm)|j.ai (?:allum|[ée]teint|mis|remis|r[ée]gl|ouvert|ferm|baiss|augment)"
    r"|(?:je |)(?:viens d.)?(?:allum|[ée]tein)[a-zé]*\s"
    r"|[^.!?\n]{1,60}\b(?:allum|[ée]teint|r[ée]gl|ouvert|ferm)[ée]*e?s?\b[^.!?\n]{0,30}[.!]?\s*$)", re.IGNORECASE)
REPONSE_HONNETE = "Pas compris, rien n'a été fait."


NOTE_MAISON = (
    "Les réponses précédentes sur les appareils (« Globe allumé. », « … : éteint ») viennent du module maison, "
    "relié à Home Assistant : elles sont fiables. Dans ce message tu ne peux pas agir sur la maison : "
    "si on te le demande, réponds seulement « Pas compris, rien n'a été fait. »"
)


NOTE_OUTILS_MAISON = (
    "Tu disposes des outils « maison » (lister_appareils, lire_etat, agir, annuler), reliés à Home Assistant. "
    "Pour toute demande sur la maison, cherche d'abord les appareils avec lister_appareils, puis agis avec agir. "
    "Pour éclairer une pièce, pense aussi aux lumières éteintes et aux prises qui alimentent une lampe "
    "(spot, LED, ruban) : allume-les plutôt que de proposer de le faire. Ne touche pas aux prises qui ne sont "
    "pas des lumières (TV, chargeur…). "
    "Si on te demande d'agir sur « tout » ou « toutes les … » sans préciser de pièce, demande d'abord la pièce "
    "(ou la confirmation pour toute la maison) avant d'appeler agir. "
    "Ne dis jamais qu'une action est faite sans avoir appelé agir et lu sa réponse. Réponds en une phrase courte "
    "qui dit ce que tu as fait (« Projo monté à 100 %. »), sans détailler ce que tu n'as pas changé. "
    "Pour plusieurs appareils, ne les énumère pas (« C'est éteint. ») ; cite seulement ceux qui ont échoué, "
    "brièvement. "
    "Le chauffage, les portails, portes, serrures et l'alarme sont des actions sensibles : agir les refusera, "
    "ne cherche pas à contourner ; dis simplement que c'est à faire depuis Home Assistant. "
    "Tu peux aussi créer des routines nommées (creer_routine) quand on te le demande. "
    "Les réponses précédentes sur les appareils viennent du module maison et sont fiables. "
    "Tu n'as ni fichiers ni commandes."
)


JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"]
RE_HEURE = re.compile(r"^\s*(?:[\w-]+\s*,\s*)?(?:(?:dis[- ]moi|tu (?:peux|sais) me dire|tu sais)\s+)?(?:quelle heure (?:est[- ]il|il est|"
                      r"c'est)|il est quelle heure|l'heure qu'il est|t'as l'heure|tu as l'heure)\b[^\w]*$", re.I)
RE_DATE = re.compile(r"^\s*(?:[\w-]+\s*,\s*)?(?:(?:dis[- ]moi|tu (?:peux|sais) me dire|tu sais)\s+)?(?:on est (?:quel|le combien)(?: jour)?|"
                     r"quel jour (?:on est|sommes[- ]nous|est[- ]on|nous sommes|c'est)(?: aujourd'hui)?|"
                     r"(?:c'est |quelle est )?(?:la date d'aujourd'hui|quelle date)(?: on est| sommes[- ]nous)?)\b[^\w]*$",
                     re.I)


def heure_dite(maintenant: datetime | None = None) -> str:
    m = maintenant or datetime.now().astimezone()
    return f"{m.hour} h {m.minute:02d}" if m.minute else f"{m.hour} h"


def date_dite(maintenant: datetime | None = None) -> str:
    m = maintenant or datetime.now().astimezone()
    return f"{JOURS_FR[m.weekday()]} {'1er' if m.day == 1 else m.day} {MOIS_FR[m.month - 1]} {m.year}"


def reponse_horloge(message: str) -> str | None:
    """« quelle heure est-il ? », « on est quel jour ? » : l'horloge du serveur, jamais un modèle (il devinerait)."""
    if RE_HEURE.match(message):
        return f"Il est {heure_dite()}."
    if RE_DATE.match(message):
        return f"Nous sommes {date_dite()}."
    return None


def _messages(cfg: Config, d: DemandeChat, outils: bool = False, profil: str = "", nom: str = "") -> list[Message]:
    # la date et l'heure : sans elles, un modèle invente (« 17:30 » à 17 h 38)
    quand = f"Nous sommes le {date_dite()}, il est {heure_dite()}."
    consigne = cfg.consigne.replace("{nom}", nom) if nom else cfg.consigne_effective   # « Hal » pour qui a ce thème
    msgs = [Message("system", consigne + "\n\n" + NOTE_STYLE + "\n\n" + quand
                    + ("\n\n" + profil if profil else ""))]
    if cfg.home_assistant.url and not outils:
        msgs.append(Message("system", NOTE_MAISON))
    msgs += [Message(t.role, t.content) for t in d.historique[-HISTORIQUE_MAX:]]
    msgs.append(Message("user", d.message))
    return msgs


def creer_app(config: Config | None = None, registre: Registre | None = None,
              ha: HomeAssistant | None = None, alias: AliasAppris | None = None,
              voix: Voix | None = None, persister: bool | None = None,
              securite: Securite | None = None) -> FastAPI:
    # persister : écrire les réglages modifiés depuis la page dans config.yaml (pas en test)
    persister = (config is None) if persister is None else persister
    cfg = config or charger()
    reg = registre or Registre.depuis_config(cfg)
    routeur = Routeur(cfg.routeur, reg)
    ha = ha or HomeAssistant(cfg.home_assistant)
    extracteur = reg.pour_role(cfg.maison.extracteur) if cfg.maison.extracteur in reg.roles else None
    maison = Maison(cfg.maison, ha, alias or AliasAppris(chemin_alias(cfg)), extracteur,
                    Historique(chemin_alias(cfg).parent))
    voix = voix or Voix(cfg.voix)
    securite = securite or Securite(cfg.securite, dossier_etat(cfg))
    audit = Audit(securite.fichier.parent)
    diag = Diagnostic(securite.fichier.parent)
    docker = (UnraidDocker(cfg.docker.unraid_url, cfg.docker.cle_unraid(), cfg.docker.timeout) if cfg.docker.unraid_url
              else Docker(cfg.docker.url, cfg.docker.timeout))
    if isinstance(docker, UnraidDocker) and cfg.docker.url:
        docker.journaux = Docker(cfg.docker.url, cfg.docker.timeout)   # l'API Unraid ne donne pas les journaux
    annonces = Annonces(securite.fichier.parent, cfg.proactif.heures_calmes)
    conteneurs_en_travaux: dict[str, float] = {}   # nom (minuscules) -> fin de la période sans alerte
    maj_ignorees = MisesAJourIgnorees(securite.fichier.parent)
    telegram = Telegram(cfg.telegram.jeton(), cfg.telegram.chat_id)
    rappels = Rappels(securite.fichier.parent)

    async def boucle_rappels() -> None:
        """Toutes les 5 s : un rappel arrivé à l'heure devient une annonce (dite même en heures calmes)."""
        import asyncio as aio
        while True:
            try:
                for r in rappels.echus():
                    qui = r.get("compte") or ""
                    pour = (securite.comptes.get(qui, {}).get("nom", "") if len(securite.comptes) > 1 else "")
                    # seulement chez la personne, et sur Telegram (celui de la maison) si elle y a droit
                    await signaler(f"Rappel{' pour ' + pour if pour else ''} : {r['texte']}", "urgent", "rappel",
                                   f"rappel:{r['id']}", cfg.telegram.agenda, compte=qui)
            except Exception:  # noqa: BLE001
                journal.exception("rappels : erreur")
            await aio.sleep(5)
    redemarrage_attente: dict[str, tuple[str, str, float, str]] = {}   # conversation -> (conteneur, compte, instant, action)
    noms_conteneurs = {"noms": [], "instant": -1e9}        # pour reconnaître « allume Adminer » sans le mot docker

    async def evoque_docker(message: str) -> bool:
        """Le message parle du serveur ou d'un conteneur existant (alors pas une commande de maison)."""
        if RE_DOCKER_FORT.search(message):
            return True
        if time.monotonic() - noms_conteneurs["instant"] > 300:
            noms_conteneurs["instant"] = time.monotonic()
            try:
                liste = await asyncio.wait_for(docker.conteneurs(), 3)
                noms_conteneurs["noms"] = [n for n in (normaliser_nom(c["nom"]) for c in liste) if len(n) >= 4]
            except Exception:  # noqa: BLE001 - serveur injoignable : on garde la liste précédente
                pass
        mots = {normaliser_nom(m) for m in re.findall(r"[\w'-]+", message)}
        return any(n in mots for n in noms_conteneurs["noms"])

    def conteneur_approchant(message: str) -> str:
        """Nom de conteneur entendu de travers (« ad mineur » -> adminer), parmi un ou deux mots qui se suivent.
        Seulement après l'échec de la maison : un appareil de la maison reste prioritaire."""
        import difflib
        mots = [normaliser_nom(m) for m in re.findall(r"[\w'-]+", message)]
        candidats = {m for m in mots if len(m) >= 5} | {a + b for a, b in zip(mots, mots[1:]) if len(a + b) >= 5}
        for n in noms_conteneurs["noms"]:
            # 0,8 : une lettre de travers sur six (« MiTube » -> metube)
            if len(n) >= 5 and any(difflib.SequenceMatcher(None, c, n).ratio() >= 0.8 for c in candidats):
                return n
        return ""

    async def signaler(texte: str, priorite: str, source: str, cle: str, vers_telegram: bool = True,
                       actions: list[dict] | None = None, cible: dict | None = None, compte: str = "") -> None:
        """Une alerte : gardée dans les annonces (sans doublon) et envoyée sur Telegram la première fois.
        `compte` : annonce personnelle (rappel, règle), montrée à cette personne seulement, et envoyée sur son
        Telegram à elle (si elle a le droit de le recevoir)."""
        a = annonces.ajouter(texte, priorite, source, cle, actions, cible, compte=compte)
        if not (a and vers_telegram and telegram.jeton):
            return
        if compte:
            chats = {chat_telegram(compte)} if securite.peut(compte, "telegram.recevoir") else set()
        else:
            chats = {telegram.chat_id}
            if source in ("docker", "disques", "mises à jour"):    # aussi aux Telegram reliés autorisés
                chats |= {chat_telegram(i) for i in securite.comptes if (securite.comptes[i].get("telegram")
                          and securite.peut(i, "serveur.alertes") and securite.peut(i, "telegram.recevoir"))}
        for chat in filter(None, chats):
            try:
                await telegram.envoyer(f"{cfg.identite.nom_effectif} : {texte}", chat)
            except Exception as e:  # noqa: BLE001
                journal.info("Telegram : envoi impossible (%s)", e)

    def chat_telegram(compte: str | None) -> str:
        """Le Telegram d'une personne : le sien s'il est relié ; sinon celui de la maison pour un administrateur."""
        c = securite.comptes.get(compte or "") or {}
        if (c.get("telegram") or {}).get("id"):
            return str(c["telegram"]["id"])
        return telegram.chat_id if (not compte or securite.est_admin(compte)) else ""
    memoire = Memoire(securite.fichier.parent)
    fil = Fil(securite.fichier.parent)
    routines = Routines(securite.fichier.parent)
    regles = Regles(securite.fichier.parent)
    regle_attente: dict[str, tuple[dict, str, float]] = {}   # conversation -> (règle proposée, compte, instant)
    fichier_ignorees = securite.fichier.parent / "habitudes_ignorees.json"
    fichier_autos = securite.fichier.parent / "automatisations.json"   # créées depuis une habitude (pour les retrouver)

    def automatisations_creees() -> dict[str, dict]:
        try:
            return json.loads(fichier_autos.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def habitudes_ignorees() -> set[str]:
        try:
            return set(json.loads(fichier_ignorees.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()

    async def lancer_routine(r: dict, conversation: str) -> str:
        echecs = []
        for e in r["etapes"]:
            res = await maison.agir_ids(e["entity_ids"], e["action"], e.get("valeur"), e.get("couleur"), conversation)
            if not res.texte.startswith("C'est") or "Refusé" in res.texte or "Pas " in res.texte:
                echecs.append(res.texte)
        return f"{r['nom'][:1].upper()}{r['nom'][1:]} : c'est lancé." + (" " + " ".join(echecs) if echecs else "")
    agenda = Agenda(cfg.agenda.url, cfg.agenda.utilisateur, cfg.agenda.mot_de_passe())

    async def demander_json(consigne: str) -> str:
        """Claude avec le web, pour une carte de rapport ; le secours (sans web) inventerait : refusé."""
        msgs = [Message("system", cfg.consigne_effective + "\n\n" + CONSIGNE_JSON), Message("user", consigne)]
        r = await reg.repondre(cfg.routeur.role_complexe, msgs, json_brut=True)
        if r.secours:
            raise RapportIndisponible("Claude ne répond pas pour le moment (le modèle de secours n'a pas accès au web).")
        return r.texte

    rapports = Rapports(cfg.proactif.ville, demander_json if cfg.routeur.role_complexe in reg.roles else None,
                        agenda, docker)

    async def faire_rapport(dem: Demande, compte: str | None, ctx_revel: frozenset) -> tuple[str, list[dict]]:
        """(phrase, cartes) ; mêmes droits que les écrans : Docker pour les administrateurs, agenda masquable."""
        if dem.type == "docker" and not securite.peut(compte, "serveur.etat"):
            return REFUS["serveur"], []
        if dem.type != "docker" and compte and not securite.peut(compte, "assistant.web"):
            return REFUS["web"], []
        if dem.type == "agenda" and "historique" in ctx_revel:
            return "L'agenda est masqué en ce moment.", []
        try:
            texte, carte = await rapports.faire(dem)
            return texte, [carte]
        except RapportIndisponible as e:
            quoi = "" if dem.type == "libre" else MODELES_RAPPORT.get(dem.type, "").lower()
            return f"Pas de rapport {quoi} : {e}".replace("  ", " "), []
        except Exception as e:  # noqa: BLE001 - service injoignable, réponse inattendue
            journal.info("rapport %s impossible : %s : %s", dem.type, e.__class__.__name__, str(e)[:200])
            return f"Je n'arrive pas à faire ce rapport pour le moment ({e.__class__.__name__}).", []

    def consigne_briefing() -> str:
        ville = f" à {cfg.proactif.ville}" if cfg.proactif.ville else ""
        return (f"Fais le briefing du jour, à dire à voix haute en moins de 6 phrases : la météo d'aujourd'hui{ville} "
                f"(températures, pluie), puis les 3 actualités principales en France ce matin, une phrase chacune. "
                f"Nous sommes le {time.strftime('%d/%m/%Y')}. Pas de liste, pas de source.")

    async def briefing() -> str:
        msgs = [Message("system", cfg.consigne_effective + "\n\n" + NOTE_STYLE), Message("user", consigne_briefing())]
        return sans_emoji((await reg.repondre(cfg.routeur.role_complexe, msgs)).texte)

    async def surveiller_une_fois(etat_veille: dict) -> None:
        """Un passage de surveillance : présence, conteneurs, règles, briefing."""
        from datetime import datetime
        etats = []
        if ha.configure:
            try:
                from .maison.inventaire import CMD_ETATS
                etats = (await ha.ws([CMD_ETATS])).get(CMD_ETATS) or []
            except Exception:  # noqa: BLE001 - Home Assistant injoignable : on réessaiera
                etats = []
            personnes = [x for x in etats if x.get("entity_id", "").startswith("person.")]
            if personnes:
                annonces.presents = any(x.get("state") == "home" for x in personnes)
        if docker.configure:
            try:
                mauvais = {c["nom"]: c for c in await docker.problemes()}
            except Exception:  # noqa: BLE001
                mauvais = None
            if mauvais is not None:
                # conteneur en cours de mise à jour (Unraid le recrée : arrêté ou « dead » un moment) : pas d'alerte
                maintenant_t = time.time()
                mauvais = {n: c for n, c in mauvais.items() if conteneurs_en_travaux.get(n.lower(), 0) < maintenant_t}
                for nom, c in mauvais.items():
                    quoi = ("est en mauvaise santé" if c["sante"] == "unhealthy"
                            else f"est {statut_lisible(c['statut'], c['etat'])}")
                    await signaler(f"Le conteneur {nom} {quoi}. Demande-moi un diagnostic.", "important",
                                   "docker", f"docker:{nom}", cfg.telegram.docker)
                for nom in etat_veille.get("docker", set()) - set(mauvais):
                    annonces.resoudre(f"docker:{nom}")
                etat_veille["docker"] = set(mauvais)
        par_id = {x.get("entity_id"): x for x in etats}
        for regle in cfg.proactif.surveiller:
            x = par_id.get(regle.entity_id)
            cle = f"regle:{regle.entity_id}:{regle.etat}"
            if not x:
                continue
            if x.get("state") != regle.etat:
                annonces.resoudre(cle)
                continue
            try:
                depuis = datetime.fromisoformat(x.get("last_changed", "").replace("Z", "+00:00"))
            except ValueError:
                continue
            minutes = (datetime.now(depuis.tzinfo) - depuis).total_seconds() / 60
            if minutes >= regle.minutes:
                nom = (x.get("attributes") or {}).get("friendly_name", regle.entity_id)
                texte = regle.message or f"{nom} est {ETATS_FR_COURTS.get(regle.etat, regle.etat)} depuis {int(minutes)} minutes."
                await signaler(texte, regle.priorite, "surveillance", cle)
        if isinstance(docker, UnraidDocker) and docker.configure:
            try:
                alertes = (await serveur_unraid())["alertes"]
            except Exception:  # noqa: BLE001
                alertes = None
            if alertes is not None:
                for texte_a in alertes:
                    await signaler(f"Serveur : {texte_a}.", "urgent" if "critique" in texte_a or "SMART" in texte_a
                                   else "important", "disques", f"disque:{texte_a}", cfg.telegram.disques)
                for ancienne in etat_veille.get("disques", set()) - set(alertes):
                    annonces.resoudre(f"disque:{ancienne}")
                etat_veille["disques"] = set(alertes)
            if cfg.docker.mises_a_jour and time.monotonic() >= etat_veille.get("maj_prochaine", 0):
                etat_veille["maj_prochaine"] = time.monotonic() + cfg.docker.mises_a_jour_intervalle_h * 3600
                await surveiller_mises_a_jour()
        if agenda.configure and cfg.agenda.annonce_avant_min:
            from datetime import datetime as dt, timedelta
            maintenant = dt.now().astimezone()
            try:
                bientot = await agenda.evenements(maintenant, maintenant + timedelta(minutes=cfg.agenda.annonce_avant_min))
            except Exception:  # noqa: BLE001
                bientot = []
            for e in bientot:
                if e.get("journee"):
                    continue
                minutes = max(0, round((e["debut"] - maintenant).total_seconds() / 60))
                quand = "C'est l'heure" if minutes <= 1 else f"Dans {minutes} minutes"
                await signaler(f"{quand} : {e.get('titre', 'un événement')}"
                               + (f" ({e['lieu']})" if e.get("lieu") else "") + ".", "important", "agenda",
                               f"agenda:{e.get('uid', e.get('titre'))}:{e['debut']:%Y%m%d%H%M}", cfg.telegram.agenda)
        # agendas personnels : chacun est prévenu de ses rendez-vous, chez lui seulement
        if cfg.agenda.annonce_avant_min:
            from datetime import datetime as dt, timedelta
            maintenant = dt.now().astimezone()
            for ident in list(securite.comptes):
                perso = agenda_perso(ident) if securite.peut(ident, "agenda.personnel") else None
                if not perso:
                    continue
                try:
                    bientot = await perso.evenements(maintenant, maintenant + timedelta(minutes=cfg.agenda.annonce_avant_min))
                except Exception:  # noqa: BLE001
                    continue
                for e in bientot:
                    if e.get("journee"):
                        continue
                    minutes = max(0, round((e["debut"] - maintenant).total_seconds() / 60))
                    quand = "C'est l'heure" if minutes <= 1 else f"Dans {minutes} minutes"
                    await signaler(f"{quand} : {e.get('titre', 'un événement')}"
                                   + (f" ({e['lieu']})" if e.get("lieu") else "") + ".", "important", "agenda-perso",
                                   f"agenda:{ident}:{e.get('uid', e.get('titre'))}:{e['debut']:%Y%m%d%H%M}",
                                   cfg.telegram.agenda, compte=ident)
        h = cfg.proactif.briefing_heure
        jour = time.strftime("%Y-%m-%d")
        if h and time.strftime("%H:%M") >= h and etat_veille.get("briefing") != jour and annonces.presents is not False:
            etat_veille["briefing"] = jour
            try:
                annonces.ajouter(await briefing(), "info", "briefing", f"briefing:{jour}")
            except Exception as e:  # noqa: BLE001
                journal.info("briefing impossible : %s", e)

    def serveur_unraid():
        return docker.serveur(cfg.docker.plein_pct, cfg.docker.libre_min_pct, cfg.docker.parite_max_jours)

    async def surveiller_mises_a_jour() -> None:
        """Une annonce par conteneur dont l'image a une mise à jour, avec « Mettre à jour » et « Ignorer »."""
        try:
            en_attente = set(await docker.mises_a_jour())
        except Exception as e:  # noqa: BLE001 - Unraid trop ancien ou injoignable : on réessaiera
            journal.info("mises à jour Docker illisibles : %s", e)
            return
        maj_ignorees.garder_seulement(en_attente)
        for nom in sorted(en_attente, key=str.lower):
            if maj_ignorees.est_ignoree(nom):
                continue
            await signaler(f"Une mise à jour de l'image de {nom} est disponible.", "info", "mises à jour",
                           f"maj:{nom}", cfg.telegram.mises_a_jour,
                           actions=[{"id": "mettre_a_jour", "libelle": "Mettre à jour"},
                                    {"id": "ignorer", "libelle": "Ignorer"}],
                           cible={"type": "maj_docker", "conteneur": nom})
        for a in annonces.liste:          # mis à jour ailleurs (page Docker d'Unraid) : l'annonce est close
            nom = (a.get("cible") or {}).get("conteneur")
            if a.get("cle", "").startswith("maj:") and not a.get("resolue") and nom not in en_attente:
                annonces.resoudre(a["cle"])

    async def boucle_surveillance() -> None:
        import asyncio as aio
        veille: dict = {"briefing": time.strftime("%Y-%m-%d") if cfg.proactif.briefing_heure
                        and time.strftime("%H:%M") >= cfg.proactif.briefing_heure else ""}
        while True:
            try:
                await surveiller_une_fois(veille)
            except Exception:  # noqa: BLE001 - la surveillance ne doit jamais s'arrêter
                journal.exception("surveillance : erreur")
            await aio.sleep(cfg.proactif.intervalle_s)
    async def etats_ha() -> dict[str, dict]:
        from .maison.inventaire import CMD_ETATS
        return {x.get("entity_id"): x for x in (await ha.ws([CMD_ETATS])).get(CMD_ETATS) or [] if x.get("entity_id")}

    async def noms_regle(r: dict, etats: dict[str, dict] | None = None) -> tuple[dict[str, str], dict[str, str]]:
        """Noms lisibles et unités des entités d'une règle, pour la relire à voix haute."""
        ids = {r["condition"].get("entity_id", "")} | {i for e in r.get("actions", []) for i in e["entity_ids"]}
        noms, unites = {}, {}
        for i in filter(None, ids):
            x = (etats or {}).get(i) or await ha.etat_entite(i)
            if not x:
                continue
            a = x.get("attributes") or {}
            noms[i] = a.get("friendly_name") or i
            if r["condition"].get("entity_id") == i:
                unites[i] = "°C" if r["condition"].get("attribut") in ("current_temperature", "temperature") \
                    else a.get("unit_of_measurement", "")
        return noms, unites

    async def executer_regle(r: dict) -> str:
        """Une règle vient de se déclencher : ses actions (mêmes garde-fous que les routines), puis son annonce."""
        faits = []
        auteur = r.get("compte") or ""
        # mêmes limites que son auteur : ce qu'il ne pilote plus, la règle ne le pilote plus non plus
        jeton = interdits.set(domaines_interdits(securite.profil(auteur)["interdits"]) if auteur in securite.comptes
                              else frozenset())
        try:
            for e in r.get("actions", []):
                res = await maison.agir_ids(e["entity_ids"], e["action"], e.get("valeur"), e.get("couleur"),
                                            f"regle:{r['id']}")
                faits.append(res.texte or "Rien n'a été fait.")
        finally:
            interdits.reset(jeton)
        if r.get("annonce"):
            await signaler(r["annonce"], "important", "regle", f"regle:{r['id']}",
                           compte=auteur if auteur in securite.comptes else "")
            faits.append("Annonce faite.")
        resultat = " ".join(faits)
        regles.noter(r["id"], resultat)
        audit.noter("règle_déclenchée", r.get("compte", ""), "", par="règle", regle=r["texte"][:200], resultat=resultat[:200])
        journal.info("règle %s déclenchée : %s", r["id"], resultat[:160])
        return resultat

    async def evaluer_regles_une_fois(etats: dict[str, dict] | None = None) -> list[str]:
        """Un passage : lit les états de Home Assistant et déclenche les règles dont la condition vient de devenir vraie."""
        if not any(r.get("active", True) for r in regles.liste):
            return []
        if etats is None:
            try:
                etats = await etats_ha()
            except Exception:  # noqa: BLE001 - Home Assistant injoignable : on réessaiera
                return []
        fronts, retombees = regles.a_declencher(etats)
        for r in retombees:
            annonces.resoudre(f"regle:{r['id']}")
        return [await executer_regle(r) for r in fronts]

    async def boucle_regles() -> None:
        import asyncio as aio
        while True:
            try:
                await evaluer_regles_une_fois()
            except Exception:  # noqa: BLE001 - les règles ne doivent jamais arrêter l'assistant
                journal.exception("règles : erreur")
            await aio.sleep(15)
    locuteurs = Locuteurs(cfg.voix.chemin_locuteur(), securite.fichier.parent, cfg.voix.seuil_locuteur)
    locuteur_conversation: dict[str, tuple[str, float]] = {}   # conversation -> (compte reconnu, instant)

    async def reconnaitre(audio: bytes, conversation: str) -> dict | None:
        """Qui a parlé ? Seulement si la voix d'au moins un compte est enregistrée."""
        if not (cfg.voix.identification and locuteurs.voix and locuteurs.disponible):
            return None
        import asyncio as aio

        from .voix import decoder_audio
        try:
            r = await aio.to_thread(lambda: locuteurs.identifier(decoder_audio(audio),
                                                                 [c for c in locuteurs.voix if c in securite.comptes]))
        except Exception as e:  # noqa: BLE001 - jamais bloquant pour la conversation
            journal.info("empreinte vocale impossible : %s", e.__class__.__name__)
            return None
        if r:
            r["nom"] = securite.comptes.get(r["compte"], {}).get("nom", r["compte"])
            if conversation:
                locuteur_conversation[conversation] = (r["compte"], time.monotonic())
        return r
    for h in [h for h in journal.handlers if isinstance(h, GestionnaireJournal)]:
        journal.removeHandler(h)        # une seule copie du journal, même si l'appli est recréée
    journal.addHandler(GestionnaireJournal(diag))
    reveil = Reveil(cfg.voix.fenetre_s, cfg.voix.toujours_a_l_ecoute)
    import asyncio as _asyncio
    verrou_transcription = _asyncio.Lock()   # une transcription à la fois (sinon CPU/GPU saturé)
    jeton_interne = secrets.token_urlsafe(24)   # pour le serveur MCP « maison », change à chaque démarrage

    def outils_maison(conversation: str) -> dict:
        """Outils de Claude pour cette conversation : seulement ceux que la personne a le droit d'utiliser
        (mcp_maison filtre avec ASSISTANT_PERMISSIONS ; l'API interne revérifie à chaque appel)."""
        d = droits_conv(conversation)
        serveur = docker.configure and "serveur.etat" in d
        avec_agenda = bool(agendas_de(proprietaire_conv(conversation), d))
        avec_telegram = bool(telegram.jeton) and "telegram.envoyer" in d
        env = {"ASSISTANT_INTERNE_URL": f"http://127.0.0.1:{cfg.serveur.port}",
               "ASSISTANT_INTERNE_JETON": jeton_interne, "ASSISTANT_CONVERSATION": conversation,
               "ASSISTANT_DOCKER": "1" if serveur else "0",
               "ASSISTANT_AGENDA": "1" if avec_agenda else "0",
               "ASSISTANT_TELEGRAM": "1" if avec_telegram else "0",
               "ASSISTANT_PERMISSIONS": ",".join(sorted(d))}
        date = f"{JOURS[time.localtime().tm_wday]} {time.strftime('%d/%m/%Y %H:%M')}"
        return {
            "mcp": {"mcpServers": {"maison": {"command": sys.executable, "args": ["-m", "assistant.mcp_maison"], "env": env}}},
            "autoriser": ["mcp__maison"],
            "note": NOTE_OUTILS_MAISON + "\n\n" + NOTE_RAPPELS.format(date=date)
                    + ("\n\n" + NOTE_OUTILS_DOCKER if serveur else "") + ("\n\n" + NOTE_TELEGRAM if avec_telegram else "")
                    + ("\n\n" + NOTE_OUTILS_AGENDA.format(date=date) if avec_agenda else "")
                    + ("\n\nCertains outils ne sont pas permis à cette personne et n'apparaissent pas : si elle demande "
                       "ce qu'ils feraient, dis-lui simplement que ce n'est pas permis pour elle."
                       if len(d) < len(D.PERMISSIONS) else ""),
        }

    def verifier_jeton(x_jeton_interne: str | None) -> None:
        if not x_jeton_interne or not secrets.compare_digest(x_jeton_interne, jeton_interne):
            raise HTTPException(403, "jeton interne invalide")

    @asynccontextmanager
    async def cycle(app: FastAPI):
        import asyncio
        if cfg.voix.active and cfg.voix.whisper_present() and cfg.voix.synthese_presente():
            app.state.prechauffage = asyncio.create_task(voix.prechauffer())
        app.state.rappels = asyncio.create_task(boucle_rappels())
        app.state.boucle_regles = asyncio.create_task(boucle_regles()) if ha.configure else None
        if (cfg.proactif.active and (ha.configure or docker.configure or cfg.proactif.briefing_heure)) or telegram.configure                 or agenda.configure:
            app.state.surveillance = asyncio.create_task(boucle_surveillance())
        yield
        if getattr(app.state, "surveillance", None):
            app.state.surveillance.cancel()
        app.state.rappels.cancel()
        if app.state.boucle_regles:
            app.state.boucle_regles.cancel()
        await reg.fermer()
        await ha.fermer()

    app = FastAPI(title=f"{cfg.identite.nom} — orchestrateur", version=__version__, lifespan=cycle)
    app.state.config = cfg
    app.state.registre = reg
    app.state.routeur = routeur
    app.state.ha = ha
    app.state.maison = maison
    app.state.voix = voix
    app.state.securite = securite
    app.state.audit = audit
    app.state.annonces = annonces
    app.state.fil = fil
    app.state.surveiller_une_fois = surveiller_une_fois     # pour les tests
    app.state.conteneurs_en_travaux = conteneurs_en_travaux
    app.state.docker = docker
    app.state.evaluer_regles_une_fois = evaluer_regles_une_fois
    app.state.regles = regles

    def ip_de(requete: Request) -> tuple[str, str]:
        """(IP directe, IP réelle du client). « testclient » = client de test de Starlette, traité comme local."""
        directe = requete.client.host if requete.client else ""
        if directe == "testclient":
            directe = "127.0.0.1"
        return directe, securite.ip_client(directe, {k.lower(): v for k, v in requete.headers.items()})

    def en_https(requete: Request, directe: str) -> bool:
        if securite.proxies and securite.reseau_autorise(directe):
            return requete.headers.get("x-forwarded-proto", "") == "https"
        return requete.url.scheme == "https"

    def compte_de(requete: Request) -> str | None:
        """Compte de la requête : « machine » sur la machine elle-même, sinon celui de la session."""
        return getattr(requete.state, "compte", None)

    def auditer(requete: Request, type_: str, **detail) -> None:
        audit.noter(type_, compte_de(requete) or "", ip_de(requete)[1], **detail)

    compte_conversation: dict[str, tuple[str, str]] = {}    # conversation -> (compte, ip)
    texte_conversation: dict[str, str] = {}                  # conversation -> dernière demande (historique)
    cartes_conversation: dict[str, list[str]] = {}           # conversation -> appareils lus ou touchés par Claude
    agenda_recent: dict[str, float] = {}                     # conversation -> dernier échange sur l'agenda
    derniere_carte: dict[str, tuple[Demande, float]] = {}    # conversation -> dernière carte de rapport (« et à Lyon ? »)
    masques_conversation: dict[str, frozenset] = {}          # conversation -> ce qu'on ne révèle pas (outils de Claude)
    interdits_conversation: dict[str, frozenset] = {}        # conversation -> domaines que la personne ne pilote pas
    personne_conversation: dict[str, str] = {}               # conversation -> qui parle (compte, ou voix reconnue)
    droits_conversation: dict[str, frozenset] = {}          # conversation -> permissions effectives (outils de Claude)

    def interdits_de(compte: str | None, conversation: str = "") -> frozenset:
        """Droits effectifs : ceux du compte connecté, réduits par ceux de la voix reconnue dans la
        conversation. La voix ne peut que retirer des droits, jamais en donner."""
        propres = (domaines_interdits(securite.profil(compte)["interdits"])
                   if compte and compte != COMPTE_MACHINE else frozenset())
        return propres | interdits_conversation.get(conversation, frozenset())

    def contexte_de(requete: Request) -> dict:
        """D'où vient la demande : à distance ? appareil partagé ? compte enfant ? Et donc ce qu'on masque."""
        import ipaddress
        ip = ip_de(requete)[1]
        try:
            a = ipaddress.ip_address(ip.split("%")[0])
            distant = not (a.is_private or a.is_loopback or a.is_link_local)
        except ValueError:
            distant = True
        partage = requete.cookies.get(COOKIE_APPAREIL) == "partage"
        compte = compte_de(requete)
        enfant = bool(compte and compte != COMPTE_MACHINE and securite.profil(compte).get("type") == "enfant")
        actifs = [n for n, v in (("distant", distant), ("partage", partage), ("enfant", enfant)) if v]
        caches = frozenset(c for n in actifs for c in cfg.revelation.masquer.get(n, []))
        return {"distant": distant, "partage": partage, "enfant": enfant, "masques": sorted(caches)}

    def exiger_revelation(requete: Request, categorie: str) -> None:
        if categorie in contexte_de(requete)["masques"]:
            raise HTTPException(403, "information non donnée sur cet appareil")

    @app.middleware("http")
    async def pas_de_page_perimee(requete: Request, suite):
        """Pages et scripts : le navigateur revérifie à chaque chargement (304 si inchangé), sinon une
        tablette peut garder l'ancien reglages.js après une mise à jour."""
        rep_ = await suite(requete)
        chemin = requete.url.path
        if chemin == "/" or chemin == "/connexion" or chemin.endswith((".js", ".html", ".css", ".webmanifest")):
            rep_.headers["Cache-Control"] = "no-cache"
        return rep_

    @app.middleware("http")
    async def controle_acces(requete: Request, suite):
        chemin = requete.url.path
        directe, ip = ip_de(requete)
        requete.state.compte = None
        # API interne (serveur MCP lancé par l'assistant) : uniquement depuis la machine, jamais via le proxy
        if chemin.startswith("/interne/"):
            if not est_locale(directe) or requete.headers.get("x-forwarded-for"):
                return JSONResponse({"detail": "interdit"}, 403)
            return await suite(requete)
        if not securite.reseau_autorise(ip):
            journal.info("accès refusé (réseau non autorisé) : %s %s", ip, chemin)
            return JSONResponse({"detail": "réseau non autorisé"}, 403)
        if not securite.exige_connexion(ip):
            requete.state.compte = COMPTE_MACHINE
            return await suite(requete)
        requete.state.compte = securite.session(requete.cookies.get(NOM_COOKIE))
        if chemin in CHEMINS_PUBLICS or chemin.startswith(PREFIXES_PUBLICS) or requete.state.compte:
            return await suite(requete)
        if requete.method == "GET" and not chemin.startswith("/api/"):
            return RedirectResponse(f"/connexion?suite={chemin}", 303)
        return JSONResponse({"detail": "connexion requise"}, 401)

    def exiger_admin(requete: Request) -> str:
        compte = compte_de(requete)
        if not securite.est_admin(compte):
            raise HTTPException(403, "réservé aux administrateurs")
        return compte

    def exiger(requete: Request, *permissions: str) -> str:
        """Une des permissions suffit ; sinon 403 avec ce qui manque, en clair."""
        compte = compte_de(requete)
        if not any(securite.peut(compte, p) for p in permissions):
            raise HTTPException(403, "pas permis pour ce compte : " + D.PERMISSIONS[permissions[0]])
        return compte

    def droits_de(compte: str | None, conversation: str = "", voix: str | None = None) -> frozenset:
        """Droits effectifs d'une demande : ceux du compte connecté, réduits par ceux de la voix reconnue
        (la voix ne donne jamais plus que le compte)."""
        d = set(securite.permissions(compte))
        if voix and voix in securite.comptes:
            d &= securite.permissions(voix)
        return frozenset(d)

    def droits_conv(conversation: str) -> frozenset:
        """Pour l'API interne (outils de Claude) : les droits posés par le chat pour cette conversation.
        Conversation inconnue (tests, outil lancé à la main sur la machine) : ceux de la machine."""
        if conversation in droits_conversation:
            return droits_conversation[conversation]
        compte = compte_conversation.get(conversation, ("", ""))[0]
        return frozenset(securite.permissions(compte or COMPTE_MACHINE))

    def exiger_conv(conversation: str, *permissions: str) -> None:
        if not set(permissions) & droits_conv(conversation):
            raise HTTPException(403, "pas permis pour cette personne : " + D.PERMISSIONS[permissions[0]])

    def a_moi(objet: dict, compte: str | None) -> bool:
        """Règle, routine, rappel : à son auteur ; les anciens (sans auteur) et l'administrateur voient tout."""
        auteur = objet.get("compte") or ""
        return auteur in ("", "claude") or auteur == compte or securite.est_admin(compte)

    def visible_annonce(a: dict, compte: str | None) -> bool:
        if compte == COMPTE_MACHINE:
            return True
        if a.get("compte"):
            return a["compte"] == compte
        if a.get("source") in ("docker", "disques", "mises à jour"):
            return securite.peut(compte, "serveur.alertes")
        if a.get("source") == "agenda":
            return securite.peut(compte, "agenda.lire")
        return True

    @app.middleware("http")
    async def tracer_voix_et_chat(requete: Request, suite):
        """Pour le diagnostic : chaque appel de la voix et du chat, sa durée, et l'erreur complète s'il plante."""
        chemin = requete.url.path
        if not (chemin.startswith("/api/voix/") or chemin == "/api/chat"):
            return await suite(requete)
        debut = time.perf_counter()
        try:
            rep_ = await suite(requete)
        except Exception:
            journal.exception("%s %s a planté après %d ms", requete.method, chemin, (time.perf_counter() - debut) * 1000)
            raise
        diag.serveur(f"{requete.method} {chemin}{'?' + requete.url.query if requete.url.query else ''} -> "
                     f"{rep_.status_code} en {round((time.perf_counter() - debut) * 1000)} ms "
                     f"({getattr(requete.state, 'compte', None) or '?'} depuis {ip_de(requete)[1]})")
        return rep_

    @app.post("/api/diagnostic/evenements", include_in_schema=False)
    async def recevoir_evenements(requete: Request) -> dict:
        """Événements de la page (envoyés toutes les 3 s et par sendBeacon à la fermeture)."""
        corps = await requete.body()
        if len(corps) > 64 * 1024:
            raise HTTPException(413, "trop gros (64 Ko maximum)")
        try:
            d = json.loads(corps or b"{}")
            lignes = d.get("lignes") or []
            if not isinstance(lignes, list) or not all(isinstance(l, dict) for l in lignes):
                raise ValueError
            diag.evenements(str(d.get("appareil", "")), str(d.get("page", "")), lignes,
                            str(d["contexte"]) if d.get("contexte") else None,
                            compte_de(requete) or "", ip_de(requete)[1])
        except (ValueError, AttributeError) as e:
            raise HTTPException(422, "format attendu : {appareil, page, contexte?, lignes:[{ms, heure, texte}]}") from e
        return {"ok": True}

    @app.get("/diagnostic", include_in_schema=False)
    def page_diagnostic(requete: Request, brut: bool = False, jours: float = 2) -> Response:
        """Tout le diagnostic (pages + serveur) des derniers jours, secrets masqués. ?brut=1 : texte seul."""
        exiger_admin(requete)
        entete = (f"Diagnostic {cfg.identite.nom_effectif} v{__version__} · {time.strftime('%Y-%m-%d %H:%M:%S %z')} · "
                  f"{'conteneur' if os.name != 'nt' and Path('/config').is_dir() else 'PC'} · voix {cfg.voix.moteur_effectif()}")
        texte = diag.rapport(max(0.1, min(jours, 7)), entete)
        if brut:
            return Response(texte, media_type="text/plain; charset=utf-8")
        import html
        return Response(PAGE_DIAGNOSTIC.replace("{texte}", html.escape(texte)), media_type="text/html; charset=utf-8")

    @app.get("/connexion", include_in_schema=False)
    def page_connexion() -> FileResponse:
        return FileResponse(STATIC / "connexion.html")

    @app.get("/admin", include_in_schema=False)
    def page_admin(requete: Request) -> Response:
        """Gestion des personnes, groupes et droits (administrateurs)."""
        if not securite.est_admin(compte_de(requete)):
            return RedirectResponse("/", 303)
        return FileResponse(STATIC / "admin.html")

    @app.get("/api/securite")
    def etat_securite(requete: Request) -> dict:
        directe, ip = ip_de(requete)
        compte = compte_de(requete)
        info = securite.comptes.get(compte or "", {})
        return {"nom": cfg.identite.nom_effectif, "mot_de_passe_defini": securite.mot_de_passe_defini,
                "locale": est_locale(ip), "ip": ip, "session_jours": cfg.securite.session_jours,
                "connecte": compte is not None, "compte": compte, "compte_nom": info.get("nom", compte),
                "admin": securite.est_admin(compte), "totp": bool(info.get("totp")),
                "permissions": sorted(securite.permissions(compte)) if compte else [],
                "totp_obligatoire": cfg.securite.totp_obligatoire}

    def poser_session(rep: Response, requete: Request, directe: str, compte: str) -> None:
        rep.set_cookie(NOM_COOKIE, securite.creer_session(compte), max_age=int(cfg.securite.session_jours * 86400),
                       httponly=True, samesite="lax", secure=en_https(requete, directe), path="/")

    def refus(requete: Request, type_: str, e: AccesRefuse, **detail):
        message = str(e)
        audit.noter(type_, detail.pop("compte", "") or "", ip_de(requete)[1], resultat="refusé", raison=message, **detail)
        journal.info("%s refusé : %s (%s)", type_, ip_de(requete)[1], message)
        return HTTPException(429 if message.startswith("Trop") else 401, message)

    @app.post("/api/connexion")
    def se_connecter(c: Connexion, requete: Request, reponse: Response) -> dict:
        """Étape 1 : mot de passe. Ensuite : code TOTP, ou configuration du TOTP à la première connexion."""
        directe, ip = ip_de(requete)
        if not securite.mot_de_passe_defini:
            raise HTTPException(409, "aucun compte : crée le premier depuis la machine de l'assistant")
        try:
            etape, compte = securite.connexion(ip, c.identifiant, c.mot_de_passe)
        except AccesRefuse as e:
            raise refus(requete, "connexion", e, identifiant=c.identifiant) from e
        if etape == "session":
            poser_session(reponse, requete, directe, compte)
            audit.noter("connexion", compte, ip, resultat="ok", facteur="mot de passe")
            return {"etape": "fini"}
        return {"etape": etape, "jeton": securite.jeton_etape(compte, etape),
                "nom": securite.comptes[compte].get("nom", compte)}

    @app.post("/api/connexion/code")
    def code_totp(e: Etape, requete: Request, reponse: Response) -> dict:
        directe, ip = ip_de(requete)
        try:
            compte = securite.lire_etape(e.jeton, "totp")
            facteur = securite.verifier_code(ip, compte, e.code)
        except AccesRefuse as x:
            raise refus(requete, "connexion", x) from x
        poser_session(reponse, requete, directe, compte)
        audit.noter("connexion", compte, ip, resultat="ok", facteur=facteur)
        return {"etape": "fini", "secours_restants": len(securite.comptes[compte].get("secours", []))}

    @app.post("/api/connexion/totp/preparer")
    def preparer_totp(e: Etape) -> dict:
        try:
            compte = securite.lire_etape(e.jeton, "configurer_totp")
        except AccesRefuse as x:
            raise HTTPException(401, str(x)) from x
        return securite.preparer_totp(compte, cfg.identite.nom)

    @app.post("/api/connexion/totp/activer")
    def activer_totp(e: Etape, requete: Request, reponse: Response) -> dict:
        directe, ip = ip_de(requete)
        try:
            compte = securite.lire_etape(e.jeton, "configurer_totp")
            codes = securite.activer_totp(ip, compte, e.code)
        except AccesRefuse as x:
            raise refus(requete, "totp_activation", x) from x
        poser_session(reponse, requete, directe, compte)
        audit.noter("totp_activé", compte, ip)
        audit.noter("connexion", compte, ip, resultat="ok", facteur="totp")
        return {"etape": "fini", "codes_de_secours": codes}

    @app.post("/api/invitation")
    def accepter_invitation(i: Invitation, requete: Request) -> dict:
        """Lien d'invitation : la personne choisit son mot de passe, puis configure son code TOTP."""
        ip = ip_de(requete)[1]
        try:
            securite._verifier_bloque(ip)
            compte = securite.utiliser_invitation(i.invitation, i.mot_de_passe)
        except AccesRefuse as e:
            securite.echec(ip)
            raise refus(requete, "invitation", e) from e
        audit.noter("invitation_acceptée", compte, ip)
        etape = "configurer_totp" if cfg.securite.totp_obligatoire else "totp"
        return {"etape": etape, "jeton": securite.jeton_etape(compte, etape),
                "nom": securite.comptes[compte].get("nom", compte)}

    @app.post("/api/deconnexion")
    def se_deconnecter(requete: Request, reponse: Response) -> dict:
        auditer(requete, "déconnexion")
        reponse.delete_cookie(NOM_COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/securite/mot-de-passe")
    def changer_mot_de_passe(m: NouveauMotDePasse, requete: Request, reponse: Response) -> dict:
        """Premier compte : seulement depuis la machine. Ensuite : son propre mot de passe, en donnant l'actuel
        (la machine elle-même peut réinitialiser le premier administrateur sans l'actuel)."""
        directe, ip = ip_de(requete)
        compte = compte_de(requete)
        if not securite.mot_de_passe_defini:
            if not est_locale(ip):
                raise HTTPException(403, "le premier compte se crée sur la machine de l'assistant")
            cree = securite.creer_premier(m.nouveau)
            audit.noter("compte_créé", COMPTE_MACHINE, ip, cible=cree, admin=True)
            return {"ok": True, "compte": cree}
        if compte == COMPTE_MACHINE:
            cible = next(i for i, c in securite.comptes.items() if c.get("admin"))
        elif compte:
            cible = compte
            if not verifier_mdp(cible, m.actuel or ""):
                raise refus(requete, "mot_de_passe", AccesRefuse("Mot de passe actuel incorrect."), compte=cible)
        else:
            raise HTTPException(401, "connexion requise")
        securite.definir_mot_de_passe(cible, m.nouveau)
        if compte != COMPTE_MACHINE:
            poser_session(reponse, requete, directe, cible)
        audit.noter("mot_de_passe_changé", compte or "", ip, cible=cible)
        journal.info("mot de passe de %s changé (ses autres sessions sont fermées)", cible)
        return {"ok": True}

    def verifier_mdp(compte: str, mdp: str) -> bool:
        from .securite import verifier as v
        return v(mdp, securite.comptes.get(compte, {}).get("mdp") or "")

    # --- comptes (administrateurs) et journal ----------------------------------------------------
    def lien_invitation(requete: Request, jeton: str) -> str:
        hote = requete.headers.get("x-forwarded-host") or requete.headers.get("host") or ""
        proto = "https" if en_https(requete, ip_de(requete)[0]) else "http"
        return f"{proto}://{hote}/connexion?invitation={jeton}"

    @app.get("/api/comptes")
    def lister_comptes(requete: Request) -> dict:
        exiger_admin(requete)
        return {"comptes": securite.liste(), "moi": compte_de(requete)}

    @app.post("/api/comptes")
    def creer_compte(n: NouveauCompte, requete: Request) -> dict:
        admin = exiger_admin(requete)
        try:
            ident, jeton = securite.creer_compte(n.nom, n.admin, n.groupe)
        except AccesRefuse as e:
            raise HTTPException(409, str(e)) from e
        except D.DroitInvalide as e:
            raise HTTPException(422, str(e)) from e
        audit.noter("compte_créé", admin, ip_de(requete)[1], cible=ident, groupe=securite.groupe_de(ident))
        return {"id": ident, "lien": lien_invitation(requete, jeton), "expire_jours": 7}

    @app.post("/api/comptes/{ident}/invitation")
    def reinviter(ident: str, requete: Request) -> dict:
        """Nouveau lien : la personne choisira un nouveau mot de passe et refera son TOTP."""
        admin = exiger_admin(requete)
        if ident not in securite.comptes:
            raise HTTPException(404, "compte inconnu")
        securite.reinitialiser_totp(ident)
        jeton = securite.nouvelle_invitation(ident)
        audit.noter("compte_réinitialisé", admin, ip_de(requete)[1], cible=ident)
        return {"lien": lien_invitation(requete, jeton), "expire_jours": 7}

    @app.delete("/api/comptes/{ident}")
    def supprimer_compte(ident: str, requete: Request) -> dict:
        admin = exiger_admin(requete)
        if ident == admin:
            raise HTTPException(409, "impossible de supprimer ton propre compte")
        try:
            securite.supprimer(ident)
        except AccesRefuse as e:
            raise HTTPException(409, str(e)) from e
        locuteurs.oublier(ident)
        audit.noter("compte_supprimé", admin, ip_de(requete)[1], cible=ident)
        return {"ok": True}

    @app.post("/api/compte/secours")
    def regenerer_secours(requete: Request) -> dict:
        compte = compte_de(requete)
        if not compte or compte == COMPTE_MACHINE:
            raise HTTPException(409, "à faire depuis ton compte (connexion depuis un autre appareil)")
        audit.noter("codes_secours_regénérés", compte, ip_de(requete)[1])
        return {"codes_de_secours": securite.nouveaux_codes_secours(compte)}

    @app.get("/api/appareil")
    def contexte_appareil(requete: Request) -> dict:
        """Contexte de cet appareil (pour ⚙) : à distance, partagé, enfant, et ce qui est masqué."""
        return contexte_de(requete)

    @app.post("/api/appareil")
    def marquer_appareil(a: AppareilPartage, requete: Request, reponse: Response) -> dict:
        if a.partage:
            reponse.set_cookie(COOKIE_APPAREIL, "partage", max_age=10 * 365 * 86400, httponly=True, samesite="lax",
                               secure=en_https(requete, ip_de(requete)[0]), path="/")
        else:
            reponse.delete_cookie(COOKIE_APPAREIL, path="/")
        auditer(requete, "appareil_partagé" if a.partage else "appareil_personnel")
        return {"ok": True, "partage": a.partage}

    def compte_personnel(requete: Request) -> str:
        compte = compte_de(requete)
        if not compte or compte == COMPTE_MACHINE:
            raise HTTPException(409, "à faire depuis ton compte (connexion depuis un autre appareil)")
        return compte

    @app.get("/api/compte/voix")
    def ma_voix(requete: Request) -> dict:
        compte = compte_personnel(requete)
        return {"echantillons": locuteurs.nombre(compte), "conseille": 3, "disponible": locuteurs.disponible,
                "active": cfg.voix.identification}

    @app.post("/api/compte/voix")
    async def enregistrer_ma_voix(requete: Request) -> dict:
        """Une phrase de quelques secondes (même format que /api/voix/transcrire)."""
        compte = compte_personnel(requete)
        if not locuteurs.disponible:
            raise HTTPException(503, "modèle d'empreinte vocale absent (téléchargé au démarrage du conteneur)")
        audio = await requete.body()
        if not audio or len(audio) > cfg.voix.max_audio_mo * 1e6:
            raise HTTPException(400, "audio vide ou trop long")
        import asyncio as aio

        from .voix import decoder_audio
        try:
            n = await aio.to_thread(lambda: locuteurs.enregistrer(compte, decoder_audio(audio)))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        auditer(requete, "voix_enregistrée", echantillons=n)
        return {"echantillons": n}

    @app.delete("/api/compte/voix")
    def oublier_ma_voix(requete: Request) -> dict:
        compte = compte_personnel(requete)
        locuteurs.oublier(compte)
        auditer(requete, "voix_oubliée")
        return {"ok": True}

    @app.post("/api/compte/nom")
    def changer_mon_nom(n: NouveauNom, requete: Request) -> dict:
        compte = compte_personnel(requete)
        ancien = securite.comptes[compte].get("nom", compte)
        securite.renommer(compte, n.nom)
        retouches = memoire.renommer_compte(compte, ancien, n.nom.strip())
        auditer(requete, "compte_renommé", avant=ancien, apres=n.nom.strip())
        return {"ok": True, "nom": n.nom.strip(), "souvenirs_mis_a_jour": retouches,
                "identifiant": compte}

    @app.get("/api/compte/profil")
    def lire_profil(requete: Request) -> dict:
        compte = compte_de(requete)
        if not compte or compte == COMPTE_MACHINE:
            raise HTTPException(409, "pas de profil pour la machine elle-même")
        return {"profil": securite.profil(compte)}

    @app.post("/api/compte/profil")
    def ecrire_profil(p: Profil, requete: Request) -> dict:
        compte = compte_de(requete)
        if not compte or compte == COMPTE_MACHINE:
            raise HTTPException(409, "pas de profil pour la machine elle-même")
        maj = p.model_dump(exclude_none=True)
        if CHAMPS_ADMIN & set(maj) and not securite.est_admin(compte):
            raise HTTPException(403, "seul un administrateur change le type ou les droits d'un compte")
        auditer(requete, "profil_modifié", champs=sorted(maj))
        return {"profil": securite.definir_profil(compte, maj)}

    @app.post("/api/comptes/{ident}/profil")
    def profil_d_un_compte(ident: str, p: Profil, requete: Request) -> dict:
        admin = exiger_admin(requete)
        if ident not in securite.comptes:
            raise HTTPException(404, "compte inconnu")
        maj = p.model_dump(exclude_none=True)
        audit.noter("profil_modifié", admin, ip_de(requete)[1], cible=ident, champs=sorted(maj))
        return {"profil": securite.definir_profil(ident, maj)}

    @app.get("/api/audit")
    def lire_audit(requete: Request, n: int = 200, type: str = "", compte: str = "") -> dict:
        exiger_admin(requete)
        n = max(1, min(n, 1000))
        if compte:
            return {"evenements": [e for e in audit.derniers(5000, type) if e.get("compte") == compte][:n]}
        return {"evenements": audit.derniers(n, type)}

    # --- droits : un groupe par personne, ses exceptions, et les groupes (page /admin) ---------------------
    @app.get("/api/droits")
    def lire_droits(requete: Request) -> dict:
        exiger_admin(requete)
        return {"catalogue": D.catalogue(), "groupes": securite.liste_groupes(), "comptes": securite.liste(),
                "moi": compte_de(requete)}

    def droit_ou_422(appel):
        try:
            return appel()
        except (D.DroitInvalide, AccesRefuse) as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/groupes")
    def creer_groupe(g: Groupe, requete: Request) -> dict:
        admin = exiger_admin(requete)
        gid = D.ident_groupe(g.nom)
        if gid in securite.groupes:
            raise HTTPException(409, f"le groupe « {g.nom} » existe déjà")
        gid = droit_ou_422(lambda: securite.enregistrer_groupe("", g.nom, g.permissions, g.enfant))
        audit.noter("groupe_créé", admin, ip_de(requete)[1], cible=gid)
        return {"id": gid, "groupes": securite.liste_groupes()}

    @app.put("/api/groupes/{gid}")
    def modifier_groupe(gid: str, g: Groupe, requete: Request) -> dict:
        admin = exiger_admin(requete)
        if gid not in securite.groupes:
            raise HTTPException(404, "groupe inconnu")
        droit_ou_422(lambda: securite.enregistrer_groupe(gid, g.nom, g.permissions, g.enfant))
        audit.noter("groupe_modifié", admin, ip_de(requete)[1], cible=gid)
        return {"id": gid, "groupes": securite.liste_groupes()}

    @app.delete("/api/groupes/{gid}")
    def supprimer_groupe(gid: str, requete: Request) -> dict:
        admin = exiger_admin(requete)
        droit_ou_422(lambda: securite.supprimer_groupe(gid))
        audit.noter("groupe_supprimé", admin, ip_de(requete)[1], cible=gid)
        return {"ok": True}

    @app.post("/api/comptes/{ident}/droits")
    def droits_d_un_compte(ident: str, dc: DroitsCompte, requete: Request) -> dict:
        admin = exiger_admin(requete)
        if ident not in securite.comptes:
            raise HTTPException(404, "compte inconnu")
        if ident == admin and dc.groupe is not None and dc.groupe != D.ADMIN:
            raise HTTPException(409, "impossible de te sortir toi-même du groupe Admin")
        droit_ou_422(lambda: securite.definir_droits(ident, dc.groupe, dc.permissions, dc.retraits))
        audit.noter("droits_modifiés", admin, ip_de(requete)[1], cible=ident,
                    champs=sorted(dc.model_dump(exclude_none=True)))
        return next(c for c in securite.liste() if c["id"] == ident)

    @app.get("/api/comptes/{ident}/detail")
    def detail_d_un_compte(ident: str, requete: Request) -> dict:
        """Ce qui appartient à cette personne : rappels, règles, routines, et son journal."""
        exiger_admin(requete)
        if ident not in securite.comptes:
            raise HTTPException(404, "compte inconnu")
        return {"rappels": [{k: r[k] for k in ("id", "quand", "texte")} for r in rappels.a_venir()
                            if r.get("compte") == ident],
                "regles": [{k: r.get(k) for k in ("id", "texte", "active")} for r in regles.liste
                           if r.get("compte") == ident],
                "routines": [r["nom"] for r in routines.liste.values() if r.get("compte") == ident],
                "journal": [e for e in audit.derniers(5000) if e.get("compte") == ident][:40]}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # --- thème, voix et écoute : à chaque personne (sinon les valeurs de la maison, réglées par l'admin) ---
    def prefs(compte: str | None) -> dict:
        maison_ = {"theme": cfg.identite.theme, "voix": cfg.voix.voix_actuelle(), "fenetre_s": cfg.voix.fenetre_s,
                   "toujours_a_l_ecoute": cfg.voix.toujours_a_l_ecoute}
        propres = ((securite.comptes.get(compte or "") or {}).get("assistant")) or {}
        return {**maison_, **{k: v for k, v in propres.items() if k in maison_ and v is not None}}

    def nom_pour(theme: str) -> str:
        return "Hal" if theme == "hal" else cfg.identite.nom

    def wake_pour(theme: str) -> str:
        return "hal" if theme == "hal" else (cfg.identite.wake_word or cfg.identite.nom).strip().lower()

    def variantes_pour(theme: str) -> list[str]:
        return ["hal", "hall", "halle", "hale"] if theme == "hal" else [wake_pour(theme)]

    def ecrire_prefs(requete: Request, valeurs: dict) -> dict:
        """Les réglages d'une personne vont dans son compte ; la machine elle-même règle ceux de la maison."""
        compte = compte_de(requete)
        if compte and compte in securite.comptes:
            c = securite.comptes[compte]
            c["assistant"] = {**(c.get("assistant") or {}), **valeurs}
            securite._ecrire()
        else:
            if "theme" in valeurs:
                cfg.identite.theme = valeurs["theme"]
            if "voix" in valeurs:
                cfg.voix.voix = valeurs["voix"]
            if "fenetre_s" in valeurs:
                cfg.voix.fenetre_s = reveil.fenetre_s = valeurs["fenetre_s"]
            if "toujours_a_l_ecoute" in valeurs:
                cfg.voix.toujours_a_l_ecoute = reveil.toujours = valeurs["toujours_a_l_ecoute"]
            if persister:
                for section, cles in (("identite", ("theme",)), ("voix", ("voix", "fenetre_s", "toujours_a_l_ecoute"))):
                    morceau = {k: valeurs[k] for k in cles if k in valeurs}
                    if morceau:
                        enregistrer_reglages(section, morceau)
        return prefs(compte)

    @app.get("/api/info")
    def info(requete: Request) -> dict:
        ident = cfg.identite
        p = prefs(compte_de(requete))
        return {
            "nom": nom_pour(p["theme"]),
            "nom_configure": ident.nom,   # le nom du thème « orbe » (bouton de Réglages)
            "wake_word": wake_pour(p["theme"]),
            "theme": p["theme"],
            "langue": ident.langue,
            "version": __version__,
        }

    @app.get("/api/fournisseurs")
    async def fournisseurs() -> dict:
        return {"roles": reg.roles, "fournisseurs": await reg.etat()}

    @app.get("/api/home-assistant")
    async def etat_ha(requete: Request) -> dict:
        exiger_admin(requete)          # adresse, version : une intégration, réservée aux administrateurs
        return await ha.etat()

    @app.get("/api/maison")
    async def etat_maison(requete: Request) -> dict:
        exiger_admin(requete)
        if ha.configure and not maison.inventaire.entites:
            await maison.inventaire.obtenir()
        return {"inventaire": maison.inventaire.resume(), "alias": maison.alias.liste()}

    @app.post("/api/maison/rafraichir")
    async def rafraichir_maison(requete: Request) -> dict:
        exiger_admin(requete)
        await maison.inventaire.obtenir(forcer=True)
        return maison.inventaire.resume()

    async def cartes_sures(entity_ids, action: str | None = None) -> list[dict]:
        """Cartes des appareils d'une réponse ; une panne de Home Assistant ne casse jamais la réponse."""
        if not entity_ids:
            return []
        try:
            return await cartes_pour(maison, list(entity_ids), action)
        except Exception as e:  # noqa: BLE001 - la carte est un bonus
            journal.info("cartes impossibles : %s", e)
            return []

    @app.post("/api/maison/carte")
    async def geste_carte(g: ReglageCarte, requete: Request) -> dict:
        """Interrupteur, curseur ou couleur d'une carte : agit sans passer par un modèle."""
        if not ha.configure:
            raise HTTPException(503, "Home Assistant n'est pas configuré")
        exiger(requete, "maison.voir")
        masques.set(frozenset(contexte_de(requete)["masques"]))
        interdits.set(interdits_de(compte_de(requete), g.conversation))
        demande_en_cours.set(("carte", texte_conversation.get(g.conversation, "")))
        champs = g.model_dump(exclude={"entity_id", "conversation"}, exclude_none=True)
        try:
            r, c = await regler_carte(maison, g.entity_id, g.conversation, **champs)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if r is None:
            raise HTTPException(404, "appareil inconnu ou non autorisé")
        auditer(requete, "action_maison", par="carte", entites=[g.entity_id], resultat=r.texte[:200], **champs)
        if r.detail.get("echecs") or r.detail.get("sensibles") or not r.texte.startswith("C'est"):
            raise HTTPException(409, r.texte or "Rien n'a été fait.")
        return {"ok": True, "texte": r.texte, "carte": c}

    @app.post("/api/maison/carte/groupe")
    async def geste_groupe(g: ReglageGroupe, requete: Request) -> dict:
        """Carte « groupe » : la même couleur (ou intensité, blanc…) pour les appareils choisis, en une action."""
        if not ha.configure:
            raise HTTPException(503, "Home Assistant n'est pas configuré")
        exiger(requete, "maison.voir")
        masques.set(frozenset(contexte_de(requete)["masques"]))
        interdits.set(interdits_de(compte_de(requete), g.conversation))
        demande_en_cours.set(("carte", texte_conversation.get(g.conversation, "")))
        champs = g.model_dump(exclude={"entity_id", "entity_ids", "conversation"}, exclude_none=True)
        try:
            r, cartes, ecartes = await regler_plusieurs(maison, g.entity_ids, g.conversation, **champs)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if r is None:
            raise HTTPException(404 if not ecartes else 409,
                                "Aucun de ces appareils n'accepte ce réglage." if ecartes else "appareils inconnus ou non autorisés")
        auditer(requete, "action_maison", par="carte", entites=[c["entity_id"] for c in cartes],
                resultat=r.texte[:200], **champs)
        if r.detail.get("echecs") or r.detail.get("sensibles") or not r.texte.startswith("C'est"):
            raise HTTPException(409, r.texte or "Rien n'a été fait.")
        return {"ok": True, "texte": r.texte, "cartes": cartes, "ecartes": ecartes}

    @app.post("/api/maison/carte/annuler")
    async def annuler_carte(c: Conv, requete: Request) -> dict:
        """Bouton « Annuler » d'une carte : comme dire « annule » dans la même conversation."""
        masques.set(frozenset(contexte_de(requete)["masques"]))
        interdits.set(interdits_de(compte_de(requete), c.conversation))
        r = await maison.annuler(c.conversation)
        auditer(requete, "action_maison", par="carte", action="annuler", resultat=r.texte[:200])
        return {"ok": r.traite, "texte": r.texte, "cartes": await cartes_sures(r.detail.get("entites"))}

    @app.delete("/api/maison/alias/{phrase}")
    def oublier_alias(phrase: str, requete: Request) -> dict:
        exiger_admin(requete)
        if not maison.alias.oublier(phrase):
            raise HTTPException(404, "alias inconnu")
        auditer(requete, "alias_oublié", phrase=phrase)
        return {"ok": True}

    # --- API interne : seulement pour le serveur MCP « maison » (jeton requis) -----------
    @app.get("/interne/maison/appareils", include_in_schema=False)
    async def interne_appareils(filtre: str = "", conversation: str = "",
                                x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        exiger_conv(conversation, "maison.voir")
        masques.set(masques_conversation.get(conversation, frozenset()))
        return await maison.appareils(filtre)

    @app.get("/interne/maison/etat/{entity_id}", include_in_schema=False)
    async def interne_etat(entity_id: str, conversation: str = "", x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        exiger_conv(conversation, "maison.voir")
        masques.set(masques_conversation.get(conversation, frozenset()))
        e = await maison.etat_detaille(entity_id)
        if e is None:
            raise HTTPException(404, "appareil inconnu ou non autorisé")
        cartes_conversation.setdefault(conversation, []).append(entity_id)
        return e

    @app.get("/api/actions")
    def historique_actions(requete: Request, limite: int = 50) -> dict:
        exiger_revelation(requete, "historique")
        exiger(requete, "maison.historique")
        h = maison.historique
        return {"actions": h.liste(max(1, min(limite, 500))) if h else []}

    @app.post("/api/actions/{ident}/annuler")
    async def annuler_action(ident: str, requete: Request) -> dict:
        exiger_revelation(requete, "historique")
        exiger(requete, "maison.historique")
        interdits.set(interdits_de(compte_de(requete)))
        ok, message = await maison.annuler_action(ident)
        if not ok:
            raise HTTPException(404 if message == "action inconnue" else 409, message)
        auditer(requete, "action_maison", par="historique", action="annuler", cible=ident, resultat=message)
        return {"ok": True, "message": message}

    @app.get("/api/appareils/etat")
    async def etat_des_appareils(requete: Request) -> dict:
        exiger_revelation(requete, "historique")
        exiger(requete, "maison.historique")
        return {"appareils": await maison.etats_appareils()}

    @app.get("/interne/unraid", include_in_schema=False)
    async def interne_unraid(conversation: str = "", x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        exiger_conv(conversation, "serveur.etat")
        if not isinstance(docker, UnraidDocker):
            raise HTTPException(503, "l'état du serveur demande l'API Unraid (docker.unraid_url)")
        return {**(await docker_ou_erreur(serveur_unraid())), **(await mises_a_jour_sures())}

    @app.post("/interne/docker/redemarrer", include_in_schema=False)
    async def interne_redemarrer(r: DemandeRedemarrage, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        compte, _ = compte_conversation.get(r.conversation, ("", ""))
        exiger_conv(r.conversation, PERMISSION_DOCKER[r.action])
        if not isinstance(docker, UnraidDocker) or not cfg.docker.cle_unraid_actions():
            raise HTTPException(503, "redémarrage non configuré (2e clé Unraid « actions Docker »)")
        c = await docker_ou_erreur(docker.trouver(r.nom))
        if not c:
            raise HTTPException(404, f"conteneur « {r.nom} » introuvable ou ambigu")
        redemarrage_attente[r.conversation] = (c["nom"], compte, time.monotonic(), r.action)
        question = {"redemarrer": "Je redémarre", "demarrer": "Je démarre", "arreter": "J'arrête",
                    "mettre_a_jour": "Je mets à jour"}[r.action]
        return {"en_attente": f"demande « {question} {c['nom']} ? » ; rien n'est fait avant le oui",
                "etat_actuel": statut_lisible(c.get("statut"), c.get("etat"))}

    @app.post("/interne/telegram", include_in_schema=False)
    async def interne_telegram(m: MessageTelegram, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        exiger_conv(m.conversation, "telegram.envoyer")
        qui = proprietaire_conv(m.conversation)
        chat = chat_telegram(qui)
        if not telegram.jeton or not chat:
            if not telegram.jeton or securite.est_admin(qui or COMPTE_MACHINE):
                raise HTTPException(503, "Telegram non configuré (jeton du bot et chat dans Réglages)")
            raise HTTPException(409, "son Telegram n'est pas relié : à faire dans ⚙ Mon compte")
        try:
            await telegram.envoyer(m.texte, chat)
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from e
        return {"ok": True, "envoye": m.texte}

    @app.get("/api/unraid")
    async def etat_unraid(requete: Request) -> dict:
        exiger(requete, "serveur.etat")
        if not isinstance(docker, UnraidDocker):
            return {"configure": False}
        return {"configure": True, **(await docker_ou_erreur(serveur_unraid())), **(await mises_a_jour_sures())}

    async def mises_a_jour_sures() -> dict:
        """Pour l'état du serveur : la liste des mises à jour est un bonus (absente sur un vieil Unraid)."""
        try:
            return {"mises_a_jour": await docker.mises_a_jour()}
        except Exception as e:  # noqa: BLE001
            journal.info("mises à jour Docker illisibles : %s", e)
            return {}

    @app.post("/api/telegram/{action}")
    async def essai_telegram(action: Literal["test", "trouver"], requete: Request) -> dict:
        exiger_admin(requete)
        if not telegram.jeton:
            raise HTTPException(409, "jeton du bot manquant (enregistre-le, puis redémarre l'assistant)")
        try:
            if action == "trouver":
                chat = await telegram.trouver_chat()
                if not chat:
                    raise HTTPException(404, "aucun message reçu : envoie « /start » à ton bot dans Telegram, puis réessaie")
                telegram.chat_id = chat["id"]
                await ecrire_reglages_telegram(chat["id"])
                await telegram.envoyer(f"{cfg.identite.nom_effectif} est relié à ce chat. Les alertes arriveront ici.")
                return {"ok": True, "chat_id": chat["id"], "nom": chat["nom"]}
            await telegram.envoyer(f"{cfg.identite.nom_effectif} : message de test.")
            return {"ok": True}
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from e

    async def ecrire_reglages_telegram(chat_id: str) -> None:
        cfg.telegram.chat_id = chat_id
        if persister:
            R.appliquer({"telegram": {"chat_id": chat_id}}, defauts_bruts())

    @app.get("/interne/docker/conteneurs", include_in_schema=False)
    async def interne_conteneurs(conversation: str = "", x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        exiger_conv(conversation, "serveur.etat")
        return [pour_modele(c) for c in await docker_ou_erreur(docker.conteneurs())]

    @app.get("/interne/docker/diagnostic", include_in_schema=False)
    async def interne_diagnostic(nom: str, conversation: str = "", x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        exiger_conv(conversation, "serveur.diagnostic")
        d = await docker_ou_erreur(docker.diagnostic(nom))
        d["causes_probables"] = causes_probables(d)
        d["redemarrage_possible"] = isinstance(docker, UnraidDocker) and bool(cfg.docker.cle_unraid_actions())
        return pour_modele(d)

    def pour_modele(c: dict) -> dict:
        """États Docker en français pour le modèle : il les répète tels quels (« Up Less than a second »)."""
        c = dict(c)
        if "statut" in c or "etat" in c:
            c["statut"] = statut_lisible(c.get("statut"), c.get("etat"))
        if c.get("etat"):
            c["etat"] = ETATS_FR.get(str(c["etat"]).lower(), c["etat"])
        if c.get("sante"):
            c["sante"] = {"healthy": "bonne", "unhealthy": "mauvaise", "starting": "contrôle en cours"}.get(c["sante"], c["sante"])
        return c

    async def docker_ou_erreur(appel):
        if not docker.configure:
            raise HTTPException(503, "diagnostic Docker non configuré (docker.url)")
        try:
            return await appel
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except PermissionError as e:
            journal.info("Docker : %s", e)
            raise HTTPException(502, str(e)) from e
        except Exception as e:  # noqa: BLE001 - serveur injoignable, certificat, réponse inattendue…
            journal.info("Docker injoignable : %s : %s", e.__class__.__name__, str(e)[:300])
            raise HTTPException(502, f"Docker injoignable : {e.__class__.__name__} : {str(e)[:200]}") from e

    taches_fond: set[asyncio.Task] = set()

    async def mise_a_jour_suivie(nom: str, intervalle: float = 30, essais: int = 20) -> dict:
        """Met à jour l'image d'un conteneur. Télécharger une image prend souvent plus d'une minute, et le
        proxy d'Unraid coupe la requête à 60 s (504) alors que la mise à jour continue : dans ce cas on
        regarde toutes les 30 s (jusqu'à 10 min) si le conteneur est encore « à mettre à jour ».
        Renvoie {"ok", "nom", "etat", "statut"} ou {"ok": False, "raison"}."""
        try:
            r = await docker.mettre_a_jour(nom, cfg.docker.cle_unraid_actions())
            return {"ok": True, **r}
        except Exception as e:  # noqa: BLE001
            journal.info("mise à jour de %s : %s : %s", nom, e.__class__.__name__, str(e)[:300])
            if not attente_depassee(e):
                return {"ok": False, "nom": nom, "raison": erreur_docker_courte(e)}
        for _ in range(essais):
            await asyncio.sleep(intervalle)
            try:
                if nom in await docker.mises_a_jour():
                    continue
                c = await docker.trouver(nom) or {}
                return {"ok": True, "nom": c.get("nom", nom), "etat": (c.get("etat") or "").lower(),
                        "statut": c.get("statut", "")}
            except Exception as e:  # noqa: BLE001 - Unraid encore occupé : on réessaie
                journal.info("suivi de la mise à jour de %s : %s", nom, e.__class__.__name__)
        return {"ok": False, "nom": nom, "raison": "le serveur Unraid ne répond plus, regarde l'onglet Docker"}

    def phrase_mise_a_jour(r: dict) -> str:
        if not r["ok"]:
            return f"La mise à jour de {r['nom']} n'a pas abouti : {r['raison']}."
        if r.get("etat") in ("running", ""):
            return f"C'est fait, {r['nom']} est à jour et tourne."
        return f"C'est fait, {r['nom']} est à jour, mais il est {statut_lisible(r.get('statut'), r.get('etat'))}."

    def lancer_mise_a_jour(nom: str, conteneur: str, ident_annonce: str = "") -> None:
        """En arrière-plan : la réponse part tout de suite, le résultat arrive en annonce (dite à voix haute)."""
        noms = {conteneur.lower(), (nom or conteneur).lower()}
        for n in noms:   # pendant la mise à jour, puis 3 min après (redémarrage) : pas d'alerte « hors service »
            conteneurs_en_travaux[n] = time.time() + 20 * 60

        async def suivre() -> None:
            try:
                r = await mise_a_jour_suivie(conteneur)
            finally:
                for n in noms:
                    conteneurs_en_travaux[n] = time.time() + 180
            if r["ok"]:
                annonces.resoudre(f"docker:{r['nom']}")
                for a in annonces.liste:
                    if (a.get("cible") or {}).get("conteneur") == r["nom"] and a.get("cle", "").startswith("maj:"):
                        annonces.resoudre(a["cle"])
            elif ident_annonce:
                annonces.annuler_decision(ident_annonce)
            journal.info("mise à jour de %s : %s", conteneur, "faite" if r["ok"] else r["raison"])
            await signaler(phrase_mise_a_jour(r), "info", "mises à jour", f"majfaite:{conteneur}:{time.time():.0f}",
                           cfg.telegram.mises_a_jour)
        t = asyncio.create_task(suivre())
        taches_fond.add(t)
        t.add_done_callback(taches_fond.discard)

    app.state.mise_a_jour_suivie = mise_a_jour_suivie
    app.state.taches_fond = taches_fond

    async def agenda_ou_erreur(appel):
        try:
            return await appel
        except PermissionError as e:
            raise HTTPException(502, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"agenda injoignable : {e.__class__.__name__}") from e

    # --- agendas : celui de la famille (Réglages) et celui de chaque personne (⚙ Mon compte) ---------------
    def agenda_perso(compte: str | None) -> Agenda | None:
        a = ((securite.comptes.get(compte or "") or {}).get("agenda")) or {}
        return Agenda(a["url"], a.get("utilisateur", ""), a.get("mdp", "")) if a.get("url") else None

    def agendas_de(compte: str | None, perms) -> list[tuple[str, Agenda]]:
        """Les agendas que cette personne lit : [("famille", …), ("perso", …)] selon ses droits."""
        sortie = []
        if agenda.configure and "agenda.lire" in perms:
            sortie.append(("famille", agenda))
        perso = agenda_perso(compte) if "agenda.personnel" in perms else None
        if perso:
            sortie.append(("perso", perso))
        return sortie

    def evenements_json(evts: list[dict], nom: str = "") -> list[dict]:
        return [{"titre": e.get("titre", ""), "debut": e["debut"].isoformat(), "journee": e.get("journee", False),
                 "lieu": e.get("lieu", ""), "texte": decrire(e), "ref": e.get("href", ""),
                 **({"agenda": nom} if nom else {})} for e in evts]

    async def prochains(jours: float, ag: Agenda | None = None) -> list[dict]:
        from datetime import datetime as dt, timedelta
        maintenant = dt.now().astimezone()
        debut = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        return await (ag or agenda).evenements(debut, maintenant + timedelta(days=jours))

    async def lire_agendas(ags: list[tuple[str, Agenda]], jours: float) -> list[dict]:
        tous = []
        for nom, ag in ags:
            tous += evenements_json(await agenda_ou_erreur(prochains(jours, ag)), nom if len(ags) > 1 else "")
        return sorted(tous, key=lambda e: e["debut"])

    @app.get("/interne/agenda", include_in_schema=False)
    async def interne_agenda(jours: int = 7, conversation: str = "", x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        exiger_conv(conversation, "agenda.lire", "agenda.personnel")
        ags = agendas_de(proprietaire_conv(conversation), droits_conv(conversation))
        if not ags:
            raise HTTPException(503, "aucun agenda : ni familial (Réglages) ni personnel (⚙ Mon compte)")
        return await lire_agendas(ags, max(1, min(jours, 60)))

    def agenda_pour_ecrire(conversation: str, choix: str) -> tuple[str, Agenda]:
        """« perso » (par défaut s'il existe) ou « famille » (droit d'écrire dans l'agenda familial)."""
        d = droits_conv(conversation)
        perso = agenda_perso(proprietaire_conv(conversation)) if "agenda.personnel" in d else None
        if choix != "famille" and perso:
            return "perso", perso
        if choix == "perso":
            raise HTTPException(409, "pas d'agenda personnel : à brancher dans ⚙ Mon compte")
        if "agenda.ecrire" not in d:
            raise HTTPException(403, "pas permis pour cette personne : " + D.PERMISSIONS["agenda.ecrire"])
        if not agenda.configure:
            raise HTTPException(503, "agenda familial non configuré (Réglages › Services › Agenda)")
        return "famille", agenda

    @app.post("/interne/agenda", include_in_schema=False)
    async def interne_ajouter(e: NouvelEvenement, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        exiger_conv(e.conversation, "agenda.ecrire", "agenda.personnel")
        nom, ag = agenda_pour_ecrire(e.conversation, e.agenda)
        from datetime import datetime as dt
        try:
            debut = dt.fromisoformat(e.debut).astimezone()
            fin = dt.fromisoformat(e.fin).astimezone() if e.fin else None
        except ValueError as x:
            raise HTTPException(422, "date invalide (format 2026-11-12T14:30)") from x
        uid = await agenda_ou_erreur(ag.ajouter(e.titre, debut, fin, e.lieu, cfg.agenda.rappel_min, e.journee))
        audit.noter("agenda_ajout", proprietaire_conv(e.conversation), "", par="claude", titre=e.titre, debut=e.debut,
                    agenda=nom)
        return {"ok": True, "uid": uid, "agenda": nom,
                "resume": decrire({"debut": debut, "titre": e.titre, "lieu": e.lieu, "journee": e.journee})}

    @app.post("/interne/agenda/supprimer", include_in_schema=False)
    async def interne_supprimer(s_: SuppressionEvenement, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        d = droits_conv(s_.conversation)
        exiger_conv(s_.conversation, "agenda.ecrire", "agenda.personnel")
        qui = proprietaire_conv(s_.conversation)
        ecrivables = [(n, a) for n, a in agendas_de(qui, d) if n == "perso" or "agenda.ecrire" in d]
        for _nom, ag in ecrivables:
            e = next((x for x in await agenda_ou_erreur(prochains(366, ag)) if x.get("href") == s_.ref), None)
            if e:
                break
        else:
            raise HTTPException(404, "événement introuvable dans l'agenda (ref inconnue)")
        await agenda_ou_erreur(ag.supprimer(s_.ref))
        compte, ip = compte_conversation.get(s_.conversation, ("", ""))
        # le journal garde de quoi le recréer si c'était une erreur
        audit.noter("agenda_suppression", compte, ip, par="claude", titre=e.get("titre", ""),
                    debut=e["debut"].isoformat(), lieu=e.get("lieu", ""))
        return {"ok": True, "supprime": decrire(e)}

    @app.get("/api/agenda")
    async def lire_agenda(requete: Request, jours: int = 7) -> dict:
        exiger_revelation(requete, "historique")
        compte = exiger(requete, "agenda.lire", "agenda.personnel")
        ags = agendas_de(compte, securite.permissions(compte))
        if not ags:
            return {"configure": False, "evenements": []}
        return {"configure": True, "evenements": await lire_agendas(ags, max(1, min(jours, 60)))}

    # --- ⚙ Mon compte : son agenda et son Telegram ---------------------------------------------------------
    @app.get("/api/compte/outils")
    def mes_outils(requete: Request) -> dict:
        compte = compte_personnel(requete)
        c = securite.comptes[compte]
        a = c.get("agenda") or {}
        # « famille » / « maison » : ce que la personne reçoit aussi sans rien brancher (l'agenda familial,
        # et pour un administrateur le Telegram de la maison), pour ne pas confondre avec Réglages › Services
        return {"agenda": {"permis": securite.peut(compte, "agenda.personnel"), "url": a.get("url", ""),
                           "utilisateur": a.get("utilisateur", ""), "mdp_defini": bool(a.get("mdp")),
                           "famille": agenda.configure and securite.peut(compte, "agenda.lire")},
                "telegram": {"permis": securite.peut(compte, "telegram.recevoir"), "bot": bool(telegram.jeton),
                             "relie": (c.get("telegram") or {}).get("nom") or ("oui" if c.get("telegram") else ""),
                             "maison": bool(telegram.chat_id) and securite.est_admin(compte)
                             and not (c.get("telegram") or {}).get("id")}}

    @app.post("/api/compte/agenda")
    async def mon_agenda(a: MonAgenda, requete: Request) -> dict:
        compte = compte_personnel(requete)
        exiger(requete, "agenda.personnel")
        c = securite.comptes[compte]
        if not a.url.strip():
            c.pop("agenda", None)
            securite._ecrire()
            auditer(requete, "agenda_personnel", action="débranché")
            return {"ok": True, "branche": False}
        ancien = c.get("agenda") or {}
        nouveau = {"url": a.url.strip(), "utilisateur": a.utilisateur.strip(),
                   "mdp": a.mot_de_passe if a.mot_de_passe else ancien.get("mdp", "")}
        essai = Agenda(nouveau["url"], nouveau["utilisateur"], nouveau["mdp"])
        try:
            n = len(await prochains(7, essai))
        except PermissionError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"agenda injoignable : {e.__class__.__name__}") from e
        c["agenda"] = nouveau
        securite._ecrire()
        auditer(requete, "agenda_personnel", action="branché")
        return {"ok": True, "branche": True, "evenements_7_jours": n}

    codes_telegram: dict[str, tuple[str, float]] = {}       # compte -> (code à envoyer au bot, expiration)

    @app.post("/api/compte/telegram/code")
    async def code_telegram(requete: Request) -> dict:
        compte = compte_personnel(requete)
        exiger(requete, "telegram.recevoir")
        if not telegram.jeton:
            raise HTTPException(409, "pas de bot Telegram : un administrateur doit le brancher (Réglages › Services)")
        code = f"{secrets.randbelow(10 ** 6):06d}"
        codes_telegram[compte] = (code, time.monotonic() + 600)
        try:
            bot = await telegram.nom_du_bot()
        except Exception:  # noqa: BLE001 - le nom du bot n'est qu'une aide
            bot = ""
        return {"code": code, "bot": bot, "expire_min": 10}

    @app.post("/api/compte/telegram/relier")
    async def relier_telegram(requete: Request) -> dict:
        compte = compte_personnel(requete)
        exiger(requete, "telegram.recevoir")
        code, fin = codes_telegram.get(compte, ("", 0))
        if not code or time.monotonic() > fin:
            raise HTTPException(409, "demande d'abord un code (valable 10 minutes)")
        try:
            chat = await telegram.trouver_chat(code)
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from e
        if not chat:
            raise HTTPException(404, f"code pas encore reçu : envoie « {code} » à ton bot dans Telegram, puis réessaie")
        codes_telegram.pop(compte, None)
        securite.comptes[compte]["telegram"] = chat
        securite._ecrire()
        try:
            await telegram.envoyer(f"{cfg.identite.nom_effectif} : ton Telegram est relié. Tes rappels arriveront ici.",
                                   chat["id"])
        except RuntimeError:
            pass
        auditer(requete, "telegram_relié")
        return {"ok": True, "nom": chat["nom"]}

    @app.delete("/api/compte/telegram")
    def delier_telegram(requete: Request) -> dict:
        compte = compte_personnel(requete)
        securite.comptes[compte].pop("telegram", None)
        securite._ecrire()
        auditer(requete, "telegram_délié")
        return {"ok": True}

    # --- mémoire (écran ⚙ Mémoire) ---------------------------------------------------------------------
    def compte_memoire(requete: Request) -> str:
        exiger_revelation(requete, "historique")       # ni sur un appareil partagé, ni pour un enfant
        compte = compte_de(requete)
        if not compte:
            raise HTTPException(401, "connexion requise")
        return compte

    def compte_souvenirs(requete: Request) -> str:
        compte = compte_memoire(requete)
        exiger(requete, "memoire.utiliser")
        return compte

    def portee_permise(requete: Request, portee: str | None) -> None:
        if portee == "foyer":
            exiger(requete, "memoire.foyer")

    def souvenir_a_moi(ident: int, compte: str, corbeille: bool = False) -> dict:
        try:
            sv = memoire.lire(ident)
        except KeyError as e:
            raise HTTPException(404, "souvenir inconnu") from e
        if not memoire.visible(sv, compte, securite.est_admin(compte)) or (sv["statut"] == "supprime" and not corbeille):
            raise HTTPException(404, "souvenir inconnu")
        return sv

    def souvenir_json(sv: dict, moi: str | None = None) -> dict:
        """Un souvenir pour l'écran : qui l'a ajouté (nom du compte), d'où il vient, ce qu'il remplace ;
        avec `moi`, s'il est à cette personne (l'administrateur voit aussi les souvenirs privés des autres)."""
        c = securite.comptes.get(sv["compte"]) or {}
        o = {**sv, "auteur": c.get("nom", sv["compte"]) if sv["compte"] != COMPTE_MACHINE else cfg.identite.nom_effectif,
             "origine": "écran" if sv["source"] == "écran" else "conversation",
             **({"a_moi": sv["compte"] == moi} if moi else {})}
        if sv.get("remplace"):
            try:
                o["remplace_texte"] = memoire.lire(sv["remplace"])["texte"]
            except KeyError:
                pass
        return o

    @app.get("/api/memoire")
    def lister_memoire(requete: Request, corbeille: bool = False) -> dict:
        """Souvenirs validés et à valider ; avec ?corbeille=1, les oubliés et refusés (restaurables)."""
        compte = compte_souvenirs(requete)
        if corbeille:
            return {"souvenirs": [souvenir_json(x, compte) for x in memoire.liste(compte, ("supprime", "refuse"),
                                                                          securite.est_admin(compte))[:200]]}
        liste = memoire.liste(compte, tous=securite.est_admin(compte))      # l'administrateur voit tout
        return {"souvenirs": [souvenir_json(x, compte) for x in liste],
                "a_valider": sum(1 for x in liste if x["statut"] == "propose")}

    @app.post("/api/memoire/valider-tout")
    def valider_tous_les_souvenirs(requete: Request) -> dict:
        compte = compte_souvenirs(requete)
        n = 0
        for sv in memoire.liste(compte, ("propose",), securite.est_admin(compte)):
            memoire.changer_statut(sv["id"], "valide", compte)
            auditer(requete, "souvenir_validé", cible=str(sv["id"]))
            n += 1
        return {"ok": True, "valides": n}

    @app.post("/api/memoire")
    def ajouter_souvenir(n: NouveauSouvenir, requete: Request) -> dict:
        compte = compte_souvenirs(requete)
        portee_permise(requete, n.portee)
        try:
            sv = memoire.ajouter(n.texte, n.portee, compte, "valide", "écran", par=compte)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        auditer(requete, "souvenir_ajouté", portee=n.portee)
        return sv

    @app.post("/api/memoire/{ident}/{decision}")
    def decider_souvenir(ident: int, decision: Literal["valider", "refuser", "restaurer"], requete: Request) -> dict:
        compte = compte_souvenirs(requete)
        if decision == "restaurer":
            souvenir_a_moi(ident, compte, corbeille=True)
            try:
                sv = memoire.restaurer(ident, compte)
            except ValueError as e:
                raise HTTPException(409, str(e)) from e
            auditer(requete, "souvenir_restauré", cible=str(ident))
            return souvenir_json(sv)
        souvenir_a_moi(ident, compte)
        sv = memoire.changer_statut(ident, "valide" if decision == "valider" else "refuse", compte)
        auditer(requete, "souvenir_" + ("validé" if decision == "valider" else "refusé"), cible=str(ident))
        return souvenir_json(sv)

    @app.patch("/api/memoire/{ident}")
    def modifier_souvenir(ident: int, m: ModifSouvenir, requete: Request) -> dict:
        compte = compte_souvenirs(requete)
        portee_permise(requete, m.portee)
        souvenir_a_moi(ident, compte)
        try:
            sv = memoire.modifier(ident, m.texte, m.portee, compte)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        auditer(requete, "souvenir_modifié", cible=str(ident))
        return souvenir_json(sv)

    @app.delete("/api/memoire/{ident}")
    def supprimer_souvenir(ident: int, requete: Request) -> dict:
        compte = compte_souvenirs(requete)
        souvenir_a_moi(ident, compte)
        memoire.changer_statut(ident, "supprime", compte)
        auditer(requete, "souvenir_supprimé", cible=str(ident))
        return {"ok": True}

    @app.get("/api/memoire/{ident}/historique")
    def historique_souvenir(ident: int, requete: Request) -> dict:
        compte = compte_souvenirs(requete)
        souvenir_a_moi(ident, compte)
        noms = {c: v.get("nom", c) for c, v in securite.comptes.items()} | {COMPTE_MACHINE: cfg.identite.nom_effectif}
        return {"historique": [{**h, "compte": noms.get(h["compte"], h["compte"])} for h in memoire.historique(ident)]}

    # --- habitudes et routines ------------------------------------------------------------------------
    @app.post("/interne/memoire", include_in_schema=False)
    def interne_souvenir(s_: SouvenirPropose, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        compte, _ = compte_conversation.get(s_.conversation, ("", ""))
        voix_de = locuteur_conversation.get(s_.conversation)
        if voix_de and time.monotonic() - voix_de[1] < 120:
            compte = voix_de[0]
        if not compte:
            raise HTTPException(409, "personne d'identifié pour ce souvenir")
        exiger_conv(s_.conversation, "memoire.utiliser")
        if s_.portee == "foyer" and "memoire.foyer" not in droits_conv(s_.conversation):
            s_ = s_.model_copy(update={"portee": "prive"})     # pas le droit au foyer : gardé pour elle
        if s_.remplace is not None:
            try:
                ancien = memoire.lire(s_.remplace)
            except KeyError as e:
                raise HTTPException(404, "souvenir à remplacer inconnu") from e
            if not memoire.visible(ancien, compte):
                raise HTTPException(404, "souvenir à remplacer inconnu")
        try:
            sv = memoire.ajouter(s_.texte, s_.portee, compte, "propose", "claude", s_.conversation, remplace=s_.remplace)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"ok": True, "en_attente": "la personne doit répondre oui ou non", "souvenir": sv["texte"]}

    def personne_de(conversation: str) -> str:
        """À qui sont les rappels de cette conversation : la voix reconnue, sinon le compte connecté."""
        compte = personne_conversation.get(conversation) or compte_conversation.get(conversation, ("", ""))[0]
        return "" if compte == COMPTE_MACHINE else compte

    @app.post("/interne/rappels", include_in_schema=False)
    def interne_creer_rappel(n: NouveauRappel, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        exiger_conv(n.conversation, "rappels.utiliser")
        from datetime import datetime as dt
        try:
            quand = dt.fromisoformat(n.quand)
            r = rappels.ajouter(n.texte, quand.astimezone() if quand.tzinfo is None else quand,
                                personne_de(n.conversation), n.conversation)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"ok": True, "id": r["id"], "quand": r["quand"], "texte": r["texte"]}

    @app.get("/interne/rappels", include_in_schema=False)
    def interne_lister_rappels(conversation: str = "", x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        compte = personne_de(conversation)
        return [{k: r[k] for k in ("id", "quand", "texte")} for r in rappels.a_venir(compte or None)]

    @app.delete("/interne/rappels/{ident}", include_in_schema=False)
    def interne_supprimer_rappel(ident: str, conversation: str = "", x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        r = rappels.supprimer(ident, personne_de(conversation) or None)
        if not r:
            raise HTTPException(404, "rappel inconnu ou déjà passé")
        return {"ok": True, "supprime": r["texte"]}

    @app.get("/api/rappels")
    def lister_rappels(requete: Request) -> dict:
        compte = compte_de(requete) or ""
        return {"rappels": [{k: r[k] for k in ("id", "quand", "texte")}
                            for r in rappels.a_venir(None if compte == COMPTE_MACHINE else compte)]}

    def proprietaire_conv(conversation: str) -> str:
        """À qui appartient ce que Claude crée dans cette conversation : la voix reconnue, sinon le compte."""
        return personne_de(conversation) or compte_conversation.get(conversation, ("", ""))[0]

    async def categories_permises(entity_ids: list[str], conversation: str) -> None:
        """Une règle ou une routine n'agit que sur ce que son auteur pilote lui-même."""
        refus = domaines_interdits(D.interdits(set(droits_conv(conversation))))
        bloques = [i for i in entity_ids if i.split(".", 1)[0] in refus]
        if bloques:
            raise HTTPException(403, f"pas permis pour cette personne : piloter {', '.join(bloques)}")

    @app.get("/interne/routines", include_in_schema=False)
    def interne_routines(conversation: str = "", x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        qui = proprietaire_conv(conversation) or COMPTE_MACHINE
        return [r for r in routines.liste.values() if a_moi(r, qui)]

    @app.post("/interne/routines", include_in_schema=False)
    async def interne_creer_routine(r: NouvelleRoutine, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        exiger_conv(r.conversation, "automatismes.routines")
        await categories_permises([i for e in r.etapes for i in e.entity_ids], r.conversation)
        qui = proprietaire_conv(r.conversation)
        try:
            rt = routines.enregistrer(r.nom, [e.model_dump(exclude_none=True) for e in r.etapes], qui)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        audit.noter("routine_créée", qui, "", par="claude", nom=rt["nom"])
        return {"ok": True, "routine": rt, "pour_lancer": f"dire « {rt['nom']} »"}

    def nom_compte(ident: str) -> str:
        return (securite.comptes.get(ident) or {}).get("nom", ident) if ident not in ("", "claude") else ""

    @app.get("/api/routines")
    def lister_routines(requete: Request) -> dict:
        compte = compte_memoire(requete)
        return {"routines": [{**r, "auteur": nom_compte(r.get("compte", ""))}
                             for r in routines.liste.values() if a_moi(r, compte)]}

    @app.delete("/api/routines/{nom}")
    def supprimer_routine(nom: str, requete: Request) -> dict:
        compte = compte_memoire(requete)
        r = routines.liste.get(routines.cle(nom))
        if not r or not a_moi(r, compte):
            raise HTTPException(404, "routine inconnue")
        if not routines.supprimer(nom):
            raise HTTPException(404, "routine inconnue")
        auditer(requete, "routine_supprimée", nom=nom)
        return {"ok": True}

    # --- règles « si… alors… » ---------------------------------------------------------------------
    @app.get("/interne/capteurs", include_in_schema=False)
    async def interne_capteurs(filtre: str = "", conversation: str = "", x_jeton_interne: str | None = Header(None)) -> list[dict]:
        """Capteurs et personnes utilisables dans une condition (température, humidité, porte, présence…)."""
        verifier_jeton(x_jeton_interne)
        exiger_conv(conversation, "maison.voir")
        from .maison.texte import normaliser
        try:
            etats = await etats_ha()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, "Home Assistant injoignable") from e
        f = set(normaliser(filtre).split())
        sortie = []
        for eid, x in etats.items():
            if eid.split(".", 1)[0] not in ("sensor", "binary_sensor", "person", "climate", "device_tracker", "weather"):
                continue
            a = x.get("attributes") or {}
            nom = a.get("friendly_name") or eid
            score = len(f & set(normaliser(f"{nom} {eid.replace('.', ' ').replace('_', ' ')} {a.get('device_class', '')}").split()))
            if f and not score:
                continue
            sortie.append({"score": score, "entity_id": eid, "nom": nom, "etat": x.get("state"),
                           **({"unite": a["unit_of_measurement"]} if a.get("unit_of_measurement") else {}),
                           **({"type": a["device_class"]} if a.get("device_class") else {}),
                           **({"current_temperature": a["current_temperature"]} if "current_temperature" in a else {})})
        sortie.sort(key=lambda x: -x["score"])          # les plus proches du filtre d'abord
        return [{k: v for k, v in x.items() if k != "score"} for x in sortie[:80]]

    @app.post("/interne/regles", include_in_schema=False)
    async def interne_proposer_regle(p: PropositionRegle, x_jeton_interne: str | None = Header(None)) -> dict:
        """Prépare une règle : rien n'est enregistré avant le « oui » de la personne."""
        verifier_jeton(x_jeton_interne)
        exiger_conv(p.conversation, "automatismes.regles")
        try:
            c, actions, annonce, plage = normaliser_regle(p.condition, [e.model_dump(exclude_none=True) for e in p.actions],
                                                          p.annonce, p.plage)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if c.get("entity_id") and not await ha.etat_entite(c["entity_id"]):
            raise HTTPException(422, f"capteur inconnu : {c['entity_id']} (cherche-le avec lister_capteurs)")
        connus = {e.entity_id: e for e in await maison.inventaire.obtenir()} if actions else {}
        for e in actions:
            inconnus = [i for i in e["entity_ids"] if i not in connus]
            if inconnus:
                raise HTTPException(422, f"appareils inconnus ou non autorisés : {', '.join(inconnus)}")
            sensibles = [connus[i].libelle for i in e["entity_ids"] if maison.sensible(connus[i])]
            if sensibles:
                raise HTTPException(422, f"action sensible, refusée même dans une règle : {', '.join(sensibles)}")
            await categories_permises(e["entity_ids"], p.conversation)
        r = {"condition": c, "actions": actions, "annonce": annonce, "plage": plage}
        r["texte"] = decrire_regle(r, *(await noms_regle(r)))
        compte = proprietaire_conv(p.conversation)
        regle_attente[p.conversation] = (r, compte, time.monotonic())
        return {"en_attente": "rien n'est enregistré avant le oui de la personne",
                "a_dire": f"Je crée cette règle : {r['texte']} D'accord ?"}

    @app.get("/interne/regles", include_in_schema=False)
    def interne_regles(conversation: str = "", x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        qui = proprietaire_conv(conversation) or COMPTE_MACHINE
        return [{"id": r["id"], "texte": r["texte"], "active": r.get("active", True)} for r in regles.liste
                if a_moi(r, qui)]

    @app.post("/interne/regles/{ident}", include_in_schema=False)
    def interne_changer_regle(ident: str, c: ChangementRegle, conversation: str = "",
                              x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        qui = proprietaire_conv(conversation) or COMPTE_MACHINE
        if not any(x["id"] == ident and a_moi(x, qui) for x in regles.liste):
            raise HTTPException(404, "règle inconnue")
        r = regles.activer(ident, c.active)
        if not r:
            raise HTTPException(404, "règle inconnue")
        compte, ip = compte_conversation.get(conversation, ("", ""))
        audit.noter("règle_reprise" if c.active else "règle_en_pause", compte, ip, par="claude", regle=r["texte"][:200])
        return {"ok": True, "texte": r["texte"], "active": r["active"]}

    @app.get("/api/regles")
    def lister_regles(requete: Request) -> dict:
        compte = compte_memoire(requete)
        return {"regles": [{**{k: r.get(k) for k in ("id", "texte", "active", "creee", "derniere", "resultat")},
                            "auteur": nom_compte(r.get("compte", ""))}
                           for r in regles.liste if a_moi(r, compte)]}

    def regle_a_moi(ident: str, requete: Request) -> None:
        compte = compte_memoire(requete)
        if not any(x["id"] == ident and a_moi(x, compte) for x in regles.liste):
            raise HTTPException(404, "règle inconnue")

    @app.post("/api/regles/{ident}")
    def changer_regle(ident: str, c: ChangementRegle, requete: Request) -> dict:
        regle_a_moi(ident, requete)
        r = regles.activer(ident, c.active)
        if not r:
            raise HTTPException(404, "règle inconnue")
        auditer(requete, "règle_reprise" if c.active else "règle_en_pause", regle=r["texte"][:200])
        return {"ok": True, "active": r["active"]}

    @app.delete("/api/regles/{ident}")
    def supprimer_regle(ident: str, requete: Request) -> dict:
        regle_a_moi(ident, requete)
        r = regles.supprimer(ident)
        if not r:
            raise HTTPException(404, "règle inconnue")
        auditer(requete, "règle_supprimée", regle=r["texte"][:200])
        return {"ok": True}

    @app.get("/api/habitudes")
    def lister_habitudes(requete: Request) -> dict:
        compte_memoire(requete)
        exiger(requete, "maison.historique")          # tirées de l'historique de toute la maison
        ignorees = habitudes_ignorees()
        h = [x for x in detecter(maison.historique.actions if maison.historique else []) if x["id"] not in ignorees]
        return {"habitudes": h}

    @app.post("/api/habitudes/{decision}")
    async def decider_habitude(decision: Literal["accepter", "ignorer"], c: ChoixHabitude, requete: Request) -> dict:
        """Accepter = créer l'automatisation dans Home Assistant (administrateurs, après un « oui » explicite)."""
        if decision == "ignorer":
            compte_memoire(requete)
            exiger(requete, "maison.historique")
            fichier_ignorees.write_text(json.dumps(sorted(habitudes_ignorees() | {c.id})), encoding="utf-8")
            return {"ok": True}
        exiger(requete, "automatismes.habitudes")
        h = next((x for x in detecter(maison.historique.actions if maison.historique else []) if x["id"] == c.id), None)
        if not h:
            raise HTTPException(404, "habitude inconnue ou plus d'actualité")
        try:
            ident, conf = automatisation(h)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if not await ha.creer_automatisation(ident, conf):
            raise HTTPException(502, "Home Assistant a refusé (le jeton doit être celui d'un administrateur HA)")
        fichier_ignorees.write_text(json.dumps(sorted(habitudes_ignorees() | {c.id})), encoding="utf-8")
        autos = automatisations_creees()
        autos[ident] = {"id": ident, "alias": conf["alias"], "compte": compte_de(requete) or "",
                        "cree": time.strftime("%Y-%m-%dT%H:%M:%S")}
        fichier_autos.write_text(json.dumps(autos, ensure_ascii=False, indent=1), encoding="utf-8")
        auditer(requete, "automatisation_créée", nom=conf["alias"])
        return {"ok": True, "automatisation": conf["alias"]}

    # --- automatisations créées dans Home Assistant à partir d'une habitude -----------------------------
    @app.get("/api/automatisations")
    def lister_automatisations(requete: Request) -> dict:
        compte = compte_memoire(requete)
        return {"automatisations": [{**a, "auteur": nom_compte(a.get("compte", ""))}
                                    for a in automatisations_creees().values() if a_moi(a, compte)]}

    @app.delete("/api/automatisations/{ident}")
    async def supprimer_automatisation(ident: str, requete: Request) -> dict:
        compte = compte_memoire(requete)
        autos = automatisations_creees()
        a = autos.get(ident)
        if not a or not a_moi(a, compte):
            raise HTTPException(404, "automatisation inconnue")
        if not await ha.supprimer_automatisation(ident):
            raise HTTPException(502, "Home Assistant a refusé (le jeton doit être celui d'un administrateur HA)")
        autos.pop(ident)
        fichier_autos.write_text(json.dumps(autos, ensure_ascii=False, indent=1), encoding="utf-8")
        auditer(requete, "automatisation_supprimée", nom=a["alias"])
        return {"ok": True}

    @app.get("/api/annonces")
    def lire_annonces(requete: Request, depuis: str = "") -> dict:
        """Ce que l'assistant a à dire de lui-même ; « parler » : à dire à voix haute maintenant."""
        caches = contexte_de(requete)["masques"]
        compte = compte_de(requete)
        liste = [a for a in annonces.depuis(depuis) if visible_annonce(a, compte)]   # rappels, alertes : à qui de droit
        if "historique" in caches:         # appareil partagé / enfant : seulement les urgences
            liste = [a for a in liste if a["priorite"] == "urgent"]
        if not securite.peut(compte, "serveur.mettre_a_jour"):     # les boutons « Mettre à jour / Ignorer »
            liste = [{k: v for k, v in a.items() if k not in ("actions", "cible")} for a in liste]
        return {"annonces": liste, "heures_calmes": cfg.proactif.heures_calmes, "presents": annonces.presents}

    @app.post("/api/annonces/{ident}/lue")
    def annonce_lue(ident: str, requete: Request) -> dict:
        a = annonces.trouver(ident)
        if not a or not visible_annonce(a, compte_de(requete)) or not annonces.marquer_lue(ident):
            raise HTTPException(404, "annonce inconnue")
        return {"ok": True}

    @app.post("/api/annonces/{ident}/action")
    async def action_annonce(ident: str, c: ChoixAnnonce, requete: Request) -> dict:
        """Bouton d'une annonce : « Mettre à jour » ou « Ignorer » une mise à jour d'image Docker."""
        exiger(requete, "serveur.mettre_a_jour")
        a = annonces.trouver(ident)
        if not a or not a.get("actions"):
            raise HTTPException(404, "annonce inconnue ou sans action")
        if c.action not in {x["id"] for x in a["actions"]}:
            raise HTTPException(422, f"action inconnue : {c.action}")
        cible = a.get("cible") or {}
        nom = cible.get("conteneur", "")
        if cible.get("type") != "maj_docker" or not nom:
            raise HTTPException(422, "annonce sans cible connue")
        if a.get("resolue") and not a.get("decision"):
            raise HTTPException(409, f"{nom} : plus de mise à jour en attente (faite ailleurs ?)")
        if c.action == "mettre_a_jour" and not (isinstance(docker, UnraidDocker) and cfg.docker.cle_unraid_actions()):
            raise HTTPException(409, "mise à jour impossible : ajoute la clé Unraid « actions Docker » dans Réglages")
        if not annonces.decider(ident, c.action):
            deja = {"mettre_a_jour": "déjà mis à jour", "ignorer": "déjà ignoré"}.get(a.get("decision"), "déjà traité")
            raise HTTPException(409, f"{nom} : {deja}")
        if c.action == "ignorer":
            maj_ignorees.ignorer(nom)
            annonces.resoudre(a["cle"])
            auditer(requete, "mise_a_jour_docker", conteneur=nom, decision="ignorée")
            return {"ok": True, "texte": f"D'accord, j'ignore la mise à jour de {nom}."}
        # le téléchargement de l'image peut dépasser la minute (et le proxy d'Unraid) : on suit en arrière-plan
        lancer_mise_a_jour(nom, nom, ident)
        auditer(requete, "mise_a_jour_docker", conteneur=nom, decision="lancée")
        return {"ok": True, "texte": f"Mise à jour de {nom} lancée. Je te préviens quand c'est fini.", "etat": "en cours"}

    @app.post("/api/briefing")
    async def demander_briefing() -> dict:
        try:
            texte = await briefing()
        except FournisseurIndisponible as e:
            raise HTTPException(503, f"aucun modèle disponible ({e})") from e
        return {"texte": texte}

    # --- rapports (cartes de synthèse) ------------------------------------------------------------------
    @app.get("/api/rapports")
    def lister_rapports() -> dict:
        """Les cartes toutes faites (le type « libre » prend un sujet quelconque)."""
        return {"modeles": [{"type": k, "nom": v} for k, v in MODELES_RAPPORT.items()], "ville": cfg.proactif.ville}

    @app.post("/api/rapport")
    async def demander_rapport(dem: DemandeRapport, requete: Request) -> dict:
        """Une carte de rapport sans passer par le chat (bouton « Actualiser » de la carte, raccourcis)."""
        if dem.type not in MODELES_RAPPORT:
            raise HTTPException(400, f"type de rapport inconnu : {dem.type}")
        if dem.type == "libre" and not dem.sujet.strip():
            raise HTTPException(400, "un rapport libre demande un sujet")
        exiger(requete, "serveur.etat" if dem.type == "docker" else "assistant.web")
        texte, cartes = await faire_rapport(Demande(dem.type, dem.sujet.strip()), compte_de(requete),
                                            frozenset(contexte_de(requete)["masques"]))
        return {"texte": texte, "cartes": cartes}

    @app.get("/api/docker")
    async def etat_docker(requete: Request) -> dict:
        """Conteneurs et ceux qui posent problème."""
        exiger(requete, "serveur.etat")
        if not docker.configure:
            return {"configure": False, "conteneurs": [], "problemes": []}
        liste = await docker_ou_erreur(docker.conteneurs())
        mauvais = [c["nom"] for c in await docker_ou_erreur(docker.problemes())]
        return {"configure": True, "conteneurs": liste, "problemes": mauvais}

    @app.post("/interne/maison/agir", include_in_schema=False)
    async def interne_agir(a: Action, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        demande_en_cours.set(("claude", texte_conversation.get(a.conversation, "")))
        interdits.set(interdits_conversation.get(a.conversation, frozenset()))
        r = await maison.agir_ids(a.entity_ids, a.action, a.valeur, a.couleur, a.conversation)
        cartes_conversation.setdefault(a.conversation, []).extend(r.detail.get("entites", []))
        journal.info("outil agir %s %s -> %s", a.action, a.entity_ids, r.texte[:120])
        compte, ip = compte_conversation.get(a.conversation, ("", ""))
        audit.noter("action_maison", compte, ip, par="claude", action=a.action, entites=a.entity_ids,
                    valeur=a.valeur, resultat=r.texte[:200])
        return {"resultat": r.texte or "Rien n'a été fait.", "entites": r.detail.get("entites", [])}

    @app.post("/interne/maison/annuler", include_in_schema=False)
    async def interne_annuler(c: Conv, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        interdits.set(interdits_conversation.get(c.conversation, frozenset()))
        r = await maison.annuler(c.conversation)
        journal.info("outil annuler -> %s", r.texte[:120])
        compte, ip = compte_conversation.get(c.conversation, ("", ""))
        audit.noter("action_maison", compte, ip, par="claude", action="annuler", resultat=r.texte[:200])
        return {"resultat": r.texte}

    # --- Voix ------------------------------------------------------------------------------
    @app.get("/api/voix")
    def etat_voix(requete: Request) -> dict:
        p = prefs(compte_de(requete))
        return {**voix.etat(), "wake_word": wake_pour(p["theme"]), "theme": p["theme"],
                "fenetre_s": p["fenetre_s"], "toujours_a_l_ecoute": p["toujours_a_l_ecoute"],
                "voix": p["voix"], "voix_disponibles": voix.disponibles()}

    @app.post("/api/voix/transcrire")
    async def transcrire(requete: Request, partiel: bool = False, conversation: str = "") -> dict:
        if not cfg.voix.active:
            raise HTTPException(503, "voix désactivée (voix.active)")
        if not cfg.voix.whisper_present():
            raise HTTPException(503, f"modèle Whisper absent : {cfg.voix.chemin_whisper()}")
        audio = await requete.body()
        if not audio:
            raise HTTPException(400, "audio vide")
        if len(audio) > cfg.voix.max_audio_mo * 1e6:
            raise HTTPException(413, "enregistrement trop long")
        # Mots probables : nom de l'assistant, appareils et pièces (aide Whisper sur « Globe », « L3 »…)
        noms = [cfg.identite.nom_effectif] + sorted({e.nom for e in maison.inventaire.entites} |
                                           {e.piece for e in maison.inventaire.entites if e.piece})[:60]
        if partiel and verrou_transcription.locked():
            return {"texte": "", "saute": True, "duree_audio_s": 0, "duree_ms": 0}
        try:
            async with verrou_transcription:
                t = await voix.transcrire(audio, ", ".join(noms))
        except Exception as e:  # noqa: BLE001 - audio illisible, modèle en erreur
            raise HTTPException(422, f"transcription impossible ({e.__class__.__name__})") from e
        qui = None
        if not partiel:   # les transcriptions partielles (mots en direct) ne sont pas tracées
            qui = await reconnaitre(audio, conversation) if t.texte else None
            journal.info("voix : « %s » (%.1f s d'audio, %d ms)%s", t.texte[:120], t.duree_audio_s, t.duree_ms,
                         f" — {qui['nom']} ({qui['score']})" if qui else "")
        return {"texte": t.texte, "duree_audio_s": t.duree_audio_s, "duree_ms": t.duree_ms, "locuteur": qui}

    @app.post("/api/voix/ecoute")
    async def ecoute(requete: Request, conversation: str = "") -> dict:
        """Écoute continue : un morceau de parole -> ignorer, réveil (« <nom> ? ») ou commande.
        Le wake word est celui de la config (identite.wake_word, ou le nom)."""
        if not cfg.voix.active or not cfg.voix.whisper_present():
            raise HTTPException(503, "voix indisponible")
        audio = await requete.body()
        if not audio or len(audio) > cfg.voix.max_audio_mo * 1e6:
            raise HTTPException(400, "audio vide ou trop long")
        p = prefs(compte_de(requete))            # son thème (mot de réveil) et son écoute
        reveil.regler(conversation, p["fenetre_s"], p["toujours_a_l_ecoute"])
        noms = [nom_pour(p["theme"])] + (["HAL"] if p["theme"] == "hal" else []) \
            + sorted({e.nom for e in maison.inventaire.entites})[:60]
        async with verrou_transcription:
            t = await voix.transcrire(audio, ", ".join(noms))
        r = reveil.analyser(t.texte, variantes_pour(p["theme"]), conversation)
        if (r["action"] == "ignorer" and r.get("raison") != "bruit" and question_en_attente(conversation)
                and (RE_OUI_DEBUT.match(t.texte) or RE_NON_DEBUT.match(t.texte))):
            # l'assistant vient de poser une question (« Je démarre adminer ? ») : « oui » suffit, sans son nom
            reveil.prolonger(conversation)
            r = {"action": "commande", "commande": t.texte.strip(), "fenetre_s": reveil.restant_s(conversation),
                 "raison": "réponse à une question"}
        qui = None
        if r["action"] != "ignorer":
            qui = await reconnaitre(audio, conversation)
            journal.info("écoute : « %s » -> %s (%d ms)%s", t.texte[:120], r["action"], t.duree_ms,
                         f" — {qui['nom']} ({qui['score']})" if qui else "")
        return {"texte": t.texte, "duree_ms": t.duree_ms, "wake_word": wake_pour(p["theme"]),
                "toujours": p["toujours_a_l_ecoute"], "locuteur": qui, **r}

    @app.post("/api/voix/reglages")
    def regler_voix(r: ReglagesVoix, requete: Request) -> dict:
        """⚙ Voix et écoute : sa voix, sa fenêtre d'écoute après le mot de réveil, « toujours à l'écoute ».
        Gardés dans son compte (chacun les siens) ; depuis la machine elle-même, ceux de la maison."""
        valeurs = {k: v for k, v in r.model_dump().items() if v is not None}
        if not valeurs:
            raise HTTPException(400, "aucun réglage fourni")
        if "voix" in valeurs and valeurs["voix"] not in {v["id"] for v in voix.disponibles()}:
            raise HTTPException(422, f"voix inconnue : {valeurs['voix']}")
        p = ecrire_prefs(requete, valeurs)
        journal.info("réglages voix de %s : %s", compte_de(requete) or "?", valeurs)
        return {"fenetre_s": p["fenetre_s"], "toujours_a_l_ecoute": p["toujours_a_l_ecoute"], "voix": p["voix"]}

    @app.post("/api/theme")
    def regler_theme(t: Theme, requete: Request) -> dict:
        """⚙ Affichage : « orbe » ou « hal », pour cette personne. Change aussi son mot de réveil
        (« Hal ») et le nom sous lequel l'assistant lui répond."""
        p = ecrire_prefs(requete, {"theme": t.theme})
        journal.info("thème de %s : %s (mot de réveil « %s »)", compte_de(requete) or "?", t.theme, wake_pour(p["theme"]))
        return {"theme": p["theme"], "wake_word": wake_pour(p["theme"])}

    # --- Page de configuration -------------------------------------------------------------
    etat_appli = {"redemarrage_necessaire": False, "telechargement": None}

    def defauts_bruts() -> dict:
        from .config import DEFAUTS, _lire_yaml
        return _lire_yaml(DEFAUTS)

    async def etat_claude() -> dict:
        f = reg.fournisseurs.get(next((i for i, fc in cfg.fournisseurs.items() if fc.type == "claude_code"), ""))
        if not f:
            return {"configure": False}
        from .fournisseurs.claude_code import resoudre_binaire
        return {"configure": True, "installe": resoudre_binaire(getattr(f, "commande", "claude")) is not None,
                "connecte": await f.disponible(), "modele": f.modele,
                "commande_connexion": "claude auth login"}

    async def etat_ollama() -> dict:
        fc = next((fc for fc in cfg.fournisseurs.values() if fc.type == "openai" and "11434" in fc.url), None)
        if not fc:
            return {"configure": False}
        import httpx
        base = fc.url.rstrip("/").removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(base + "/api/tags")
            modeles = [m["name"] for m in r.json().get("models", [])]
            return {"configure": True, "url": fc.url, "joignable": True, "modeles": modeles,
                    "modele_actuel": fc.modele, "integre": os.environ.get("ASSISTANT_OLLAMA_INTEGRE") == "1",
                    "telechargement": etat_appli["telechargement"]}
        except Exception:  # noqa: BLE001 - Ollama arrêté, mauvaise adresse
            return {"configure": True, "url": fc.url, "joignable": False, "modeles": [], "modele_actuel": fc.modele,
                    "telechargement": etat_appli["telechargement"]}

    @app.get("/api/reglages")
    async def lire_reglages(requete: Request) -> dict:
        import asyncio as aio
        exiger_admin(requete)          # réseau, intégrations, modèles : administrateurs seulement
        claude, ollama, ha_etat = await aio.gather(etat_claude(), etat_ollama(), ha.etat())
        return {
            "reglages": R.vue(cfg),
            "etat": {"version": __version__, "conteneur": os.name != "nt" and Path("/config").is_dir(),
                     "redemarrage_necessaire": etat_appli["redemarrage_necessaire"],
                     "claude": claude, "ollama": ollama, "home_assistant": ha_etat,
                     "voix": {"appareil": getattr(voix, "appareil", "") or cfg.voix.appareil, "moteur": cfg.voix.moteur_effectif()},
                     "securite": {"mot_de_passe_defini": securite.mot_de_passe_defini}},
            "voix_disponibles": voix.disponibles(),
        }

    @app.post("/api/reglages")
    async def ecrire_reglages(changements: dict, requete: Request) -> dict:
        exiger_admin(requete)
        auditer(requete, "réglages", sections=sorted(changements))
        try:
            _, fichier = R.appliquer(changements, defauts_bruts())
        except R.ReglageInvalide as e:
            raise HTTPException(422, str(e)) from e
        # appliqué tout de suite quand c'est sans risque, sinon au redémarrage
        if "voix" in changements:
            v = changements["voix"]
            if "fenetre_s" in v:
                cfg.voix.fenetre_s = reveil.fenetre_s = float(v["fenetre_s"])
            if "toujours_a_l_ecoute" in v:
                cfg.voix.toujours_a_l_ecoute = reveil.toujours = bool(v["toujours_a_l_ecoute"])
            if "voix" in v:
                cfg.voix.voix = v["voix"]
            if "lenteur" in v:
                cfg.voix.lenteur = float(v["lenteur"])
        if "ville" in changements.get("proactif", {}):     # météo sans ville : prise tout de suite
            cfg.proactif.ville = rapports.ville = str(changements["proactif"]["ville"] or "").strip()
        if "theme" in changements.get("identite", {}):
            cfg.identite.theme = changements["identite"]["theme"]
        if set(changements) - {"voix"}:
            etat_appli["redemarrage_necessaire"] = True
        journal.info("réglages modifiés depuis la page : %s", sorted(changements))
        return {"ok": True, "fichier": fichier.name, "redemarrage_necessaire": etat_appli["redemarrage_necessaire"]}

    @app.post("/api/reglages/secret")
    def ecrire_secret(s_: Secret, requete: Request) -> dict:
        exiger_admin(requete)
        auditer(requete, "secret_modifié", nom=s_.nom)      # le nom seulement, jamais la valeur
        try:
            R.ecrire_secret(s_.nom, s_.valeur.strip())
        except R.ReglageInvalide as e:
            raise HTTPException(422, str(e)) from e
        etat_appli["redemarrage_necessaire"] = True
        journal.info("secret %s modifié depuis la page", s_.nom)
        return {"ok": True, "redemarrage_necessaire": True}

    @app.post("/api/reglages/ollama/telecharger")
    async def telecharger_ollama(t: TelechargementOllama, requete: Request) -> dict:
        """Télécharge un modèle dans Ollama (en arrière-plan) ; l'avancement est dans GET /api/reglages."""
        import asyncio as aio
        exiger_admin(requete)
        auditer(requete, "modèle_téléchargé", modele=t.modele)
        etat_o = await etat_ollama()
        if not etat_o.get("joignable"):
            raise HTTPException(503, "Ollama injoignable")
        if etat_appli["telechargement"] and etat_appli["telechargement"].get("en_cours"):
            raise HTTPException(409, "un téléchargement est déjà en cours")
        base = etat_o["url"].rstrip("/").removesuffix("/v1")
        etat_appli["telechargement"] = {"modele": t.modele, "en_cours": True, "pourcent": 0, "statut": "démarrage"}

        async def tirer():
            import httpx
            try:
                async with httpx.AsyncClient(timeout=None) as c:
                    async with c.stream("POST", base + "/api/pull", json={"model": t.modele}) as r:
                        async for ligne in r.aiter_lines():
                            if not ligne:
                                continue
                            o = json.loads(ligne)
                            d = etat_appli["telechargement"]
                            d["statut"] = o.get("status", d["statut"])
                            if o.get("total"):
                                d["pourcent"] = round(100 * o.get("completed", 0) / o["total"])
                            if o.get("error"):
                                d.update(en_cours=False, statut="erreur : " + o["error"])
                                return
                etat_appli["telechargement"].update(en_cours=False, statut="terminé", pourcent=100)
            except Exception as e:  # noqa: BLE001
                etat_appli["telechargement"].update(en_cours=False, statut=f"erreur : {e.__class__.__name__}")
        app.state.tirage = aio.create_task(tirer())
        return {"ok": True}

    @app.post("/api/redemarrer")
    async def redemarrer(requete: Request) -> dict:
        """Dans le conteneur : l'assistant s'arrête et Docker le relance (--restart=unless-stopped).
        Sur le PC : on le relance depuis l'onglet Outils du tableau de suivi."""
        if not (os.name != "nt" and Path("/config").is_dir()):
            raise HTTPException(409, "sur le PC, redémarre l'assistant depuis l'onglet Outils du tableau de suivi")
        import asyncio as aio
        exiger_admin(requete)
        auditer(requete, "redémarrage")
        journal.info("redémarrage demandé depuis la page de configuration")
        aio.get_running_loop().call_later(0.5, os._exit, 0)
        return {"ok": True}

    def question_en_attente(conversation: str) -> bool:
        """L'assistant attend un oui / non dans cette conversation (redémarrage, souvenir, « lequel ? »)."""
        if not conversation:
            return False
        a = redemarrage_attente.get(conversation)
        if a and time.monotonic() - a[2] < 120:
            return True
        if ha.configure and maison.en_attente(conversation):
            return True
        try:
            return bool(memoire.proposition_en_attente(conversation, personne_conversation.get(conversation, ""), 120))
        except Exception:  # noqa: BLE001
            return False

    @app.post("/api/voix/prolonger")
    def prolonger(c: Conv, requete: Request) -> dict:
        """La page a fini de lire la réponse : la fenêtre d'écoute repart de là (sinon elle se ferme pendant
        que l'assistant parle et le « oui » qui suit est ignoré)."""
        p = prefs(compte_de(requete))
        reveil.regler(c.conversation, p["fenetre_s"], p["toujours_a_l_ecoute"])
        if c.conversation:
            reveil.prolonger(c.conversation)
        return {"fenetre_s": reveil.restant_s(c.conversation), "toujours": p["toujours_a_l_ecoute"]}

    @app.post("/api/voix/endormir")
    def endormir(c: Conv) -> dict:
        reveil.endormir(c.conversation)
        return {"ok": True}

    @app.post("/api/voix/parler")
    async def parler(p: Parole, requete: Request) -> Response:
        if not cfg.voix.active or not cfg.voix.synthese_presente():
            raise HTTPException(503, "synthèse vocale indisponible")
        if p.voix and p.voix not in {v["id"] for v in voix.disponibles()}:
            raise HTTPException(422, f"voix inconnue : {p.voix}")
        # sans voix précisée (réponse, annonce) : celle de la personne, sinon celle de la maison
        choisie = p.voix or ((securite.comptes.get(compte_de(requete) or "") or {}).get("assistant") or {}).get("voix")
        wav, duree = await voix.parler(p.texte, choisie)
        return Response(wav, media_type="audio/wav", headers={"X-Duree-Ms": str(duree), "Cache-Control": "no-store"})

    @app.post("/api/route")
    async def route(d: DemandeChat) -> dict:
        """Décision du routeur seule, sans appeler le modèle choisi (tests, réglages)."""
        return (await _decider(d)).en_dict()

    async def _decider(d: DemandeChat) -> Decision:
        precedent = next((t.content for t in reversed(d.historique) if t.role == "user"), "")
        return await routeur.decider(d.message, precedent)

    def fil_du_compte(requete: Request) -> str | None:
        """Compte dont la page partage le fil, ou None (appareil partagé, enfant, à distance : rien n'est relu)."""
        compte = compte_de(requete)
        if not compte or "historique" in contexte_de(requete)["masques"]:
            return None
        return compte

    def note_actions_recentes(compte: str | None = None) -> str:
        """Dernières actions sur la maison (toutes pages et voix confondues), pour que le modèle s'en souvienne.
        Seulement pour qui a le droit de voir l'historique de toute la maison."""
        if not maison.historique or (compte is not None and not securite.peut(compte, "maison.historique")):
            return ""
        from datetime import datetime, timedelta
        limite = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds")
        recentes = [a for a in maison.historique.liste(8) if a.get("heure", "") >= limite]
        if not recentes:
            return ""
        lignes = [f"- {a['heure'][11:16]} {a.get('nom') or a.get('appareil')} : {a.get('action')}"
                  f"{' (annulée)' if a.get('annulee') else ''}" for a in reversed(recentes)]
        return "Dernières actions sur la maison (tous appareils confondus) :\n" + "\n".join(lignes)

    @app.post("/api/chat")
    async def chat(d: DemandeChat, requete: Request):
        """Fil partagé : avec l'identifiant de conversation du compte, la question et la réponse sont gardées
        et envoyées en direct aux autres pages ouvertes ; le modèle reçoit l'historique du compte."""
        compte = fil_du_compte(requete)
        proprietaire = fil.compte_de(d.conversation) if d.conversation.startswith("fil-") else None
        if proprietaire and proprietaire != compte_de(requete):
            raise HTTPException(403, "conversation d'un autre compte")
        if not (compte and proprietaire == compte):
            return await chat_interne(d, requete)
        d = d.model_copy(update={"historique": [Tour(**t) for t in fil.contexte(compte, HISTORIQUE_MAX)]})
        nom_app = nom_appareil(requete.headers.get("user-agent", ""))
        q = fil.ajouter(compte, "user", d.message, d.appareil, d.source, extra={"appareil_nom": nom_app})
        try:
            rep_ = await chat_interne(d, requete, note_fil=note_actions_recentes(compte))
        except HTTPException as e:
            fil.ajouter(compte, "assistant", "", d.appareil, question=q["id"], extra={"erreur": str(e.detail)})
            raise
        extra_de = lambda o: {k: o[k] for k in ("cartes", "masquer_texte", "fournisseur", "modele", "duree_ms", "erreur") if o.get(k)}
        if isinstance(rep_, dict):
            fil.ajouter(compte, "assistant", rep_.get("reponse", ""), d.appareil, question=q["id"], extra=extra_de(rep_))
            return rep_
        corps = rep_.body_iterator

        async def suivre():
            texte, extra = "", {}
            try:
                async for ligne in corps:
                    yield ligne
                    try:
                        o = json.loads(ligne)
                    except (TypeError, ValueError):
                        continue
                    if o.get("delta"):
                        texte += o["delta"]
                        fil.publier(compte, {"type": "delta", "question": q["id"], "delta": o["delta"],
                                             "appareil": d.appareil})
                    extra.update(extra_de(o))
            finally:
                fil.ajouter(compte, "assistant", texte, d.appareil, question=q["id"], extra=extra)
        rep_.body_iterator = suivre()
        return rep_

    @app.get("/api/fil")
    def lire_fil(requete: Request, depuis: int = 0, n: int = 60) -> dict:
        """Conversation du compte : son identifiant et ses derniers échanges (pour une page qui s'ouvre)."""
        compte = fil_du_compte(requete)
        if not compte:
            return {"actif": False}
        return {"actif": True, "conversation": fil.conversation(compte),
                "tours": fil.tours(compte, depuis, max(1, min(n, 200))), "ouverts": fil.ouverts(compte)}

    @app.delete("/api/fil")
    async def effacer_fil(requete: Request, appareil: str = "") -> dict:
        """« Nouvelle conversation » : sur tous les appareils du compte (les souvenirs validés restent)."""
        compte = fil_du_compte(requete)
        if not compte:
            raise HTTPException(403, "information non donnée sur cet appareil")
        fil.effacer(compte, appareil[:40])
        auditer(requete, "conversation_effacée")
        return {"ok": True}

    @app.get("/api/fil/flux")
    async def flux_fil(requete: Request, depuis: int = 0):
        """Server-Sent Events : chaque question, chaque morceau de réponse et chaque réponse du compte,
        quel que soit l'appareil. À la reconnexion (Last-Event-ID), les échanges manqués sont renvoyés."""
        import asyncio as aio
        compte = fil_du_compte(requete)
        if not compte:
            raise HTTPException(403, "information non donnée sur cet appareil")
        try:
            depuis = max(depuis, int(requete.headers.get("last-event-id", "0")))
        except ValueError:
            pass
        q = fil.abonner(compte)

        def sse(o: dict) -> str:
            ident = f"id: {o['tour']['id']}\n" if o.get("type") == "tour" else ""
            return f"{ident}data: {json.dumps(o, ensure_ascii=False)}\n\n"

        async def evenements():
            try:
                yield "retry: 3000\n\n"
                if depuis:
                    for t in fil.tours(compte, depuis, 200):
                        yield sse({"type": "tour", "tour": t})
                while True:
                    try:
                        o = await aio.wait_for(q.get(), 20)
                    except aio.TimeoutError:
                        if await requete.is_disconnected():
                            return
                        yield ": ping\n\n"      # garde la connexion ouverte (proxy, Cloudflare)
                        continue
                    if o is None:
                        return
                    yield sse(o)
            finally:
                fil.desabonner(compte, q)

        return StreamingResponse(evenements(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def chat_interne(d: DemandeChat, requete: Request, note_fil: str = ""):
        if RE_BRIEFING.search(d.message):
            d = d.model_copy(update={"message": consigne_briefing(), "role": cfg.routeur.role_complexe})
        demande_en_cours.set((d.source, d.message))
        compte = compte_de(requete)
        ctx_revel = frozenset(contexte_de(requete)["masques"])
        masques.set(ctx_revel)
        masques_conversation[d.conversation] = ctx_revel
        profil = securite.profil(compte) if compte and compte != COMPTE_MACHINE else {}
        nom_profil = securite.comptes.get(compte or "", {}).get("nom", "")
        voix_de = locuteur_conversation.get(d.conversation)
        if d.source == "voix" and voix_de and time.monotonic() - voix_de[1] < 60 and voix_de[0] in securite.comptes:
            # reconnu à la voix : son ton, sa pièce, ses préférences, et ses limites (jamais plus de droits
            # que le compte connecté : les interdits des deux s'additionnent)
            profil, nom_profil = securite.profil(voix_de[0]), securite.comptes[voix_de[0]].get("nom", voix_de[0])
            route_locuteur = nom_profil
            interdits_voix = domaines_interdits(profil.get("interdits"))
        else:
            route_locuteur = ""
            interdits_voix = frozenset()
        interdits_conversation[d.conversation] = interdits_voix
        interdits_effectifs = interdits_de(compte, d.conversation)
        interdits_conversation[d.conversation] = interdits_effectifs
        interdits.set(interdits_effectifs)
        if profil:
            profil = {**profil, "interdits": sorted(c for c, doms in CATEGORIES_DROITS.items()
                                                    if set(doms) <= interdits_effectifs)}
        if "personnel" in ctx_revel:
            profil = {k: v for k, v in profil.items() if k in ("type", "ton", "interdits")}   # ni pièce ni préférences
        piece_preferee.set(profil.get("piece", ""))
        consigne_profil = note_profil(nom_profil, profil) if profil else ""
        # droits de cette demande (compte connecté, réduits par la voix reconnue) : pour le chat et les outils
        droits = droits_de(compte, d.conversation, voix_de[0] if route_locuteur else None)
        droits_conversation[d.conversation] = droits
        compte_conversation[d.conversation] = (compte_de(requete) or "", ip_de(requete)[1])

        def refuser(cle: str):
            route_x = {"role": "droits", "raison": f"pas permis ({cle})"}
            _tracer(route_x, d.message)
            return _reponse_ha(d.flux, REFUS[cle], route_x, "droits", "refus", 0)

        # --- redémarrage de conteneur en attente de « oui » ------------------------------------------------
        attente_r = redemarrage_attente.get(d.conversation)
        if attente_r and time.monotonic() - attente_r[2] < 120 and (RE_OUI_DEBUT.match(d.message)
                                                                   or RE_NON_DEBUT.match(d.message)):
            redemarrage_attente.pop(d.conversation, None)
            nom_c, action_c = attente_r[0], attente_r[3]
            if RE_NON_DEBUT.match(d.message) or attente_r[1] != compte:
                rien = {"redemarrer": "je ne redémarre rien", "demarrer": "je ne démarre rien",
                        "arreter": "je n'arrête rien", "mettre_a_jour": "je ne mets rien à jour"}[action_c]
                return _reponse_ha(d.flux, f"D'accord, {rien}.", {"role": "docker", "raison": "annulé"},
                                   "docker", "unraid", 0)
            fait = {"redemarrer": ("a redémarré", "Le redémarrage", "conteneur_redémarré"),
                    "demarrer": ("est démarré", "Le démarrage", "conteneur_démarré"),
                    "arreter": ("est arrêté", "L'arrêt", "conteneur_arrêté"),
                    "mettre_a_jour": ("est à jour et tourne", "La mise à jour", "mise_a_jour_docker")}[action_c]
            if action_c == "mettre_a_jour":
                # le téléchargement de l'image peut prendre des minutes : on répond tout de suite
                lancer_mise_a_jour(nom_c, nom_c)
                auditer(requete, "mise_a_jour_docker", conteneur=nom_c, decision="lancée à la voix")
                return _reponse_ha(d.flux, f"Je lance la mise à jour de {nom_c}. Ça peut prendre quelques minutes, "
                                   "je te préviens quand c'est fini.", {"role": "docker", "raison": "mise à jour lancée"},
                                   "docker", "unraid", 0)
            try:
                res = await docker.agir(nom_c, action_c, cfg.docker.cle_unraid_actions())
                attendu = res["etat"] in (("exited", "stopped", "created") if action_c == "arreter" else ("running",))
                texte_r = (f"C'est fait, {res['nom']} {fait[0]}." if attendu or not (res["etat"] or res["statut"])
                           else f"C'est fait, mais {res['nom']} est {statut_lisible(res['statut'], res['etat'])}.")
                auditer(requete, fait[2], nom=res["nom"])
            except Exception as e:  # noqa: BLE001
                texte_r = f"{fait[1]} de {nom_c} a échoué : {erreur_docker_courte(e)}."
                journal.info("%s de %s impossible : %s", action_c, nom_c, e)
            return _reponse_ha(d.flux, texte_r, {"role": "docker", "raison": f"{action_c} confirmé"}, "docker", "unraid", 0)

        # --- règle « si… alors… » proposée, en attente de « oui » ---------------------------------------
        attente_regle = regle_attente.get(d.conversation)
        if (attente_regle and time.monotonic() - attente_regle[2] < 300
                and (RE_OUI.match(d.message) or RE_NON.match(d.message))):
            regle_attente.pop(d.conversation, None)
            if RE_NON.match(d.message) or attente_regle[1] != (compte or ""):
                return _reponse_ha(d.flux, "D'accord, je n'enregistre pas cette règle.",
                                   {"role": "maison", "raison": "règle refusée"}, "maison", "règles", 0)
            try:
                rg = regles.enregistrer(attente_regle[0], compte or "")
            except ValueError as e:
                return _reponse_ha(d.flux, f"Je n'ai pas pu l'enregistrer : {e}.",
                                   {"role": "maison", "raison": "règle refusée"}, "maison", "règles", 0)
            auditer(requete, "règle_créée", regle=rg["texte"][:200])
            return _reponse_ha(d.flux, "C'est enregistré. Tu peux la mettre en pause quand tu veux, ici ou dans "
                               "Réglages, Règles.", {"role": "maison", "raison": "règle enregistrée"}, "maison", "règles", 0)

        # --- mémoire : réponse à une proposition, « retiens que… », souvenirs utiles à la réponse ---------
        qui = ((voix_de[0] if route_locuteur else compte) or "")
        personne_conversation[d.conversation] = qui
        if "memoire.utiliser" not in droits:
            qui = ""          # pas de mémoire pour cette personne : rien proposé, rien relu
        attente = memoire.proposition_en_attente(d.conversation, qui) if d.conversation else None
        if attente and RE_OUI.match(d.message):
            memoire.changer_statut(attente["id"], "valide", qui)
            return _reponse_ha(d.flux, "C'est retenu.", {"role": "mémoire", "raison": "souvenir validé"}, "mémoire", "sqlite", 0)
        if attente and RE_NON.match(d.message):
            memoire.changer_statut(attente["id"], "refuse", qui)
            return _reponse_ha(d.flux, "D'accord, je ne le retiens pas.", {"role": "mémoire", "raison": "souvenir refusé"},
                               "mémoire", "sqlite", 0)
        m_cor = RE_CORRECTION.match(d.message)
        if m_cor and qui and not attente:
            # « non, en fait je préfère la lumière chaude » : une correction à retenir (après « oui »)
            fait = m_cor.group("fait").strip().rstrip(".")
            sv = memoire.ajouter(f"Correction de {nom_profil or qui} : {fait}", "prive", qui, "propose", "correction",
                                 d.conversation)
            return _reponse_ha(d.flux, f"Compris. Je retiens pour la suite : « {fait} » ? Dis oui ou non.",
                               {"role": "mémoire", "raison": "correction proposée"}, "mémoire", "sqlite", 0)
        m_ret = RE_RETIENS.match(d.message)
        if m_ret and qui:
            fait = m_ret.group("fait").strip().rstrip(".")
            portee = ("prive" if RE_PRIVE.search(d.message) or RE_PERSO.search(fait) or "memoire.foyer" not in droits
                      else "foyer")
            fait = RE_PRIVE.sub("", fait).strip(" ,")
            texte_souvenir = f"{nom_profil or qui} : {fait}" if RE_PERSO.search(fait) else fait
            sv = memoire.ajouter(texte_souvenir[:1].upper() + texte_souvenir[1:], portee, qui, "propose", "demande",
                                 d.conversation)
            quoi = "pour toi seulement" if portee == "prive" else "pour toute la maison"
            return _reponse_ha(d.flux, f"Je retiens « {sv['texte']} », {quoi} ? Dis oui ou non.",
                               {"role": "mémoire", "raison": "souvenir proposé"}, "mémoire", "sqlite", 0)
        if qui and RE_FAIT_PERSO.search(d.message) and len(d.message) < 300:
            # repéré dans la conversation : proposé discrètement, à valider dans ⚙ Mémoire
            try:
                memoire.ajouter(f"{nom_profil or qui} : {d.message.strip()}", "prive", qui, "propose", "conversation")
            except ValueError:
                pass
        if qui:
            prive_ok = "personnel" not in ctx_revel
            tous = [x for x in memoire.liste(qui, ("valide",)) if prive_ok or x["portee"] == "foyer"]
            utiles = tous if len(tous) <= 40 else memoire.chercher(d.message, qui, prive=prive_ok, n=12)
            if utiles:
                consigne_profil = (consigne_profil + "\n\nCe que tu sais (souvenirs validés par la famille) :\n"
                                   + "\n".join(f"- [{x['id']}] {x['texte']}" for x in utiles)).strip()
            consigne_profil = (consigne_profil + "\n\n" + NOTE_MEMOIRE).strip()
        if (horloge := reponse_horloge(d.message)) is not None:
            route_h = {"role": "horloge", "raison": "heure ou date, lue sur le serveur"}
            _tracer(route_h, d.message)
            return _reponse_ha(d.flux, horloge, route_h, "horloge", "serveur", 0)
        routine = routines.trouver(d.message) if ha.configure and routines.liste and len(d.message) < 80 else None
        if routine and not a_moi(routine, personne_conversation.get(d.conversation) or compte):
            routine = None            # la routine d'un autre : ni lancée ni dévoilée
        if routine:
            texte_r = await lancer_routine(routine, d.conversation)
            return _reponse_ha(d.flux, texte_r, {"role": "maison", "raison": f"routine « {routine['nom']} »"},
                               "maison", "routine", 0)
        # --- carte ou texte (presentation.py) : « montre-moi », sujet à carte, suite d'une carte… ----------
        choix = Choix()
        if d.role == ROLE_AUTO:
            prec = derniere_carte.get(d.conversation)
            prec = prec[0] if prec and time.monotonic() - prec[1] < SUITE_VALIDE_S else None
            exclu = bool(RE_RAPPEL.search(d.message) or RE_CREER_ROUTINE.search(d.message)
                         or RE_CREER_REGLE.search(d.message) or RE_GERER_REGLE.search(d.message))
            choix = choisir_carte(d.message, prec, exclu)
            dem_rapport = detecter_rapport(d.message)
            if dem_rapport:
                choix = Choix(dem_rapport, 1, voir=True)       # « fais-moi un rapport du CAC 40 »
            elif (choix.voir and choix.demande is None and choix.reste
                    and not (ha.configure and await maison.evoque(d.message))):
                # « montre-moi un point sur la Fed » : pas de carte toute faite, la carte libre (Claude et le web)
                choix.demande = Demande("libre", sujet_rapport("point " + choix.reste) or choix.reste)
        if choix.voir and choix.demande is None and choix.reste and ha.configure and "maison.voir" in droits:
            # « montre-moi la lampe du bureau » : la carte de l'appareil, lue dans Home Assistant, sans modèle
            debut_r = time.perf_counter()
            try:
                ids = await maison.a_montrer(choix.reste)
            except Exception as e:  # noqa: BLE001 - Home Assistant injoignable : Claude répondra
                journal.info("appareil à montrer introuvable : %s", e)
                ids = []
            cartes_d = await cartes_sures(ids)
            if cartes_d:
                c0 = cartes_d[0]
                quoi = c0.get("titre") if c0.get("type") == "groupe" else c0.get("nom")
                route_d = {"role": "maison", "raison": f"carte d'appareil « {choix.reste} » (règle 1)"}
                _tracer(route_d, d.message)
                return _reponse_ha(d.flux, f"Voici {quoi}." if quoi else "Voici.", route_d, "maison", "carte",
                                   round((time.perf_counter() - debut_r) * 1000), cartes_d)
        if choix.demande:
            debut_r = time.perf_counter()
            texte_r, cartes_r = await faire_rapport(choix.demande, compte, ctx_revel)
            libre_rate = choix.demande.type == "libre" and "illisible" in texte_r
            if cartes_r or (choix.regle == 1 and not libre_rate):
                # demandée (règle 1) : la carte, ou pourquoi il n'y en a pas ; sinon, faute de carte, on continue
                # (carte libre ratée, « les planètes » : Claude répond quand même, en texte)
                if cartes_r:
                    derniere_carte[d.conversation] = (choix.demande, time.monotonic())
                    texte_r = phrase_carte(d.message, cartes_r[0], texte_r, choix.repliee)
                    if choix.repliee:
                        cartes_r[0]["repliee"] = True
                dem = choix.demande
                route_r = {"role": "rapport", "raison": f"carte {dem.type}" + (f" « {dem.sujet} »" if dem.sujet else "")
                           + f" (règle {choix.regle})"}
                _tracer(route_r, d.message)
                return _reponse_ha(d.flux, texte_r, route_r, "rapport", dem.type,
                                   round((time.perf_counter() - debut_r) * 1000), cartes_r)
        decision = None
        compte_conversation[d.conversation] = (compte_de(requete) or "", ip_de(requete)[1])
        texte_conversation[d.conversation] = d.message
        cartes_conversation.pop(d.conversation, None)
        creer_regle = ha.configure and bool(RE_CREER_REGLE.search(d.message) or RE_GERER_REGLE.search(d.message))
        creer_routine = bool(RE_CREER_ROUTINE.search(d.message)) or creer_regle
        # « mets un rappel », « rappelle-moi » : l'agenda, jamais une commande de maison ni Assist (minuteur HA)
        if RE_RAPPEL.search(d.message) and not creer_regle:
            d = d.model_copy(update={"role": cfg.routeur.role_complexe})
            pour_agenda = True
        else:
            pour_agenda = False
        if creer_routine:
            d = d.model_copy(update={"role": cfg.routeur.role_complexe})
        # --- ce que la personne n'a pas le droit de faire : refusé clairement, sans rien tenter ---------------
        if pour_agenda and "rappels.utiliser" not in droits:
            return refuser("rappels")
        if creer_regle and "automatismes.regles" not in droits:
            return refuser("regles")
        if creer_routine and not creer_regle and "automatismes.routines" not in droits:
            return refuser("routines")
        if RE_TELEGRAM.search(d.message) and "telegram.envoyer" not in droits:
            return refuser("telegram")
        if (docker.configure and "serveur.etat" not in droits and not creer_routine and not pour_agenda
                and await evoque_docker(d.message)):
            return refuser("serveur")
        mes_agendas = agendas_de(personne_conversation.get(d.conversation) or compte, droits)
        if (not pour_agenda and not mes_agendas and RE_AGENDA.search(d.message)
                and (agenda.configure or "agenda.personnel" in droits)):
            return refuser("agenda")
        if (ha.configure and "maison.voir" not in droits and not creer_routine and not pour_agenda
                and (maison.en_attente(d.conversation) or maison.reconnait(d.message, d.conversation))):
            return refuser("maison")
        # serveur, conteneurs : avant la maison (sinon « allume le docker X » finit en « introuvable »)
        pour_docker = bool(not creer_routine and not pour_agenda and docker.configure
                           and "serveur.etat" in droits and await evoque_docker(d.message))
        if pour_docker and d.role == ROLE_AUTO:
            decision = Decision(COMPLEXE, cfg.routeur.role_complexe, "demande Docker", 0.0)
            role = decision.role
        elif (d.role == ROLE_AUTO and ha.configure and not creer_routine and not pour_agenda
                and (maison.en_attente(d.conversation) or maison.reconnait(d.message, d.conversation))):
            # Commande de maison reconnue (ou réponse à « Lequel ? ») : inutile de consulter le routeur
            decision = Decision(SIMPLE, cfg.routeur.role_simple, "commande maison", 0.0)
            role = decision.role
        elif d.role == ROLE_AUTO:
            decision = await _decider(d)
            role = decision.role
        elif d.role in reg.roles:
            role = d.role
        else:
            raise HTTPException(400, f"rôle inconnu : {d.role}")
        route = decision.en_dict() if decision else {"role": role, "raison": "choisi à la main"}
        if route_locuteur:
            route["locuteur"] = route_locuteur

        # Maison : réponse à une question « lequel ? » en cours, ou demande simple
        echec_ha = ""
        simple = decision is not None and decision.categorie == SIMPLE
        if (decision is None and ha.configure and not creer_routine and not pour_agenda and not pour_docker
                and maison.reconnait(d.message, d.conversation)):
            simple = True    # rôle choisi à la main : une commande de maison reste une commande de maison
        if (ha.configure and cfg.home_assistant.pour_simples and not pour_docker
                and (simple or maison.en_attente(d.conversation))):
            rm = await maison.traiter(d.message, d.conversation)
            route["maison"] = {"traite": rm.traite, **{k: v for k, v in rm.detail.items() if k != "commande"}}
            if rm.traite:
                if rm.detail.get("entites") or "ordres" in str(rm.detail.get("resolution", "")):
                    auditer(requete, "action_maison", par="règles", demande=d.message[:200],
                            entites=rm.detail.get("entites"), resultat=rm.texte[:200])
                _tracer(route, d.message)
                return _reponse_ha(d.flux, rm.texte, route, "maison", "inventaire", rm.detail.get("duree_ms", 0),
                                   await cartes_sures(rm.detail.get("entites"),
                                                      (rm.detail.get("commande") or {}).get("action")))
            if ("commande" in rm.detail and docker.configure and "serveur.etat" not in droits
                    and conteneur_approchant(d.message)):
                return refuser("serveur")      # « arrête Plex » : un conteneur, pas un appareil introuvable
            if ("commande" in rm.detail and docker.configure and "serveur.etat" in droits
                    and conteneur_approchant(d.message)):
                # rien dans la maison, mais un conteneur au nom proche : Claude avec les outils Docker
                route["maison"]["vers_docker"] = conteneur_approchant(d.message)
                pour_docker = True
            elif "commande" in rm.detail and cfg.maison.assist_en_secours and maison.mentionne_sensible(d.message):
                # Assist pourrait ouvrir une serrure ou un portail : on refuse sans lui demander
                route["maison"]["sensible"] = True
                _tracer(route, d.message)
                return _reponse_ha(d.flux, "Je ne pilote pas ça : action sensible, à faire depuis Home Assistant.",
                                   route, "maison", "inventaire", 0)
            if not pour_docker and "commande" in rm.detail and cfg.maison.assist_en_secours and not interdits_effectifs:
                # commande comprise mais appareil introuvable : l'agent Assist de HA tente sa chance
                # (pas pour une personne aux droits limités : Assist ne les connaît pas)
                debut_ha = time.perf_counter()
                rha = await ha.conversation(maison.texte_pour_assist(d.message))
                duree = round((time.perf_counter() - debut_ha) * 1000)
                route["home_assistant"] = {"compris": rha.compris, "type": rha.type, "code": rha.code,
                                           "duree_ms": duree, "entites": rha.entites}
                if rha.compris:
                    auditer(requete, "action_maison", par="assist", demande=d.message[:200], entites=rha.entites,
                            resultat=(rha.texte or "")[:200])
                    appris = await maison.apprendre_d_assist(d.message, rha.entites, d.conversation)
                    if appris:
                        route["home_assistant"]["alias_appris"] = appris
                    _tracer(route, d.message)
                    return _reponse_ha(d.flux, rha.texte or "C'est fait.", route, "home-assistant", "assist", duree,
                                       await cartes_sures(rha.entites))
                echec_ha = rha.code or rha.type
            if not pour_docker and "commande" in rm.detail and rm.texte:
                # Commande de maison comprise mais appareil introuvable : réponse factuelle,
                # jamais confiée à un modèle (il inventerait « c'est allumé »).
                _tracer(route, d.message)
                return _reponse_ha(d.flux, rm.texte, route, "maison", "inventaire", rm.detail.get("duree_ms", 0))
            if not pour_docker and "commande" in rm.detail and not echec_ha:
                echec_ha = rm.detail.get("raison", "appareil introuvable")
        # Claude avec les outils maison : demande ouverte sur la maison (« il fait sombre au salon »),
        # ou demande simple que les règles n'ont pas su traiter
        outils = None
        claude_outille = (cfg.routeur.role_complexe in reg.roles
                          and getattr(reg.pour_role(cfg.routeur.role_complexe), "accepte_outils", False))
        if claude_outille and pour_agenda:
            role = cfg.routeur.role_complexe
            route["outils"] = "Claude avec rappels et agenda"
            agenda_recent[d.conversation] = time.monotonic()
            outils = outils_maison(d.conversation)
        elif claude_outille and telegram.configure and RE_TELEGRAM.search(d.message) and "telegram.envoyer" in droits:
            role = cfg.routeur.role_complexe
            route["outils"] = "Claude avec Telegram"
            outils = outils_maison(d.conversation)
        elif claude_outille and creer_regle:
            route["outils"] = "Claude prépare une règle « si… alors… »"
            outils = outils_maison(d.conversation)
            outils["note"] += "\n\n" + NOTE_REGLES
        elif claude_outille and ha.configure and creer_routine:
            route["outils"] = "Claude crée une routine"
            outils = outils_maison(d.conversation)
        elif claude_outille and qui and RE_SUJET_MEMOIRE.search(d.message) and "personnel" not in ctx_revel:
            # information à retenir ou à corriger : Claude peut proposer un souvenir (validé par oui / non)
            role = cfg.routeur.role_complexe
            route["outils"] = "Claude avec la mémoire"
            outils = outils_maison(d.conversation)
        elif (mes_agendas and claude_outille and "historique" not in ctx_revel
                and (RE_AGENDA.search(d.message) or (time.monotonic() - agenda_recent.get(d.conversation, -1e9) < 180
                                                  and RE_SUITE_AGENDA.search(d.message)))):
            agenda_recent[d.conversation] = time.monotonic()
            role = cfg.routeur.role_complexe
            route["outils"] = "Claude avec l'agenda"
            outils = outils_maison(d.conversation)
        elif docker.configure and claude_outille and (pour_docker or RE_DOCKER.search(d.message)) and "serveur.etat" in droits:
            # serveur, conteneurs : Claude avec les outils de diagnostic (administrateurs seulement)
            role = cfg.routeur.role_complexe
            route["outils"] = "Claude avec diagnostic Docker"
            outils = outils_maison(d.conversation)
        elif (ha.configure and cfg.maison.outils_pour_claude and (decision is not None or role == cfg.routeur.role_complexe)
                and claude_outille and "maison.voir" in droits
                and await maison.evoque(d.message)):
            if role == cfg.routeur.role_simple:
                role = cfg.routeur.role_complexe
                route["outils"] = "maison non comprise par les règles : Claude avec outils maison"
            elif role == cfg.routeur.role_complexe:
                route["outils"] = "Claude avec outils maison"
            if route.get("outils"):
                outils = outils_maison(d.conversation)
                echec_ha = ""
        _tracer(route, d.message)
        if role == cfg.routeur.role_complexe and not outils and "assistant.web" not in droits:
            return refuser("web")       # question générale (Claude, web) : pas pour cette personne

        if note_fil:
            consigne_profil = (consigne_profil + "\n\n" + note_fil).strip()
        # règle 5 : Claude peut demander une carte par un marqueur en tête de réponse (jamais pour une explication)
        marqueur_actif = not choix.texte_seul and (role == cfg.routeur.role_complexe or choix.voir)
        if marqueur_actif:
            consigne_profil = (consigne_profil + "\n\n" + NOTE_CARTE + ("\n" + NOTE_VOIR if choix.voir else "")).strip()
        msgs = _messages(cfg, d, outils=bool(outils), profil=consigne_profil, nom=nom_pour(prefs(compte)["theme"]))
        # garde-fou : le modèle rapide ne doit pas prétendre avoir agi sur la maison
        garde = ha.configure and role == cfg.routeur.role_simple
        if echec_ha:
            msgs.insert(1, Message("system", NOTE_HA_ECHEC.format(code=echec_ha)))

        async def carte_marqueur(dem: Demande) -> tuple[str, list[dict]]:
            """Règle 5 : la vraie carte demandée par le modèle, et sa phrase (ou pourquoi elle manque)."""
            try:
                phrase_r, cartes_r = await asyncio.wait_for(faire_rapport(dem, compte, ctx_revel), 20)
            except asyncio.TimeoutError:
                return "", []
            journal.info("carte demandée par le modèle : %s « %s » (%s)", dem.type, dem.sujet,
                         "faite" if cartes_r else "impossible")
            if cartes_r:
                derniere_carte[d.conversation] = (dem, time.monotonic())
            return phrase_r, cartes_r

        async def carte_de_marqueur(texte: str, actif: bool) -> tuple[str, list[dict]]:
            """« [carte:meteo:Lyon] Voici la météo. » -> (« Voici la météo. » + la phrase de la carte, [carte])."""
            dem, reste = lire_marqueur(texte) if actif else (None, texte)
            if not dem:
                return reste, []        # un marqueur abîmé et inconnu est retiré quand même
            phrase_r, cartes_r = await carte_marqueur(dem)
            return (reste.strip() + " " + phrase_r).strip(), cartes_r

        async def avec_marqueur(flux, actif: bool):
            """Le flux du modèle sans le marqueur de carte : la carte d'abord (un dict), la phrase du modèle,
            puis celle de la carte (les vraies données) juste avant la fin."""
            if not actif:
                async for x in flux:
                    yield x
                return
            debut_txt, decide, apres = "", False, ""
            async for x in flux:
                if not decide and not isinstance(x, Reponse):
                    debut_txt += x
                    dem, reste = lire_marqueur(debut_txt)
                    if dem:
                        decide = True
                        apres, cartes_m = await carte_marqueur(dem)
                        if cartes_m:
                            yield {"cartes": cartes_m, "masquer_texte": True}
                        if reste.strip():
                            yield reste.lstrip()
                    elif reste != debut_txt:      # marqueur abîmé et inconnu : retiré, pas de carte
                        decide = True
                        if reste.strip():
                            yield reste.lstrip()
                    elif not marqueur_possible(debut_txt):
                        decide = True
                        yield debut_txt
                    continue
                if isinstance(x, Reponse):
                    if not decide and debut_txt:
                        decide = True
                        yield debut_txt
                    if apres:
                        yield " " + apres
                        apres = ""
                yield x

        if not d.flux:
            debut = time.perf_counter()
            try:
                r = await reg.repondre(role, msgs, outils)
            except FournisseurIndisponible as e:
                raise HTTPException(503, f"aucun modèle disponible ({e})") from e
            r.texte = sans_emoji(r.texte)
            if (garde or (outils and r.secours)) and RE_FAUSSE_ACTION.match(r.texte):
                # le secours (qwen) n'a pas les outils : s'il dit « c'est fait », c'est inventé
                journal.info("réponse inventée remplacée : %s", r.texte[:120])
                r.texte = REPONSE_HONNETE
            vus = cartes_conversation.pop(d.conversation, [])
            texte, cartes = await carte_de_marqueur(r.texte, marqueur_actif)
            cartes += [] if choix.texte_seul else await cartes_sures(vus)
            return {
                "reponse": texte,
                "fournisseur": r.fournisseur,
                "modele": r.modele,
                "secours": r.secours,
                "route": route,
                "duree_ms": round((time.perf_counter() - debut) * 1000),
                **({"cartes": cartes, "masquer_texte": masquer_texte(texte, cartes)} if cartes else {}),
            }

        async def lignes():
            # NDJSON : {"route": ...}, puis {"delta": "..."}, puis {"fin": true, ...} ou {"erreur": "..."}
            debut = time.perf_counter()
            premier = None
            yield _ndjson({"route": route})
            if outils:
                # Avec les outils, la réponse est vérifiée en entier : si Claude échoue et que le secours
                # (sans outils) prétend avoir agi, on ne doit pas l'afficher. Pas de mot-à-mot ici.
                try:
                    r = await reg.repondre(role, msgs, outils)
                except FournisseurIndisponible as e:
                    yield _ndjson({"erreur": f"aucun modèle disponible ({e})"})
                    return
                texte, cartes = await carte_de_marqueur(sans_emoji(r.texte), marqueur_actif)
                if r.secours and RE_FAUSSE_ACTION.match(texte):
                    journal.info("réponse inventée remplacée : %s", texte[:120])
                    texte = REPONSE_HONNETE
                journal.info("réponse %s : %s", r.fournisseur, texte[:160].replace("\n", " "))
                duree = round((time.perf_counter() - debut) * 1000)
                vus = cartes_conversation.pop(d.conversation, [])
                cartes += [] if choix.texte_seul else await cartes_sures(vus)
                if cartes:      # la carte d'abord : la page sait tout de suite qu'elle cache le texte
                    yield _ndjson({"cartes": cartes, "masquer_texte": masquer_texte(texte, cartes)})
                yield _ndjson({"delta": texte})
                yield _ndjson({"fin": True, "fournisseur": r.fournisseur, "modele": r.modele, "secours": r.secours,
                               "premier_ms": duree, "duree_ms": duree})
                return
            tampon, verifie = "", not garde
            try:
                async for x in avec_marqueur(reg.flux(role, msgs, outils), marqueur_actif):
                    if isinstance(x, dict):
                        yield _ndjson(x)       # la carte demandée par Claude (règle 5), avant sa phrase
                        continue
                    if not isinstance(x, Reponse):
                        x = sans_emoji(x)
                        if not x:
                            continue
                    if not isinstance(x, Reponse) and not verifie:
                        # on retient le début de la réponse pour vérifier qu'elle n'invente pas une action
                        tampon += x
                        if len(tampon) < 40:
                            continue
                        verifie = True
                        if RE_FAUSSE_ACTION.match(tampon):
                            journal.info("réponse inventée remplacée : %s", tampon[:120])
                            yield _ndjson({"delta": REPONSE_HONNETE})
                            tampon = None
                            continue
                        x, tampon = tampon, ""
                    if tampon is None and not isinstance(x, Reponse):
                        continue  # suite de la réponse inventée : ignorée
                    if isinstance(x, Reponse) and tampon:
                        # réponse courte (< 40 caractères) jamais vérifiée : on la vérifie maintenant
                        if RE_FAUSSE_ACTION.match(tampon):
                            journal.info("réponse inventée remplacée : %s", tampon[:120])
                            tampon = REPONSE_HONNETE
                        if premier is None:
                            premier = round((time.perf_counter() - debut) * 1000)
                        yield _ndjson({"delta": tampon})
                        tampon = ""
                    if isinstance(x, Reponse):
                        journal.info("réponse %s : %s", x.fournisseur, x.texte[:160].replace("\n", " "))
                        yield _ndjson({
                            "fin": True,
                            "fournisseur": x.fournisseur,
                            "modele": x.modele,
                            "secours": x.secours,
                            "premier_ms": premier,
                            "duree_ms": round((time.perf_counter() - debut) * 1000),
                        })
                    else:
                        if premier is None:
                            premier = round((time.perf_counter() - debut) * 1000)
                        yield _ndjson({"delta": x})
            except FournisseurIndisponible as e:
                yield _ndjson({"erreur": f"aucun modèle disponible ({e})"})

        return StreamingResponse(lignes(), media_type="application/x-ndjson")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(STATIC / "icones" / "favicon.ico", media_type="image/x-icon")

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def icone_apple() -> FileResponse:
        return FileResponse(STATIC / "icones" / "apple-touch-icon.png", media_type="image/png")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifeste() -> JSONResponse:
        """Pour « Ajouter à l'écran d'accueil » : le nom suit la configuration."""
        nom = cfg.identite.nom_effectif
        icones = [{"src": f"/static/icones/icone-{t}.png", "sizes": f"{t}x{t}", "type": "image/png",
                   "purpose": "any maskable"} for t in (192, 512)]
        return JSONResponse({"name": nom, "short_name": nom, "start_url": "/", "display": "standalone",
                             "background_color": "#000000", "theme_color": "#000000", "icons": icones},
                            media_type="application/manifest+json")

    @app.get("/", include_in_schema=False)
    def index() -> Response:
        """La page, avec la version dans l'adresse des scripts (« reglages.js?v=0.21.0 ») : après une mise à
        jour, ni le navigateur ni Cloudflare ne peuvent resservir un ancien script."""
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        page = re.sub(r'src="/static/([\w.-]+\.js)"', rf'src="/static/\1?v={__version__}"', page)
        return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def nom_appareil(ua: str) -> str:
    """Nom lisible de l'appareil d'après le navigateur (« iPhone », « Android », « Windows »…)."""
    for motif, nom in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                       ("Macintosh", "Mac"), ("Linux", "Linux")):
        if motif in ua:
            return nom
    return "un autre appareil"


def _tracer(route: dict, message: str) -> None:
    """Une ligne par demande dans la console : par où elle est passée et pourquoi."""
    h = route.get("home_assistant")
    ha = ""
    if h:
        cibles = f", {', '.join(h['entites'])}" if h.get("entites") else ""
        appris = f", alias appris « {h['alias_appris']} »" if h.get("alias_appris") else ""
        ha = (f" | HA : {'compris' if h['compris'] else 'non compris'} "
              f"({h['type']}{' ' + h['code'] if h['code'] else ''}{cibles}{appris}, {h['duree_ms']} ms)")
    m = route.get("maison")
    if m:
        appris = f", alias appris « {m['alias_appris']} »" if m.get("alias_appris") else ""
        etat = "traité" if m["traite"] else "non traité"
        ha = f" | maison : {etat} ({m.get('resolution') or m.get('raison', '')}{appris}, {m.get('duree_ms', 0)} ms)" + ha
    cat = route.get("categorie", "forcé")
    if route.get("outils"):
        ha += f" | {route['outils']}"
    journal.info("route %s (%s) -> %s%s | « %s »", cat, route.get("raison", ""), route.get("role", ""), ha, message[:80])


def _reponse_ha(flux: bool, texte: str, route: dict, fournisseur: str, modele: str, duree_ms: int,
                cartes: list[dict] | None = None):
    """Réponse toute faite (maison, rapport…). Avec une carte, la carte part avant la phrase et dit à la page
    de cacher le texte (il reste dit à voix haute)."""
    journal.info("réponse %s : %s", fournisseur, texte[:160])
    fin = {"fournisseur": fournisseur, "modele": modele, "secours": False, "duree_ms": duree_ms}
    avec = {"cartes": cartes, "masquer_texte": masquer_texte(texte, cartes)} if cartes else {}
    if not flux:
        return {"reponse": texte, **fin, "route": route, **avec}

    async def lignes():
        yield _ndjson({"route": route})
        if avec:
            yield _ndjson(avec)
        yield _ndjson({"delta": texte})
        yield _ndjson({"fin": True, **fin, "premier_ms": fin["duree_ms"]})

    return StreamingResponse(lignes(), media_type="application/x-ndjson")


def _ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def run() -> None:
    import uvicorn

    secrets = charger_secrets()
    if secrets:
        print(f"Secrets chargés depuis {secrets}")
    cfg = charger()
    # persister=True : la config est passée, mais les réglages changés depuis la page (thème, voix,
    # chat Telegram) doivent quand même être écrits dans config.yaml pour survivre au redémarrage.
    uvicorn.run(creer_app(cfg, persister=True), host=cfg.serveur.hote, port=cfg.serveur.port)


if __name__ == "__main__":
    run()
