"""Point d'entrée HTTP de l'orchestrateur."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
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
from .maison import Maison, masques, piece_preferee
from .maison.historique import Historique
from .maison.historique import demande as demande_en_cours
from .maison.alias import AliasAppris
from .routeur import SIMPLE, Decision, Routeur
from . import reglages as R
from .reveil import Reveil
from .agenda import JOURS, Agenda, decrire
from .audit import Audit
from .proactif import Annonces
from .docker_diag import Docker, UnraidDocker
from .locuteur import Locuteurs
from .diagnostic import Diagnostic, GestionnaireJournal
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


class Action(BaseModel):
    """Action demandée par l'outil « agir » du serveur MCP maison."""
    entity_ids: list[str] = Field(min_length=1, max_length=30)
    action: Literal["allumer", "eteindre", "ouvrir", "fermer", "regler"]
    valeur: float | None = None
    couleur: str | None = None
    conversation: str = ""


class Conv(BaseModel):
    conversation: str = ""


class ReglagesVoix(BaseModel):
    """Réglages modifiables depuis la page (⚙) : écoute continue et voix."""
    fenetre_s: float | None = Field(default=None, ge=0, le=3600)
    toujours_a_l_ecoute: bool | None = None
    voix: str | None = Field(default=None, max_length=120)


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
RE_AGENDA = re.compile(r"\b(agenda|calendrier|rendez-vous|rdv|r[ée]union|rappelle-moi|rappel|"
                       r"qu.est-ce que j.ai|ai-je quelque chose|programme de (la journ[ée]e|demain|la semaine)|"
                       r"note (dans|sur) l.agenda|anniversaire)\b", re.I)
NOTE_OUTILS_AGENDA = (
    "Tu as aussi lire_agenda et ajouter_evenement (agenda familial). Nous sommes le {date}. Pour « rappelle-moi "
    "de … demain à 9 h », ajoute un événement à cette heure. Résume l'agenda en phrases courtes, sans liste."
)
RE_DOCKER = re.compile(r"\b(docker|conteneurs?|containers?|unraid|serveur|plex|nginx|npm|logs?|journaux|"
                       r"plant[ée]s?|crash|red[ée]marr\w*|ne (r[ée]pond|marche|fonctionne) plus)\b", re.I)
NOTE_OUTILS_DOCKER = (
    "Tu as aussi lister_conteneurs et diagnostiquer_conteneur (serveur Unraid, lecture seule). Pour un service "
    "qui ne marche pas, diagnostique d'abord, puis donne la cause probable et un correctif concret que la "
    "personne appliquera elle-même (ex. « redémarre le conteneur Plex depuis l'onglet Docker »). Tu ne peux "
    "rien redémarrer ni modifier : ne prétends jamais l'avoir fait."
)


class NouvelEvenement(BaseModel):
    titre: str = Field(min_length=1, max_length=200)
    debut: str = Field(min_length=8, max_length=40)
    fin: str = Field(default="", max_length=40)
    lieu: str = Field(default="", max_length=200)
    journee: bool = False


class Profil(BaseModel):
    ton: Literal["concis", "normal", "chaleureux"] | None = None
    consigne: str | None = Field(default=None, max_length=500)
    piece: str | None = Field(default=None, max_length=40)
    type: Literal["adulte", "enfant"] | None = None       # modifiable par un administrateur seulement


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
    return " ".join(morceaux)


class NouveauCompte(BaseModel):
    nom: str = Field(min_length=1, max_length=40)
    admin: bool = False


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
    "Les réponses précédentes sur les appareils viennent du module maison et sont fiables. "
    "Tu n'as ni fichiers ni commandes."
)


