"""Point d'entrée HTTP de l'orchestrateur."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import Config, charger
from .fournisseurs import FournisseurIndisponible, Message, Registre, Reponse

STATIC = Path(__file__).resolve().parent / "static"
HISTORIQUE_MAX = 20


class Tour(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class DemandeChat(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    historique: list[Tour] = Field(default_factory=list)
    role: str = "rapide"
    flux: bool = False


def _messages(cfg: Config, d: DemandeChat) -> list[Message]:
    msgs = [Message("system", cfg.consigne_effective)]
    msgs += [Message(t.role, t.content) for t in d.historique[-HISTORIQUE_MAX:]]
    msgs.append(Message("user", d.message))
    return msgs


def creer_app(config: Config | None = None, registre: Registre | None = None) -> FastAPI:
    cfg = config or charger()
    reg = registre or Registre.depuis_config(cfg)

    @asynccontextmanager
    async def cycle(app: FastAPI):
        yield
        await reg.fermer()

    app = FastAPI(title=f"{cfg.identite.nom} — orchestrateur", version=__version__, lifespan=cycle)
    app.state.config = cfg
    app.state.registre = reg

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

    @app.post("/api/chat")
    async def chat(d: DemandeChat):
        if d.role not in reg.roles:
            raise HTTPException(400, f"rôle inconnu : {d.role}")
        msgs = _messages(cfg, d)

        if not d.flux:
            debut = time.perf_counter()
            try:
                r = await reg.repondre(d.role, msgs)
            except FournisseurIndisponible as e:
                raise HTTPException(503, f"aucun modèle disponible ({e})") from e
            return {
                "reponse": r.texte,
                "fournisseur": r.fournisseur,
                "modele": r.modele,
                "secours": r.secours,
                "duree_ms": round((time.perf_counter() - debut) * 1000),
            }

        async def lignes():
            # NDJSON : {"delta": "..."} puis {"fin": true, ...} ou {"erreur": "..."}
            debut = time.perf_counter()
            premier = None
            try:
                async for x in reg.flux(d.role, msgs):
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


def _ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def run() -> None:
    import uvicorn

    cfg = charger()
    uvicorn.run(creer_app(cfg), host=cfg.serveur.hote, port=cfg.serveur.port)


if __name__ == "__main__":
    run()
