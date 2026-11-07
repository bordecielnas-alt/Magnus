"""Point d'entrée HTTP de l'orchestrateur."""

from __future__ import annotations

import json
import logging
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
from .maison import Maison
from .maison.alias import AliasAppris
from .routeur import SIMPLE, Decision, Routeur
from .reveil import Reveil
from .securite import NOM_COOKIE, Securite, est_locale
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
    mot_de_passe: str = Field(min_length=1, max_length=200)


class NouveauMotDePasse(BaseModel):
    nouveau: str = Field(min_length=10, max_length=200)
    actuel: str | None = Field(default=None, max_length=200)


# Accessibles sans être connecté (le reste demande une session hors de la machine elle-même)
CHEMINS_PUBLICS = {"/health", "/connexion", "/api/connexion", "/api/securite", "/api/securite/mot-de-passe",
                   "/favicon.ico"}


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
    "Les réponses précédentes sur les appareils viennent du module maison et sont fiables. "
    "Tu n'as aucun autre outil : ni fichiers, ni commandes, ni web."
)


def _messages(cfg: Config, d: DemandeChat, outils: bool = False) -> list[Message]:
    msgs = [Message("system", cfg.consigne_effective + "\n\n" + NOTE_STYLE)]
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
    maison = Maison(cfg.maison, ha, alias or AliasAppris(chemin_alias(cfg)), extracteur)
    voix = voix or Voix(cfg.voix)
    securite = securite or Securite(cfg.securite, dossier_etat(cfg))
    reveil = Reveil(cfg.voix.fenetre_s, cfg.voix.toujours_a_l_ecoute)
    import asyncio as _asyncio
    verrou_transcription = _asyncio.Lock()   # une transcription à la fois (sinon CPU/GPU saturé)
    jeton_interne = secrets.token_urlsafe(24)   # pour le serveur MCP « maison », change à chaque démarrage

    def outils_maison(conversation: str) -> dict:
        env = {"ASSISTANT_INTERNE_URL": f"http://127.0.0.1:{cfg.serveur.port}",
               "ASSISTANT_INTERNE_JETON": jeton_interne, "ASSISTANT_CONVERSATION": conversation}
        return {
            "mcp": {"mcpServers": {"maison": {"command": sys.executable, "args": ["-m", "assistant.mcp_maison"], "env": env}}},
            "autoriser": ["mcp__maison"],
            "note": NOTE_OUTILS_MAISON,
        }

    def verifier_jeton(x_jeton_interne: str | None) -> None:
        if not x_jeton_interne or not secrets.compare_digest(x_jeton_interne, jeton_interne):
            raise HTTPException(403, "jeton interne invalide")

    @asynccontextmanager
    async def cycle(app: FastAPI):
        import asyncio
        if cfg.voix.active and cfg.voix.whisper_present() and cfg.voix.synthese_presente():
            app.state.prechauffage = asyncio.create_task(voix.prechauffer())
        yield
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

    @app.middleware("http")
    async def controle_acces(requete: Request, suite):
        chemin = requete.url.path
        directe, ip = ip_de(requete)
        # API interne (serveur MCP lancé par l'assistant) : uniquement depuis la machine, jamais via le proxy
        if chemin.startswith("/interne/"):
            if not est_locale(directe) or requete.headers.get("x-forwarded-for"):
                return JSONResponse({"detail": "interdit"}, 403)
            return await suite(requete)
        if not securite.reseau_autorise(ip):
            journal.info("accès refusé (réseau non autorisé) : %s %s", ip, chemin)
            return JSONResponse({"detail": "réseau non autorisé"}, 403)
        if chemin in CHEMINS_PUBLICS or not securite.exige_connexion(ip):
            return await suite(requete)
        if securite.session_valide(requete.cookies.get(NOM_COOKIE)):
            return await suite(requete)
        if requete.method == "GET" and not chemin.startswith("/api/"):
            return RedirectResponse(f"/connexion?suite={chemin}", 303)
        return JSONResponse({"detail": "connexion requise"}, 401)

    @app.get("/connexion", include_in_schema=False)
    def page_connexion() -> FileResponse:
        return FileResponse(STATIC / "connexion.html")

    @app.get("/api/securite")
    def etat_securite(requete: Request) -> dict:
        directe, ip = ip_de(requete)
        return {"nom": cfg.identite.nom, "mot_de_passe_defini": securite.mot_de_passe_defini,
                "locale": est_locale(ip), "ip": ip, "session_jours": cfg.securite.session_jours,
                "connecte": (not securite.exige_connexion(ip)) or securite.session_valide(requete.cookies.get(NOM_COOKIE))}

    def poser_session(rep: Response, requete: Request, directe: str) -> None:
        rep.set_cookie(NOM_COOKIE, securite.creer_session(), max_age=int(cfg.securite.session_jours * 86400),
                       httponly=True, samesite="lax", secure=en_https(requete, directe), path="/")

    @app.post("/api/connexion")
    def se_connecter(c: Connexion, requete: Request, reponse: Response) -> dict:
        directe, ip = ip_de(requete)
        if not securite.mot_de_passe_defini:
            raise HTTPException(409, "aucun mot de passe défini : choisis-le depuis la machine de l'assistant")
        ok, message = securite.connexion(ip, c.mot_de_passe)
        if not ok:
            journal.info("connexion refusée : %s (%s)", ip, message)
            raise HTTPException(429 if "Trop" in message else 401, message)
        poser_session(reponse, requete, directe)
        journal.info("connexion : %s", ip)
        return {"ok": True}

    @app.post("/api/deconnexion")
    def se_deconnecter(reponse: Response) -> dict:
        reponse.delete_cookie(NOM_COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/securite/mot-de-passe")
    def changer_mot_de_passe(m: NouveauMotDePasse, requete: Request, reponse: Response) -> dict:
        """Premier mot de passe : seulement depuis la machine elle-même. Changement : depuis la machine,
        ou en donnant l'actuel."""
        directe, ip = ip_de(requete)
        if not est_locale(ip):
            if not securite.mot_de_passe_defini:
                raise HTTPException(403, "le premier mot de passe se choisit sur la machine de l'assistant")
            ok, message = securite.connexion(ip, m.actuel or "")
            if not ok:
                raise HTTPException(403, message)
        securite.definir_mot_de_passe(m.nouveau)
        poser_session(reponse, requete, directe)
        journal.info("mot de passe défini depuis %s (toutes les sessions précédentes sont fermées)", ip)
        return {"ok": True}

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
    def oublier_alias(phrase: str) -> dict:
        if not maison.alias.oublier(phrase):
            raise HTTPException(404, "alias inconnu")
        return {"ok": True}

    # --- API interne : seulement pour le serveur MCP « maison » (jeton requis) -----------
    @app.get("/interne/maison/appareils", include_in_schema=False)
    async def interne_appareils(filtre: str = "", x_jeton_interne: str | None = Header(None)) -> list[dict]:
        verifier_jeton(x_jeton_interne)
        return await maison.appareils(filtre)

    @app.get("/interne/maison/etat/{entity_id}", include_in_schema=False)
    async def interne_etat(entity_id: str, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        e = await maison.etat_detaille(entity_id)
        if e is None:
            raise HTTPException(404, "appareil inconnu ou non autorisé")
        return e

    @app.post("/interne/maison/agir", include_in_schema=False)
    async def interne_agir(a: Action, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        r = await maison.agir_ids(a.entity_ids, a.action, a.valeur, a.couleur, a.conversation)
        journal.info("outil agir %s %s -> %s", a.action, a.entity_ids, r.texte[:120])
        return {"resultat": r.texte or "Rien n'a été fait.", "entites": r.detail.get("entites", [])}

    @app.post("/interne/maison/annuler", include_in_schema=False)
    async def interne_annuler(c: Conv, x_jeton_interne: str | None = Header(None)) -> dict:
        verifier_jeton(x_jeton_interne)
        r = await maison.annuler(c.conversation)
        journal.info("outil annuler -> %s", r.texte[:120])
        return {"resultat": r.texte}

    # --- Voix ------------------------------------------------------------------------------
    @app.get("/api/voix")
    def etat_voix() -> dict:
        return {**voix.etat(), "wake_word": cfg.identite.wake_word_effectif,
                "fenetre_s": cfg.voix.fenetre_s, "toujours_a_l_ecoute": cfg.voix.toujours_a_l_ecoute,
                "voix": cfg.voix.voix_actuelle(), "voix_disponibles": voix.disponibles()}

    @app.post("/api/voix/transcrire")
    async def transcrire(requete: Request, partiel: bool = False) -> dict:
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
        if not partiel:   # les transcriptions partielles (mots en direct) ne sont pas tracées
            journal.info("voix : « %s » (%.1f s d'audio, %d ms)", t.texte[:120], t.duree_audio_s, t.duree_ms)
        return {"texte": t.texte, "duree_audio_s": t.duree_audio_s, "duree_ms": t.duree_ms}

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
        if r["action"] != "ignorer":
            journal.info("écoute : « %s » -> %s (%d ms)", t.texte[:120], r["action"], t.duree_ms)
        return {"texte": t.texte, "duree_ms": t.duree_ms, "wake_word": cfg.identite.wake_word_effectif,
                "toujours": reveil.toujours, **r}

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
    async def chat(d: DemandeChat):
        decision = None
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

        # Maison : réponse à une question « lequel ? » en cours, ou demande simple
        echec_ha = ""
        simple = decision is not None and decision.categorie == SIMPLE
        if ha.configure and cfg.home_assistant.pour_simples and (simple or maison.en_attente(d.conversation)):
            rm = await maison.traiter(d.message, d.conversation)
            route["maison"] = {"traite": rm.traite, **{k: v for k, v in rm.detail.items() if k != "commande"}}
            if rm.traite:
                _tracer(route, d.message)
                return _reponse_ha(d.flux, rm.texte, route, "maison", "inventaire", rm.detail.get("duree_ms", 0))
            if "commande" in rm.detail and cfg.maison.assist_en_secours:
                # commande comprise mais appareil introuvable : l'agent Assist de HA tente sa chance
                debut_ha = time.perf_counter()
                rha = await ha.conversation(maison.texte_pour_assist(d.message))
                duree = round((time.perf_counter() - debut_ha) * 1000)
                route["home_assistant"] = {"compris": rha.compris, "type": rha.type, "code": rha.code,
                                           "duree_ms": duree, "entites": rha.entites}
                if rha.compris:
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
        if (ha.configure and cfg.maison.outils_pour_claude and decision is not None
                and cfg.routeur.role_complexe in reg.roles
                and getattr(reg.pour_role(cfg.routeur.role_complexe), "accepte_outils", False)
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

        msgs = _messages(cfg, d, outils=bool(outils))
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