def _messages(cfg: Config, d: DemandeChat, outils: bool = False, profil: str = "") -> list[Message]:
    msgs = [Message("system", cfg.consigne_effective + "\n\n" + NOTE_STYLE + ("\n\n" + profil if profil else ""))]
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
    annonces = Annonces(securite.fichier.parent, cfg.proactif.heures_calmes)
    agenda = Agenda(cfg.agenda.url, cfg.agenda.utilisateur, cfg.agenda.mot_de_passe())

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
                for nom, c in mauvais.items():
                    quoi = "est en mauvaise santé" if c["sante"] == "unhealthy" else f"s'est arrêté ({c['statut']})"
                    annonces.ajouter(f"Le conteneur {nom} {quoi}. Demande-moi un diagnostic.", "important",
                                     "docker", f"docker:{nom}")
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
                annonces.ajouter(texte, regle.priorite, "surveillance", cle)
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
                annonces.ajouter(f"Dans {minutes} minutes : {e.get('titre', 'un événement')}"
                                 + (f" ({e['lieu']})" if e.get("lieu") else "") + ".", "important", "agenda",
                                 f"agenda:{e.get('uid', e.get('titre'))}:{e['debut']:%Y%m%d%H%M}")
        h = cfg.proactif.briefing_heure
        jour = time.strftime("%Y-%m-%d")
        if h and time.strftime("%H:%M") >= h and etat_veille.get("briefing") != jour and annonces.presents is not False:
            etat_veille["briefing"] = jour
            try:
                annonces.ajouter(await briefing(), "info", "briefing", f"briefing:{jour}")
            except Exception as e:  # noqa: BLE001
                journal.info("briefing impossible : %s", e)

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
        env = {"ASSISTANT_INTERNE_URL": f"http://127.0.0.1:{cfg.serveur.port}",
               "ASSISTANT_INTERNE_JETON": jeton_interne, "ASSISTANT_CONVERSATION": conversation,
               "ASSISTANT_DOCKER": "1" if docker.configure else "0",
               "ASSISTANT_AGENDA": "1" if agenda.configure else "0"}
        return {
            "mcp": {"mcpServers": {"maison": {"command": sys.executable, "args": ["-m", "assistant.mcp_maison"], "env": env}}},
            "autoriser": ["mcp__maison"],
            "note": NOTE_OUTILS_MAISON + ("\n\n" + NOTE_OUTILS_DOCKER if docker.configure else "")
                    + ("\n\n" + NOTE_OUTILS_AGENDA.format(date=f"{JOURS[time.localtime().tm_wday]} {time.strftime('%d/%m/%Y %H:%M')}") if agenda.configure else ""),
        }

    def verifier_jeton(x_jeton_interne: str | None) -> None:
        if not x_jeton_interne or not secrets.compare_digest(x_jeton_interne, jeton_interne):
            raise HTTPException(403, "jeton interne invalide")

    @asynccontextmanager
    async def cycle(app: FastAPI):
        import asyncio
        if cfg.voix.active and cfg.voix.whisper_present() and cfg.voix.synthese_presente():
            app.state.prechauffage = asyncio.create_task(voix.prechauffer())
        if cfg.proactif.active and (ha.configure or docker.configure or cfg.proactif.briefing_heure):
            app.state.surveillance = asyncio.create_task(boucle_surveillance())
        yield
        if getattr(app.state, "surveillance", None):
            app.state.surveillance.cancel()
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
    app.state.surveiller_une_fois = surveiller_une_fois     # pour les tests

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
    masques_conversation: dict[str, frozenset] = {}          # conversation -> ce qu'on ne révèle pas (outils de Claude)

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
        entete = (f"Diagnostic {cfg.identite.nom} v{__version__} · {time.strftime('%Y-%m-%d %H:%M:%S %z')} · "
                  f"{'conteneur' if os.name != 'nt' and Path('/config').is_dir() else 'PC'} · voix {cfg.voix.moteur_effectif()}")
        texte = diag.rapport(max(0.1, min(jours, 7)), entete)
        if brut:
            return Response(texte, media_type="text/plain; charset=utf-8")
        import html
        return Response(PAGE_DIAGNOSTIC.replace("{texte}", html.escape(texte)), media_type="text/html; charset=utf-8")

    @app.get("/connexion", include_in_schema=False)
    def page_connexion() -> FileResponse:
        return FileResponse(STATIC / "connexion.html")

    @app.get("/api/securite")
    def etat_securite(requete: Request) -> dict:
        directe, ip = ip_de(requete)
        compte = compte_de(requete)
        info = securite.comptes.get(compte or "", {})
        return {"nom": cfg.identite.nom, "mot_de_passe_defini": securite.mot_de_passe_defini,
                "locale": est_locale(ip), "ip": ip, "session_jours": cfg.securite.session_jours,
                "connecte": compte is not None, "compte": compte, "compte_nom": info.get("nom", compte),
                "admin": securite.est_admin(compte), "totp": bool(info.get("totp")),
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
            ident, jeton = securite.creer_compte(n.nom, n.admin)
        except AccesRefuse as e:
            raise HTTPException(409, str(e)) from e
        audit.noter("compte_créé", admin, ip_de(requete)[1], cible=ident, admin=n.admin)
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
        if "type" in maj and not securite.est_admin(compte):
            raise HTTPException(403, "seul un administrateur change le type d'un compte")
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
    def lire_audit(requete: Request, n: int = 200, type: str = "") -> dict:
        exiger_admin(requete)
        return {"evenements": audit.derniers(max(1, min(n, 1000)), type)}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/info")
    def info() -> dict:
        ident = cfg.identite
        return {
            "nom": ident.nom,
            "wake_word": ident.wake_word_effectif,
            "langue": ident.langue,
            "version": __version__,
        }

    @app.get("/api/fournisseurs")
    async def fournisseurs() -> dict:
        return {"roles": reg.roles, "fournisseurs": await reg.etat()}

    @app.get("/api/home-assistant")
    async def etat_ha() -> dict:
        return await ha.etat()

    @app.get("/api/maison")
    async def etat_maison() -> dict:
        if ha.configure and not maison.inventaire.entites:
            await maison.inventaire.obtenir()
        return {"inventaire": maison.inventaire.resume(), "alias": maison.alias.liste()}

    @app.post("/api/maison/rafraichir")
    async def rafraichir_maison() -> dict:
        await maison.inventaire.obtenir(forcer=True)
        return maison.inventaire.resume()

    @app.delete("/api/maison/alias/{phrase}")
    def oublier_alias(phrase: str, requete: Request) -> dict:
        if not maison.alias.oublier(phrase):
            raise HTTPException(404, "alias inconnu")
        auditer(requete, "alias_oublié", phrase=phrase)
        return {"ok": True}

    # --- API interne : seulement pour le serveur MCP « maison » (jeton requis) -----------
    @app.get("/interne/maison/appareils", include_in_schema=False)
    async def interne_appareils(filtre: str = "", conversation: str = "",
                                x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        masques.set(masques_conversation.get(conversation, frozenset()))
        return await maison.appareils(filtre)

    @app.get("/interne/maison/etat/{entity_id}", include_in_schema=False)
    async def interne_etat(entity_id: str, conversation: str = "", x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        masques.set(masques_conversation.get(conversation, frozenset()))
        e = await maison.etat_detaille(entity_id)
        if e is None:
            raise HTTPException(404, "appareil inconnu ou non autorisé")
        return e

    @app.get("/api/actions")
    def historique_actions(requete: Request, limite: int = 50) -> dict:
        exiger_revelation(requete, "historique")
        h = maison.historique
        return {"actions": h.liste(max(1, min(limite, 500))) if h else []}

    @app.post("/api/actions/{ident}/annuler")
    async def annuler_action(ident: str, requete: Request) -> dict:
        exiger_revelation(requete, "historique")
        ok, message = await maison.annuler_action(ident)
        if not ok:
            raise HTTPException(404 if message == "action inconnue" else 409, message)
        auditer(requete, "action_maison", par="historique", action="annuler", cible=ident, resultat=message)
        return {"ok": True, "message": message}

    @app.get("/api/appareils/etat")
    async def etat_des_appareils(requete: Request) -> dict:
        exiger_revelation(requete, "historique")
        return {"appareils": await maison.etats_appareils()}

    @app.get("/interne/docker/conteneurs", include_in_schema=False)
    async def interne_conteneurs(x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        return await docker_ou_erreur(docker.conteneurs())

    @app.get("/interne/docker/diagnostic", include_in_schema=False)
    async def interne_diagnostic(nom: str, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        return await docker_ou_erreur(docker.diagnostic(nom))

    async def docker_ou_erreur(appel):
        if not docker.configure:
            raise HTTPException(503, "diagnostic Docker non configuré (docker.url)")
        try:
            return await appel
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001 - proxy injoignable, refus…
            raise HTTPException(502, f"Docker injoignable : {e.__class__.__name__}") from e

    async def agenda_ou_erreur(appel):
        if not agenda.configure:
            raise HTTPException(503, "agenda non configuré (agenda.url)")
        try:
            return await appel
        except PermissionError as e:
            raise HTTPException(502, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"agenda injoignable : {e.__class__.__name__}") from e

    def evenements_json(evts: list[dict]) -> list[dict]:
        return [{"titre": e.get("titre", ""), "debut": e["debut"].isoformat(), "journee": e.get("journee", False),
                 "lieu": e.get("lieu", ""), "texte": decrire(e)} for e in evts]

    async def prochains(jours: float) -> list[dict]:
        from datetime import datetime as dt, timedelta
        maintenant = dt.now().astimezone()
        debut = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        return await agenda.evenements(debut, maintenant + timedelta(days=jours))

    @app.get("/interne/agenda", include_in_schema=False)
    async def interne_agenda(jours: int = 7, x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        return evenements_json(await agenda_ou_erreur(prochains(max(1, min(jours, 60)))))

    @app.post("/interne/agenda", include_in_schema=False)
    async def interne_ajouter(e: NouvelEvenement, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        from datetime import datetime as dt
        try:
            debut = dt.fromisoformat(e.debut).astimezone()
            fin = dt.fromisoformat(e.fin).astimezone() if e.fin else None
        except ValueError as x:
            raise HTTPException(422, "date invalide (format 2026-11-12T14:30)") from x
        uid = await agenda_ou_erreur(agenda.ajouter(e.titre, debut, fin, e.lieu, cfg.agenda.rappel_min, e.journee))
        audit.noter("agenda_ajout", "", "", par="claude", titre=e.titre, debut=e.debut)
        return {"ok": True, "uid": uid, "resume": decrire({"debut": debut, "titre": e.titre, "lieu": e.lieu,
                                                           "journee": e.journee})}

    @app.get("/api/agenda")
    async def lire_agenda(requete: Request, jours: int = 7) -> dict:
        exiger_revelation(requete, "historique")
        if not agenda.configure:
            return {"configure": False, "evenements": []}
        return {"configure": True, "evenements": evenements_json(await agenda_ou_erreur(prochains(max(1, min(jours, 60)))))}

    @app.get("/api/annonces")
    def lire_annonces(requete: Request, depuis: str = "") -> dict:
        """Ce que l'assistant a à dire de lui-même ; « parler » : à dire à voix haute maintenant."""
        caches = contexte_de(requete)["masques"]
        liste = annonces.depuis(depuis)
        if "historique" in caches:         # appareil partagé / enfant : seulement les urgences
            liste = [a for a in liste if a["priorite"] == "urgent"]
        return {"annonces": liste, "heures_calmes": cfg.proactif.heures_calmes, "presents": annonces.presents}

    @app.post("/api/annonces/{ident}/lue")
    def annonce_lue(ident: str) -> dict:
        if not annonces.marquer_lue(ident):
            raise HTTPException(404, "annonce inconnue")
        return {"ok": True}

    @app.post("/api/briefing")
    async def demander_briefing() -> dict:
        try:
            texte = await briefing()
        except FournisseurIndisponible as e:
            raise HTTPException(503, f"aucun modèle disponible ({e})") from e
        return {"texte": texte}

    @app.get("/api/docker")
    async def etat_docker(requete: Request) -> dict:
        """Conteneurs et ceux qui posent problème (administrateurs)."""
        exiger_admin(requete)
        if not docker.configure:
            return {"configure": False, "conteneurs": [], "problemes": []}
        liste = await docker_ou_erreur(docker.conteneurs())
        mauvais = [c["nom"] for c in await docker_ou_erreur(docker.problemes())]
        return {"configure": True, "conteneurs": liste, "problemes": mauvais}

    @app.post("/interne/maison/agir", include_in_schema=False)
    async def interne_agir(a: Action, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        demande_en_cours.set(("claude", texte_conversation.get(a.conversation, "")))
        r = await maison.agir_ids(a.entity_ids, a.action, a.valeur, a.couleur, a.conversation)
        journal.info("outil agir %s %s -> %s", a.action, a.entity_ids, r.texte[:120])
        compte, ip = compte_conversation.get(a.conversation, ("", ""))
        audit.noter("action_maison", compte, ip, par="claude", action=a.action, entites=a.entity_ids,
                    valeur=a.valeur, resultat=r.texte[:200])
        return {"resultat": r.texte or "Rien n'a été fait.", "entites": r.detail.get("entites", [])}

    @app.post("/interne/maison/annuler", include_in_schema=False)
    async def interne_annuler(c: Conv, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        r = await maison.annuler(c.conversation)
        journal.info("outil annuler -> %s", r.texte[:120])
        compte, ip = compte_conversation.get(c.conversation, ("", ""))
        audit.noter("action_maison", compte, ip, par="claude", action="annuler", resultat=r.texte[:200])
        return {"resultat": r.texte}

    # --- Voix ------------------------------------------------------------------------------
    @app.get("/api/voix")
    def etat_voix() -> dict:
        return {**voix.etat(), "wake_word": cfg.identite.wake_word_effectif,
                "fenetre_s": cfg.voix.fenetre_s, "toujours_a_l_ecoute": cfg.voix.toujours_a_l_ecoute,
                "voix": cfg.voix.voix_actuelle(), "voix_disponibles": voix.disponibles()}

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
        noms = [cfg.identite.nom] + sorted({e.nom for e in maison.inventaire.entites} |
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
        noms = [cfg.identite.nom] + sorted({e.nom for e in maison.inventaire.entites})[:60]
        async with verrou_transcription:
            t = await voix.transcrire(audio, ", ".join(noms))
        r = reveil.analyser(t.texte, cfg.identite.wake_word_effectif, conversation)
        qui = None
        if r["action"] != "ignorer":
            qui = await reconnaitre(audio, conversation)
            journal.info("écoute : « %s » -> %s (%d ms)%s", t.texte[:120], r["action"], t.duree_ms,
                         f" — {qui['nom']} ({qui['score']})" if qui else "")
        return {"texte": t.texte, "duree_ms": t.duree_ms, "wake_word": cfg.identite.wake_word_effectif,
                "toujours": reveil.toujours, "locuteur": qui, **r}

    @app.post("/api/voix/reglages")
    def regler_voix(r: ReglagesVoix) -> dict:
        """Change la fenêtre d'écoute après le wake word et le mode « toujours à l'écoute »,
        tout de suite et dans config.yaml (gardé au redémarrage)."""
        valeurs = {k: v for k, v in r.model_dump().items() if v is not None}
        if not valeurs:
            raise HTTPException(400, "aucun réglage fourni")
        if "voix" in valeurs:
            if valeurs["voix"] not in {v["id"] for v in voix.disponibles()}:
                raise HTTPException(422, f"voix inconnue : {valeurs['voix']}")
            cfg.voix.voix = valeurs["voix"]
        if "fenetre_s" in valeurs:
            cfg.voix.fenetre_s = reveil.fenetre_s = valeurs["fenetre_s"]
        if "toujours_a_l_ecoute" in valeurs:
            cfg.voix.toujours_a_l_ecoute = reveil.toujours = valeurs["toujours_a_l_ecoute"]
        if persister:
            enregistrer_reglages("voix", valeurs)
        journal.info("réglages voix : %s", valeurs)
        return {"fenetre_s": cfg.voix.fenetre_s, "toujours_a_l_ecoute": cfg.voix.toujours_a_l_ecoute,
                "voix": cfg.voix.voix_actuelle()}

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
    async def lire_reglages() -> dict:
        import asyncio as aio
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

    @app.post("/api/voix/endormir")
    def endormir(c: Conv) -> dict:
        reveil.endormir(c.conversation)
        return {"ok": True}

    @app.post("/api/voix/parler")
    async def parler(p: Parole) -> Response:
        if not cfg.voix.active or not cfg.voix.synthese_presente():
            raise HTTPException(503, "synthèse vocale indisponible")
        if p.voix and p.voix not in {v["id"] for v in voix.disponibles()}:
            raise HTTPException(422, f"voix inconnue : {p.voix}")
        wav, duree = await voix.parler(p.texte, p.voix)
        return Response(wav, media_type="audio/wav", headers={"X-Duree-Ms": str(duree), "Cache-Control": "no-store"})

    @app.post("/api/route")
    async def route(d: DemandeChat) -> dict:
        """Décision du routeur seule, sans appeler le modèle choisi (tests, réglages)."""
        return (await _decider(d)).en_dict()

    async def _decider(d: DemandeChat) -> Decision:
        precedent = next((t.content for t in reversed(d.historique) if t.role == "user"), "")
        return await routeur.decider(d.message, precedent)

    @app.post("/api/chat")
    async def chat(d: DemandeChat, requete: Request):
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
            # reconnu à la voix : son ton, sa pièce, ses préférences (pas ses droits)
            profil, nom_profil = securite.profil(voix_de[0]), securite.comptes[voix_de[0]].get("nom", voix_de[0])
            route_locuteur = nom_profil
        else:
            route_locuteur = ""
        if "personnel" in ctx_revel:
            profil = {k: v for k, v in profil.items() if k in ("type", "ton")}   # ni pièce ni préférences
        piece_preferee.set(profil.get("piece", ""))
        consigne_profil = note_profil(nom_profil, profil) if profil else ""
        decision = None
        compte_conversation[d.conversation] = (compte_de(requete) or "", ip_de(requete)[1])
        texte_conversation[d.conversation] = d.message
        if d.role == ROLE_AUTO and ha.configure and (maison.en_attente(d.conversation) or maison.reconnait(d.message, d.conversation)):
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
        if decision is None and ha.configure and maison.reconnait(d.message, d.conversation):
            simple = True    # rôle choisi à la main : une commande de maison reste une commande de maison
        if ha.configure and cfg.home_assistant.pour_simples and (simple or maison.en_attente(d.conversation)):
            rm = await maison.traiter(d.message, d.conversation)
            route["maison"] = {"traite": rm.traite, **{k: v for k, v in rm.detail.items() if k != "commande"}}
            if rm.traite:
                if rm.detail.get("entites") or "ordres" in str(rm.detail.get("resolution", "")):
                    auditer(requete, "action_maison", par="règles", demande=d.message[:200],
                            entites=rm.detail.get("entites"), resultat=rm.texte[:200])
                _tracer(route, d.message)
                return _reponse_ha(d.flux, rm.texte, route, "maison", "inventaire", rm.detail.get("duree_ms", 0))
            if "commande" in rm.detail and cfg.maison.assist_en_secours and maison.mentionne_sensible(d.message):
                # Assist pourrait ouvrir une serrure ou un portail : on refuse sans lui demander
                route["maison"]["sensible"] = True
                _tracer(route, d.message)
                return _reponse_ha(d.flux, "Je ne pilote pas ça : action sensible, à faire depuis Home Assistant.",
                                   route, "maison", "inventaire", 0)
            if "commande" in rm.detail and cfg.maison.assist_en_secours:
                # commande comprise mais appareil introuvable : l'agent Assist de HA tente sa chance
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
                    return _reponse_ha(d.flux, rha.texte or "C'est fait.", route, "home-assistant", "assist", duree)
                echec_ha = rha.code or rha.type
            if "commande" in rm.detail and rm.texte:
                # Commande de maison comprise mais appareil introuvable : réponse factuelle,
                # jamais confiée à un modèle (il inventerait « c'est allumé »).
                _tracer(route, d.message)
                return _reponse_ha(d.flux, rm.texte, route, "maison", "inventaire", rm.detail.get("duree_ms", 0))
            if "commande" in rm.detail and not echec_ha:
                echec_ha = rm.detail.get("raison", "appareil introuvable")
        # Claude avec les outils maison : demande ouverte sur la maison (« il fait sombre au salon »),
        # ou demande simple que les règles n'ont pas su traiter
        outils = None
        claude_outille = (cfg.routeur.role_complexe in reg.roles
                          and getattr(reg.pour_role(cfg.routeur.role_complexe), "accepte_outils", False))
        if agenda.configure and claude_outille and RE_AGENDA.search(d.message) and "historique" not in ctx_revel:
            role = cfg.routeur.role_complexe
            route["outils"] = "Claude avec l'agenda"
            outils = outils_maison(d.conversation)
        elif docker.configure and claude_outille and RE_DOCKER.search(d.message) and securite.est_admin(compte):
            # serveur, conteneurs : Claude avec les outils de diagnostic (administrateurs seulement)
            role = cfg.routeur.role_complexe
            route["outils"] = "Claude avec diagnostic Docker"
            outils = outils_maison(d.conversation)
        elif (ha.configure and cfg.maison.outils_pour_claude and (decision is not None or role == cfg.routeur.role_complexe)
                and claude_outille
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

        msgs = _messages(cfg, d, outils=bool(outils), profil=consigne_profil)
        # garde-fou : le modèle rapide ne doit pas prétendre avoir agi sur la maison
        garde = ha.configure and role == cfg.routeur.role_simple
        if echec_ha:
            msgs.insert(1, Message("system", NOTE_HA_ECHEC.format(code=echec_ha)))

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
            return {
                "reponse": r.texte,
                "fournisseur": r.fournisseur,
                "modele": r.modele,
                "secours": r.secours,
                "route": route,
                "duree_ms": round((time.perf_counter() - debut) * 1000),
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
                texte = sans_emoji(r.texte)
                if r.secours and RE_FAUSSE_ACTION.match(texte):
                    journal.info("réponse inventée remplacée : %s", texte[:120])
                    texte = REPONSE_HONNETE
                journal.info("réponse %s : %s", r.fournisseur, texte[:160].replace("\n", " "))
                duree = round((time.perf_counter() - debut) * 1000)
                yield _ndjson({"delta": texte})
                yield _ndjson({"fin": True, "fournisseur": r.fournisseur, "modele": r.modele, "secours": r.secours,
                               "premier_ms": duree, "duree_ms": duree})
                return
            tampon, verifie = "", not garde
            try:
                async for x in reg.flux(role, msgs, outils):
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
        nom = cfg.identite.nom
        icones = [{"src": f"/static/icones/icone-{t}.png", "sizes": f"{t}x{t}", "type": "image/png",
                   "purpose": "any maskable"} for t in (192, 512)]
        return JSONResponse({"name": nom, "short_name": nom, "start_url": "/", "display": "standalone",
                             "background_color": "#000000", "theme_color": "#000000", "icons": icones},
                            media_type="application/manifest+json")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


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


def _reponse_ha(flux: bool, texte: str, route: dict, fournisseur: str, modele: str, duree_ms: int):
    journal.info("réponse %s : %s", fournisseur, texte[:160])
    fin = {"fournisseur": fournisseur, "modele": modele, "secours": False, "duree_ms": duree_ms}
    if not flux:
        return {"reponse": texte, **fin, "route": route}

    async def lignes():
        yield _ndjson({"route": route})
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
    uvicorn.run(creer_app(cfg), host=cfg.serveur.hote, port=cfg.serveur.port)


if __name__ == "__main__":
    run()
