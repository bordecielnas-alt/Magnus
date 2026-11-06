"""Point d'entrée HTTP de l'orchestrateur."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Config, charger

STATIC = Path(__file__).resolve().parent / "static"


def creer_app(config: Config | None = None) -> FastAPI:
    cfg = config or charger()
    app = FastAPI(title=f"{cfg.identite.nom} — orchestrateur", version=__version__)
    app.state.config = cfg

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/info")
    def info() -> dict:
        ident = app.state.config.identite
        return {
            "nom": ident.nom,
            "wake_word": ident.wake_word_effectif,
            "langue": ident.langue,
            "version": __version__,
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def run() -> None:
    import uvicorn

    cfg = charger()
    uvicorn.run(creer_app(cfg), host=cfg.serveur.hote, port=cfg.serveur.port)


if __name__ == "__main__":
    run()
