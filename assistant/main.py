"""Point d'entrée HTTP de l'orchestrateur."""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import Config, charger, charger_secrets
from .fournisseurs import FournisseurIndisponible, Message, Registre, Reponse
from .home_assistant import HomeAssistant
from .routeur import SIMPLE, Decision, Routeur

STATIC = Path(__file__).resolve().parent / "static"
journal = logging.getLogger("uvicorn.error")  # s'affiche dans la console de l'assistant
HISTORIQUE_MAX = 20
ROLE_AUTO = "auto"
NOTE_HA_ECHEC = (
    "Home Assistant n'a pas pu traiter ce message ({code}). Tu n'as accès ni à l'état ni au pilotage "
    "des appareils de la maison. Si le message concerne un appareil, dis-le en une phrase et propose "
    "de reformuler avec le nom exact de l'appareil tel qu'il apparaît dans Home Assistant. "
    "Sinon, réponds normalement."
)


class Tour(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class DemandeChat(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    historique: list[Tour] = Field(default_factory=list)
    role: str = ROLE_AUTO   # "auto" = le routeur choisit
    flux: bool = False


def _messages(cfg: Config, d: DemandeChat) -> list[Message]:
    msgs = [Message("system", cfg.consigne_effective)]
    msgs += [Message(t.role, t.content) for t in d.historique[-HISTORIQUE_MAX:]]
    msgs.append(Message("user", d.message))
    return msgs


def creer_app(config: Config | None = None, registre: Registre | None = None,
              ha: HomeAssistant | None = None) -> FastAPI:
    cfg = config or charger()
    reg = registre or Registre.depuis_config(cfg)
    routeur = Routeur(cfg.routeur, reg)
    ha = ha or HomeAssistant(cfg.home_assistant)

    @asynccontextmanager
    async def cycle(app: FastAPI):
        yield
        await reg.fermer()
        await ha.fermer()

    app = FastAPI(title=f"{cfg.identite.nom} — orchestrateur", version=__version__, lifespan=cycle)
    app.state.config = cfg
    app.state.registre = reg
    app.state.routeur = routeur
    app.state.ha = ha

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
        if d.role == ROLE_AUTO:
            decision = await _decider(d)
            role = decision.role
        elif d.role in reg.roles:
            role = d.role
        else:
            raise HTTPException(400, f"rôle inconnu : {d.role}")
        route = decision.en_dict() if decision else {"role": role, "raison": "choisi à la main"}

        # Demande simple : l'agent Assist de Home Assistant d'abord (exécute l'action, sans LLM)
        rha, echec_ha = None, ""
        if decision and decision.categorie == SIMPLE and cfg.home_assistant.pour_simples and ha.configure:
            debut_ha = time.perf_counter()
            rha = await ha.conversation(d.message)
            route["home_assistant"] = {"compris": rha.compris, "type": rha.type, "code": rha.code,
                                       "duree_ms": round((time.perf_counter() - debut_ha) * 1000)}
            if not rha.compris:
                echec_ha = rha.code or rha.type
                rha = None
        _tracer(route, d.message)
        if rha:
            return _reponse_ha(d.flux, rha.texte, route)

        msgs = _messages(cfg, d)
        if echec_ha:
            msgs.insert(1, Message("system", NOTE_HA_ECHEC.format(code=echec_ha)))

        if not d.flux:
            debut = time.perf_counter()
            try:
                r = await reg.repondre(role, msgs)
            except FournisseurIndisponible as e:
                raise HTTPException(503, f"aucun modèle disponible ({e})") from e
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
            try:
                async for x in reg.flux(role, msgs):
                    if isinstance(x, Reponse):
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
    ha = f" | HA : {'compris' if h['compris'] else 'non compris'} ({h['type']}{' ' + h['code'] if h['code'] else ''}, {h['duree_ms']} ms)" if h else ""
    cat = route.get("categorie", "forcé")
    journal.info("route %s (%s) -> %s%s | « %s »", cat, route.get("raison", ""), route.get("role", ""), ha, message[:80])


def _reponse_ha(flux: bool, texte: str, route: dict):
    fin = {"fournisseur": "home-assistant", "modele": "assist", "secours": False,
           "duree_ms": route["home_assistant"]["duree_ms"]}
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
