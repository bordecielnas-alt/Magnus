"""Lancement : python -m assistant.

Le journal du serveur (démarrage, requêtes, erreurs avec leur trace) s'affiche dans la console
et s'écrit aussi dans <état>/logs/assistant.log : /config/logs sur Unraid (appdata/magnus/logs),
config/logs sur le PC. 5 Mo par fichier, 5 fichiers gardés (assistant.log.1 … .5).
"""

import copy
import logging
import logging.config


def config_journal(dossier) -> dict:
    from uvicorn.config import LOGGING_CONFIG

    dossier.mkdir(parents=True, exist_ok=True)
    c = copy.deepcopy(LOGGING_CONFIG)
    c["formatters"]["fichier"] = {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                                  "datefmt": "%Y-%m-%d %H:%M:%S"}
    c["handlers"]["fichier"] = {"class": "logging.handlers.RotatingFileHandler", "formatter": "fichier",
                                "filename": str(dossier / "assistant.log"), "maxBytes": 5 * 1024 * 1024,
                                "backupCount": 5, "encoding": "utf-8"}
    for nom in ("uvicorn", "uvicorn.access"):
        c["loggers"][nom]["handlers"] = c["loggers"][nom]["handlers"] + ["fichier"]
    return c


def principal() -> None:
    import uvicorn

    from .config import charger, charger_secrets, dossier_etat
    from .main import creer_app

    secrets = charger_secrets()
    cfg = charger()
    journal = config_journal(dossier_etat(cfg) / "logs")
    logging.config.dictConfig(journal)
    log = logging.getLogger("uvicorn.error")
    log.info("journal écrit dans %s", journal["handlers"]["fichier"]["filename"])
    if secrets:
        log.info("secrets chargés depuis %s", secrets)
    uvicorn.run(creer_app(cfg), host=cfg.serveur.hote, port=cfg.serveur.port, log_config=journal)


principal()
